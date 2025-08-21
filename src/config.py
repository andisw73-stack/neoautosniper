import os
import re
import logging

# ---------------------------
# Hilfsfunktionen (Parsing)
# ---------------------------
def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise EnvironmentError(f"{name} ist nicht gesetzt (ENV Secret fehlt).")
    return v

def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} muss eine ganze Zahl sein (bekommen: '{raw}').")

def _as_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} muss eine Zahl sein (bek
