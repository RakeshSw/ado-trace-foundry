#!/usr/bin/env python3
"""Generate offline structural coverage reports from an existing export."""
import argparse
import json
from pathlib import Path
from adotracefoundry.traceability import analyze


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True, help='Existing export directory')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--requirement-types', nargs='+', default=['User Story', 'Product Backlog Item', 'Requirement'])
    args = p.parse_args()
    nodes = [json.loads(x) for x in (args.input / 'workitems.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    edges = json.loads((args.input / 'relationships.json').read_text(encoding='utf-8'))
    data = analyze(nodes, edges, requirement_types=args.requirement_types)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in [('traceability-graph.json', data['traces']), ('coverage-report.json', {k: v for k, v in data.items() if k != 'traces'})]:
        (args.output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    s = data['summary']
    report = ['# Structural traceability report', '', '**Coverage measures links only, not test sufficiency.**', '']
    report += [f'- {k.replace("_", " ").capitalize()}: {v}' for k,v in s.items()]
    report += ['', '## Requirements without linked tests', ', '.join(map(str, data['requirements_without_tests'])) or 'None',
               '', '## Orphan test cases', ', '.join(map(str, data['orphan_test_case_ids'])) or 'None', '']
    (args.output / 'traceability-report.md').write_text('\n'.join(report), encoding='utf-8')
    print(json.dumps(s, indent=2))
    if data['issues']:
        print('WARNING: graph issues:', len(data['issues']))

if __name__ == '__main__':
    main()
