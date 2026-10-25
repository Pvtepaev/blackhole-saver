"""Reasons not to start the saver: locked screen, inhibitors, playing media."""

import logging

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

GSM_INHIBIT_IDLE = 8  # org.gnome.SessionManager inhibit flag "idle"


def _call(bus, name, path, iface, method, args=None, reply=None, timeout=1000):
    try:
        return bus.call_sync(name, path, iface, method, args,
                             GLib.VariantType(reply) if reply else None,
                             Gio.DBusCallFlags.NONE, timeout, None).unpack()
    except GLib.Error as e:
        log.debug("%s.%s: %s", iface, method, e.message)
        return None


def _prop(bus, name, path, iface, prop):
    r = _call(bus, name, path, "org.freedesktop.DBus.Properties", "Get",
              GLib.Variant("(ss)", (iface, prop)), "(v)")
    return r[0] if r else None


def screen_locked(bus, system_bus=None):
    """GNOME shield up (locked or blanked), or logind says the session is locked."""
    r = _call(bus, "org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
              "org.gnome.ScreenSaver", "GetActive", reply="(b)")
    if r and r[0]:
        return True
    if system_bus is not None:
        hint = _prop(system_bus, "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                     "org.freedesktop.login1.Session", "LockedHint")
        if hint:
            return True
    return False


def idle_inhibited(bus):
    """True while any app holds an idle inhibitor.

    gnome-session is the single owner of idle inhibition in GNOME: apps calling
    org.freedesktop.ScreenSaver.Inhibit (browsers, video players) and Wayland
    idle-inhibit surfaces both land here.
    """
    r = _call(bus, "org.gnome.SessionManager", "/org/gnome/SessionManager",
              "org.gnome.SessionManager", "IsInhibited",
              GLib.Variant("(u)", (GSM_INHIBIT_IDLE,)), "(b)")
    return bool(r and r[0])


def media_playing(bus):
    """True if any MPRIS player reports PlaybackStatus == Playing."""
    r = _call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
              "org.freedesktop.DBus", "ListNames", reply="(as)")
    if not r:
        return False
    for name in r[0]:
        if not name.startswith("org.mpris.MediaPlayer2."):
            continue
        status = _prop(bus, name, "/org/mpris/MediaPlayer2",
                       "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
        if status == "Playing":
            log.debug("media playing: %s", name)
            return True
    return False


def blockers(cfg, bus, system_bus=None):
    """List of human-readable reasons the saver must not start right now."""
    why = []
    if screen_locked(bus, system_bus):
        why.append("screen locked")
    if cfg["idle"]["skip_when_inhibited"] and idle_inhibited(bus):
        why.append("idle inhibited")
    if cfg["idle"]["skip_when_media_playing"] and media_playing(bus):
        why.append("media playing")
    return why


def gnome_idle_delay():
    """GNOME's own blank/lock idle delay in seconds (0 = never), or None."""
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup("org.gnome.desktop.session", True) is None:
            return None
        return Gio.Settings.new("org.gnome.desktop.session").get_uint("idle-delay")
    except GLib.Error:
        return None


def warn_timeout_vs_gnome(cfg):
    delay = gnome_idle_delay()
    timeout = cfg["idle"]["timeout_sec"]
    if delay and timeout >= delay:
        log.warning("idle timeout_sec=%d is not below GNOME's screen blank delay (%d s, "
                    "Settings > Power > Screen Blank): GNOME will blank the screen first "
                    "and the saver will never show", timeout, delay)
        return False
    return True
