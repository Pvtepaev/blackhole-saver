# blackhole-saver

An idle-launched, fullscreen black hole screensaver for Ubuntu GNOME, on X11 and
Wayland. When the session goes idle it takes a screenshot of your desktop and
drops a ray-traced Schwarzschild black hole on top. The hole bends the desktop
around it, has a Keplerian accretion disk, and slowly grows and drifts across
every monitor. Any key press, click, scroll or mouse movement dismisses it.

The renderer is a port of `blackhole.glsl` from
[ghostty-blackhole](https://github.com/s0xDk/ghostty-blackhole) by s13k (MIT).
That shader follows Eric Bruneton's
[Real-time High-Quality Rendering of Non-Rotating Black Holes](https://ebruneton.github.io/black_hole_shader/).
These parts are carried over unchanged:
- the per-pixel null-geodesic integration (leapfrog on `x'' = -(3/2) h² x / r⁵`)
- gravitational lensing and the photon ring
- the thin Keplerian disk with its Shakura–Sunyaev temperature profile
- the Doppler and gravitational shift, relativistic beaming, and gravitational time dilation

## Install

```sh
./install.sh             # apt deps (sudo), files in ~/.local, enables the systemd --user unit
blackhole-saver --test   # screenshot + saver right now, quits after 10 s
```

`install.sh` installs any of these apt packages that are missing:
- the runner: `python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0 python3-opengl`
- X11 idle time: `libxss1 xprintidle`
- X11 screenshots: `maim imagemagick`
- Wayland screenshots: `gnome-screenshot xdg-desktop-portal xdg-desktop-portal-gnome`

It then copies the program to `~/.local/share/blackhole-saver`, puts a launcher in `~/.local/bin/blackhole-saver`, and enables `blackhole-saver.service`.

`./uninstall.sh` reverses this. It asks before deleting your config and leaves the apt packages installed, printing which ones `install.sh` added.

## Usage

| Command | What it does |
|---|---|
| `blackhole-saver` | The idle daemon (the systemd unit runs this). |
| `blackhole-saver --test` | Launches immediately and quits after 10 s (or on input). |
| `blackhole-saver saver` | Launches immediately and runs until input. |
| `blackhole-saver saver --no-capture` | Lenses the procedural starfield instead of the desktop. |
| `blackhole-saver saver --bg image.png` | Lenses any image. |
| `-v`, `--config PATH`, `--duration S` | Verbose logs, alternate config, auto-quit. |

## Configuration

`~/.config/blackhole-saver/config.toml`. Every key is documented in [config.example.toml](config.example.toml). The main ones:

| Key | Default | |
|---|---|---|
| `idle.timeout_sec` | 120 | Idle seconds before launch. Must be below GNOME's Screen Blank delay. |
| `render.render_scale` | 0.5 | Fraction of native resolution the shader runs at; it is upscaled with linear filtering. Away from the hole the desktop is drawn from the screenshot at native resolution, so only the warped area is softened. |
| `render.fps` | 30 | Frame cap, 30–60. |
| `render.n_steps` | 48 | Geodesic steps per pixel near the hole. This is the main GPU cost. |
| `hole.size_mode` | `"grow"` | `grow`: size cycles `size_min → 1 → size_min` every `grow_period_sec`. `fixed`: size stays at `fixed_size`. |
| `hole.hole_radius` | 0.08 | Shadow radius at full size, as a fraction of screen height. |
| `disk.*` | upstream | Inner/outer radius, inclination, temperature, Doppler, beaming and so on. |
| `capture.method` | `"auto"` | `portal`, `gnome-screenshot`, `maim`, `import`, or `none`. |

Config is re-read on every launch. A changed idle timeout needs `systemctl --user reload blackhole-saver`.

## How it works

- **Daemon** (`daemon.py`): runs as a `systemd --user` service bound to `graphical-session.target`. It uses `$XDG_SESSION_TYPE` to pick an idle source:
  - **Wayland:** `org.gnome.Mutter.IdleMonitor.AddIdleWatch` over D-Bus. It is event-driven, so there is no polling.
  - **X11:** `XScreenSaverQueryInfo` through libXss, polled every 2 s. If libXss is missing it falls back to `xprintidle`, then to Mutter's IdleMonitor, which also works on GNOME X11.
- **Guards** (`guards.py`): when the session goes idle, the saver does not start if any of these is true:
  - the screen is locked or blanked (`org.gnome.ScreenSaver.GetActive`, logind `LockedHint`)
  - an app holds an idle inhibitor (`org.gnome.SessionManager.IsInhibited`). GNOME routes `org.freedesktop.ScreenSaver.Inhibit` and Wayland idle-inhibit through this, so browsers and video players that inhibit are covered.
  - any MPRIS media player reports `Playing`

  While you stay idle it re-checks every 15 s.
- **Saver** (`renderer.py`): a child process that:
  1. takes the screenshot (Wayland: xdg-desktop-portal, then `gnome-screenshot`; X11: `maim`, then `import`)
  2. opens one fullscreen GTK4 window with its own `GtkGLArea` GL context per monitor, with the cursor hidden
  3. gives each monitor its own slice of the screenshot
  4. renders at `render_scale` and upscales, drawing unwarped desktop at native resolution

  If no screenshot works, a procedural starfield is lensed instead. When GNOME blanks or locks the screen, the daemon stops the saver.

## Troubleshooting

- **Logs:** `journalctl --user -u blackhole-saver -f`. Add `-v` to `ExecStart` for debug output.
- **It never starts:**
  - `systemctl --user status blackhole-saver`
  - Check that `idle.timeout_sec` is below `gsettings get org.gnome.desktop.session idle-delay` (Settings → Power → Screen Blank). Otherwise GNOME blanks the screen first. The log warns about this.
  - Look for an inhibitor. The log says `idle, but not launching: idle inhibited` or `media playing`. List inhibitors with `gdbus call --session -d org.gnome.SessionManager -o /org/gnome/SessionManager -m org.gnome.SessionManager.GetInhibitors`. A paused browser tab can keep an MPRIS player in `Playing`; set `skip_when_media_playing = false` if that bites.
- **Starfield instead of my desktop:**
  - Wayland: the first portal screenshot may show GNOME's "Allow … to take screenshots?" prompt. Run `blackhole-saver --test`, which waits up to 45 s, and allow it. If it still fails, set `capture.method = "gnome-screenshot"`. GNOME Shell allowlists `gnome-screenshot`, so it does not prompt.
  - X11: install `maim` or `imagemagick`.
  - The log line `screenshot via …` / `no screenshot method worked` tells you which method was used.
- **Choppy or hot GPU:** lower `render_scale` (0.35), `n_steps` (32) or `hole_radius`; keep `fps = 30`. Only pixels near the hole run the geodesic loop, so a bigger hole costs more.
- **Black window or immediate exit:** the saver needs desktop OpenGL 3.3. Check `glxinfo -B` and the journal for `shader compile failed` or `GL context error`. Run `blackhole-saver --test -v` in a terminal to see everything.
- **It exits immediately even though I didn't touch anything:** motion of under 8 px and anything in the first 0.7 s is ignored. Something that grabs the pointer, such as a screenshot tool, can still trip it.
- **Wayland: keys go to the app underneath / saver opens behind windows:** Mutter may refuse focus to a window opened without an activation token. Mouse movement still dismisses the saver. Check that `gsettings get org.gnome.desktop.wm.preferences focus-new-windows` is `'smart'` rather than `'strict'`.
- **A monitor isn't covered:** monitors are enumerated at launch. With mixed scale factors the screenshot slice can be slightly off, but the hole still renders.

## License

MIT. See [LICENSE](LICENSE). The shader is derived from ghostty-blackhole © 2026 s13k and keeps its credits to Eric Bruneton's black hole shader.
