#!/bin/bash
# ============================================================
#  AI Sticker Ecommerce — One-click deployment (conda + systemd)
#  Conda env: sticker | Project dir: /root/AI-Sticker-Ecommerce
# ============================================================

if [ -z "$BASH_VERSION" ]; then
    echo "ERROR: Run with: bash deploy/setup.sh"
    exit 1
fi

set -euo pipefail

APP_DIR="/root/AI-Sticker-Ecommerce"
CONDA_ENV="sticker"
CONDA_BASE="/root/miniconda3"
PYTHON_BIN="${CONDA_BASE}/envs/${CONDA_ENV}/bin/python"
PIP_BIN="${CONDA_BASE}/envs/${CONDA_ENV}/bin/pip"
LOG_DIRS=("/var/log/feishu-blog-bot" "/var/log/feishu-sticker-bot")

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Please run as root: bash deploy/setup.sh"

# ---------- 1. Check conda env ----------
info "=== Step 1/5: Check conda environment ==="
[[ ! -f "$PYTHON_BIN" ]] && error "Conda env '${CONDA_ENV}' not found at ${CONDA_BASE}/envs/${CONDA_ENV}"
info "Python: $($PYTHON_BIN --version)"

# ---------- 2. Install dependencies ----------
info "=== Step 2/5: Install Python dependencies ==="
"$PIP_BIN" install -r "$APP_DIR/requirements.txt" -q
info "Dependencies installed"

# ---------- 3. Create directories ----------
info "=== Step 3/5: Create directories ==="
mkdir -p "$APP_DIR/data" "$APP_DIR/output/blogs" "$APP_DIR/logs"
for d in "${LOG_DIRS[@]}"; do
    mkdir -p "$d"
done

# ---------- 4. Check .env ----------
info "=== Step 4/5: Check .env ==="
if [[ ! -f "$APP_DIR/.env" ]]; then
    if [[ -f "$APP_DIR/.env.example" ]]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        chmod 600 "$APP_DIR/.env"
        warn ".env created from template — edit it: nano $APP_DIR/.env"
        warn "Fill in API keys before starting services!"
    else
        error "No .env or .env.example found."
    fi
else
    info ".env exists"
fi

# ---------- 5. Install systemd services ----------
info "=== Step 5/5: Configure systemd services ==="
cp "$APP_DIR/deploy/feishu-blog-bot.service"   /etc/systemd/system/
cp "$APP_DIR/deploy/feishu-sticker-bot.service" /etc/systemd/system/
cp "$APP_DIR/deploy/logrotate-feishu-bots"      /etc/logrotate.d/feishu-bots 2>/dev/null || true
systemctl daemon-reload

systemctl enable feishu-blog-bot.service
systemctl enable feishu-sticker-bot.service
systemctl restart feishu-blog-bot.service
systemctl restart feishu-sticker-bot.service

info ""
info "============================================="
info "  Deployment complete!"
info "============================================="
info ""
info "  feishu-blog-bot     $(systemctl is-active feishu-blog-bot)"
info "  feishu-sticker-bot  $(systemctl is-active feishu-sticker-bot)"
info ""
info "Commands:"
info "  systemctl status feishu-blog-bot"
info "  systemctl status feishu-sticker-bot"
info "  journalctl -u feishu-blog-bot -f"
info "  journalctl -u feishu-sticker-bot -f"
info "  systemctl restart feishu-blog-bot feishu-sticker-bot"
