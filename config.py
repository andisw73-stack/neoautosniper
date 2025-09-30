
import os
from runtime_state import get_state, get_overrides

def getenv_str(name: str, default: str) -> str:
    v = os.getenv(name, default)
    return v.strip() if isinstance(v, str) else default

def getenv_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except Exception:
        return float(default)

def getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return int(default)

def get_strategy_name() -> str:
    st = get_state()
    return (st.get("strategy") or os.getenv("STRATEGY", "dexscreener")).lower()

# Trading/Safety
AUTO_BUY = getenv_int("AUTO_BUY", 0)   # 0/1
DRY_RUN = getenv_int("DRY_RUN", 1)     # 1 = never buy
MAX_BUY_USD = getenv_float("MAX_BUY_USD", 50)

# Runtime overrides helpers (used by strategies optionally)
def get_override_float(key: str, env_name: str, default: float) -> float:
    ov = get_overrides()
    if key in ov:
        try:
            return float(ov[key])
        except Exception:
            pass
    return getenv_float(env_name, default)
