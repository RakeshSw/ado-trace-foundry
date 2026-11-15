"""Public-safe CLI. No destructive ADO operations; no automatic data uploads."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from .core import build, export, write_json
from .documents import extract_text
from .asset_intel import process_assets


def load_items(path: Path) -> list[dict]:
    obj = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get('value'), list):
        return obj['value']
    raise ValueError('Expected JSON array of work items or an object with a value array')


def process_local(folder: Path, manifest: list[dict], *, ocr=False) -> dict[str, str]:
    out = {}
    for x in manifest:
        file = folder / x['file']
        if not file.is_file() or not file.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Attachment must be a local file under --attachments-dir')
        text, status = extract_text(file, ocr=ocr)
        out[f"{int(x['work_item_id'])}:{file.name}"] = text
        x['extraction_status'] = status
        x['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
    return out


def download_assets(client, attachments: list[dict], dest: Path, *, max_files=100, ocr=False) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    extracted = {}
    for ix, x in enumerate(attachments):
        if ix >= max_files:
            x['extraction_status'] = 'not-downloaded: max files limit'
            continue
        name = re.sub(r'[^a-zA-Z0-9._-]', '_', Path(x['name']).name)[:100].strip('.') or 'attachment.bin'
        data = client.download_attachment(x['url'])
        digest = hashlib.sha256(data).hexdigest()
        local = dest / f"{x['work_item_id']}-{digest[:10]}-{name}"
        local.write_bytes(data)
        content, status = extract_text(local, ocr=ocr)
        x.update({'sha256': digest, 'local_path': str(local), 'extraction_status': status, 'size': len(data)})
        extracted[f"{x['work_item_id']}:{x['name']}"] = content
    return extracted


def main(argv=None):
    p = argparse.ArgumentParser(description='ADOTraceFoundry: read-only Azure DevOps traceability → knowledge')
    sub = p.add_subparsers(dest='command', required=True)
    offline = sub.add_parser('demo', help='Export synthetic/offline work item JSON')
    offline.add_argument('--input', type=Path, default=Path('samples/workitems.json'))
    offline.add_argument('--output', type=Path, default=Path('output/demo'))
    offline.add_argument('--attachments-dir', type=Path)
    offline.add_argument('--ocr', action='store_true', help='OCR image attachments only if local dependencies exist')
    live = sub.add_parser('sync', help='Read Azure DevOps using WIQL, no writes')
    live.add_argument('--org', required=True)
    live.add_argument('--project', required=True)
    live.add_argument('--wiql-file', type=Path, required=True)
    live.add_argument('--output', type=Path, default=Path('output/live'))
    live.add_argument('--max-items', type=int, default=1000)
    live.add_argument('--download-attachments', action='store_true')
    live.add_argument('--ocr', action='store_true')
    ai = sub.add_parser('analyze-coverage', help='Read-only Azure OpenAI coverage assessment')
    ai.add_argument('--input', type=Path, required=True)
    ai.add_argument('--requirement-id', type=int, required=True)
    ai.add_argument('--provider', default='azure-openai', choices=['azure-openai'])
    ai.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'analyze-coverage':
            from .ai_coverage import run_coverage
            assessment = run_coverage(args.input, args.requirement_id, args.output, args.provider)
            counts = {}
            for row in assessment['criteria']:
                counts[row['status']] = counts.get(row['status'], 0) + 1
            print(json.dumps({'requirement_id': args.requirement_id,
                              'model': assessment['model'], 'criteria_by_status': counts,
                              'validation_warnings': len(assessment['validation_warnings']),
                              'output': str(args.output)}, indent=2))
            return 0
        if args.command == 'demo':
            items = load_items(args.input)
            data = build(items)
            manifest = []
            extra = {}
            if args.attachments_dir:
                manifest = json.loads((args.attachments_dir / 'manifest.json').read_text(encoding='utf-8'))
                extra = process_local(args.attachments_dir, manifest, ocr=args.ocr)
            export(data, args.output, attachment_text=extra)
            if manifest:
                write_json(args.output / 'local-attachment-manifest.json', manifest)
        else:
            from .ado import AdoClient
            if args.max_items < 1 or args.max_items > 10000:
                raise ValueError('--max-items must be between 1 and 10000')
            client = AdoClient(args.org, args.project)
            ids = client.query_ids(args.wiql_file.read_text(encoding='utf-8'))[:args.max_items]
            items = client.work_items(ids)
            data = build(items)
            args.output.mkdir(parents=True, exist_ok=True)
            write_json(args.output / 'source-workitems.json', items)
            if args.download_attachments:
                extra, asset_manifest = process_assets(client, items, data['attachments'], args.output, ocr=args.ocr)
            else:
                extra, asset_manifest = {}, []
            export(data, args.output, attachment_text=extra)
            if args.download_attachments:
                write_json(args.output / 'asset-manifest.json', asset_manifest)
                report = json.loads((args.output / 'manifest.json').read_text(encoding='utf-8'))
                report.update({'asset_references': len(asset_manifest),
                               'inline_image_references': sum(a['kind'] == 'inline-image' for a in asset_manifest),
                               'unique_downloaded_assets': len({a.get('sha256') for a in asset_manifest if a.get('sha256')}),
                               'failed_asset_references': sum(a['extraction_status'].startswith(('failed:', 'rejected:')) for a in asset_manifest)})
                write_json(args.output / 'manifest.json', report)
        print(f"Exported {len(data['nodes'])} work items, {len(data['edges'])} links, {len(data['traces'])} trace paths → {args.output}")
        return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as ex:
        print(f'ERROR: {ex}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
