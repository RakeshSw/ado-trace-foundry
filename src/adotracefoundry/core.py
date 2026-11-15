"""Offline normalization and relationship reconstruction; never modifies work items."""
from __future__ import annotations
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")
_ID_END = re.compile(r"/(\d+)(?:\?.*)?$", re.I)


def plain(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    s = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6])\s*>", "\n", value)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = _TAG.sub(" ", s)
    return "\n".join(_WS.sub(" ", html.unescape(part)).strip() for part in s.splitlines() if part.strip())


def target_id(url: str) -> int | None:
    m = _ID_END.search(urlsplit(url).path)
    return int(m.group(1)) if m else None


def normalize(item: dict) -> dict:
    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
        raise ValueError("Each work item requires an integer id")
    fields = item.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    descriptions = {k: plain(v) for k, v in fields.items() if k in (
        "System.Description", "Microsoft.VSTS.Common.AcceptanceCriteria", "Microsoft.VSTS.TCM.Steps", "System.History") and v}
    return {
        "id": item["id"], "revision": item.get("rev", 0),
        "type": fields.get("System.WorkItemType", "Unknown"),
        "title": plain(fields.get("System.Title", "")),
        "state": fields.get("System.State", "Unknown"),
        "changed_at": fields.get("System.ChangedDate"),
        "url": item.get("_links", {}).get("html", {}).get("href") or item.get("url", ""),
        "descriptions": descriptions,
        "fields": fields,  # preserve all source fields without making assumptions about custom fields
    }


def extract_relations(items: list[dict]) -> tuple[list[dict], list[dict]]:
    edges = {}
    attachments = {}
    for item in items:
        item_id = item["id"]
        for rel in item.get("relations") or []:
            name = rel.get("rel", "")
            attrs = rel.get("attributes") or {}
            uri = rel.get("url", "")
            if name == "AttachedFile":
                key = (item_id, uri)
                attachments[key] = {"work_item_id": item_id, "url": uri,
                                    "name": str(attrs.get("name") or "attachment.bin")}
                continue
            other = target_id(uri)
            if other is None:
                continue
            # normalize direction so parent-child relationships are consistent
            if name.endswith("Hierarchy-Reverse"):
                source, target, kind = other, item_id, "parent-child"
            elif name.endswith("Hierarchy-Forward"):
                source, target, kind = item_id, other, "parent-child"
            else:
                source, target, kind = item_id, other, name or "related"
            key = (kind, source, target)
            edges[key] = {"kind": kind, "source": source, "target": target}
    return sorted(edges.values(), key=lambda x: (x["kind"], x["source"], x["target"])), sorted(
        attachments.values(), key=lambda x: (x["work_item_id"], x["name"], x["url"]))


def paths_from(start: int, adj: dict[int, set[int]], eligible: set[int]) -> list[list[int]]:
    # graph trace: only direct and two-step paths, avoids infinite cycles
    result = []
    for child in sorted(adj.get(start, set())):
        if child not in eligible:
            continue
        grandchildren = sorted(x for x in adj.get(child, set()) if x in eligible and x != start)
        if grandchildren:
            result.extend([[start, child, g] for g in grandchildren])
        else:
            result.append([start, child])
    return result


def build(items: list[dict]) -> dict:
    unique = {}
    for item in items:
        i = item.get("id")
        if not isinstance(i, int):
            raise ValueError("Work item ID must be integer")
        if i not in unique or int(item.get("rev", 0)) > int(unique[i].get("rev", 0)):
            unique[i] = item
    raw = list(unique.values())
    nodes = sorted((normalize(x) for x in raw), key=lambda x: x["id"])
    edges, attachments = extract_relations(raw)
    adj = defaultdict(set)
    for edge in edges:
        if edge["kind"] == "parent-child":
            adj[edge["source"]].add(edge["target"])
    ids = {n["id"] for n in nodes}
    known = {n["id"]: n for n in nodes}
    paths = []
    for node in nodes:
        if "requirement" in str(node["type"]).lower():
            for path in paths_from(node["id"], adj, ids):
                paths.append({"ids": path, "titles": [known[x]["title"] for x in path]})
    missing = sorted({x for e in edges for x in (e["source"], e["target"]) if x not in ids})
    return {"nodes": nodes, "edges": edges, "attachments": attachments,
            "traces": paths, "unresolved_link_ids": missing}


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def safe_markdown(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`")


def export(data: dict, output: Path, *, attachment_text: dict | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "relationships.json", data["edges"])
    write_json(output / "traceability.json", data["traces"])
    write_json(output / "attachment-manifest.json", data["attachments"])
    write_json(output / "unresolved-links.json", data["unresolved_link_ids"])
    nodes = data["nodes"]
    with (output / "workitems.jsonl").open("w", encoding="utf-8") as fp:
        for node in nodes:
            fp.write(json.dumps(node, ensure_ascii=False, sort_keys=True) + "\n")
    knowledge = output / "knowledge"
    knowledge.mkdir(exist_ok=True)
    contents = attachment_text or {}
    related = defaultdict(list)
    for edge in data["edges"]:
        related[edge["source"]].append(edge["target"])
        related[edge["target"]].append(edge["source"])
    with (output / "knowledge.jsonl").open("w", encoding="utf-8") as fp:
        for node in nodes:
            sections = [f"# {safe_markdown(node['title'])}", f"Source work item: {node['id']}",
                        f"Type: {node['type']}", f"Revision: {node['revision']}",
                        f"Related IDs: {', '.join(map(str, sorted(set(related[node['id']]))))}"]
            if node["url"]:
                sections.append(f"Source URL: {node['url']}")
            for field, txt in node["descriptions"].items():
                sections += [f"\n## {field}", txt]
            for key, text in contents.items():
                if key.startswith(f"{node['id']}:") and text:
                    sections += [f"\n## Attachment {key.split(':', 1)[1]}", text]
            body = "\n\n".join(sections) + "\n"
            (knowledge / f"{node['id']}.md").write_text(body, encoding="utf-8")
            doc_id = hashlib.sha256(f"workitem:{node['id']}:rev:{node['revision']}".encode()).hexdigest()[:20]
            fp.write(json.dumps({"id": doc_id, "source_id": node["id"], "revision": node["revision"],
                                 "type": node["type"], "content": body, "related_ids": sorted(set(related[node["id"]]))},
                                ensure_ascii=False) + "\n")
    write_json(output / "manifest.json", {"schema_version": "0.2", "workitems": len(nodes),
             "relations": len(data["edges"]), "attachments": len(data["attachments"]),
             "traces": len(data["traces"]), "unresolved_link_ids": len(data["unresolved_link_ids"]),
             "knowledge_documents": len(nodes), "note": "Source-linked export, not a vector index"})
    return output
