"""Registra Naim mediante el túnel abierto; contraseña pedida en terminal."""
import getpass
import json
import os
from pathlib import Path
from sqlalchemy.engine import URL
import transfers
from conectar_silvina import private_write


def main():
    folder = Path(__file__).resolve().parent / 'instance'
    folder.mkdir(exist_ok=True, mode=0o700)
    registry = folder / 'connections.json'
    if os.getenv('PERFUME_CONNECTIONS') and Path(os.environ['PERFUME_CONNECTIONS']).resolve() != registry:
        raise transfers.TransferError('Hay otro archivo PERFUME_CONNECTIONS activo.')
    secret = folder / 'naim.url'
    password = getpass.getpass('Contraseña PostgreSQL de catalogo_naim_user: ')
    url = URL.create('postgresql+psycopg', username='catalogo_naim_user', password=password,
                     host='127.0.0.1', port=55433, database='catalogo_naim')
    # Validar antes de guardar cambios; las consultas siguen siendo de solo lectura.
    alias = 'catalogo_naim'
    entry = {'database':alias, 'url_file':str(secret), 'roles':['target'], 'import_products':True,
             'category_ids':{'masculino':1, 'femenino':2, 'unisex':3}}
    from sqlalchemy import create_engine
    config = json.loads(registry.read_text()) if registry.exists() else {}
    engine = create_engine(url, connect_args={'connect_timeout':10, 'options':'-c default_transaction_read_only=on'})
    try:
        with engine.connect() as conn:
            if conn.exec_driver_sql('SELECT current_database()').scalar_one() != alias:
                raise transfers.TransferError('La base no coincide con Naim.')
            categories = transfers.target_categories(conn, entry)
    finally:
        engine.dispose()
    private_write(secret, url.render_as_string(hide_password=False))
    config[alias] = entry
    private_write(registry, json.dumps(config, indent=2))
    print('Naim configurado. Categorías verificadas:', categories)
    print('No se importaron productos. Abrí una selección guardada y prepará el envío a catalogo_naim.')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, Exception):
        print('No se completó la configuración. Revisá contraseña, túnel 55433 y categorías 1/2/3. No se muestran credenciales.')
        raise SystemExit(1)
