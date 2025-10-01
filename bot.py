# bot.py
# DexScreener-Scanner + Telegram-Kommandos (ohne Extra-Libs)
import os
import time
import json
import threading
from datetime import datetime, timezone
import requests

from telegram_handlers import TelegramBot

# ------------------ Konfiguration (ENV + Laufzeit-Overrides) ------------------

def _env_int(name, default):
    try:
        return int(str(os.getenv(name, default)).strip())
    except Exception:
        return int(default)

CFG = {
    "SCAN_INTERVAL": _env_int("SCAN_INTERVAL", 30),
    "STRICT_QUOTE": _env_int("STRICT_QUOTE", 0),         # 1 = nur STRAT_QUOTE
    "STRAT_QUOTE": os.getenv("STRAT_QUOTE", "SOL").upper(),
    "STRAT_CHAIN": os.getenv("STRAT_CHAIN", "solana").lower(),

    "LIQ_MIN": _env_int("STRAT_LIQ_MIN", 130000),
    "FDV_MAX": _env_int("STRAT_FDV_MAX", 400000),
    "VOL5M_MIN": _env_int("STRAT_VOL5M_MIN", 20000),

    "DRY_RUN": 1 if os.getenv("DRY_RUN", "1") == "1" else 0,
}

RUNTIME = {}  # überschreibt CFG-Werte während der Laufzeit (via Telegram /set)

def G(key):
    return RUNTIME.get(key, CFG[key])

def set_param_runtime(key, value):
    key_map = {
        "liq": "LIQ_MIN",
        "fdv": "FDV_MAX",
        "vol5m": "VOL5M_MIN",
    }
    k = key_map.get(key.lower())
    if not k:
        return "❌ Unbekannter Parameter. Erlaubt: liq, fdv, vol5m."
    try:
        v = int(float(str(value).replace("_", "")))
        RUNTIME[k] = v
        return f"✅ {k} = {v} (für diese Laufzeit gesetzt)"
    except Exception:
        return "❌ Zahl ungültig."

def set_dry_run(flag: bool):
    RUNTIME["DRY_RUN"] = 1 if flag else 0
    return f"✅ DRY_RUN = {'ON' if flag else 'OFF'} (für diese Laufzeit gesetzt)"

def status_text():
    return (
        "<b>NeoAutoSniper Status</b>\n"
        f"• Chain/Quote: {G('STRAT_CHAIN')}/{G('STRAT_QUOTE')}\n"
        f"• STRICT_QUOTE: {G('STRICT_QUOTE')}\n"
        f"• LIQ_MIN: {G('LIQ_MIN'):,}\n"
        f"• FDV_MAX: {G('FDV_MAX'):,}\n"
        f"• VOL5M_MIN: {G('VOL5M_MIN'):,}\n"
        f"• DRY_RUN: {'ON' if G('DRY_RUN') else 'OFF'}\n"
        f"• Interval: {G('SCAN_INTERVAL')}s\n"
    )

# ------------------ Telegram Setup ------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None
TELEGRAM_ALLOWED_USER = os.getenv("TELEGRAM_ALLOWED_USER", "").strip() or None

tg = None
tg_thread = None
_refresh_now = threading.Event()

def _start_telegram():
    global tg, tg_thread
    if not TELEGRAM_BOT_TOKEN:
        print("[TG] Kein TELEGRAM_BOT_TOKEN – Telegram deaktiviert.")
        return
    try:
        tg = TelegramBot(
            token=TELEGRAM_BOT_TOKEN,
            chat_id=int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None,
            allowed_user=int(TELEGRAM_ALLOWED_USER) if TELEGRAM_ALLOWED_USER else None,
        )
    except Exception as e:
        print("[TG] Startfehler:", e)
        tg = None
        return

    def poller():
        tg.poll_loop({
            "get_status": status_text,
            "set_param": set_param_runtime,
            "set_dry_run": set_dry_run,
            "refresh": lambda: (_refresh_now.set() or "🔄 Angestoßen"),
        })

    tg_thread = threading.Thread(target=poller, daemon=True)
    tg_thread.start()
    tg.send_message("🚀 NeoAutoSniper boot OK.\n" + status_text(), show_menu=True)

# ------------------ DexScreener Scan ------------------

def _get(url, params=None, timeout=15):
    return requests.get(url, params=params or {}, timeout=timeout)

