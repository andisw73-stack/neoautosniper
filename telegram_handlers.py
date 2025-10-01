# telegram_handlers.py — NeoAutoSniper Telegram-Steuerung
# - Long-Polling über die Telegram Bot API (kein webhook nötig)
# - /start, /help, /settings, /refresh
# - /set <key> <value>   (liq, fdv, vol5m, volbest, age, maxitems)
# - /quote <SYMBOL>|off  (setzt STRAT_QUOTE & STRICT_QUOTE)
# - /interval <sec>      (SCAN_INTERVAL anpassen)
# - /dryrun on|off
# - Menü-Buttons (einige als Platzhalter)

import time
import json
import re
from typing import Any, Dict, Optional, List

import requests


def _as_int(val, default=0) -> int:
    try:
        s = str(val).strip().replace("_", "").replace(",", "")
        return int(float(s))
    except Exception:
        return int(default)


class TelegramBot:
    def __init__(
        self,
        token: str,
        chat_id: Optional[int],
        config: Dict[str, Any],
        on_refresh=None,
    ):
        self.token = token
        self.chat_id = chat_id  # kann None sein -> beim ersten /start “lernen”
        self.config = config
        self.on_refresh = on_refresh
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.offset = 0

        # Für /set <key> <value>
        self.key_map = {
            "liq": "STRAT_LIQ_MIN",
            "fdv": "STRAT_FDV_MAX",
            "vol5m": "STRAT_VOL5M_MIN",
            "volbest": "STRAT_VOL_BEST_MIN",
            "age": "MAX_AGE_MIN",
            "maxitems": "STRAT_MAX_ITEMS",
        }

    # ------------- Low-level API -------------

    def _post(self, method: str, payload: Dict[str, Any]):
        try:
            r = requests.post(f"{self.base}/{method}", json=payload, timeout=15)
            return r.json()
        except Exception:
            return None

    def _get(self, method: str, params: Dict[str, Any]):
        try:
            r = requests.get(f"{self.base}/{method}", params=params, timeout=60)
            return r.json()
        except Exception:
            return None

    # ------------- UI / Sending -------------

    def _keyboard(self) -> Dict[str, Any]:
        # einfache Menü-Buttons
        keyboard = [
            ["Buy", "Fund"],
            ["Help", "Alerts"],
            ["Wallet", "Settings"],
            ["DCA Orders", "Limit Orders"],
            ["Refresh"],
        ]
        return {"keyboard": [[{"text": txt} for txt in row] for row in keyboard], "resize_keyboard": True}

    def send_text(self, text: str):
        if not self.chat_id:
            print("[TG] send_text: kein chat_id verknüpft — Nachricht verworfen")
            return
        self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                "reply_markup": self._keyboard(),
            },
        )

    def send_hits(self, title: str, rows: List[str]):
        msg = f"🎯 <b>{title}</b>:\n" + "\n".join(rows)
        self.send_text(msg)

    # ------------- Command Handling -------------

    def _help_text(self) -> str:
        return (
            "<b>NeoAutoSniper – Befehle</b>\n"
            "• /start – Bot verbinden\n"
            "• /help – Hilfe\n"
            "• /settings – aktuelle Einstellungen\n"
            "• /refresh – sofort scannen\n"
            "• /dryrun on|off – Käufe simulieren/aktivieren\n"
            "• /quote <SYMBOL>|off – Quote setzen (z.B. SOL, USDC; off = nicht strikt)\n"
            "• /interval <sec> – Scanintervall\n"
            "• /set liq <zahl>\n"
            "• /set fdv <zahl>\n"
            "• /set vol5m <zahl>\n"
            "• /set volbest <zahl>\n"
            "• /set age <min> – max. Alter der Pairs (0 = egal)\n"
            "• /set maxitems <anzahl>\n"
        )

    def _settings_text(self) -> str:
        c = self.config
        return (
            "<b>Aktuelle Settings</b>\n"
            f"• Strategy: {c['STRATEGY']} | Chain: {c['STRAT_CHAIN']} | Quote: {c['STRAT_QUOTE']} (STRICT={c['STRICT_QUOTE']})\n"
            f"• LIQ_MIN: {c['STRAT_LIQ_MIN']:,} | FDV_MAX: {c['STRAT_FDV_MAX']:,}\n"
            f"• VOL5M_MIN: {c['STRAT_VOL5M_MIN']:,} | VOL_BEST_MIN: {c['STRAT_VOL_BEST_MIN']:,}\n"
            f"• MAX_AGE_MIN: {c['MAX_AGE_MIN']} | MAX_ITEMS: {c['STRAT_MAX_ITEMS']}\n"
            f"• DRY_RUN: {c['DRY_RUN']} | AUTO_BUY: {c['AUTO_BUY']}\n"
            f"• INTERVAL: {c['SCAN_INTERVAL']}s\n"
        )

    def _placeholder(self, name: str):
        self.send_text("ℹ️ Diese Funktion ist als Platzhalter angelegt.")

    def _handle_text(self, chat_id: int, text: str):
        # Ersten Chat “lernen”, falls keine chat_id fix hinterlegt
        if self.chat_id is None:
            self.chat_id = chat_id
            print(f"[TG] Chat verknüpft: {self.chat_id}")
            self.send_text("🔐 Chat verknüpft. Nur diese Chat-ID darf Befehle senden.")
            self.send_text("🤖 NeoAutoSniper ist bereit.\nNutze /help für alle Befehle oder die Tasten unten.")
            return

        t = (text or "").strip()

        # Buttons -> Kommandos
        if t.lower() == "refresh":
            if self.on_refresh:
                self.on_refresh()
            self.send_text("🔄 Sofort-Scan ausgelöst.")
            return
        if t.lower() in {"buy", "fund", "wallet", "alerts", "dca orders", "limit orders"}:
            self._placeholder(t)
            return
        if t.lower() in {"help"}:
            self.send_text(self._help_text())
            return
        if t.lower() in {"settings"}:
            self.send_text(self._settings_text())
            return

        # Slash-Commands
        if t.startswith("/start"):
            self.send_text("🤖 NeoAutoSniper ist bereit.\nNutze /help für alle Befehle oder die Tasten unten.")
            return

        if t.startswith("/help"):
            self.send_text(self._help_text())
            return

        if t.startswith("/settings"):
            self.send_text(self._settings_text())
            return

        if t.startswith("/refresh"):
            if self.on_refresh:
                self.on_refresh()
            self.send_text("🔄 Sofort-Scan ausgelöst.")
            return

        m = re.match(r"^/dryrun\s+(on|off)\s*$", t, re.I)
        if m:
            v = 1 if m.group(1).lower() == "on" else 0
            self.config["DRY_RUN"] = v
            self.send_text(f"✅ DRY_RUN = {v}")
            return

        m = re.match(r"^/interval\s+(\d+)\s*$", t, re.I)
        if m:
            self.config["SCAN_INTERVAL"] = _as_int(m.group(1), self.config["SCAN_INTERVAL"])
            self.send_text(f"✅ INTERVAL = {self.config['SCAN_INTERVAL']}s")
            return

        m = re.match(r"^/quote\s+([A-Za-z]+|off)\s*$", t, re.I)
        if m:
            q = m.group(1).upper()
            if q == "OFF":
                self.config["STRICT_QUOTE"] = 0
                self.send_text("✅ STRICT_QUOTE = 0 (Quote-Filter aus)")
            else:
                self.config["STRAT_QUOTE"] = q
                self.config["STRICT_QUOTE"] = 1
                self.send_text(f"✅ Quote = {q} | STRICT_QUOTE = 1")
            return

        m = re.match(r"^/set\s+(\w+)\s+([-\w,._]+)\s*$", t, re.I)
        if m:
            key = m.group(1).lower()
            val = m.group(2)
            if key in self.key_map:
                cfg_key = self.key_map[key]
                self.config[cfg_key] = _as_int(val, self.config[cfg_key])
                self.send_text(f"✅ {cfg_key} = {self.config[cfg_key]:,}")
            else:
                self.send_text("Unbekannter Schlüssel. Erlaubt: liq, fdv, vol5m, volbest, age, maxitems.")
            return

        # Fallback
        self.send_text("❓ Unbekannter Befehl. Nutze /help.")

    # ------------- Poll loop -------------

    def poll_forever(self):
        # Begrüßung, wenn Chat-ID bereits gesetzt ist
        if self.chat_id:
            try:
                self.send_text("🤖 NeoAutoSniper ist bereit.\nNutze /help für alle Befehle oder die Tasten unten.")
            except Exception:
                pass

        while True:
            try:
                data = self._get(
                    "getUpdates",
                    {"timeout": 25, "allowed_updates": json.dumps(["message"]), "offset": self.offset + 1},
                )
                if not data or not data.get("ok"):
                    time.sleep(1)
                    continue

                for upd in data.get("result", []):
                    self.offset = max(self.offset, int(upd.get("update_id", 0)))
                    msg = upd.get("message") or {}
                    chat = (msg.get("chat") or {}).get("id")
                    text = msg.get("text") or ""

                    # Wenn ein Chat verknüpft ist, ignoriere andere Chats
                    if self.chat_id and chat != self.chat_id:
                        continue

                    self._handle_text(int(chat) if chat else 0, text)

            except Exception:
                time.sleep(1)
