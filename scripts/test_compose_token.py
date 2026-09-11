"""Synthetic token composition and private-output regressions."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ID = '11111111-1111-4111-8111-111111111111'
SECRET = 'SYNTHETIC-SECRET-NOT-A-CREDENTIAL'

class ComposeTokenTests(unittest.TestCase):
    def test_403_diagnostic_checks_token_format_before_roles(self):
        e = importlib.import_module('enhance')
        text = str(e.status_error(403))
        self.assertIn('token-id_secret', text)
        self.assertIn('do not widen', text)

    def test_joins_id_and_secret_without_double_prefix(self):
        m = importlib.import_module('compose_token')
        self.assertEqual(m.compose({'id':ID,'unencryptedToken':SECRET}), ID+'_'+SECRET)
        self.assertEqual(m.compose({'id':ID,'unencryptedToken':ID+'_'+SECRET}), ID+'_'+SECRET)

    def test_invalid_inputs_fail_without_secret_in_error(self):
        m = importlib.import_module('compose_token')
        for value in [{}, [], {'id':ID,'unencryptedToken':''},
                      {'id':'not-a-uuid','unencryptedToken':SECRET},
                      {'id':ID,'unencryptedToken':SECRET+'\n'},
                      {'id':ID,'unencryptedToken':'22222222-2222-4222-8222-222222222222_'+SECRET}]:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(Exception) as error:m.compose(value)
                self.assertNotIn(SECRET,str(error.exception))

    def test_private_file_cli_and_no_overwrite(self):
        script=Path(__file__).with_name('compose_token.py')
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'response.json';target=Path(directory)/'token.txt'
            source.write_text(json.dumps({'id':ID,'unencryptedToken':SECRET}))
            command=[sys.executable,str(script),'--response',str(source),'--output',str(target)]
            first=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertNotIn(SECRET,first.stdout+first.stderr)
            self.assertEqual(target.read_text(),ID+'_'+SECRET+'\n')
            if os.name=='posix':self.assertEqual(target.stat().st_mode & 0o777,0o600)
            second=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(target.read_text(),ID+'_'+SECRET+'\n')

    def test_duplicate_json_rejected_before_output(self):
        script=Path(__file__).with_name('compose_token.py')
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'response.json';target=Path(directory)/'token.txt'
            source.write_text('{"id":"'+ID+'","id":"'+ID+'","unencryptedToken":"'+SECRET+'"}')
            result=subprocess.run([sys.executable,str(script),'--response',str(source),'--output',str(target)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(target.exists())
            self.assertNotIn(SECRET,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
