import tempfile
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import create_app, Perfume, Picture
from presentation import rich_description


class Details(unittest.TestCase):
    def test_html_preserves_catalog_format_without_executing_markup(self):
        result = str(rich_description('<p onclick="bad()"><b>Notas</b><br><i>Vainilla</i></p><ul><li>Dulce</li></ul><img src=x onerror="bad()"><script>bad()</script>&lt;script&gt;texto&lt;/script&gt;'))
        self.assertIn('<strong>Notas</strong>',result)
        self.assertIn('<em>Vainilla</em>',result)
        self.assertIn('<ul><li>Dulce</li></ul>',result)
        self.assertNotIn('onclick',result)
        self.assertNotIn('<script>',result)
        self.assertNotIn('<img',result)
        self.assertNotIn('bad()',result)
        self.assertIn('&lt;script&gt;texto&lt;/script&gt;',result)

    def test_local_and_source_details_gallery_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            url = f'sqlite:///{Path(folder)/"test.db"}'
            app = create_app(url)
            app.config['TESTING'] = True
            engine = create_engine(url)
            with Session(engine) as db:
                p = Perfume(name='Asad',brand='Lattafa',category='Árabes',name_key='asad',brand_key='lattafa',
                            description='<p><strong>Descripción principal</strong></p>',
                            short_description='<ul><li>Notas de vainilla</li></ul>')
                db.add(p)
                db.flush()
                pid = p.id
                for i in range(2):
                    db.add(Picture(perfume_id=p.id,mime_type='image/png',data=f'foto-{i}'.encode(),digest=str(i)))
                db.commit()
            client = app.test_client()
            r = client.get(f'/perfume/{pid}')
            self.assertEqual(r.status_code,200)
            self.assertIn(b'<strong>Descripci',r.data)
            self.assertIn(b'<ul><li>Notas de vainilla</li></ul>',r.data)
            self.assertIn(b'Consultar',r.data)
            self.assertIn(b'100 ml',r.data)
            self.assertIn(b'2 fotos',r.data)
            self.assertEqual(client.get(f'/picture/{pid}/1').data,b'foto-1')
            self.assertEqual(client.get(f'/picture/{pid}/8').status_code,404)
            self.assertEqual(client.get('/perfume/98765').status_code,404)
            row = {'name':'Asad','brand':'Lattafa','category':'Árabes','category_labels':['Árabes'],
                   'description':'<b>Principal de origen</b>','short_description':'<p>Notas de origen</p>',
                   'origin':{'source':'catalogo_silvina','product_id':10},
                   'pictures':[{'bytes':b'origen','mime_type':'image/png','width':None,'height':None,'digest':'d'}]}
            with patch('transfers.extract',return_value=[row]) as extract:
                r = client.get('/source/catalogo_silvina/perfume/10')
                self.assertEqual(r.status_code,200)
                self.assertIn(b'<strong>Principal de origen</strong>',r.data)
                image_url = re.search(r'id="active-photo" src="([^"]+)"',r.data.decode())[1]
                self.assertEqual(client.get(image_url).data,b'origen')
                self.assertEqual(app.test_client().get(image_url).status_code,400)
                extract.assert_called_once_with('catalogo_silvina',{10})
                with client.session_transaction() as sess:
                    csrf = sess['csrf']
                r = client.post('/recover/preview',data={'csrf':csrf,'source':'catalogo_silvina','perfume':['10']})
                token = re.search(r'name="token" value="([^"]+)"',r.data.decode())[1]
                self.assertEqual(client.get(f'/recover/detail/{token}/0').status_code,200)
                r = client.post('/recover/confirm',data={'csrf':csrf,'token':token,'row':['0']})
                # Existía el mismo nombre y marca: la vista no creó otro producto.
                self.assertEqual(r.status_code,302)
                row['name'] = 'Asad Bourbon'
                row['pictures'].append(dict(row['pictures'][0],bytes=b'segunda',digest='d2'))
                r = client.post('/recover/preview',data={'csrf':csrf,'source':'catalogo_silvina','perfume':['10']})
                token = re.search(r'name="token" value="([^"]+)"',r.data.decode())[1]
                self.assertEqual(client.post('/recover/confirm',data={'csrf':csrf,'token':token,'row':['0']}).status_code,302)
                from sqlalchemy import select
                with Session(engine) as db:
                    copied = db.scalar(select(Perfume).where(Perfume.name == 'Asad Bourbon'))
                    self.assertEqual(copied.description,row['description'])
                    self.assertEqual(copied.short_description,row['short_description'])
                    photos = list(db.scalars(select(Picture).where(Picture.perfume_id == copied.id).order_by(Picture.id)))
                    self.assertEqual([p.data for p in photos],[b'origen',b'segunda'])
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
