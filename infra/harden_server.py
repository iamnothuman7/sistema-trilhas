"""Harden this installation without changing SSH, passwords or payment credentials.

Run as root on the production host, after deploying the matching application.
The current SQLite database is moved, never replaced by a stale snapshot.
"""
import json
from contextlib import closing
import os
from pathlib import Path
import pwd
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
import urllib.request
import urllib.error

ROOT = Path('/var/www/apps/equipemorcego')
SERVICE = 'equipemorcego.service'
ACCOUNT = 'morcego-prod'
NGINX = Path('/etc/nginx/sites-available/equipemorcego.nabio.pro')
DROPIN = Path('/etc/systemd/system/equipemorcego.service.d/security.conf')


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=180, **kwargs)


def verify_http(path, attempts=6):
    """Allow workers a short startup interval before treating a deploy as failed."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen('https://equipemorcego.nabio.pro'+path, timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError('Unexpected HTTP status')
                return
        except (urllib.error.URLError, OSError, RuntimeError):
            if attempt + 1 == attempts:
                raise RuntimeError('Health check did not become ready: '+path) from None
            time.sleep(2)


def apply():
    assert os.geteuid() == 0 and ROOT.resolve() == ROOT
    assert 'SQLITE_DATABASE_PATH' in (ROOT/'config/settings/production.py').read_text()
    assert NGINX.is_file() and not NGINX.is_symlink()
    run(['nginx', '-t'])
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        run(['useradd', '--system', '--user-group', '--home-dir', str(ROOT), '--shell', '/usr/sbin/nologin', ACCOUNT])
        account = pwd.getpwnam(ACCOUNT)
    assert account.pw_shell in ('/usr/sbin/nologin', '/bin/false')
    backup = Path('/var/backups/equipemorcego') / ('security-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    os.chmod(backup, 0o700)
    env_before = (ROOT/'.env').read_bytes()
    nginx_before = NGINX.read_bytes()
    dropin_before = DROPIN.read_bytes() if DROPIN.exists() else None
    for name, data in (('environment.before', env_before), ('nginx.before', nginx_before)):
        (backup/name).write_bytes(data); os.chmod(backup/name, 0o600)
    if dropin_before is not None:
        (backup/'security-dropin.before').write_bytes(dropin_before)
        os.chmod(backup/'security-dropin.before', 0o600)
    metadata = {}

    def save_permissions_metadata():
        # Atomic, private snapshot remains available even if a later rollback fails.
        temporary = backup/'permissions.before.json.tmp'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(metadata, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, backup/'permissions.before.json')

    def permissions(path, uid, gid, mode):
        assert path == ROOT or path.is_relative_to(ROOT)
        if path.is_symlink():
            return
        stat = path.stat()
        metadata.setdefault(str(path), [stat.st_uid, stat.st_gid, stat.st_mode & 0o7777])
        os.chown(path, uid, gid); os.chmod(path, mode)

    database = ROOT/'db.sqlite3'
    runtime = ROOT/'runtime'
    actual_database = runtime/'db.sqlite3'
    assert database.is_file() and database.resolve().is_relative_to(ROOT)
    assert not runtime.is_symlink()
    for name in ('media', 'private-media', 'staticfiles', '.models'):
        assert not (ROOT/name).is_symlink(), name
    moved_database = False
    stopped = False
    try:
        run(['systemctl', 'stop', SERVICE]); stopped = True
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup/'db.sqlite3')) as destination:
            source.backup(destination)
            destination.commit()
        os.chmod(backup/'db.sqlite3', 0o600)
        runtime.mkdir(mode=0o700, exist_ok=True)
        permissions(runtime, account.pw_uid, account.pw_gid, 0o700)
        if not database.is_symlink():
            assert not actual_database.exists()
            assert not (ROOT/'db.sqlite3-wal').exists(), 'Checkpoint WAL before moving a database with an active WAL.'
            database.rename(actual_database)
            moved_database = True
            database.symlink_to(actual_database)
        else:
            assert database.resolve() == actual_database
        permissions(actual_database, account.pw_uid, account.pw_gid, 0o600)
        env = re.sub(rb'(?m)^SQLITE_DATABASE_PATH=.*\r?\n?', b'', env_before).rstrip(b'\r\n')
        (ROOT/'.env').write_bytes(env + b'\nSQLITE_DATABASE_PATH=' + str(actual_database).encode() + b'\n')

        writable = {'runtime', 'media', 'private-media', 'staticfiles', '.models'}
        for directory, dirs, files in os.walk(ROOT, followlinks=False):
            directory = Path(directory)
            dirs[:] = [name for name in dirs if not (directory == ROOT and name in writable)]
            permissions(directory, 0, 0, 0o755)
            for name in files:
                path = directory/name
                if path == ROOT/'.env' or path == database or path.is_symlink():
                    continue
                relative = path.relative_to(ROOT)
                source = relative.parts[0] in ('apps','config','frontend','tests','docs','infra','venv') or name in ('manage.py','requirements.txt','gunicorn.conf.py')
                mode = (0o755 if path.stat().st_mode & 0o111 else 0o644) if source else path.stat().st_mode & 0o755
                permissions(path, 0, 0, mode)
        permissions(ROOT/'.env', 0, account.pw_gid, 0o640)
        for name in writable:
            path = ROOT/name
            path.mkdir(mode=0o700 if name in ('runtime', 'private-media') else 0o755, exist_ok=True)
            assert not path.is_symlink()
            private = name in ('runtime', 'private-media')
            for directory, dirs, files in os.walk(path, followlinks=False):
                permissions(Path(directory), account.pw_uid, account.pw_gid, 0o700 if private else 0o755)
                for filename in files:
                    target = Path(directory)/filename
                    if target.is_symlink():
                        continue
                    permissions(target, account.pw_uid, account.pw_gid, 0o600 if private else 0o644)

        DROPIN.parent.mkdir(mode=0o755, exist_ok=True)
        DROPIN.write_text('[Service]\nUser=' + ACCOUNT + '\nGroup=' + ACCOUNT + '\nUMask=0077\n'
            'Environment=PYTHONDONTWRITEBYTECODE=1\n'
            'Environment=NUMBA_CACHE_DIR=' + str(runtime/'numba-cache') + '\n'
            'Environment=XDG_CACHE_HOME=' + str(runtime/'cache') + '\n'
            'NoNewPrivileges=yes\nPrivateTmp=yes\n'
            'ProtectSystem=strict\nProtectHome=yes\nProtectKernelTunables=yes\n'
            'ProtectKernelModules=yes\nProtectControlGroups=yes\nRestrictSUIDSGID=yes\n'
            'CapabilityBoundingSet=\nReadWritePaths=' + ' '.join(str(ROOT/name) for name in ('runtime','media','private-media','.models')) + '\n')
        os.chmod(DROPIN, 0o644)
        nginx = nginx_before.decode()
        marker = '    # Private documents must never be served by the public media alias.\n'
        if marker not in nginx:
            deny = marker + ''.join('    location ^~ '+path+' { return 404; }\n' for path in
                ('/media/expenses/receipts/', '/media/evidences/', '/private-media/', '/runtime/')) + '\n'
            assert '    location /media/ {' in nginx
            nginx = nginx.replace('    location /media/ {', deny+'    location /media/ {', 1)
        NGINX.write_text(nginx)
        run(['nginx', '-t'])
        python = str(ROOT/'venv/bin/python')
        command = ['runuser','-u',ACCOUNT,'--',python,'manage.py']
        run(command+['check','--deploy','--settings=config.settings.production'], cwd=ROOT)
        run(command+['migrate','--check','--settings=config.settings.production'], cwd=ROOT)
        run(command+['shell','--settings=config.settings.production','-c',
            "from django.db import connection, transaction\n"
            "from django.utils import timezone\n"
            "from apps.core.models import RateLimitBucket\n"
            "from pathlib import Path\n"
            "from uuid import uuid4\n"
            "assert Path(connection.settings_dict['NAME']).resolve()==Path('runtime/db.sqlite3').resolve()\n"
            "probe = uuid4().hex + uuid4().hex\n"
            "with transaction.atomic():\n"
            "    RateLimitBucket.objects.create(key=probe, count=1, expires_at=timezone.now())\n"
            "    assert RateLimitBucket.objects.filter(pk=probe).exists()\n"
            "    transaction.set_rollback(True)\n"
            "assert not RateLimitBucket.objects.filter(pk=probe).exists()\n"
            "print('Private runtime database read/write verified; probe rolled back')"], cwd=ROOT)
        save_permissions_metadata()
        run(['systemctl','daemon-reload'])
        run(['systemctl','start',SERVICE])
        run(['systemctl','reload','nginx'])
        for path in ('/', '/login/', '/static/img/logo-nav.webp'):
            verify_http(path)
        assert run(['systemctl','show',SERVICE,'--property=User','--value']).stdout.strip() == ACCOUNT
        # Other web applications using www-data must not read this site's secrets/data.
        for path in (ROOT/'.env', actual_database):
            result = subprocess.run(['runuser','-u','www-data','--','test','-r',str(path)])
            assert result.returncode != 0, 'Shared web account still has access to private files.'
        print(json.dumps({'hardening':'verified','service_user':ACCOUNT,'private_database':True,'ssh_credentials':'unchanged','backup':str(backup)}))
    except BaseException:
        try:
            save_permissions_metadata()
        except OSError:
            # Keep attempting rollback even if the backup volume becomes unavailable.
            print('Could not persist permission metadata; attempting rollback.', file=sys.stderr)
        if stopped:
            run(['systemctl','stop',SERVICE])
            if moved_database:
                database.unlink(missing_ok=True)
                actual_database.rename(database)
            for name, (uid,gid,mode) in reversed(list(metadata.items())):
                path=Path(name)
                if path.exists() and not path.is_symlink():
                    os.chown(path,uid,gid);os.chmod(path,mode)
            if moved_database:
                # Restore the original file's ownership, without restoring stale DB contents.
                prior = metadata.get(str(actual_database))
                if prior:
                    os.chown(database,prior[0],prior[1]);os.chmod(database,prior[2])
            (ROOT/'.env').write_bytes(env_before)
            NGINX.write_bytes(nginx_before)
            if dropin_before is None:
                DROPIN.unlink(missing_ok=True)
            else:
                DROPIN.write_bytes(dropin_before)
            run(['systemctl','daemon-reload'])
            run(['nginx','-t']);run(['systemctl','reload','nginx'])
            run(['systemctl','start',SERVICE])
        raise


if __name__ == '__main__':
    apply()
