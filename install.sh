#!/usr/bin/env bash
# Install blackhole-saver for the current user and enable its systemd --user unit.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE="$HOME/.local/share/blackhole-saver"
BIN="$HOME/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/blackhole-saver"
UNIT=blackhole-saver.service

PACKAGES=(
    python3 python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0 python3-opengl  # runner
    libxss1 xprintidle                                                     # X11 idle time
    maim imagemagick                                                       # X11 screenshot
    gnome-screenshot xdg-desktop-portal xdg-desktop-portal-gnome           # Wayland screenshot
)

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user (it uses sudo only for apt)." >&2
    exit 1
fi

echo "==> Checking packages"
missing=()
for p in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
done
if ((${#missing[@]})); then
    echo "    installing: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
    printf '%s\n' "${missing[@]}" > "$SRC/.installed-packages"
else
    echo "    all present"
fi

echo "==> Installing files to $SHARE"
mkdir -p "$SHARE" "$BIN" "$UNIT_DIR" "$CONF_DIR"
if [[ -d "$SHARE/blackhole_saver" ]]; then
    rm -rf -- "$SHARE/blackhole_saver"
fi
tar -C "$SRC" --exclude=__pycache__ -cf - blackhole_saver | tar -C "$SHARE" -xf -
cp "$SRC/README.md" "$SRC/LICENSE" "$SRC/config.example.toml" "$SHARE/"
[[ -f "$SRC/.installed-packages" ]] && cp "$SRC/.installed-packages" "$SHARE/"

cat > "$BIN/blackhole-saver" <<EOF
#!/bin/sh
# blackhole-saver launcher (installed by install.sh)
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec /usr/bin/python3 -m blackhole_saver "\$@"
EOF
chmod +x "$BIN/blackhole-saver"

if [[ ! -f "$CONF_DIR/config.toml" ]]; then
    cp "$SRC/config.example.toml" "$CONF_DIR/config.toml"
    echo "    created $CONF_DIR/config.toml"
else
    echo "    kept existing $CONF_DIR/config.toml"
fi

echo "==> Enabling systemd user unit"
cp "$SRC/systemd/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
systemctl --user enable "$UNIT"
systemctl --user restart "$UNIT"
sleep 1
systemctl --user --no-pager --lines=5 status "$UNIT" || true

idle_delay=$(gsettings get org.gnome.desktop.session idle-delay 2>/dev/null | awk '{print $NF}')
echo
echo "Installed. Session type: ${XDG_SESSION_TYPE:-unknown}; GNOME screen blank after ${idle_delay:-?} s."
echo "  Try it now:     blackhole-saver --test"
echo "  Configure:      $CONF_DIR/config.toml   (then: systemctl --user reload $UNIT)"
echo "  Logs:           journalctl --user -u $UNIT -f"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  Note: $BIN is not on your PATH; log out and back in, or run $BIN/blackhole-saver";; esac
