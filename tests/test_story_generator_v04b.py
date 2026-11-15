import json
import unittest
from unittest.mock import patch
from adotracefoundry.playground_ai import generate_story


class FakeLimiter:
    def reserve(self, visitor_id):
        return 'fake-lock'

    def release(self, lock):
        pass


class StoryGenerationNormalizationTests(unittest.TestCase):
    def _run(self, generated):
        def transport(req):
            return {'status': 'completed', 'output': [{'content': [
                {'type': 'output_text', 'text': json.dumps(generated)}]}]}
        with patch.dict('os.environ', {
            'AZURE_OPENAI_ENDPOINT': 'https://test.openai.azure.com',
            'AZURE_OPENAI_DEPLOYMENT': 'test-model',
            'AZURE_OPENAI_API_KEY': 'dummy-key',
        }):
            return generate_story({'id': 2, 'title': 'Checkout', 'fields': {}},
                                  visitor_id='a'*32, limiter=FakeLimiter(), transport=transport)

    def test_string_fields(self):
        result = self._run({'title': 'Retry checkout', 'description': 'As a shopper...',
                            'acceptance_criteria': '1. Works\n2. Logs\n3. Persists'})
        self.assertEqual(result['title'], 'Retry checkout')

    def test_criteria_as_list(self):
        result = self._run({'title': 'Retry checkout', 'description': 'As a shopper...',
                            'acceptance_criteria': ['Retry succeeds', 'Failure is visible', 'No duplicate']})
        self.assertIn('1. Retry succeeds', result['acceptance_criteria'])
        self.assertIn('3. No duplicate', result['acceptance_criteria'])

    def test_criteria_as_object_list(self):
        result = self._run({'title': 'Retry checkout', 'description': 'As a shopper...',
                            'acceptance_criteria': [{'criterion': 'A retry is safe'}, {'text': 'Failures logged'}]})
        self.assertIn('2. Failures logged', result['acceptance_criteria'])

    def test_missing_required_content_fails(self):
        with self.assertRaisesRegex(ValueError, 'description'):
            self._run({'title': 'Retry checkout', 'acceptance_criteria': ['A', 'B', 'C']})

    def test_bad_list_element_fails(self):
        with self.assertRaisesRegex(ValueError, 'unsupported list element'):
            self._run({'title': 'Retry checkout', 'description': 'As a shopper...',
                       'acceptance_criteria': ['A', 100]})


if __name__ == '__main__':
    unittest.main()
