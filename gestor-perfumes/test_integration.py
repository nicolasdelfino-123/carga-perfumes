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
    def setUp(self):
        from unittest.mock import patch
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)

    def test_complete_workflow(self):
        os.environ['PATH'] = '/usr/lib/postgresql/18/bin:' + os.environ['PATH']
        with tempfile.TemporaryDirectory() as folder:
            tag = os.urandom(4).hex()
            names = [f'perfumes_test_source_{tag}', f'perfumes_test_target_{tag}']
            with psycopg.connect('host=127.0.0.1 port=55465 dbname=postgres user=nico', autocommit=True) as admin:
                for name in names:
                    admin.execute(psycopg.sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(psycopg.sql.Identifier(name)))
            engines = [create_engine(f'postgresql+psycopg://nico@127.0.0.1:55465/{name}') for name in names]
            for e in engines:
                with e.begin() as conn:
                    conn.exec_driver_sql(DDL)
            config = {}
            for i, name in enumerate(names):
                key = f'PERFUMES_TEST_CONNECTION_{i}'
                os.environ[key] = f'postgresql+psycopg://nico@127.0.0.1:55465/{name}'
                config[name] = {'database':name, 'url_env':key, 'roles':['source'] if i == 0 else ['target'], 'allow_write':i == 1, 'import_products':i == 1, 'category_ids':{'masculino':47,'unisex':83}}
            path = Path(folder)/'connections.json'
            path.write_text(json.dumps(config))
            os.environ['PERFUME_CONNECTIONS'] = str(path)
            os.environ['ALLOW_CLIENT_WRITES'] = '1'
            src, dst = names
            with engines[0].begin() as c:
                c.exec_driver_sql("INSERT INTO category(name) VALUES ('Árabes Masc')")
                for name in ['Asad', 'Asad Bourbon', 'Asad Zanzibar']:
                    pid = c.execute(text("""INSERT INTO product(name,brand,description,price,stock,category_id,is_active,created_at,flavor_stock_mode,image_url)
                        VALUES (:name,'Lattafa','Descripción completa',45000,99,1,true,now(),false,NULL) RETURNING id"""), {'name':name}).scalar_one()
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
            saved = client.post('/selections/save',data={'csrf':csrf,'token':selection,'client':'Prueba',
                'price_1':'45.000,50','volume_1':'75'})
            self.assertEqual(saved.status_code,302)
            r = client.post(saved.location + '/prepare',data={'csrf':csrf})
            selection = token(r)
            r = client.post('/target/preview',data={'csrf':csrf,'token':selection,'target':dst})
            mapping = token(r)
            self.assertIn(b'Perfumes', r.data)
            r = client.post('/target/map',data={'csrf':csrf,'token':mapping,
                'category_0':'Perfumes Árabes Masculinos','category_1':'Perfumes Árabes Masculinos',
                'category_2':'Perfumes Árabes Masculinos','extra_0':['Unisex']})
            self.assertEqual(r.status_code, 200)
            target_token = token(r)
            self.assertIn(b'Hacer backup y confirmar', r.data)
            r = client.post('/target/confirm', data={'csrf':csrf, 'approve':'yes', 'confirmation':dst})
            self.assertEqual(r.status_code,400)
            with engines[1].begin() as c:
                c.exec_driver_sql("CREATE TABLE admin_sentinel (id integer primary key, value text)")
                c.exec_driver_sql("INSERT INTO admin_sentinel VALUES (1,'unchanged')")
            r = client.post('/target/confirm',data={'csrf':csrf,'token':target_token,'confirmation':dst})
            self.assertEqual(r.status_code,302, r.data.decode())
            with engines[1].connect() as c:
                before = c.execute(text('SELECT * FROM product ORDER BY id')).all()
                self.assertEqual(len(before),3)
                self.assertEqual(set(c.execute(text('SELECT DISTINCT price,stock,is_active,category_id FROM product')).all()),{(0.0,12,True,47),(45000.50,12,True,47)})
                self.assertEqual(c.scalar(text("SELECT volume_ml FROM product WHERE price=45000.50")),75)
                self.assertEqual(c.scalar(text('SELECT count(*) FROM product_images')),3)
                self.assertEqual(c.scalar(text('SELECT value FROM admin_sentinel')), 'unchanged')
                self.assertEqual(c.scalar(text("SELECT count(*) FROM product p JOIN product_images i ON p.image_url='/public/img/' || i.id WHERE i.product_id=p.id")),3)
            actual = transfers.imported_product(dst,'Asad',normalize)
            self.assertEqual(actual['description'],'Descripción completa')
            self.assertEqual(actual['price'],45000.50)
            self.assertEqual(actual['pictures'][0]['bytes'],b'test-image')
            # Repetir la confirmación no debe insertar ni modificar productos existentes.
            r = client.post('/target/confirm',data={'csrf':csrf,'token':target_token,'confirmation':dst})
            self.assertEqual(r.status_code,302,r.data.decode())
            with engines[1].connect() as c:
                self.assertEqual(c.execute(text('SELECT * FROM product ORDER BY id')).all(),before)
            rows = transfers.extract(src,{1})
            rows[0].update(name='Rollback probe',category='No autorizada')
            with self.assertRaises(transfers.TransferError):
                transfers.execute(dst,rows,normalize,folder)
            with engines[1].connect() as c:
                self.assertEqual(c.execute(text('SELECT * FROM product ORDER BY id')).all(),before)
            # Producto del cliente, con foto y valores propios: tampoco se pisa al elegirlo en la maestra.
            with engines[1].begin() as c:
                pid = c.execute(text("""INSERT INTO product(name,brand,description,price,stock,category_id,is_active,created_at,flavor_stock_mode)
                    VALUES ('Existente del cliente','Marca','Texto propio',777,3,83,false,now(),false) RETURNING id""")).scalar_one()
                c.execute(text("INSERT INTO product_images(product_id,mime_type,bytes,created_at) VALUES (:pid,'image/png',:data,now())"),{'pid':pid,'data':b'foto-propia'})
            with engines[1].connect() as c:
                before_products = c.execute(text('SELECT * FROM product ORDER BY id')).all()
                before_images = c.execute(text('SELECT * FROM product_images ORDER BY id')).all()
                before_categories = c.execute(text('SELECT * FROM category ORDER BY id')).all()
            existing = dict(rows[0], name='EXISTENTE DEL CLIENTE',brand='Marca',category='Perfumes Árabes Masculinos',price='99999',stock=12)
            inserted, omitted, _ = transfers.execute(dst,[existing],normalize,folder)
            self.assertEqual(inserted,[])
            self.assertEqual(omitted,['EXISTENTE DEL CLIENTE'])
            with engines[1].connect() as c:
                self.assertEqual(c.execute(text('SELECT * FROM product ORDER BY id')).all(),before_products)
                self.assertEqual(c.execute(text('SELECT * FROM product_images ORDER BY id')).all(),before_images)
                self.assertEqual(c.execute(text('SELECT * FROM category ORDER BY id')).all(),before_categories)
                self.assertEqual(c.scalar(text('SELECT value FROM admin_sentinel')),'unchanged')
            with engines[1].begin() as c:
                c.exec_driver_sql("""INSERT INTO product(name,brand,description,price,stock,category_id,is_active,created_at,flavor_stock_mode)
                    VALUES ('ARMAF MANDARÍN SKY ELIXIR','ARMAF','Original',500,7,47,true,now(),false)""")
            probe = dict(rows[0], name='Odyssey Mandarin Sky Elixir - Armaf',brand='Armaf',category='Perfumes Árabes Masculinos')
            with self.assertRaisesRegex(transfers.TransferError,'posible duplicado'):
                transfers.execute(dst,[probe],normalize,folder)
            with engines[1].connect() as c:
                self.assertEqual(c.scalar(text('SELECT count(*) FROM product')),5)
                self.assertEqual(c.scalar(text("SELECT stock FROM product WHERE name='ARMAF MANDARÍN SKY ELIXIR'")),7)
            # Única excepción de escritura sobre existentes: precio explícito, con control del valor anterior.
            with engines[1].connect() as c:
                original = dict(c.execute(text('SELECT * FROM product WHERE id=:id'),{'id':pid}).mappings().one())
                photos = c.execute(text('SELECT * FROM product_images ORDER BY id')).all()
                categories_before_prices = c.execute(text('SELECT * FROM category ORDER BY id')).all()
            change = {'id':pid,'name':original['name'],'old_price':str(original['price']),'price':'888.00','activate':True}
            transfers.execute(dst,[],normalize,folder,price_updates=[change])
            with engines[1].connect() as c:
                after_price = dict(c.execute(text('SELECT * FROM product WHERE id=:id'),{'id':pid}).mappings().one())
                self.assertEqual(after_price,dict(original,price=888.0,is_active=True))
                self.assertEqual(c.execute(text('SELECT * FROM product_images ORDER BY id')).all(),photos)
                self.assertEqual(c.execute(text('SELECT * FROM category ORDER BY id')).all(),categories_before_prices)
                self.assertEqual(c.scalar(text('SELECT value FROM admin_sentinel')),'unchanged')
            with self.assertRaises(transfers.TransferError):
                transfers.execute(dst,[],normalize,folder,price_updates=[change])
            # Si falla otro producto del lote, también se revierte el cambio de precio.
            change.update(old_price='888.0',price='0.00')
            bad_new = dict(rows[0],name='Producto de rollback de precio',brand='Test',category='No permitida')
            with self.assertRaises(transfers.TransferError):
                transfers.execute(dst,[bad_new],normalize,folder,price_updates=[change])
            with engines[1].connect() as c:
                self.assertEqual(c.scalar(text('SELECT price FROM product WHERE id=:id'),{'id':pid}),888.0)
            transfers.execute(dst,[],normalize,folder,price_updates=[change])
            with engines[1].connect() as c:
                self.assertEqual(dict(c.execute(text('SELECT * FROM product WHERE id=:id'),{'id':pid}).mappings().one()),dict(original,price=0.0,is_active=True))
            # Activación con precio vacío: conserva precio cero y todos los demás campos.
            with engines[1].begin() as c:
                c.execute(text('UPDATE product SET is_active=false WHERE id=:id'),{'id':pid})
                inactive = dict(c.execute(text('SELECT * FROM product WHERE id=:id'),{'id':pid}).mappings().one())
                changes = transfers.existing_product_changes(c,transfers.schema(c),[dict(inactive,price_entered=False)],normalize,[])
                self.assertEqual(changes,[{'id':pid,'name':inactive['name'],'old_price':'0.0','price':None,'activate':True}])
            activation = {'id':pid,'name':inactive['name'],'old_price':'0.0','price':None,'activate':True}
            transfers.execute(dst,[],normalize,folder,price_updates=[activation])
            with engines[1].connect() as c:
                self.assertEqual(dict(c.execute(text('SELECT * FROM product WHERE id=:id'),{'id':pid}).mappings().one()),dict(inactive,is_active=True))
                self.assertEqual(c.execute(text('SELECT * FROM product_images ORDER BY id')).all(),photos)
                self.assertEqual(c.execute(text('SELECT * FROM category ORDER BY id')).all(),categories_before_prices)
            # El filtro SQL bloquea modificaciones incluso si se intentaran desde el importador.
            guarded = create_engine(os.environ['PERFUMES_TEST_CONNECTION_1'])
            transfers.protect_import_engine(guarded)
            with guarded.begin() as c:
                tables = transfers.schema(c)
                forbidden = [text('UPDATE public.product SET stock=0'),text('DELETE FROM public.product_images'),
                    text('TRUNCATE public.product'),text('ALTER TABLE public.product ADD COLUMN bad int'),
                    tables['category'].insert().values(name='No crear'),
                    tables['product'].update().values(stock=0),tables['product'].update().values(price=0),tables['product'].update().values(is_active=True),
                    text('UPDATE public.product SET price=0'),tables['product_images'].delete()]
                from sqlalchemy.dialects.postgresql import insert
                forbidden.append(insert(tables['product']).values(name='No upsert').on_conflict_do_update(
                    index_elements=['id'],set_={'name':'No modificar'}))
                for statement in forbidden:
                    with self.assertRaises(transfers.TransferError):
                        c.execute(statement)
            guarded.dispose()
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
            with patch('transfers.authorized', return_value=({}, None)) as auth, patch('transfers.backup') as backup:
                with self.assertRaises(transfers.TransferError):
                    with transfers.connect('anything','target',write=True):
                        self.fail('No debe abrir conexión')
                with self.assertRaises(transfers.TransferError):
                    transfers.execute('anything',[],normalize,'/tmp')
                auth.assert_called_once()
                backup.assert_not_called()
        with patch('app.create_engine') as engine:
            with self.assertRaises(transfers.TransferError):
                create_app('postgresql+psycopg://user:secret@localhost/customer')
            engine.assert_not_called()


