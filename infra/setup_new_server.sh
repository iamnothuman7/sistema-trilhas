#!/usr/bin/env bash
# Instalação inicial do Sistema Trilhas num servidor Ubuntu que já tem nginx e certbot.
# Não mexe em outros sites do servidor. Uso (como root):
#   APP_DOMAIN=equipemorcego.com APP_PORT=8055 bash setup_new_server.sh /root/eqm_migracao
set -euo pipefail

SRC="${1:?pasta com code.tar.gz, overlays.tar.gz e backup.tar.gz}"
DOMAIN="${APP_DOMAIN:-equipemorcego.com}"
PORT="${APP_PORT:-8055}"
APP=/var/www/apps/equipemorcego
SVC=equipemorcego.service
RUN_USER=morcego-prod

id "$RUN_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin "$RUN_USER"
mkdir -p "$APP" "$APP/media" "$APP/private-media" "$APP/.models"
tar -xzf "$SRC/code.tar.gz" -C "$APP"
tar -xzf "$SRC/overlays.tar.gz" -C "$APP/media"

command -v python3 >/dev/null
python3 -m venv --help >/dev/null 2>&1 || apt-get install -y python3-venv
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$APP/.env" ]; then
  SECRET=$("$APP/venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(64))")
  MPKEY=$("$APP/venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(48))")
  cat > "$APP/.env" <<EOF
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=False
SITE_DOMAIN=$DOMAIN
LEGACY_SITE_DOMAINS=equipemorcego.nabio.pro
DJANGO_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN,.$DOMAIN,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN,https://*.$DOMAIN
PUBLIC_SITE_URL=https://$DOMAIN
SQLITE_DATABASE_PATH=$APP/db.sqlite3
PRIVATE_MEDIA_ROOT=$APP/private-media
U2NET_HOME=$APP/.models
TIME_ZONE=America/Fortaleza
MP_ENVIRONMENT=production
MP_WEBHOOK_URL=https://$DOMAIN/integracoes/mercadopago/webhook/
MP_CREDENTIALS_ENCRYPTION_KEY=$MPKEY
DEFAULT_FROM_EMAIL=contato@equipemorcego.com.br
EOF
fi
chmod 600 "$APP/.env"

manage() { (cd "$APP" && set -a && . "$APP/.env" && set +a && "$APP/venv/bin/python" manage.py "$@"); }

manage migrate --noinput
if [ ! -f "$APP/.backup-imported" ]; then
  rm -rf "$SRC/backup" && mkdir -p "$SRC/backup"
  tar -xzf "$SRC/backup.tar.gz" -C "$SRC/backup" --strip-components=1
  manage import_site_backup "$SRC/backup"
  date -Is > "$APP/.backup-imported"
fi
manage sync_trilhao_2027
manage collectstatic --noinput -v 0
manage check --deploy 2>&1 | tail -n 5 || true

chown -R "$RUN_USER:$RUN_USER" "$APP"
chmod 750 "$APP"
chmod 600 "$APP/.env"
[ -f "$APP/db.sqlite3" ] && chmod 600 "$APP/db.sqlite3"

cat > /etc/systemd/system/$SVC <<EOF
[Unit]
Description=Sistema Trilhas - Equipe Morcego ($DOMAIN)
After=network.target

[Service]
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/gunicorn --config $APP/gunicorn.conf.py --bind 127.0.0.1:$PORT --workers 3 config.wsgi:application
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now $SVC
systemctl restart $SVC

NGINX=/etc/nginx/sites-available/$DOMAIN
if [ ! -f "$NGINX" ]; then
  cat > "$NGINX" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN *.$DOMAIN;
    client_max_body_size 25M;

    location /static/ { alias $APP/staticfiles/; expires 30d; add_header Cache-Control "public, max-age=2592000, immutable"; }
    location ^~ /media/expenses/receipts/ { return 404; }
    location ^~ /media/evidences/ { return 404; }
    location ^~ /private-media/ { return 404; }
    location /media/ { alias $APP/media/; }

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
  ln -sf "$NGINX" /etc/nginx/sites-enabled/
fi
# O nginx precisa ler estáticos e mídia pública
usermod -a -G "$RUN_USER" www-data
chmod 750 "$APP" && chmod -R g+rX "$APP/staticfiles" "$APP/media"
nginx -t
systemctl reload nginx
sleep 2
systemctl is-active $SVC
curl -s -o /dev/null -w "local: %{http_code}\n" -H "Host: $DOMAIN" http://127.0.0.1:$PORT/
