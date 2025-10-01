# bot.py — NeoAutoSniper (Scanner + Telegram + Wallet-Erkennung)
import os, time, json, threading
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import requests

from telegram_handlers import TelegramBot
from trading import JupiterTrader

# ---------------- Helpers ----------------
def _as_int(v, default=0):
    try:
        return int(float(str(v).strip().replace("_","").replace(",","")))
    except Exception:
        return int(default)

def _num(d, *path, default=0.0):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return float(default)
        cur = cur[k]
    try:
        return float(cur)
    except Exception:
        return float(default)

def _best_vol(p: Dict[str, Any]) -> int:
    vol = p.get("volume") or {}
    candidates = [
        _as_int(vol.get("m5", 0)), _as_int(vol.get("m15", 0)),
        _as_int(vol.get("h1", 0)), _as_int(vol.get("h6", 0)),
        _as_int(vol.get("h24", 0))
    ]
    return max(candidates) if candidates else 0

def _age_minutes(p: Dict[str, Any]) -> int:
    ts = p.get("pairCreatedAt")
    try:
        if ts is None:
            return 10**9
        dt = datetime.fromtimestamp(int(ts)/1000.0, tz=timezone.utc)
        return int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    except Exception:
        return 10**9

def _fmt_pair(p, liq, fdv, vol5, bestv) -> str:
    base  = (p.get("baseToken") or {}).get("symbol") or "?"
    quote = (p.get("quoteToken") or {}).get("symbol") or "?"
    url   = p.get("url") or ""
    ageM  = _age_minutes(p)
    return f"• <b>{base}/{quote}</b> | liq ${liq:,.0f} | fdv ${fdv:,.0f} | vol5 {int(vol5):,} | best {bestv:,} | age {ageM}m | {url}"

# ---------------- Config ----------------
CONFIG: Dict[str, Any] = {
    "STRAT_CHAIN":       os.getenv("STRAT_CHAIN", "solana").lower(),
    "STRAT_QUOTE":       os.getenv("STRAT_QUOTE", "SOL").upper(),
    "STRICT_QUOTE":      _as_int(os.getenv("STRICT_QUOTE", "1"), 1),
    "STRAT_LIQ_MIN":     _as_int(os.getenv("STRAT_LIQ_MIN", "130000"), 130000),
    "STRAT_FDV_MAX":     _as_int(os.getenv("STRAT_FDV_MAX", "400000"), 400000),
    "STRAT_VOL5M_MIN":   _as_int(os.getenv("STRAT_VOL5M_MIN", "20000"), 20000),
    "STRAT_VOL_BEST_MIN":_as_int(os.getenv("STRAT_VOL_BEST_MIN", "0"), 0),
    "MAX_AGE_MIN":       _as_int(os.getenv("MAX_AGE_MIN", "120"), 120),
    "STRAT_MAX_ITEMS":   _as_int(os.getenv("STRAT_MAX_ITEMS", "200"), 200),
    "HTTP_TIMEOUT":      _as_int(os.getenv("HTTP_TIMEOUT", "15"), 15),
    "SCAN_INTERVAL":     _as_int(os.getenv("SCAN_INTERVAL", "60"), 60),
    "DRY_RUN":           _as_int(os.getenv("DRY_RUN", "1"), 1),
    "AUTO_BUY":          _as_int(os.getenv("AUTO_BUY", "0"), 0),
}

def settings_text() -> str:
    c = CONFIG
    return (
        "<b>NeoAutoSniper Status</b>\n"
        f"• Chain/Quote: {c['STRAT_CHAIN']}/{c['STRAT_QUOTE']} (STRICT={c['STRICT_QUOTE']})\n"
        f"• LIQ_MIN: {c['STRAT_LIQ_MIN']:,} | FDV_MAX: {c['STRAT_FDV_MAX']:,}\n"
        f"• VOL5M_MIN: {c['STRAT_VOL5M_MIN']:,} | VOL_BEST_MIN: {c['STRAT_VOL_BEST_MIN']:,}\n"
        f"• MAX_AGE_MIN: {c['MAX_AGE_MIN']} | MAX_ITEMS: {c['STRAT_MAX_ITEMS']}\n"
        f"• DRY_RUN: {c['DRY_RUN']} | AUTO_BUY: {c['AUTO_BUY']}\n"
        f"• INTERVAL: {c['SCAN_INTERVAL']}s | TIMEOUT: {c['HTTP_TIMEOUT']}s\n"
    )

# ---------------- Telegram ----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None

tg: Optional[TelegramBot] = None
_force_scan = threading.Event()

