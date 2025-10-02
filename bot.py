# bot.py — NeoAutoSniper mit Teilverkäufen (TP1/TP2 + BE/Trailing)
import os, time, json, threading
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import requests

from telegram_handlers import TelegramBot
from trading import JupiterTrader

# ---------------- Utils ----------------
def _as_int(v, default=0):
    try:
        return int(float(str(v).strip().replace("_","").replace(",","")))
    except Exception:
        return int(default)

def _as_float(v, default=0.0):
    try:
        return float(str(v).strip().replace("%","").replace(",","."))
    except Exception:
        return float(default)

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
    # Scanner
    "STRAT_CHAIN":       os.getenv("STRAT_CHAIN", "solana").lower(),
    "STRAT_QUOTE":       os.getenv("STRAT_QUOTE", "SOL").upper(),
    "STRICT_QUOTE":      _as_int(os.getenv("STRICT_QUOTE", "1"), 1),
    "STRAT_LIQ_MIN":     _as_int(os.getenv("STRAT_LIQ_MIN", "130000")),
    "STRAT_FDV_MAX":     _as_int(os.getenv("STRAT_FDV_MAX", "400000")),
    "STRAT_VOL5M_MIN":   _as_int(os.getenv("STRAT_VOL5M_MIN", "5000")),
    "STRAT_VOL_BEST_MIN":_as_int(os.getenv("STRAT_VOL_BEST_MIN", "10000")),
    "MAX_AGE_MIN":       _as_int(os.getenv("MAX_AGE_MIN", "120")),
    "STRAT_MAX_ITEMS":   _as_int(os.getenv("STRAT_MAX_ITEMS", "200")),
    "HTTP_TIMEOUT":      _as_int(os.getenv("HTTP_TIMEOUT", "15")),
    "SCAN_INTERVAL":     _as_int(os.getenv("SCAN_INTERVAL", "45")),
    # Trading toggles
    "DRY_RUN":           _as_int(os.getenv("DRY_RUN", "1"), 1),
    "AUTO_BUY":          _as_int(os.getenv("AUTO_BUY", "0"), 0),
    "SLIPPAGE_BPS":      _as_int(os.getenv("SLIPPAGE_BPS", "50"), 50),
    # Reinvest / Sizing
    "INVEST_MODE":       os.getenv("INVEST_MODE", "pct").lower(), # 'pct' oder 'fixed'
    "INVEST_PCT":        _as_float(os.getenv("INVEST_PCT", "50")),
    "RESERVE_SOL":       _as_float(os.getenv("RESERVE_SOL", "0.05")),
    "MIN_BUY_SOL":       _as_float(os.getenv("MIN_BUY_SOL", "0.05")),
    "MAX_BUY_SOL":       _as_float(os.getenv("MAX_BUY_SOL", "0.25")),
    "TP_PCT":            _as_float(os.getenv("TP_PCT", "20")),   # einfacher Full TP (Fallback)
    # Teilverkauf / Risk
    "PARTIAL_ENABLED":       _as_int(os.getenv("PARTIAL_ENABLED", "0"), 0),
    "TP1_PCT":               _as_float(os.getenv("TP1_PCT", "12")),
    "TP1_SELL_PCT":          _as_float(os.getenv("TP1_SELL_PCT", "50")),
    "TP2_PCT":               _as_float(os.getenv("TP2_PCT", "25")),
    "TP2_SELL_PCT":          _as_float(os.getenv("TP2_SELL_PCT", "100")),
    "BREAKEVEN_AFTER_TP1":   _as_int(os.getenv("BREAKEVEN_AFTER_TP1", "1"), 1),
    "TRAIL_AFTER_TP1_PCT":   _as_float(os.getenv("TRAIL_AFTER_TP1_PCT", "8")),
    "STOP_LOSS_PCT":         _as_float(os.getenv("STOP_LOSS_PCT", "10")),
}

