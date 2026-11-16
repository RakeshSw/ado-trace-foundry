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


def edit_synthetic_item(workspace: Workspace, item_id: int, *, title: str,
                        description: str, acceptance_criteria: str = '') -> int:
    """Modify an existing simulated ADO item; source changes remain unsynced."""
    if type(item_id) is not int or item_id not in workspace.source:
        raise ValueError('Unknown work item')
    values = [title.strip(), description.strip(), acceptance_criteria.strip()]
    if not values[0] or not values[1] or any(len(x) > 8000 for x in values):
        raise ValueError('Non-empty title and description required; each field <= 8000 characters')
    node = deepcopy(workspace.source[item_id])
    node['title'] = values[0]
    node['revision'] = int(node.get('revision', 0)) + 1
    node.setdefault('fields', {}).update({'System.Title': values[0], 'System.Description': values[1]})
    node.setdefault('descriptions', {})['System.Description'] = values[1]
    if values[2]:
        node['fields']['Microsoft.VSTS.Common.AcceptanceCriteria'] = values[2]
        node['descriptions']['Microsoft.VSTS.Common.AcceptanceCriteria'] = values[2]
    workspace.source[item_id] = node
    return item_id


def change_synthetic_link(workspace: Workspace, source: int, target: int,
                          kind: str, *, add: bool) -> bool:
    """Change only the in-memory simulated graph, never real Azure DevOps."""
    if kind not in {'parent-child', 'tested-by', 'related'}:
        raise ValueError('Unsupported relation type')
    if type(source) is not int or type(target) is not int or source == target:
        raise ValueError('Invalid source/target IDs')
    if source not in workspace.source or target not in workspace.source:
        raise ValueError('Both items must exist in the simulated source')
    src, dst = workspace.source[source], workspace.source[target]
    if kind == 'tested-by' and (src['type'] not in {'User Story','Requirement','Product Backlog Item'} or dst['type'] != 'Test Case'):
        raise ValueError('Tested-by requires requirement → Test Case')
    if kind == 'parent-child' and src['type'] not in {'Epic','Feature','User Story','Product Backlog Item','Requirement'}:
        raise ValueError('Invalid hierarchy parent')
    if kind == 'related':
        source, target = sorted((source, target))
    key = (kind, source, target)
    exists = any((e['kind'], int(e['source']), int(e['target'])) == key for e in workspace.source_edges)
    if add and not exists:
        workspace.source_edges.append({'kind': kind, 'source': source, 'target': target})
        return True
    if not add and exists:
        workspace.source_edges = [e for e in workspace.source_edges
                                  if (e['kind'], int(e['source']), int(e['target'])) != key]
        return True
    return False


def create_synthetic_test_case(workspace: Workspace, *, title: str, action: str,
                               expected: str, requirement_id: int | None = None) -> int:
    """Add a simple one-step test to the simulated source; requires explicit sync."""
    import xml.etree.ElementTree as ET
    values = [title.strip(), action.strip(), expected.strip()]
    if any(not x or len(x) > 8000 for x in values):
        raise ValueError('Title, action and expected result must be non-empty and <= 8000 characters')
    if requirement_id is not None and (requirement_id not in workspace.source or
                                      workspace.source[requirement_id]['type'] not in {'User Story', 'Requirement', 'Product Backlog Item'}):
        raise ValueError('Select an existing requirement')
    item_id = max(workspace.source, default=0) + 1
    root = ET.Element('steps', {'id': '0', 'last': '1'})
    step = ET.SubElement(root, 'step', {'id': '1', 'type': 'ValidateStep'})
    for part in (values[1], values[2]):
        el = ET.SubElement(step, 'parameterizedString', {'isformatted': 'true'})
        el.text = part
    ET.SubElement(step, 'description')
    node = {'id': item_id, 'type': 'Test Case', 'title': values[0], 'revision': 1,
            'state': 'Design', 'url': '', 'changed_at': None,
            'fields': {'System.WorkItemType': 'Test Case', 'System.Title': values[0],
                       'Microsoft.VSTS.TCM.Steps': ET.tostring(root, encoding='unicode')},
            'descriptions': {}}
    workspace.source[item_id] = node
    if requirement_id is not None:
        change_synthetic_link(workspace, requirement_id, item_id, 'tested-by', add=True)
    return item_id
