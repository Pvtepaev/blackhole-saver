"""Grab a screenshot of the desktop right before the saver's windows appear."""

import logging
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

PORTAL_TIMEOUT_SEC = 4          # daemon launches: never hold the saver up for long
PORTAL_TIMEOUT_TEST_SEC = 45    # --test: long enough to answer GNOME's "Allow screenshot?" prompt
TOOL_TIMEOUT_SEC = 5


def session_type():
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st in ("wayland", "x11"):
        return st
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/blackhole-saver-{os.getuid()}"
    d = Path(base) / "blackhole-saver"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _run_tool(argv, out):
    if not shutil.which(argv[0]):
        return False
    try:
        subprocess.run(argv, check=True, timeout=TOOL_TIMEOUT_SEC,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (subprocess.SubprocessError, OSError) as e:
        log.debug("%s failed: %s", argv[0], e)
        return False
    return out.exists() and out.stat().st_size > 0


def _portal(out, timeout_sec=PORTAL_TIMEOUT_SEC):
    """xdg-desktop-portal Screenshot, non-interactive.

    GNOME may ask once whether this app may take screenshots; until that is
    granted (run `blackhole-saver --test` and allow it) the request is denied
    or times out and we fall back to the next method.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as e:
        log.debug("portal: no session bus: %s", e.message)
        return False
    token = "blackhole_" + secrets.token_hex(6)
    sender = bus.get_unique_name()[1:].replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    loop = GLib.MainLoop()
    result = {}

    def on_response(_bus, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        result["code"] = code
        result["uri"] = results.get("uri")
        loop.quit()

    sub = bus.signal_subscribe("org.freedesktop.portal.Desktop",
                               "org.freedesktop.portal.Request", "Response",
                               request_path, None, Gio.DBusSignalFlags.NONE,
                               on_response)
    try:
        bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                      "org.freedesktop.portal.Screenshot", "Screenshot",
                      GLib.Variant("(sa{sv})", ("", {
                          "handle_token": GLib.Variant("s", token),
                          "interactive": GLib.Variant("b", False),
                      })),
                      GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 3000, None)
        if timeout_sec > PORTAL_TIMEOUT_SEC:
            log.info("waiting up to %d s for the screenshot portal; if GNOME asks "
                     "whether to allow screenshots, choose Allow", timeout_sec)
        timer = GLib.timeout_add_seconds(timeout_sec, loop.quit)
        loop.run()
        if "code" in result:
            GLib.source_remove(timer)
    except GLib.Error as e:
        log.debug("portal: %s", e.message)
        return False
    finally:
        bus.signal_unsubscribe(sub)

    if result.get("code") != 0 or not result.get("uri"):
        log.info("portal screenshot not available (response %s)", result.get("code", "timeout"))
        return False
    src = Path(unquote(urlparse(result["uri"]).path))
    try:
        # the portal saves into ~/Pictures; move it out so screenshots don't pile up
        shutil.move(str(src), out)
    except OSError as e:
        log.debug("portal: cannot move %s: %s", src, e)
        return False
    return True


def capture(out=None, method="auto", portal_timeout=PORTAL_TIMEOUT_SEC):
    """Return (path, method) of a fresh desktop screenshot, or (None, None).

    method: "auto" (by session type), "portal", "gnome-screenshot", "maim",
    "import", or "none" (always use the starfield).
    """
    out = Path(out) if out else runtime_dir() / "bg.png"
    out.unlink(missing_ok=True)
    st = session_type()
    tools = {
        "portal": lambda: _portal(out, portal_timeout),
        "gnome-screenshot": lambda: _run_tool(["gnome-screenshot", "-f", str(out)], out),
        "maim": lambda: _run_tool(["maim", "--hidecursor", str(out)], out),
        "import": lambda: _run_tool(["import", "-silent", "-window", "root", str(out)], out),
    }
    if method == "none":
        return None, None
    if method in tools:
        order = [method]
    elif st == "x11":
        order = ["maim", "import", "portal"]
    else:
        order = ["portal", "gnome-screenshot"]
    for name in order:
        if tools[name]():
            log.info("screenshot via %s (%s session)", name, st)
            return str(out), name
    log.warning("no screenshot method worked (%s session, tried %s); using the starfield",
                st, ", ".join(order))
    return None, None
