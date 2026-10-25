from contextlib import contextmanager
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine,MetaData,Table,Column,Integer,String,LargeBinary,JSON,event,select
from sqlalchemy.orm import Session
from app import create_app,Perfume,Picture,ContentIssue
import transfers


class MissingImages(unittest.TestCase):
    def test_missing_primary_does_not_block_healthy_products(self):
        engine=create_engine('sqlite:///:memory:')
        meta=MetaData()
        category=Table('category',meta,Column('id',Integer,primary_key=True),Column('name',String))
        product=Table('product',meta,Column('id',Integer,primary_key=True),Column('name',String),
                      Column('brand',String),Column('description',String),Column('short_description',String),
                      Column('category_id',Integer),Column('flavor_catalog',JSON),Column('image_url',String))
        image=Table('product_images',meta,Column('id',Integer,primary_key=True),Column('product_id',Integer),
                    Column('mime_type',String),Column('bytes',LargeBinary),Column('width',Integer),Column('height',Integer))
        meta.create_all(engine)
        with engine.begin() as conn:
            conn.execute(category.insert(),{'id':1,'name':'Perfumes Árabes'})
            for i,main in [(1,'/public/img/999'),(2,'/public/img/888'),(3,'https://example.invalid/foto.jpg'),(4,'/public/img/40')]:
                conn.execute(product.insert(),{'id':i,'name':f'Perfume {i}','brand':'Marca','description':'Principal',
                    'short_description':'Notas','category_id':1,'flavor_catalog':[],'image_url':main})
            conn.execute(image.insert(),[{'id':11,'product_id':1,'mime_type':'image/png','bytes':b'alternativa'},
                {'id':31,'product_id':3,'mime_type':'image/png','bytes':b'asociada'},
                {'id':40,'product_id':None,'mime_type':'image/png','bytes':b'principal'}])
        @contextmanager
        def local_read(*args,**kwargs):
            with engine.connect() as conn:
                yield conn,{}
        queries=[]
        event.listen(engine,'before_cursor_execute',lambda conn,cursor,stmt,params,ctx,many:queries.append(stmt))
        with patch('transfers.connect',local_read),patch('transfers.schema',return_value={'product':product,'category':category,'product_images':image}):
            rows=transfers.extract('test',{1,2,3,4})
        self.assertEqual(len(rows),4)
        self.assertEqual([x['pictures'][0]['bytes'] if x['pictures'] else None for x in rows],
                         [b'alternativa',None,b'asociada',b'principal'])
        self.assertTrue(rows[0]['image_warnings'])
        self.assertTrue(rows[1]['image_warnings'])
        self.assertTrue(rows[2]['image_warnings'])
        self.assertEqual(rows[3]['image_warnings'],[])
        self.assertTrue(all(x['description']=='Principal' and x['short_description']=='Notas' for x in rows))
        self.assertTrue(all(q.lstrip().upper().startswith('SELECT') for q in queries))
        self.assertEqual(len(queries),3)
        engine.dispose()

    def test_explicit_no_photo_choice_saves_warning_and_healthy_photos(self):
        with tempfile.TemporaryDirectory() as folder:
            url=f'sqlite:///{Path(folder)/"master.db"}'
            app=create_app(url); client=app.test_client(); client.get('/')
            with client.session_transaction() as session:
                csrf=session['csrf']
            rows=[{'name':'Sin foto','brand':'Marca','category':'Perfumes','description':'Principal','short_description':'Notas',
                   'pictures':[],'image_warnings':['Falta la foto principal.'],'origin':{'source':'test','product_id':1}},
                  {'name':'Con foto','brand':'Marca','category':'Perfumes','description':'Principal','short_description':'Notas',
                   'pictures':[{'bytes':b'valida','mime_type':'image/png','width':None,'height':None,'digest':'d'}],
                   'origin':{'source':'test','product_id':2}}]
            with patch('transfers.source_products',return_value=[dict(x,id=x['origin']['product_id']) for x in rows]):
                response=client.post('/recover/analyze',data={'csrf':csrf,'source':'test'})
            token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            with patch('transfers.extract',return_value=rows):
                response=client.post('/recover/analyzed',data={'csrf':csrf,'token':token,'perfume':['1','2']})
            html=response.data.decode()
            no_photo=re.search(r'<input type="checkbox" name="row" value="0"[^>]*>',html)[0]
            self.assertNotIn('checked',no_photo)
            self.assertIn('SIN FOTOS',html)
            token=re.search(r'name="token" value="([^"]+)"',html)[1]
            # Se puede guardar el sano junto al incompleto solo si se elige este explícitamente.
            self.assertEqual(client.post('/recover/confirm',data={'csrf':csrf,'token':token,'row':['0','1']}).status_code,302)
            engine=create_engine(url)
            with Session(engine) as db:
                perfumes=list(db.scalars(select(Perfume)))
                self.assertEqual(len(perfumes),2)
                self.assertEqual(len(list(db.scalars(select(Picture)))),1)
                self.assertEqual(len(list(db.scalars(select(ContentIssue)))),2)
                missing=next(x for x in perfumes if x.name=='Sin foto')
                self.assertIn(b'Falta la foto principal.',client.get(f'/perfume/{missing.id}').data)
            engine.dispose()


if __name__=='__main__':
    unittest.main()
