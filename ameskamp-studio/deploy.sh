#!/usr/bin/env bash
#
# Ameskamp Studio production deploy.
#
# Syncs the umbrella repo to the VPS, builds + starts the tool containers
# via the root docker-compose.yml, installs nginx server blocks for each
# subdomain, issues Let's Encrypt certificates as needed, and reloads
# nginx.
#
# Usage:
#   ./ameskamp-studio/deploy.sh                    # default server
#   ./ameskamp-studio/deploy.sh root@217.154.2.230 # explicit
#
# Idempotent — safe to re-run after pulling new commits.

set -euo pipefail

SERVER="${1:-root@217.154.2.230}"
PROJECT_DIR="/opt/ameskamp-audio-tools"
APEX="ameskamp.studio"
ADMIN_EMAIL="admin@zuacaldeira.com"

# Subdomains to deploy + the tool each one fronts.
# Update this list when new tools come online.
SUBDOMAINS=(
    "ameskamp.studio"
    "metadata.ameskamp.studio"
    "match.ameskamp.studio"
    "silence.ameskamp.studio"
)

echo "=== Ameskamp Studio deploy ==="
echo "Target:  $SERVER:$PROJECT_DIR"
echo "Domains: ${SUBDOMAINS[*]}"
echo

# ---------------------------------------------------------------------------
# 1. Sync project files
# ---------------------------------------------------------------------------
echo "[1/5] Syncing project files..."

ssh "$SERVER" "mkdir -p $PROJECT_DIR"

rsync -avz --delete \
    --exclude=".git" \
    --exclude=".venv" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".pytest_cache" \
    --exclude=".DS_Store" \
    --exclude="node_modules" \
    --exclude="test-audio" \
    ./ "$SERVER:$PROJECT_DIR/"

# ---------------------------------------------------------------------------
# 2. Containers: build + start via umbrella compose
# ---------------------------------------------------------------------------
echo
echo "[2/5] Building and starting containers..."

ssh "$SERVER" bash -s -- "$PROJECT_DIR" <<'CONTAINERS'
set -euo pipefail
PROJECT_DIR="$1"

# Remove containers from the legacy silence-trimmer-only compose if present;
# the new umbrella compose recreates them under different names.
docker rm -f silence-trimmer-app 2>/dev/null || true

cd "$PROJECT_DIR"

# .env on the host carries ACOUSTID_API_KEY and any other secrets. Compose
# auto-loads it, but warn loudly if it's missing.
if [ ! -f .env ]; then
    echo "  WARNING: $PROJECT_DIR/.env is missing — match.ameskamp.studio will return 503 until ACOUSTID_API_KEY is set."
fi

docker compose pull 2>/dev/null || true
docker compose up -d --build
CONTAINERS

# ---------------------------------------------------------------------------
# 3. Nginx snippets + per-subdomain server blocks
# ---------------------------------------------------------------------------
echo
echo "[3/5] Installing nginx configs..."

ssh "$SERVER" bash -s -- "$PROJECT_DIR" "${SUBDOMAINS[*]}" <<'NGINX_SETUP'
set -euo pipefail
PROJECT_DIR="$1"; shift
SUBDOMAINS=($@)

if ! command -v nginx >/dev/null; then
    apt-get update && apt-get install -y nginx
fi

# Shared snippets — prefixed so they don't collide with other apps' snippets.
mkdir -p /etc/nginx/snippets
cp "$PROJECT_DIR/ameskamp-studio/nginx/snippets/ssl-params.conf"   /etc/nginx/snippets/ameskamp-ssl-params.conf
cp "$PROJECT_DIR/ameskamp-studio/nginx/snippets/proxy-params.conf" /etc/nginx/snippets/ameskamp-proxy-params.conf

# Webroot for ACME http-01 challenges.
mkdir -p /var/www/certbot

# For each subdomain: if no cert exists, install a minimal HTTP-only stub so
# certbot can complete the http-01 challenge against /.well-known/acme-challenge/.
# After certs exist, the real conf (with SSL) gets installed and certbot can
# renew through the same webroot.
for d in "${SUBDOMAINS[@]}"; do
    if [ ! -d "/etc/letsencrypt/live/$d" ]; then
        echo "  $d: no cert yet — installing HTTP-only bootstrap"
        cat > "/etc/nginx/sites-available/$d" <<HTTP_STUB
server {
    listen 80;
    listen [::]:80;
    server_name $d;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 "$d: awaiting SSL\n"; add_header Content-Type text/plain; }
}
HTTP_STUB
    else
        echo "  $d: cert present — installing full HTTPS config"
        cp "$PROJECT_DIR/ameskamp-studio/nginx/conf.d/$d.conf" "/etc/nginx/sites-available/$d"
    fi
    ln -sf "/etc/nginx/sites-available/$d" "/etc/nginx/sites-enabled/$d"
done

nginx -t
systemctl reload nginx
NGINX_SETUP

# ---------------------------------------------------------------------------
# 4. Issue/renew Let's Encrypt certs for any missing subdomains
# ---------------------------------------------------------------------------
echo
echo "[4/5] Issuing/renewing SSL certificates..."

ssh "$SERVER" bash -s -- "$PROJECT_DIR" "$ADMIN_EMAIL" "${SUBDOMAINS[*]}" <<'CERTBOT_SETUP'
set -euo pipefail
PROJECT_DIR="$1"; ADMIN_EMAIL="$2"; shift 2
SUBDOMAINS=($@)

if ! command -v certbot >/dev/null; then
    apt-get update && apt-get install -y certbot python3-certbot-nginx
fi

issued_any=0
for d in "${SUBDOMAINS[@]}"; do
    if [ -d "/etc/letsencrypt/live/$d" ]; then
        echo "  $d: cert exists, skipping issuance"
        continue
    fi
    echo "  $d: issuing cert via http-01..."
    certbot certonly --webroot -w /var/www/certbot \
        -d "$d" \
        --non-interactive --agree-tos --email "$ADMIN_EMAIL" \
        --no-eff-email
    cp "$PROJECT_DIR/ameskamp-studio/nginx/conf.d/$d.conf" "/etc/nginx/sites-available/$d"
    issued_any=1
done

if [ "$issued_any" -eq 1 ]; then
    nginx -t
    systemctl reload nginx
fi
CERTBOT_SETUP

# ---------------------------------------------------------------------------
# 5. Health check each subdomain
# ---------------------------------------------------------------------------
echo
echo "[5/5] Health check..."
sleep 5

ssh "$SERVER" "cd $PROJECT_DIR && docker compose ps"
echo

failed=0
for d in "${SUBDOMAINS[@]}"; do
    code=$(ssh "$SERVER" "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://$d/" || echo "000")
    if [ "$code" = "200" ] || [ "$code" = "301" ] || [ "$code" = "302" ]; then
        echo "  $d -> HTTP $code  OK"
    else
        echo "  $d -> HTTP $code  FAIL"
        failed=1
    fi
done

echo
if [ "$failed" -eq 0 ]; then
    echo "=== Deploy successful ==="
    echo "Open https://$APEX"
else
    echo "=== Deploy completed with health-check failures ==="
    echo "Inspect: ssh $SERVER 'cd $PROJECT_DIR && docker compose logs --tail=100'"
    exit 1
fi
