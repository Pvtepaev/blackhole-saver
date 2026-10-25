"""Assemble GLSL sources: a #version line, config-driven #defines, the body."""

from pathlib import Path

SHADER_DIR = Path(__file__).resolve().parent / "shaders"
VERSION = "#version 330 core\n"


def _f(x):
    # GLSL needs a decimal point for floats or `1 / 2` style folding goes wrong
    return f"{float(x):.6f}"


def defines(cfg):
    h, d, r = cfg["hole"], cfg["disk"], cfg["render"]
    values = {
        "N_STEPS": str(int(r["n_steps"])),
        "SIZE_MODE": "1" if h["size_mode"] == "fixed" else "0",
        "HOLE_RADIUS": _f(h["hole_radius"]),
        "FIXED_SIZE": _f(h["fixed_size"]),
        "SIZE_MIN": _f(h["size_min"]),
        "GROW_PERIOD": _f(h["grow_period_sec"]),
        "DRIFT_SPEED": _f(h["drift_speed"]),
        "LENS_DEPTH": _f(h["lens_depth"]),
        "STAR_GAIN": _f(h["star_gain"]),
        "DILATION_MIN": _f(h["dilation_min"]),
        "DISK_INNER": _f(d["inner"]),
        "DISK_OUTER": _f(d["outer"]),
        "DISK_INCL": _f(d["incl"]),
        "DISK_ROLL": _f(d["roll"]),
        "DISK_GAIN": _f(d["gain"]),
        "DISK_OPACITY": _f(d["opacity"]),
        "DISK_TEMP": _f(d["temp"]),
        "DOPPLER_MIX": _f(d["doppler_mix"]),
        "DISK_BEAM": _f(d["beam"]),
        "DISK_SPEED": _f(d["speed"]),
        "DISK_WIND": _f(d["wind"]),
        "DISK_CONTRAST": _f(d["contrast"]),
        "EXPOSURE": _f(d["exposure"]),
    }
    return "".join(f"#define {k} {v}\n" for k, v in values.items())


def source(name, cfg=None):
    body = (SHADER_DIR / name).read_text()
    header = VERSION + (defines(cfg) if cfg is not None else "")
    return header + "#line 1\n" + body
