"""Source-linked Azure DevOps asset processing (v0.2).

Read-only. All network activity uses AdoClient's authenticated, no-redirect,
allowlisted downloader. No model calls or external uploads.
"""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .documents import extract_text

IMAGE_FIELDS = ("System.Description", "Microsoft.VSTS.Common.AcceptanceCriteria",
                "Microsoft.VSTS.TCM.Steps")
IMAGE_MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff"}


class _ImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() == "img":
            src = dict(attrs).get("src", "")
            if src:
                self.sources.append(src)


def inline_references(items: list[dict]) -> list[dict]:
    """Discover images with ADO attachment URLs without exporting URL query strings."""
    refs = []
    for item in items:
        for field in IMAGE_FIELDS:
            value = item.get("fields", {}).get(field)
            if not isinstance(value, str):
                continue
            parser = _ImageParser()
            parser.feed(value)
            for index, source in enumerate(parser.sources, 1):
                refs.append({"work_item_id": int(item["id"]), "field": field,
                             "index": index, "url": source, "kind": "inline-image"})
    return refs


def _approved_attachment_url(url: str, organization: str) -> bool:
    """Only Azure DevOps attachment URLs for the requested organization."""
    p = urlsplit(url)
    return (p.scheme == "https" and p.hostname == "dev.azure.com" and not p.username
            and not p.password and p.port in (None, 443)
            and p.path.lower().startswith("/" + organization.lower() + "/")
            and re.search(r"/_apis/wit/attachments/[0-9a-f-]{36}$", p.path, re.I) is not None)


def _file_extension(data: bytes, filename: str, *, inline: bool) -> str:
    if data.startswith(IMAGE_MAGIC[".png"]):
        return ".png"
    if data.startswith(IMAGE_MAGIC[".jpg"]):
        return ".jpg"
    if inline:
        raise ValueError("Inline image did not contain PNG or JPEG content")
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in (".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".json", ".csv") else ".bin"


def process_assets(client, items: list[dict], attachments: list[dict], output: Path,
                   *, ocr: bool = False, max_files: int = 100) -> tuple[dict[str, str], list[dict]]:
    """Process attachment + inline image references, deduplicate by SHA-256.

    Returns text map for export() and a source-reference manifest. A failure on a
    single asset is recorded; other assets can still be processed.
    """
    store = output / "assets"
    store.mkdir(parents=True, exist_ok=True)
    refs = [{"work_item_id": a["work_item_id"], "kind": "attachment", "field": "relations",
             "name": a["name"], "url": a["url"]} for a in attachments]
    refs.extend(inline_references(items))
    manifest: list[dict] = []
    text_for_knowledge: dict[str, str] = {}
    cache: dict[str, tuple[str, str, str]] = {}
    for index, ref in enumerate(refs):
        wid = ref["work_item_id"]
        kind = ref["kind"]
        title = ref.get("name", f"inline-image-{ref.get('index', 1)}")
        entry = {"work_item_id": wid, "kind": kind, "source_field": ref["field"],
                 "name": title, "extraction_status": "pending"}
        manifest.append(entry)
        if index >= max_files:
            entry["extraction_status"] = "skipped: max-files-limit"
            continue
        if not _approved_attachment_url(ref["url"], client.org):
            entry["extraction_status"] = "rejected: unapproved-attachment-url"
            continue
        try:
            data = client.download_attachment(ref["url"])
            digest = hashlib.sha256(data).hexdigest()
            extension = _file_extension(data, title, inline=(kind == "inline-image"))
            path = store / (digest + extension)
            if not path.is_file():
                path.write_bytes(data)
            if digest not in cache:
                content, status = extract_text(path, ocr=ocr)
                cache[digest] = (str(path.relative_to(output)), content, status)
            relative, content, status = cache[digest]
            entry.update({"sha256": digest, "stored_path": relative, "size": len(data),
                          "extraction_status": status})
            if content.strip():
                # Include source details so image OCR is distinguishable from work-item text.
                description = (f"Source field: {ref['field']}\nAsset SHA256: {digest}\n"
                               f"Extraction: {status}\n\n{content}")
                key = f"{wid}:{title}"
                text_for_knowledge[key] = description
        except (RuntimeError, ValueError, OSError) as exc:
            entry["extraction_status"] = f"failed: {type(exc).__name__}"
    return text_for_knowledge, manifest
