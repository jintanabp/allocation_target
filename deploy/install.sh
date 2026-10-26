#!/usr/bin/env bash
# ติดตั้งครั้งแรกบน Ubuntu VM — รันจากในโฟลเดอร์ repo ที่ clone มาแล้ว:
#   bash deploy/install.sh alloc.company.com
#
# ทำ: ลงแพ็กเกจ → venv + pip → สร้าง config/.env (ถ้ายังไม่มี) → systemd → nginx → ufw
# รันซ้ำได้ (ไม่ทับ config/.env ที่มีอยู่)
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "ใช้: bash deploy/install.sh <โดเมน เช่น alloc.company.com>" >&2
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
if [[ "$APP_USER" == "root" ]]; then
  echo "อย่ารันด้วย root — รันด้วย user ปกติที่มี sudo (แอปจะรันเป็น user นั้น)" >&2
  exit 1
fi

echo "==> โฟลเดอร์แอป: $APP_DIR · user: $APP_USER · โดเมน: $DOMAIN"

echo "==> ติดตั้งแพ็กเกจระบบ"
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip nginx

PY_OK="$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')"
if [[ "$PY_OK" != "1" ]]; then
  echo "ต้องใช้ Python 3.11 ขึ้นไป (เครื่องนี้: $(python3 --version))" >&2
  exit 1
fi

echo "==> สร้าง venv + ติดตั้ง dependency"
cd "$APP_DIR"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [[ ! -f config/.env ]]; then
  cp config/.env.example config/.env
  chmod 600 config/.env
  echo "==> สร้าง config/.env จากตัวอย่างแล้ว — ต้องกรอกค่าจริงก่อนใช้งาน"
else
  echo "==> มี config/.env อยู่แล้ว — ไม่แตะ"
fi
mkdir -p data

echo "==> ติดตั้ง systemd service"
sed -e "s#__APP_USER__#${APP_USER}#g" -e "s#__APP_DIR__#${APP_DIR}#g" \
  deploy/allocation.service | sudo tee /etc/systemd/system/allocation.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable allocation
sudo systemctl restart allocation

echo "==> ตั้งค่า nginx"
sed -e "s#__DOMAIN__#${DOMAIN}#g" deploy/nginx-allocation.conf \
  | sudo tee /etc/nginx/sites-available/allocation >/dev/null
sudo ln -sf /etc/nginx/sites-available/allocation /etc/nginx/sites-enabled/allocation
sudo rm -f /etc/nginx/sites-enabled/default

# ยังไม่มี cert → ใส่ self-signed ชั่วคราวให้ nginx ขึ้นได้ (certbot --nginx ต้องให้ nginx -t ผ่านก่อน)
# พอได้ cert จริงจาก IT ก็วางทับสองไฟล์นี้ · หรือ certbot เปลี่ยน path ให้เอง
NGINX_STATE="พร้อม (ใช้ cert ที่มีอยู่)"
if [[ ! -f /etc/ssl/alloc/fullchain.pem || ! -f /etc/ssl/alloc/privkey.pem ]]; then
  sudo mkdir -p /etc/ssl/alloc
  sudo openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
    -subj "/CN=${DOMAIN}" \
    -keyout /etc/ssl/alloc/privkey.pem -out /etc/ssl/alloc/fullchain.pem
  sudo chmod 600 /etc/ssl/alloc/privkey.pem
  NGINX_STATE="ใช้ cert self-signed ชั่วคราว (เบราว์เซอร์จะเตือน) — ต้องเปลี่ยนเป็น cert จริง"
fi
sudo nginx -t
sudo systemctl reload nginx

if command -v ufw >/dev/null; then
  echo "==> เปิด firewall (SSH + HTTP/HTTPS)"
  sudo ufw allow OpenSSH
  sudo ufw allow 'Nginx Full'
  sudo ufw --force enable
fi

cat <<EOF

ติดตั้งเสร็จ · nginx: $NGINX_STATE

ที่เหลือ:
  1. กรอก $APP_DIR/config/.env แล้ว: sudo systemctl restart allocation
  2. Cert HTTPS (เลือกอย่างใดอย่างหนึ่ง):
       - จาก IT: วาง fullchain.pem + privkey.pem ที่ /etc/ssl/alloc/ แล้ว
                 sudo nginx -t && sudo systemctl reload nginx
       - Let's Encrypt: sudo apt-get install -y certbot python3-certbot-nginx
                        sudo certbot --nginx -d $DOMAIN
  3. Entra: เพิ่ม Redirect URI (SPA) = https://$DOMAIN/   (มี / ท้าย)
  4. ตรวจ: curl -s http://127.0.0.1:8000/health
EOF
