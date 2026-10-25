import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import create_app,Perfume,Picture,SavedSelection,candidates,normalize


class MasterSearch(unittest.TestCase):
    def test_short_names_typos_and_distinct_editions(self):
        perfumes=[]
        for i,name in enumerate(['Liquid Brun Limited Edition 150 ML – French Avenue','Liquid Brun – French Avenue','Asad Bourbon'],1):
            brand='French Avenue' if i<3 else 'Lattafa'
            perfumes.append(Perfume(id=i,name=name,brand=brand,name_key=normalize(name),brand_key=normalize(brand)))
        self.assertEqual([p.id for p,_,_ in candidates('liquid brun',perfumes)][:2],[2,1])
        self.assertIn(2,[p.id for p,_,_ in candidates('liquid brum',perfumes)])
        self.assertIn(2,[p.id for p,_,_ in candidates('liquidbrun',perfumes)])
        self.assertEqual(candidates('',perfumes),[])

    def test_modal_search_manual_choice_prices_and_local_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            url=f'sqlite:///{Path(folder)/"master.db"}'
            app=create_app(url)
            engine=create_engine(url)
            with Session(engine) as db:
                p=Perfume(name='Liquid Brun – French Avenue',brand='French Avenue',name_key=normalize('Liquid Brun – French Avenue'),
                    brand_key='french avenue',category='Árabes',description='Principal',short_description='Notas')
                db.add(p);db.flush();pid=p.id
                db.add(Picture(perfume_id=pid,data=b'foto',mime_type='image/png',digest='d'))
                db.commit()
            client=app.test_client();client.get('/')
            with client.session_transaction() as session:
                csrf=session['csrf']
            with patch('transfers.connect') as remote:
                result=client.get('/api/master/search?q=liquid%20brum')
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json['total_master'],1)
                item=result.json['items'][0]
                self.assertEqual(item['id'],pid)
                self.assertEqual(item['photo_count'],1)
                self.assertTrue(item['image_url'])
                self.assertEqual(client.get('/api/master/search?q=').json['matching_count'],1)
                original='Nombre desconocido $ 35.000'
                page=client.post('/',data={'csrf':csrf,'list':original})
                self.assertIn(b'Buscar en maestra',page.data)
                self.assertIn(b'id="master-dialog"',page.data)
                self.assertIn(b'data-line="0"',page.data)
                self.assertIn(b'name="line_price_0"',page.data)
                self.assertIn(b'name="line_volume_0" class="line-volume" value="100"',page.data)
                with client.get('/static/master-search.js') as script_response:
                    self.assertEqual(script_response.status_code,200)
                # El usuario elige desde el modal, aunque la sugerencia inicial no exista.
                preview=client.post('/preview',data={'csrf':csrf,'client_list':original,'match_0':str(pid),
                    'line_price_0':'35.000,50','line_volume_0':'100','line_price_99':'ignorar'})
                self.assertEqual(preview.status_code,200)
                self.assertIn(original.encode(),preview.data)
                self.assertIn('Liquid Brun – French Avenue'.encode(),preview.data)
                self.assertIn(f'name="price_{pid}" value="35000.50"'.encode(),preview.data)
                token=re.search(r'name="token" value="([^"]+)"',preview.data.decode())[1]
                save=client.post('/selections/save',data={'csrf':csrf,'token':token,'client':'Cliente de prueba'})
                self.assertEqual(save.status_code,302)
                remote.assert_not_called()
            with Session(engine) as db:
                saved=db.scalar(select(SavedSelection))
                self.assertEqual(saved.items[0]['client_lines'],[original])
                self.assertEqual(saved.items[0]['price'],'35000.50')
                self.assertEqual(saved.items[0]['volume_ml'],100)
            self.assertEqual(client.post('/preview',data={'csrf':csrf,'client_list':original,'match_0':'999999'}).status_code,400)
            self.assertEqual(client.post('/preview',data={'csrf':csrf,'match_0':'invalid'}).status_code,400)
            engine.dispose()


if __name__=='__main__':
    unittest.main()
