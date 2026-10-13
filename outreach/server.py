"""Local authenticated HTTP workbench; no Instagram sending capability."""
import csv
import io
import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

from .service import Service
from .providers import import_apify_dataset

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 2 * 1024 * 1024


def export_csv(rows):
    out = io.StringIO(newline='')
    fields = ['username', 'profile_url', 'text', 'status']
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        draft = row.get('approved_draft') or {}
        profile = row.get('profile') or {}
        values = {'username': row.get('username', ''), 'profile_url': row.get('profile_url', profile.get('profile_url', '')),
                  'text': row.get('message', row.get('text', draft.get('text', ''))), 'status': row.get('status', 'approved')}
        writer.writerow({k: "'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else v for k, v in values.items()})
    return out.getvalue().encode('utf-8-sig')


def make_server(service, host, port, token):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('OUTREACH_ACCESS_TOKEN braucht mindestens 32 Zeichen.')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Outreach'
        sys_version = ''

        def log_message(self, *_):
            pass  # No profile data, bearer tokens or URLs in request logs.

        def reply(self, code, payload, content_type='application/json; charset=utf-8', download=False):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="approved-outreach.csv"')
            self.end_headers()
            self.wfile.write(data)

        def authenticated(self):
            supplied = self.headers.get('Authorization', '')
            expected = 'Bearer ' + token
            if not secrets.compare_digest(supplied.encode(), expected.encode()):
                self.reply(401, {'error': 'Zugangsschlüssel fehlt oder ist ungültig.'})
                return False
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ('http', 'https') or parsed.netloc != self.headers.get('Host'):
                    self.reply(403, {'error': 'Diese Herkunft ist nicht erlaubt.'})
                    return False
            return True

        def body(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise ValueError('Ungültige Inhaltslänge.')
            if size <= 0 or size > MAX_BODY:
                raise ValueError('JSON-Anfrage muss zwischen 1 Byte und 2 MB liegen.')
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Content-Type muss application/json sein.')
            value = json.loads(self.rfile.read(size))
            if not isinstance(value, dict):
                raise ValueError('JSON-Objekt erwartet.')
            return value

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/health':
                return self.reply(200, {'status': 'ok'})
            if path.startswith('/api/'):
                if not self.authenticated():
                    return
                try:
                    if path == '/api/state':
                        return self.reply(200, {'campaign': service.campaign, 'profiles': service.list_profiles(),
                                                'jobs': service.jobs(), 'stats': service.stats(),
                                                'providers': ['template'] + (['openai'] if os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL') else [])})
                    if path == '/api/export':
                        return self.reply(200, export_csv(service.export_rows()), 'text/csv; charset=utf-8', True)
                    return self.reply(404, {'error': 'Unbekannte Schnittstelle.'})
                except Exception:
                    return self.reply(500, {'error': 'Daten konnten nicht geladen werden.'})
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(path)
            if not name:
                return self.reply(404, {'error': 'Nicht gefunden.'})
            return self.reply(200, (STATIC / name).read_bytes(), (mimetypes.guess_type(name)[0] or 'text/plain') + '; charset=utf-8')

        def do_POST(self):
            if not self.authenticated():
                return
            try:
                body = self.body()
                path = urlsplit(self.path).path
                if path == '/api/agent':
                    result = service.configure_agent(body)
                elif path == '/api/import':
                    rows = body.get('profiles')
                    if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
                        raise ValueError('Bitte 1 bis 5.000 Profile als JSON-Liste importieren.')
                    result = service.import_profiles(rows)
                elif path == '/api/import/apify':
                    limit = body.get('max_items', 100)
                    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
                        raise ValueError('Maximal 1.000 Datensätze pro Import.')
                    rows = import_apify_dataset(body.get('dataset_id', ''), max_items=limit)
                    result = service.import_profiles(rows)
                elif path == '/api/generate':
                    provider = body.get('provider', 'template')
                    if provider not in ('template', 'openai'):
                        raise ValueError('Unbekannter Textanbieter.')
                    names = [body['username']] if body.get('username') else [p['username'] for p in service.list_profiles() if p['status'] in ('pending', 'review') and not p.get('drafts') and (p.get('analysis') or {}).get('eligible')]
                    result = {'jobs': [service.enqueue(name, provider=provider) for name in names]}
                elif path.startswith('/api/profile/'):
                    parts = path.split('/')
                    if len(parts) != 5:
                        raise ValueError('Ungültiger Profilpfad.')
                    username, action = unquote(parts[3]), parts[4]
                    if action == 'approve':
                        result = service.approve(username, body.get('draft_id'), text=body.get('text'))
                    elif action == 'sent':
                        result = service.mark_sent(username)
                    elif action == 'replied':
                        result = service.mark_replied(username)
                    elif action == 'suppress':
                        result = service.suppress(username)
                    else:
                        return self.reply(404, {'error': 'Unbekannte Aktion.'})
                else:
                    return self.reply(404, {'error': 'Unbekannte Schnittstelle.'})
                self.reply(200, result)
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, {'error': str(exc)[:400]})
            except Exception:
                self.reply(500, {'error': 'Aktion fehlgeschlagen. Konfiguration und Aufgabenstatus prüfen.'})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(service, host='127.0.0.1', port=8787, token=None):
    token = token or secrets.token_urlsafe(32)
    server = make_server(service, host, port, token)
    stopped = threading.Event()

    def worker():
        while not stopped.is_set():
            try:
                worked = service.run_once()
            except Exception:
                worked = False
            stopped.wait(0.2 if worked else 1)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print('Outreach bereit: http://{}:{}/'.format(host, server.server_port), flush=True)
    print('Zugangsschlüssel: ' + token, flush=True)
    print('Erstnachrichten werden vorbereitet. Versand erfolgt manuell in Instagram.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        server.server_close()
        thread.join(timeout=2)
