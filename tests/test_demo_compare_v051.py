import unittest
from adotracefoundry.playground_core import Workspace
from adotracefoundry.demo_compare import capture_before, answer_after, freeze_question


class FakeLimiter:
    def __init__(self): self.calls = 0
    def reserve(self, visitor_id):
        self.calls += 1
        return 'lock'
    def release(self, key): pass


class DemoComparisonTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace([
            {'id': 1, 'type': 'Feature', 'title': 'Identity', 'revision': 1, 'fields': {}},
        ], [])
        self.story = self.ws.save_story(title='Login with passkey',
                description='As a customer I want passkey sign-in',
                acceptance_criteria='Passkey challenge must expire', parent_id=1)

    def test_question_is_frozen_and_targeted(self):
        result = freeze_question('How does story {work_item_id} handle passkey expiry?', self.story)
        self.assertIn('2', result)
        self.assertNotIn('{work_item_id}', result)
        self.assertIn('For work item 2:', freeze_question('Explain passkey expiry', 2))

    def test_before_retrieval_never_leaks_unsynced_story(self):
        before = capture_before(self.ws, self.story, 'Explain requirements for {work_item_id}')
        self.assertFalse(before['found_in_synced_knowledge'])
        self.assertEqual(before['synced_count'], 1)
        self.assertTrue(before['evidence']['pending_sync'])
        self.assertNotIn('Passkey challenge must expire', str(before))
        self.assertEqual(before['model_calls'], 0)

    def test_after_requires_sync_and_same_question(self):
        before = capture_before(self.ws, self.story, 'What requirements does {work_item_id} define?')
        model_calls=[]
        def mock_model(payload, instructions):
            model_calls.append(payload)
            self.assertEqual(payload['question'], before['question'])
            self.assertEqual(payload['evidence']['id'], self.story)
            self.assertIn('Passkey challenge must expire', str(payload['evidence']))
            return {'answer': 'Work item [WI-2] requires expiring passkey challenges.'}
        limiter=FakeLimiter()
        with self.assertRaisesRegex(ValueError, 'Synchronize'):
            answer_after(self.ws, before, visitor_id='a'*32, limiter=limiter, model_call=mock_model)
        self.assertEqual(limiter.calls, 0)
        list(self.ws.sync())
        after=answer_after(self.ws, before, visitor_id='a'*32, limiter=limiter, model_call=mock_model)
        self.assertEqual(len(model_calls), 1)
        self.assertEqual(limiter.calls, 1)
        self.assertEqual(after['question'], before['question'])
        self.assertTrue(after['found_in_synced_knowledge'])
        self.assertEqual(after['synced_count'], 2)

    def test_after_refuses_missing_evidence(self):
        before = capture_before(self.ws, self.story, 'Describe {work_item_id}')
        with self.assertRaisesRegex(ValueError, 'Synchronize'):
            answer_after(self.ws, before, visitor_id='a'*32,
                         model_call=lambda *_: self.fail('Model should not run'))

if __name__ == '__main__': unittest.main()
