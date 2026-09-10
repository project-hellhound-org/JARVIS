#!/bin/bash
set -e

echo ""
echo "  installing J.A.R.V.I.S...."
echo ""

# ── Python Environment ────────────────────────────────────────
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$INSTALL_DIR/config.yaml" ] && [ -f "$INSTALL_DIR/config.yaml.example" ]; then
    echo "  initializing config.yaml from config.yaml.example..."
    cp "$INSTALL_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
fi

if [ -n "$VIRTUAL_ENV" ]; then
    echo "  using active virtual environment: $VIRTUAL_ENV"
    VENV_ACTIVATE="$VIRTUAL_ENV/bin/activate"
else
    if [ -f "$INSTALL_DIR/jarvis-env/bin/activate" ]; then
        echo "  using existing local virtual environment: jarvis-env"
        source "$INSTALL_DIR/jarvis-env/bin/activate"
        VENV_ACTIVATE="$INSTALL_DIR/jarvis-env/bin/activate"
    elif [ -f "$INSTALL_DIR/joe-env/bin/activate" ]; then
        echo "  using existing local virtual environment: joe-env"
        source "$INSTALL_DIR/joe-env/bin/activate"
        VENV_ACTIVATE="$INSTALL_DIR/joe-env/bin/activate"
    elif [ -f "$INSTALL_DIR/venv/bin/activate" ]; then
        echo "  using existing local virtual environment: venv"
        source "$INSTALL_DIR/venv/bin/activate"
        VENV_ACTIVATE="$INSTALL_DIR/venv/bin/activate"
    else
        VENV_DIR="$INSTALL_DIR/jarvis-env"
        echo "  setting up new virtual environment in $VENV_DIR..."
        # Note: --system-site-packages is required so pywebview can inherit system-level GTK (gi/PyGObject) bindings
        python3 -m venv --system-site-packages "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        VENV_ACTIVATE="$VENV_DIR/bin/activate"
    fi
fi

VENV_BIN="$(dirname "$VENV_ACTIVATE")"

# ── Python deps ───────────────────────────────────────────────
"$VENV_BIN/python3" -m pip install -e .
"$VENV_BIN/python3" -m pip install sherlock-project maigret holehe

# ── Playwright (headless browser for verification) ────────────
echo "  installing playwright chromium..."
"$VENV_BIN/python3" -m pip install playwright
"$VENV_BIN/python3" -m playwright install chromium 2>/dev/null || true

# ── Ollama Check (Optional — Local Fallback) ──────────────────
echo ""
echo "  ─────────────────────────────────────────────────────────"
echo "  J.A.R.V.I.S. uses NVIDIA NIM or Gemini API for primary"
echo "  inference. No local models are automatically downloaded."
echo "  ─────────────────────────────────────────────────────────"
echo ""

if command -v ollama &> /dev/null; then
    echo "  ollama detected ✓ (Optional local fallback ready if models exist)"
else
    echo "  ollama not installed (Cloud APIs will be used for LLM features)"
fi

# ── System & User Commands ─────────────────────────────────────
echo "  registering jarvis command wrapper..."

# Clean up legacy joe wrappers if present
rm -f ~/.local/bin/joe 2>/dev/null || true

mkdir -p ~/.local/bin
cat > ~/.local/bin/jarvis << WRAPPER
#!/bin/bash
source $VENV_ACTIVATE
cd $INSTALL_DIR
exec $VENV_BIN/python3 $INSTALL_DIR/jarvis.py "\$@"
WRAPPER

chmod +x ~/.local/bin/jarvis

if command -v sudo &>/dev/null && [ -w /usr/local/bin ]; then
    sudo rm -f /usr/local/bin/joe 2>/dev/null || true
    sudo bash -c "cat > /usr/local/bin/jarvis << 'WRAPPER'
#!/bin/bash
source $VENV_ACTIVATE
cd $INSTALL_DIR
exec $VENV_BIN/python3 $INSTALL_DIR/jarvis.py \"\$@\"
WRAPPER"
    sudo chmod +x /usr/local/bin/jarvis
fi

# ── Desktop entry ─────────────────────────────────────────────
echo "  creating desktop entry..."

ICON_PATH="$INSTALL_DIR/frontend/jarvis-icon.png"
if [ ! -f "$ICON_PATH" ]; then
    ICON_PATH="$INSTALL_DIR/assets/logo.png"
fi

mkdir -p ~/.local/share/applications
rm -f ~/.local/share/applications/joe-goldberg.desktop

cat > ~/.local/share/applications/jarvis.desktop << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=J.A.R.V.I.S.
GenericName=OSINT Assistant
Comment=Autonomous OSINT Assistant — zero APIs, fully local
Exec=$VENV_BIN/python3 $INSTALL_DIR/jarvis.py
Icon=$ICON_PATH
Terminal=false
Categories=Security;Network;
Keywords=osint;recon;investigation;security;pentest;jarvis;
StartupNotify=true
StartupWMClass=JARVIS
EOF

chmod +x ~/.local/share/applications/jarvis.desktop

# Refresh desktop application menu
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database ~/.local/share/applications/ 2>/dev/null
fi

# Try to refresh desktop shell if running
if command -v xdg-desktop-menu &> /dev/null; then
    xdg-desktop-menu forceupdate 2>/dev/null
fi

# ── Ollama autostart on login ─────────────────────────────────
echo "  configuring ollama autostart..."

mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/ollama.service << EOF
[Unit]
Description=Ollama LLM Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ollama serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user enable ollama.service 2>/dev/null
systemctl --user start ollama.service 2>/dev/null

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "  ✓ jarvis installed"
echo "  ✓ system command registered — run: jarvis"
echo "  ✓ desktop icon created — search 'J.A.R.V.I.S.' in your application menu"
echo "  ✓ ollama configured to start automatically on login"
echo ""
echo "  ─────────────────────────────────────────────────────────"
echo "  Gemini API key setup  (free key at https://aistudio.google.com/apikey)"
echo "  ─────────────────────────────────────────────────────────"
echo ""

# Detect the user's login shell and print the right config command
USER_SHELL="$(basename "$(getent passwd "$USER" | cut -d: -f7 2>/dev/null || echo "${SHELL:-bash}")")"

case "$USER_SHELL" in
  zsh)
    echo "  Your shell: zsh"
    echo ""
    echo "  echo 'export GEMINI_API_KEY=\"your_key_here\"' >> ~/.zshrc"
    echo "  source ~/.zshrc"
    echo ""
    echo "  (if you see shopt errors, remove any 'source ~/.bashrc' line from ~/.zshrc)"
    ;;
  fish)
    echo "  Your shell: fish"
    echo ""
    echo "  set -Ux GEMINI_API_KEY \"your_key_here\""
    ;;
  *)
    echo "  Your shell: bash / other"
    echo ""
    echo "  echo 'export GEMINI_API_KEY=\"your_key_here\"' >> ~/.bashrc"
    echo "  source ~/.bashrc"
    ;;
esac

echo ""
echo "  To make narration work from the desktop icon too, also set:"
echo "  sudo nano /usr/local/bin/jarvis"
echo "  Add: export GEMINI_API_KEY=\"your_key_here\" (below the shebang)"
echo ""