"""Prueba real contra un cluster TEMPORAL exclusivo, puerto 55465.

Ejecutar con PERFUMES_TEST_PG=1. Nunca usa las conexiones del negocio.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest

import psycopg
from sqlalchemy import create_engine, text

from app import create_app, normalize
import transfers


DDL = """
CREATE TABLE category (id SERIAL PRIMARY KEY, name VARCHAR(50) NOT NULL UNIQUE, description VARCHAR(200));
CREATE TABLE product (
 id SERIAL PRIMARY KEY, name VARCHAR(2000) NOT NULL, brand VARCHAR(200),
 description VARCHAR(40000), short_description VARCHAR(40000),
 price DOUBLE PRECISION NOT NULL, stock INTEGER NOT NULL, image_url VARCHAR(200),
 category_id INTEGER NOT NULL REFERENCES category(id), is_active BOOLEAN NOT NULL,
 created_at TIMESTAMP NOT NULL, volume_ml INTEGER, volume_options JSONB,
 flavors JSONB, flavor_enabled BOOLEAN, flavor_catalog JSONB, flavor_stock_mode BOOLEAN NOT NULL,
 price_wholesale DOUBLE PRECISION, puffs INTEGER, nicotine_mg INTEGER
);
CREATE TABLE product_images (
 id SERIAL PRIMARY KEY, product_id INTEGER REFERENCES product(id), mime_type VARCHAR(64) NOT NULL,
 bytes BYTEA NOT NULL, width INTEGER, height INTEGER, digest VARCHAR(64), created_at TIMESTAMP NOT NULL
);
"""


@unittest.skipUnless(os.getenv('PERFUMES_TEST_PG') == '1', 'requiere cluster temporal explícito')
class Integration(unittest.TestCase):
    def test_complete_workflow(self):
        os.environ['PATH'] = '/usr/lib/postgresql/18/bin:' + os.environ['PATH']
        with tempfile.TemporaryDirectory() as folder:
            tag = os.urandom(4).hex()
            names = [f'perfumes_test_source_{tag}', f'perfumes_test_target_{tag}']
            with psycopg.connect('host=127.0.0.1 port=55465 dbname=postgres user=nico', autocommit=True) as admin:
                for name in names:
                    admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
            engines = [create_engine(f'postgresql+psycopg://nico@127.0.0.1:55465/{name}') for name in names]
            for e in engines:
                with e.begin() as conn:
                    conn.exec_driver_sql(DDL)
            config = {}
            for i, name in enumerate(names):
                key = f'PERFUMES_TEST_CONNECTION_{i}'
                os.environ[key] = f'postgresql+psycopg://nico@127.0.0.1:55465/{name}'
                config[name] = {'database':name, 'url_env':key, 'roles':['source'] if i == 0 else ['target'], 'allow_write':i == 1}
            path = Path(folder)/'connections.json'
            path.write_text(json.dumps(config))
            os.environ['PERFUME_CONNECTIONS'] = str(path)
            os.environ['ALLOW_CLIENT_WRITES'] = '1'
            src, dst = names
            with engines[0].begin() as c:
                c.exec_driver_sql("INSERT INTO category(name) VALUES ('Árabes Masc')")
                for name in ['Asad', 'Asad Bourbon', 'Asad Zanzibar']:
                    pid = c.execute(text("""INSERT INTO product(name,brand,price,stock,category_id,is_active,created_at,flavor_stock_mode,image_url)
                        VALUES (:name,'Lattafa',45000,99,1,true,now(),false,NULL) RETURNING id"""), {'name':name}).scalar_one()
                    iid = c.execute(text("INSERT INTO product_images(product_id,mime_type,bytes,created_at) VALUES (:pid,'image/png',:data,now()) RETURNING id"), {'pid':pid,'data':b'test-image'}).scalar_one()
                    c.execute(text("UPDATE product SET image_url=:url WHERE id=:pid"), {'url':f'/public/img/{iid}','pid':pid})
            with engines[1].begin() as c:
                c.exec_driver_sql("INSERT INTO category(id,name) VALUES (47,'Perfumes Árabes Masculinos'), (83,'Unisex')")
            self.assertEqual(len(transfers.source_products(src)), 3)
            with transfers.connect(src, 'source') as (c, _):
                self.assertEqual(c.scalar(text('SHOW transaction_read_only')), 'on')
            app = create_app(f'sqlite:///{folder}/master.db')
            app.config['TESTING'] = True
            client = app.test_client()
            self.assertEqual(client.get('/').status_code, 200)
            with client.session_transaction() as sess:
                csrf = sess['csrf']
            self.assertEqual(client.post('/recover/preview',data={'source':src,'perfume':['1']}).status_code,400)
            def token(response):
                import re
                self.assertEqual(response.status_code,200, response.data.decode())
                return re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            r = client.post('/recover/preview',data={'csrf':csrf,'source':src,'perfume':['1','2','3']})
            recovery = token(r)
            r = client.post('/recover/confirm',data={'csrf':csrf,'token':recovery,'row':['0','1','2']})
            self.assertEqual(r.status_code,302)
            self.assertEqual(client.get('/picture/1').data,b'test-image')
            r = client.post('/',data={'csrf':csrf,'list':'Asad $ 45000\n9 PM'})
            self.assertIn(b'Asad Bourbon',r.data)
            r = client.post('/preview',data={'csrf':csrf,'perfume':['1','1','2','3']})
            selection = token(r)
            r = client.post('/target/preview',data={'csrf':csrf,'token':selection,'target':dst})
            mapping = token(r)
            self.assertIn(b'Perfumes', r.data)
            r = client.post('/target/map',data={'csrf':csrf,'token':mapping,
                'category_0':'Perfumes Árabes Masculinos','category_1':'Perfumes Árabes Masculinos',
                'category_2':'Perfumes Árabes Masculinos','extra_0':['Unisex']})
            self.assertEqual(r.status_code, 200)
            self.assertNotIn(b'Hacer backup e importar', r.data)
            r = client.post('/target/confirm', data={'csrf':csrf, 'approve':'yes', 'confirmation':dst})
            self.assertEqual(r.status_code,400)
            self.assertIn(b'ABORTADO',r.data)
            with engines[1].connect() as c:
                self.assertEqual(c.scalar(text('SELECT count(*) FROM product')),0)
            with transfers.connect(src, 'source') as (c, _):
                self.assertEqual(c.scalar(text('SHOW default_transaction_read_only')), 'on')
                for statement in ['DELETE FROM public.product', 'UPDATE public.product SET stock=0',
                                  'CREATE TABLE forbidden(id int)', 'SET TRANSACTION READ WRITE',
                                  'TRUNCATE public.product', 'INSERT INTO category(name) VALUES (\'prohibido\')']:
                    with self.assertRaises(transfers.TransferError):
                        c.exec_driver_sql(statement)
            # Incluso un SELECT que intente cambiar una secuencia es rechazado por PostgreSQL.
            from sqlalchemy.exc import InternalError
            with self.assertRaises(InternalError):
                with transfers.connect(src, 'source') as (c, _):
                    c.exec_driver_sql("SELECT nextval('public.product_id_seq')")
            with engines[0].connect() as c:
                self.assertEqual(c.scalar(text('SELECT count(*) FROM product')),3)
                self.assertEqual(c.scalar(text('SELECT sum(stock) FROM product')),297)
            for e in engines:
                e.dispose()


class WriteProtection(unittest.TestCase):
    def test_cannot_override_with_environment(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'ALLOW_CLIENT_WRITES':'1'}):
            with patch('transfers.authorized') as auth, patch('transfers.backup') as backup:
                with self.assertRaises(transfers.TransferError):
                    with transfers.connect('anything','target',write=True):
                        self.fail('No debe abrir conexión')
                with self.assertRaises(transfers.TransferError):
                    transfers.execute('anything',[],normalize,'/tmp')
                auth.assert_not_called()
                backup.assert_not_called()
        with patch('app.create_engine') as engine:
            with self.assertRaises(transfers.TransferError):
                create_app('postgresql+psycopg://user:secret@localhost/customer')
            engine.assert_not_called()


class CategoryMatching(unittest.TestCase):
    def test_keywords_and_gender(self):
        suggest = lambda name, names: transfers.suggest_category(name,names,normalize)[0]
        self.assertEqual(suggest('Árabes Masc',['Perfumes Árabes Masculinos','Árabes Femeninos']), 'Perfumes Árabes Masculinos')
        self.assertEqual(suggest('Árabes Unisex',['Árabes Masculinos','Árabes Femeninos']), '')
        self.assertEqual(suggest('Árabes Unisex',['Árabes','Diseñador']), 'Árabes')
        self.assertEqual(suggest('Árabes',['Árabes Masculinos','Árabes Femeninos']), '')
        self.assertEqual(suggest('Nicho Árabes',['Árabes','Diseñador']), '')
        self.assertEqual(suggest('Diseñador Fem',['Diseñador Femeninos','Nicho Femeninos']), 'Diseñador Femeninos')


if __name__ == '__main__':
    unittest.main()
