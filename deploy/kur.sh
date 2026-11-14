#!/usr/bin/env bash
# Denetçi'yi Ubuntu 24.04 sunucuya kurar ya da günceller. Depo klasörünün içinden root olarak çalıştırılır:
#     sudo bash deploy/kur.sh denetci.ornek.com
# Tekrar çalıştırmak güvenlidir: kodu yeniler, veriye dokunmaz.
set -euo pipefail

ALAN="${1:?Kullanım: sudo bash deploy/kur.sh <alan-adi>}"
KAYNAK="$(cd "$(dirname "$0")/.." && pwd)"
UYG=/opt/denetci
VERI=/var/lib/denetci

apt-get update -q
apt-get install -y -q python3-venv caddy ufw rsync

id denetci >/dev/null 2>&1 || useradd --system --home "$VERI" --shell /usr/sbin/nologin denetci
mkdir -p "$UYG" "$VERI/veri" "$VERI/buro" "$VERI/yedek"

rsync -a --delete --exclude .git --exclude __pycache__ --exclude tests --exclude .venv "$KAYNAK"/ "$UYG"/
[ -d "$UYG/.venv" ] || python3 -m venv "$UYG/.venv"
"$UYG/.venv/bin/pip" install -q --upgrade pip
"$UYG/.venv/bin/pip" install -q -r "$UYG/requirements.txt"

cat > /etc/denetci.env <<ENV
DENETCI_SUNUCU=1
DENETCI_VERI=$VERI/veri
DENETCI_BURO=$VERI/buro
DENETCI_YEDEK=$VERI/yedek
ENV

chown -R denetci:denetci "$VERI"
chmod 700 "$VERI"
# Başka bilgisayardan (ör. Windows tar ile) gelen dosyalar herkese açık izinlerle gelebilir; daralt.
find "$VERI" -mindepth 1 -type d -exec chmod 750 {} +
find "$VERI" -mindepth 1 -type f -exec chmod 640 {} +
[ -f "$VERI/veri/.oturum_anahtari" ] && chmod 600 "$VERI/veri/.oturum_anahtari"
# Başka bilgisayardan taşınan veride dosya yollarını düzelt (veri yoksa bir şey yapmaz).
bash "$UYG/deploy/komut.sh" yollari-duzelt

cat > /etc/systemd/system/denetci.service <<UNIT
[Unit]
Description=Denetci
After=network.target

[Service]
User=denetci
WorkingDirectory=$UYG
EnvironmentFile=/etc/denetci.env
# Mevzuat asistanının API anahtarı (deploy/yz-anahtar.sh yazar; kurulum bu dosyaya dokunmaz)
EnvironmentFile=-/etc/denetci-yz.env
ExecStart=$UYG/.venv/bin/python -m denetci calistir --port 8000
Restart=always
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$VERI
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/denetci-yedek.service <<UNIT
[Unit]
Description=Denetci gece yedegi

[Service]
Type=oneshot
User=denetci
WorkingDirectory=$UYG
EnvironmentFile=/etc/denetci.env
ExecStart=$UYG/.venv/bin/python -m denetci yedek
UNIT

cat > /etc/systemd/system/denetci-yedek.timer <<UNIT
[Unit]
Description=Denetci gece yedegi (her gece 03:00)

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# https sertifikasını Caddy kendisi alır ve yeniler.
cat > /etc/caddy/Caddyfile <<CADDY
$ALAN {
    encode gzip
    request_body {
        max_size 25MB
    }
    reverse_proxy 127.0.0.1:8000
}
CADDY

ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

timedatectl set-timezone Europe/Istanbul
systemctl daemon-reload
systemctl enable -q denetci denetci-yedek.timer
systemctl restart denetci
systemctl start denetci-yedek.timer
systemctl reload caddy || systemctl restart caddy

echo
echo "Kurulum tamam: https://$ALAN"
echo "Kullanıcı eklemek için:      sudo bash $UYG/deploy/komut.sh kullanici-ekle <ad>"
echo "Telefon kodu anahtarı için:  sudo bash $UYG/deploy/komut.sh telefon-kodu <ad>"
