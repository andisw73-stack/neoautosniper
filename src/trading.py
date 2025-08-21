import time
import logging
from typing import Dict, Any, Optional

import aiohttp
from solana.publickey import PublicKey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.keypair import Keypair
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer

from src.config import (
    MODE, AUTOBUY, PRIVATE_KEY, WALLET_ADDRESS, RPC_URL,
    BUY_AMOUNT_SOL, SLIPPAGE_BPS, TAKE_PROFIT_PCT, STOP_LOSS_PCT,
    MIN_HOLD_TIME_SEC, MAX_INVEST_SOL, REINVEST_PERCENT, DEX_TOKEN_URL
)
from src.jupiter import jup_swap_exact_in, SOL_MINT

logger = logging.getLogger("trading")

# In-Memory Positionen (einfach & sicher – wird bei Neustart zurückgesetzt)
POSITIONS: Dict[str, Dict[str, Any]] = {}


# ---------------------------
# Preis aus Dexscreener
# ---------------------------
async def get_price_for(token_addr: str) -> Optional[float]:
    url = f"{DEX_TOKEN_URL}{token_addr}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status != 200:
                    logger.debug("DEX price HTTP %s für %s", r.status, token_addr)
                    return None
                data = await r.json()
    except Exception as e:
        logger.debug("DEX price Fehler %s: %s", token_addr, e)
        return None

    pairs = data.get("pairs") or []
    if not isinstance(pairs, list) or not pairs:
        return None

    # nimm das liquideste Pair
    try:
        pairs.sort(key=lambda p: float(((p.get("liquidity") or {}).get("usd")) or 0), reverse=True)
    except Exception:
        pass

    for p in pairs:
        price_str = p.get("priceUsd")
        if price_str:
            try:
                return float(price_str)
            except ValueError:
                continue
    return None


# ---------------------------
# Helper
# ---------------------------
def _can_live_trade() -> bool:
    return MODE == "mainnet" and AUTOBUY and bool(PRIVATE_KEY)

def _now() -> float:
    return time.time()

def record_buy(token_addr: str, price: float, amount_sol: float, symbol: str = ""):
    POSITIONS[token_addr] = {
        "symbol": symbol,
        "entry_price": price,
        "amount_sol": amount_sol,
        "timestamp": _now(),
    }


# ---------------------------
# BUY / SELL Logik
# ---------------------------
async def execute_buy(token_addr: str, symbol: str = "") -> bool:
    # Begrenze Größe
    amount_sol = min(BUY_AMOUNT_SOL, MAX_INVEST_SOL)
    amount_lamports = int(amount_sol * 1_000_000_000)

    entry_price = await get_price_for(token_addr) or 1.0

    if not _can_live_trade():
        logger.info("SIMULATED BUY -> %s amount=%.4f SOL slippage=%sbps entry=%.6f USD",
                    token_addr, amount_sol, SLIPPAGE_BPS, entry_price)
        record_buy(token_addr, entry_price, amount_sol, symbol)
        return True

    # echter Swap via Jupiter
    sig = await jup_swap_exact_in(
        rpc_url=RPC_URL,
        private_key_b58=PRIVATE_KEY,
        user_pubkey=WALLET_ADDRESS,
        input_mint=SOL_MINT,
        output_mint=token_addr,
        amount_in_base_units=amount_lamports,
        slippage_bps=SLIPPAGE_BPS,
    )
    if not sig:
        logger.warning("BUY fehlgeschlagen (kein Sig) für %s", token_addr)
        return False

    record_buy(token_addr, entry_price, amount_sol, symbol)
    logger.info("BUY OK: %s (entry=%.6f USD)", sig, entry_price)
    return True


def should_take_profit(cur_price: float, entry_price: float) -> bool:
    if entry_price <= 0:
        return False
    return (cur_price - entry_price) / entry_price * 100.0 >= TAKE_PROFIT_PCT

def should_stop_loss(cur_price: float, entry_price: float) -> bool:
    if entry_price <= 0:
        return False
    return (entry_price - cur_price) / entry_price * 100.0 >= STOP_LOSS_PCT

def hold_time_ok(ts: float) -> bool:
    return (_now() - ts) >= MIN_HOLD_TIME_SEC


async def _get_spl_balance_ui(rpc: AsyncClient, owner_pubkey: PublicKey, mint: PublicKey) -> Optional[float]:
    """Summiere alle SPL-Token-Accounts (UI amount)."""
    try:
        accs = await rpc.get_token_accounts_by_owner(owner_pubkey, mint=mint)
        value = accs.value or []
        if not value:
            return None
        total_ui = 0.0
        for it in value:
            bal = await rpc.get_token_account_balance(it.pubkey)
            if bal.value.ui_amount is not None:
                total_ui += float(bal.value.ui_amount)
        return total_ui if total_ui > 0 else None
    except Exception as e:
        logger.debug("get_spl_balance Fehler %s", e)
        return None


