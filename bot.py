# bot.py
# NeoAutoSniper – Scanner + Telegram-Bot + (optional) Trading über Jupiter
# ---------------------------------------------------------------
import os, time, json, math, threading, datetime as dt
from typing import List, Dict, Any, Optional
import requests

from telegram_handlers import TelegramBot
from trading import JupiterTrader

API_DS = "https://api.dexscreener.com"

# ----------------------- Konfiguration aus ENV -----------------------
def env_int(key: str, default: int) -> int:
    try:
        v = os.getenv(key, "").strip()
        if not v:
            return default
        return int(v)
    except Exception:
        return default

def env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")

STRATEGY       = os.getenv("STRATEGY", "dexscreener")
CHAIN          = os.getenv("STRAT_CHAIN", "solana")
STRICT_QUOTE   = env_bool("STRICT_QUOTE", False)
STRAT_QUOTE    = os.getenv("STRAT_QUOTE", "SOL").upper()   # SOL/USDC/…
LIQ_MIN        = env_int("STRAT_LIQ_MIN", 130000)
FDV_MAX        = env_int("STRAT_FDV_MAX", 400000)
VOL5M_MIN      = env_int("STRAT_VOL5M_MIN", 2000)
VOL_BEST_MIN   = env_int("STRAT_VOL_BEST_MIN", 5000)
MAX_AGE_MIN    = env_int("MAX_AGE_MIN", 120)
MAX_ITEMS      = env_int("MAX_ITEMS", 200)

INTERVAL       = env_int("INTERVAL", 60)
TIMEOUT        = env_int("TIMEOUT", 15)

DRY_RUN        = env_bool("DRY_RUN", True)   # True = nur simulieren
AUTO_BUY       = env_bool("AUTO_BUY", False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "").strip()  # optional

# Wallet/Trading
RPC_URL        = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
WALLET_SECRET  = os.getenv("WALLET_SECRET", "").strip()     # base58 oder JSON-Array
SLIPPAGE_BPS   = env_int("SLIPPAGE_BPS", 50)                # 0.5%
DEFAULT_BUY_SOL= float(os.getenv("DEFAULT_BUY_SOL", "0.05"))# Standard-Kaufsumme

# ----------------------- Util -----------------------
def now() -> str:
    return dt.datetime.utcnow().strftime("%H:%M:%S")

def mins_ago(ts_ms: Optional[int]) -> Optional[int]:
    if not ts_ms:
        return None
    return int((time.time()*1000 - ts_ms) / 60000)

def summarize_settings() -> str:
    return (
        f"• Strategy: {STRATEGY} | Chain: {CHAIN} | Quote: {STRAT_QUOTE} (STRICT={1 if STRICT_QUOTE else 0})\n"
        f"• LIQ_MIN: {LIQ_MIN:,} | FDV_MAX: {FDV_MAX:,}\n"
        f"• VOL5M_MIN: {VOL5M_MIN:,} | VOL_BEST_MIN: {VOL_BEST_MIN:,}\n"
        f"• MAX_AGE_MIN: {MAX_AGE_MIN} | MAX_ITEMS: {MAX_ITEMS}\n"
        f"• DRY_RUN: {1 if DRY_RUN else 0} | AUTO_BUY: {1 if AUTO_BUY else 0}\n"
        f"• INTERVAL: {INTERVAL}s | TIMEOUT: {TIMEOUT}s"
    )

# ----------------------- Dexscreener: Scannen -----------------------
def ds_search_batches() -> List[Dict[str, Any]]:
    """
    DexScreener liefert /latest/dex/pairs/solana häufig 404.
    Daher mehrere Search-Queries als Fallback mixen.
    """
    queries = ["solana", "SOL", "SOL/USDC", "USDC/SOL"]
    pairs: Dict[str, Dict[str, Any]] = {}
    for q in queries:
        try:
            url = f"{API_DS}/latest/dex/search?q={q}"
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"[{now()}] [SCAN] {url} -> HTTP {r.status_code}")
                continue
            data = r.json().get("pairs", []) or []
            for p in data:
                # Use pairAddress as unique key if available else url
                key = p.get("pairAddress") or p.get("url")
                if not key:
                    key = json.dumps(p, sort_keys=True)[:64]
                pairs[key] = p
        except Exception as e:
            print(f"[{now()}] [SCAN] ERR search {q}: {e}")
    uniq = list(pairs.values())
    print(f"[{now()}] [SCAN] collected {len(uniq)} unique pairs from {len(uniq)} raw results (3+fallback sources)")
    return uniq

def keep_chain(p: Dict[str, Any]) -> bool:
    chain_id = p.get("chainId") or p.get("chain") or ""
    # Viele Einträge haben chainId='solana'
    if chain_id.lower() == "solana":
        return True
    # Fallback: URL enthält '/solana/'
    url = (p.get("url") or "").lower()
    return "/solana/" in url

