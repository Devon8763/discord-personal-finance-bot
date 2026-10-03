"""Full migration uses synthetic SQLite and a stubbed OAuth identity only."""
import re
import unittest
from pathlib import Path
from sqlite3 import OperationalError
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import life_ledger_service as service
import test_portable_life_backup as portable
from web.app import create_app
from web.settings import WebSettings


class WebLedgerBackupTests(unittest.TestCase):
    owner = '123456789012345678'

    def setUp(self):
        self.helper = portable.PortableLifeBackupTests('test_full_roundtrip_preserves_meaning_settings_and_history')
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.bk = self.helper.bk
        fixture = Path(__file__).parent / 'fixtures/portable_life_ledger.json'
        self.bk.restore_backup(self.owner, fixture.read_bytes())
        self.bk.restore_backup('999999999999999999', fixture.read_bytes())
        service.add_expense('999999999999999999', '1.29', '餐飲', 'OTHER-OWNER-ONLY', '2025-02-01')
        self.client = TestClient(create_app(WebSettings(session_secret='s' * 32)))
        self.addCleanup(self.client.close)

    def login(self, owner=None):
        with patch('web.auth.fetch_discord_user_id', new=AsyncMock(return_value=owner or self.owner)):
            self.assertEqual(self.client.get('/auth/discord/callback', follow_redirects=False).status_code, 303)
        response = self.client.get('/export/ledger')
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def test_authentication_and_csrf_precede_export(self):
        with patch.object(service, 'export_portable_backup') as export:
            for path in ('/export/ledger', '/export/ledger/payload'):
                response = self.client.get(path) if path.endswith('ledger') else self.client.post(path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            export.assert_not_called()
        token = self.login()
        with patch.object(service, 'export_portable_backup') as export:
            for body in ('', 'csrf_token=wrong', 'csrf_token=中文', f'csrf_token={token}&csrf_token={token}'):
                self.assertEqual(self.client.post('/export/ledger/payload', content=body,
                    headers={'content-type': 'application/x-www-form-urlencoded'}).status_code, 403)
            export.assert_not_called()

    def test_bounded_body_and_malformed_requests_never_export(self):
        token = self.login()
        with patch.object(service, 'export_portable_backup') as export:
            response = self.client.post('/export/ledger/payload', content='x' * 1025,
                headers={'content-type': 'application/x-www-form-urlencoded'})
            self.assertEqual(response.status_code, 400)
            for body, mime in [(b'\xff', 'application/x-www-form-urlencoded'),
                               (f'csrf_token={token}', 'text/plain')]:
                response = self.client.post('/export/ledger/payload', content=body, headers={'content-type': mime})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            export.assert_not_called()

    def test_injected_identity_password_and_queries_are_rejected(self):
        token = self.login()
        with patch.object(service, 'export_portable_backup') as export:
            for key in ('user_id', 'owner', 'password', 'mode'):
                response = self.client.post('/export/ledger/payload', data={'csrf_token': token, key: 'PRIVATE'})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('PRIVATE', response.text)
            self.assertEqual(self.client.post('/export/ledger/payload?user_id=OTHER', data={'csrf_token': token}).status_code, 400)
            export.assert_not_called()

    def test_owner_full_export_and_readonly_roundtrip(self):
        token = self.login()
        before = self.helper.snapshot()
        with patch.object(service, 'export_portable_backup', wraps=service.export_portable_backup) as export:
            response = self.client.post('/export/ledger/payload', data={'csrf_token': token})
            export.assert_called_once_with(self.owner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.bk.export_backup(self.owner))
        self.assertNotIn(b'OTHER-OWNER-ONLY', response.content)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('content-disposition', response.headers)
        self.assertEqual(before, self.helper.snapshot())
        bundle = self.bk.read_backup(response.content)
        self.assertEqual(len(bundle['data']), 9)
        self.assertTrue(any(row['voided'] for row in bundle['data']['expenses']))
        self.assertTrue(any(row['revision'] > 2**53 for row in bundle['data']['expenses']))
        self.bk.restore_backup('blank-target', response.content)
        self.assertEqual(response.content, self.bk.export_backup('blank-target'))

    def test_empty_owner_export_does_not_initialize_any_records(self):
        token = self.login('111111111111111111')
        before = self.helper.snapshot()
        response = self.client.post('/export/ledger/payload', data={'csrf_token': token})
        self.assertEqual(response.status_code, 200)
        data = self.bk.read_backup(response.content)['data']
        self.assertTrue(all(not rows for key, rows in data.items() if key != 'settings'))
        self.assertEqual(before, self.helper.snapshot())

    def test_export_errors_and_exact_capacity_boundary_are_safe(self):
        token = self.login()
        before = self.helper.snapshot()
        for failure in (ValueError('PRIVATE-DATA'), OperationalError('PRIVATE-SQL'), RuntimeError('PRIVATE-DETAIL')):
            with patch.object(service, 'export_portable_backup', side_effect=failure):
                response = self.client.post('/export/ledger/payload', data={'csrf_token': token})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn('PRIVATE', response.text)
        payload = self.bk.export_backup(self.owner)
        with patch.object(self.bk, 'MAX_BYTES', len(payload)):
            self.assertEqual(self.client.post('/export/ledger/payload', data={'csrf_token': token}).status_code, 200)
        with patch.object(self.bk, 'MAX_BYTES', len(payload) - 1):
            self.assertEqual(self.client.post('/export/ledger/payload', data={'csrf_token': token}).status_code, 503)
        self.assertEqual(before, self.helper.snapshot())

    def test_page_and_exact_local_modules_keep_security_and_existing_csv(self):
        self.login()
        page = self.client.get('/export/ledger')
        self.assertIn("script-src 'self'", page.headers['content-security-policy'])
        self.assertIn('不會加密 IndexedDB', page.text)
        self.assertNotIn('name="password"', page.text)
        self.assertIn('/export/ledger', self.client.get('/settings').text)
        self.assertIn('下載 CSV', self.client.get('/export').text)
        for name, path in [('backup-crypto.mjs','local-first/backup-crypto.mjs'),
                           ('backup.mjs','local-first/backup.mjs'), ('rules.mjs','local-first/rules.mjs'),
                           ('vendor/lossless-json-4.3.1/lossless-json.js','local-first/vendor/lossless-json-4.3.1/lossless-json.js')]:
            response = self.client.get('/export/ledger/modules/' + name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, (Path(__file__).parents[1] / path).read_bytes())
        for path in ('serve.mjs', 'ledger.mjs', 'page.mjs', '.env', 'vendor/'):
            self.assertEqual(self.client.get('/export/ledger/modules/' + path).status_code, 404)
