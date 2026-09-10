#!/usr/bin/env python3
"""Maintainer-helper acceptance checks; synthetic schemas, no network."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest

class MaintenanceTests(unittest.TestCase):
    def api(self):
        return importlib.import_module('prepare_schema')

    def valid(self):
        return {'openapi':'3.0.3','info':{'version':'1.0.0','title':'Synthetic'},'paths':{'/version':{'get':{'operationId':'orchdVersion','responses':{'200':{'description':'ok'}}}}}}

    def test_schema_inventory(self):
        self.assertEqual(self.api().inspect_schema(self.valid())['operations'], 1)

    def test_duplicate_operation_rejected(self):
        doc=self.valid()
        doc['paths']['/other']={'get':{'operationId':'orchdVersion'}}
        with self.assertRaises(ValueError): self.api().inspect_schema(doc)

    def test_external_reference_rejected(self):
        doc=self.valid()
        doc['components']={'schemas':{'External':{'$ref':'https://other.invalid/schema'}}}
        with self.assertRaises(ValueError): self.api().inspect_schema(doc)

    def test_candidate_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.json'; dst=Path(d)/'candidate.json'
            src.write_text(json.dumps(self.valid()))
            self.api().prepare(src,dst)
            self.assertEqual(json.loads(dst.read_text())['info']['version'],'1.0.0')
            with self.assertRaises(FileExistsError): self.api().prepare(src,dst)

    def test_invalid_schema_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.json'; dst=Path(d)/'candidate.json'
            src.write_text('{"paths":{}}')
            with self.assertRaises(ValueError): self.api().prepare(src,dst)
            self.assertFalse(dst.exists())

    def test_duplicate_mapping_keys_rejected_before_output(self):
        samples=[json.dumps(self.valid()).replace('"version": "1.0.0"','"version": "1.0.0", "version": "2.0.0"')]
        try: import yaml
        except ImportError: pass
        else: samples.append('openapi: 3.0.3\ninfo: {title: Synthetic, version: 1.0.0}\npaths:\n  /version:\n    get: {operationId: syntheticLost}\n    get: {operationId: orchdVersion}\n')
        for text in samples:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as d:
                src=Path(d)/'source.input'; dst=Path(d)/'candidate.json';src.write_text(text)
                with self.assertRaises(ValueError):self.api().prepare(src,dst)
                self.assertFalse(dst.exists())

    def test_nonfinite_numbers_rejected_before_output(self):
        doc=self.valid();doc['components']={'schemas':{'Synthetic':{'type':'number','minimum':float('nan')}}}
        samples=[json.dumps(doc),json.dumps(doc).replace('NaN','1e9999')]
        try: import yaml
        except ImportError: pass
        else:samples.append('openapi: 3.0.3\ninfo: {title: Synthetic, version: 1.0.0}\npaths: { /version: { get: { operationId: orchdVersion } } }\ncomponents: {schemas: {Synthetic: {type: number, minimum: .nan}}}\n')
        for text in samples:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as d:
                src=Path(d)/'source.input';dst=Path(d)/'candidate.json';src.write_text(text)
                with self.assertRaises(ValueError):self.api().prepare(src,dst)
                self.assertFalse(dst.exists())

    def test_nonregular_input_rejected_without_blocking(self):
        import os, subprocess, sys
        if not hasattr(os,'mkfifo'):self.skipTest('FIFO not supported on this test OS')
        with tempfile.TemporaryDirectory() as d:
            fifo=Path(d)/'input.fifo';dst=Path(d)/'candidate.json';os.mkfifo(fifo)
            code='import prepare_schema as m,sys\ntry:m.prepare(sys.argv[1],sys.argv[2])\nexcept (ValueError,OSError):sys.exit(0)\nsys.exit(1)'
            try:
                p=subprocess.run([sys.executable,'-c',code,str(fifo),str(dst)],cwd=str(Path(self.api().__file__).parent),capture_output=True,timeout=1)
            except subprocess.TimeoutExpired:self.fail('Nonregular source blocked instead of rejecting')
            self.assertEqual(p.returncode,0)
            self.assertFalse(dst.exists())

    def test_yaml_key_type_collision_never_creates_candidate(self):
        try: import yaml
        except ImportError:self.skipTest('Optional YAML parser not available')
        text='openapi: 3.0.3\ninfo: {version: 1.0.0}\npaths: {/version: {get: {operationId: orchdVersion}}}\nx-extra: {1: first, "1": second}\n'
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.yaml';dst=Path(d)/'candidate.json';src.write_text(text)
            with self.assertRaises(ValueError):self.api().prepare(src,dst)
            self.assertFalse(dst.exists())

if __name__=='__main__': unittest.main()
