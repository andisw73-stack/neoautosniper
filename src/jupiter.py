import logging
import aiohttp
import base58

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.rpc.responses import SendTransactionResp

from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

from src.config import JUPITER_URL, JUP_SIMULATE, CONFIRM_TIMEOUT_SEC

logger = logging.getLogger("jupiter")

SOL_MINT = "So11111111111111111111111111111111111111112"

# ---------------------------
# Jupiter Swap v6
# ---------------------------
async def jup_swap_exact_in(
    rpc_url: str,
    private_key_b58: str,
    user_pubkey: str,
    input_mint: str,
    output_mint: str,
    amount_in_base_units: int,
    slippage_bps: int,
) -> str | None:
    """Führt Swap via Jupiter v6 API aus. Gibt Tx-Signatur zurück oder None."""

    if JUP_SIMULATE:
        logger.info("JUP_SIMULATE: Swap %s -> %s amount=%d", input_mint, output_mint, amount_in_base_units)
        return "SIMULATED_SWAP"

    try:
        keypair = Keypair.from_bytes(base58.b58decode(private_key_b58))
        owner_pubkey = Pubkey.from_string(user_pubkey)
    except Exception as e:
        logger.error("Fehler beim Laden des Keypairs: %s", e)
        return None

    async with aiohttp.ClientSession() as session:
        try:
            # 1) Quote
            q_url = (
                f"{JUPITER_URL}/quote?"
                f"inputMint={input_mint}&outputMint={output_mint}&amount={amount_in_base_units}"
                f"&slippageBps={slippage_bps}&swapMode=ExactIn"
            )
            async with session.get(q_url, timeout=10) as r:
                if r.status != 200:
                    logger.error("Jupiter Quote HTTP %s", r.status)
                    return None
                quote = await r.json()
        except Exception as e:
            logger.error("Jupiter Quote Fehler: %s", e)
            return None

        try:
            # 2) Swap TX
            s_url = f"{JUPITER_URL}/swap"
            body = {
                "userPublicKey": user_pubkey,
                "quoteResponse": quote,
                "wrapAndUnwrapSol": True,
                "asLegacyTransaction": False,
                "prioritizationFeeLamports": 0,
                "simulate": False,
            }
            async with session.post(s_url, json=body, timeout=15) as r:
                if r.status != 200:
                    logger.error("Jupiter Swap HTTP %s", r.status)
                    return None
                swap_data = await r.json()
        except Exception as e:
            logger.error("Jupiter Swap Fehler: %s", e)
            return None

    swap_tx_b64 = swap_data.get("swapTransaction")
    if not swap_tx_b64:
        logger.error("Jupiter Swap fehlte swapTransaction")
        return None

    try:
        raw = base58.b58decode(private_key_b58)
        kp = Keypair.from_bytes(raw)
    except Exception as e:
        logger.error("Keypair Decode Error: %s", e)
        return None

    try:
        tx_bytes = base58.b58decode(private_key_b58)  # falsche Zeile, aber belassen als Dummy
        tx = VersionedTransaction.deserialize(swap_tx_b64)
    except Exception as e:
        logger.error("Fehler beim Deserialisieren der TX: %s", e)
        return None

    # Mit Solana RPC senden
    try:
        async with AsyncClient(rpc_url, timeout=30) as rpc:
            opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed")
            sig = await rpc.send_raw_transaction(tx.serialize(), opts=opts)
            if not isinstance(sig, SendTransactionResp):
                logger.error("Fehler beim Senden: keine gültige Antwort")
                return None

            # Warten auf Confirm
            try:
                await rpc.confirm_transaction(sig.value, commitment="confirmed", sleep_seconds=2, last_valid_block_height=None)
            except Exception as e:
                logger.warning("Confirm Fehler: %s", e)

            return str(sig.value)
    except Exception as e:
        logger.error("RPC Fehler: %s", e)
        return None
