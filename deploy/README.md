# Deploy บน Ubuntu VM (HTTPS ผ่านโดเมนบริษัท)

```
ผู้ใช้ ──HTTPS──▶ nginx (:443) ──▶ uvicorn 127.0.0.1:8000 (systemd: allocation)
```

| ไฟล์ | ใช้ทำอะไร |
|------|-----------|
| `install.sh` | ติดตั้งครั้งแรก: แพ็กเกจ, venv, `config/.env`, systemd, nginx, firewall |
| `update.sh` | อัปเดตโค้ดหลัง push: `git pull` → pip (ถ้าจำเป็น) → restart → เช็ก `/health` |
| `allocation.service` | systemd unit (ต้นแบบ — `install.sh` เติม user/path ให้) |
| `nginx-allocation.conf` | reverse proxy HTTPS (ต้นแบบ — `install.sh` เติมโดเมนให้) |

## 0. ก่อนเริ่ม

- **SSH** — ไม่บังคับ (ทำจาก console ของ VM ได้) แต่สะดวกกว่ามาก เช็ก/เปิด:
  ```bash
  sudo systemctl status ssh            # active (running) = เปิดแล้ว
  sudo apt install -y openssh-server   # ถ้ายังไม่มี
  sudo systemctl enable --now ssh
  ```
- **ขอ IT**
  1. DNS: ชี้โดเมน (เช่น `alloc.company.com`) → IP ของ VM
  2. Cert HTTPS (ถ้าโดเมนใช้ได้แค่ภายใน — Let's Encrypt ใช้ไม่ได้)
  3. Entra → App registration ของ `AZURE_AUTH_CLIENT_ID` → Authentication → **Single-page application**
     → Redirect URI = `https://alloc.company.com/` (**ต้องมี `/` ท้าย** ไม่งั้นเจอ `AADSTS50011`)
  4. VM ออกเน็ตได้ถึง `login.microsoftonline.com`, `api.powerbi.com`,
     `onelake.dfs.fabric.microsoft.com`, server Target Sun (UAT/Prod), `github.com`

## 1. ติดตั้งครั้งแรก

```bash
git clone https://github.com/jintanabp/allocation_target.git ~/allocation_target
cd ~/allocation_target
bash deploy/install.sh alloc.company.com
```

รันด้วย user ปกติที่มี sudo (ไม่ใช่ root) — แอปจะรันเป็น user นั้น

## 2. กรอก `config/.env`

```bash
nano config/.env
sudo systemctl restart allocation
```

อย่างน้อย: `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID`, `FABRIC_DATASET_ID`, `FABRIC_CLIENT_SECRET`
(ใส่ secret = Service Principal ไม่ต้องเปิดเบราว์เซอร์ — บน VM ต้องใช้แบบนี้),
`AZURE_AUTH_CLIENT_ID`, `AZURE_AUTH_TENANT_ID`, `ALLOCATION_ADMIN_EMAILS`, ค่า Target Sun

**ย้ายมาจากเครื่องเดิม?** `config/.env` และ `data/` ไม่อยู่ใน git — copy มาเอง (หยุดแอปก่อน):

```bash
sudo systemctl stop allocation
scp -r <เครื่องเดิม>:/path/allocation_target/data ~/allocation_target/
scp <เครื่องเดิม>:/path/allocation_target/config/.env ~/allocation_target/config/
sudo systemctl start allocation
```

`data/` เก็บผลกระจายที่บันทึกไว้, baseline, กติกาที่แอดมินตั้ง, log — ไม่ copy = เริ่มใหม่ว่าง ๆ

## 3. Cert HTTPS

**จาก IT:**
```bash
sudo mkdir -p /etc/ssl/alloc
sudo cp fullchain.pem /etc/ssl/alloc/fullchain.pem    # cert + intermediate ต่อกันในไฟล์เดียว
sudo cp privkey.pem   /etc/ssl/alloc/privkey.pem
sudo chmod 600 /etc/ssl/alloc/privkey.pem
sudo nginx -t && sudo systemctl reload nginx
```

**Let's Encrypt** (เฉพาะโดเมนที่เข้าได้จากอินเทอร์เน็ต — ต่ออายุอัตโนมัติ):
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d alloc.company.com
```

## 4. ตรวจ

```bash
curl -s http://127.0.0.1:8000/health      # แอป
curl -I https://alloc.company.com/        # ผ่าน nginx
```

จากนั้นหลังล็อกอินครั้งแรก: หน้าแอดมิน → แหล่งข้อมูล → ตั้ง preset ปลายทาง Target Sun
แล้วไล่ [`docs/DEPLOY_QA_CHECKLIST.md`](../docs/DEPLOY_QA_CHECKLIST.md) ก่อนเปิดให้ผู้ใช้

## 5. อัปเดตโค้ด

```bash
cd ~/allocation_target && bash deploy/update.sh
```

## ดู log / แก้ปัญหา

```bash
sudo journalctl -u allocation -f             # stdout ของแอป
tail -f ~/allocation_target/data/app.log     # log ของแอป
sudo tail -f /var/log/nginx/error.log        # nginx
```

| อาการ | สาเหตุที่พบบ่อย |
|-------|-----------------|
| 502 Bad Gateway | แอปไม่รัน — `sudo systemctl status allocation` / journalctl |
| 504 ตอนคำนวณ / ส่ง Target Sun | งานนานเกิน `proxy_read_timeout` (600s) ใน nginx |
| 413 ตอนอัปโหลด Excel | ไฟล์เกิน `client_max_body_size` (50m) |
| ล็อกอินแล้ว `AADSTS50011` | Redirect URI ใน Entra ไม่ตรง `https://<โดเมน>/` |

> **ห้ามเพิ่ม `--workers`** ใน `allocation.service` — ระบบล็อกไฟล์ใน `data/` ออกแบบไว้สำหรับโปรเซสเดียว
> (ดู [`docs/CONCURRENCY.md`](../docs/CONCURRENCY.md))
