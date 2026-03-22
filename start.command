#!/bin/bash
# Ghost Processing — Mac launcher
# Double-click this file in Finder to start the app.

cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "  Ghost Processing"
echo "  ================"
echo ""

# Check for Python 3
if ! command -v python3 &>/dev/null; then
  echo -e "  ${RED}✗ Python 3 not found.${NC}"
  echo "    Install from: https://www.python.org/downloads/"
  echo ""
  read -p "  Press Enter to close..."
  exit 1
fi

# Create venv and install deps if needed
if [ ! -d "ghost-processing-venv" ]; then
  echo "  First time setup — this takes about a minute..."
  echo ""
  python3 -m venv ghost-processing-venv
  source ghost-processing-venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  echo -e "  ${GREEN}✓ Dependencies installed${NC}"
  echo ""
else
  source ghost-processing-venv/bin/activate
fi

# Warn about missing system tools (sox, ffmpeg)
MISSING=""
command -v sox    &>/dev/null || MISSING="${MISSING}sox "
command -v ffmpeg &>/dev/null || MISSING="${MISSING}ffmpeg "

if [ -n "$MISSING" ]; then
  echo -e "  ${YELLOW}⚠  Missing system tools: ${MISSING}${NC}"
  echo "     Install with: brew install ${MISSING}"
  echo "     (Processing will fail without these)"
  echo ""
fi

# Kill any existing instance on port 5001
if lsof -ti:5001 &>/dev/null; then
  echo "  Restarting existing session..."
  lsof -ti:5001 | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo -e "  ${GREEN}✓ Starting at http://localhost:5001${NC}"
echo "  Press Ctrl+C here to stop."
echo ""

# Open browser after Flask starts
(sleep 1.5 && open http://localhost:5001) &

python3 app.py