def scan_sources():
    """
    Liefert rohe Pair-Liste aus mehreren Quellen (mit Fallbacks),
    dann Filterung nach Chain/Quote + Limits.
    """
    chain = G("STRAT_CHAIN")
    quote = G("STRAT_QUOTE")

    raw = []
    # 1) offizielle Pairs-List (kann 404 geben, ist ok)
    try:
        r = _get(f"https://api.dexscreener.com/latest/dex/pairs/{chain}")
        if r.status_code == 200:
            raw += r.json().get("pairs", [])
        else:
            print(f"[SCAN] pairs/{chain} -> HTTP {r.status_code}")
    except Exception:
        pass

    # 2) Such-Fallbacks (verschiedene Queries)
    queries = [f"{quote}", f"{chain}", f"{quote}/{chain}"]
    for q in queries:
        try:
            r = _get("https://api.dexscreener.com/latest/dex/search", params={"q": q})
            if r.status_code == 200:
                raw += r.json().get("pairs", [])
            else:
                print(f"[SCAN] search?q={q} -> HTTP {r.status_code}")
        except Exception:
            pass

    # Deduplizieren anhand pairAddress
    uniq = {}
    for p in raw:
        pa = p.get("pairAddress") or p.get("url")
        if not pa:
            continue
        uniq[pa] = p
    pairs = list(uniq.values())

    # Grober Chain-Filter
    pairs = [p for p in pairs if (p.get("chainId") or "").lower() == chain]

    # Quote-Filter (strict oder relaxed)
    if G("STRICT_QUOTE"):
        pairs = [p for p in pairs if (p.get("quoteToken", {}).get("symbol") or "").upper() == quote]

    return pairs

def _num(x, *path, default=0.0):
    for k in path:
        if isinstance(x, dict):
            x = x.get(k)
        else:
            return default
    try:
        return float(x)
    except Exception:
        return default

def apply_limits(pairs):
    LIQ_MIN = G("LIQ_MIN")
    FDV_MAX = G("FDV_MAX")
    VOL5M_MIN = G("VOL5M_MIN")
    out = []
    for p in pairs:
        liq = _num(p, "liquidity", "usd")
        fdv = _num(p, "fdv")
        vol5m = _num(p, "volume", "m5")
        if (liq >= LIQ_MIN) and (fdv <= FDV_MAX) and (vol5m >= VOL5M_MIN):
            out.append((p, liq, fdv, vol5m))
    # sortiere nach bestVol (falls vorhanden) / sonst vol5m
    out.sort(key=lambda t: _num(t[0], "bestTrade", "volume", default=t[3]), reverse=True)
    return out

def fmt_pair(p, liq, fdv, vol5m):
    base = p.get("baseToken", {}).get("symbol") or "?"
    quote = p.get("quoteToken", {}).get("symbol") or "?"
    url = p.get("url") or ""
    age_ms = (int(p.get("pairCreatedAt") or 0))
    if age_ms > 10**12:  # ms -> s
        age_s = int((age_ms / 1000.0))
    else:
        age_s = int(age_ms)
    age_m = int(max(0, time.time() - age_s) // 60) if age_s else 0
    return f"• <b>{base}/{quote}</b> | liq ${liq:,.0f} | fdv ${fdv:,.0f} | vol* {int(vol5m):,} | age {age_m}m | {url}"

# ------------------ Main Loop ------------------

def main_loop():
    _start_telegram()
    last_scan = 0
    while True:
        # sofortiger Scan auf Wunsch
        if _refresh_now.is_set():
            _refresh_now.clear()
            last_scan = 0

        now = time.time()
        if now - last_scan < G("SCAN_INTERVAL"):
            time.sleep(1)
            continue
        last_scan = now

        try:
            pairs = scan_sources()
            hits = apply_limits(pairs)
            top = hits[:5]
            if tg:
                if not top:
                    tg.send_message("✅ [HITS] keine Treffer im aktuellen Scan.")
                else:
                    lines = ["🎯 <b>Treffer</b> (Top 5):"]
                    for p, liq, fdv, vol5m in top:
                        lines.append(fmt_pair(p, liq, fdv, vol5m))
                    if G("DRY_RUN"):
                        lines.append("\n[MODE] DRY_RUN aktiv – keine Käufe.")
                    tg.send_message("\n".join(lines), disable_web_page_preview=False)
            else:
                print("[HITS]", len(top))
        except Exception as e:
            print("[ERR]", e)
            time.sleep(2)

if __name__ == "__main__":
    print("Starting NeoAutoSniper…")
    print(status_text())
    main_loop()
