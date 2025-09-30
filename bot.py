
import os, time
from strategies import STRATEGIES
from config import get_strategy_name, DRY_RUN, AUTO_BUY, MAX_BUY_USD

def load_strategy():
    name = get_strategy_name()
    cls = STRATEGIES.get(name)
    if not cls:
        raise RuntimeError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES)}")
    return cls()

def main_loop_once():
    strat = load_strategy()
    signals = strat.get_signals()

    for s in signals:
        # Replace with real Telegram logger
        print(f"[SIGNAL] {s['strategy']} {s.get('symbol')} FDV={s.get('fdv')} LIQ={s.get('liq_usd')} VOL5m={s.get('vol5m')}")

        ok = True  # TODO: insert RugCheck/Sniffer validations here

        if ok and not DRY_RUN and AUTO_BUY:
            # TODO: call Jupiter Aggregator buy here
            # e.g., jup_buy(token_address=s['address'], amount_usd=MAX_BUY_USD)
            pass

def run_forever():
    interval = int(os.getenv("SCAN_INTERVAL", "30"))
    while True:
        try:
            main_loop_once()
        except Exception as e:
            print("[ERROR]", e)
        time.sleep(interval)

if __name__ == "__main__":
    import time, signal, sys, traceback

    running = True

    def _handle(sig, frame):
        # sauberes Herunterfahren bei Railway-Stop
        nonlocal_running = globals().get("running")
        print(f"[signal] received {sig}, shutting down ...", flush=True)
        try:
            # falls du später Ressourcen schließt, hier tun
            pass
        finally:
            if nonlocal_running is not None:
                globals()["running"] = False

    # Stop-Signale abfangen (Railway sendet SIGTERM beim Stop)
    try:
        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
    except Exception:
        pass  # manche Umgebungen erlauben das Setzen nicht

    print("NeoAutoSniper boot OK", flush=True)
    try:
        while running:
            print("Heartbeat: service alive (DRY_RUN may be on).", flush=True)
            time.sleep(30)
    except Exception:
        traceback.print_exc()
        # kurz warten, damit Logs sicher rausgehen
        time.sleep(5)


