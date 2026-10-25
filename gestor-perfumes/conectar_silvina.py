"""Configuración LOCAL de Silvina. En el VPS solo lee un archivo por SSH."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from sqlalchemy.engine import make_url
import transfers


REMOTE_READ = '''
import sys
try:
    from dotenv import dotenv_values
    values = dotenv_values('/var/www/catalogo/catalogo-silvina/backend/.env')
    raw = values.get('SQLALCHEMY_DATABASE_URI') or values.get('DATABASE_URL')
    if not raw:
        sys.exit(2)
    sys.stdout.write(raw)
except Exception:
    sys.exit(2)
'''


def local_url(raw, database='catalogo_silvina', username='catalogo_silvina_user'):
    url = make_url(raw.strip())
    if (url.get_backend_name() != 'postgresql' or url.database != database
            or not url.username or (username is not None and url.username != username)
            or url.host not in {'localhost','127.0.0.1','::1'}
            or (url.port or 5432) != 5432):
        raise transfers.TransferError(f'La configuración no coincide con {database} en localhost:5432. Se abortó sin consultar otra base.')
    if set(url.query) - {'sslmode','sslrootcert','sslcert','sslkey'}:
        raise transfers.TransferError('La URL tiene parámetros adicionales que requieren revisión. Se abortó.')
    return url.set(drivername='postgresql+psycopg', host='127.0.0.1', port=55432)


def private_write(path, content):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.private-')
    try:
        with os.fdopen(fd, 'w') as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(content)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def main(client='silvina'):
    if client not in {'silvina','gleidys','juli'}:
        raise transfers.TransferError('Origen no autorizado para este configurador.')
    database = f'catalogo_{client}'
    print(f'Configurar SOLO {database} como origen de lectura.')
    print('Dejá abierto el túnel SSH en otra terminal (puerto local 55432).')
    print('SSH puede pedir la contraseña del VPS; escribila aquí, nunca en el chat.')
    # SSH conserva su verificación normal de host y pide la contraseña en la terminal.
    # stdout se captura: la URL con contraseña nunca se imprime ni aparece en argumentos.
    remote_read = REMOTE_READ.replace('catalogo-silvina',f'catalogo-{client}')
    command = f'/var/www/catalogo/catalogo-{client}/backend/venv/bin/python -c ' + shlex.quote(remote_read)
    result = subprocess.run(['ssh','-p','5285','-T','-o','ConnectTimeout=15',
                             'root@200.58.126.102', command], stdout=subprocess.PIPE, timeout=180)
    if result.returncode:
        raise transfers.TransferError(f'No se pudo leer la conexión de {client} por SSH. No se cambió nada en el VPS.')
    url = local_url(result.stdout.decode(), database=database,
                    username='catalogo_silvina_user' if client=='silvina' else None)
    folder = Path(__file__).resolve().parent / 'instance'
    folder.mkdir(exist_ok=True, mode=0o700)
    secret = folder / f'{client}.url'
    registry = folder / 'connections.json'
    config = json.loads(registry.read_text()) if registry.exists() else {}
    if os.getenv('PERFUME_CONNECTIONS') and Path(os.environ['PERFUME_CONNECTIONS']).resolve() != registry:
        raise transfers.TransferError('Hay otro archivo PERFUME_CONNECTIONS activo. Revisalo antes de continuar.')
    private_write(secret, url.render_as_string(hide_password=False))
    config[database] = {'database':database, 'url_file':str(secret), 'roles':['source']}
    private_write(registry, json.dumps(config, indent=2))
    print('Conexión guardada en archivos locales privados. Comprobando solo lectura…')
    # Mantiene el mismo filtro SQL y las transacciones READ ONLY del panel.
    with transfers.connect(database, 'source') as (conn, _):
        current = conn.exec_driver_sql('SELECT current_database()').scalar_one()
        mode = conn.exec_driver_sql('SHOW transaction_read_only').scalar_one()
        if current != database or mode != 'on':
            raise transfers.TransferError('No se confirmó la base o el modo de solo lectura. Se abortó.')
        transfers.schema(conn)
    print('OK: acceso de SOLO LECTURA confirmado y esquema compatible.')
    print(f'Abrí http://127.0.0.1:5055/sources y elegí {database}.')
    if client != 'silvina':
        print('Presioná «Comparar con la maestra» para detectar repetidos y revisar los parecidos antes de guardar.')
    print('Todavía no se copiaron productos: revisá y seleccioná los que vos cargaste.')


def run(client='silvina'):
    try:
        main(client)
    except KeyboardInterrupt:
        print('\nCancelado. No se modificó el VPS.')
        raise SystemExit(1)
    except transfers.TransferError as error:
        print(str(error))
        raise SystemExit(1)
    except Exception:
        # No imprimir tracebacks con URLs o parámetros de conexión.
        print('No se completó la conexión. Verificá SSH, el túnel local 55432 y el acceso a PostgreSQL. No se muestran credenciales ni se modificó el VPS.')
        raise SystemExit(1)


if __name__ == '__main__':
    run()
