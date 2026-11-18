"""Loopback-only, per-run live monitor. Reads Codex records; never calls models."""
import json
from pathlib import Path
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from monitor import Monitor, ROOT

RUNS = ROOT / 'runs'
PORT = 8768
monitors = {}
lock = threading.Lock()


def resolve(run_id):
    if not re.fullmatch(r'[a-f0-9]{24}', run_id):
        raise ValueError('Invalid run ID')
    path = RUNS / run_id / 'config.json'
    with lock:
        if run_id not in monitors:
            monitors[run_id] = Monitor(json.loads(path.read_text(encoding='utf-8')), path)
        return monitors[run_id]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{PORT}', f'localhost:{PORT}') or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_error(403)
            return
        route = urlsplit(self.path).path
        if route == '/health':
            body, mime = b'{"service":"agent-workflow","version":1}', 'application/json'
        else:
            match = re.fullmatch(r'/runs/([a-f0-9]{24})(/api/state|/diagram|/)?', route)
            if not match:
                self.send_error(404)
                return
            run_id, suffix = match[1], match[2]
            try:
                monitor = resolve(run_id)
                if suffix == '/api/state':
                    body, mime = monitor.snapshot(), 'application/json; charset=utf-8'
                elif suffix == '/diagram':
                    body, mime = (RUNS / run_id / 'diagram.html').read_bytes(), 'text/html; charset=utf-8'
                else:
                    html = (ROOT / 'index.html').read_text(encoding='utf-8')
                    base = '/runs/' + run_id
                    html = html.replace('src="/diagram?', 'src="' + base + '/diagram?').replace("fetch('/api/state')", "fetch('" + base + "/api/state')")
                    body, mime = html.encode(), 'text/html; charset=utf-8'
            except (OSError, ValueError, KeyError) as exc:
                self.send_error(404, 'Run unavailable')
                return
        self.send_response(200)
        for key, value in {'Content-Type': mime, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "frame-ancestors 'self'", 'Referrer-Policy': 'no-referrer'}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
