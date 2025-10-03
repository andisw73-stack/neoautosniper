# trading.py — Wallet & Trading über Jupiter v6 (mit Auto-ATA)
# - Echte Swaps (SOL <-> Token) mit Quote-/Swap-API
# - Legt fehlende Associated Token Accounts (ATA) automatisch an
# - Slippage/Timeout/Priority-Fee per ENV und /set steuerbar

from __future__ import annotations
from typing import Optional, Tuple, List
import os, json, base64, requests

# ===== Konstanten =====
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

# ===== ENV Helpers =====
def _pick_env(keys: List[str]) -> Tuple[Optional[str], Optional[str]]:
    for k in keys:
        v = os.getenv(k)
        if v and v.strip():
            return v.strip(), k
    return None, None

def _pick_rpc() -> str:
    return (
        os.getenv("SOLANA_RPC")
        or os.getenv("SOLANA_RPC_URL")
        or os.getenv("RPC_URL")
        or "https://api.mainnet-beta.solana.com"
    ).strip()

# ===== Trader =====
class JupiterTrader:
    def __init__(self):
        self.rpc_url = _pick_rpc()
        self.slippage_bps = int(os.getenv("SLIPPAGE_BPS", "100"))      # 1.00% default
        self.swap_timeout = int(os.getenv("SWAP_TIMEOUT", "45"))        # Sekunden
        self.priority_lamports = int(os.getenv("JUPITER_PRIORITY_LAMPORTS", "5000"))
        self.as_legacy = os.getenv("JUPITER_AS_LEGACY", "1") in ("1","true","True")
        self.dynamic_cu = os.getenv("JUPITER_DYNAMIC_CU_LIMIT", "1") in ("1","true","True")
        self.wrap_unwrap = os.getenv("WRAP_UNWRAP_SOL", "1") in ("1","true","True")

        # Lazy Imports / Flags
        self._sol_ok = False
        try:
            from solana.rpc.api import Client          # noqa: F401
            from solana.publickey import PublicKey     # noqa: F401
            from solana.keypair import Keypair         # noqa: F401
            from spl.token.instructions import get_associated_token_address, create_associated_token_account  # noqa: F401
            import base58                              # noqa: F401
            self._sol_ok = True
        except Exception:
            self._sol_ok = False

        # Wallet laden
        self._client = None
        self._keypair = None
        self._pubkey = None

        secret, src = _pick_env([
            "WALLET_SECRET",
            "SOL_PRIVATE_KEY_B58",
            "SOL_PRIVATE_KEY_JSON",
            "WalletSecret",
            "SolPrivateKeyB58",
        ])
        if not secret:
            print("[WALLET] Kein Secret gefunden (setze WALLET_SECRET).")
            return

        if not self._sol_ok:
            print("[WALLET] solana/spl/base58-Pakete fehlen – Trading deaktiviert.")
            return

        try:
            from solana.rpc.api import Client
            self._client = Client(self.rpc_url)
            self._keypair = self._parse_secret(secret)
            self._pubkey = str(self._keypair.public_key)
            print(f"[WALLET] Secret OK | RPC={self.rpc_url}")
            print(f"[WALLET] Address: {self._pubkey}")
        except Exception as e:
            print(f"[WALLET] Fehler beim Laden: {e}")

    # ---------- Wallet Basics ----------
    @property
    def public_key(self) -> Optional[str]:
        return self._pubkey

    def _rpc(self):
        return self._client

    def _parse_secret(self, secret: str):
        from solana.keypair import Keypair
        import base58, json
        s = secret.strip()
        if s.startswith("["):
            arr = json.loads(s)
            return Keypair.from_secret_key(bytes(arr))
        return Keypair.from_secret_key(base58.b58decode(s))

    def get_sol_balance(self) -> float:
        if not (self._sol_ok and self._pubkey):
            return 0.0
        try:
            lamports = self._rpc().get_balance(self._keypair.public_key).value
            return lamports / LAMPORTS_PER_SOL
        except Exception:
            return 0.0

    def describe_wallet(self) -> str:
        if not self._sol_ok:
            return "Wallet: Python-Pakete fehlen (solana/spl/base58)."
        if not self._pubkey:
            return "Wallet: kein Private Key (WALLET_SECRET) gefunden."
        try:
            bal = self.get_sol_balance()
            return f"Wallet\n• Address: <code>{self._pubkey}</code>\n• SOL: {bal:.6f}"
        except Exception as e:
            return f"Wallet-Fehler: {e}"

    # ---------- Token Helpers ----------
    def get_token_balance(self, mint: str) -> Tuple[float, int, int]:
        """
        Returns (ui_amount, decimals, raw_amount).
        """
        if not (self._sol_ok and self._pubkey):
            return 0.0, 0, 0
        try:
            from solana.publickey import PublicKey
            owner = self._keypair.public_key
            mint_pk = PublicKey(mint)
            resp = self._rpc().get_token_accounts_by_owner_json_parsed(owner, mint=mint_pk)
            arr = resp.value
            if not arr:
                return 0.0, 0, 0
            info = arr[0].account.data.parsed["info"]["tokenAmount"]
            ui = float(info.get("uiAmount", 0.0) or 0.0)
            dec = int(info.get("decimals", 0) or 0)
            raw = int(info.get("amount", "0") or "0")
            return ui, dec, raw
        except Exception:
            return 0.0, 0, 0

    def _ensure_ata(self, mint: str) -> Optional[str]:
        try:
            from solana.publickey import PublicKey
            from spl.token.instructions import get_associated_token_address, create_associated_token_account
            from solana.transaction import Transaction
            from solana.rpc.types import TxOpts

            mint_pk = PublicKey(mint)
            owner = self._keypair.public_key
            ata = get_associated_token_address(owner, mint_pk)
            info = self._rpc().get_account_info(ata, commitment="confirmed")
            if info.value is None:
                tx = Transaction()
                tx.add(create_associated_token_account(payer=owner, owner=owner, mint=mint_pk))
                sig = self._rpc().send_transaction(tx, self._keypair, opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"))
                self._rpc().confirm_transaction(sig.value, commitment="confirmed")
            return str(ata)
        except Exception as e:
            print("[ATA] Fehler:", e)
            return None

    # ---------- Jupiter Swap ----------
    def _jupiter_quote(self, input_mint: str, output_mint: str, amount_raw: int) -> dict:
        url = os.getenv("JUPITER_QUOTE_URL", "https://quote-api.jup.ag/v6/quote")
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_raw,
            "slippageBps": self.slippage_bps,
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "true" if self.as_legacy else "false",
        }
        r = requests.get(url, params=params, timeout=self.swap_timeout)
        r.raise_for_status()
        return r.json()

    def _jupiter_swap(self, quote: dict, output_mint: Optional[str] = None) -> str:
        url = os.getenv("JUPITER_SWAP_URL", "https://quote-api.jup.ag/v6/swap")
        payload = {
            "userPublicKey": self._pubkey,
            "quoteResponse": quote,
            "wrapAndUnwrapSol": self.wrap_unwrap,
            "asLegacyTransaction": self.as_legacy,
            "dynamicComputeUnitLimit": self.dynamic_cu,
            "prioritizationFeeLamports": self.priority_lamports,
        }
        # Ziel-ATA (nur wenn Output != SOL)
        if output_mint and output_mint != SOL_MINT:
            ata = self._ensure_ata(output_mint)
            if ata:
                payload["destinationTokenAccount"] = ata

        r = requests.post(url, json=payload, timeout=self.swap_timeout)
        r.raise_for_status()
        data = r.json()
        b64tx = data.get("swapTransaction")
        if not b64tx:
            raise RuntimeError(f"Swap-Error: {data}")
        # Wir nutzen Legacy-Transaktionen (asLegacy=true)
        from solana.transaction import Transaction
        from solana.rpc.types import TxOpts
        raw = base64.b64decode(b64tx)
        tx = Transaction.deserialize(raw)
        tx.sign(self._keypair)
        sig = self._rpc().send_raw_transaction(bytes(tx), opts=TxOpts(skip_preflight=False, max_retries=3))
        self._rpc().confirm_transaction(sig.value, commitment="confirmed")
        return str(sig.value)

    # ---------- Public: BUY & SELL ----------
    def buy_with_sol(self, out_mint: str, amount_sol: float) -> str:
        """
        Kauft amount_sol (SOL) -> out_mint (Token).
        """
        if not (self._sol_ok and self._pubkey):
            return "⚠️ Trading inaktiv (Pakete oder WALLET_SECRET fehlen)."
        if amount_sol <= 0:
            return "⚠️ Amount <= 0."
        amount_raw = int(float(amount_sol) * LAMPORTS_PER_SOL)
        try:
            quote = self._jupiter_quote(SOL_MINT, out_mint, amount_raw)
            sig = self._jupiter_swap(quote, output_mint=out_mint)
            return f"✅ BUY {amount_sol:.6f} SOL -> {out_mint[:6]}…\nTX: https://solscan.io/tx/{sig}"
        except Exception as e:
            return f"❌ Buy-Fehler: {e}"

    def sell_to_sol(self, in_mint: str, pct: float) -> str:
        """
        Verkauft pct% des Token-Bestandes (in_mint) -> SOL.
        """
        if not (self._sol_ok and self._pubkey):
            return "⚠️ Trading inaktiv (Pakete oder WALLET_SECRET fehlen)."
        if pct <= 0:
            return "⚠️ Prozent muss > 0 sein."
        try:
            # Token-Balance (roh) ermitteln
            from solana.publickey import PublicKey
            from spl.token.instructions import get_associated_token_address
            owner = self._keypair.public_key
            mint_pk = PublicKey(in_mint)
            ata = get_associated_token_address(owner, mint_pk)
            bal = self._rpc().get_token_account_balance(ata).value
            if not bal:
                return "⚠️ Kein Token-Bestand."
            raw = int(bal.amount)
            if raw <= 0:
                return "⚠️ Kein Token-Bestand."
            raw_to_sell = int(raw * min(pct, 100.0) / 100.0)

            quote = self._jupiter_quote(in_mint, SOL_MINT, raw_to_sell)
            sig = self._jupiter_swap(quote, output_mint=SOL_MINT)
            return f"✅ SELL {pct:.2f}% ({in_mint[:6]}…) -> SOL\nTX: https://solscan.io/tx/{sig}"
        except Exception as e:
            return f"❌ Sell-Fehler: {e}"
