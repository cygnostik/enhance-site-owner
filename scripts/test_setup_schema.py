"""Schema first-install checks; fixtures are synthetic and do not use credentials."""
import copy
import importlib
import tempfile
import unittest
from pathlib import Path

class SetupSchemaTests(unittest.TestCase):
    def doc(self):
        return {'openapi':'3.0.3','info':{'title':'Synthetic','version':'1.0.0'},'paths':{'/version':{'get':{'operationId':'orchdVersion','responses':{'200':{'description':'ok'}}}}}}

    def test_installs_only_expected_version_to_new_file(self):
        m=importlib.import_module('setup_schema')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'openapi.json'
            result=m.install(self.doc(),out,'1.0.0')
            self.assertEqual(result['operations'],1)
            self.assertTrue(out.is_file())
            with self.assertRaises(FileExistsError):m.install(self.doc(),out,'1.0.0')

    def test_wrong_version_or_reference_never_writes(self):
        m=importlib.import_module('setup_schema')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'openapi.json'
            with self.assertRaises(ValueError):m.install(self.doc(),out,'2.0.0')
            broken=copy.deepcopy(self.doc());broken['components']={'schemas':{'x':{'$ref':'#/components/schemas/missing'}}}
            with self.assertRaises(Exception):m.install(broken,out,'1.0.0')
            self.assertFalse(out.exists())

    def test_serialization_key_collision_never_installs(self):
        m=importlib.import_module('setup_schema');doc=self.doc();doc['x-extra']={1:'first','1':'second'}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'openapi.json'
            with self.assertRaises(ValueError):m.install(doc,out,'1.0.0')
            self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
