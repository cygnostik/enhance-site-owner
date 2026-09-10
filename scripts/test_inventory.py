"""Clean-copy regression for the offline inventory entrypoint."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class InventoryEntrypointTests(unittest.TestCase):
    def test_creates_report_directory_in_clean_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            for name in ('enhance.py', 'build_inventory.py'):
                shutil.copyfile(ROOT / name, work / name)
            (work / 'assets').mkdir()
            shutil.copyfile(ROOT / 'assets' / 'openapi.json', work / 'assets' / 'openapi.json')
            self.assertFalse((work / 'evidence').exists())
            result = subprocess.run(
                [sys.executable, '-S', str(work / 'build_inventory.py')],
                cwd=work, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((work / 'evidence' / 'operation-coverage.json').read_text())
            self.assertGreater(report['total'], 0)
            self.assertEqual(len(report['operations']), report['total'])
            self.assertEqual(report['constructed'] + report['not_constructed'], report['total'])
            self.assertIn('SYNTHETIC', report['fixture_notice'])


if __name__ == '__main__':
    unittest.main()
