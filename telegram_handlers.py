
# Skeleton handlers for strategy switching and runtime thresholds.
# Integrate with your bot framework (python-telegram-bot / aiogram).
from strategies import STRATEGIES
from runtime_state import get_state, set_state, set_override

def cmd_strategy_show(update, ctx):
    st = get_state()
    names = ", ".join(STRATEGIES.keys())
    txt = (
        f"Aktuell: {st.get('strategy')}\n"
        f"Verfügbar: {names}\n"
        f"Ändern: /strategy_set <name>\n\n"
        f"Schwellwerte: /set fdv <zahl>, /set liq <zahl>, /set vol5m <zahl>"
    )
    update.message.reply_text(txt)

def cmd_strategy_set(update, ctx):
    args = ctx.args
    if not args:
        update.message.reply_text("Bitte Namen angeben, z. B. /strategy_set dexscreener")
        return
    wanted = args[0].lower()
    if wanted not in STRATEGIES:
        update.message.reply_text(f"Unbekannte Strategie: {wanted}")
        return
    set_state({"strategy": wanted})
    update.message.reply_text(f"Strategie gesetzt: {wanted}")

def cmd_set(update, ctx):
    # Usage: /set fdv 1000000  OR  /set liq 200000  OR  /set vol5m 30000
    args = ctx.args
    if len(args) != 2:
        update.message.reply_text("Nutze: /set <fdv|liq|vol5m> <zahl>")
        return
    key, val = args[0].lower(), args[1]
    valid = {"fdv": "STRAT_FDV_MAX", "liq": "STRAT_LIQ_MIN", "vol5m": "STRAT_VOL5M_MIN"}
    if key not in valid:
        update.message.reply_text("Erlaubt: fdv, liq, vol5m")
        return
    try:
        float(val)
    except Exception:
        update.message.reply_text("Bitte eine Zahl angeben, z. B. 1000000")
        return
    set_override(key, float(val))
    update.message.reply_text(f"{key} Override gesetzt auf {val} (wirksam ab nächstem Scan)")
