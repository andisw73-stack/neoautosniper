# trading.py — Wallet & Trading (Jupiter v6) für NeoAutoSniper
# - Liest WALLET_SECRET (oder Alternativen) aus ENV
# - Base58 ODER JSON-Array möglich
# - Bietet: SOL-Balance, BUY (SOL->Token), SELL (Token->SOL)

from __future__ import annotations
from typing import Optional, Tuple, List
import os, json, base64, requests

SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

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

class JupiterTrader:
    def __init__(self, rpc_url: Optional[str] = None, wallet_secret: Optional[str] = None, slippage_bps: int = 50):
        self.rpc_url = (rpc_url or _pick_rpc()).strip()
        self.slippage_bps = int(slippage_bps)
        self._sol_ok = False
        self._client = None
        self._keypair = None
        self._pubkey = None

        try:
            from solana.rpc.api import Client          # noqa
            from solders.keypair import Keypair        # noqa
            from solders.transaction import VersionedTransaction  # noqa
            from solders.pubkey import Pubkey          # noqa
            import base58                               # noqa
            self._sol_ok = True
        except Exception:
            self._sol_ok = False

        if wallet_secret is None:
            secret, src = _pick_env([
                "WALLET_SECRET",
                "SOL_PRIVATE_KEY_B58",
                "SOL_PRIVATE_KEY_JSON",
                "WalletSecret",
                "SolPrivateKeyB58",
            ])
        else:
            secret, src = wallet_secret, "passed_arg"

        if not secret:
            print("[WALLET] kein Secret gefunden (ENV: WALLET_SECRET | SOL_PRIVATE_KEY_B58 | SOL_PRIVATE_KEY_JSON)")
            return

        if not self._sol_ok:
            print("[WALLET] Python-Pakete fehlen (solana/solders/base58). Trading deaktiviert.")
            return

        try:
            from solana.rpc.api import Client
            self._client = Client(self.rpc_url)
            self._keypair = self._parse_secret(secret)
            self._pubkey = str(self._keypair.pubkey())
            print(f"[WALLET] Secret geladen aus ENV=WALLET_SECRET | RPC={self.rpc_url}")
            print(f"[WALLET] Public address: {self._pubkey}")
        except Exception as e:
            print(f"[WALLET] Fehler beim Laden des Secrets: {e}")
            self._keypair = None
            self._pubkey = None

    # ------------- Helpers -------------
    @property
    def public_key(self) -> Optional[str]:
        return self._pubkey

    def _rpc(self):
        return self._client

    def _parse_secret(self, secret: str):
        from solders.keypair import Keypair
        import base58, json
        s = secret.strip()
        if s.startswith("["):
            arr = json.loads(s)
            return Keypair.from_bytes(bytes(arr))
        return Keypair.from_bytes(base58.b58decode(s))

    # ------------- Wallet -------------
    def get_sol_balance(self) -> float:
        if not self._sol_ok or not self._pubkey:
            return 0.0
        try:
            lamports = self._rpc().get_balance(self._keypair.pubkey()).value
            return lamports / 1_000_000_000
        except Exception:
            return 0.0

    def describe_wallet(self) -> str:
        if not self._sol_ok:
            return "Wallet: Python-Pakete fehlen (solana/solders/base58) – Anzeige eingeschränkt."
        if not self._pubkey:
            return "Wallet: kein Private Key gefunden/geladen. Setze ENV WALLET_SECRET."
        try:
            bal = self.get_sol_balance()
            return f"Wallet\n• Address: <code>{self._pubkey}</code>\n• SOL: {bal:.6f}"
        except Exception as e:
            return f"Wallet-Fehler: {e}"

    # ------------- Jupiter Trades -------------
    def buy_with_sol(self, out_mint: str, amount_sol: float) -> str:
        if not self._sol_ok or not self._pubkey:
            return "⚠️ Trading inaktiv (fehlende Pakete oder kein Private Key)."
        lamports = int(float(amount_sol) * 1_000_000_000)
        try:
            quote = requests.get(
                "https://quote-api.jup.ag/v6/quote",
                params={
                    "inputMint": SOL_MINT,
                    "outputMint": out_mint,
                    "amount": lamports,
                    "slippageBps": self.slippage_bps,
                },
                timeout=20
            ).json()
            if "error" in quote:
                return f"Quote-Error: {quote.get('error')}"
            swap = requests.post(
                "https://quote-api.jup.ag/v6/swap",
                json={
                    "userPublicKey": self._pubkey,
                    "quoteResponse": quote,
                    "wrapAndUnwrapSol": True
                },
                timeout=20
            ).json()
            b64tx = swap.get("swapTransaction")
            if not b64tx:
                return f"Swap-Error: {swap}"
            sig = self._sign_and_send(b64tx)
            return f"✅ BUY {amount_sol} SOL -> {out_mint}\nTX: https://solscan.io/tx/{sig}"
        except Exception as e:
            return f"Buy-Fehler: {e}"

    def sell_to_sol(self, in_mint: str, amount_ui: float) -> str:
        """Verkauft 'amount_ui' Einheiten des Tokens (in UI-Units) gegen SOL."""
        if not self._sol_ok or not self._pubkey:
            return "⚠️ Trading inaktiv (fehlende Pakete oder kein Private Key)."
        try:
            amt_raw, dec = self._ui_to_raw(in_mint, amount_ui)
            if amt_raw < 1:
                return "Zu wenig Token (Dust)."
            quote = requests.get(
                "https://quote-api.jup.ag/v6/quote",
                params={
                    "inputMint": in_mint,
                    "outputMint": SOL_MINT,
                    "amount": amt_raw,
                    "slippageBps": self.slippage_bps,
                },
                timeout=20
            ).json()
            if "error" in quote:
                return f"Quote-Error: {quote.get('error')}"
            swap = requests.post(
                "https://quote-api.jup.ag/v6/swap",
                json={
                    "userPublicKey": self._pubkey,
                    "quoteResponse": quote,
                    "wrapAndUnwrapSol": True
                },
                timeout=20
            ).json()
            b64tx = swap.get("swapTransaction")
            if not b64tx:
                return f"Swap-Error: {swap}"
            sig = self._sign_and_send(b64tx)
            return f"✅ SELL {amount_ui:.6f} ({in_mint[:6]}...) -> SOL\nTX: https://solscan.io/tx/{sig}"
        except Exception as e:
            return f"Sell-Fehler: {e}"

    # ------------- Token-Balance Helpers -------------
    def get_token_balance(self, mint: str) -> Tuple[float, int]:
        """Gibt (ui_amount, decimals) zurück."""
        from solders.pubkey import Pubkey
        try:
            owner = self._keypair.pubkey()
            mint_pk = Pubkey.from_string(mint)
            resp = self._rpc().get_token_accounts_by_owner_json_parsed(owner, mint=mint_pk)
            val = resp.value
            if not val:
                return 0.0, 0
            acc = val[0]
            info = acc.account.data.parsed["info"]["tokenAmount"]
            ui_amt = float(info.get("uiAmount", 0.0))
            dec = int(info.get("decimals", 0))
            return ui_amt, dec
        except Exception:
            return 0.0, 0

    def _ui_to_raw(self, mint: str, ui_amt: float) -> Tuple[int, int]:
        ui, dec = self.get_token_balance(mint)  # holt auch decimals
        if dec <= 0:
            dec = 6  # fallback
        raw = int(float(ui_amt) * (10 ** dec))
        return raw, dec

    # ------------- TX Sign & Send -------------
    def _sign_and_send(self, b64tx: str) -> str:
        from solders.transaction import VersionedTransaction
        raw = base64.b64decode(b64tx)
        tx = VersionedTransaction.from_bytes(raw)
        tx = tx.sign([self._keypair])
        raw2 = bytes(tx)
        sig = self._rpc().send_raw_transaction(raw2).value
        return str(sig)