class ImportValidation(unittest.TestCase):
    def test_gender_original_brief_and_conflict(self):
        names = ['Masculinos','Femeninos','Unisex']
        row = {'category':'Perfumes masculinos','short_description':'Tipo: masculino'}
        self.assertEqual(transfers.suggest_gender(row,names)[0],'Masculinos')
        row['short_description'] = 'Tipo: femenino'
        self.assertEqual(transfers.suggest_gender(row,names)[0],'Femeninos')
        self.assertIn('tomada de la descripción breve',transfers.suggest_gender(row,names)[1])
        row['category'] = 'Perfumes'
        self.assertEqual(transfers.suggest_gender(row,names)[0],'Femeninos')
        row['short_description'] = 'Tipo: unisex'
        self.assertEqual(transfers.suggest_gender(row,names)[0],'Unisex')
        row['short_description'] = '<p>Género: <strong>femenino</strong></p>'
        self.assertEqual(transfers.suggest_gender(row,names)[0],'Femeninos')
        for brief in ['', 'Sin género', 'Masculino y femenino']:
            row.update(category='Masculinos',short_description=brief)
            self.assertEqual(transfers.suggest_gender(row,names)[0],'')
        row['short_description'] = 'Femenino'
        self.assertEqual(transfers.suggest_gender(row,['Masculinos'])[0],'')

    def test_destination_name_variations_require_review(self):
        row = {'name':'Odyssey Mandarin Sky Elixir - Armaf','brand':'Armaf'}
        existing = [{'id':88,'name':'ARMAF MANDARÍN SKY ELIXIR','brand':'ARMAF'}]
        matches = transfers.possible_target_matches(row,existing,normalize)
        self.assertEqual([m['id'] for m in matches],[88])
        self.assertFalse(transfers.duplicate_status(row,existing,normalize))
        self.assertEqual(transfers.possible_target_matches({'name':'Asad','brand':'Lattafa'},
            [{'id':1,'name':'Asad Zanzibar','brand':'Lattafa'}],normalize),[])
        self.assertEqual(transfers.possible_target_matches(row,[dict(existing[0],brand='Otra marca')],normalize),[])

    def test_incomplete_products_and_bad_prices_are_blocked(self):
        row = {'name':'Test','description':'Completa','pictures':[{'bytes':b'image','mime_type':'image/png'}]}
        transfers.validate_import([row])
        for change in [{'description':'<p> </p>'},{'pictures':[]},{'price':'NaN'},{'price':-1}]:
            with self.assertRaises(transfers.TransferError):
                transfers.validate_import([dict(row,**change)])


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