def settings_text() -> str:
    c = CONFIG
    lines = [
        "<b>NeoAutoSniper Status</b>",
        f"• Chain/Quote: {c['STRAT_CHAIN']}/{c['STRAT_QUOTE']} (STRICT={c['STRICT_QUOTE']})",
        f"• LIQ_MIN: {c['STRAT_LIQ_MIN']:,} | FDV_MAX: {c['STRAT_FDV_MAX']:,}",
        f"• VOL5M_MIN: {c['STRAT_VOL5M_MIN']:,} | VOL_BEST_MIN: {c['STRAT_VOL_BEST_MIN']:,}",
        f"• MAX_AGE_MIN: {c['MAX_AGE_MIN']} | MAX_ITEMS: {c['STRAT_MAX_ITEMS']}",
        f"• DRY_RUN: {c['DRY_RUN']} | AUTO_BUY: {c['AUTO_BUY']} | SLIPPAGE: {c['SLIPPAGE_BPS']} bps",
        f"• INVEST: {c['INVEST_MODE']} | PCT: {c['INVEST_PCT']}% | RES: {c['RESERVE_SOL']} | MIN/MAX: {c['MIN_BUY_SOL']}/{c['MAX_BUY_SOL']}",
        f"• TP: {c['TP_PCT']}% | PARTIAL: {c['PARTIAL_ENABLED']} (TP1 {c['TP1_PCT']}%/{c['TP1_SELL_PCT']}% | TP2 {c['TP2_PCT']}%/{c['TP2_SELL_PCT']}%)",
        f"• BE_after_TP1: {c['BREAKEVEN_AFTER_TP1']} | TRAIL_after_TP1: {c['TRAIL_AFTER_TP1_PCT']}% | SL: {c['STOP_LOSS_PCT']}%",
        f"• INTERVAL: {c['SCAN_INTERVAL']}s | TIMEOUT: {c['HTTP_TIMEOUT']}s",
    ]
    return "\n".join(lines)

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
trader = JupiterTrader(slippage_bps=CONFIG["SLIPPAGE_BPS"])

# ---------------- Portfolio (Positionsverwaltung) ----------------
PORT_PATH = "positions.json"
_lock = threading.Lock()

def _load_positions() -> List[Dict[str, Any]]:
    try:
        with open(PORT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_positions(items: List[Dict[str, Any]]):
    try:
        with open(PORT_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _has_position(mint: str) -> bool:
    with _lock:
        return any(p.get("mint")==mint for p in _load_positions())

def _add_position(pos: Dict[str, Any]):
    with _lock:
        items = _load_positions()
        items.append(pos)
        _save_positions(items)

def _update_position(mint: str, updates: Dict[str, Any]):
    with _lock:
        items = _load_positions()
        for p in items:
            if p.get("mint")==mint:
                p.update(updates)
        _save_positions(items)

def _remove_position(mint: str):
    with _lock:
        items = [p for p in _load_positions() if p.get("mint")!=mint]
        _save_positions(items)

def _list_positions_text() -> str:
    items = _load_positions()
    if not items:
        return "Keine offenen Positionen."
    lines = ["<b>Offene Positionen</b>"]
    for p in items:
        lines.append(
            f"• {p.get('symbol','?')} ({p['mint'][:6]}…): "
            f"entry={p.get('entry_price_sol',0):.10f} SOL | tp1={p.get('tp1_hit',False)} | qty≈{p.get('qty_est',0):.6f}"
        )
    return "\n".join(lines)

# ---------------- DexScreener Helpers ----------------
def ds_pairs_for_mint(mint: str) -> List[Dict[str, Any]]:
    try:
        url = f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}"
        r = requests.get(url, timeout=CONFIG["HTTP_TIMEOUT"])
        if r.status_code != 200: return []
        data = r.json() or {}
        return data if isinstance(data, list) else data.get("pairs") or []
    except Exception:
        return []

def ds_price_native_sol(mint: str) -> Optional[float]:
    pairs = ds_pairs_for_mint(mint)
    # Bevorzugt SOL-Quote
    for p in pairs:
        q = ((p.get("quoteToken") or {}).get("symbol") or "").upper()
        if q == "SOL":
            pr = p.get("priceNative")
            try:
                return float(pr)
            except Exception:
                pass
    # Fallback: erster Pair
    if pairs:
        pr = pairs[0].get("priceNative")
        try:
            return float(pr)
        except Exception:
            return None
    return None

# ---------------- Invest-Sizing ----------------
def compute_invest_amount_sol() -> float:
    bal = trader.get_sol_balance()
    avail = max(0.0, bal - CONFIG["RESERVE_SOL"])
    if CONFIG["INVEST_MODE"] == "pct":
        amt = avail * (CONFIG["INVEST_PCT"]/100.0)
    else:  # fixed
        amt = _as_float(os.getenv("INVEST_AMOUNT_SOL","0.05"), 0.05)
    amt = max(CONFIG["MIN_BUY_SOL"], min(CONFIG["MAX_BUY_SOL"], amt))
    return round(amt, 6)

# ---------------- Telegram Handlers ----------------
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
    elif t == "alerts":
        tg.safe_send(chat_id, _list_positions_text(), parse_mode="HTML", disable_web_page_preview=True)
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
            "• /set liq|fdv|vol5m|volbest|age <Wert>\n"
            "• /quote <SYM>|off | /strict 0|1\n"
            "• /dryrun on|off | /interval <sec>\n"
            "• /buy <MINT>  – kauft mit berechnetem Betrag\n"
            "• /positions   – offene Positionen anzeigen\n"
            "• Refresh – sofort scannen\n"
        )
        return
    if c == "/positions":
        tg.safe_send(chat_id, _list_positions_text(), parse_mode="HTML")
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
    if c == "/buy" and len(args) >= 1:
        mint = args[0]
        _handle_buy(mint, chat_id)
        return
    tg.safe_send(chat_id, "Unbekannter Befehl. /help")

