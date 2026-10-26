#!/usr/bin/env bash
# อัปเดตโค้ดบน VM หลัง push ขึ้น GitHub:
#   bash deploy/update.sh
#
# git pull → pip install (เฉพาะเมื่อ requirements.txt เปลี่ยน) → restart → เช็ก /health
# config/.env และ data/ ไม่อยู่ใน git — ไม่หายตอน pull
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "มีไฟล์ใน git ถูกแก้บนเครื่องนี้ — ตรวจด้วย git status ก่อน (ไม่ pull ให้)" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

BEFORE="$(git rev-parse HEAD)"
git pull --ff-only
AFTER="$(git rev-parse HEAD)"

if [[ "$BEFORE" == "$AFTER" ]]; then
  echo "==> โค้ดล่าสุดอยู่แล้ว ($(git rev-parse --short HEAD))"
else
  echo "==> อัปเดต $(git rev-parse --short "$BEFORE") → $(git rev-parse --short "$AFTER")"
  if ! git diff --quiet "$BEFORE" "$AFTER" -- requirements.txt; then
    echo "==> requirements.txt เปลี่ยน — ติดตั้ง dependency"
    .venv/bin/pip install -r requirements.txt
  fi
fi

sudo systemctl restart allocation

echo "==> รอแอปขึ้น"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "==> แอปทำงานแล้ว ($(git rev-parse --short HEAD))"
    exit 0
  fi
  sleep 2
done

echo "แอปไม่ตอบ /health ภายใน 60 วินาที — ดู log: sudo journalctl -u allocation -n 100" >&2
exit 1
