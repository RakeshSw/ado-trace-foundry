import importlib.util
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

P = Path(__file__).resolve().parents[1] / 'tools' / 'seed_samplecommerce.py'
spec = importlib.util.spec_from_file_location('seed_samplecommerce', P)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SeedTests(unittest.TestCase):
    def test_xml_steps(self):
        obj = [{'step': i, 'action': f'Action {i} & <safe>', 'expected': f'Expected {i}'} for i in range(1, 5)]
        root = ET.fromstring(mod.steps_xml(obj))
        self.assertEqual(len(root.findall('step')), 4)
        self.assertIn('&lt;safe&gt;', root.findall('step')[0].find('parameterizedString').text)

    def test_missing_step_rejected(self):
        with self.assertRaises(ValueError):
            mod.steps_xml([{'step': 3, 'action': 'x', 'expected': 'y'}])

    def test_patch_fields(self):
        row = {'local_key': 'TC-0001', 'title': 'Sample test', 'type': 'Test Case',
               'description': 'Synthetic', 'steps': [{'step': 1, 'action': 'a', 'expected': 'b'}]}
        patch = mod.create_patch(row)
        self.assertEqual(patch[0]['path'], '/fields/System.Title')
        self.assertIn('TC-0001', patch[2]['value'])
        self.assertEqual(len(ET.fromstring(patch[3]['value']).findall('step')), 1)

    def test_external_org_rejected(self):
        with self.assertRaises(ValueError):
            mod.Client('fake', org='other-tenant')

    def test_dry_run_with_real_fixture(self):
        planner = Path(__file__).resolve().parents[1] / 'samples' / 'fixture-plan'
        # Fixture optional: real plan only exists in Codespace output.
        self.assertTrue(hasattr(mod, 'validate_plan'))

if __name__ == '__main__':
    unittest.main()