# ---------------- BUY / Positionsanlage ----------------
def _handle_buy(mint: str, chat_id: Optional[int] = None, symbol_hint: Optional[str] = None):
    if _has_position(mint):
        if tg and chat_id: tg.safe_send(chat_id, "Position existiert bereits – kein Doppelkauf.")
        return

    invest_sol = compute_invest_amount_sol()
    price = ds_price_native_sol(mint) or 0.0
    symbol = symbol_hint or mint[:6]
    qty_est = invest_sol / price if price > 0 else 0.0

    if CONFIG["DRY_RUN"] == 1:
        msg = f"🧪 DRY_RUN – BUY {invest_sol} SOL -> {symbol} ({mint[:6]}...), entry≈{price:.10f} SOL, qty≈{qty_est:.6f}"
        if tg and chat_id: tg.safe_send(chat_id, msg, disable_web_page_preview=True)
    else:
        res = trader.buy_with_sol(mint, invest_sol)
        if tg and chat_id: tg.safe_send(chat_id, res, disable_web_page_preview=True)

    pos = {
        "mint": mint,
        "symbol": symbol,
        "entry_price_sol": price,
        "qty_est": qty_est,
        "tp1_hit": False,
        "tp2_hit": False,
        "high_after_tp1": 0.0,
        "stop_price": 0.0,
        "opened_at": int(time.time()),
    }
    _add_position(pos)
    if tg and chat_id:
        tg.safe_send(chat_id, f"📌 Position angelegt: {symbol} ({mint[:6]}…), entry≈{price:.10f} SOL", parse_mode="HTML")

# ---------------- SCAN / Strategy ----------------
def _http_get(url: str, params=None, timeout=15):
    try:
        return requests.get(url, params=params or {}, timeout=timeout)
    except Exception:
        return None

