import json
import os
import unittest
from unittest.mock import patch

from adotracefoundry.playground_core import Workspace
from adotracefoundry.playground_ai import UpstashLimiter, QuotaExceeded, generate_story


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        nodes = [
            {'id': 1, 'type': 'Epic', 'title': 'Storefront', 'revision': 1, 'fields': {}},
            {'id': 2, 'type': 'Feature', 'title': 'Checkout', 'revision': 1, 'fields': {}},
            {'id': 3, 'type': 'User Story', 'title': 'Buy item', 'revision': 1,
             'fields': {'System.Description': 'I want to buy items',
                        'Microsoft.VSTS.Common.AcceptanceCriteria': 'Purchase succeeds'}},
        ]
        edges = [{'kind': 'parent-child', 'source': 1, 'target': 2},
                 {'kind': 'parent-child', 'source': 2, 'target': 3}]
        self.ws = Workspace(nodes, edges)

    def test_save_does_not_mutate_synced_and_sync_is_incremental(self):
        old = self.ws.documents[3]
        i = self.ws.save_story(title='Retry payments', description='As a shopper, I want retry',
                               acceptance_criteria='Retry is safe', parent_id=2)
        self.assertEqual(i, 4)
        self.assertNotIn(i, self.ws.synced)
        self.assertEqual(len(self.ws.diff()['created_ids']), 1)
        updates = list(self.ws.sync())
        self.assertEqual(updates[-1]['stage'], 'Validate consistency')
        self.assertEqual(self.ws.last_report['after']['workitems'], 4)
        self.assertEqual(self.ws.last_report['after']['relationships'], 3)
        self.assertEqual(self.ws.last_report['regenerated_document_ids'], [2, 4])
        self.assertEqual(self.ws.documents[3], old)
        self.assertEqual(self.ws.last_report['unchanged_documents'], 2)
        self.assertFalse(self.ws.diff()['created_ids'])
        list(self.ws.sync())
        self.assertEqual(self.ws.last_report['regenerated_document_ids'], [])

    def test_edit_increments_revision_only_in_source_before_sync(self):
        self.ws.save_story(title='Buy item changed', description='New description',
                           acceptance_criteria='Better checks', item_id=3, parent_id=2)
        self.assertEqual(self.ws.source[3]['revision'], 2)
        self.assertEqual(self.ws.synced[3]['revision'], 1)
        self.assertEqual(self.ws.diff()['modified_ids'], [3])
        list(self.ws.sync())
        self.assertEqual(self.ws.synced[3]['revision'], 2)
        self.assertEqual(self.ws.last_report['regenerated_document_ids'], [3])

    def test_reject_invalid_parent(self):
        with self.assertRaises(ValueError):
            self.ws.save_story(title='x', description='y', acceptance_criteria='z', parent_id=999)


class LimiterTests(unittest.TestCase):
    @patch.dict(os.environ, {'UPSTASH_REDIS_REST_URL': 'https://example.upstash.io',
                              'UPSTASH_REDIS_REST_TOKEN': 'test-token',
                              'AI_RATE_LIMIT_PER_HOUR': '20', 'AI_GLOBAL_LIMIT_PER_DAY': '200',
                              'AI_MAX_CONCURRENT_REQUESTS': '2'})
    def test_atomic_reservation_calls_redis_and_limits_are_server_side(self):
        commands = []
        def fake(req):
            commands.append(json.loads(req.data))
            if len(commands) == 1:
                return {'result': 'allowed'}
            if len(commands) == 2:
                return {'result': 0}
            return {'result': 'visitor-limit'}
        limiter = UpstashLimiter(transport=fake)
        key = limiter.reserve('a'*32)
        limiter.release(key)
        self.assertEqual(commands[0][0], 'EVAL')
        self.assertEqual(commands[0][2], 3)
        self.assertEqual(commands[0][6:9], [20, 200, 2])
        with self.assertRaises(QuotaExceeded):
            limiter.reserve('a'*32)

    @patch.dict(os.environ, {}, clear=True)
    def test_no_redis_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'AI disabled'):
            UpstashLimiter()

    @patch.dict(os.environ, {'UPSTASH_REDIS_REST_URL': 'https://example.upstash.io',
                              'UPSTASH_REDIS_REST_TOKEN': 'test-token'})
    def test_redis_outage_fails_closed(self):
        limiter = UpstashLimiter(transport=lambda req: (_ for _ in ()).throw(OSError('offline')))
        with self.assertRaisesRegex(RuntimeError, 'AI disabled'):
            limiter.reserve('b'*32)

    @patch.dict(os.environ, {'AZURE_OPENAI_ENDPOINT': 'https://knowledge-studio-ai-24514.openai.azure.com',
                              'AZURE_OPENAI_DEPLOYMENT': 'knowledge-studio-mini',
                              'AZURE_OPENAI_API_KEY': 'unit-test-key'})
    def test_dynamic_llm_generation_uses_feature_context(self):
        class FakeLimiter:
            def __init__(self):
                self.events = []
            def reserve(self, visitor_id):
                self.events.append('reserve')
                return 'key'
            def release(self, key):
                self.events.append('release')
        limiter = FakeLimiter()
        seen = []
        def fake(req):
            payload = json.loads(req.data)
            seen.append(payload)
            return {'status': 'completed', 'output': [{'content': [{
                'type': 'output_text', 'text': json.dumps({
                    'title': 'LLM-generated checkout story',
                    'description': 'As a buyer, I want checkout',
                    'acceptance_criteria': '1. Payment validated\n2. Receipt recorded\n3. Duplicate prevented'})}]}]}
        result = generate_story({'id': 2, 'title': 'Checkout', 'fields': {}},
                                visitor_id='c'*32, limiter=limiter, transport=fake)
        self.assertEqual(result['title'], 'LLM-generated checkout story')
        self.assertIn('Checkout', seen[0]['input'])
        self.assertEqual(limiter.events, ['reserve', 'release'])


if __name__ == '__main__':
    unittest.main()
