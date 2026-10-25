from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import create_app,Perfume,SavedSelection
from client_list import make_client_summary


class ClientCounts(unittest.TestCase):
    def test_repeated_matches_manual_pending_and_invalid_indices(self):
        lines=[('Asad','Asad'),('Asad 100ml','Asad 100ml'),('Otro','Otro'),('Pendiente','Pendiente')]
        summary=make_client_summary(lines,{0:11,1:11,99:15},{1,2,99},1)
        self.assertEqual({key:summary[key] for key in ('total_requested','matched_lines','unique_selected',
            'manual_count','pending_count','outside_count','repeated_lines')},
            {'total_requested':4,'matched_lines':2,'unique_selected':1,'manual_count':1,
             'pending_count':1,'outside_count':2,'repeated_lines':1})
        self.assertEqual([(x['index'],x['status']) for x in summary['outside']],[(2,'manual'),(3,'pending')])

    def test_summary_is_preserved_when_saved_and_reopened_without_vps(self):
        with tempfile.TemporaryDirectory() as folder:
            url=f'sqlite:///{Path(folder)/"master.db"}'
            app=create_app(url);engine=create_engine(url)
            with Session(engine) as db:
                p=Perfume(name='Asad',brand='Lattafa',name_key='asad',brand_key='lattafa',category='Árabes')
                db.add(p);db.flush();pid=p.id;db.commit()
            client=app.test_client();client.get('/')
            with client.session_transaction() as session:
                csrf=session['csrf']
            original='Asad $ 45000\nAsad 100 ml $ 45000\nPerfume manual $ 50000\nSin revisar $ 30000'
            searched=client.post('/',data={'csrf':csrf,'list':original})
            self.assertRegex(searched.data,rb'id="client-total-count"\s*>4')
            self.assertIn(b'name="manual_2"',searched.data)
            data={'csrf':csrf,'client_list':original,'match_0':str(pid),'match_1':str(pid),'manual_2':'1'}
            with patch('transfers.connect') as remote:
                response=client.post('/preview',data=data)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'Pedidos en tu lista',response.data)
                token=re.search(r'name="token" value="([^"]+)"',response.data.decode())[1]
                response=client.post('/selections/save',data={'csrf':csrf,'token':token,'client':'Cliente',f'price_{pid}':'45000'})
                self.assertEqual(response.status_code,302)
                location=response.location
                restarted=create_app(url).test_client()
                response=restarted.get(location)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'Fuera de esta selecci',response.data)
                self.assertIn(b'Perfume manual $ 50000',response.data)
                self.assertIn(b'Cargar a mano',response.data)
                remote.assert_not_called()
            with Session(engine) as db:
                selection=db.scalar(select(SavedSelection))
                self.assertEqual(len(selection.items),1)
                summary=selection.items[0]['client_summary']
                self.assertEqual((summary['total_requested'],summary['matched_lines'],summary['manual_count'],
                                  summary['pending_count'],summary['outside_count'],summary['repeated_lines']),
                                 (4,2,1,1,2,1))
            engine.dispose()


if __name__=='__main__':
    unittest.main()