def fetch_pairs() -> List[Dict[str, Any]]:
    timeout = CONFIG["HTTP_TIMEOUT"]
    urls = [
        "https://api.dexscreener.com/latest/dex/search?q=solana",
        "https://api.dexscreener.com/latest/dex/search?q=SOL",
        "https://api.dexscreener.com/latest/dex/search?q=SOL/USDC",
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

# ---------------- Partial-Take-Profit Engine ----------------
def _check_positions_loop():
    while True:
        try:
            items = _load_positions()
            for p in items:
                mint = p["mint"]
                entry = float(p.get("entry_price_sol", 0.0) or 0.0)
                if entry <= 0:
                    continue
                price = ds_price_native_sol(mint)
                if price is None or price <= 0:
                    continue
                change_pct = (price/entry - 1.0) * 100.0

                # STOP LOSS
                if CONFIG["STOP_LOSS_PCT"] > 0 and change_pct <= -CONFIG["STOP_LOSS_PCT"]:
                    _execute_sell(mint, 1.0, reason=f"SL {CONFIG['STOP_LOSS_PCT']}%")
                    _remove_position(mint)
                    if tg: tg.safe_broadcast(f"🛑 SL ausgelöst {p.get('symbol','?')} {change_pct:.2f}%")
                    continue

                if CONFIG["PARTIAL_ENABLED"] == 1:
                    # TP1
                    if not p.get("tp1_hit", False) and change_pct >= CONFIG["TP1_PCT"]:
                        frac = max(0.0, min(1.0, CONFIG["TP1_SELL_PCT"]/100.0))
                        _execute_sell(mint, frac, reason=f"TP1 {CONFIG['TP1_PCT']}%")
                        _update_position(mint, {"tp1_hit": True, "high_after_tp1": price})
                        if CONFIG["BREAKEVEN_AFTER_TP1"] == 1:
                            _update_position(mint, {"stop_price": entry})
                        if tg: tg.safe_broadcast(f"✅ TP1 {p.get('symbol','?')} +{change_pct:.2f}% → verkauft {CONFIG['TP1_SELL_PCT']}%")
                        continue

                    # Nach TP1: Trailing-Stop
                    if p.get("tp1_hit", False):
                        hi = float(p.get("high_after_tp1", 0.0) or 0.0)
                        if price > hi:
                            _update_position(mint, {"high_after_tp1": price})
                            hi = price
                        trail = CONFIG["TRAIL_AFTER_TP1_PCT"]
                        if trail > 0 and hi > 0:
                            drop_pct = (price/hi - 1.0)*100.0
                            if drop_pct <= -trail:
                                _execute_sell(mint, 1.0, reason=f"Trailing {trail}%")
                                _remove_position(mint)
                                if tg: tg.safe_broadcast(f"🔻 Trailing-Exit {p.get('symbol','?')} bei {drop_pct:.2f}% unter Hoch")
                                continue

                    # TP2 (Rest)
                    if p.get("tp1_hit", False) and change_pct >= CONFIG["TP2_PCT"]:
                        frac = max(0.0, min(1.0, CONFIG["TP2_SELL_PCT"]/100.0))
                        _execute_sell(mint, frac, reason=f"TP2 {CONFIG['TP2_PCT']}%")
                        _remove_position(mint)
                        if tg: tg.safe_broadcast(f"🎯 TP2 {p.get('symbol','?')} +{change_pct:.2f}% → Position geschlossen")
                        continue
                else:
                    # Einfache Voll-TP
                    if CONFIG["TP_PCT"] > 0 and change_pct >= CONFIG["TP_PCT"]:
                        _execute_sell(mint, 1.0, reason=f"TP {CONFIG['TP_PCT']}%")
                        _remove_position(mint)
                        if tg: tg.safe_broadcast(f"🎯 TP {p.get('symbol','?')} +{change_pct:.2f}% → Position geschlossen")

        except Exception as e:
            print("[TP-ENGINE] ERR:", e)

        time.sleep(10)  # Check-Interval

def _execute_sell(mint: str, fraction: float, reason: str):
    # fraction: 0..1
    if CONFIG["DRY_RUN"] == 1:
        if tg: tg.safe_broadcast(f"🧪 DRY_RUN SELL {int(fraction*100)}% {mint[:6]}… ({reason})")
        return
    # Token-Balance holen
    ui_bal, _dec = trader.get_token_balance(mint)
    amt_ui = ui_bal * fraction
    if amt_ui <= 0:
        if tg: tg.safe_broadcast(f"⚠️ SELL abgebrochen (kein Bestand) {mint[:6]}…")
        return
    res = trader.sell_to_sol(mint, amt_ui)
    if tg: tg.safe_broadcast(res)

# ---------------- Main Loop: Scan + optional Auto-Buy ----------------
def main():
    print("Starting NeoAutoSniper…")
    print(settings_text())
    start_telegram()

    # TP-Engine starten
    threading.Thread(target=_check_positions_loop, daemon=True).start()

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

            # Telegram Output
            if tg:
                if top:
                    rows = [_fmt_pair(p, liq, fdv, vol5, bestv) for (p, liq, fdv, vol5, bestv) in top]
                    if CONFIG["DRY_RUN"] == 1: rows.append("\n[MODE] DRY_RUN aktiv – keine Käufe.")
                    tg.safe_broadcast("🎯 Treffer (Top 5):\n" + "\n".join(rows), parse_mode="HTML", disable_web_page_preview=False)
                else:
                    tg.safe_broadcast("✅ [HITS] keine Treffer im aktuellen Scan.")
            else:
                print("[HITS]", len(top))

            # Optional: Auto-Buy Top-1
            if CONFIG["AUTO_BUY"] == 1 and top:
                p, liq, fdv, vol5, bestv = top[0]
                mint = (p.get("baseToken") or {}).get("address")
                symbol = (p.get("baseToken") or {}).get("symbol") or mint[:6]
                if mint and not _has_position(mint):
                    _handle_buy(mint, None, symbol)

        except Exception as e:
            print("[ERR]", e)
            time.sleep(2)

if __name__ == "__main__":
    main()
