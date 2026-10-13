import csv
import io
import json
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from outreach.service import Service
from outreach.server import make_server, export_csv

TOKEN = 'synthetic-test-access-key-1234567890123456789'
PROFILE = {'username': 'demo_lina', 'full_name': 'Lina Beispiel', 'bio': 'Fotografie mit natürlichem Licht', 'language': 'de'}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('OUTREACH_TEST_TMP') or os.environ.get('TMPDIR'))
        self.service = Service(self.temp.name, {'id': 'test', 'min_score': 0})
        self.server = make_server(self.service, '127.0.0.1', 0, TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, auth=True, headers=None):
        merged = {'Content-Type': 'application/json'}
        if auth:
            merged['Authorization'] = 'Bearer ' + TOKEN
        merged.update(headers or {})
        req = Request(self.url + path, data=None if body is None else json.dumps(body).encode(), headers=merged)
        try:
            with urlopen(req) as response:
                return response.status, response.read(), response.headers
        except HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_authentication_and_static_allowlist(self):
        self.assertEqual(self.request('/api/state', auth=False)[0], 401)
        self.assertEqual(self.request('/api/import', {'profiles': [PROFILE]}, auth=False)[0], 401)
        self.assertEqual(self.request('/../../.env')[0], 404)
        code, html, headers = self.request('/', auth=False)
        self.assertEqual(code, 200)
        self.assertIn(b'Outreach Studio', html)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_foreign_origin_rejected(self):
        self.assertEqual(self.request('/api/import', {'profiles': [PROFILE]}, headers={'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.service.list_profiles(), [])

    def test_import_generate_approve_and_export(self):
        self.assertEqual(self.request('/api/import', {'profiles': [PROFILE]})[0], 200)
        code, body, _ = self.request('/api/generate', {'provider': 'template'})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)['jobs']), 1)
        self.service.run_once()
        state = json.loads(self.request('/api/state')[1])
        record = state['profiles'][0]
        self.assertEqual(len(record['drafts']), 3)
        self.assertEqual(self.request('/api/profile/demo_lina/sent', {})[0], 400)
        draft = record['drafts'][0]
        self.assertEqual(self.request('/api/profile/demo_lina/approve', {'draft_id': draft['id'], 'text': draft['text']})[0], 200)
        code, raw, _ = self.request('/api/export')
        self.assertEqual(code, 200)
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertEqual(rows[0]['text'], draft['text'])
        self.assertEqual(self.request('/api/profile/demo_lina/sent', {})[0], 200)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.request('/api/export')[1].decode('utf-8-sig'))))), 0)
        self.assertEqual(self.request('/api/profile/demo_lina/replied', {})[0], 200)

    def test_configuration_roundtrip_and_validation(self):
        agent = dict(self.service.campaign, name='Linas Agent', sender={'name': 'Lina', 'role': 'Fotografin', 'context': 'Geschäftlicher Austausch'}, offerings=['Portraits für Selbstständige'])
        self.assertEqual(self.request('/api/agent', agent)[0], 200)
        self.assertEqual(json.loads(self.request('/api/state')[1])['campaign']['sender']['name'], 'Lina')
        self.assertEqual(self.request('/api/agent', dict(agent, id='another'))[0], 400)

    def test_csv_formula_injection(self):
        raw = export_csv([{'username': 'user', 'message': '=1+1', 'profile_url': 'https://example.com', 'status': 'approved'}])
        row = next(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertEqual(row['text'], "'=1+1")

    def test_no_send_or_arbitrary_file_routes(self):
        self.assertEqual(self.request('/api/send', {'recipient': 'x'})[0], 404)
        self.assertEqual(self.request('/api/import', {'profiles': []})[0], 400)
        self.assertEqual(self.request('/api/import/apify', {'dataset_id': 'https://evil.example'})[0], 400)


if __name__ == '__main__':
    unittest.main()
