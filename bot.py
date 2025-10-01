# bot.py — NeoAutoSniper (Railway)
# - DexScreener-Scanner (Solana)
# - Telegram-Menü (Background) – siehe telegram_handlers.py
# - Settings via ENV ODER zur Laufzeit via /Settings (werden in STATE_FILE persistiert)

import os, time, json, requests, traceback
from datetime import datetime, timezone
from telegram_handlers import start_background_polling  # <— Telegram-Menü starten

# ---------- Helpers ----------
def _parse_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on")

def _parse_int(v, default=0):
    try:
        return int(str(v).strip().replace("_","").replace(",",""))
    except:
        return default

def _env(name, default=None):
    v = os.getenv(name)
    return v if v is not None else default

# ---------- Persistent State ----------
STATE_FILE = _env("STATE_FILE", "/data/runtime_state.json")

DEFAULTS = {
    "DRY_RUN": True,
    "AUTO_BUY": False,
    "SCAN_INTERVAL": 30,        # Sekunden
    "HTTP_TIMEOUT": 15,
    "STRAT_LIQ_MIN": 130000,    # USD
    "STRAT_FDV_MAX": 400000,    # USD
    "STRAT_VOL5M_MIN": 20000,   # USD
    "STRAT_QUOTE": "SOL",       # SOL | USDC | ANY
    "STRICT_QUOTE": 1,          # 1=strikt; 0=egal
    "MAX_BUY_USD": 50,
    "MAX_AGE_MIN": 0,           # 0 = kein Alters-Filter; sonst <= X Minuten alt
}

def load_state():
    s = {}
    try:
        with open(STATE_FILE, "r") as f:
            s = json.load(f)
    except:
        s = {}
    # ENV überschreibt Defaults, State überschreibt danach ENV
    for k, v in DEFAULTS.items():
        s.setdefault(k, v)

    # ENV anwenden (falls vorhanden)
    s["DRY_RUN"]           = _parse_bool(_env("DRY_RUN",           s["DRY_RUN"]),           True)
    s["AUTO_BUY"]          = _parse_bool(_env("AUTO_BUY",          s["AUTO_BUY"]),          False)
    s["SCAN_INTERVAL"]     = _parse_int(_env("SCAN_INTERVAL",      s["SCAN_INTERVAL"]),     30)
    s["HTTP_TIMEOUT"]      = _parse_int(_env("HTTP_TIMEOUT",       s["HTTP_TIMEOUT"]),      15)
    s["STRAT_LIQ_MIN"]     = _parse_int(_env("STRAT_LIQ_MIN",      s["STRAT_LIQ_MIN"]),     130000)
    s["STRAT_FDV_MAX"]     = _parse_int(_env("STRAT_FDV_MAX",      s["STRAT_FDV_MAX"]),     400000)
    s["STRAT_VOL5M_MIN"]   = _parse_int(_env("STRAT_VOL5M_MIN",    s["STRAT_VOL5M_MIN"]),   20000)
    s["STRAT_QUOTE"]       = (_env("STRAT_QUOTE", s["STRAT_QUOTE"]) or "SOL").upper()
    s["STRICT_QUOTE"]      = _parse_int(_env("STRICT_QUOTE",       s["STRICT_QUOTE"]),      1)
    s["MAX_BUY_USD"]       = _parse_int(_env("MAX_BUY_USD",        s["MAX_BUY_USD"]),       50)
    s["MAX_AGE_MIN"]       = _parse_int(_env("MAX_AGE_MIN",        s["MAX_AGE_MIN"]),       0)
    return s

def apply_state_to_env(s):
    # macht State sofort wirksam (z.B. für Strategien / Telegram)
    for k, v in s.items():
        os.environ[k] = str(v)

# ---------- DexScreener ----------
DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search"
# Wir nutzen wenige Queries pro Zyklus, um 429 zu vermeiden
SEARCH_QUERIES = ["solana", "SOL"]  # bewusst klein halten

def fetch_pairs(http_timeout):
    pairs = {}
    for q in SEARCH_QUERIES:
        try:
            r = requests.get(DEX_SEARCH, params={"q": q}, timeout=http_timeout)
            if r.status_code != 200:
                print(f"[SCAN] {DEX_SEARCH}?q={q} -> HTTP {r.status_code}")
                # kleine Pause, damit wir Rate Limits nicht hart triggern
                time.sleep(0.7)
                continue
            data = r.json() or {}
            for p in data.get("pairs", []):
                addr = p.get("pairAddress") or p.get("url") or f"{p.get('chainId')}-{p.get('baseToken',{}).get('address')}-{p.get('quoteToken',{}).get('address')}"
                if addr not in pairs:
                    pairs[addr] = p
            time.sleep(0.4)  # sanfte Drosselung
        except Exception as e:
            print(f"[SCAN] error on query '{q}': {e}")
    return list(pairs.values())

