# telegram_handlers.py – Mini-Version mit Menü & Settings
import os, json, time, threading, requests

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ALLOW = {x.strip() for x in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if x.strip()}
STATE_FILE = os.getenv("STATE_FILE", "/data/runtime_state.json")
API = f"https://api.telegram.org/bot{TOKEN}"

DEFAULTS = {
    "DRY_RUN": True,
    "AUTO_BUY": False,
    "STRAT_LIQ_MIN": 130000,
    "STRAT_FDV_MAX": 400000,
    "STRAT_VOL5M_MIN": 20000,
    "STRAT_QUOTE": "SOL",
    "STRICT_QUOTE": 1,
    "MAX_BUY_USD": 50,
}

def load_state():
    try:
        with open(STATE_FILE,"r") as f: s=json.load(f)
    except: s={}
    for k,v in DEFAULTS.items(): s.setdefault(k,v)
    return s

def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2,sort_keys=True)
    # sofort im Prozess wirksam machen:
    for k,v in s.items(): os.environ[k]=str(v)

def send(chat_id,text,keyboard=None):
    payload={"chat_id":chat_id,"text":text,"parse_mode":"Markdown"}
    if keyboard: payload["reply_markup"]=keyboard
    try: requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except: pass

def kb(rows):
    return {"keyboard":rows,"resize_keyboard":True,"one_time_keyboard":False,"is_persistent":True}

MAIN = kb([
    ["Buy","Fund"],
    ["Help","Refer Friends","Alerts"],
    ["Wallet","Settings"],
    ["DCA Orders","Limit Orders"],
    ["[ nighthawk ]","Refresh"],
])

SET = kb([
    ["DryRun ON","DryRun OFF","AutoBuy ON","AutoBuy OFF"],
    ["Quote SOL","Quote USDC","Quote ANY","StrictQuote TOGGLE"],
    ["LIQ 80k","LIQ 130k","LIQ 200k"],
    ["FDV 200k","FDV 400k","FDV 1M"],
    ["VOL5 5k","VOL5 10k","VOL5 20k"],
    ["MaxBuy $25","MaxBuy $50","MaxBuy $100"],
    ["Back"],
])

def status(s):
    return (
        "*NeoAutoSniper*\n"
        f"• DRY_RUN `{s['DRY_RUN']}`  • AUTO_BUY `{s['AUTO_BUY']}`\n"
        f"• QUOTE `{s['STRAT_QUOTE']}`  • STRICT `{s['STRICT_QUOTE']}`\n"
        f"• LIQ_MIN `${s['STRAT_LIQ_MIN']:,}`  • FDV_MAX `${s['STRAT_FDV_MAX']:,}`\n"
        f"• VOL5M_MIN `${s['STRAT_VOL5M_MIN']:,}`  • MAX_BUY `${s['MAX_BUY_USD']}`"
    )

def handle(chat_id, txt, s):
    t = (txt or "").strip().lower()
    if t in ("/start","/menu","menu","start"):
        send(chat_id,"Hauptmenü:", MAIN); send(chat_id,status(s)); return
    if t=="refresh": send(chat_id,status(s), MAIN); return
    if t=="help": send(chat_id,"Tippe *Settings* für Schalter & Presets.", MAIN); return
    if t=="settings": send(chat_id,"*Settings* – wähle:", SET); return
    if t=="back": send(chat_id,"Zurück.", MAIN); return

    changed=False
    if t=="dryrun on": s["DRY_RUN"]=True; changed=True
    elif t=="dryrun off": s["DRY_RUN"]=False; changed=True
    elif t=="autobuy on": s["AUTO_BUY"]=True; changed=True
    elif t=="autobuy off": s["AUTO_BUY"]=False; changed=True
    elif t.startswith("quote "):
        q=t.split(" ",1)[1].upper()
        if q in ("SOL","USDC","ANY"): s["STRAT_QUOTE"]=q; changed=True
    elif t=="strictquote toggle":
        s["STRICT_QUOTE"]=0 if int(s.get("STRICT_QUOTE",1))==1 else 1; changed=True
    elif t.startswith("liq "):
        s["STRAT_LIQ_MIN"]=int(t.split(" ",1)[1].replace("k","000")); changed=True
    elif t.startswith("fdv "):
        v=t.split(" ",1)[1]; s["STRAT_FDV_MAX"]=int(float(v[:-1])*1_000_000) if v.endswith("m") else int(v.replace("k","000")); changed=True
    elif t.startswith("vol5 "):
        s["STRAT_VOL5M_MIN"]=int(t.split(" ",1)[1].replace("k","000")); changed=True
    elif t.startswith("maxbuy $"):
        s["MAX_BUY_USD"]=int(t.replace("maxbuy $","")); changed=True
    else:
        # Platzhalter
        if t in ("buy","fund","wallet","dca orders","limit orders","refer friends","alerts","[ nighthawk ]"):
            send(chat_id,f"`{txt}` – Placeholder (UI steht).", MAIN); return
        send(chat_id,"Unbekannt. `/menu` öffnet das Menü.", MAIN); return

    if changed:
        save_state(s)
        send(chat_id, "✅ *Gespeichert*\n"+status(s), SET)

def loop():
    if not TOKEN or not ALLOW: print("[TG] disabled"); return
    s=load_state(); save_state(s)
    off=None
    for cid in ALLOW:
        try: send(int(cid),"NeoAutoSniper bereit. Tippe /menu.", MAIN)
        except: pass
    while True:
        try:
            p={"timeout":50}
            if off is not None: p["offset"]=off
            r=requests.get(f"{API}/getUpdates", params=p, timeout=55).json()
            for u in r.get("result",[]):
                off=u["update_id"]+1
                m=u.get("message") or u.get("edited_message"); 
                if not m or "text" not in m: continue
                cid=m["chat"]["id"]
                if str(cid) not in ALLOW: continue
                handle(cid, m["text"], s)
        except Exception as e:
            print("[TG] loop error:", e); time.sleep(2)

def start_background_polling():
    threading.Thread(target=loop, daemon=True).start()
