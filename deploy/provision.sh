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
FIN
    chown root:swx "$ARCHIVO_ENV"
    chmod 640 "$ARCHIVO_ENV"
    echo "   created with a fresh SECRET_KEY"
else
    echo "   $ARCHIVO_ENV already present, left as is"
fi

echo "==> 7/7 GoatCounter binary (D-043/D-051 — no browser JS: imported from nginx's own access log, never a hosted goatcounter.com script tag)"
if [[ ! -x /opt/goatcounter/goatcounter ]]; then
    install -d -o root -g root -m 755 /opt/goatcounter
    curl -sL "$GOATCOUNTER_URL" | gunzip > /opt/goatcounter/goatcounter
    chmod 755 /opt/goatcounter/goatcounter
else
    echo "   already installed, left as is"
fi
# D-053/12-HANDOFF item 5: GoatCounter looks sites up by Host header, so
# both the import service (on this machine) and the admin dashboard (over
# the SSH tunnel, from Victor's own PC) need this name to resolve to the
# instance's own loopback address — confirmed required, not optional,
# 11-MANUAL-STEPS.md Part 1.
grep -q 'stats.clima-espacial.internal' /etc/hosts || echo '127.0.0.1 stats.clima-espacial.internal' >> /etc/hosts
# adm: the group that owns nginx's logs on Debian/Ubuntu — the log-import
# service (below) reads /var/log/nginx/access.log as this user, never root.
usermod -aG adm swx
SYSTEMD_SRC="$(dirname "${BASH_SOURCE[0]}")/systemd"
install -o root -g root -m 644 \
    "$SYSTEMD_SRC/goatcounter.service" \
    "$SYSTEMD_SRC/goatcounter-import.service" \
    /etc/systemd/system/
systemctl daemon-reload

cat <<'INSTRUCTIONS'

===================== MANUAL STEPS =====================
1. nginx (only remaining manual step -- this script never touches the
   live group-site vhost): install the snippet, then add ONE line inside
   the existing TLS server{} block for helio.fisica.usach.cl:

     install -o root -g root -m 644 deploy/nginx-clima-espacial.conf \
         /etc/nginx/snippets/clima-espacial.conf
     # then edit /etc/nginx/sites-available/helio and add:
     #     include snippets/clima-espacial.conf;
     nginx -t && systemctl reload nginx

2. First deploy: bash deploy.sh <tag>  (e.g. v0.1.0)

3. GoatCounter (D-051, corrected by D-053/12-HANDOFF item 5 -- running this
   for real found GoatCounter looks sites up by Host header, so -vhost DOES
   need to resolve, even for a single-site self-hosted instance reached
   only over an SSH tunnel. This script already added
   "127.0.0.1 stats.clima-espacial.internal" to /etc/hosts for you):

     runuser -u swx -- /opt/goatcounter/goatcounter db create site \
         -db sqlite+/srv/swx/goatcounter/db.sqlite3 -createdb \
         -vhost stats.clima-espacial.internal -user.email you@usach.cl
     systemctl enable --now goatcounter

   Then an API key for the log-import service. "-user 1"/"-user <email>"
   on "db create apitoken" was unreliable against the real v2.7.0 CLI
   (prints nothing, or "site.ByHost ... no rows" -- confirmed
   11-MANUAL-STEPS.md Part 1); check the database directly first:

     runuser -u swx -- /opt/goatcounter/goatcounter db create apitoken \
         -db sqlite+/srv/swx/goatcounter/db.sqlite3 -user 1 -name import -perm count
     # if that prints no token: open the tunnel below, log in, click your
     # email (top right) -> API -> New API token, name "import", tick
     # "Record pageviews", save, copy it.

   Put the token into /etc/swx/goatcounter-import.env as:
     GOATCOUNTER_API_KEY=<token>
   Then:
     chown root:swx /etc/swx/goatcounter-import.env
     chmod 640 /etc/swx/goatcounter-import.env
     systemctl enable --now goatcounter-import

   To see the dashboard from your own PC: open a tunnel
   (ssh -L 8081:localhost:8081 helio), add
   "127.0.0.1 stats.clima-espacial.internal" to YOUR OWN PC's hosts file
   too, then browse http://stats.clima-espacial.internal:8081 -- not
   localhost:8081, same Host-header lookup as above.
==========================================================
INSTRUCTIONS
