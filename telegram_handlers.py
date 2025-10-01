# telegram_handlers.py
# Drop-in Telegram-Integration ohne Zusatz-Dependencies (nur requests).
# – Buttons: Buy, Fund, Help, Alerts, Wallet, Settings, DCA Orders, Limit Orders, Refresh
# – Befehle: /start, /help, /settings, /refresh, /set <key> <value>, /dryrun on|off,
/autobuy on|off, /interval <sec>, /quote <SYMBOL>|off
# – Teilt sich eine mutable CONFIG-Dict mit bot.py und kann eine on_refresh()-Callback auslösen.

import os
import time
import json
import logging
import threading
from typing import Callable, Dict, Optional, Any, List

import requests

log = logging.getLogger("tg")

# Mapping für /set aliases -> CONFIG-Keys
SET_ALIASES = {
    "liq": "STRAT_LIQ_MIN",
    "fdv": "STRAT_FDV_MAX",
    "vol5m": "STRAT_VOL5M_MIN",
    "volbest": "STRAT_VOL_BEST_MIN",
    "age": "MAX_AGE_MIN",              # Minuten; optional
    "maxitems": "STRAT_MAX_ITEMS",     # wie viele Paare max. anzeigen/prüfen
    "timeout": "HTTP_TIMEOUT",         # Sekunden HTTP-Timeout
}

HELP_TEXT = (
    "🤖 *NeoAutoSniper – Hilfe*\n\n"
    "*Buttons*\n"
    "• *Refresh* – sofort scannen\n"
    "• *Settings* – aktuelle Filter anzeigen\n"
    "• Die restlichen Buttons sind Platzhalter (UI), Funktionen folgen.\n\n"
    "*Befehle*\n"
    "• `/help` – diese Hilfe\n"
    "• `/settings` – Filter zeigen\n"
    "• `/refresh` – sofort scannen\n"
    "• `/set liq 130000` – Min-Liquidität setzen\n"
    "• `/set fdv 400000` – Max-FDV setzen\n"
    "• `/set vol5m 20000` – Min 5-Min-Volumen setzen\n"
    "• `/set volbest 5000` – Min bestVol setzen (optional)\n"
    "• `/set age 120` – Max Pair-Alter (Min) (optional)\n"
    "• `/dryrun on|off` – Käufe simulieren/aktivieren\n"
    "• `/autobuy on|off` – Auto-Kauf an/aus\n"
    "• `/interval 60` – Scan-Intervall Sekunden\n"
    "• `/quote SOL` – Quote auf SOL festnageln (`/quote off` zum Freigeben)\n"
)

def _mk_keyboard() -> dict:
    # ReplyKeyboardMarkup laut Telegram Bot API
    rows = [
        [{"text": "Buy"}, {"text": "Fund"}],
        [{"text": "Help"}, {"text": "Alerts"}],
        [{"text": "Wallet"}, {"text": "Settings"}],
        [{"text": "DCA Orders"}, {"text": "Limit Orders"}],
        [{"text": "Refresh"}],
    ]
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
    }

