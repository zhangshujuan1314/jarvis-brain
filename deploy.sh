#!/bin/bash
# Jarvis Brain — VPS deployment script
# Run on target VPS (Ubuntu 22.04+):
#   curl -sSL https://raw.githubusercontent.com/zhangshujuan1314/jarvis-brain/master/deploy.sh | bash
set -e

APP_DIR="/opt/jarvis-brain"
REPO="https://github.com/zhangshujuan1314/jarvis-brain.git"

echo "=== Jarvis Brain Deployment ==="

# 1. System deps
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx certbot python3-certbot-nginx libportaudio2 curl

# 2. Clone repo
echo "[2/6] Cloning repository..."
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR" && git pull
else
    git clone "$REPO" "$APP_DIR"
    cd "$APP_DIR"
fi

# 3. Python venv + deps
echo "[3/6] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt

# 4. Download STT models
echo "[4/6] Downloading STT models..."
python download_models.py

# 5. Configure .env
echo "[5/6] Configuring environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ">>> EDIT /opt/jarvis-brain/.env with your API keys <<<"
    echo ">>> Then re-run this script <<<"
    exit 1
fi

# 6. Install systemd service
echo "[6/6] Installing systemd service..."
cp jarvis-brain.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable jarvis-brain
systemctl restart jarvis-brain

echo ""
echo "=== Deployment complete ==="
echo "Service: systemctl status jarvis-brain"
echo "Logs:    journalctl -u jarvis-brain -f"
echo ""
echo "Next steps:"
echo "  1. Point DNS (jarvis.yourdomain.com) to this VPS IP"
echo "  2. Run: certbot --nginx -d jarvis.yourdomain.com"
echo "  3. Copy nginx.conf to /etc/nginx/sites-available/jarvis-brain"
echo "  4. Run: ln -s /etc/nginx/sites-available/jarvis-brain /etc/nginx/sites-enabled/"
echo "  5. Run: nginx -t && systemctl reload nginx"
echo "  6. Test: python test_client.py (set JARVIS_URI=wss://jarvis.yourdomain.com/ws)"