def start_telegram():
    global tg
    if not TELEGRAM_BOT_TOKEN:
        print("[TG] Kein TELEGRAM_BOT_TOKEN – Telegram deaktiviert.")
        return
    tg = TelegramBot(
        token=TELEGRAM_BOT_TOKEN,
        fixed_chat_id=TELEGRAM_CHAT_ID,
        on_command=_on_command,
        on_button=_on_button,
    )
    tg.start()
    tg.safe_broadcast("🚀 NeoAutoSniper boot OK.\n" + settings_text(), parse_mode="HTML")

# ---------------- Trading / Wallet ----------------
trader = JupiterTrader()  # Wallet-Secret wird INNEN aus ENV erkannt

def _on_button(chat_id: int, text: str):
    t = text.strip().lower()
    if t == "refresh":
        _force_scan.set()
        tg.safe_send(chat_id, "🔄 Sofort-Scan ausgelöst.")
    elif t == "settings":
        tg.safe_send(chat_id, settings_text(), parse_mode="HTML")
    elif t == "wallet":
        tg.safe_send(chat_id, trader.describe_wallet(), parse_mode="HTML", disable_web_page_preview=True)
    elif t == "fund":
        addr = trader.public_key or "—"
        tg.safe_send(chat_id, f"Einzahlungs-Adresse (SOL): <code>{addr}</code>", parse_mode="HTML")
    else:
        tg.safe_send(chat_id, "Platzhalter.")

def _on_command(chat_id: int, cmd: str, args: List[str]):
    c = cmd.lower()
    if c == "/start":
        tg.send_keyboard(chat_id)
        tg.safe_send(chat_id, "🤖 NeoAutoSniper ist bereit.\nNutze /help oder die Tasten unten.")
        return
    if c == "/help":
        tg.safe_send(chat_id,
            "Befehle:\n"
            "• /set liq <USD> | /set fdv <USD> | /set vol5m <USD>\n"
            "• /quote <SYM>|off | /strict 0|1\n"
            "• /dryrun on|off | /interval <sec>\n"
            "• /buy <MINT> <SOL> (Platzhalter, nur mit Wallet & DRY_RUN=0)\n"
            "• Refresh – sofort scannen\n"
        )
        return
    if c == "/set" and len(args) >= 2:
        k, v = args[0].lower(), args[1]
        try:
            if k == "liq": CONFIG["STRAT_LIQ_MIN"] = _as_int(v, CONFIG["STRAT_LIQ_MIN"])
            elif k == "fdv": CONFIG["STRAT_FDV_MAX"] = _as_int(v, CONFIG["STRAT_FDV_MAX"])
            elif k == "vol5m": CONFIG["STRAT_VOL5M_MIN"] = _as_int(v, CONFIG["STRAT_VOL5M_MIN"])
            elif k == "volbest": CONFIG["STRAT_VOL_BEST_MIN"] = _as_int(v, CONFIG["STRAT_VOL_BEST_MIN"])
            elif k == "age": CONFIG["MAX_AGE_MIN"] = _as_int(v, CONFIG["MAX_AGE_MIN"])
            tg.safe_send(chat_id, "OK.\n" + settings_text(), parse_mode="HTML")
        except Exception as e:
            tg.safe_send(chat_id, f"Fehler: {e}")
        return
    if c == "/quote" and len(args) == 1:
        q = args[0].upper()
        if q == "OFF":
            CONFIG["STRICT_QUOTE"] = 0
            tg.safe_send(chat_id, "STRICT_QUOTE=0 (Quote-Filter aus).")
        else:
            CONFIG["STRAT_QUOTE"] = q
            CONFIG["STRICT_QUOTE"] = 1
            tg.safe_send(chat_id, f"Quote={q}, STRICT_QUOTE=1.")
        return
    if c == "/strict" and len(args) == 1:
        CONFIG["STRICT_QUOTE"] = 1 if args[0] in ("1","true","on","yes") else 0
        tg.safe_send(chat_id, "OK.\n" + settings_text(), parse_mode="HTML")
        return
    if c == "/dryrun" and len(args) == 1:
        CONFIG["DRY_RUN"] = 1 if args[0] in ("1","on","true","yes") else 0
        tg.safe_send(chat_id, "OK.\n" + settings_text(), parse_mode="HTML")
        return
    if c == "/interval" and len(args) == 1:
        CONFIG["SCAN_INTERVAL"] = max(10, _as_int(args[0], CONFIG["SCAN_INTERVAL"]))
        tg.safe_send(chat_id, "OK.\n" + settings_text(), parse_mode="HTML")
        return
    tg.safe_send(chat_id, "Unbekannter Befehl. /help")