class TelegramBot:
    def __init__(
        self,
        token: str,
        chat_id: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
        session: Optional[requests.Session] = None,
    ):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id  # kann None sein; wird bei /start gesetzt
        self.config = config if isinstance(config, dict) else {}
        self.on_refresh = on_refresh
        self.s = session or requests.Session()
        self.offset = None
        self._stop = False
        self._keyboard = _mk_keyboard()

    # ---------- Low-level ----------
    def _api(self, method: str, params: dict) -> dict:
        url = f"{self.base}/{method}"
        try:
            r = self.s.post(url, data=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning("Telegram API error %s: %s", method, e)
            return {"ok": False, "error": str(e)}

    def send_text(self, text: str, parse_mode: Optional[str] = "Markdown") -> None:
        if not self.chat_id:
            return
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": json.dumps(self._keyboard),
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        self._api("sendMessage", payload)

    def send_hits(self, title: str, rows: List[str]) -> None:
        if not self.chat_id or not rows:
            return
        msg = f"🎯 *{title}*\n" + "\n".join(rows)
        self.send_text(msg)

    # ---------- Public ----------
    def stop(self):
        self._stop = True

    def poll_forever(self, announce_ready: bool = True):
        # Optional Begrüßung, wenn Chat-ID bekannt
        if announce_ready and self.chat_id:
            self.send_text("🤖 *NeoAutoSniper* ist bereit.\nNutze */help* oder die Tasten unten.")
        while not self._stop:
            self._drain_updates()
            time.sleep(1.0)

    def _drain_updates(self):
        params = {"timeout": 25}
        if self.offset:
            params["offset"] = self.offset
        try:
            r = self.s.get(f"{self.base}/getUpdates", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("getUpdates failed: %s", e)
            return

        if not data.get("ok"):
            return

        for upd in data.get("result", []):
            self.offset = upd["update_id"] + 1
            self._handle_update(upd)

    # ---------- Handlers ----------
    def _handle_update(self, upd: dict):
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return

        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        if not text:
            return

        # Chat-ID festlegen, wenn noch nicht gesetzt
        if not self.chat_id:
            self.chat_id = chat.get("id")
            log.info("[TG] Chat verknüpft: %s", self.chat_id)
            self.send_text("🔐 Chat verknüpft. Nur diese Chat-ID darf Befehle senden.")
            self.send_text("🤖 *NeoAutoSniper* ist bereit.\nNutze */help* oder die Tasten unten.")

        t = text.lower()

        # Buttons (einfacher Text)
        if t == "refresh" or t == "/refresh":
            self._cmd_refresh()
            return
        if t == "help" or t == "/help":
            self._cmd_help()
            return
        if t == "settings" or t == "/settings":
            self._cmd_settings()
            return
        if t in ("buy", "fund", "alerts", "wallet", "dca orders", "limit orders"):
            self.send_text("ℹ️ Diese Funktion ist aktuell als Platzhalter angelegt.")
            return

        # Slash Commands
        if t.startswith("/start"):
            self.send_text("🤖 *NeoAutoSniper* ist bereit.\nNutze */help* oder die Tasten unten.")
            return
        if t.startswith("/help"):
            self._cmd_help()
            return
        if t.startswith("/settings"):
            self._cmd_settings()
            return
        if t.startswith("/refresh"):
            self._cmd_refresh()
            return
        if t.startswith("/dryrun"):
            self._cmd_toggle_flag("DRY_RUN", t)
            return
        if t.startswith("/autobuy"):
            self._cmd_toggle_flag("AUTO_BUY", t)
            return
        if t.startswith("/interval"):
            self._cmd_set_simple("SCAN_INTERVAL", t, int)
            return
        if t.startswith("/quote"):
            self._cmd_quote(t)
            return
        if t.startswith("/set"):
            self._cmd_set(t)
            return

        # Fallback
        self.send_text("❓ Unbekannter Befehl. Nutze */help*.")

    # ----- command impls -----
    def _cmd_help(self):
        self.send_text(HELP_TEXT)

    def _cmd_settings(self):
        cfg = self.config or {}
        lines = [
            "*Aktuelle Filter / Settings*",
            f"• STRATEGY = `{cfg.get('STRATEGY', 'dexscreener')}`",
            f"• CHAIN = `{cfg.get('STRAT_CHAIN', 'solana')}`",
            f"• QUOTE = `{cfg.get('STRAT_QUOTE', 'SOL')}` (STRICT={int(cfg.get('STRICT_QUOTE', 1))})",
            f"• LIQ_MIN = `{cfg.get('STRAT_LIQ_MIN')}`",
            f"• FDV_MAX = `{cfg.get('STRAT_FDV_MAX')}`",
            f"• VOL5M_MIN = `{cfg.get('STRAT_VOL5M_MIN')}`",
            f"• VOL_BEST_MIN = `{cfg.get('STRAT_VOL_BEST_MIN', 0)}`",
            f"• MAX_AGE_MIN = `{cfg.get('MAX_AGE_MIN', '∞')}`",
            f"• MAX_ITEMS = `{cfg.get('STRAT_MAX_ITEMS', 200)}`",
            f"• HTTP_TIMEOUT = `{cfg.get('HTTP_TIMEOUT', 15)}`",
            f"• INTERVAL = `{cfg.get('SCAN_INTERVAL', 60)}s`",
            f"• DRY_RUN = `{int(cfg.get('DRY_RUN', 1))}`  AUTO_BUY = `{int(cfg.get('AUTO_BUY', 0))}`",
        ]
        self.send_text("\n".join(lines))

    def _cmd_refresh(self):
        self.send_text("🔄 Scan wird gestartet …")
        try:
            if callable(self.on_refresh):
                self.on_refresh()
        except Exception as e:
            log.warning("on_refresh callback failed: %s", e)

    def _cmd_toggle_flag(self, key: str, raw: str):
        parts = raw.split()
        if len(parts) < 2:
            self.send_text(f"Nutze `/{parts[0][1:]} on|off`")
            return
        val = parts[1].lower() in ("on", "1", "true", "yes", "y")
        self.config[key] = 1 if val else 0
        self.send_text(f"✅ `{key}` = `{int(self.config[key])}` gesetzt.")

    def _cmd_set_simple(self, key: str, raw: str, cast):
        parts = raw.split()
        if len(parts) < 2:
            self.send_text(f"Nutze `/{parts[0][1:]} <wert>`")
            return
        try:
            v = cast(parts[1])
        except Exception:
            self.send_text("❌ Ungültiger Wert.")
            return
        self.config[key] = v
        self.send_text(f"✅ `{key}` = `{v}` gesetzt.")

    def _cmd_quote(self, raw: str):
        parts = raw.split()
        if len(parts) < 2:
            self.send_text("Nutze `/quote SOL` oder `/quote off`.")
            return
        arg = parts[1].upper()
        if arg == "OFF":
            self.config["STRICT_QUOTE"] = 0
            self.send_text("✅ Quote-Filter deaktiviert.")
        else:
            self.config["STRICT_QUOTE"] = 1
            self.config["STRAT_QUOTE"] = arg
            self.send_text(f"✅ Quote-Filter aktiv: `{arg}`")

    def _cmd_set(self, raw: str):
        # /set <alias> <value>
        parts = raw.split()
        if len(parts) < 3:
            self.send_text("Nutze `/set <liq|fdv|vol5m|volbest|age|maxitems|timeout> <wert>`")
            return
        alias = parts[1].lower()
        key = SET_ALIASES.get(alias)
        if not key:
            self.send_text("❌ Unbekannter Parameter. Erlaubt: liq, fdv, vol5m, volbest, age, maxitems, timeout")
            return
        try:
            val = int(float(parts[2]))
        except Exception:
            self.send_text("❌ Zahl erwartet.")
            return
        self.config[key] = val
        self.send_text(f"✅ `{key}` = `{val}` gesetzt.")
