"""On-demand quote endpoint, compatible with Vercel Python Functions."""
import json
from http.server import BaseHTTPRequestHandler
from scripts.collect_quotes import collect_live

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            data = collect_live()
            code = 200
        except Exception:
            # Never pass cached quotes off as a successful fresh response.
            data = {'error': 'Market data provider unavailable; retry later.'}
            code = 502
        body = json.dumps(data, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
