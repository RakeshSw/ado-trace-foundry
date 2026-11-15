#!/usr/bin/env python3
"""Opt-in SampleCommerce test seeder. Never used by the read-only extractor.

Always produces an offline preview without credentials. --apply enables writes,
restricted to the explicitly confirmed synthetic organization/project.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

ORG = 'adotracefoundry-lab'
PROJECT = 'SampleCommerce'
KINDS = {'System.LinkTypes.Hierarchy-Forward', 'System.LinkTypes.Related', 'Microsoft.VSTS.Common.TestedBy-Forward'}
TAG = 'ADOTraceFoundry-Seed'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def steps_xml(steps):
    if not steps or sorted(x['step'] for x in steps) != list(range(1, len(steps) + 1)):
        raise ValueError('Test steps must use contiguous 1-based indices')
    root = ET.Element('steps', {'id': '0', 'last': str(len(steps))})
    for record in steps:
        el = ET.SubElement(root, 'step', {'id': str(record['step']), 'type': 'ValidateStep'})
        for value in (record['action'], record['expected']):
            child = ET.SubElement(el, 'parameterizedString', {'isformatted': 'true'})
            child.text = f'<DIV><P>{escape_html(str(value))}</P></DIV>'
        ET.SubElement(el, 'description')
    return ET.tostring(root, encoding='unicode')


def escape_html(s):
    from html import escape
    return escape(s, quote=False)


def create_patch(test):
    key = test['local_key']
    if not re.fullmatch(r'TC-\d{4}', key):
        raise ValueError('Unexpected local test key')
    if test['type'] != 'Test Case' or not test['title'].strip():
        raise ValueError('Invalid test case specification')
    return [
        {'op': 'add', 'path': '/fields/System.Title', 'value': test['title']},
        {'op': 'add', 'path': '/fields/System.Description', 'value': test.get('description', '')},
        {'op': 'add', 'path': '/fields/System.Tags', 'value': f'{TAG}; {key}'},
        {'op': 'add', 'path': '/fields/Microsoft.VSTS.TCM.Steps', 'value': steps_xml(test['steps'])},
    ]


class Client:
    def __init__(self, pat, org=ORG, project=PROJECT):
        if org != ORG or project != PROJECT:
            raise ValueError('Seeder is restricted to synthetic SampleCommerce lab')
        if not pat:
            raise ValueError('Set ADO_SEED_PAT to a scoped PAT with Work Items Read & Write')
        self.base = f'https://dev.azure.com/{ORG}/{PROJECT}/_apis/wit'
        self.token = base64.b64encode((':' + pat).encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect())

    def req(self, endpoint, *, method='GET', data=None):
        if not endpoint.startswith('/') or endpoint.startswith('//'):
            raise ValueError('Invalid API endpoint')
        url = self.base + endpoint
        headers = {'Authorization': 'Basic ' + self.token, 'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json-patch+json' if method == 'PATCH' else 'application/json'
        payload = json.dumps(data).encode() if data is not None else None
        for retry in range(4):
            request = urllib.request.Request(url, data=payload, method=method, headers=headers)
            try:
                with self.opener.open(request, timeout=35) as response:
                    raw = response.read(8_000_001)
                    if len(raw) > 8_000_000:
                        raise RuntimeError('Response too large')
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and retry < 3:
                    time.sleep(min(2 ** retry, 6))
                    continue
                raise RuntimeError(f'ADO HTTP {e.code} for {method} {endpoint.split("?")[0]}') from None
        raise RuntimeError('Request failed')

    def existing_tests(self):
        wiql = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
                "AND [System.WorkItemType] = 'Test Case' ORDER BY [System.Id]")
        result = self.req('/wiql?api-version=7.1', method='POST', data={'query': wiql})
        ids = [x['id'] for x in result.get('workItems', [])]
        found = {}
        for i in range(0, len(ids), 100):
            query = urllib.parse.urlencode({'ids': ','.join(map(str, ids[i:i + 100])), 'api-version': '7.1'})
            result = self.req('/workitems?' + query)
            for item in result.get('value', []):
                tags = {t.strip() for t in str(item.get('fields', {}).get('System.Tags', '')).split(';')}
                keys = [tag for tag in tags if re.fullmatch(r'TC-\d{4}', tag)]
                if TAG not in tags or not keys:
                    continue
                if len(keys) != 1 or keys[0] in found:
                    raise RuntimeError('Duplicate or ambiguous seeded Test Case tags detected; manual review required')
                found[keys[0]] = item
        return found

    def get_item(self, item_id):
        return self.req(f'/workitems/{int(item_id)}?$expand=Relations&api-version=7.1')

    def create(self, patch):
        return self.req('/workitems/$Test%20Case?api-version=7.1', method='PATCH', data=patch)

    def link(self, source, target, rel):
        item = self.get_item(source)
        url = f'https://dev.azure.com/{ORG}/_apis/wit/workItems/{int(target)}'
        # Existing relations may use different URL hosts, so compare target ID + kind.
        for edge in item.get('relations', []) or []:
            if edge.get('rel') == rel and re.search(r'/workItems/' + re.escape(str(target)) + r'(?:\?|$)', edge.get('url', ''), re.I):
                return False
        patch = [
            {'op': 'test', 'path': '/rev', 'value': item['rev']},
            {'op': 'add', 'path': '/relations/-', 'value': {'rel': rel, 'url': url, 'attributes': {'comment': 'ADOTraceFoundry synthetic lab seeding'}}},
        ]
        self.req(f'/workitems/{int(source)}?api-version=7.1', method='PATCH', data=patch)
        return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Redirect refused: will not forward write-enabled credentials')


def validate_plan(folder):
    folder = Path(folder)
    summary = load(folder / 'summary.json')
    expected = {'reference_items': 630, 'planned_relations': 624,
                'ambiguous_matches': 0, 'unmapped_non_test_items': 0,
                'unmatched_ado_items': 0, 'duplicate_relation_specs': 0,
                'test_step_rows': 960}
    mapped = summary['mapped_items']
    pending = summary['uncreated_test_cases']
    if mapped < 390 or mapped > 630 or pending < 0 or mapped + pending != 630:
        raise ValueError('Invalid incremental seeding counts')
    for field, value in expected.items():
        if summary.get(field) != value:
            raise ValueError(f'Plan mismatch for {field}: expected {value}, got {summary.get(field)}')
    mapping = load(folder / 'id-map.json')
    tests = load(folder / 'testcases-to-create.json')
    relations = load(folder / 'relationships-to-create.json')
    if len(mapping) != mapped or len(tests) != pending or len(relations) != 624:
        raise ValueError('Unexpected plan file counts')
    if len(set(mapping.values())) != mapped:
        raise ValueError('Duplicate ADO numeric IDs in mapping')
    for t in tests:
        create_patch(t)
        if len(t['steps']) != 4:
            raise ValueError('Unexpected test step count')
    for rel in relations:
        if rel['relation'] not in KINDS or rel['source_key'] == rel['target_key']:
            raise ValueError('Unexpected relation specification')
    if len({(r['source_key'], r['target_key'], r['relation']) for r in relations}) != 624:
        raise ValueError('Duplicate relation specifications')
    return mapping, tests, relations


def execute(args):
    mapping, tests, relations = validate_plan(args.plan)
    log_path = Path(args.plan) / 'seed-journal.json'
    journal = load(log_path) if log_path.exists() else {'created_tests': {}, 'linked': [], 'failures': []}
    # The journal supplies newly created test IDs without overwriting baseline planner files.
    for key, value in journal['created_tests'].items():
        if not re.fullmatch(r'TC-\d{4}', key) or not isinstance(value, int) or value <= 0:
            raise ValueError('Invalid local seeding journal')
        mapping[key] = value
    if args.phase == 'tests':
        selection = tests[:args.limit]
        print(f'PLAN: {len(selection)} Test Cases, {sum(len(x["steps"]) for x in selection)} steps; total pending: {len(tests)}')
    else:
        possible = [r for r in relations if r['source_key'] in mapping and r['target_key'] in mapping]
        selection = possible[:args.limit]
        print(f'PLAN: {len(selection)} relationships currently mapped; 624 total planned')
        if len(possible) < 624:
            print('INFO: test-case links unavailable until all Test Cases are created and journaled')
    if not args.apply:
        print('DRY RUN: no network access, no Azure DevOps changes. Use --apply with explicit confirmation after review.')
        return 0
    if args.confirm != f'{ORG}/{PROJECT}':
        raise ValueError('Exact --confirm adotracefoundry-lab/SampleCommerce required for writes')
    client = Client(os.environ.get('ADO_SEED_PAT'))
    if args.phase == 'tests':
        existing = client.existing_tests()
        for test in selection:
            key = test['local_key']
            if key in existing:
                item = existing[key]
                if item['fields'].get('System.Title') != test['title']:
                    raise RuntimeError(f'Title mismatch for previously seeded {key}; manual review required')
                print(f'SKIP existing {key} = {item["id"]}')
                mapping[key] = item['id']
            else:
                item = client.create(create_patch(test))
                mapping[key] = int(item['id'])
                existing[key] = item
                print(f'CREATED {key} = {item["id"]}')
            journal['created_tests'][key] = mapping[key]
            write_atomic(log_path, journal)
            time.sleep(args.delay)
        # Never mutate planner id-map until reconciled after a full re-extraction.
        print('Test creation complete. Re-run ADOTraceFoundry sync and the offline planner to reconcile IDs.')
    else:
        # Each planned edge is prevalidated and must have two known IDs.
        for r in selection:
            key = f'{r["source_key"]}->{r["target_key"]}:{r["relation"]}'
            if key in journal['linked']:
                print('SKIP journal:', key)
                continue
            source, target = mapping[r['source_key']], mapping[r['target_key']]
            created = client.link(source, target, r['relation'])
            print(('LINKED' if created else 'SKIP existing'), key)
            journal['linked'].append(key)
            write_atomic(log_path, journal)
            time.sleep(args.delay)
        print('Link phase complete. Re-run read-only sync to verify graph.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic SampleCommerce test seeder; DRY RUN by default')
    parser.add_argument('--plan', type=Path, default=Path('output/v03-plan'))
    parser.add_argument('--phase', choices=('tests', 'links'), required=True)
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--delay', type=float, default=0.15)
    parser.add_argument('--apply', action='store_true', help='Enables ADO write calls')
    parser.add_argument('--confirm', default='', help='Must match adotracefoundry-lab/SampleCommerce')
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 624 or args.delay < 0:
        parser.error('Use 1 <= --limit <= 624 and nonnegative --delay')
    try:
        return execute(args)
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        print('ERROR:', e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
