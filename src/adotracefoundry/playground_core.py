"""Session-scoped synthetic ADO simulator and deterministic incremental knowledge sync.

No Azure DevOps network requests or writes. Changes to source are NOT immediately
visible in synced knowledge. All inputs must be synthetic/public-safe.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def fingerprint(obj: Any) -> str:
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def load_export(folder: Path) -> "Workspace":
    folder = Path(folder)
    with (folder / 'workitems.jsonl').open(encoding='utf-8') as fp:
        nodes = [json.loads(line) for line in fp if line.strip()]
    edges = json.loads((folder / 'relationships.json').read_text(encoding='utf-8'))
    return Workspace(nodes, edges)


def _node_text(node: dict, neighbors: list[dict]) -> str:
    parts = [f"# {node['title']}", f"Source work item: {node['id']}",
             f"Type: {node['type']}", f"Revision: {node['revision']}"]
    fields = node.get('fields', {})
    for key in ('System.Description', 'Microsoft.VSTS.Common.AcceptanceCriteria'):
        value = fields.get(key)
        if value:
            parts.extend([f'\n## {key}', str(value)])
    parts.append('\n## Graph references')
    for edge in neighbors:
        parts.append(f"- {edge['kind']}: {edge['source']} → {edge['target']}")
    return '\n\n'.join(parts) + '\n'


class Workspace:
    """In-memory, per-session source and knowledge snapshots. No persistence by design."""
    def __init__(self, nodes: list[dict], edges: list[dict]):
        ids = [int(n['id']) for n in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate work item IDs')
        self.source = {int(n['id']): deepcopy(n) for n in nodes}
        self.source_edges = deepcopy(edges)
        self.synced = deepcopy(self.source)
        self.synced_edges = deepcopy(edges)
        self.documents = {i: _node_text(n, self._neighbors(i, edges)) for i, n in self.synced.items()}
        self.run_number = 0
        self.last_report: dict | None = None

    @staticmethod
    def _neighbors(id_: int, edges: list[dict]) -> list[dict]:
        return sorted((e for e in edges if int(e['source']) == id_ or int(e['target']) == id_),
                      key=lambda e: (e['kind'], int(e['source']), int(e['target'])))

    def save_story(self, *, title: str, description: str, acceptance_criteria: str,
                   parent_id: int | None = None, item_id: int | None = None) -> int:
        title, description, acceptance_criteria = (x.strip() for x in (title, description, acceptance_criteria))
        if not title or not description or not acceptance_criteria:
            raise ValueError('Title, description and acceptance criteria are required')
        if any(len(x) > 8000 for x in (title, description, acceptance_criteria)):
            raise ValueError('A story field exceeds 8,000 characters')
        if parent_id is not None:
            if parent_id not in self.source or self.source[parent_id]['type'] != 'Feature':
                raise ValueError('Parent must be an existing Feature')
        if item_id is None:
            item_id = max(self.source, default=0) + 1
            if item_id in self.source:
                raise ValueError('New ID collision')
            node = {'id': item_id, 'type': 'User Story', 'revision': 1, 'title': title,
                    'state': 'New', 'url': '', 'changed_at': None,
                    'fields': {'System.WorkItemType': 'User Story', 'System.Title': title,
                               'System.Description': description,
                               'Microsoft.VSTS.Common.AcceptanceCriteria': acceptance_criteria},
                    'descriptions': {}}
        else:
            if item_id not in self.source or self.source[item_id]['type'] != 'User Story':
                raise ValueError('Only User Stories can be edited in the MVP')
            node = deepcopy(self.source[item_id])
            node['revision'] = int(node.get('revision', 0)) + 1
            node['title'] = title
            node.setdefault('fields', {}).update({
                'System.Title': title, 'System.Description': description,
                'Microsoft.VSTS.Common.AcceptanceCriteria': acceptance_criteria})
        node['descriptions'] = {'System.Description': description,
                                'Microsoft.VSTS.Common.AcceptanceCriteria': acceptance_criteria}
        self.source[item_id] = node
        if parent_id is not None:
            self.source_edges = [e for e in self.source_edges
                                 if not (e['kind'] == 'parent-child' and int(e['target']) == item_id)]
            self.source_edges.append({'kind': 'parent-child', 'source': parent_id, 'target': item_id})
        return item_id

    def diff(self) -> dict:
        created = sorted(set(self.source) - set(self.synced))
        changed = sorted(i for i in set(self.source) & set(self.synced)
                         if fingerprint(self.source[i]) != fingerprint(self.synced[i]))
        def edgekey(e):
            return e['kind'], int(e['source']), int(e['target'])
        src = {edgekey(e) for e in self.source_edges}
        dst = {edgekey(e) for e in self.synced_edges}
        return {'created_ids': created, 'modified_ids': changed,
                'added_edges': sorted(src - dst), 'removed_edges': sorted(dst - src),
                'source_count': len(self.source), 'synced_count': len(self.synced)}

    def sync(self):
        """Yield actual progress checkpoints; apply delta without rewriting untouched documents."""
        before = {'workitems': len(self.synced), 'relationships': len(self.synced_edges),
                  'knowledge_documents': len(self.documents)}
        delta = self.diff()
        yield {'stage': 'Detect changes', 'detail': delta}
        touched = set(delta['created_ids'] + delta['modified_ids'])
        changed_edges = delta['added_edges'] + delta['removed_edges']
        for kind, src, dst in changed_edges:
            touched.add(src)
            touched.add(dst)
        yield {'stage': 'Extract changed source records', 'detail': {'records': len(touched)}}
        for i in touched:
            if i in self.source:
                self.synced[i] = deepcopy(self.source[i])
        yield {'stage': 'Normalize changed records', 'detail': {'records': len(touched)}}
        self.synced_edges = deepcopy(self.source_edges)
        yield {'stage': 'Reconcile relationship graph', 'detail': {'changed_edges': len(changed_edges)}}
        # Document content includes adjacent edges but not adjacent node descriptions.
        regenerated = sorted(i for i in touched if i in self.synced)
        for i in regenerated:
            self.documents[i] = _node_text(self.synced[i], self._neighbors(i, self.synced_edges))
        yield {'stage': 'Regenerate affected knowledge', 'detail': {'documents': len(regenerated)}}
        # No semantic/vector index implemented yet; do not claim embeddings were updated.
        yield {'stage': 'Refresh deterministic lookup', 'detail': {'indexed_items': len(self.synced)}}
        if self.diff()['created_ids'] or self.diff()['modified_ids'] or self.diff()['added_edges'] or self.diff()['removed_edges']:
            raise AssertionError('Sync left unprocessed changes')
        after = {'workitems': len(self.synced), 'relationships': len(self.synced_edges),
                 'knowledge_documents': len(self.documents)}
        report = {'before': before, 'after': after, 'delta': delta,
                  'regenerated_document_ids': regenerated,
                  'unchanged_documents': len(self.synced) - len(regenerated),
                  'embeddings_refreshed': 0,
                  'model_assessments_invalidated': sorted(touched),
                  'mode': 'synthetic-ado-incremental', 'sync_number': self.run_number + 1}
        self.run_number += 1
        self.last_report = report
        yield {'stage': 'Validate consistency', 'detail': {'remaining_changes': 0}}
        return report
