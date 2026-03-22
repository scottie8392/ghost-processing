#!/bin/bash
# Ghost Processing — first-time setup
# Run this once: bash setup.sh

cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "  Ghost Processing — Setup"
echo "  ========================"
echo ""

# Python
if ! command -v python3 &>/dev/null; then
  echo -e "  ${RED}✗ Python 3 not found.${NC}"
  echo "    Install from: https://www.python.org/downloads/"
  exit 1
fi
echo -e "  ${GREEN}✓ Python $(python3 --version)${NC}"

# Virtual environment
if [ -d "ghost-processing-venv" ]; then
  echo "  Updating existing virtual environment..."
else
  echo "  Creating virtual environment..."
  python3 -m venv ghost-processing-venv
fi

source ghost-processing-venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo -e "  ${GREEN}✓ Python dependencies installed${NC}"

# Check Homebrew (needed for sox/ffmpeg on Mac)
echo ""
echo "  Checking system tools..."

if ! command -v brew &>/dev/null; then
  echo -e "  ${YELLOW}⚠  Homebrew not found.${NC}"
  echo "     Install from: https://brew.sh"
  echo "     Then run: brew install sox ffmpeg"
else
  # SoX
  if command -v sox &>/dev/null; then
    echo -e "  ${GREEN}✓ SoX: $(sox --version 2>&1 | head -1)${NC}"
  else
    echo -e "  ${YELLOW}⚠  SoX not found — installing...${NC}"
    brew install sox && echo -e "  ${GREEN}✓ SoX installed${NC}"
  fi

  # FFmpeg
  if command -v ffmpeg &>/dev/null; then
    echo -e "  ${GREEN}✓ FFmpeg: $(ffmpeg -version 2>&1 | head -1)${NC}"
  else
    echo -e "  ${YELLOW}⚠  FFmpeg not found — installing...${NC}"
    brew install ffmpeg && echo -e "  ${GREEN}✓ FFmpeg installed${NC}"
  fi
fi

# Make launcher executable
chmod +x start.command 2>/dev/null || true

echo ""
echo "  ================================="
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  To start: double-click start.command in Finder"
echo "  Or from terminal: bash start.command"
echo "  Then open: http://localhost:5001"
echo ""
