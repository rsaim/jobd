#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu VM (tested against Oracle's always-free
# A1.Flex shape, Ubuntu 24.04 aarch64). Idempotent — safe to re-run.
#
#   demo/bootstrap.sh                    # serve on <public-ip>.sslip.io
#   demo/bootstrap.sh demo.example.com   # serve on your own hostname
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Docker with the compose plugin, if missing. A fresh install needs the
#    docker group to take effect in a new login shell — the script says so
#    and exits rather than limping along under sudo.
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
if ! docker info >/dev/null 2>&1; then
  sudo usermod -aG docker "$USER"
  echo "Docker is installed but this shell cannot use it yet." >&2
  echo "Log out, back in, and re-run demo/bootstrap.sh." >&2
  exit 1
fi

# 2. Oracle's Ubuntu images ship iptables rules that reject everything but
#    SSH — opening 80/443 in the cloud security list is not enough, the OS
#    firewall must open them too.
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null ||
    sudo iptables -I INPUT -p tcp --dport "$port" -j ACCEPT
done
sudo netfilter-persistent save 2>/dev/null || true

# 3. The hostname Caddy serves. sslip.io resolves <ip>.sslip.io to that ip,
#    so a bare VM gets a real certificate with no DNS setup at all.
domain="${1:-}"
if [[ -z "$domain" ]]; then
  ip="$(curl -fsS https://api.ipify.org)"
  domain="${ip//./-}.sslip.io"
fi
if grep -q '^DEMO_DOMAIN=' .env 2>/dev/null; then
  sed -i "s/^DEMO_DOMAIN=.*/DEMO_DOMAIN=$domain/" .env
else
  echo "DEMO_DOMAIN=$domain" >> .env
fi

# 4. Cert storage the nightly `down -v` cannot delete.
docker volume create jobd_demo_caddy_data >/dev/null
docker volume create jobd_demo_caddy_config >/dev/null

# 5. Stamp the host. demo-restart.sh wipes every volume and refuses to run
#    on a machine without this marker — i.e. a developer laptop.
echo "stamped by demo/bootstrap.sh — demo-restart.sh refuses to run without this file" |
  sudo tee /etc/jobd-demo-host >/dev/null

# 6. Build, migrate, seed.
demo/demo-restart.sh --build

echo
echo "The demo is up:  https://$domain   (user demo, password demo)"
echo "Nightly reset:   crontab demo/cron   (edit the path first if the"
echo "                 repo is not at ~/jobd)"
