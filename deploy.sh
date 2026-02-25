#!/bin/bash
set -euo pipefail

# AmeskampAudioTools Production Deploy Script
# Usage: ./deploy.sh [user@host]
# Example: ./deploy.sh root@217.154.2.230

SERVER="${1:-root@217.154.2.230}"
PROJECT_DIR="/opt/ameskamp-audio-tools"
COMPOSE_FILE="docker-compose.prod.yml"
DOMAIN="ameskamp.zuacaldeira.com"

echo "=== AmeskampAudioTools Production Deploy ==="
echo "Target: $SERVER:$PROJECT_DIR"
echo ""

# 1. Copy project files to server
echo "[1/5] Syncing project files..."
ssh "$SERVER" "mkdir -p $PROJECT_DIR/web/static $PROJECT_DIR/nginx"
scp docker-compose.prod.yml "$SERVER:$PROJECT_DIR/$COMPOSE_FILE"
scp Dockerfile "$SERVER:$PROJECT_DIR/Dockerfile"
scp requirements.txt "$SERVER:$PROJECT_DIR/requirements.txt"
scp web/__init__.py "$SERVER:$PROJECT_DIR/web/__init__.py"
scp web/silence_trimmer_web.py "$SERVER:$PROJECT_DIR/web/silence_trimmer_web.py"
scp web/static/index.html "$SERVER:$PROJECT_DIR/web/static/index.html"
scp "nginx/$DOMAIN.conf" "$SERVER:$PROJECT_DIR/nginx/$DOMAIN.conf"

# 2. Build and start containers (remove stale container if it exists from a previous compose project)
echo "[2/5] Building and starting containers..."
ssh "$SERVER" "docker rm -f silence-trimmer-app 2>/dev/null || true"
ssh "$SERVER" "cd $PROJECT_DIR && docker compose -f $COMPOSE_FILE up -d --build"

# 3. Configure Nginx reverse proxy and SSL (idempotent)
echo "[3/5] Configuring Nginx reverse proxy..."
ssh "$SERVER" bash -s <<'NGINX_SETUP'
set -euo pipefail
DOMAIN="ameskamp.zuacaldeira.com"

if ! command -v nginx &> /dev/null; then
    apt-get update && apt-get install -y nginx
fi
mkdir -p /var/www/certbot

# If SSL certs don't exist yet, install HTTP-only config first for certbot
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "No SSL cert found — installing HTTP-only config for certbot..."
    cat > /etc/nginx/sites-available/$DOMAIN <<'HTTP_CONF'
server {
    listen 80;
    server_name ameskamp.zuacaldeira.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { proxy_pass http://127.0.0.1:5000; }
}
HTTP_CONF
    ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
    nginx -t && systemctl reload nginx
else
    echo "SSL cert exists — installing full config..."
    cp /opt/ameskamp-audio-tools/nginx/$DOMAIN.conf /etc/nginx/sites-available/$DOMAIN
    ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
    nginx -t && systemctl reload nginx
fi
NGINX_SETUP

# 4. SSL certificate via certbot (if not already present)
echo "[4/5] Checking SSL certificate..."
ssh "$SERVER" bash -s <<'CERTBOT_SETUP'
set -euo pipefail
DOMAIN="ameskamp.zuacaldeira.com"
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "Obtaining SSL certificate for $DOMAIN..."
    if ! command -v certbot &> /dev/null; then
        apt-get update && apt-get install -y certbot python3-certbot-nginx
    fi
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@zuacaldeira.com
    # Now install the full SSL config
    cp /opt/ameskamp-audio-tools/nginx/$DOMAIN.conf /etc/nginx/sites-available/$DOMAIN
    nginx -t && systemctl reload nginx
else
    echo "SSL certificate already exists for $DOMAIN"
fi
CERTBOT_SETUP

# 5. Verify health
echo "[5/5] Verifying deployment..."
sleep 15

ssh "$SERVER" "cd $PROJECT_DIR && docker compose -f $COMPOSE_FILE ps"

# Health check
HTTP_STATUS=$(ssh "$SERVER" "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://$DOMAIN/ || echo '000'")
if [ "$HTTP_STATUS" = "200" ]; then
    echo ""
    echo "=== Deployment successful ==="
    echo "Application: https://$DOMAIN"
else
    echo ""
    echo "=== WARNING: Health check returned HTTP $HTTP_STATUS ==="
    echo "Check container logs: ssh $SERVER 'cd $PROJECT_DIR && docker compose -f $COMPOSE_FILE logs'"
fi
