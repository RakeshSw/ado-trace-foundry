import unittest
from adotracefoundry.playground_core import Workspace
from adotracefoundry.demo_compare import freeze_question, capture_before, answer_after
from adotracefoundry.engineering_agent import respond, explicit_work_item_id, verified_test_summary, lookup

class CounterLimiter:
    def __init__(self): self.used = 0
    def reserve(self, ident): self.used += 1; return 'lock'
    def release(self, token): pass

class GroundedComparisonTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace([
            {'id': 67, 'type': 'Feature', 'title': 'Product Catalog', 'revision': 1, 'fields': {}},
            {'id': 100, 'type': 'User Story', 'title': 'Search catalog', 'revision': 1,
             'fields': {'System.Description': 'Search products by title and price.',
                        'Microsoft.VSTS.Common.AcceptanceCriteria': 'Search results in two seconds.'}},
            {'id': 101, 'type': 'Test Case', 'title': 'Catalog test', 'revision': 1, 'fields': {}},
        ], [
            {'kind': 'parent-child', 'source': 67, 'target': 100},
            {'kind': 'tested-by', 'source': 100, 'target': 101},
        ])
        self.new_id = self.ws.save_story(title='Live delivery notifications',
            description='As a customer I want delivery status notices.',
            acceptance_criteria='Email and SMS notification when delivery status changes.',
            parent_id=67)

    def test_question_stem_gets_item_id(self):
        q = freeze_question('How are tests covering new notifications for work item', self.new_id)
        self.assertTrue(q.endswith(f'work item {self.new_id}?'))
        self.assertEqual(freeze_question(q, self.new_id), q)
        self.assertEqual(freeze_question('What does {work_item_id} require?', self.new_id),
                         f'What does {self.new_id} require?')
        self.assertIn(f'work item {self.new_id}', freeze_question('What is new?', self.new_id))

    def test_id_precedence(self):
        self.assertEqual(explicit_work_item_id('Explain WI-100'), 100)
        self.assertEqual(explicit_work_item_id('What tests cover story #100?'), 100)
        self.assertEqual(explicit_work_item_id('How does it work with respect to 102?'), 102)
        self.assertIsNone(explicit_work_item_id('Compare WI-100 and TC-101'))
        self.assertIsNone(explicit_work_item_id('Which test cases are absent?'))

    def test_missing_work_item_never_calls_foundry_or_searches_other_tests(self):
        limiter = CounterLimiter()
        output = respond(f'What tests are linked to work item {self.new_id}?', self.ws,
                         visitor_id='f' * 32, limiter=limiter,
                         model_call=lambda *args: self.fail('Missing work item must not invoke model'))
        self.assertEqual(limiter.used, 0)
        self.assertEqual(output['plan']['tool'], 'work_item')
        self.assertIn('not synchronized', output['answer'])
        self.assertNotIn('Catalog test', output['answer'])

    def test_router_cannot_override_exact_target_with_similar_record(self):
        limiter = CounterLimiter()
        responses = [ {'tool': 'search', 'args': {'query': 'catalog'}},
                      {'answer': 'New story [WI-102] requires delivery notifications.'} ]
        def model(data, instructions): return responses.pop(0)
        list(self.ws.sync())
        output = respond(f'Explain work item {self.new_id} and linked tests', self.ws,
                         visitor_id='f' * 32, limiter=limiter, model_call=model)
        self.assertEqual(limiter.used, 2)
        self.assertEqual(output['plan']['tool'], 'work_item')
        self.assertEqual(output['evidence']['id'], self.new_id)
        self.assertIn('0 linked test cases', output['answer'])
        self.assertNotIn('TC-101', output['answer'])

    def test_after_answers_same_question_and_has_authoritative_zero_tests(self):
        question = freeze_question('Which tests cover notification for work item', self.new_id)
        before = capture_before(self.ws, self.new_id, question)
        self.assertEqual(before['synced_count'], 3)
        list(self.ws.sync())
        limiter = CounterLimiter()
        def model(data, instruction):
            self.assertEqual(data['question'], before['question'])
            self.assertEqual(data['evidence']['id'], self.new_id)
            self.assertEqual(data['evidence']['linked_test_case_ids'], [])
            return {'answer': 'New [WI-102] notifies customers about status changes.'}
        after = answer_after(self.ws, before, visitor_id='f' * 32,
                             limiter=limiter, model_call=model)
        self.assertEqual(limiter.used, 1)
        self.assertEqual(after['linked_test_ids'], [])
        self.assertEqual(after['synced_count'], 4)
        self.assertEqual(after['relationship_count'], 3)
        self.assertEqual(after['structural_coverage'], 'no-linked-tests')
        self.assertIn('No test cases are currently linked', after['answer'])
        self.assertEqual(after['question'], before['question'])

    def test_existing_test_link_count_is_exact(self):
        evidence = lookup(self.ws, 100)
        self.assertEqual(evidence['linked_test_case_ids'], [101])
        self.assertIn('[TC-101]', verified_test_summary(evidence))
        self.assertNotIn('functional sufficiency', verified_test_summary(evidence).lower()[:20])

if __name__ == '__main__': unittest.main()
