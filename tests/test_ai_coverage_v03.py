import json
import tempfile
import unittest
from pathlib import Path
from adotracefoundry.ai_coverage import evidence_from_export, _validate_model, _steps


class CoverageTests(unittest.TestCase):
    def test_parse_ado_steps(self):
        xml = '<steps><step id="1"><parameterizedString>&lt;DIV&gt;Click register&lt;/DIV&gt;</parameterizedString><parameterizedString>&lt;P&gt;Done&lt;/P&gt;</parameterizedString></step></steps>'
        self.assertEqual(_steps(xml), [{"step": 1, "action": "Click register", "expected": "Done"}])

    def test_evidence_and_citation_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nodes = [
                {"id": 3, "type": "User Story", "title": "Register", "fields": {"Microsoft.VSTS.Common.AcceptanceCriteria": "Can register"}},
                {"id": 391, "type": "Test Case", "title": "Happy path", "fields": {"Microsoft.VSTS.TCM.Steps": '<steps><step id="1"><parameterizedString>Do it</parameterizedString><parameterizedString>Works</parameterizedString></step></steps>'}}
            ]
            (root / 'workitems.jsonl').write_text('\n'.join(json.dumps(n) for n in nodes), encoding='utf-8')
            (root / 'relationships.json').write_text(json.dumps([{'kind': 'tested-by', 'source': 3, 'target': 391}]), encoding='utf-8')
            ev = evidence_from_export(root, 3)
            self.assertEqual(ev['test_cases'][0]['steps'][0]['step'], 1)
            result = _validate_model({'criteria': [{'criterion': 'Can register', 'status': 'covered', 'evidence': [{'test_case_id': 999, 'step': 1}]}]}, ev)
            self.assertEqual(result['criteria'][0]['status'], 'insufficient_evidence')
            self.assertTrue(result['validation_warnings'])


if __name__ == '__main__':
    unittest.main()
