import json
import unittest
from unittest.mock import patch
from adotracefoundry.playground_ai import generate_story

class DummyLimiter:
    def reserve(self, visitor_id): return 'lock'
    def release(self, key): pass

class GenerationQuestionTests(unittest.TestCase):
    def make(self, data):
        def transport(req):
            return {'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(data)}]}]}
        with patch.dict('os.environ', {
            'AZURE_OPENAI_ENDPOINT':'https://test.openai.azure.com',
            'AZURE_OPENAI_DEPLOYMENT':'test-deployment',
            'AZURE_OPENAI_API_KEY':'fake',
        }):
            return generate_story({'id':2,'title':'Identity','fields':{}},
                visitor_id='a'*32,limiter=DummyLimiter(),transport=transport)

    def test_question_is_generated_by_model(self):
        result=self.make({'title':'Passwordless login', 'description':'As a user...',
                'acceptance_criteria':['Passkeys expire','Rate limits are checked','Events logged'],
                'demo_question':'Which behaviors does work item {work_item_id} require for passwordless login?'})
        self.assertEqual(result['question_source'],'model-generated')
        self.assertIn('{work_item_id}',result['demo_question'])

    def test_model_omission_is_marked_as_fallback(self):
        result=self.make({'title':'Passwordless login','description':'As a user...',
                'acceptance_criteria':'Passkeys expire'})
        self.assertEqual(result['question_source'],'title-derived fallback')
        self.assertIn('Passwordless login',result['demo_question'])
