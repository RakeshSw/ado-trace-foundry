"""Produce a minimized SYNTHETIC demo snapshot from local ADOTraceFoundry export.

Review the JSONL and edge file before publishing; no automated upload/commit.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path('output/live-v03-complete'))
    parser.add_argument('--output', type=Path, default=Path('output/public-demo-snapshot'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    allowed = {'System.Title', 'System.WorkItemType', 'System.Description',
               'Microsoft.VSTS.Common.AcceptanceCriteria', 'Microsoft.VSTS.TCM.Steps'}
    with (args.input / 'workitems.jsonl').open(encoding='utf-8') as source, \
         (args.output / 'workitems.jsonl').open('w', encoding='utf-8') as target:
        count = 0
        for line in source:
            if not line.strip():
                continue
            node = json.loads(line)
            safe = {k: node.get(k) for k in ('id', 'type', 'revision', 'title', 'state')}
            safe['fields'] = {k: v for k, v in (node.get('fields') or {}).items() if k in allowed}
            safe['url'] = ''
            safe['descriptions'] = {}
            target.write(json.dumps(safe, ensure_ascii=False) + '\n')
            count += 1
    relationships = json.loads((args.input / 'relationships.json').read_text(encoding='utf-8'))
    (args.output / 'relationships.json').write_text(json.dumps(relationships, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {count} work items and {len(relationships)} relationships at {args.output}')
    print('REVIEW ALL CONTENT BEFORE COPYING TO samples/playground-snapshot OR COMMITTING.')


if __name__ == '__main__':
    main()
