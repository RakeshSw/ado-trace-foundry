import unittest
from unittest.mock import patch
from adotracefoundry.playground_core import Workspace, edit_synthetic_item, change_synthetic_link, create_synthetic_test_case
from adotracefoundry.engineering_agent import (respond, route, run_tool, lookup, gap_report,
                                                test_quality, source_status)

class FakeLimiter:
    def __init__(self): self.uses=0
    def reserve(self, visitor_id): self.uses+=1; return 'inflight'
    def release(self, key): pass

class EngineeringAgentTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace([
          {'id':1,'type':'Epic','title':'Commerce','revision':1,'fields':{}},
          {'id':2,'type':'Feature','title':'Registration','revision':1,'fields':{}},
          {'id':3,'type':'User Story','title':'Register customer','revision':1,'fields':{'System.Description':'Must validate inputs','Microsoft.VSTS.Common.AcceptanceCriteria':''}},
          {'id':4,'type':'Test Case','title':'Invalid and unauthorized requests','revision':1,'fields':{'Microsoft.VSTS.TCM.Steps':'<steps><step id="1"><parameterizedString>Submit invalid or unauthorized request</parameterizedString><parameterizedString>Appropriate error occurs</parameterizedString></step></steps>'}}
        ], [{'kind':'parent-child','source':1,'target':2}, {'kind':'parent-child','source':2,'target':3}, {'kind':'tested-by','source':3,'target':4}])

    def test_read_only_tool_grounded_in_snapshot(self):
        data=lookup(self.ws,3)
        self.assertEqual([x['id'] for x in data['linked_records']],[2,4])
        self.assertEqual(gap_report(self.ws)['summary']['requirements_with_tests'],1)
        self.assertEqual(len(test_quality(self.ws,3)['deterministic_quality_flags']),1)

    def test_source_edit_requires_sync(self):
        edit_synthetic_item(self.ws,3,title='Updated',description='New',acceptance_criteria='')
        self.assertEqual(self.ws.synced[3]['title'],'Register customer')
        self.assertEqual(self.ws.source[3]['revision'],2)
        self.assertEqual(self.ws.source[3]['fields']['Microsoft.VSTS.Common.AcceptanceCriteria'],'')
        self.assertEqual(source_status(self.ws)['pending']['modified_ids'],[3])
        list(self.ws.sync())
        self.assertEqual(self.ws.synced[3]['title'],'Updated')

    def test_changes_to_links_do_not_autosync(self):
        self.assertTrue(change_synthetic_link(self.ws,3,4,'tested-by',add=False))
        self.assertEqual(len(self.ws.diff()['removed_edges']),1)
        self.assertEqual(gap_report(self.ws)['summary']['requirements_with_tests'],1)
        list(self.ws.sync())
        self.assertEqual(gap_report(self.ws)['summary']['requirements_without_tests'],1)

    def test_model_routes_and_explains_with_two_limited_calls(self):
        calls=[]
        def model(data, instruction):
            calls.append(data)
            return {'tool':'work_item','args':{'id':3}} if len(calls)==1 else {'answer':'Story [WI-3] is linked to test [TC-4].'}
        limiter=FakeLimiter()
        result=respond('Explain work item 3',self.ws,visitor_id='f'*32,limiter=limiter,model_call=model)
        self.assertEqual(limiter.uses,2)
        self.assertEqual(result['plan']['tool'],'work_item')
        self.assertIn('3',result['answer'])
        self.assertEqual(result['evidence']['id'],3)

    def test_no_hallucinated_work_item_lookup(self):
        planner=lambda data,instruction:{'tool':'work_item','args':{'id':999}}
        plan=route('Explain registration',self.ws,visitor_id='f'*32,limiter=FakeLimiter(),model_call=planner)
        self.assertEqual(plan['args']['id'],0)

    def test_model_cannot_modify_workspace(self):
        planner=lambda data,instruction:{'tool':'delete_all','args':{}}
        with self.assertRaises(ValueError):
            route('Delete everything',self.ws,visitor_id='f'*32,limiter=FakeLimiter(),model_call=planner)
        self.assertEqual(len(self.ws.source),4)

    def test_new_test_case_requires_sync(self):
        tid=create_synthetic_test_case(self.ws,title='Reject duplicate',action='Submit twice',
                                       expected='Second request creates no extra record',requirement_id=3)
        self.assertEqual(tid,5)
        self.assertNotIn(tid,self.ws.synced)
        self.assertEqual(len(self.ws.diff()['added_edges']),1)
        list(self.ws.sync())
        self.assertEqual(len(self.ws.synced),5)
        self.assertTrue(any(e['kind']=='tested-by' and e['target']==5 for e in self.ws.synced_edges))

    def test_draft_never_saves_without_confirmation(self):
        planner=lambda data,instruction:{'tool':'draft_story','args':{'feature_id':2}}
        result=respond('Generate a new User Story under registration',self.ws,visitor_id='f'*32,
                       limiter=FakeLimiter(),model_call=planner)
        self.assertEqual(result['proposed_feature_id'],2)
        self.assertEqual(len(self.ws.source),4)

if __name__=='__main__':unittest.main()

class EvidenceCoverageTests(unittest.TestCase):
    def test_invalid_citation_downgraded_and_ambiguous_step_downgraded(self):
        from adotracefoundry.engineering_agent import assess_functional_coverage
        e = {'requirement': {'id': 3, 'description': 'Must validate authorization', 'acceptance_criteria': ''},
             'linked_tests': [{'id': 4, 'steps': [
                 {'step': 1, 'action': 'Submit invalid or unauthorized', 'expected': 'Appropriate error'}]}],
             'deterministic_quality_flags': [{'test_id': 4, 'step': 1, 'findings': ['ambiguous OR']}],
             'no_linked_tests': False}
        def fake(data, instructions):
            return {'criteria': [
                {'criterion': 'Authorization', 'status': 'covered', 'source_field': 'Microsoft.VSTS.Common.AcceptanceCriteria',
                 'rationale': 'Said authorized', 'evidence': [{'test_case_id': 4, 'step': 1}]},
                {'criterion': 'Validation', 'status': 'covered', 'evidence': [{'test_case_id': 999, 'step': 3}]}],
                'suggested_tests': ['Independent negative authorization test']}
        result=assess_functional_coverage(e,visitor_id='0'*32,limiter=FakeLimiter(),model_call=fake)
        self.assertEqual([x['status'] for x in result['criteria']], ['partial','insufficient_evidence'])
        self.assertTrue(all(x['source_field']=='System.Description' for x in result['criteria']))
        self.assertTrue(result['warnings'])

    def test_no_linked_tests_makes_no_model_calls(self):
        from adotracefoundry.engineering_agent import assess_functional_coverage
        e={'requirement': {'id': 100},'linked_tests': [],'deterministic_quality_flags': []}
        limiter=FakeLimiter()
        result=assess_functional_coverage(e,visitor_id='0'*32,limiter=limiter,
            model_call=lambda *args: self.fail('Model should not be called'))
        self.assertEqual(limiter.uses,0)
        self.assertIn('No linked tests',result['warnings'][0])