# ---------------- DexScreener Scan ----------------
def _http_get(url: str, params=None, timeout=15):
    try:
        return requests.get(url, params=params or {}, timeout=timeout)
    except Exception:
        return None

def fetch_pairs() -> List[Dict[str, Any]]:
    chain = CONFIG["STRAT_CHAIN"]
    timeout = CONFIG["HTTP_TIMEOUT"]
    urls = [
        f"https://api.dexscreener.com/latest/dex/search?q=solana",
        f"https://api.dexscreener.com/latest/dex/search?q=SOL",
        f"https://api.dexscreener.com/latest/dex/search?q=SOL/USDC",
    ]
    uniq = {}
    raw_count = 0
    for url in urls:
        r = _http_get(url, timeout=timeout)
        if not r or r.status_code != 200:
            print(f"[SCAN] {url} -> {r.status_code if r else 'ERR'}")
            continue
        arr = (r.json() or {}).get("pairs") or []
        raw_count += len(arr)
        for p in arr:
            pid = p.get("pairAddress") or p.get("url")
            if pid and pid not in uniq:
                uniq[pid] = p
        time.sleep(0.2)
        if len(uniq) >= CONFIG["STRAT_MAX_ITEMS"]:
            break
    pairs = list(uniq.values())
    print(f"[SCAN] collected {len(pairs)} unique pairs from {raw_count} raw results")
    return pairs

def filter_pairs(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chain = CONFIG["STRAT_CHAIN"]
    strict = CONFIG["STRICT_QUOTE"] == 1
    quote = CONFIG["STRAT_QUOTE"].upper()

    sol = [p for p in pairs if (p.get("chainId") or "").lower() == chain or "/solana/" in (p.get("url") or "").lower()]
    print(f"[SCAN] after chain filter: {len(sol)} pairs")
    if strict and quote != "ANY":
        sol = [p for p in sol if ((p.get("quoteToken") or {}).get("symbol") or "").upper() == quote]
        print(f"[SCAN] after quote filter: {len(sol)} pairs (quote={quote})")
    else:
        print(f"[SCAN] quote filter disabled (STRICT_QUOTE={CONFIG['STRICT_QUOTE']}) -> using {len(sol)} pairs")
    if CONFIG["MAX_AGE_MIN"] > 0:
        sol = [p for p in sol if _age_minutes(p) <= CONFIG["MAX_AGE_MIN"]]
        print(f"[SCAN] after age filter: {len(sol)} pairs (≤ {CONFIG['MAX_AGE_MIN']}m)")
    return sol

def apply_strategy(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in pairs:
        liq   = _num(p, "liquidity", "usd")
        fdv   = _num(p, "fdv")
        vol5  = _num(p, "volume", "m5") or _num(p, "volume", "h5")
        bestv = _best_vol(p)
        if liq < CONFIG["STRAT_LIQ_MIN"]: continue
        if fdv and fdv > CONFIG["STRAT_FDV_MAX"]: continue
        if vol5 < CONFIG["STRAT_VOL5M_MIN"]: continue
        if CONFIG["STRAT_VOL_BEST_MIN"] and bestv < CONFIG["STRAT_VOL_BEST_MIN"]: continue
        out.append((p, liq, fdv, vol5, bestv))
    out.sort(key=lambda t: (-t[1], t[2], -t[4]))  # viel Liq, kleiner FDV, hohes bestVol
    return out

# ---------------- Main Loop ----------------
def main():
    print("Starting NeoAutoSniper…")
    print(settings_text())
    start_telegram()

    last_scan = 0.0
    while True:
        try:
            if _force_scan.is_set():
                _force_scan.clear()
                last_scan = 0.0
            now = time.time()
            if now - last_scan < max(5, CONFIG["SCAN_INTERVAL"]):
                time.sleep(1); continue
            last_scan = now

            raw = fetch_pairs()
            pool = filter_pairs(raw)
            hits = apply_strategy(pool)
            top  = hits[:5]

            if tg:
                if top:
                    rows = [_fmt_pair(p, liq, fdv, vol5, bestv) for (p, liq, fdv, vol5, bestv) in top]
                    if CONFIG["DRY_RUN"] == 1: rows.append("\n[MODE] DRY_RUN aktiv – keine Käufe.")
                    tg.safe_broadcast("🎯 Treffer (Top 5):\n" + "\n".join(rows), parse_mode="HTML", disable_web_page_preview=False)
                else:
                    tg.safe_broadcast("✅ [HITS] keine Treffer im aktuellen Scan.")
            else:
                print("[HITS]", len(top))
        except Exception as e:
            print("[ERR]", e)
            time.sleep(2)

if __name__ == "__main__":
    main()
