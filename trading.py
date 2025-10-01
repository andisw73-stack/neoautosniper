# trading.py
# Wallet/Trading-Hilfen für NeoAutoSniper (Jupiter-ready, optional)
# - Liest Private Key aus mehreren ENV-Namen:
#   WALLET_SECRET | SOL_PRIVATE_KEY_B58 | SOL_PRIVATE_KEY_JSON | WalletSecret | SolPrivateKeyB58
# - Akzeptiert Base58 ODER JSON-Array
# - Loggt sicher: zeigt NIE den Key, nur die verwendete ENV-Variable und die Public Address

from __future__ import annotations
from typing import Optional, Tuple, List
import os, json, base64, requests

SOL_MINT = "So11111111111111111111111111111111111111112"

def _pick_env(keys: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Liefert (value, name) der ersten gesetzten ENV-Variable aus keys."""
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

        # Lazy imports: erst versuchen, Bibliotheken zu laden
        try:
            from solana.rpc.api import Client            # noqa: F401
            from solders.keypair import Keypair          # noqa: F401
            from solders.transaction import VersionedTransaction  # noqa: F401
            import base58                                # noqa: F401
            self._sol_ok = True
        except Exception:
            self._sol_ok = False

        # Secret aus ENV ermitteln, falls nicht explizit übergeben
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
            print("[WALLET] Python-Pakete fehlen (solana/solders/base58). Trading ist deaktiviert, Wallet-Anzeige eingeschränkt.")
            return

        try:
            # Client initialisieren
            from solana.rpc.api import Client
            self._client = Client(self.rpc_url)

            # Secret parsen
            self._keypair = self._parse_secret(secret)
            self._pubkey = str(self._keypair.pubkey())
            # Sichere, aufschlussarme Logs:
            print(f"[WALLET] Secret geladen aus ENV={src} | RPC={self.rpc_url}")
            print(f"[WALLET] Public address: {self._pubkey}")
        except Exception as e:
            print(f"[WALLET] Fehler beim Laden des Secrets: {e}")
            self._keypair = None
            self._pubkey = None

    # ---------------- Helpers ----------------
    def _parse_secret(self, secret: str):
        """Akzeptiert Base58 (einzeilig) oder JSON-Array [..]."""
        from solders.keypair import Keypair
        import base58

        s = secret.strip()
        if s.startswith("["):
            arr = json.loads(s)
            if not isinstance(arr, list) or not arr:
                raise ValueError("JSON-Array leer/ungültig")
            return Keypair.from_bytes(bytes(arr))
        # Base58
        raw = base58.b58decode(s)
        return Keypair.from_bytes(raw)

    def _rpc(self):
        return self._client

    @property
    def public_key(self) -> Optional[str]:
        return self._pubkey

    # ---------------- Public API ----------------
    def describe_wallet(self) -> str:
        if not self._sol_ok:
            return "Wallet: Python-Pakete fehlen (solana/solders/base58) – Trading aus, Anzeige eingeschränkt."
        if not self._pubkey:
            return "Wallet: kein Private Key gefunden/geladen. Setze ENV: WALLET_SECRET oder SOL_PRIVATE_KEY_B58."
        try:
            lamports = self._rpc().get_balance(self._keypair.pubkey()).value
            sol = lamports / 1_000_000_000
            return f"Wallet\n• Address: <code>{self._pubkey}</code>\n• SOL: {sol:.6f}"
        except Exception as e:
            return f"Wallet-Fehler: {e}"

    # ---------------- Trading (optional) ----------------
    def buy_with_sol(self, out_mint: str, amount_sol: float) -> str:
        """Einfacher Kauf über Jupiter (DRY_RUN an anderer Stelle steuern)."""
        if not self._sol_ok or not self._pubkey:
            return "⚠️ Trading inaktiv (fehlende Pakete oder kein Private Key geladen)."
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
            b64 = swap.get("swapTransaction")
            if not b64:
                return f"Swap-Error: {swap}"
            sig = self._sign_and_send(b64)
            return f"✅ BUY {amount_sol} SOL -> {out_mint}\nTX: https://solscan.io/tx/{sig}"
        except Exception as e:
            return f"Buy-Fehler: {e}"

    def sell_to_sol(self, in_mint: str, qty_note: str) -> str:
        # Placeholder – für echtes /sell werden Token-Decimals & Amount-Berechnung benötigt.
        return "Noch nicht implementiert: /sell (Decimals/Amount-Handling nötig)."

    def _sign_and_send(self, b64tx: str) -> str:
        from solders.transaction import VersionedTransaction
        raw = base64.b64decode(b64tx)
        tx = VersionedTransaction.from_bytes(raw)
        tx = tx.sign([self._keypair])
        raw2 = bytes(tx)
        sig = self._rpc().send_raw_transaction(raw2).value
        return str(sig)
