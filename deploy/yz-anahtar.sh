#!/usr/bin/env bash
# Mevzuat asistanı için Anthropic API anahtarını sunucuya kaydeder. Anahtar ekranda görünmez ve hiçbir yere yazdırılmaz.
# Kullanım (Burak'ın bilgisayarında PowerShell): ssh -t root@91.124.63.132 "bash /opt/denetci/deploy/yz-anahtar.sh"
# İsteğe bağlı model: bash yz-anahtar.sh claude-opus-5-5   (varsayılan claude-haiku-5-5)
set -euo pipefail
read -rsp "Anthropic API anahtarını yapıştırıp Enter'a basın (görünmez): " KEY
echo
KEY="$(echo -n "$KEY" | tr -d '[:space:]')"
if [[ ! "$KEY" =~ ^sk-ant- ]]; then
  echo "Bu bir Anthropic API anahtarına benzemiyor (sk-ant- ile başlamalı). Kaydedilmedi." >&2
  exit 1
fi
umask 077
{
  echo "ANTHROPIC_API_KEY=$KEY"
  [ -n "${1:-}" ] && echo "DENETCI_YZ_MODEL=$1"
} > /etc/denetci-yz.env
chmod 600 /etc/denetci-yz.env
systemctl restart denetci
echo "Anahtar kaydedildi, Denetçi yeniden başlatıldı. Mevzuat Asistanı sayfasını yenileyin."
