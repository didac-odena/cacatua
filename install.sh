#!/usr/bin/env bash
# Whisper dictation - Linux installer (Arch-based distro + KDE Plasma on Wayland)
# Usage: ./install.sh
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
venv="$root/.venv"

echo "== Whisper dictation installer (Linux) =="

# tk: floating pill. python-gobject + libayatana-appindicator: tray icon.
# wl-clipboard: clipboard on Wayland. ydotool: sends Ctrl+V to other apps.
echo "Installing system packages..."
sudo pacman -S --needed --noconfirm python tk python-gobject libayatana-appindicator wl-clipboard ydotool

if [ ! -d "$venv" ]; then
    echo "Creating virtual environment..."
    # System site-packages so the venv sees the distro's python-gobject.
    python3 -m venv --system-site-packages "$venv"
fi

echo "Installing base requirements..."
"$venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install -r "$root/requirements.txt"

if command -v nvidia-smi >/dev/null; then
    echo "NVIDIA GPU detected - installing CUDA libraries..."
    "$venv/bin/python" -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
else
    echo "No NVIDIA GPU detected - will run on CPU (slower transcription)."
fi

# ydotool needs read/write on /dev/uinput. systemd usually grants it to the
# logged-in user; if not, add a udev rule.
if [ ! -w /dev/uinput ]; then
    echo "Granting /dev/uinput access to the active user..."
    echo 'KERNEL=="uinput", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/60-uinput-uaccess.rules >/dev/null
    sudo udevadm control --reload
    sudo udevadm trigger /dev/uinput
fi
systemctl --user enable --now ydotool.service

# systemd user service: starts at login and relaunches it if it dies
# (the equivalent of launch.vbs). Exiting from the menu or Ctrl+Alt+Q is
# a clean exit (status 0), so it stays stopped.
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
cat > "$unit_dir/cacatua.service" <<EOF
[Unit]
Description=Cacatua - dictado por voz local
PartOf=graphical-session.target
After=graphical-session.target ydotool.service
Wants=ydotool.service

[Service]
ExecStart=$venv/bin/python $root/main.py
Restart=always
RestartSec=3
RestartPreventExitStatus=0

[Install]
WantedBy=graphical-session.target
EOF
systemctl --user daemon-reload
systemctl --user enable cacatua.service
echo "systemd user service 'cacatua' registered (starts at login)."

# Global shortcuts: Wayland apps can't grab keys, so KDE runs the command.
apps="$HOME/.local/share/applications"
mkdir -p "$apps"
make_shortcut() {  # id, name, argument, KDE key, Qt key code
    cat > "$apps/$1.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Exec=$venv/bin/python $root/main.py $3
Icon=audio-input-microphone
NoDisplay=true
X-KDE-Shortcuts=$4
EOF
    local action="['$1.desktop','_launch','$2','$2']"
    local kga="gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel --method org.kde.KGlobalAccel"
    $kga.doRegister "$action" >/dev/null
    $kga.setForeignShortcutKeys "$action" "[([$5, 0, 0, 0],)]" >/dev/null
    kwriteconfig6 --file kglobalshortcutsrc --group services --group "$1.desktop" --key _launch "$4"
}
if command -v kwriteconfig6 >/dev/null; then
    make_shortcut cacatua-toggle "Cacatua: dictar" --toggle F9 16777272
    make_shortcut cacatua-quit "Cacatua: salir" --quit "Ctrl+Alt+Q" 201326673
    echo "KDE shortcuts registered: F9 (dictar), Ctrl+Alt+Q (salir)."
else
    echo "KDE not found: bind F9 to '$venv/bin/python $root/main.py --toggle' in your desktop's shortcut settings."
fi

echo ""
echo "Install complete."
echo "First run will download the Whisper model (~500 MB for small, ~1.5 GB for medium)."
echo "Start now with: systemctl --user start cacatua"
echo "Or log out and back in and it will start at login."
