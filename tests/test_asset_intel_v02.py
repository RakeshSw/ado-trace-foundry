import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from adotracefoundry.asset_intel import inline_references, process_assets

PNG = b'\x89PNG\r\n\x1a\n' + b'test-bytes'

class FakeClient:
    org = 'example-lab'
    def __init__(self): self.calls = 0
    def download_attachment(self, url):
        self.calls += 1
        return PNG

class AssetIntelTests(unittest.TestCase):
    def test_parse_two_refs(self):
        items = [{'id': i, 'fields': {'System.Description':
                    '<p>Demo</p><img src="https://dev.azure.com/example-lab/abc/_apis/wit/attachments/68927873-b70c-4af4-850a-13b9d354880b?fileName=x.png">'}} for i in (28,32)]
        refs = inline_references(items)
        self.assertEqual(len(refs), 2)

    def test_reject_external_url(self):
        items = [{'id': 1, 'fields': {'System.Description': '<img src="https://evil.example/test.png">'}}]
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            _, entries = process_assets(client, items, [], Path(tmp))
            self.assertEqual(entries[0]['extraction_status'], 'rejected: unapproved-attachment-url')
            self.assertEqual(client.calls, 0)

    def test_duplicate_binary_deduplicated(self):
        uri = 'https://dev.azure.com/example-lab/abc/_apis/wit/attachments/68927873-b70c-4af4-850a-13b9d354880b'
        items = [{'id': i, 'fields': {'System.Description': f'<img src="{uri}">'}} for i in (28,32)]
        with tempfile.TemporaryDirectory() as tmp:
            _, entries = process_assets(FakeClient(), items, [], Path(tmp))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]['sha256'], entries[1]['sha256'])
            self.assertEqual(len(list((Path(tmp)/'assets').iterdir())), 1)

if __name__ == '__main__': unittest.main()
