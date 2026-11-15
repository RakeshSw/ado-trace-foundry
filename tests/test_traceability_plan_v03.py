import csv
import json
import tempfile
import unittest
from pathlib import Path
from tools.traceability_plan import build_plan

class TestV03Plan(unittest.TestCase):
    def setUp(self):
        self.seed=Path(__file__).resolve().parents[1]/'samples'/'seed'
        self.temporary=tempfile.TemporaryDirectory()
        self.source=Path(self.temporary.name)/'source.json'
        with open(self.seed/'ADO_Import_630.csv',encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        self.items=[{'id':i+1,'rev':1,'fields':{'System.WorkItemType':r['Work Item Type'],'System.Title':r['Title']}} for i,r in enumerate(rows) if r['Work Item Type']!='Test Case']
        self.source.write_text(json.dumps(self.items))
    def tearDown(self): self.temporary.cleanup()
    def test_expected_complete_plan(self):
        p=build_plan(self.seed,self.source)
        s=p['summary']
        self.assertEqual(s['mapped_items'],390)
        self.assertEqual(s['uncreated_test_cases'],240)
        self.assertEqual(s['planned_relations'],624)
        self.assertEqual(s['test_step_rows'],960)
        self.assertEqual(s['ambiguous_matches'],0)
        self.assertEqual(s['unmapped_non_test_items'],0)
        self.assertEqual(s['duplicate_relation_specs'],0)
    def test_duplicate_title_fails_closed(self):
        data=self.items+[dict(self.items[0],id=5000)]
        self.source.write_text(json.dumps(data))
        p=build_plan(self.seed,self.source)
        self.assertEqual(p['summary']['ambiguous_matches'],1)
    def test_missing_work_item_flagged(self):
        self.source.write_text(json.dumps(self.items[1:]))
        p=build_plan(self.seed,self.source)
        self.assertEqual(p['summary']['unmapped_non_test_items'],1)

if __name__=='__main__':unittest.main()
