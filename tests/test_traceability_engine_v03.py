import unittest
from adotracefoundry.traceability import analyze


def node(id, type):
    return {'id': id, 'type': type, 'title': str(id)}


class TraceabilityV03Tests(unittest.TestCase):
    def test_agile_paths_and_missing(self):
        nodes = [node(1, 'Epic'), node(2, 'Feature'), node(3, 'User Story'), node(4, 'Test Case'), node(5, 'User Story'), node(6, 'Test Case')]
        edges = [{'source': 1, 'target': 2, 'kind': 'parent-child'},
                 {'source': 2, 'target': 3, 'kind': 'parent-child'},
                 {'source': 3, 'target': 4, 'kind': 'tested-by'}]
        d = analyze(nodes, edges)
        self.assertEqual(d['traces'][0]['path_ids'], [1,2,3,4])
        self.assertEqual(d['requirements_without_tests'], [5])
        self.assertEqual(d['orphan_test_case_ids'], [6])
        self.assertEqual(d['summary']['trace_paths'], 1)

    def test_scrum_and_cmmi(self):
        for type_ in ('Product Backlog Item', 'Requirement'):
            result = analyze([node(10,type_),node(11,'Test Case')], [{'kind':'tested-by','source':10,'target':11}])
            self.assertEqual(result['summary']['requirements_with_tests'], 1)

    def test_unknown_link_and_cycle(self):
        result = analyze([node(1,'User Story'),node(2,'Epic')], [
            {'kind':'parent-child','source':1,'target':2},
            {'kind':'parent-child','source':2,'target':1},
            {'kind':'tested-by','source':1,'target':99}])
        self.assertEqual(result['summary']['trace_paths'], 0)
        self.assertEqual({x['reason'] for x in result['issues']}, {'missing-node', 'hierarchy-cycle'})

if __name__ == '__main__':
    unittest.main()
