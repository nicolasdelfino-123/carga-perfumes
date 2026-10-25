from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import create_app,Perfume,Picture,SavedSelection
from pricing import retail_price,selected_line_values


class SavedSelections(unittest.TestCase):
    def test_prices_snapshots_and_photos_survive_restart_without_tunnel(self):
        with tempfile.TemporaryDirectory() as folder:
            url=f'sqlite:///{Path(folder)/"master.db"}'
            app=create_app(url)
            engine=create_engine(url)
            with Session(engine) as db:
                perfume=Perfume(name='Asad',brand='Lattafa',name_key='asad',brand_key='lattafa',
                    description='<b>Original principal</b>',short_description='Notas originales',category='Árabes')
                db.add(perfume);db.flush();pid=perfume.id
                db.add(Picture(perfume_id=pid,mime_type='image/png',data=b'foto-original',digest='d'))
                db.commit()
            client=app.test_client();client.get('/')
            with client.session_transaction() as session:
                csrf=session['csrf']
            original='Lattafa Asad $ 45.000,50'
            response=client.post('/preview',data={'csrf':csrf,'perfume':[str(pid)],'client_list':original})
            self.assertEqual(response.status_code,200)
            self.assertIn(f'name="volume_{pid}" value="100"'.encode(),response.data)
            self.assertIn(original.encode(),response.data)
            token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
            response=client.post('/selections/save',data={'csrf':csrf,'token':token,'client':'Nuevo cliente',
                f'price_{pid}':'-10',f'volume_{pid}':'100'})
            self.assertEqual(response.status_code,400)
            data={'csrf':csrf,'token':token,'client':'Nuevo cliente','name':'Carga inicial',
                  f'price_{pid}':'45.000,50',f'volume_{pid}':'100'}
            with patch('transfers.connect') as remote:
                response=client.post('/selections/save',data=data)
                self.assertEqual(response.status_code,302)
                self.assertEqual(client.post('/selections/save',data=data).status_code,302)
                remote.assert_not_called()
            location=response.location
            with Session(engine) as db:
                saved=list(db.scalars(select(SavedSelection)))
                self.assertEqual(len(saved),1)
                snapshot=saved[0].items[0]
                self.assertEqual((snapshot['price'],snapshot['volume_ml'],snapshot['stock']),('45000.50',100,11))
                self.assertEqual(snapshot['client_list'],original)
                self.assertEqual(snapshot['description'],'<b>Original principal</b>')
                db.get(Perfume,pid).description='Texto modificado después'
                db.commit()
            # Nueva instancia sin las vistas previas en memoria ni cookies anteriores.
            restarted=create_app(url).test_client()
            with patch('transfers.connect') as remote:
                response=restarted.get(location)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'$ 45.000,50',response.data)
                self.assertIn(b'100 ml',response.data)
                self.assertEqual(restarted.get('/selections').status_code,200)
                detail=restarted.get(location+'/perfume/0')
                self.assertIn(b'<strong>Original principal</strong>',detail.data)
                self.assertNotIn(b'Texto modificado',detail.data)
                photo=re.search(r'id="active-photo" src="([^"]+)"',detail.data.decode())[1]
                self.assertEqual(restarted.get(photo).data,b'foto-original')
                with restarted.session_transaction() as session:
                    new_csrf=session['csrf']
                prepare=restarted.post(location+'/prepare',data={'csrf':new_csrf})
                self.assertEqual(prepare.status_code,200)
                self.assertIn(f'name="price_{pid}" value="45000.50"'.encode(),prepare.data)
                remote.assert_not_called()
            next_token=re.search(r'name="token" value="([^"]+)"',prepare.data.decode())[1]
            response=restarted.post('/selections/save',data={'csrf':new_csrf,'token':next_token,
                'client':'Nuevo cliente','name':'Carga corregida',f'price_{pid}':'50000',f'volume_{pid}':'100'})
            self.assertEqual(response.status_code,302)
            with Session(engine) as db:
                saved=list(db.scalars(select(SavedSelection).order_by(SavedSelection.id)))
                self.assertEqual([s.items[0]['price'] for s in saved],['45000.50','50000.00'])
                # Si el binario cambia, no se prepara silenciosamente otra foto.
                image=db.scalar(select(Picture));image.data=b'otra-foto';db.commit()
            self.assertEqual(restarted.post(location+'/prepare',data={'csrf':new_csrf}).status_code,400)
            engine.dispose()

    def test_retail_formats_and_invalid_values(self):
        for raw,expected in [('45000','45000.00'),('45.000','45000.00'),('$ 45.000,50','45000.50'),
                             ('45000.50','45000.50'),('','0.00'),('0','0.00')]:
            self.assertEqual(retail_price(raw),expected)
        for raw in ['-10','NaN','1e5','1.2345','1,2,3']:
            with self.assertRaises(ValueError):
                retail_price(raw)

    def test_inline_prices_follow_line_and_conflicts_are_not_silently_lost(self):
        values=selected_line_values({0:10,1:10,2:20},{'line_price_0':'45.000',
            'line_volume_0':'100','line_price_1':'','line_volume_1':'100',
            'line_price_2':'$ 60.000,50','line_volume_2':'150','line_price_3':'inválido'})
        self.assertEqual(values,{10:{'price':'45000.00','volume_ml':100},20:{'price':'60000.50','volume_ml':150}})
        with self.assertRaises(ValueError):
            selected_line_values({0:10,1:10},{'line_price_0':'45000','line_price_1':'50000'})
        with self.assertRaises(ValueError):
            selected_line_values({0:10,1:10},{'line_volume_0':'100','line_volume_1':'150'})
        for data in [{'line_price_0':'-10'},{'line_volume_0':'0'},{'line_volume_0':''}]:
            with self.assertRaises(ValueError):
                selected_line_values({0:10},data)


if __name__=='__main__':
    unittest.main()