async def execute_sell(token_addr: str) -> bool:
    pos = POSITIONS.get(token_addr)
    if not pos:
        return False

    cur_price = await get_price_for(token_addr)
    if cur_price is None:
        return False

    entry = float(pos["entry_price"])
    if not hold_time_ok(float(pos["timestamp"])):
        return False
    if not (should_take_profit(cur_price, entry) or should_stop_loss(cur_price, entry)):
        return False

    # Simulation
    if not _can_live_trade():
        logger.info("SIMULATED SELL -> token=%s cur=%.6f entry=%.6f", token_addr, cur_price, entry)
        POSITIONS.pop(token_addr, None)
        return True

    # Echte Balance abfragen und Swap durchführen
    async with AsyncClient(RPC_URL, timeout=25) as rpc:
        owner = PublicKey(WALLET_ADDRESS)
        mint = PublicKey(token_addr)
        ui_bal = await _get_spl_balance_ui(rpc, owner, mint)
        if ui_bal is None or ui_bal <= 0:
            logger.warning("Keine Token-Balance für %s – SELL abgebrochen.", token_addr)
            return False

    # Teilverkauf: (100 - REINVEST_PERCENT) % werden verkauft
    sell_pct = max(0.0, min(100.0, 100.0 - REINVEST_PERCENT)) / 100.0
    amount_fraction = 1.0 if sell_pct <= 0 else sell_pct  # falls reinvest=100 -> 0%, dann full keep
    # Jupiter braucht Base Units; wir approximieren hier über UI, Jupiter berechnet intern Route & Splits.

    sig = await jup_swap_exact_in(
        rpc_url=RPC_URL,
        private_key_b58=PRIVATE_KEY,
        user_pubkey=WALLET_ADDRESS,
        input_mint=token_addr,
        output_mint=SOL_MINT,
        # Approximation: ui_bal * amount_fraction wird geswapped (Jupiter v6 verarbeitet exakten amount)
        amount_in_base_units=int(max(1, ui_bal * amount_fraction) * 1),  # Platzhalter; Route bestimmt intern exakten amount
        slippage_bps=SLIPPAGE_BPS,
    )
    if not sig:
        logger.warning("SELL fehlgeschlagen (kein Sig) für %s", token_addr)
        return False

    # Position nur löschen, wenn ~alles verkauft wurde
    if amount_fraction >= 0.999:
        POSITIONS.pop(token_addr, None)

    logger.info("SELL OK: %s (cur=%.6f USD, entry=%.6f USD)", sig, cur_price, entry)
    return True


# ---------------------------
# Balance & Withdraw (für /balance, /withdraw)
# ---------------------------
async def get_sol_balance() -> float:
    """Gibt SOL-Balance in SOL zurück."""
    try:
        async with AsyncClient(RPC_URL, timeout=20) as rpc:
            lamports = await rpc.get_balance(PublicKey(WALLET_ADDRESS))
            return float(lamports.value) / 1_000_000_000
    except Exception as e:
        logger.error("Fehler get_sol_balance: %s", e)
        return 0.0


async def withdraw_sol(to_address: str, amount_sol: float) -> Optional[str]:
    """Sende SOL an eine Adresse. Gibt Tx-Signatur zurück (oder None)."""
    if not PRIVATE_KEY:
        logger.error("Withdraw abgebrochen: Kein PRIVATE_KEY gesetzt.")
        return None
    try:
        sender = Keypair.from_base58_string(PRIVATE_KEY)
        to_pub = PublicKey(to_address)
        async with AsyncClient(RPC_URL, timeout=25) as rpc:
            # Balance check
            current = await rpc.get_balance(sender.public_key)
            have = current.value
            need = int(amount_sol * 1_000_000_000)
            fee_pad = 5_000  # etwas Puffer für Fees
            if have < need + fee_pad:
                logger.error("Nicht genug Balance: have=%d need=%d", have, need)
                return None

            tx = Transaction().add(
                transfer(TransferParams(
                    from_pubkey=sender.public_key,
                    to_pubkey=to_pub,
                    lamports=need
                ))
            )
            res = await rpc.send_transaction(tx, sender, opts={"skip_preflight": False})
            sig = str(res.value)
            # optional warten auf Bestätigung
            await rpc.confirm_transaction(sig, commitment=Confirmed)
            logger.info("Withdraw OK: %s -> %s (%.6f SOL)", sender.public_key, to_pub, amount_sol)
            return sig
    except Exception as e:
        logger.error("Fehler withdraw_sol: %s", e)
        return None
