# trading.py
# Jupiter v6 Quote+Swap (REST). Signieren & Senden via solana/solders.
from typing import Optional
import json, base64, requests

SOL_MINT = "So11111111111111111111111111111111111111112"

class JupiterTrader:
    def __init__(self, rpc_url: str, wallet_secret: str, slippage_bps: int = 50):
        self.rpc_url = rpc_url
        self.wallet_secret_raw = wallet_secret or ""
        self.slippage_bps = slippage_bps
        self._pubkey = None
        # Lazy import: nur wenn gebraucht
        self._sol_ok = False
        try:
            from solders.keypair import Keypair  # noqa
            from solana.rpc.api import Client    # noqa
            from solders.transaction import VersionedTransaction  # noqa
            import base58                         # noqa
            self._sol_ok = True
        except Exception:
            self._sol_ok = False

        if self._sol_ok and self.wallet_secret_raw:
            try:
                self._keypair = self._parse_secret(self.wallet_secret_raw)
                self._client  = self._rpc()
                self._pubkey  = str(self._keypair.pubkey())
            except Exception:
                self._pubkey = None

    # ------------ Helpers ------------
    @property
    def public_key(self) -> Optional[str]:
        return self._pubkey

    def _rpc(self):
        from solana.rpc.api import Client
        return Client(self.rpc_url)

    def _parse_secret(self, secret: str):
        from solders.keypair import Keypair
        import base58, json
        s = secret.strip()
        if s.startswith("["):
            arr = json.loads(s)
            return Keypair.from_bytes(bytes(arr))
        # base58
        return Keypair.from_bytes(base58.b58decode(s))

    def describe_wallet(self) -> str:
        if not self._sol_ok:
            return "Wallet: (Python-Pakete fehlen – installiere 'solders', 'solana', 'base58')"
        if not self.public_key:
            return "Wallet: nicht konfiguriert. Setze WALLET_SECRET (base58 oder JSON-Array)."
        try:
            bal = self._rpc().get_balance(self._keypair.pubkey()).value / 1e9
            return f"Wallet\n• Address: <code>{self.public_key}</code>\n• SOL: {bal:.6f}"
        except Exception as e:
            return f"Wallet-Fehler: {e}"

    # ------------ Buy / Sell ------------
    def buy_with_sol(self, out_mint: str, amount_sol: float) -> str:
        if not self._sol_ok or not self.public_key:
            return "⚠️ Trading nicht aktiv: fehlende Pakete oder WALLET_SECRET."
        lamports = int(amount_sol * 1_000_000_000)
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
                    "userPublicKey": self.public_key,
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

    def sell_to_sol(self, in_mint: str, qty: str) -> str:
        if not self._sol_ok or not self.public_key:
            return "⚠️ Trading nicht aktiv: fehlende Pakete oder WALLET_SECRET."
        # qty: Zahl oder '50%' etc. (vereinfachte Variante – erwartet absolute Token-Menge in kleinsten Einheiten ist komplex;
        # hier wird Jupiter die 'amount' in Token-Decimals erwarten. Für eine robuste Lösung bräuchte man Token-Decimals.)
        # Für einen einfachen ersten Schritt verkaufen wir 100% (route ohne 'amount') -> Jupiter erlaubt das nicht direkt.
        return "Noch nicht implementiert: /sell (Token-Decimals nötig)."

    def _sign_and_send(self, b64tx: str) -> str:
        from solders.transaction import VersionedTransaction
        raw = base64.b64decode(b64tx)
        tx  = VersionedTransaction.from_bytes(raw)
        tx  = tx.sign([self._keypair])
        raw2 = bytes(tx)
        sig = self._rpc().send_raw_transaction(raw2).value
        return str(sig)
