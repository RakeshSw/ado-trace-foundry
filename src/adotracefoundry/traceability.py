"""Offline, provider-independent graph traceability and structural coverage."""
from __future__ import annotations
from collections import defaultdict, Counter
from typing import Iterable

DEFAULT_REQUIREMENT_TYPES = frozenset({'User Story', 'Product Backlog Item', 'Requirement'})
DEFAULT_TEST_TYPES = frozenset({'Test Case'})


def analyze(nodes: list[dict], edges: list[dict], *, requirement_types: Iterable[str] = DEFAULT_REQUIREMENT_TYPES,
            test_types: Iterable[str] = DEFAULT_TEST_TYPES) -> dict:
    """Build graph-based traces and link coverage; no LLM sufficiency claims."""
    by_id = {int(n['id']): n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError('Duplicate work item IDs in input')
    req_types = set(requirement_types)
    tc_types = set(test_types)
    children, parents, tests_by_req, reqs_by_test, related = (defaultdict(set) for _ in range(5))
    issues = []
    for edge in edges:
        src, dst, kind = int(edge['source']), int(edge['target']), edge['kind']
        if src not in by_id or dst not in by_id:
            issues.append({'reason': 'missing-node', 'edge': edge})
            continue
        if kind == 'parent-child':
            children[src].add(dst)
            parents[dst].add(src)
        elif kind == 'tested-by':
            tests_by_req[src].add(dst)
            reqs_by_test[dst].add(src)
        elif kind == 'related':
            related[src].add(dst)
            related[dst].add(src)
    requirement_ids = sorted(i for i,n in by_id.items() if n['type'] in req_types)
    test_ids = sorted(i for i,n in by_id.items() if n['type'] in tc_types)

    def ancestry(node_id):
        """All root-to-node hierarchy paths, cycle-safe."""
        def walk(current, seen):
            if current in seen:
                issues.append({'reason': 'hierarchy-cycle', 'node_id': current})
                return []
            ps = sorted(parents[current])
            if not ps:
                return [[current]]
            result = []
            for p in ps:
                for path in walk(p, seen | {current}):
                    result.append(path + [current])
            return result
        return walk(node_id, set())

    traces = []
    req_records = []
    for req in requirement_ids:
        linked = sorted(t for t in tests_by_req[req] if t in by_id and by_id[t]['type'] in tc_types)
        ancestry_paths = ancestry(req)
        req_records.append({'requirement_id': req, 'title': by_id[req]['title'],
                            'test_case_ids': linked, 'test_count': len(linked),
                            'has_linked_tests': bool(linked),
                            'parent_ids': sorted(parents[req]),
                            'related_ids': sorted(related[req])})
        for ancestor_path in ancestry_paths:
            for test in linked:
                traces.append({'path_ids': ancestor_path + [test],
                               'path_types': [by_id[x]['type'] for x in ancestor_path + [test]],
                               'requirement_id': req, 'test_case_id': test})
    without_tests = [r['requirement_id'] for r in req_records if not r['has_linked_tests']]
    orphan_tests = [t for t in test_ids if not any(r in requirement_ids for r in reqs_by_test[t])]
    return {
        'schema_version': '0.3',
        'coverage_kind': 'structural-links-only',
        'summary': {'workitems': len(nodes), 'relationships': len(edges),
                    'requirements': len(requirement_ids), 'test_cases': len(test_ids),
                    'requirements_with_tests': len(requirement_ids) - len(without_tests),
                    'requirements_without_tests': len(without_tests),
                    'orphan_test_cases': len(orphan_tests), 'trace_paths': len(traces)},
        'requirements': req_records,
        'requirements_without_tests': without_tests,
        'orphan_test_case_ids': orphan_tests,
        'traces': traces,
        'issues': issues,
    }
