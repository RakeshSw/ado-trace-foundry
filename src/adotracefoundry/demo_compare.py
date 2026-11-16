"""Synchronized-evidence-only before/after demo for a newly saved synthetic User Story.

Before answer is deterministic and never sent to a model: the new work item is absent
from the synchronized snapshot. After answer uses the existing Redis-gated Foundry
explanation with the same frozen question and an explicit, validated item lookup.
"""
from __future__ import annotations

import re
from .engineering_agent import lookup, explain, verified_test_summary


def freeze_question(question: str, item_id: int) -> str:
    if type(item_id) is not int or item_id <= 0:
        raise ValueError('Invalid work item ID')
    if not isinstance(question, str) or not question.strip():
        raise ValueError('Demo question is required')
    result = question.strip().replace('{work_item_id}', str(item_id))
    # A model occasionally ends its question mid-phrase: '... for work item'.
    # Complete the phrase with the assigned ID rather than awkwardly prefixing it.
    if not re.search(r'(?<!\d)' + str(item_id) + r'(?!\d)', result):
        if re.search(r'(?i)\b(?:work\s*item|user\s*story|story|requirement)\s*[#:]?\s*[?.!]*$', result):
            result = re.sub(r'[?.!]*\s*$', '', result).rstrip() + f' {item_id}?'
        else:
            result = f'For work item {item_id}: ' + result
    if len(result) > 750:
        raise ValueError('Demo question is too long')
    return result


def capture_before(workspace, item_id: int, question: str) -> dict:
    """Capture what synchronized knowledge knows, not what the simulated source knows."""
    if item_id not in workspace.source or workspace.source[item_id]['type'] != 'User Story':
        raise ValueError('Select a newly saved synthetic User Story')
    if item_id in workspace.synced:
        raise ValueError('Before-sync snapshot must be captured before synchronization')
    evidence = lookup(workspace, item_id)
    if 'error' not in evidence or evidence.get('pending_sync') is not True:
        raise RuntimeError('Synchronized-state separation could not be confirmed')
    frozen = freeze_question(question, item_id)
    return {
        'item_id': item_id, 'question': frozen,
        'answer': (f'Work item {item_id} is not present in synchronized knowledge yet. '
                   'Its source changes are pending; I cannot verify its requirements, '
                   'relationships or test coverage using this knowledge snapshot. '
                   'Run incremental sync to make its evidence available.'),
        'evidence': evidence,
        'synced_count': len(workspace.synced),
        'relationship_count': len(workspace.synced_edges),
        'found_in_synced_knowledge': False,
        'model_calls': 0,
    }


def answer_after(workspace, before: dict, *, visitor_id: str, limiter=None, model_call=None) -> dict:
    """Answer the SAME frozen question, using only synchronized knowledge."""
    item_id = before.get('item_id')
    question = before.get('question')
    if type(item_id) is not int or not isinstance(question, str):
        raise ValueError('Invalid recorded before-sync comparison')
    if item_id not in workspace.synced:
        raise ValueError('Synchronize this work item before asking the after question')
    evidence = lookup(workspace, item_id)
    if 'error' in evidence:
        raise RuntimeError('Synchronized item could not be retrieved')
    # Direct deterministic lookup, not a model route; prevents the model selecting
    # a different item or source and avoids an unnecessary second model call.
    plan = {'tool': 'work_item', 'args': {'id': item_id}}
    answer = explain(question, plan, evidence, visitor_id=visitor_id,
                     limiter=limiter, model_call=model_call)
    linked_tests = evidence['linked_test_case_ids']
    # The model explains documented intent, while this authoritative statement
    # is computed from actual tested-by edges (not a semantic guess).
    answer += '\n\n### Verified graph evidence\n' + verified_test_summary(evidence)
    return {
        'item_id': item_id, 'question': question, 'answer': answer,
        'evidence': evidence,
        'synced_count': len(workspace.synced),
        'relationship_count': len(workspace.synced_edges),
        'found_in_synced_knowledge': True,
        'linked_test_ids': sorted(set(linked_tests)),
        'structural_coverage': 'linked' if linked_tests else 'no-linked-tests',
        'model_calls': 1,
    }
