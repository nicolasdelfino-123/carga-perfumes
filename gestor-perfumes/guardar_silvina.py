"""Recupera exclusivamente Silvina. Solo escribe la biblioteca SQLite local."""
import argparse
import os
from pathlib import Path
import re

from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session

from app import create_app,Perfume,Picture,normalize
from library import classify
import transfers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirmar',action='store_true',help='Guardar los nuevos en la biblioteca LOCAL')
    args = parser.parse_args()
    app = create_app()
    url = os.getenv('MASTER_DATABASE_URL') or f"sqlite:///{Path(__file__).resolve().parent/'instance'/'biblioteca.db'}"
    engine = create_engine(url)
    try:
        products = transfers.source_products('catalogo_silvina')
        if len(products) >= 1000:
            raise transfers.TransferError('Se alcanzó el límite de resultados; no se hará una carga incompleta.')
        with Session(engine) as db:
            existing = [{'id':p.id,'name':p.name,'brand':p.brand} for p in db.scalars(select(Perfume))]
        analysis = classify(products,existing,normalize)
        new = [int(x['row']['id']) for x in analysis if x['status']=='new']
        review = [x['row']['name'] for x in analysis if x['status']=='review']
        duplicates = sum(x['status']=='duplicate' for x in analysis)
        print(f'Silvina: {len(products)} productos; {len(new)} nuevos; {duplicates} duplicados exactos; {len(review)} dudosos.')
        if review:
            print('Revisión pendiente (no se guardan automáticamente): '+', '.join(review))
        if not args.confirmar:
            print('Solo análisis. No se guardó nada.')
            return
        client = app.test_client()
        client.get('/')
        with client.session_transaction() as session:
            csrf = session['csrf']
        for offset in range(0,len(new),500):
            ids = new[offset:offset+500]
            response = client.post('/recover/preview',data={'csrf':csrf,'source':'catalogo_silvina','perfume':[str(x) for x in ids]})
            if response.status_code != 200:
                raise transfers.TransferError('Falló la recuperación de fotos/textos. No se guardó este lote. Revisá la recuperación en el panel.')
            token = re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            response = client.post('/recover/confirm',data={'csrf':csrf,'token':token,'row':[str(i) for i in range(len(ids))]})
            if response.status_code != 302:
                raise transfers.TransferError('No se completó el guardado local del lote; revisá el panel antes de repetir.')
        with Session(engine) as db:
            total = db.scalar(select(func.count()).select_from(Perfume))
            photos = db.scalar(select(func.count()).select_from(Picture))
            no_main = db.scalar(select(func.count()).select_from(Perfume).where(Perfume.description == ''))
            no_short = db.scalar(select(func.count()).select_from(Perfume).where(Perfume.short_description == ''))
        print(f'Biblioteca LOCAL: {total} perfumes y {photos} fotos guardados.')
        print(f'Sin descripción principal: {no_main}; sin descripción de pestaña: {no_short}. No se inventaron textos ausentes.')
        print('Las bases de clientes se consultaron únicamente en SOLO LECTURA.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except transfers.TransferError as error:
        print(str(error))
        raise SystemExit(1)
    except Exception:
        print('Falló la operación; no se muestran credenciales. Comprobá el túnel y la conexión de Silvina.')
        raise SystemExit(1)