def keep_quote(p: Dict[str, Any]) -> bool:
    if not STRICT_QUOTE:
        return True
    qsym = (p.get("quoteToken", {}).get("symbol") or p.get("quoteSymbol") or "").upper()
    return qsym == STRAT_QUOTE

def metric_num(obj, *keys) -> float:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return 0.0
        cur = cur[k]
    try:
        return float(cur)
    except Exception:
        return 0.0

def keep_metrics(p: Dict[str, Any]) -> bool:
    liq = metric_num(p, "liquidity", "usd")
    fdv = metric_num(p, "fdv")
    vol5 = metric_num(p, "volume", "m5") or metric_num(p, "volume", "h5")  # manche liefern h5 statt m5
    best_vol = max(
        metric_num(p, "volume", "m5"),
        metric_num(p, "volume", "h1"),
        metric_num(p, "volume", "h6"),
        metric_num(p, "volume", "h24"),
        0.0,
    )
    age_min = mins_ago(p.get("pairCreatedAt"))

    if liq < LIQ_MIN: return False
    if fdv and fdv > FDV_MAX: return False
    if VOL5M_MIN and vol5 < VOL5M_MIN: return False
    if VOL_BEST_MIN and best_vol < VOL_BEST_MIN: return False
    if MAX_AGE_MIN and (age_min is not None) and age_min > MAX_AGE_MIN: return False
    return True

def format_hit(p: Dict[str, Any]) -> str:
    base = p.get("baseToken", {}).get("symbol") or p.get("baseSymbol") or "?"
    quote = p.get("quoteToken", {}).get("symbol") or p.get("quoteSymbol") or "?"
    url   = p.get("url") or ""
    liq   = metric_num(p, "liquidity", "usd")
    fdv   = metric_num(p, "fdv")
    v5    = metric_num(p, "volume", "m5") or metric_num(p, "volume", "h5")
    age   = mins_ago(p.get("pairCreatedAt"))
    return f"{base}/{quote} | liq ${liq:,.0f} | fdv ${fdv:,.0f} | vol* {int(v5):,} | age {age}m | {url}"

def scan() -> List[Dict[str, Any]]:
    raw = ds_search_batches()
    raw = [p for p in raw if keep_chain(p)]
    print(f"[{now()}] [SCAN] after relaxed-chain filter: {len(raw)} pairs (contains 'sol')")
    if STRICT_QUOTE:
        raw = [p for p in raw if keep_quote(p)]
        print(f"[{now()}] [SCAN] quote filter enabled (STRICT_QUOTE=1) -> using {len(raw)} pairs")
    else:
        print(f"[{now()}] [SCAN] quote filter disabled (STRICT_QUOTE=0) -> using {len(raw)} pairs")

    hits = [p for p in raw if keep_metrics(p)]
    hits.sort(key=lambda x: (metric_num(x, "liquidity", "usd"), metric_num(x, "volume", "h24")), reverse=True)
    return hits[:MAX_ITEMS]

