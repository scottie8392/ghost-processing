#!/bin/bash
# Ghost Processing — launcher
# Double-click this file to set up and start the app.
# First run installs everything automatically.

cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "  Ghost Processing"
echo "  ================"
echo ""

# ── Python ────────────────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
  echo -e "  ${RED}✗ Python 3 not found.${NC}"
  echo "    Opening download page..."
  open "https://www.python.org/downloads/"
  echo ""
  read -p "  Install Python 3.11+, then double-click start.command again. Press Enter to close..."
  exit 1
fi

PY_MIN=$(python3 -c "import sys; print(sys.version_info.major * 100 + sys.version_info.minor)")
if [ "$PY_MIN" -lt 311 ]; then
  PY_STR=$(python3 --version 2>&1)
  echo -e "  ${YELLOW}⚠  $PY_STR found, but 3.11+ is required.${NC}"
  echo "    Opening download page..."
  open "https://www.python.org/downloads/"
  echo ""
  read -p "  Install Python 3.11+, then double-click start.command again. Press Enter to close..."
  exit 1
fi

# ── Homebrew ──────────────────────────────────────────────────────────────────

if ! command -v brew &>/dev/null; then
  echo "  Installing Homebrew (needed for SoX and FFmpeg)..."
  echo "  You may be asked for your password and to install Xcode Command Line Tools."
  echo ""
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Add Homebrew to PATH for Apple Silicon Macs
  [ -f "/opt/homebrew/bin/brew" ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  echo ""
fi

# ── SoX and FFmpeg ────────────────────────────────────────────────────────────

NEED_BREW=""
command -v sox    &>/dev/null || NEED_BREW="${NEED_BREW} sox"
command -v ffmpeg &>/dev/null || NEED_BREW="${NEED_BREW} ffmpeg"

if [ -n "$NEED_BREW" ]; then
  echo "  Installing:${NEED_BREW} ..."
  brew install ${NEED_BREW}
  echo -e "  ${GREEN}✓ System tools ready${NC}"
  echo ""
fi

# ── Python venv and dependencies ──────────────────────────────────────────────

if [ ! -d "ghost-processing-venv" ]; then
  echo "  First-time setup — installing Python dependencies..."
  python3 -m venv ghost-processing-venv
  source ghost-processing-venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  echo -e "  ${GREEN}✓ Python dependencies installed${NC}"
  echo ""
else
  source ghost-processing-venv/bin/activate
  pip install -q --upgrade pip 2>/dev/null
  pip install -q -r requirements.txt 2>/dev/null
fi

# ── Passwordless sudo for NFS mounting ────────────────────────────────────────
# Allows the app to mount NFS shares silently (no macOS dialogs on failure).
# Sets up a narrow sudoers rule — only mount_nfs and mkdir are allowed without
# a password. Runs once; skipped on subsequent launches.

SUDOERS_FILE="/etc/sudoers.d/ghost-processing"
RULE="$(whoami) ALL=(root) NOPASSWD: /sbin/mount_nfs, /bin/mkdir"
NEEDS_SETUP=false
if [ ! -f "$SUDOERS_FILE" ]; then
  NEEDS_SETUP=true
fi
# Also check that sudoers.d is included by the main sudoers file
if ! sudo -n grep -q "includedir" /etc/sudoers 2>/dev/null; then
  NEEDS_SETUP=true
fi
# Re-check: if sudo is already passwordless for mount_nfs, skip entirely
if sudo -n /sbin/mount_nfs --help &>/dev/null 2>&1; then
  NEEDS_SETUP=false
fi

if [ "$NEEDS_SETUP" = true ]; then
  echo "  NFS mounting requires a one-time sudo permission setup."
  echo "  You will be asked for your Mac login password (once only)."
  echo ""
  # Write the rule file
  echo "$RULE" | sudo tee "$SUDOERS_FILE" > /dev/null
  sudo chmod 440 "$SUDOERS_FILE"
  # Ensure /etc/sudoers includes the sudoers.d directory
  if ! sudo grep -q "includedir" /etc/sudoers 2>/dev/null; then
    sudo sh -c 'echo "#includedir /private/etc/sudoers.d" >> /etc/sudoers'
  fi
  # Validate — if broken, remove to avoid locking out sudo
  if sudo visudo -c &>/dev/null; then
    echo -e "  ${GREEN}✓ NFS mount permission configured${NC}"
  else
    sudo rm -f "$SUDOERS_FILE"
    echo -e "  ${YELLOW}⚠  Setup failed — NFS will fall back to Finder mount (may show dialogs)${NC}"
  fi
  echo ""
fi

# ── Launch ────────────────────────────────────────────────────────────────────

# If the server is already running, check whether a job is active.
# If it is, just reopen the browser and let it reconnect — don't kill it.
if lsof -ti:5001 &>/dev/null; then
  STATUS=$(curl -s --max-time 2 http://localhost:5001/status 2>/dev/null)
  if echo "$STATUS" | grep -q '"running": true'; then
    echo ""
    echo -e "  ${YELLOW}A job is currently running.${NC}"
    echo -e "  Reconnecting to existing session..."
    echo -e "  ${GREEN}  http://localhost:5001${NC}  ← Ctrl+click to open"
    open http://localhost:5001
    exit 0
  else
    echo "  Restarting existing session..."
    lsof -ti:5001 | xargs kill -9 2>/dev/null || true
    sleep 0.5
  fi
fi

echo ""
echo -e "  ${GREEN}✓ Ghost Processing is running${NC}"
echo -e "  ${GREEN}  http://localhost:5001${NC}  ← Ctrl+click to open"
echo "  Press Ctrl+C to stop."
echo ""

# Wait until Flask is actually listening, then open the browser
(
  for i in $(seq 1 20); do
    sleep 0.5
    if lsof -ti:5001 &>/dev/null; then
      open http://localhost:5001
      break
    fi
  done
) &

python3 app.py

# Printed after Ctrl+C — server has stopped
echo ""
echo "  ────────────────────────────────────────────"
echo -e "  ${YELLOW}Server stopped.${NC}"
echo -e "  Reopen:  ${GREEN}http://localhost:5001${NC}  ← Ctrl+click"
echo "  Run start.command again to restart."
echo "  ────────────────────────────────────────────"
echo ""
