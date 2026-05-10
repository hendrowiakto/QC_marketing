#!/bin/bash
# install_mac.sh - One-liner bootstrap installer untuk QC Marketing Bot di Mac.
#
# Cara pakai (Mac fresh / first-time install):
#   1. Buka Terminal app (Spotlight: Cmd+Space, ketik "Terminal", Enter)
#   2. Copy-paste perintah ini, tekan Enter:
#
#      curl -fsSL https://raw.githubusercontent.com/hendrowiakto/QC_marketing/main/install_mac.sh | bash
#
#   3. Tunggu ~10-15 menit (auto-install Homebrew + Python + git + ffmpeg + clone repo)
#   4. Done!
#
# Script ini bypass Gatekeeper karena di-pipe dari curl ke bash langsung,
# tidak melalui filesystem .command yang kena quarantine attribute.

set -e

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

print_step()    { echo ""; echo -e "${GREEN}[$1]${NC} $2"; echo ""; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warn()    { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }


print_header "QC Marketing Bot - Mac Bootstrap Installer"

echo "Akan install:"
echo "  - Homebrew (kalau belum ada)"
echo "  - Python 3.13"
echo "  - git"
echo "  - ffmpeg"
echo "  - Clone repo ke ~/QC_marketing"
echo "  - Python deps"
echo ""
echo "Estimasi waktu: 10-15 menit (tergantung internet)."
echo ""
echo "Kamu akan diminta password Mac SEKALI (saat Homebrew install)."
echo "Pas ngetik password, Terminal TIDAK menampilkan apapun (no dots)."
echo "Itu normal - ketik aja, terus tekan Enter."
echo ""
read -p "Tekan Enter untuk mulai (Ctrl+C untuk batal)..." </dev/tty


# ============================================================
# 1. Homebrew
# ============================================================
print_step "1/5" "Cek Homebrew..."

if command -v brew &> /dev/null; then
    print_success "Homebrew sudah ada: $(brew --version | head -1)"
else
    print_warn "Homebrew belum ada. Install sekarang..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty

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
# 2. Python 3.13 + git + ffmpeg
# ============================================================
print_step "2/5" "Install Python 3.13 + git + ffmpeg..."

brew install python@3.13 git ffmpeg

if command -v python3.13 &> /dev/null; then
    PYTHON_BIN="python3.13"
else
    PYTHON_BIN="python3"
fi

print_success "Python: $($PYTHON_BIN --version)"
print_success "Git   : $(git --version)"
print_success "FFmpeg: $(ffmpeg -version 2>/dev/null | head -1 | cut -c1-60)"


# ============================================================
# 3. Clone / pull repo
# ============================================================
print_step "3/5" "Clone repo dari GitHub..."

TARGET_DIR="$HOME/QC_marketing"

if [ -d "$TARGET_DIR/.git" ]; then
    echo "Repo sudah ada di $TARGET_DIR. Pull update terbaru..."
    cd "$TARGET_DIR"
    git pull origin main
    print_success "Repo updated"
else
    cd "$HOME"
    git clone https://github.com/hendrowiakto/QC_marketing.git QC_marketing
    cd "$TARGET_DIR"
    print_success "Repo cloned ke $TARGET_DIR"
fi


# ============================================================
# 4. Python dependencies
# ============================================================
print_step "4/5" "Install Python dependencies..."

if [ ! -f "requirements.txt" ]; then
    print_error "requirements.txt missing!"
    exit 1
fi

$PYTHON_BIN -m pip install --user --upgrade pip
$PYTHON_BIN -m pip install --user -r requirements.txt
print_success "Python dependencies installed"


# ============================================================
# 5. Auto-create credential files + permission fix
# ============================================================
print_step "5/5" "Setup credential templates + fix permissions..."

cd "$TARGET_DIR"

# Strip quarantine attribute (defensive, untuk kalau user re-download zip)
xattr -dr com.apple.quarantine . 2>/dev/null || true

# Pastikan .command files bisa dijalankan
chmod +x *.command 2>/dev/null || true

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
echo -e "${YELLOW}Next steps (sekali saja):${NC}"
echo ""
echo "  1. Edit 4 file kredensial di ${BOLD}$TARGET_DIR${NC}:"
echo "     - ${BOLD}API Claude.txt${NC}   — key dari https://console.anthropic.com"
echo "     - ${BOLD}API Gemini.txt${NC}   — key dari https://aistudio.google.com/apikey"
echo "     - ${BOLD}Trello.txt${NC}       — key + token + board IDs"
echo "     - ${BOLD}config.txt${NC}       — settings (default OK)"
echo ""
echo "     Contoh edit pakai TextEdit:"
echo "       open -e \"$TARGET_DIR/Trello.txt\""
echo ""
echo "  2. Run bot:"
echo "     ${BOLD}cd $TARGET_DIR && $PYTHON_BIN main.py${NC}"
echo ""
echo -e "${GREEN}Untuk update bot ke versi terbaru:${NC}"
echo "     cd $TARGET_DIR && bash update_mac.command"
echo ""
echo "Atau double-click ${BOLD}update_mac.command${NC} di Finder."
echo ""
