import os

def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} muss eine Zahl sein (bekam: {value})")

def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} muss eine Kommazahl sein (bekam: {value})")

def get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower()
    return value in ("1", "true", "yes", "on")

# Konfigurationen
AUTO_START = get_env_bool("AUTO_START", True)
CONFIRM_TIMEOUT_SEC = get_env_int("CONFIRM_TIMEOUT_SEC", 20)
MAX_PAIR_AGE_SEC = get_env_int("MAX_PAIR_AGE_SEC", 600)
JSON_LOGS = get_env_bool("JSON_LOGS", False)
JUP_SIMULATE = get_env_bool("JUP_SIMULATE", True)

# API Keys und Tokens
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = get_env_int("OWNER_ID", 0)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

if AUTO_START and not PRIVATE_KEY:
    print("⚠️ Warnung: AUTOBUY ist aktiviert, aber kein PRIVATE_KEY gesetzt!")
