import json
import tempfile
import unittest
from pathlib import Path
from adotracefoundry.core import build, export, plain, target_id
from adotracefoundry.cli import main

ROOT = Path(__file__).parents[1]

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.items = json.loads((ROOT / 'samples/workitems.json').read_text())
    def test_dedup_revision(self):
        result = build(self.items)
        self.assertEqual(len(result['nodes']), 5)
        self.assertEqual(next(n['title'] for n in result['nodes'] if n['id']==201), 'Validate checkout flow')
    def test_traceability(self):
        data = build(self.items)
        self.assertEqual(len(data['edges']), 4)
        self.assertEqual(sorted(x['ids'] for x in data['traces']), [[101,201,301],[101,202,302]])
    def test_attachment_detected(self):
        data = build(self.items)
        self.assertEqual(len(data['attachments']), 1)
        self.assertEqual(data['attachments'][0]['name'], 'checkout-notes.txt')
    def test_markup_cleanup(self):
        self.assertIn('Customer submits an order', plain('<p>Customer submits an order</p>'))
    def test_edge_url_rejection(self):
        self.assertIsNone(target_id('https://example.test/not-an-id'))
    def test_exports(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            export(build(self.items), dest)
            self.assertEqual(len((dest/'workitems.jsonl').read_text().splitlines()),5)
            self.assertEqual(len((dest/'knowledge.jsonl').read_text().splitlines()),5)
            self.assertEqual(json.loads((dest/'manifest.json').read_text())['traces'],2)
    def test_cli_demo(self):
        with tempfile.TemporaryDirectory() as d:
            rc=main(['demo','--input',str(ROOT/'samples/workitems.json'), '--attachments-dir',str(ROOT/'samples/attachments'), '--output', d])
            self.assertEqual(rc,0)
            self.assertIn('Synthetic checkout notes', (Path(d)/'knowledge'/'201.md').read_text())
            self.assertEqual(json.loads((Path(d)/'local-attachment-manifest.json').read_text())[0]['extraction_status'],'text')

if __name__ == '__main__': unittest.main()
