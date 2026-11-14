#!/usr/bin/env bash
# Sunucuda Denetçi komutlarını doğru kullanıcı ve ayarlarla çalıştırır: sudo bash deploy/komut.sh yedek
set -euo pipefail
cd /opt/denetci
exec runuser -u denetci -- env $(grep -v '^#' /etc/denetci.env | xargs) HOME=/var/lib/denetci \
    /opt/denetci/.venv/bin/python -m denetci "$@"
