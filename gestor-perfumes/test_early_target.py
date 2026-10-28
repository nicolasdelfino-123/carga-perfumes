import re
import tempfile
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import create_app,Perfume,SavedSelection


class EarlyTarget(unittest.TestCase):
    def test_existing_before_prices_and_only_new_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            url=f'sqlite:///{folder}/master.db'
            app=create_app(url);engine=create_engine(url)
            with Session(engine) as db, db.begin():
                for name in ['Asad','Nuevo']:
                    db.add(Perfume(name=name,brand='Lattafa',name_key=name.lower(),brand_key='lattafa',
                                   category='Masculinos',description='Descripción'))
            client=app.test_client();client.get('/')
            with client.session_transaction() as sess:csrf=sess['csrf']
            inventory=[{'id':44,'name':'Asad','brand':'Lattafa','price':40000.0}]
            with patch('transfers.connections',return_value={'catalogo_naim':{'roles':['target'],'import_products':True}}), \
                 patch('transfers.target_inventory',return_value=inventory) as remote:
                response=client.post('/',data={'csrf':csrf,'target':'catalogo_naim','list':'Asad\nNuevo\nManual'})
                html=response.data.decode()
                self.assertEqual(response.status_code,200)
                self.assertIn('Ya cargado en catalogo_naim',html)
                self.assertIn('data-existing="true"',html)
                self.assertIn('Precio actual:',html)
                remote.assert_called_with('catalogo_naim')
                modal=client.get('/api/master/search?q=Asad&target=catalogo_naim').get_json()
                self.assertTrue(modal['items'][0]['target_existing'])
                response=client.post('/preview',data={'csrf':csrf,'target':'catalogo_naim',
                    'client_list':'Asad\nNuevo\nManual','match_0':'1','match_1':'2',
                    'line_price_0':'45.000','line_price_1':'50000','manual_2':'1'})
                self.assertEqual(response.status_code,200,response.data.decode())
                token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
                saved=client.post('/selections/save',data={'csrf':csrf,'token':token,'client':'Naim'})
                self.assertEqual(saved.status_code,302)
            with Session(engine) as db:
                items=db.scalar(select(SavedSelection)).items
                self.assertEqual([item['name'] for item in items],['Nuevo'])
                self.assertEqual(items[0]['price'],'50000.00')
                self.assertEqual(items[0]['target_alias'],'catalogo_naim')
                self.assertEqual(items[0]['price_updates'],[{'id':44,'name':'Asad','old_price':'40000.0','price':'45000.00','activate':True}])
                summary=items[0]['client_summary']
                self.assertEqual(summary['existing_count'],1)
                self.assertEqual(summary['manual_count'],1)
                self.assertEqual(summary['outside_count'],1)
                self.assertEqual(summary['existing'][0]['raw'],'Asad')
            engine.dispose()

    def test_existing_price_blank_is_preserved_and_explicit_price_is_reviewed(self):
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(f'sqlite:///{folder}/master.db');client=app.test_client();client.get('/')
            with client.session_transaction() as sess:csrf=sess['csrf']
            inventory=[{'id':44,'name':'Asad','brand':'Lattafa','price':40000.0}]
            with patch('transfers.target_inventory',return_value=inventory), \
                 patch('transfers.authorized',return_value=({'import_products':True},None)):
                blank=client.post('/preview',data={'csrf':csrf,'target':'catalogo_naim','client_list':'Asad','line_price_0':''})
                self.assertEqual(blank.status_code,400)
                for price in ['50.000','0']:
                    reviewed=client.post('/preview',data={'csrf':csrf,'target':'catalogo_naim',
                        'client_list':'Asad','line_price_0':price})
                    self.assertEqual(reviewed.status_code,200,reviewed.data.decode())
                    self.assertIn('Productos existentes: precio y activación',reviewed.data.decode())
                    token=re.search(r'name="token" value="([^"]+)"',reviewed.data.decode())[1]
                    with patch('transfers.execute',return_value=([],[],'backup.dump')) as send:
                        result=client.post('/target/confirm',data={'csrf':csrf,'token':token,'confirmation':'catalogo_naim'})
                        self.assertEqual(result.status_code,302)
                        changes=send.call_args.kwargs['price_updates']
                        self.assertEqual(changes[0]['id'],44)
                        self.assertTrue(changes[0]['activate'])
                        self.assertEqual(changes[0]['price'],'50000.00' if price!='0' else '0.00')
                        report=client.get(result.location)
                        self.assertIn('Existentes actualizados: 1',report.data.decode())

    def test_inactive_existing_is_activated_with_blank_price(self):
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(f'sqlite:///{folder}/master.db');client=app.test_client();client.get('/')
            with client.session_transaction() as sess:csrf=sess['csrf']
            inventory=[{'id':44,'name':'Asad','brand':'Lattafa','price':40000.0,'is_active':False}]
            with patch('transfers.target_inventory',return_value=inventory), \
                 patch('transfers.authorized',return_value=({'import_products':True},None)):
                reviewed=client.post('/preview',data={'csrf':csrf,'target':'catalogo_naim','client_list':'Asad','line_price_0':''})
                self.assertEqual(reviewed.status_code,200,reviewed.data.decode())
                self.assertIn('Conservar precio actual',reviewed.data.decode())
                self.assertIn('Quedará activo',reviewed.data.decode())
                token=re.search(r'name="token" value="([^"]+)"',reviewed.data.decode())[1]
                with patch('transfers.execute',return_value=([],[],'backup.dump')) as send:
                    response=client.post('/target/confirm',data={'csrf':csrf,'token':token,'confirmation':'catalogo_naim'})
                    self.assertEqual(response.status_code,302)
                    change=send.call_args.kwargs['price_updates'][0]
                    self.assertIsNone(change['price'])
                    self.assertTrue(change['activate'])

    def test_failed_destination_query_is_not_treated_as_empty_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(f'sqlite:///{folder}/master.db')
            client=app.test_client();client.get('/')
            with client.session_transaction() as sess:csrf=sess['csrf']
            import transfers
            with patch('transfers.target_inventory',side_effect=transfers.TransferError('Túnel cerrado')):
                response=client.post('/',data={'csrf':csrf,'target':'catalogo_naim','list':'Asad'})
                self.assertEqual(response.status_code,400)
                self.assertIn('Túnel cerrado',response.data.decode())
