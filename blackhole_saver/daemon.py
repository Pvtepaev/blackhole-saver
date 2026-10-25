"""Idle daemon: waits for the session to go idle, then starts the saver.

Runs from the systemd --user unit. The saver itself is a child process
(`python -m blackhole_saver saver`), so a GL crash never takes the daemon down
and every launch picks up the current config.
"""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from . import capture, config, guards, idle

log = logging.getLogger(__name__)

RETRY_SEC = 15  # how often a blocked launch is re-checked while still idle


class Daemon:
    def __init__(self, cfg, config_path):
        self.cfg = cfg
        self.config_path = config_path
        self.loop = GLib.MainLoop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:
            self.system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            self.system_bus = None
        self.session = capture.session_type()
        self.backend = idle.choose(self.session, self.bus)
        self.child = None
        self.retry = None

    # -- idle events ---------------------------------------------------------
    def _arm(self):
        timeout_ms = int(self.cfg["idle"]["timeout_sec"] * 1000)
        self.backend.start(timeout_ms, self._on_idle, self._on_active)
        log.info("session %s, idle backend: %s, timeout %d s",
                 self.session, self.backend.label, self.cfg["idle"]["timeout_sec"])
        guards.warn_timeout_vs_gnome(self.cfg)

    def _on_idle(self):
        if self.child is not None:
            return
        self._try_launch()

    def _on_active(self):
        if self.retry is not None:
            GLib.source_remove(self.retry)
            self.retry = None
            log.info("user active again; pending launch dropped")

    def _try_launch(self):
        why = guards.blockers(self.cfg, self.bus, self.system_bus)
        if why:
            log.info("idle, but not launching: %s (re-checking every %d s)",
                     ", ".join(why), RETRY_SEC)
            if self.retry is None:
                self.retry = GLib.timeout_add_seconds(RETRY_SEC, self._on_retry)
            return
        self._launch()

    def _on_retry(self):
        ms = self.backend.idle_ms()
        if ms is None or ms < self.cfg["idle"]["timeout_sec"] * 1000:
            self.retry = None
            return GLib.SOURCE_REMOVE
        if guards.blockers(self.cfg, self.bus, self.system_bus):
            return GLib.SOURCE_CONTINUE
        self.retry = None
        self._launch()
        return GLib.SOURCE_REMOVE

    # -- the saver process ---------------------------------------------------
    def _launch(self):
        pkg_parent = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [pkg_parent, env.get("PYTHONPATH")]))
        argv = [sys.executable, "-m", "blackhole_saver", "saver"]
        if self.config_path:
            argv += ["--config", str(self.config_path)]
        log.info("idle: launching saver")
        try:
            self.child = subprocess.Popen(argv, env=env)
        except OSError as e:
            log.error("cannot start saver: %s", e)
            return
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.child.pid, self._on_child_exit)

    def _on_child_exit(self, pid, status):
        code = os.waitstatus_to_exitcode(status)
        if code not in (0, -signal.SIGTERM):
            log.warning("saver exited with status %d", code)
        self.child = None

    def _on_shield(self, _bus, _sender, _path, _iface, _sig, params):
        (active,) = params.unpack()
        if active and self.child is not None:
            log.info("GNOME screen shield up (blank/lock); stopping saver")
            self.child.terminate()

    # -- lifecycle -----------------------------------------------------------
    def _on_hup(self):
        log.info("SIGHUP: reloading config")
        self.cfg = config.load(self.config_path)
        self.backend.stop()
        self._arm()
        return GLib.SOURCE_CONTINUE

    def _on_term(self):
        if self.child is not None:
            self.child.terminate()
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def run(self):
        self.bus.signal_subscribe("org.gnome.ScreenSaver", "org.gnome.ScreenSaver",
                                  "ActiveChanged", "/org/gnome/ScreenSaver", None,
                                  Gio.DBusSignalFlags.NONE, self._on_shield)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self._on_hup)
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_term)
        self._arm()
        self.loop.run()
        self.backend.stop()
        return 0


def run(cfg, config_path=None):
    try:
        d = Daemon(cfg, config_path)
    except (RuntimeError, GLib.Error) as e:
        log.error("%s", e)
        return 1
    return d.run()
