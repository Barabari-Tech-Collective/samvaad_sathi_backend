#!/usr/bin/env bash
set -euo pipefail

# EC2/systemd alternative to docker-compose. Run once as root, then configure:
# BULK_ATS_OCR_ENABLED=True
# BULK_ATS_OCR_LANGUAGE=eng+hin
# BULK_ATS_CLAMAV_HOST=127.0.0.1
# BULK_ATS_CLAMAV_PORT=3310

apt-get update
packages=(tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin)
install_local_clamav="${INSTALL_LOCAL_CLAMAV:-true}"
if [ "${install_local_clamav}" = "true" ]; then
  memory_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  if [ "${memory_kb}" -lt 3500000 ]; then
    echo "At least 4 GB RAM is required for API + worker + Redis + local ClamAV. Set INSTALL_LOCAL_CLAMAV=false and use a remote ClamAV service on smaller hosts." >&2
    exit 1
  fi
  packages+=(clamav clamav-daemon)
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"

if [ "${install_local_clamav}" != "true" ]; then
  tesseract --version | head -1
  echo "Tesseract installed. Configure BULK_ATS_CLAMAV_HOST for the remote scanner."
  exit 0
fi

sed -i 's/^LocalSocket /#LocalSocket /' /etc/clamav/clamd.conf
if grep -q '^TCPSocket ' /etc/clamav/clamd.conf; then
  sed -i 's/^TCPSocket .*/TCPSocket 3310/' /etc/clamav/clamd.conf
else
  printf '\nTCPSocket 3310\n' >> /etc/clamav/clamd.conf
fi
if grep -q '^TCPAddr ' /etc/clamav/clamd.conf; then
  sed -i 's/^TCPAddr .*/TCPAddr 127.0.0.1/' /etc/clamav/clamd.conf
else
  printf 'TCPAddr 127.0.0.1\n' >> /etc/clamav/clamd.conf
fi

systemctl enable clamav-freshclam clamav-daemon
systemctl restart clamav-freshclam
systemctl restart clamav-daemon

tesseract --version | head -1
clamdscan --ping 10
