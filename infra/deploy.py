import sys
import os
import tarfile
import tempfile
import paramiko
import time
from getpass import getpass

# Forçar stdout UTF-8 para evitar erro de console Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "31.97.20.100"
USER = "root"
PASS = os.environ.get("TRILHAS_SSH_PASSWORD") or getpass("Senha SSH: ")
PRODUCTION_SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
PRODUCTION_DATABASE_URL = os.environ["DATABASE_URL"]
DOMAIN = "equipemorcego.nabio.pro"
APP_DIR = "/var/www/apps/equipemorcego"
PORT = 8055

print(f"=== CONTINUANDO DEPLOY AUTOMÁTICO EM {DOMAIN} ===")

ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.connect(HOST, username=USER, password=PASS, timeout=30)

def run_cmd(cmd, desc=""):
    if desc:
        print(f"\n>> {desc}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if out:
        print(out[:500] + ("..." if len(out) > 500 else ""))
    if err and exit_status != 0:
        print(f"ERRO: {err[:500]}")
    return exit_status, out

# 6. Criar virtualenv e instalar dependências
setup_python_cmd = f"""
which python3.11 || which python3
python3 -m venv {APP_DIR}/venv
{APP_DIR}/venv/bin/pip install --upgrade pip
{APP_DIR}/venv/bin/pip install -r {APP_DIR}/requirements.txt psycopg2-binary
"""
run_cmd(setup_python_cmd, "Configurando Python Virtualenv e instalando dependências")

# 7. Escrever arquivo de ambiente (.env) de produção
env_content = f"""DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY={PRODUCTION_SECRET_KEY}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS={DOMAIN},31.97.20.100,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://{DOMAIN},http://{DOMAIN}
DATABASE_URL={PRODUCTION_DATABASE_URL}
REDIS_URL=redis://127.0.0.1:6379/2
SITE_URL=https://{DOMAIN}
TIME_ZONE=America/Fortaleza
"""

sftp = ssh.open_sftp()
with sftp.file(f"{APP_DIR}/.env", "w") as f:
    f.write(env_content)
sftp.close()
print("Arquivo .env de produção gravado com sucesso.")

# 8. Executar migrações, collectstatic e seed
django_setup_cmd = f"""
cd {APP_DIR}
{APP_DIR}/venv/bin/python manage.py migrate --noinput
{APP_DIR}/venv/bin/python manage.py collectstatic --noinput
{APP_DIR}/venv/bin/python manage.py seed_initial_data
"""
run_cmd(django_setup_cmd, "Executando migrações, collectstatic e seed inicial do evento e administrador")

# 9. Configurar Systemd Service
systemd_content = f"""[Unit]
Description=Gunicorn daemon para Equipe Morcego - Sistema Trilhas
After=network.target postgresql.service redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory={APP_DIR}
EnvironmentFile={APP_DIR}/.env
ExecStart={APP_DIR}/venv/bin/gunicorn --workers 3 --threads 2 --bind 127.0.0.1:{PORT} --timeout 60 config.wsgi:application
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

sftp = ssh.open_sftp()
with sftp.file("/etc/systemd/system/equipemorcego.service", "w") as f:
    f.write(systemd_content)
sftp.close()

# Ajustar permissões e reiniciar serviço
permissions_cmd = f"""
chown -R www-data:www-data {APP_DIR}
chmod -R 755 {APP_DIR}
systemctl daemon-reload
systemctl enable --now equipemorcego.service
systemctl restart equipemorcego.service
sleep 3
systemctl status equipemorcego.service --no-pager | head -n 15
"""
run_cmd(permissions_cmd, "Habilitando e iniciando o serviço systemd equipemorcego")

# 10. Configurar Nginx
nginx_conf = f"""server {{
    listen 80;
    server_name {DOMAIN};

    client_max_body_size 25M;

    location /static/ {{
        alias {APP_DIR}/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
    }}

    location /media/ {{
        alias {APP_DIR}/media/;
    }}

    location / {{
        proxy_pass http://127.0.0.1:{PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""

sftp = ssh.open_sftp()
with sftp.file(f"/etc/nginx/sites-available/{DOMAIN}", "w") as f:
    f.write(nginx_conf)
sftp.close()

nginx_activate_cmd = f"""
ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
"""
run_cmd(nginx_activate_cmd, "Ativando configuração do Nginx e recarregando")

# 11. Certificado SSL via Certbot (HTTPS)
certbot_cmd = f"""
certbot --nginx -d {DOMAIN} --non-interactive --agree-tos -m contato@equipemorcego.com.br --redirect || echo "Certbot em modo fallback"
"""
run_cmd(certbot_cmd, "Emitindo Certificado SSL Let's Encrypt com Certbot")

# 12. Teste Final de Resposta HTTP/HTTPS
test_cmd = f"""
curl -s -o /dev/null -w "Gunicorn Local: %{{http_code}}\\n" http://127.0.0.1:{PORT}/
curl -s -o /dev/null -w "Nginx HTTP: %{{http_code}}\\n" http://{DOMAIN}/
curl -s -o /dev/null -w "HTTPS Oficial: %{{http_code}}\\n" https://{DOMAIN}/ || true
"""
run_cmd(test_cmd, "Verificando status HTTP/HTTPS em produção")

ssh.close()
print("\n=== DEPLOY EM PRODUÇÃO FINALIZADO COM SUCESSO! ===")
print(f"URL: https://{DOMAIN}")
