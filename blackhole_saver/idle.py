"""Idle detection backends.

All backends share one interface:
    start(timeout_ms, on_idle, on_active)   on_idle fires once per idle period
    idle_ms() -> int | None                 current idle time
    stop()

on_active fires when the user comes back after on_idle.
"""

import ctypes
import ctypes.util
import logging
import os
import shutil
import subprocess

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

POLL_MS = 2000


class MutterIdle:
    """org.gnome.Mutter.IdleMonitor: event-driven, works on GNOME Wayland and X11."""

    NAME = "org.gnome.Mutter.IdleMonitor"
    PATH = "/org/gnome/Mutter/IdleMonitor/Core"
    IFACE = "org.gnome.Mutter.IdleMonitor"
    label = "Mutter IdleMonitor (D-Bus)"

    def __init__(self, bus):
        self.bus = bus
        self.sub = None
        self.idle_id = self.active_id = None
        if self.idle_ms() is None:
            raise RuntimeError("org.gnome.Mutter.IdleMonitor not available")

    def _call(self, method, args=None, reply=None):
        try:
            r = self.bus.call_sync(self.NAME, self.PATH, self.IFACE, method, args,
                                   GLib.VariantType(reply) if reply else None,
                                   Gio.DBusCallFlags.NONE, 2000, None)
            return r.unpack() if r is not None else ()
        except GLib.Error as e:
            log.warning("IdleMonitor.%s: %s", method, e.message)
            return None

    def idle_ms(self):
        r = self._call("GetIdletime", reply="(t)")
        return r[0] if r else None

    def start(self, timeout_ms, on_idle, on_active):
        self.on_idle, self.on_active = on_idle, on_active
        self.sub = self.bus.signal_subscribe(self.NAME, self.IFACE, "WatchFired", self.PATH,
                                             None, Gio.DBusSignalFlags.NONE, self._on_fired)
        r = self._call("AddIdleWatch", GLib.Variant("(t)", (timeout_ms,)), "(u)")
        if not r:
            raise RuntimeError("AddIdleWatch failed")
        self.idle_id = r[0]

    def _on_fired(self, _bus, _sender, _path, _iface, _sig, params):
        (watch_id,) = params.unpack()
        if watch_id == self.idle_id:
            # user-active watches are one-shot; Mutter drops them after firing
            r = self._call("AddUserActiveWatch", reply="(u)")
            self.active_id = r[0] if r else None
            self.on_idle()
        elif watch_id == self.active_id:
            self.active_id = None
            self.on_active()

    def stop(self):
        for wid in (self.idle_id, self.active_id):
            if wid is not None:
                self._call("RemoveWatch", GLib.Variant("(u)", (wid,)))
        if self.sub is not None:
            self.bus.signal_unsubscribe(self.sub)
        self.sub = self.idle_id = self.active_id = None


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong),
                ("eventMask", ctypes.c_ulong)]


class _PollingIdle:
    """Shared rising-edge logic for backends that can only be polled."""

    def start(self, timeout_ms, on_idle, on_active):
        self.timeout_ms, self.on_idle, self.on_active = timeout_ms, on_idle, on_active
        self.fired = False
        self.source = GLib.timeout_add(POLL_MS, self._poll)

    def _poll(self):
        ms = self.idle_ms()
        if ms is None:
            return GLib.SOURCE_CONTINUE
        if not self.fired and ms >= self.timeout_ms:
            self.fired = True
            self.on_idle()
        elif self.fired and ms < self.timeout_ms:
            self.fired = False
            self.on_active()
        return GLib.SOURCE_CONTINUE

    def stop(self):
        if getattr(self, "source", None):
            GLib.source_remove(self.source)
            self.source = None


class XssIdle(_PollingIdle):
    """XScreenSaverQueryInfo through ctypes (libXss), polled."""

    label = "XScreenSaver extension (libXss)"

    def __init__(self):
        xlib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        xss = ctypes.CDLL(ctypes.util.find_library("Xss") or "libXss.so.1")
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
        xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                              ctypes.POINTER(_XScreenSaverInfo)]
        self.dpy = xlib.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("cannot open X display")
        self.root = xlib.XDefaultRootWindow(self.dpy)
        self.info = xss.XScreenSaverAllocInfo()
        self.xss = xss
        if self.idle_ms() is None:
            raise RuntimeError("XScreenSaver extension not available")

    def idle_ms(self):
        if not self.xss.XScreenSaverQueryInfo(self.dpy, self.root, self.info):
            return None
        return self.info.contents.idle


class XprintidleIdle(_PollingIdle):
    """The xprintidle tool, polled (fallback when libXss can't be loaded)."""

    label = "xprintidle"

    def __init__(self):
        if not shutil.which("xprintidle") or self.idle_ms() is None:
            raise RuntimeError("xprintidle not usable")

    def idle_ms(self):
        try:
            out = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=2)
            return int(out.stdout.strip())
        except (subprocess.SubprocessError, OSError, ValueError):
            return None


def choose(session, bus):
    """Pick a backend for the session type; raises RuntimeError if none works.

    BLACKHOLE_SAVER_IDLE_BACKEND=mutter|xss|xprintidle forces one (debugging).
    """
    forced = {"mutter": lambda: MutterIdle(bus), "xss": XssIdle, "xprintidle": XprintidleIdle}
    name = os.environ.get("BLACKHOLE_SAVER_IDLE_BACKEND", "").lower()
    if name in forced:
        candidates = [forced[name]]
    elif session == "x11":
        candidates = [XssIdle, XprintidleIdle, lambda: MutterIdle(bus)]
    else:
        candidates = [lambda: MutterIdle(bus)]
    errors = []
    for make in candidates:
        try:
            return make()
        except (RuntimeError, OSError) as e:
            errors.append(str(e))
    raise RuntimeError("no idle backend available: " + "; ".join(errors))