# ----------------------- Telegram + Trading Layer -----------------------
class App:
    def __init__(self):
        self.trader = JupiterTrader(rpc_url=RPC_URL, wallet_secret=WALLET_SECRET, slippage_bps=SLIPPAGE_BPS)
        self.bot = None  # type: Optional[TelegramBot]
        if TELEGRAM_TOKEN:
            self.bot = TelegramBot(
                token=TELEGRAM_TOKEN,
                fixed_chat_id=TELEGRAM_CHAT or None,
                on_command=self._on_command,
                on_button=self._on_button
            )
            # Startet Long-Polling in Thread
            self.bot.start()
            # Status bei Start
            self.bot.safe_broadcast(f"🚀 NeoAutoSniper boot OK.\n<b>NeoAutoSniper Status</b>\n{summarize_settings()}", parse_mode="HTML")
        else:
            print(f"[{now()}] [TG] Kein TELEGRAM_BOT_TOKEN – Telegram deaktiviert.")

    # ---------- Telegram Callbacks ----------
    def _on_button(self, chat_id: int, text: str):
        t = text.strip().lower()
        if t == "refresh":
            self._send_scan(chat_id)
        elif t == "settings":
            self.bot.safe_send(chat_id, f"Aktuelle Settings:\n{summarize_settings()}")
        elif t == "wallet":
            msg = self.trader.describe_wallet()
            self.bot.safe_send(chat_id, msg, disable_web_page_preview=True)
        elif t == "buy":
            self.bot.safe_send(chat_id, "Format: /buy <MINT> <SOL>\nBeispiel: <code>/buy 9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E 0.05</code>", parse_mode="HTML")
        elif t == "fund":
            self.bot.safe_send(chat_id, f"Einzahlungs-Adresse (SOL): <code>{self.trader.public_key or '—'}</code>", parse_mode="HTML")
        else:
            self.bot.safe_send(chat_id, "Platzhalter.")

    def _on_command(self, chat_id: int, cmd: str, args: List[str]):
        c = cmd.lower()
        if c == "/start":
            self.bot.send_keyboard(chat_id)
            self.bot.safe_send(chat_id, f"🤖 NeoAutoSniper ist bereit.\nNutze /help für alle Befehle oder die Tasten unten.")
            return
        if c == "/help":
            self.bot.safe_send(chat_id,
                "Befehle:\n"
                "• /set liq <USD> | /set fdv <USD> | /set vol5m <USD>\n"
                "• /set quote <SYM> (z.B. SOL) | /strict 0|1\n"
                "• /dryrun on|off | /interval <sec>\n"
                "• /buy <MINT> <SOL>  – kauft via Jupiter\n"
                "• /sell <MINT> <MENGE|%> – verkauft Token -> SOL\n"
                "• Refresh – sofort scannen\n"
            )
            return
        if c == "/set" and len(args) >= 2:
            key, val = args[0].lower(), args[1]
            global LIQ_MIN, FDV_MAX, VOL5M_MIN, STRAT_QUOTE
            try:
                if key == "liq":
                    LIQ_MIN = int(val)
                elif key == "fdv":
                    FDV_MAX = int(val)
                elif key == "vol5m":
                    VOL5M_MIN = int(val)
                elif key == "quote":
                    STRAT_QUOTE = val.upper()
                self.bot.safe_send(chat_id, "OK. " + summarize_settings())
            except Exception as e:
                self.bot.safe_send(chat_id, f"Fehler: {e}")
            return
        if c == "/strict" and len(args) == 1:
            global STRICT_QUOTE
            STRICT_QUOTE = args[0] in ("1","true","on","yes")
            self.bot.safe_send(chat_id, "OK. " + summarize_settings())
            return
        if c == "/dryrun" and len(args) == 1:
            global DRY_RUN
            DRY_RUN = args[0] in ("1","true","on","yes")
            self.bot.safe_send(chat_id, "OK. " + summarize_settings())
            return
        if c == "/interval" and len(args) == 1:
            global INTERVAL
            try:
                INTERVAL = max(10, int(args[0]))
                self.bot.safe_send(chat_id, "OK. " + summarize_settings())
            except:
                self.bot.safe_send(chat_id, "Ungültiger Wert.")
            return
        if c == "/buy" and len(args) >= 1:
            mint = args[0]
            sol_amt = float(args[1]) if len(args) >= 2 else DEFAULT_BUY_SOL
            if DRY_RUN:
                self.bot.safe_send(chat_id, f"🧪 DRY_RUN – würde kaufen: {sol_amt} SOL -> {mint}")
                return
            res = self.trader.buy_with_sol(mint, sol_amt)
            self.bot.safe_send(chat_id, res, disable_web_page_preview=True)
            return
        if c == "/sell" and len(args) >= 2:
            mint = args[0]; qty = args[1]
            if DRY_RUN:
                self.bot.safe_send(chat_id, f"🧪 DRY_RUN – würde verkaufen: {qty} {mint} -> SOL")
                return
            res = self.trader.sell_to_sol(mint, qty)
            self.bot.safe_send(chat_id, res, disable_web_page_preview=True)
            return

        self.bot.safe_send(chat_id, "Unbekannter Befehl. /help")

    # ---------- Scan + Alerts ----------
    def _send_scan(self, chat_id: Optional[int] = None):
        hits = scan()
        if not hits:
            msg = "✅ [HITS] keine Treffer im aktuellen Scan."
            if self.bot:
                if chat_id: self.bot.safe_send(chat_id, msg)
                else: self.bot.safe_broadcast(msg)
            else:
                print(msg)
            return

        # Nachricht bauen
        top_lines = []
        for p in hits[:5]:
            top_lines.append("• " + format_hit(p))
        text = "🎯 Treffer (Top 5):\n" + "\n".join(top_lines)
        if self.bot:
            if chat_id: self.bot.safe_send(chat_id, text, disable_web_page_preview=False)
            else: self.bot.safe_broadcast(text, disable_web_page_preview=False)
        else:
            print(text)

        # Auto-Buy (optional)
        if (not DRY_RUN) and AUTO_BUY and len(hits) > 0:
            best = hits[0]
            base_mint = (best.get("baseToken") or {}).get("address")
            if base_mint:
                res = self.trader.buy_with_sol(base_mint, DEFAULT_BUY_SOL)
                if self.bot: self.bot.safe_broadcast(res)

    def run(self):
        print(f"[{now()}] Starting NeoAutoSniper…")
        if self.bot:
            self.bot.safe_broadcast(f"<b>NeoAutoSniper Status</b>\n{summarize_settings()}", parse_mode="HTML")

        while True:
            try:
                self._send_scan()
            except Exception as e:
                print(f"[{now()}] Scan-Fehler: {e}")
            time.sleep(INTERVAL)

if __name__ == "__main__":
    App().run()