def _get(p, *path, default=None):
    cur = p
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur

def to_int(x):
    # DexScreener liefert teilweise floats für USD; wir runden ab
    try:
        return int(float(x))
    except:
        return 0

def minutes_since_ms(ms):
    try:
        dt = datetime.fromtimestamp(ms/1000.0, tz=timezone.utc)
        return int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    except:
        return 10**9

# ---------- (Optional) Buy-Stub ----------
def maybe_buy(pair, s):
    # Platzhalter: hier würden wir z.B. Jupiter/Raydium ansteuern.
    # Aktuell nur Log, niemals echter Buy.
    base = _get(pair, "baseToken", "symbol", default="?")
    quote= _get(pair, "quoteToken","symbol", default="?")
    url  = pair.get("url","")
    print(f"[BUY-DRY] Would buy {base}/{quote} for ~${s['MAX_BUY_USD']} – {url}")

# ---------- Main Loop ----------
def main():
    print("Starting Container")
    s = load_state()
    apply_state_to_env(s)

    print("NeoAutoSniper boot OK")
    print(f"Settings: LIQ_MIN={s['STRAT_LIQ_MIN']}  FDV_MAX={s['STRAT_FDV_MAX']}  VOL5M_MIN={s['STRAT_VOL5M_MIN']}  "
          f"QUOTE={s['STRAT_QUOTE']}  STRICT={s['STRICT_QUOTE']}  DRY_RUN={s['DRY_RUN']}")

    # Telegram-Menü im Hintergrund starten
    start_background_polling()

    while True:
        try:
            print("Heartbeat: service alive (DRY_RUN may be on).")

            raw = fetch_pairs(s["HTTP_TIMEOUT"])
            total_raw = len(raw)
            print(f"[SCAN] collected {len(set(id(x) for x in raw))} unique pairs from {total_raw} raw results (fallback sources)")

            # --- Filter: nur Solana
            sol = [p for p in raw if str(_get(p, "chainId", default="")).lower() == "solana"]
            print(f"[SCAN] after relaxed-chain filter: {len(sol)} pairs (contains 'sol')")

            # --- Quote-Filter
            quote = s["STRAT_QUOTE"].upper()
            strict = int(s["STRICT_QUOTE"]) == 1
            filtered = []
            for p in sol:
                qsym = (_get(p, "quoteToken", "symbol", default="") or "").upper()
                if strict:
                    if quote != "ANY" and qsym != quote:
                        continue
                # Alters-Filter
                if s["MAX_AGE_MIN"] > 0:
                    age_min = minutes_since_ms(_get(p, "pairCreatedAt", default=0))
                    if age_min > s["MAX_AGE_MIN"]:
                        continue
                # Numerische Filter
                liq = to_int(_get(p, "liquidity", "usd", default=0))
                fdv = to_int(_get(p, "fdv", default=0))
                vol5= to_int(_get(p, "volume", "m5", default=0))
                if liq >= s["STRAT_LIQ_MIN"] and 0 < fdv <= s["STRAT_FDV_MAX"] and vol5 >= s["STRAT_VOL5M_MIN"]:
                    filtered.append(p)

            # Debug-Ausgabe (Top 5 nach Liquidity)
            filtered.sort(key=lambda p: to_int(_get(p,"liquidity","usd",default=0)), reverse=True)
            top = filtered[:5]

            # Reporting
            if strict:
                print(f"[SCAN] quote filter enabled (STRICT_QUOTE=1) -> using {len(filtered)} pairs")
            else:
                print(f"[SCAN] quote filter disabled (STRICT_QUOTE=0) -> using {len(filtered)} pairs")

            if not top:
                print("[HITS] none matching filters")
            else:
                print(f"[HITS] {len(filtered)} match(es) (top 5):")
                for p in top:
                    base = _get(p, "baseToken", "symbol", default="?")
                    quote_s = _get(p, "quoteToken", "symbol", default="?")
                    liq  = to_int(_get(p, "liquidity","usd",default=0))
                    fdv  = to_int(_get(p, "fdv", default=0))
                    vol5 = to_int(_get(p, "volume","m5",default=0))
                    ageM = minutes_since_ms(_get(p,"pairCreatedAt",default=0))
                    url  = p.get("url","")
                    print(f"  • {base}/{quote_s} | liq ${liq:,} | fdv ${fdv:,} | vol* {vol5:,} | age {ageM}m | {url}")

                    # Auto-Buy (noch Dry-Run / Stub)
                    if s["AUTO_BUY"] and not s["DRY_RUN"]:
                        maybe_buy(p, s)

            if s["DRY_RUN"]:
                print("[MODE] DRY_RUN active — no buys.")

        except Exception as e:
            print("[ERROR]", e)
            traceback.print_exc()

        # Schlafen bis zum nächsten Scan
        time.sleep(max(5, int(s["SCAN_INTERVAL"])))


if __name__ == "__main__":
    main()
