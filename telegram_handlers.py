# telegram_handlers.py
# Minimaler Long-Poll-Handler ohne externe Bot-Library
import time, threading, requests, html
from typing import Optional, Callable, List

TG_API = "https://api.telegram.org"

class TelegramBot:
    def __init__(
        self,
        token: str,
        fixed_chat_id: Optional[str],
        on_command: Callable[[int, str, List[str]], None],
        on_button:  Callable[[int, str], None],
    ):
        self.token = token
        self.fixed_chat_id = fixed_chat_id  # wenn gesetzt, nur diese Chat-ID akzeptieren
        self.on_command = on_command
        self.on_button = on_button
        self.last_update_id = 0
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)

    # ------------ Public API ------------
    def start(self):
        self.thread.start()

    def _api(self, method: str, **params):
        url = f"{TG_API}/bot{self.token}/{method}"
        try:
            r = requests.post(url, json=params, timeout=15)
            return r.json()
        except Exception as e:
            print(f"[TG] API-ERR {method}: {e}")
            return {"ok": False}

    def safe_send(self, chat_id: int, text: str, parse_mode: Optional[str]=None, disable_web_page_preview=True):
        if self.fixed_chat_id and str(chat_id) != str(self.fixed_chat_id):
            print("[TG] send_text: kein chat_id verknüpft – Nachricht verworfen")
            return
        self._api(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=self._keyboard()
        )

    def safe_broadcast(self, text: str, parse_mode: Optional[str]=None, disable_web_page_preview=True):
        if self.fixed_chat_id:
            self.safe_send(int(self.fixed_chat_id), text, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)

    def send_keyboard(self, chat_id: int):
        self._api("sendMessage", chat_id=chat_id, text="Menü:", reply_markup=self._keyboard())

    def _keyboard(self):
        # Acht Buttons, wie besprochen
        return {
            "keyboard": [
                [{"text":"Buy"},{"text":"Fund"}],
                [{"text":"Help"},{"text":"Alerts"}],
                [{"text":"Wallet"},{"text":"Settings"}],
                [{"text":"DCA Orders"},{"text":"Limit Orders"}],
                [{"text":"Refresh"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
            "is_persistent": True
        }

    # ------------ Polling ------------
    def _poll_loop(self):
        while True:
            try:
                url = f"{TG_API}/bot{self.token}/getUpdates"
                r = requests.get(url, params={"timeout": 30, "offset": self.last_update_id + 1}, timeout=35)
                data = r.json()
                if not data.get("ok"):
                    time.sleep(2)
                    continue
                for upd in data.get("result", []):
                    self.last_update_id = max(self.last_update_id, upd.get("update_id", 0))
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    if not msg: 
                        continue
                    chat_id = msg.get("chat", {}).get("id")
                    text = (msg.get("text") or "").strip()
                    if not chat_id or not text:
                        continue
                    # Chat-ID Restriktion
                    if self.fixed_chat_id and str(chat_id) != str(self.fixed_chat_id):
                        # Ignorieren, aber „Chat verknüpft“-Hinweis schicken
                        self._api("sendMessage", chat_id=chat_id, text="🔒 Chat verknüpft. Nur diese Chat-ID darf Befehle senden.")
                        continue

                    if text.startswith("/"):
                        parts = text.split()
                        cmd = parts[0]
                        args = parts[1:]
                        self.on_command(chat_id, cmd, args)
                    else:
                        # Buttons
                        self.on_button(chat_id, text)
            except Exception as e:
                print(f"[TG] poll error: {e}")
                time.sleep(2)
