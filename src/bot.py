import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from src.config import TELEGRAM_TOKEN, OWNER_ID

logger = logging.getLogger("bot")

# ---------------------------
# Telegram Bot
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != OWNER_ID:
        await update.message.reply_text("🚫 Keine Berechtigung.")
        return
    await update.message.reply_text("🤖 NeoAutoSniper Bot ist online.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != OWNER_ID:
        return
    autobuy = os.getenv("AUTOBUY", "false").lower()
    await update.message.reply_text(f"ℹ️ Status: AUTOBUY = {autobuy.upper()}")

async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != OWNER_ID:
        return
    cfg = {k: v for k, v in os.environ.items() if k.isupper()}
    msg = "\n".join([f"{k}={v}" for k, v in cfg.items() if k in ["FDV_LIMIT", "LIQ_MIN", "AUTOBUY", "TAKE_PROFIT_PCT", "STOP_LOSS_PCT"]])
    await update.message.reply_text(f"⚙️ Config:\n{msg}")

async def autobuy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /autobuy on|off")
        return
    arg = context.args[0].lower()
    if arg not in ["on", "off"]:
        await update.message.reply_text("Usage: /autobuy on|off")
        return
    os.environ["AUTOBUY"] = "true" if arg == "on" else "false"
    await update.message.reply_text(f"✅ AUTOBUY ist jetzt: {os.environ['AUTOBUY'].upper()}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != OWNER_ID:
        return
    await update.message.reply_text("💰 Balance-Funktion hier noch einzubinden.")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != OWNER_ID:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /withdraw <Ziel-Adresse> <Menge>")
        return
    target, amount = context.args[0], context.args[1]
    await update.message.reply_text(f"➡️ Withdraw von {amount} SOL an {target} gestartet (noch Dummy).")

# ---------------------------
# Setup Bot
# ---------------------------
def setup_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("config", config_cmd))
    app.add_handler(CommandHandler("autobuy", autobuy_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("withdraw", withdraw))
    return app
