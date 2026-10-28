import re
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import create_app,Perfume,Picture,ImportReport


class ManualExclusions(unittest.TestCase):
    def test_incomplete_can_be_excluded_and_manual_list_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            url=f'sqlite:///{folder}/master.db';app=create_app(url);engine=create_engine(url)
            with Session(engine) as db, db.begin():
                good=Perfume(name='Bueno',brand='Marca',name_key='bueno',brand_key='marca',category='Masculinos',
                             description='<p>Completa</p>',short_description='Masculino')
                bad=Perfume(name='Incompleto',brand='Marca',name_key='incompleto',brand_key='marca',category='Masculinos',
                            description='',short_description='Masculino')
                db.add_all([good,bad]);db.flush()
                db.add(Picture(perfume_id=good.id,mime_type='image/png',data=b'foto',digest='test'))
            client=app.test_client();client.get('/')
            with client.session_transaction() as sess:csrf=sess['csrf']
            def post(path,data):return client.post(path,data=dict(csrf=csrf,**data))
            def token(response):
                self.assertEqual(response.status_code,200,response.data.decode())
                return re.search(r'name="token" value="([^\"]+)"',response.data.decode())[1]
            selected=token(post('/preview',{'client_list':'Bueno $ 100\nIncompleto $ 900\nIncompleto $ 900\nManual previo\nPendiente',
                'match_0':'1','match_1':'2','match_2':'2','manual_3':'1'}))
            with patch('transfers.connect',return_value=nullcontext((None,{}))), \
                 patch('transfers.inspect_target',side_effect=lambda conn,rows,norm:({},rows,[])), \
                 patch('transfers.target_categories',return_value={'Masculinos':1}):
                review=post('/target/preview',{'token':selected,'target':'catalogo_naim'})
                mapping=token(review)
                self.assertIn('Quitar de la selección y cargar a mano',review.data.decode())
                self.assertIn('se requiere descripción y foto',review.data.decode())
            with patch('transfers.authorized',return_value=({'import_products':True},None)):
                blocked=post('/target/map',{'token':mapping,'category_0':'Masculinos','category_1':'Masculinos'})
                self.assertEqual(blocked.status_code,400)
                target=token(post('/target/map',{'token':mapping,'category_0':'Masculinos','manual_1':'1'}))
            with patch('transfers.execute',return_value=(['Bueno'],[],'backup.dump')) as send:
                completed=post('/target/confirm',{'token':target,'confirmation':'catalogo_naim'})
                self.assertEqual(completed.status_code,302)
                self.assertEqual([row['name'] for row in send.call_args.args[1]],['Bueno'])
            with Session(engine) as db:
                report=db.scalar(select(ImportReport)).result
                self.assertEqual(report['manual_items'][0]['name'],'Incompleto')
                self.assertEqual(report['client_summary']['manual_count'],3)
                self.assertEqual(report['client_summary']['pending_count'],1)
                self.assertEqual(report['client_summary']['matched_lines'],1)
                self.assertEqual(report['client_summary']['unique_selected'],1)
            restarted=create_app(url).test_client()
            with patch('transfers.connect') as remote:
                result=restarted.get(completed.location)
                self.assertIn('Quitados de este envío para cargar a mano: 1',result.data.decode())
                text=restarted.get(completed.location+'/download').data.decode()
                self.assertIn('Incompleto $ 900 — Cargar a mano',text)
                self.assertIn('Manual previo — Cargar a mano',text)
                self.assertIn('Pendiente — Pendiente de revisar',text)
                remote.assert_not_called()
            # También se puede guardar una lista enteramente manual sin enviar ni hacer backup.
            with patch('transfers.authorized',return_value=({'import_products':True},None)):
                manual_only=token(post('/target/map',{'token':mapping,'manual_0':'1','manual_1':'1'}))
            with patch('transfers.execute') as send:
                response=post('/target/confirm',{'token':manual_only,'confirmation':'catalogo_naim'})
                self.assertEqual(response.status_code,302)
                send.assert_not_called()
            engine.dispose()
