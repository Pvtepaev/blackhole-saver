# CLAUDE.md

Idle-launched black hole screensaver for Ubuntu GNOME (X11 + Wayland). User-facing docs are in README.md; every config key is documented in config.example.toml.

## Runtime constraints

- Runs on the **system** `/usr/bin/python3` (needs 3.11+ for `tomllib`). No pip deps and no venv: GTK4 (`python3-gi`, `gir1.2-gtk-4.0`) and PyOpenGL (`python3-opengl`) come from apt. Don't add PyPI dependencies; if something new is needed, add the apt package to `PACKAGES` in install.sh and the README list.
- OpenGL 3.3 core (`#version 330 core`, prepended by shader.py).
- Not a pip package: install.sh copies `blackhole_saver/` to `~/.local/share/blackhole-saver` and the launcher/systemd unit set `PYTHONPATH` to it.

## Layout

- `__main__.py` CLI: `daemon` (default), `saver`, `--test`.
- `daemon.py` idle loop (systemd `--user` service); SIGHUP reloads config.
- `idle.py` idle sources: Mutter IdleMonitor (D-Bus, Wayland), libXss / xprintidle (X11).
- `guards.py` don't-launch checks: lock/blank, idle inhibitors, MPRIS playing.
- `capture.py` screenshot: xdg-desktop-portal, gnome-screenshot, maim, import.
- `renderer.py` GTK4 fullscreen window + GtkGLArea per monitor; spawned as a child process by the daemon.
- `shader.py` builds GLSL from `shaders/` with config values injected as `#define`s.
- `config.py` defaults, TOML loading, clamping. Defaults here must match config.example.toml.
- `shaders/blackhole.frag` port of ghostty-blackhole's `blackhole.glsl` (MIT). Keep the physics (geodesic integration, disk, Doppler/beaming) faithful to upstream.

## Running / testing

No automated tests. Check changes manually:

```sh
python3 -m blackhole_saver --test -v              # from the repo root, 10 s run with screenshot
python3 -m blackhole_saver saver --no-capture -v  # starfield only, no screenshot
python3 -m blackhole_saver daemon -v              # idle daemon in the foreground
./install.sh && journalctl --user -u blackhole-saver -f   # installed service
```

GUI/GL code can only be verified in a real GNOME session; say so when a change hasn't been run.

## Conventions

- When adding a config key: add it to the defaults and clamping in config.py, document it in config.example.toml, and if the shader uses it, map it in `shader.defines()`.
- Keep install.sh / uninstall.sh symmetric.
