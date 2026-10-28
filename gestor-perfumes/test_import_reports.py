import re
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import create_app, Perfume, Picture


class ImportReports(unittest.TestCase):
    def test_report_keeps_manual_pending_existing_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            url = f'sqlite:///{folder}/master.db'
            app = create_app(url)
            engine = create_engine(url)
            with Session(engine) as db, db.begin():
                for name in ['Asad','Existing']:
                    p = Perfume(name=name,brand='Lattafa',name_key=name.lower(),brand_key='lattafa',
                                category='Masculinos',description='Descripción',origins=[{'source':'catalogo_silvina','product_id':114}])
                    db.add(p); db.flush()
                    db.add(Picture(perfume_id=p.id,mime_type='image/png',data=b'image',digest='test'))
            client = app.test_client(); client.get('/')
            with client.session_transaction() as session:
                csrf = session['csrf']
            def post(path, data):
                return client.post(path,data=dict(csrf=csrf,**data))
            def token(response):
                self.assertEqual(response.status_code,200,response.data.decode())
                return re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            selected = token(post('/preview',{'client_list':'Asad $ 100\nExisting\nManual $ 200\nPendiente',
                'match_0':'1','match_1':'2','manual_2':'1'}))
            saved = post('/selections/save',{'token':selected,'client':'Naim'})
            self.assertEqual(saved.status_code,302)
            with patch('transfers.connections',return_value={'catalogo_naim':{'roles':['target'],'import_products':True}}):
                html = client.get(saved.location).data.decode()
                self.assertIn('Importar esta selección',html)
                self.assertIn('catalogo_naim',html)
                self.assertIn(saved.location+'/import',html)
            def inspect(conn, rows, normalize):
                return {}, [dict(rows[0],target_matches=[{'id':99,'name':'Lattafa Asad EDP','brand':'Lattafa'}])], [rows[1]['name']]
            with patch('transfers.connect',return_value=nullcontext((None,{}))), \
                 patch('transfers.inspect_target',side_effect=inspect), \
                 patch('transfers.target_categories',return_value={'Masculinos':1}), \
                 patch('transfers.existing_product_changes',return_value=[]):
                mapping = token(post(saved.location+'/import',{'target':'catalogo_naim'}))
            with patch('transfers.authorized',return_value=({'import_products':True},None)):
                self.assertEqual(post('/target/map',{'token':mapping,'category_0':'Masculinos'}).status_code,400)
                target = token(post('/target/map',{'token':mapping,'category_0':'Masculinos','duplicate_0':'new'}))
            with patch('transfers.execute',return_value=(['Asad'],[],'/private/backup.dump')):
                response = post('/target/confirm',{'token':target,'confirmation':'catalogo_naim'})
                self.assertEqual(response.status_code,302,response.data.decode())
            restarted = create_app(url).test_client()
            with patch('transfers.connect') as remote:
                report = restarted.get(response.location)
                self.assertEqual(report.status_code,200)
                html = report.data.decode()
                for text in ['Asad','Existing','Manual $ 200','Pendiente','Cargar a mano','Pendiente de revisar']:
                    self.assertIn(text,html)
                download = restarted.get(response.location+'/download')
                self.assertEqual(download.status_code,200)
                self.assertIn('Manual $ 200 — Cargar a mano',download.data.decode())
                self.assertIn('Pendiente — Pendiente de revisar',download.data.decode())
                self.assertIn(response.location,restarted.get('/imports').data.decode())
                self.assertEqual(restarted.get('/imports/999').status_code,404)
                remote.assert_not_called()
            destination = {'id':117,'name':'Asad','brand':'Lattafa','category':'Masculinos',
                           'description':'<p><strong>Intenso ✨</strong><br>Texto</p>',
                           'short_description':'<p><strong>Masculino 🖤</strong></p>',
                           'price':45000,'volume_ml':100,'stock':12,'is_active':True,'pictures':[]}
            with patch('transfers.imported_product',return_value=destination) as read:
                preview = restarted.get(response.location+'/product/0')
                self.assertEqual(preview.status_code,200)
                html = preview.data.decode()
                self.assertIn('<strong>Intenso ✨</strong>',html)
                self.assertIn('<strong>Masculino 🖤</strong>',html)
                self.assertIn('Producto ID 117',html)
                self.assertIn('catalogo_silvina · ID 114',html)
                self.assertNotIn('Recuperar desde una base',html)
                read.assert_called_once()
                self.assertEqual(read.call_args.args[:2],('catalogo_naim','Asad'))
                self.assertEqual(restarted.get(response.location+'/product/99').status_code,404)
            self.assertIn('catalogo_silvina · ID 114',report.data.decode())
            self.assertIn('Ver en catálogo',report.data.decode())
            self.assertIn('Ver historial de importaciones',report.data.decode())
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
