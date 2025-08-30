import os

def validate_env(name: str, default=None, required=False):
    """Liest eine Umgebungsvariable und prüft, ob sie vorhanden ist"""
    value = os.getenv(name, default)
    if required and value is None:
        raise ValueError(f"{name} ist erforderlich, aber nicht gesetzt!")
    return value


# === Wichtige Umgebungsvariablen ===

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = validate_env("TELEGRAM_BOT_TOKEN", required=True)

# Besitzer-ID (deine Telegram User-ID)
OWNER_ID = validate_env("OWNER_ID", required=True)

# Solana Wallet Private Key
PRIVATE_KEY = validate_env("PRIVATE_KEY", required=True)

# AutoStart des Bots (true/false)
AUTO_START = validate_env("AUTO_START", default="false")

# Debug Modus
DEBUG = validate_env("DEBUG", default="false")
