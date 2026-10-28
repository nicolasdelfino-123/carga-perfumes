import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy.engine import make_url
import transfers


class Backup(unittest.TestCase):
    def test_dump_and_validation_use_paired_binaries_and_private_file(self):
        url = make_url('postgresql://reader:private-password@127.0.0.1:55433/test')
        def run(args, **kwargs):
            if '--format=custom' in args:
                kwargs['stdout'].write(b'PGDMP')
            return subprocess.CompletedProcess(args,0)
        with tempfile.TemporaryDirectory() as folder, \
             patch('transfers.authorized',return_value=({},url)), \
             patch('transfers.backup_tools',return_value=('/tools/18/pg_dump','/tools/18/pg_restore')), \
             patch('transfers.subprocess.run',side_effect=run) as execute:
            path = Path(transfers.backup('test',folder))
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(execute.call_args_list[0].args[0][0],'/tools/18/pg_dump')
            self.assertEqual(execute.call_args_list[1].args[0][0],'/tools/18/pg_restore')
            self.assertNotIn('private-password',str(execute.call_args_list[0].args))

    def test_validation_failure_still_blocks_backup(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('transfers.authorized',return_value=({},make_url('postgresql://reader@localhost/test'))), \
             patch('transfers.subprocess.run',side_effect=[subprocess.CompletedProcess([],0),
                 subprocess.CalledProcessError(1,['pg_restore'],stderr=b'invalid archive')]):
            with self.assertRaises(transfers.TransferError):
                transfers.backup('test',folder)
