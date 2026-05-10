#!/bin/bash
# setup_mac.command - One-click setup untuk QC Marketing Bot di macOS.
#
# Cara pakai (user awam):
#   1. Download file ini dari github
#   2. Klik kanan -> Open -> Open (bypass Gatekeeper sekali)
#   3. Terminal akan kebuka & jalanin script
#   4. Input password Mac kamu (1x, saat brew minta sudo)
#   5. Tunggu ~10-15 menit
#   6. Done!

set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BOLD}  $1${NC}"
    echo -e "${BLUE}============================================================${NC}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${GREEN}[$1]${NC} $2"
    echo ""
}

print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warn()    { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }


print_header "QC Marketing Bot - Mac Setup"

echo "Script ini akan install + setup bot otomatis."
echo "Estimasi waktu: 10-15 menit (tergantung internet)."
echo ""
echo "Kamu akan diminta password Mac SEKALI (saat Homebrew install)."
echo "Pas ngetik password, Terminal TIDAK menampilkan apapun (no dots/stars)."
echo "Itu normal - Unix security feature. Ketik aja, terus tekan Enter."
echo ""
read -p "Tekan Enter untuk mulai (Ctrl+C untuk batal)..."


# ============================================================
# STEP 1: Homebrew
# ============================================================
print_step "1/5" "Cek Homebrew..."

if command -v brew &> /dev/null; then
    print_success "Homebrew sudah ke-install: $(brew --version | head -1)"
else
    print_warn "Homebrew belum ada. Install sekarang..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    if [ -d "/opt/homebrew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
        if ! grep -q "/opt/homebrew/bin/brew shellenv" "$HOME/.zprofile" 2>/dev/null; then
            echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
        fi
    elif [ -d "/usr/local/Homebrew" ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    print_success "Homebrew installed"
fi


# ============================================================
# STEP 2: Python 3.13 + git + ffmpeg
# ============================================================
print_step "2/5" "Install Python 3.13 + git + ffmpeg via brew..."

brew install python@3.13 git ffmpeg

if command -v python3.13 &> /dev/null; then
    PYTHON_BIN="python3.13"
else
    PYTHON_BIN="python3"
fi

print_success "Python: $($PYTHON_BIN --version)"
print_success "Git   : $(git --version)"
print_success "FFmpeg: $(ffmpeg -version 2>/dev/null | head -1)"


# ============================================================
# STEP 3: Clone / update repo
# ============================================================
print_step "3/5" "Clone / update repo dari github..."

TARGET_DIR="$HOME/QC_marketing"

if [ -d "$TARGET_DIR/.git" ]; then
    echo "Repo sudah ada di $TARGET_DIR. Pull update terbaru..."
    cd "$TARGET_DIR"
    git pull origin main
    print_success "Repo updated"
else
    echo "Cloning ke $TARGET_DIR..."
    cd "$HOME"
    git clone https://github.com/hendrowiakto/QC_marketing.git QC_marketing
    cd "$TARGET_DIR"
    print_success "Repo cloned to $TARGET_DIR"
fi


# ============================================================
# STEP 4: Install Python dependencies
# ============================================================
print_step "4/5" "Install Python dependencies..."

if [ ! -f "requirements.txt" ]; then
    print_error "requirements.txt missing! Repo perlu update manual."
    exit 1
fi

$PYTHON_BIN -m pip install --user --upgrade pip
$PYTHON_BIN -m pip install --user -r requirements.txt
print_success "Python dependencies installed"


# ============================================================
# STEP 5: Reminder setup credentials
# ============================================================
print_step "5/5" "Setup credential files..."

cd "$TARGET_DIR"

# Auto-copy template files kalau belum ada
for f in "config.txt" "API Claude.txt" "API Gemini.txt" "Trello.txt"; do
    if [ ! -f "$f" ] && [ -f "$f.example" ]; then
        cp "$f.example" "$f"
        echo "     Created $f from template (perlu edit isi)"
    fi
done


# ============================================================
# DONE
# ============================================================
print_header "✅ Setup Selesai!"

echo "Folder bot: ${BOLD}$TARGET_DIR${NC}"
echo ""
echo -e "${YELLOW}Next steps (manual, sekali saja):${NC}"
echo ""
echo "  1. Edit 4 file kredensial di ${BOLD}$TARGET_DIR${NC}:"
echo "     - API Claude.txt   (key dari https://console.anthropic.com)"
echo "     - API Gemini.txt   (key dari https://aistudio.google.com/apikey)"
echo "     - Trello.txt       (key + token + board IDs)"
echo "     - config.txt       (default OK, edit kalau mau adjust)"
echo ""
echo "  2. Run bot:"
echo "     ${BOLD}cd $TARGET_DIR && $PYTHON_BIN main.py${NC}"
echo ""
echo -e "${GREEN}Untuk update bot ke versi terbaru: jalanin update_mac.command${NC}"
echo ""
read -p "Tekan Enter untuk close window..."
