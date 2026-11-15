#!/usr/bin/env python3
"""ADOTraceFoundry v0.3: offline, read-only mapping and seeding planner.

Maps the 630 synthetic reference items to ADO work items imported via CSV,
using type + exact normalized title, not spreadsheet row ID assumptions.
Produces a reviewable plan; DOES NOT connect to ADO or write anything.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PREFIXES = {'Epic':'EP', 'Feature':'FT', 'User Story':'US', 'Task':'TK', 'Bug':'BG', 'Test Case':'TC'}
EXPECTED = {'EP':6, 'FT':24, 'US':120, 'TK':180, 'BG':60, 'TC':240}
ALLOWED_RELATIONS = {'System.LinkTypes.Hierarchy-Forward', 'System.LinkTypes.Related',
                     'Microsoft.VSTS.Common.TestedBy-Forward'}

def csv_rows(path):
    with open(path, encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))

def key_for(kind, ordinal):
    return f'{PREFIXES[kind]}-{ordinal:04d}'

def reference_data(path):
    rows=csv_rows(path)
    counts=Counter()
    items=[]
    seen=set()
    for row in rows:
        kind=row['Work Item Type'].strip()
        if kind not in PREFIXES:
            raise ValueError(f'Unexpected work item type: {kind}')
        counts[kind] += 1
        local_key=key_for(kind, counts[kind])
        title=row['Title'].strip()
        fingerprint=(kind, title.casefold())
        if fingerprint in seen:
            raise ValueError(f'Ambiguous reference title: {kind}: {title}')
        seen.add(fingerprint)
        items.append({'local_key':local_key,'type':kind,'title':title,
                      'description':row.get('Description','')})
    if Counter(x['local_key'].split('-')[0] for x in items) != Counter(EXPECTED):
        raise ValueError('Reference file not expected 630-item dataset; refusing ambiguous mapping')
    return items

def load_ado(path):
    data=json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        data=data.get('value', [])
    if not isinstance(data, list):
        raise ValueError('ADO input must be JSON list or {"value": [...]}')
    return data

def build_plan(seed_dir, source_path):
    refs=reference_data(seed_dir/'ADO_Import_630.csv')
    refs_by_key={x['local_key']:x for x in refs}
    ado=load_ado(source_path)
    live=defaultdict(list)
    for x in ado:
        f=x.get('fields') or {}
        key=(str(f.get('System.WorkItemType','')).strip(), str(f.get('System.Title','')).strip().casefold())
        if not isinstance(x.get('id'), int):
            raise ValueError('ADO item lacks numeric ID')
        live[key].append({'id':x['id'],'revision':x.get('rev',0)})
    mapping={}
    not_found=[]
    ambiguous=[]
    for ref in refs:
        key=(ref['type'],ref['title'].casefold())
        found=live.get(key,[])
        if len(found)==1:
            mapping[ref['local_key']]=found[0]['id']
        elif len(found)>1:
            ambiguous.append({'local_key':ref['local_key'],'matches':found})
        else:
            not_found.append(ref)
    # Known 390 existing and 240 uncreated Test Cases is the valid baseline.
    unknown=[]
    expected_titles={(ref['type'],ref['title'].casefold()) for ref in refs}
    for key, records in live.items():
        if key not in expected_titles:
            unknown.extend({'id':r['id'],'type':key[0], 'title':key[1]} for r in records)
    rels=csv_rows(seed_dir/'Relationship_Plan.csv')
    planned=[]
    for r in rels:
        src=r['Source Local Key'].strip()
        dst=r['Target Local Key'].strip()
        kind=r['Relationship'].strip()
        if src not in refs_by_key or dst not in refs_by_key or kind not in ALLOWED_RELATIONS:
            raise ValueError(f'Invalid relationship specification: {src} -> {dst}: {kind}')
        planned.append({'source_key':src, 'target_key':dst, 'relation':kind,
                        'source_id':mapping.get(src), 'target_id':mapping.get(dst),
                        'ready':src in mapping and dst in mapping})
    steps=csv_rows(seed_dir/'Test_Steps_Plan.csv')
    step_groups=defaultdict(list)
    for r in steps:
        key=r['Test Case Local Key'].strip()
        if key not in refs_by_key or not key.startswith('TC-'):
            raise ValueError(f'Test step references unknown test case: {key}')
        step_groups[key].append({'step':int(r['Step']), 'action':r['Action'],
                                 'expected':r['Expected Result'], 'title':r['Step Title']})
    uncreated=[r for r in refs if r['local_key'] not in mapping and r['type']=='Test Case']
    for r in uncreated:
        r['steps']=sorted(step_groups.get(r['local_key'],[]),key=lambda a:a['step'])
    duplicates=Counter((x['source_key'], x['target_key'],x['relation']) for x in planned)
    duplicate_edges=[{'edge':list(k), 'count':v} for k,v in duplicates.items() if v>1]
    summary={'reference_items':len(refs),'ado_input_items':len(ado),'mapped_items':len(mapping),
             'unmapped_non_test_items':len([r for r in not_found if r['type']!='Test Case']),
             'uncreated_test_cases':len(uncreated),'ambiguous_matches':len(ambiguous),
             'unmatched_ado_items':len(unknown),'planned_relations':len(planned),
             'ready_relations':sum(r['ready'] for r in planned),
             'pending_relations':sum(not r['ready'] for r in planned),
             'duplicate_relation_specs':len(duplicate_edges),'test_step_rows':len(steps)}
    return {'summary':summary,'mapping':mapping,'uncreated_tests':uncreated,
            'relationships':planned,'ambiguous':ambiguous,
            'unmapped_non_test':[r for r in not_found if r['type']!='Test Case'],
            'unmatched_ado':unknown,'duplicate_edges':duplicate_edges}

def write_plan(plan,output):
    output.mkdir(parents=True,exist_ok=True)
    for key, filename in [('summary','summary.json'),('mapping','id-map.json'),
       ('uncreated_tests','testcases-to-create.json'),('relationships','relationships-to-create.json'),
       ('ambiguous','ambiguous-matches.json'),('unmapped_non_test','unmapped-non-test.json'),
       ('unmatched_ado','unmatched-ado.json'),('duplicate_edges','duplicate-relations.json')]:
        (output/filename).write_text(json.dumps(plan[key],indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main(argv=None):
    p=argparse.ArgumentParser(description='Offline ADOTraceFoundry seed/traceability plan, no Azure writes')
    p.add_argument('--ado-json',type=Path,required=True,help='Local source-workitems.json produced by sync')
    p.add_argument('--seed-dir',type=Path,default=Path('samples/seed'))
    p.add_argument('--output',type=Path,default=Path('output/v03-plan'))
    args=p.parse_args(argv)
    plan=build_plan(args.seed_dir,args.ado_json)
    write_plan(plan,args.output)
    print(json.dumps(plan['summary'],indent=2))
    issues=sum([plan['summary']['ambiguous_matches'],plan['summary']['unmapped_non_test_items'],
                plan['summary']['duplicate_relation_specs']])
    if issues:
        print('REVIEW REQUIRED: ambiguous, unmapped, or duplicate entries. Do not seed work items.')
        return 2
    print('OFFLINE PLAN ONLY: no Azure DevOps work items were created or modified.')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
