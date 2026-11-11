import unittest
from pricing import presentation_values, selected_line_values
import transfers


class Presentations(unittest.TestCase):
    def test_only_filled_options_and_base_price(self):
        values = presentation_values({'p_5':'2.500', 'p_10':'', 'p_100':'45.000'}, 'p')
        self.assertEqual(values['volume_ml'], 100)
        self.assertEqual(values['price'], '45000.00')
        self.assertEqual([o['ml'] for o in values['volume_options']], [5,100])
        self.assertEqual(presentation_values({'p_10':'5000'}, 'p')['volume_ml'],10)

    def test_empty_zero_invalid_and_duplicate_prices(self):
        for raw in ('', '0', '-1', 'no', '1,234'):
            with self.assertRaises(ValueError):
                presentation_values({'p_5': raw}, 'p')
        with self.assertRaises(ValueError):
            selected_line_values({0:1,1:1}, {'presentation_mode':'1','line_option_0_5':'100','line_option_1_5':'200'})

    def test_selection_keeps_options(self):
        values = selected_line_values({0:3}, {'presentation_mode':'1','line_option_0_10':'5.000'})
        self.assertEqual(values[3]['volume_options'],[{'ml':10,'price':'5000.00','stock':12}])

    def test_import_rejects_inconsistent_options(self):
        row = dict(name='Prueba', description='Descripción', pictures=[{'bytes':b'photo','mime_type':'image/jpeg'}],
                   price='5000.00',volume_ml=10,volume_options=[{'ml':10,'price':'5000.00','stock':12}])
        transfers.validate_import([row])
        row['volume_options'].append({'ml':7,'price':'5000.00','stock':12})
        with self.assertRaises(transfers.TransferError):
            transfers.validate_import([row])


class PresentationPersistence(unittest.TestCase):
    def test_choose_save_and_resume_presentations(self):
        import re
        import tempfile
        from pathlib import Path
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session
        from app import create_app, Perfume, Picture, SavedSelection, normalize
        with tempfile.TemporaryDirectory() as folder:
            url = f'sqlite:///{Path(folder)/"master.db"}'
            app = create_app(url)
            engine = create_engine(url)
            with Session(engine) as db, db.begin():
                perfume = Perfume(name='Prueba', name_key=normalize('Prueba'), brand_key='',
                                  description='Texto', short_description='Unisex', category='Árabes')
                db.add(perfume)
                db.flush()
                pid = perfume.id
                db.add(Picture(perfume_id=pid, data=b'foto', mime_type='image/png', digest='foto'))
            client = app.test_client()
            client.get('/')
            with client.session_transaction() as session:
                csrf = session['csrf']
            response = client.post('/preview', data={'csrf':csrf, 'client_list':'Prueba',
                'match_0':pid, 'presentation_mode':'1', 'line_option_0_5':'2500', 'line_option_0_100':'45000'})
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'name="option_{pid}_10" value=""'.encode(), response.data)
            token = re.search(r'name="token" value="([^"]+)"', response.data.decode())[1]
            response = client.post('/selections/save', data={'csrf':csrf, 'token':token, 'client':'Silvina',
                f'option_{pid}_5':'2500', f'option_{pid}_10':'5000'})
            self.assertEqual(response.status_code, 302)
            with Session(engine) as db:
                saved = db.scalar(select(SavedSelection))
                self.assertEqual(saved.items[0]['volume_options'], [
                    {'ml':5,'price':'2500.00','stock':12}, {'ml':10,'price':'5000.00','stock':12}])
                self.assertEqual(saved.items[0]['volume_ml'],5)
                self.assertEqual(saved.items[0]['price'],'2500.00')
            self.assertEqual(client.get(response.location).status_code,200)
            engine.dispose()


class ExistingPresentations(unittest.TestCase):
    def test_merge_preserves_stock_and_untouched_prices(self):
        from pricing import presentation_update
        old = {'volume_ml':100, 'volume_options':[
            {'ml':5,'price':2000,'stock':3,'price_wholesale':1500},
            {'ml':100,'price':40000,'stock':9}]}
        values = presentation_values({'p_5':'2500','p_10':'5000'}, 'p')
        change = presentation_update(old, values['volume_options'])
        self.assertIsNone(change['price'])
        self.assertEqual(change['volume_options'],[
            {'ml':5,'price':2500.0,'stock':3,'price_wholesale':1500},
            {'ml':100,'price':40000,'stock':9}, {'ml':10,'price':5000.0,'stock':12}])
        self.assertEqual(old['volume_options'][0]['price'],2000)

    def test_existing_options_are_reviewed_and_passed_to_send(self):
        import re
        import tempfile
        from unittest.mock import patch
        from app import create_app
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(f'sqlite:///{folder}/master.db')
            client = app.test_client()
            client.get('/')
            with client.session_transaction() as session:
                csrf = session['csrf']
            item = {'id':44,'name':'Asad','brand':'Lattafa','price':40000.0,'is_active':True,
                    'volume_ml':100,'volume_options':[{'ml':100,'price':40000,'stock':7}]}
            with patch('transfers.target_inventory',return_value=[item]), patch('transfers.authorized',return_value=({'import_products':True},None)):
                response = client.post('/preview',data={'csrf':csrf,'target':'silvina', 'client_list':'Asad',
                    'presentation_mode':'1','line_option_0_5':'2500','line_option_0_10':'5000'})
                self.assertEqual(response.status_code,200,response.data.decode())
                self.assertIn(b'5 ml',response.data)
                self.assertIn(b'10 ml',response.data)
                token = re.search(r'name="token" value="([^\"]+)"',response.data.decode())[1]
                with patch('transfers.execute',return_value=([],[],'backup.dump')) as send:
                    response = client.post('/target/confirm',data={'csrf':csrf,'token':token,'confirmation':'silvina'})
                    self.assertEqual(response.status_code,302)
                    change = send.call_args.kwargs['price_updates'][0]
                    self.assertEqual([o['ml'] for o in change['presentation_prices']],[5,10])
                    self.assertEqual(change['volume_options'][0],item['volume_options'][0])
                    self.assertIsNone(change['price'])
