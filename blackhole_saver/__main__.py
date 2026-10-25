"""blackhole-saver command line.

  blackhole-saver              run the idle daemon (what the systemd unit runs)
  blackhole-saver --test       screenshot + saver right now, quits after 10 s
  blackhole-saver saver        screenshot + saver right now, until input
"""

import argparse
import logging
import sys
from pathlib import Path

from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="blackhole-saver",
                                 description="Idle-launched black hole screensaver.")
    ap.add_argument("command", nargs="?", default="daemon", choices=("daemon", "saver"),
                    help="daemon (default): wait for idle; saver: show the saver now")
    ap.add_argument("--test", action="store_true",
                    help="launch the saver immediately and quit after 10 s")
    ap.add_argument("--duration", type=float, default=None,
                    help="saver: quit after this many seconds")
    ap.add_argument("--bg", metavar="PATH",
                    help="saver: use this image instead of taking a screenshot")
    ap.add_argument("--no-capture", action="store_true",
                    help="saver: skip the screenshot, lens the procedural starfield")
    ap.add_argument("--config", metavar="PATH", help=f"config file (default {config.CONFIG_PATH})")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(name)s: %(levelname)s: %(message)s")
    cfg = config.load(args.config)

    if args.test or args.command == "saver":
        from . import capture, renderer

        duration = 10.0 if args.test and args.duration is None else args.duration
        bg = args.bg
        if args.test:
            from . import guards
            guards.warn_timeout_vs_gnome(cfg)
        if bg is None and not args.no_capture:
            timeout = capture.PORTAL_TIMEOUT_TEST_SEC if args.test else capture.PORTAL_TIMEOUT_SEC
            bg, _ = capture.capture(method=cfg["capture"]["method"], portal_timeout=timeout)
        try:
            return renderer.run_saver(cfg, bg, duration)
        finally:
            if bg and args.bg is None:
                Path(bg).unlink(missing_ok=True)

    from . import daemon
    return daemon.run(cfg, args.config)


if __name__ == "__main__":
    sys.exit(main())
