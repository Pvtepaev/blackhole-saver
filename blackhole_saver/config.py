"""Config loading for ~/.config/blackhole-saver/config.toml.

Every key has a default and a valid range; values outside the range are
clamped and unknown keys are reported, so a typo never stops the saver.
"""

import logging
import os
import tomllib
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blackhole-saver"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# section -> key -> (default, min, max); for string keys the second slot is
# the tuple of allowed values
SCHEMA = {
    "idle": {
        "timeout_sec": (120, 5, 86400),
        "skip_when_inhibited": (True, None, None),
        "skip_when_media_playing": (True, None, None),
    },
    "capture": {
        "method": ("auto", ("auto", "portal", "gnome-screenshot", "maim", "import", "none"), None),
    },
    "render": {
        "render_scale": (0.5, 0.1, 1.0),
        "fps": (30, 30, 60),
        "n_steps": (48, 8, 256),
        "fade_in_sec": (1.5, 0.0, 10.0),
    },
    "hole": {
        "size_mode": ("grow", ("grow", "fixed"), None),
        "hole_radius": (0.08, 0.005, 0.5),
        "fixed_size": (0.6, 0.1, 1.0),
        "size_min": (0.25, 0.1, 1.0),
        "grow_period_sec": (180.0, 10.0, 7200.0),
        "drift_speed": (0.35, 0.0, 5.0),
        "lens_depth": (13.0, 1.0, 100.0),
        "star_gain": (0.6, 0.0, 5.0),
        "dilation_min": (0.2, 0.0, 1.0),
    },
    "disk": {
        "inner": (1.8, 1.6, 50.0),
        "outer": (8.0, 2.0, 60.0),
        "incl": (1.5, 0.0, 3.1416),
        "roll": (0.35, -3.1416, 3.1416),
        "gain": (2.2, 0.0, 20.0),
        "opacity": (0.9, 0.0, 1.0),
        "temp": (5500.0, 1500.0, 40000.0),
        "doppler_mix": (0.6, 0.0, 1.0),
        "beam": (2.5, 0.0, 8.0),
        "speed": (5.0, -50.0, 50.0),
        "wind": (7.0, 0.0, 50.0),
        "contrast": (1.6, 0.0, 10.0),
        "exposure": (1.4, 0.0, 10.0),
    },
}


def defaults():
    return {sec: {k: spec[0] for k, spec in keys.items()} for sec, keys in SCHEMA.items()}


def _coerce(sec, key, value):
    default, lo, hi = SCHEMA[sec][key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            log.warning("config: [%s] %s must be true/false, using %s", sec, key, default)
            return default
        return value
    if isinstance(default, str):
        if value not in lo:
            log.warning("config: [%s] %s must be one of %s, using %r", sec, key, lo, default)
            return default
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        log.warning("config: [%s] %s must be a number, using %s", sec, key, default)
        return default
    clamped = min(max(value, lo), hi)
    if clamped != value:
        log.warning("config: [%s] %s = %s out of range [%s, %s], clamped to %s",
                    sec, key, value, lo, hi, clamped)
    return int(clamped) if isinstance(default, int) else float(clamped)


def load(path=None):
    """Return the merged config dict; never raises on a bad file."""
    path = Path(path) if path else CONFIG_PATH
    cfg = defaults()
    try:
        with open(path, "rb") as f:
            user = tomllib.load(f)
    except FileNotFoundError:
        return cfg
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("config: cannot read %s (%s), using defaults", path, e)
        return cfg

    for sec, keys in user.items():
        if sec not in SCHEMA or not isinstance(keys, dict):
            log.warning("config: unknown section [%s] ignored", sec)
            continue
        for key, value in keys.items():
            if key not in SCHEMA[sec]:
                log.warning("config: unknown key [%s] %s ignored", sec, key)
                continue
            cfg[sec][key] = _coerce(sec, key, value)

    if cfg["disk"]["outer"] < cfg["disk"]["inner"] + 0.5:
        cfg["disk"]["outer"] = cfg["disk"]["inner"] + 0.5
    return cfg
