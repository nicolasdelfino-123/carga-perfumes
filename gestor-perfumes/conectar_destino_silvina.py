"""Habilita Silvina como destino local; solo consulta el VPS al configurar."""
import json
import os
from pathlib import Path
from sqlalchemy import select
import transfers
from conectar_silvina import main as configure_source, private_write


def main():
    registry = Path(__file__).resolve().parent / 'instance' / 'connections.json'
    if os.getenv('PERFUME_CONNECTIONS') and Path(os.environ['PERFUME_CONNECTIONS']).resolve() != registry:
        raise transfers.TransferError('Hay otro archivo PERFUME_CONNECTIONS activo. Revisalo antes de continuar.')
    alias = 'catalogo_silvina'
    if 'source' not in transfers.connections().get(alias, {}).get('roles', []):
        configure_source('silvina')
    with transfers.connect(alias, 'source') as (conn, item):
        entry = dict(item, roles=['source', 'target'], import_products=True, category_mode='destination')
        verified = transfers.target_categories(conn, entry)
    registry = Path(__file__).resolve().parent / 'instance' / 'connections.json'
    config = json.loads(registry.read_text())
    config[alias] = entry
    private_write(registry, json.dumps(config, indent=2))
    print('Silvina habilitada como destino. Categorías verificadas:', verified)
    print('No se enviaron productos. Abrí el panel y elegí catalogo_silvina; cada envío requiere revisión y backup.')


if __name__ == '__main__':
    try:
        main()
    except transfers.TransferError as error:
        print(str(error))
        raise SystemExit(1)
    except (Exception, KeyboardInterrupt):
        print('No se completó la conexión. Revisá SSH, túnel 55432 y permisos. No se muestran credenciales.')
        raise SystemExit(1)
