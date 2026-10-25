import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import re

from sqlalchemy import create_engine
from app import normalize,create_app,Perfume,ComparisonSkip
from sqlalchemy.orm import Session
from library import classify,backup_library,comparison_groups


class LibraryTests(unittest.TestCase):
    def test_bulk_analysis_skips_duplicates_and_saves_selected_new(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'master.db'
            app = create_app(f'sqlite:///{path}')
            engine = create_engine(f'sqlite:///{path}')
            with Session(engine) as db:
                db.add(Perfume(name='Asad',brand='Lattafa',name_key='asad',brand_key='lattafa'))
                db.commit()
            client = app.test_client()
            client.get('/')
            with client.session_transaction() as sess:
                csrf = sess['csrf']
            rows = [{'id':1,'name':'Asad 100 ml','brand':'Lattafa'},
                    {'id':2,'name':'Asad Bourbon','brand':'Lattafa'},
                    {'id':3,'name':'Asadd','brand':'Lattafa'}]
            with patch('transfers.source_products',return_value=rows):
                response = client.post('/recover/analyze',data={'csrf':csrf,'source':'test'})
            self.assertEqual(response.status_code,200)
            self.assertIn(b'<strong>1</strong><span>Nuevos</span>',response.data)
            self.assertIn(b'id="top-duplicate-count">1</strong><span>Repetidos excluidos</span>',response.data)
            self.assertIn(b'id="visible-count">4</strong>',response.data)
            token = re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            self.assertEqual(client.post('/recover/analyzed',data={'csrf':csrf,'token':token,'perfume':['1']}).status_code,400)
            full = dict(rows[1],category='Árabes',description='Principal',short_description='Notas',
                        pictures=[],origin={'source':'test','product_id':2})
            with patch('transfers.extract',return_value=[full]):
                response = client.post('/recover/analyzed',data={'csrf':csrf,'token':token,'perfume':['2']})
            self.assertEqual(response.status_code,200)
            token = re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            self.assertEqual(client.post('/recover/confirm',data={'csrf':csrf,'token':token,'row':['0']}).status_code,302)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM master_perfume').fetchone()[0],2)
            self.assertEqual(len(list((path.parent/'library-backups').glob('*.sqlite'))),2)
            engine.dispose()

    def test_pairs_keep_all_master_rows_and_do_not_confuse_ids(self):
        master = [{'id':1,'name':'Asad','brand':'Lattafa'}, {'id':2,'name':'Yara','brand':'Lattafa'}]
        rows = [{'id':1,'name':'Asad 100 ml','brand':'Lattafa'}, {'id':2,'name':'Asadd','brand':'Lattafa'}]
        groups = comparison_groups(classify(rows,master,normalize),master,normalize)
        self.assertEqual(sum(len(g['rows']) for g in groups),4)
        asad = next(g for g in groups if g['rows'][0]['row']['name']=='Asad')
        self.assertEqual([(x['kind'],x['row']['id']) for x in asad['rows']],
                         [('master',1),('source',1),('source',2)])
        self.assertTrue(any(g['rows'][0]['row']['name']=='Yara' for g in groups))

    def test_same_decision_is_local_persistent_and_reversible(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'master.db'
            app=create_app(f'sqlite:///{path}')
            engine=create_engine(f'sqlite:///{path}')
            with Session(engine) as db:
                db.add(Perfume(name='Asad',brand='Lattafa',name_key='asad',brand_key='lattafa'))
                db.commit()
            client=app.test_client()
            client.get('/')
            with client.session_transaction() as sess:
                csrf=sess['csrf']
            rows=[{'id':22,'name':'Asadd','brand':'Lattafa'}]
            with patch('transfers.source_products',return_value=rows):
                response=client.post('/recover/analyze',data={'csrf':csrf,'source':'gleidys'})
            token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            with patch('transfers.extract') as extract:
                response=client.post('/recover/analyzed',data={'csrf':csrf,'token':token,'decision_22':'same'})
                extract.assert_not_called()
            self.assertEqual(response.status_code,200)
            self.assertIn(b'MARCADO COMO EL MISMO',response.data)
            with Session(engine) as db:
                self.assertEqual(len(list(db.query(ComparisonSkip))),1)
                self.assertEqual(len(list(db.query(Perfume))),1)
            token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            response=client.post('/recover/decision/reset',data={'csrf':csrf,'token':token,'source_id':'22'})
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Pendiente de revisar',response.data)
            with Session(engine) as db:
                self.assertEqual(len(list(db.query(ComparisonSkip))),0)
                self.assertEqual(len(list(db.query(Perfume))),1)
            engine.dispose()

    def test_different_decision_requests_only_selected_variant(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'master.db'
            app=create_app(f'sqlite:///{path}')
            engine=create_engine(f'sqlite:///{path}')
            with Session(engine) as db:
                db.add(Perfume(name='Asad',brand='Lattafa',name_key='asad',brand_key='lattafa'))
                db.commit()
            client=app.test_client(); client.get('/')
            with client.session_transaction() as sess:
                csrf=sess['csrf']
            row={'id':22,'name':'Asadd','brand':'Lattafa','category':'Árabes','pictures':[],
                 'origin':{'source':'gleidys','product_id':22},'description':'Descripción','short_description':'Notas'}
            with patch('transfers.source_products',return_value=[row]):
                response=client.post('/recover/analyze',data={'csrf':csrf,'source':'gleidys'})
            token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            with patch('transfers.extract',return_value=[row]) as extract:
                response=client.post('/recover/analyzed',data={'csrf':csrf,'token':token,'decision_22':'new'})
                extract.assert_called_once_with('gleidys',{22})
            self.assertEqual(response.status_code,200)
            token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            self.assertEqual(client.post('/recover/confirm',data={'csrf':csrf,'token':token,'row':['0']}).status_code,302)
            with Session(engine) as db:
                self.assertEqual(len(list(db.query(Perfume))),2)
            engine.dispose()

    def test_exact_sizes_variants_and_ambiguous(self):
        existing = [{'name':'Asad','brand':'Lattafa'}]
        rows = [{'id':1,'name':'LATTAFA ASAD 100ml','brand':'Láttafa'},
                {'id':2,'name':'Asad Bourbon','brand':'Lattafa'},
                {'id':3,'name':'Asad','brand':''},
                {'id':4,'name':'Asad Zanzibar','brand':'Lattafa'},
                {'id':5,'name':'Asad Bourbon 50 ml','brand':'Lattafa'},
                {'id':6,'name':'Asad Zanzíbar','brand':'Lattafa'}]
        self.assertEqual([x['status'] for x in classify(rows,existing,normalize)],
                         ['duplicate','new','review','new','duplicate','duplicate'])
        rows = [{'name':'Club de Nuit Intence','brand':'Armaf'}]
        self.assertEqual(classify(rows,[{'name':'Club de Nuit Intense','brand':'Armaf'}],normalize)[0]['status'],'review')

    def test_backup_can_be_read_and_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'master.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE perfumes(name text)')
                conn.execute("INSERT INTO perfumes VALUES ('Asad')")
            engine = create_engine(f'sqlite:///{path}')
            target = backup_library(engine)
            self.assertEqual(Path(target).stat().st_mode & 0o777,0o600)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute('SELECT name FROM perfumes').fetchall(),[('Asad',)])
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
