"""Client HTTP minimal (urllib) avec lecture en flux. Remplaçable par un faux transport dans les tests."""
import json, urllib.error, urllib.request


class HTTPError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f'HTTP {status} : {body[:500]}')


class Transport:
    def __init__(self, timeout=600):
        self.timeout = timeout

    def _open(self, method, url, headers, body, timeout):
        data = json.dumps(body).encode() if body is not None else None
        h = {'Content-Type': 'application/json', 'User-Agent': 'livequill'} if data is not None else {'User-Agent': 'livequill'}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            raise HTTPError(e.code, e.read().decode('utf-8', 'replace')) from None

    def json(self, method, url, headers=None, body=None, timeout=15):
        with self._open(method, url, headers, body, timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def lines(self, method, url, headers=None, body=None, job=None):
        """Itère les lignes d'une réponse en flux (SSE ou NDJSON). job.on_cancel ferme la connexion."""
        r = self._open(method, url, headers, body, None)
        if job:
            job.on_cancel(r.close)
        try:
            for raw in r:
                yield raw.decode('utf-8', 'replace').rstrip('\r\n')
        except (OSError, ValueError):
            if not (job and job.cancelled):
                raise
        finally:
            r.close()


def sse(lines):
    """Lignes SSE -> (event, data). Les lignes vides séparent les messages."""
    event, data = None, []
    for ln in lines:
        if ln == '':
            if data:
                yield event, '\n'.join(data)
            event, data = None, []
        elif ln.startswith(':'):
            continue
        elif ln.startswith('event:'):
            event = ln[6:].strip()
        elif ln.startswith('data:'):
            v = ln[5:]
            data.append(v[1:] if v.startswith(' ') else v)
    if data:
        yield event, '\n'.join(data)
