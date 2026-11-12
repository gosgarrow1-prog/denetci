#!/usr/bin/env bash
# Sunucuda Denetçi komutlarını doğru kullanıcı ve ayarlarla çalıştırır: sudo bash deploy/komut.sh yedek
set -euo pipefail
cd /opt/denetci
set -a; . /etc/denetci.env; set +a
exec runuser -u denetci --preserve-environment -- /opt/denetci/.venv/bin/python -m denetci "$@"
