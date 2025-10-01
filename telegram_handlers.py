# telegram_handlers.py
# Long-Polling Telegram Client (nur 'requests')
import os
import json
import time
import threading
import requests

TG_API_TIMEOUT = 60

def _j(o):
    return json.dumps(o, ensure_ascii=False)

class TelegramBot:
    """
    Minimaler Telegram-Client mit Long-Polling.
    - send_message(...) für Outbound
    - poll_loop(...) um /start, /help, /status, /set, /dryrun, "Refresh" zu verarbeiten
    """
    def __init__(self, token: str, chat_id: int | None = None, allowed_user: int | None = None):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN fehlt.")
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = int(chat_id) if chat_id else None
        self.allowed_user = int(allowed_user) if allowed_user else None
        # Falls Chat-ID beim ersten Kontakt gelernt werden soll:
        self._learn_file = "/mnt/data/telegram_chat.json"  # survives restarts in this container session
        self._load_chat_from_disk()
        self._offset = 0
        self._stop = threading.Event()

    # ---------- persistence für auto-learn ----------
    def _load_chat_from_disk(self):
        try:
            with open(self._learn_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.chat_id = self.chat_id or data.get("chat_id")
                self.allowed_user = self.allowed_user or data.get("allowed_user")
        except Exception:
            pass

    def _save_chat_to_disk(self):
        try:
            with open(self._learn_file, "w", encoding="utf-8") as f:
                json.dump({"chat_id": self.chat_id, "allowed_user": self.allowed_user}, f)
        except Exception:
            pass

    # ---------- outbound ----------
    def send_message(self, text: str, show_menu: bool = False, disable_web_page_preview: bool = True):
        if not self.chat_id:
            return  # niemand gebunden -> später nochmal probieren, sobald Chat kommt
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_web_page_preview
        }
        if show_menu:
            payload["reply_markup"] = _j(self._main_keyboard())
        try:
            requests.post(f"{self.api}/sendMessage", data=payload, timeout=10)
        except Exception:
            pass

    def _main_keyboard(self):
        # schlichtes Menü – anpassbar
        return {
            "keyboard": [
                [{"text": "Buy"}, {"text": "Fund"}],
                [{"text": "Help"}, {"text": "Alerts"}],
                [{"text": "Wallet"}, {"text": "Settings"}],
                [{"text": "DCA Orders"}, {"text": "Limit Orders"}],
                [{"text": "Refresh"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False
        }

    # ---------- inbound (Long-Polling) ----------
    def stop(self):
        self._stop.set()

    def poll_loop(self, callbacks: dict):
        """
        callbacks:
          - get_status(): str
          - set_param(key:str, value:int|str): str  -> Rückmeldungstext
          - set_dry_run(flag:bool): str
          - refresh(): str|None
        """
        while not self._stop.is_set():
            try:
                r = requests.get(
                    f"{self.api}/getUpdates",
                    params={"timeout": 50, "offset": self._offset + 1},
                    timeout=TG_API_TIMEOUT,
                )
                data = r.json() if r.ok else {}
                for upd in data.get("result", []):
                    self._offset = upd.get("update_id", self._offset)
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    chat = msg.get("chat", {})
                    chat_id = chat.get("id")
                    text = (msg.get("text") or "").strip()

                    # Erstkontakt: erlaubten User & chat_id lernen
                    if self.allowed_user is None:
                        self.allowed_user = chat_id
                        self.chat_id = chat_id
                        self._save_chat_to_disk()
                        self.send_message("🔐 Chat verknüpft. Nur diese Chat-ID darf Befehle senden.", show_menu=True)

                    if chat_id != self.allowed_user:
                        # Ignoriere fremde Chats
                        continue

                    self.chat_id = chat_id  # sicherstellen

                    self._handle_text(text, callbacks)
            except requests.RequestException:
                time.sleep(2)
            except Exception:
                time.sleep(2)

    # ---------- parsing ----------
    def _handle_text(self, text: str, cb: dict):
        t = text.lower()
        if t in ("/start", "start", "menu", "menü"):
            self.send_message(
                "🤖 <b>NeoAutoSniper</b> ist bereit.\n"
                "Nutze /help für alle Befehle oder die Tasten unten.",
                show_menu=True)
            return

        if t in ("/help", "help"):
            self.send_message(
                "<b>Befehle</b>\n"
                "• /status – aktuelle Limits & Modus\n"
                "• /set liq 130000 – Min-Liquidität setzen\n"
                "• /set fdv 400000 – Max-FDV setzen\n"
                "• /set vol5m 20000 – Min 5-Min-Volumen setzen\n"
                "• /dryrun on|off – Käufe simulieren/aktivieren\n"
                "• Refresh – sofort scannen\n",
                show_menu=True)
            return

        if t in ("/status", "status", "settings"):
            s = cb.get("get_status", lambda: "n/a")()
            self.send_message(s, show_menu=True)
            return

        if t.startswith("/set "):
            parts = t.split()
            if len(parts) == 3:
                key = parts[1]
                val = parts[2]
                resp = cb.get("set_param", lambda *_: "Unbekannter Setter.")(key, val)
                self.send_message(resp, show_menu=False)
                return
            else:
                self.send_message("❌ Format: /set <liq|fdv|vol5m> <zahl>", show_menu=False)
                return

        if t.startswith("/dryrun"):
            flag = "on" in t or "true" in t
            resp = cb.get("set_dry_run", lambda *_: "n/a")(flag)
            self.send_message(resp)
            return

        if t == "refresh":
            resp = cb.get("refresh", lambda: None)()
            self.send_message(resp or "🔄 Scan wird ausgeführt…")
            return

        # Platzhalter für die anderen Tasten:
        if t in ("buy", "fund", "alerts", "wallet", "dca orders", "limit orders", "help"):
            self.send_message("ℹ️ Diese Funktion ist als Platzhalter angelegt.", show_menu=True)
            return

        # Fallback
        self.send_message("❓ Befehl unbekannt. /help", show_menu=True)
