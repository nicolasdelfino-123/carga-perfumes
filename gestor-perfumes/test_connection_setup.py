import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock
from contextlib import redirect_stdout
import io

from conectar_silvina import local_url, private_write, main
import transfers


class ConnectionSetup(unittest.TestCase):
    def test_gleidys_preserves_silvina_and_registers_only_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            instance = root/'instance'
            instance.mkdir()
            registry = instance/'connections.json'
            silvina = {'database':'catalogo_silvina','url_file':'/private/silvina.url','roles':['source']}
            private_write(registry,json.dumps({'catalogo_silvina':silvina}))
            remote = MagicMock(returncode=0,stdout=b'postgresql://gleidys_user:prueba_secreta@localhost:5432/catalogo_gleidys')
            conn = MagicMock()
            conn.exec_driver_sql.return_value.scalar_one.side_effect = ['catalogo_gleidys','on']
            context = MagicMock()
            context.__enter__.return_value = (conn,{})
            output = io.StringIO()
            with patch.dict(os.environ,{},clear=True), patch('conectar_silvina.__file__',str(root/'setup.py')), \
                    patch('conectar_silvina.subprocess.run',return_value=remote) as ssh, \
                    patch('transfers.connect',return_value=context) as connect, patch('transfers.schema'), redirect_stdout(output):
                main('gleidys')
            config = json.loads(registry.read_text())
            self.assertEqual(config['catalogo_silvina'],silvina)
            self.assertEqual(config['catalogo_gleidys']['roles'],['source'])
            self.assertNotIn('prueba_secreta',output.getvalue())
            self.assertIn('catalogo-gleidys/backend/.env',ssh.call_args.args[0][-1])
            self.assertNotIn('catalogo-silvina',ssh.call_args.args[0][-1])
            connect.assert_called_once_with('catalogo_gleidys','source')
            self.assertEqual((instance/'gleidys.url').stat().st_mode & 0o777,0o600)

    def test_only_silvina_can_be_configured(self):
        base = 'postgresql://catalogo_silvina_user:prueba@localhost:5432/catalogo_silvina'
        url = local_url(base)
        self.assertEqual((url.host,url.port,url.database),('127.0.0.1',55432,'catalogo_silvina'))
        self.assertEqual(url.password,'prueba')
        for raw in [base.replace('catalogo_silvina','catalogo_otro'),
                    base.replace('localhost','otro-servidor'), base + '?options=readwrite']:
            with self.assertRaises(transfers.TransferError):
                local_url(raw)

    def test_private_credentials_and_source_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = root/'silvina.url'
            private_write(secret, 'postgresql://catalogo_silvina_user:prueba@127.0.0.1:55432/catalogo_silvina')
            self.assertEqual(secret.stat().st_mode & 0o777,0o600)
            registry = root/'connections.json'
            private_write(registry,json.dumps({'catalogo_silvina':{'database':'catalogo_silvina',
                'roles':['source'],'url_file':str(secret)}}))
            with patch.dict(os.environ,{'PERFUME_CONNECTIONS':str(registry)}):
                _,url = transfers.authorized('catalogo_silvina','source')
                self.assertEqual(url.password,'prueba')
                with self.assertRaises(transfers.TransferError):
                    transfers.authorized('catalogo_silvina','target')
                with self.assertRaises(transfers.TransferError):
                    transfers.authorized('catalogo_otro','source')
                secret.chmod(0o644)
                with self.assertRaises(transfers.TransferError):
                    transfers.authorized('catalogo_silvina','source')


if __name__ == '__main__':
    unittest.main()
