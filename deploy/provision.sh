#!/usr/bin/env bash
# One-shot preparation of the helio VM for Clima Espacial USACH (D-040).
# Idempotent: safe to run again. Does NOT touch the existing `helio` nginx
# vhost or the group site — see the printed instructions at the end for the
# one manual edit that does (adding the include line).
#
# Run on the server as root:
#   sudo bash provision.sh
set -euo pipefail

ARCHIVO_ENV=/etc/swx/swx.env
GOATCOUNTER_VERSION=v2.7.0
GOATCOUNTER_URL="https://github.com/arp242/goatcounter/releases/download/${GOATCOUNTER_VERSION}/goatcounter-${GOATCOUNTER_VERSION}-linux-amd64.gz"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

echo "==> 1/7 Swapfile (D-040: VM ships with none; the flare-nrt producer needs headroom for CPU inference)"
if ! swapon --show | grep -q .; then
    fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
    echo "   swap already active, left as is"
fi

echo "==> 2/7 Packages (nginx is already installed and configured — left alone)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql python3-venv rsync curl

echo "==> 3/7 swx user: system account, nologin, no sudo"
id swx >/dev/null 2>&1 || useradd --system --shell /usr/sbin/nologin --home-dir /opt/swx swx

echo "==> 4/7 Directories"
install -d -o root -g root -m 755 /opt/swx /var/www/clima-espacial/static
install -d -o root -g swx -m 750 /etc/swx
install -d -o root -g swx -m 750 /srv/swx/data
install -d -o root -g root -m 700 /srv/swx/backups
install -d -o swx -g swx -m 750 /srv/swx/goatcounter

echo "==> 5/7 PostgreSQL: local socket only, role and database"
PG_MAIN="$(ls -d /etc/postgresql/*/main 2>/dev/null | head -1)"
[[ -n $PG_MAIN ]] || { echo "PostgreSQL cluster not found." >&2; exit 1; }
install -d "$PG_MAIN/conf.d"
echo "listen_addresses = ''" > "$PG_MAIN/conf.d/swx.conf"
systemctl restart postgresql
for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done
pg_isready
sudo -u postgres createuser swx 2>/dev/null || echo "   role swx already exists"
sudo -u postgres createdb -O swx swx 2>/dev/null || echo "   database swx already exists"

echo "==> 6/7 Settings file (created only if missing — a later re-run never regenerates SECRET_KEY)"
if [[ ! -f $ARCHIVO_ENV ]]; then
    cat > "$ARCHIVO_ENV" <<FIN
DJANGO_SETTINGS_MODULE=climaespacial.settings.prod
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
DATABASE_URL=postgresql:///swx?host=/var/run/postgresql
ALLOWED_HOSTS=helio.fisica.usach.cl
CSRF_TRUSTED_ORIGINS=https://helio.fisica.usach.cl
SWX_AMBIENTE=produccion
SWX_HTTPS=1
SWX_SCRIPT_NAME=/clima-espacial
STATIC_ROOT=/var/www/clima-espacial/static
SWX_DATA_DIR=/srv/swx/data
GOATCOUNTER_SITE=
FIN
    chown root:swx "$ARCHIVO_ENV"
    chmod 640 "$ARCHIVO_ENV"
    echo "   created with a fresh SECRET_KEY"
else
    echo "   $ARCHIVO_ENV already present, left as is"
fi

echo "==> 7/7 GoatCounter binary (D-043)"
if [[ ! -x /opt/goatcounter/goatcounter ]]; then
    install -d -o root -g root -m 755 /opt/goatcounter
    curl -sL "$GOATCOUNTER_URL" | gunzip > /opt/goatcounter/goatcounter
    chmod 755 /opt/goatcounter/goatcounter
else
    echo "   already installed, left as is"
fi
install -o root -g root -m 644 "$(dirname "${BASH_SOURCE[0]}")/systemd/goatcounter.service" /etc/systemd/system/
systemctl daemon-reload

echo
echo "===================== MANUAL STEPS ====================="
echo "1. nginx (only remaining manual step — this script never touches the"
echo "   live group-site vhost): install the snippet, then add ONE line"
echo "   inside the existing TLS server{} block for helio.fisica.usach.cl:"
echo
echo "     install -o root -g root -m 644 $(dirname "${BASH_SOURCE[0]}")/nginx-clima-espacial.conf /etc/nginx/snippets/clima-espacial.conf"
echo "     # then edit /etc/nginx/sites-available/helio and add:"
echo "     #     include snippets/clima-espacial.conf;"
echo "     nginx -t && systemctl reload nginx"
echo
echo "2. First deploy: bash deploy.sh <tag>  (e.g. v0.1.0)"
echo
echo "3. GoatCounter: create the first site/user, then start the service:"
echo "     runuser -u swx -- /opt/goatcounter/goatcounter create -domain localhost:8081 -email you@usach.cl"
echo "     systemctl enable --now goatcounter"
echo "   then set GOATCOUNTER_SITE in $ARCHIVO_ENV and restart swx-web."
echo "=========================================================="
