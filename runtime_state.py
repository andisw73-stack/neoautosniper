
import json, os

_STATE_FILE = os.getenv("STATE_FILE", "runtime_state.json")

_DEFAULT_STATE = {
    "strategy": (os.getenv("STRATEGY", "dexscreener") or "dexscreener").lower(),
    "overrides": {
        # optional runtime overrides, e.g. {"fdv": 1000000}
    }
}

def _read() -> dict:
    try:
        with open(_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return dict(_DEFAULT_STATE)

def _write(d: dict):
    with open(_STATE_FILE, "w") as f:
        json.dump(d, f, indent=2)

def get_state() -> dict:
    return _read()

def set_state(patch: dict):
    data = _read()
    data.update(patch)
    _write(data)
    return data

def set_override(key: str, value):
    data = _read()
    ov = data.get("overrides") or {}
    ov[key] = value
    data["overrides"] = ov
    _write(data)
    return data

def get_overrides() -> dict:
    return (_read().get("overrides") or {})
