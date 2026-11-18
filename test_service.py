import http.client
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import workflow_service as service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), service.Handler)
        self.port = self.server.server_address[1]
        self.port_patch = patch.object(service, 'PORT', self.port)
        self.port_patch.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.port_patch.stop()

    def request(self, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request('GET', path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, response.read().decode()
        conn.close()
        return result

    def test_blocks_foreign_host_and_cross_site(self):
        self.assertEqual(self.request('/health', {'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('/health', {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/health')[0], 200)

    def test_each_page_polls_only_its_own_run(self):
        fake = SimpleNamespace(snapshot=lambda: b'{"agents":[]}')
        with patch.object(service, 'resolve', return_value=fake):
            first = 'a' * 24
            second = 'b' * 24
            code, page = self.request('/runs/' + first)
            self.assertEqual(code, 200)
            self.assertIn("fetch('/runs/" + first + "/api/state')", page)
            self.assertIn('src="/runs/' + first + '/diagram?', page)
            self.assertNotIn(second, page)
            self.assertEqual(self.request('/runs/' + second + '/api/state')[0], 200)
        self.assertEqual(self.request('/runs/../../config.json')[0], 404)


if __name__ == '__main__':
    unittest.main()
