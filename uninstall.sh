#!/usr/bin/env bash
# Remove blackhole-saver for the current user. apt packages are left installed.
set -euo pipefail

SHARE="$HOME/.local/share/blackhole-saver"
BIN="$HOME/.local/bin/blackhole-saver"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/blackhole-saver"
UNIT=blackhole-saver.service

echo "==> Stopping and disabling $UNIT"
systemctl --user disable --now "$UNIT" 2>/dev/null || true
rm -f -- "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
systemctl --user reset-failed "$UNIT" 2>/dev/null || true

pkgs=""
[[ -f "$SHARE/.installed-packages" ]] && pkgs=$(tr '\n' ' ' < "$SHARE/.installed-packages")

echo "==> Removing $SHARE and $BIN"
if [[ -d "$SHARE" ]]; then
    rm -rf -- "$SHARE"
fi
rm -f -- "$BIN"
if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "$XDG_RUNTIME_DIR/blackhole-saver" ]]; then
    rm -rf -- "$XDG_RUNTIME_DIR/blackhole-saver"
fi

if [[ -d "$CONF_DIR" ]]; then
    read -r -p "Delete your config in $CONF_DIR? [y/N] " ans || ans=""
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm -rf -- "$CONF_DIR"
        echo "    config deleted"
    else
        echo "    config kept"
    fi
fi

echo
echo "Uninstalled."
if [[ -n "$pkgs" ]]; then
    echo "install.sh had installed these apt packages; remove them yourself if nothing else needs them:"
    echo "  sudo apt-get remove $pkgs"
fi
