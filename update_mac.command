#!/bin/bash
# update_mac.command - One-click update + launch bot di macOS.
#
# Cara pakai:
#   1. Pastikan setup_mac.command sudah dijalankan untuk first-time install
#   2. Double-click file ini
#   3. Auto: stop bot -> git pull -> upgrade deps -> launch bot

set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${BOLD}============================================================${NC}"
echo -e "${BOLD}  QC Marketing Bot - Mac Update${NC}"
echo -e "${BOLD}============================================================${NC}"
echo ""

# Detect Python
if command -v python3.13 &> /dev/null; then
    PYTHON_BIN="python3.13"
else
    PYTHON_BIN="python3"
fi

# Brew di PATH (Apple Silicon)
if [ -d "/opt/homebrew" ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
fi

# === 1. Stop bot ===
echo -e "${GREEN}[1/4]${NC} Stop bot yang lagi jalan (kalau ada)..."
pkill -f "QC_marketing/main.py" 2>/dev/null || true
pkill -f "QC Marketing/main.py" 2>/dev/null || true
sleep 1
echo -e "${GREEN}     ✅ Done${NC}"
echo ""

# === 2. Git pull ===
echo -e "${GREEN}[2/4]${NC} Pull update dari github..."

if [ ! -d ".git" ]; then
    echo -e "${RED}❌ Folder ini bukan git repo. Run setup_mac.command dulu.${NC}"
    read -p "Tekan Enter untuk close..."
    exit 1
fi

git stash push -m "auto-stash before update" 2>/dev/null || true
git pull origin main
git stash pop 2>/dev/null || true

if [ -f "VERSION.txt" ]; then
    VERSION=$(head -1 VERSION.txt)
    PUBDATE=$(sed -n '2p' VERSION.txt)
    echo "     Versi terbaru: $VERSION"
    [ -n "$PUBDATE" ] && echo "     Last update  : $PUBDATE"
fi
echo ""

# === 3. Upgrade deps ===
echo -e "${GREEN}[3/4]${NC} Upgrade Python dependencies..."
$PYTHON_BIN -m pip install --user --upgrade --quiet -r requirements.txt 2>&1 | grep -v "already satisfied" || true
echo -e "${GREEN}     ✅ Done${NC}"
echo ""

# === 4. Launch ===
echo -e "${GREEN}[4/4]${NC} Launch bot..."
nohup $PYTHON_BIN main.py > /dev/null 2>&1 &
BOT_PID=$!
sleep 2

if kill -0 $BOT_PID 2>/dev/null; then
    echo -e "${GREEN}     ✅ Bot launched (PID $BOT_PID)${NC}"
else
    echo -e "${RED}     ❌ Bot gagal launch. Cek manual:${NC}"
    echo -e "${YELLOW}        cd $(pwd) && $PYTHON_BIN main.py${NC}"
fi

echo ""
echo -e "${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD}  UPDATE SELESAI. Bot udah launch dengan versi terbaru.${NC}"
echo -e "${BOLD}============================================================${NC}"
echo ""
sleep 3
