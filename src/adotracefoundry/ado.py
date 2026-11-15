"""Read-only Azure DevOps REST retrieval with bounded paging and retries."""
from __future__ import annotations
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward PAT-bearing requests to a redirect destination."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Redirect refused to prevent credential forwarding; download from an approved endpoint')

class AdoClient:
    def __init__(self, org: str, project: str, pat: str | None = None):
        if not org or not project or any(c in org for c in '/?@#'):
            raise ValueError('Use an Azure DevOps organization slug and project name')
        self.base = f"https://dev.azure.com/{urllib.parse.quote(org)}/{urllib.parse.quote(project, safe='')}"
        self.pat = pat or os.environ.get('ADO_PAT')
        if not self.pat:
            raise ValueError('ADO_PAT environment variable is required for live extraction')
    def _request(self, url: str, *, method='GET', payload: dict | None = None, max_bytes=12_000_000) -> bytes:
        if not url.startswith('https://'):
            raise ValueError('Only HTTPS requests are allowed')
        host = urllib.parse.urlsplit(url).hostname
        if host not in ('dev.azure.com', 'vssps.dev.azure.com') and not (host or '').endswith('.visualstudio.com'):
            raise ValueError('Refusing external attachment or API host')
        auth = base64.b64encode((':' + self.pat).encode()).decode()
        headers = {'Authorization': 'Basic ' + auth, 'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(4):
            try:
                with urllib.request.build_opener(_NoRedirect()).open(req, timeout=40) as r:
                    contents = r.read(max_bytes + 1)
                    if len(contents) > max_bytes:
                        raise ValueError('Server response exceeds configured size limit')
                    return contents
            except urllib.error.HTTPError as ex:
                if ex.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise RuntimeError(f'ADO request returned HTTP {ex.code}') from None
                time.sleep(min(2 ** attempt, 6))
        raise RuntimeError('Request failed')
    def json(self, url: str, *, method='GET', payload=None) -> dict:
        return json.loads(self._request(url, method=method, payload=payload))
    def query_ids(self, wiql: str) -> list[int]:
        url = self.base + '/_apis/wit/wiql?api-version=7.1'
        obj = self.json(url, method='POST', payload={'query': wiql})
        return sorted({int(x['id']) for x in obj.get('workItems', [])})
    def work_items(self, ids: list[int]) -> list[dict]:
        items = []
        for start in range(0, len(ids), 100):
            subset = ids[start:start+100]
            q = urllib.parse.urlencode({'ids': ','.join(map(str, subset)), '$expand': 'Relations', 'api-version': '7.1'})
            obj = self.json(self.base + '/_apis/wit/workitems?' + q)
            items.extend(obj.get('value', []))
        return items
    def download_attachment(self, url: str, *, max_bytes=15_000_000) -> bytes:
        return self._request(url, max_bytes=max_bytes)
