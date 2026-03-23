#!/usr/bin/env bash
# ============================================================
#  AI Sticker Ecommerce — Ubuntu Deployment Script
#  Deploys: Feishu Blog Bot + Feishu Sticker Bot (systemd)
# ============================================================
set -euo pipefail

APP_DIR="/opt/ai-sticker-ecommerce"
DEPLOY_USER="deploy"
REPO_URL="https://github.com/YOUR_USER/AI-Sticker-Ecommerce.git"   # <-- change this
PYTHON_VERSION="3.12"
LOG_DIRS=("/var/log/feishu-blog-bot" "/var/log/feishu-sticker-bot")

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---------- Pre-flight checks ----------
[[ $EUID -ne 0 ]] && error "Please run as root:  sudo bash deploy/setup.sh"

info "=== Step 1/8: System packages ==="
apt-get update -qq
apt-get install -y -qq software-properties-common curl git

if ! command -v "python${PYTHON_VERSION}" &>/dev/null; then
    info "Adding deadsnakes PPA for Python ${PYTHON_VERSION}..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev"
fi
info "Python: $(python${PYTHON_VERSION} --version)"

# ---------- Deploy user ----------
info "=== Step 2/8: Deploy user ==="
if ! id "$DEPLOY_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$DEPLOY_USER"
    info "Created user: $DEPLOY_USER"
else
    info "User $DEPLOY_USER already exists"
fi

# ---------- Clone / pull repo ----------
info "=== Step 3/8: Application code ==="
if [[ -d "$APP_DIR/.git" ]]; then
    info "Pulling latest code..."
    cd "$APP_DIR"
    sudo -u "$DEPLOY_USER" git pull --ff-only || warn "git pull failed; using existing code"
else
    info "Cloning repository..."
    git clone "$REPO_URL" "$APP_DIR"
    chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$APP_DIR"
fi
cd "$APP_DIR"

# ---------- Virtual environment ----------
info "=== Step 4/8: Python virtual environment ==="
if [[ ! -d "$APP_DIR/venv" ]]; then
    sudo -u "$DEPLOY_USER" "python${PYTHON_VERSION}" -m venv "$APP_DIR/venv"
    info "Created venv"
fi
sudo -u "$DEPLOY_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip -q
sudo -u "$DEPLOY_USER" "$APP_DIR/venv/bin/pip" install -r requirements.txt -q
info "Dependencies installed"

# ---------- Data & log directories ----------
info "=== Step 5/8: Directories ==="
for d in "$APP_DIR/data" "$APP_DIR/output" "$APP_DIR/output/blogs" "$APP_DIR/logs"; do
    sudo -u "$DEPLOY_USER" mkdir -p "$d"
done
for d in "${LOG_DIRS[@]}"; do
    mkdir -p "$d"
    chown "$DEPLOY_USER":"$DEPLOY_USER" "$d"
done

# ---------- .env check ----------
info "=== Step 6/8: Environment file ==="
if [[ ! -f "$APP_DIR/.env" ]]; then
    if [[ -f "$APP_DIR/.env.example" ]]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        chown "$DEPLOY_USER":"$DEPLOY_USER" "$APP_DIR/.env"
        chmod 600 "$APP_DIR/.env"
        warn ".env created from template — edit it now:  nano $APP_DIR/.env"
    else
        error "No .env file found. Create one before starting services."
    fi
else
    info ".env already exists"
fi
chmod 600 "$APP_DIR/.env"

# ---------- systemd services ----------
info "=== Step 7/8: systemd services ==="
cp "$APP_DIR/deploy/feishu-blog-bot.service"    /etc/systemd/system/
cp "$APP_DIR/deploy/feishu-sticker-bot.service"  /etc/systemd/system/
systemctl daemon-reload

systemctl enable feishu-blog-bot.service
systemctl enable feishu-sticker-bot.service

systemctl restart feishu-blog-bot.service
systemctl restart feishu-sticker-bot.service

info "=== Step 8/8: Log rotation ==="
cp "$APP_DIR/deploy/logrotate-feishu-bots" /etc/logrotate.d/feishu-bots
info "Logrotate configured (daily, keep 14 days)"

info ""
info "============================================="
info "  Deployment complete!"
info "============================================="
info ""
info "Services:"
info "  feishu-blog-bot     $(systemctl is-active feishu-blog-bot)"
info "  feishu-sticker-bot  $(systemctl is-active feishu-sticker-bot)"
info ""
info "Useful commands:"
info "  sudo systemctl status  feishu-blog-bot"
info "  sudo systemctl status  feishu-sticker-bot"
info "  sudo journalctl -u feishu-blog-bot -f"
info "  sudo journalctl -u feishu-sticker-bot -f"
info "  tail -f /var/log/feishu-blog-bot/stdout.log"
info "  tail -f /var/log/feishu-sticker-bot/stdout.log"
info ""
info "Config:  nano $APP_DIR/.env"
info "Restart: sudo systemctl restart feishu-blog-bot feishu-sticker-bot"
