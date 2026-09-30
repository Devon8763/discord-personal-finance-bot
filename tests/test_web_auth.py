import ast
import builtins
import csv
import io
import os
import re
import tempfile
import unittest
from html import unescape
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from authlib.integrations.base_client import OAuthError
from fastapi.testclient import TestClient

import db
import life_ledger_service as life_service
import spending as sp
from web import settings as web_settings
from web import routes
from web.app import create_app
from web.auth import current_user_id, start_session
from web.settings import WebSettings, load_web_settings


class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.valid_env = {
            "WEB_SESSION_SECRET": "s" * 32,
            "DISCORD_CLIENT_ID": "123456789012345678",
            "DISCORD_CLIENT_SECRET": "client-secret-for-tests",
            "DISCORD_REDIRECT_URI": (
                "http://127.0.0.1:8000/auth/discord/callback"
            ),
        }

    def test_settings_reject_missing_short_and_example_session_secrets(self):
        for secret in ("", "too-short", "change-me", "your-secret-here"):
            with self.subTest(secret=secret):
                env = self.valid_env | {"WEB_SESSION_SECRET": secret}
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ValueError):
                        load_web_settings()

    def test_settings_reject_missing_or_non_decimal_oauth_fields(self):
        cases = (
            ("DISCORD_CLIENT_ID", ""),
            ("DISCORD_CLIENT_ID", "not-a-number"),
            ("DISCORD_CLIENT_SECRET", ""),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                env = self.valid_env | {field: value}
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ValueError):
                        load_web_settings()

    def test_settings_reject_non_local_or_malformed_redirects(self):
        invalid_uris = (
            "https://127.0.0.1:8000/auth/discord/callback",
            "http://0.0.0.0:8000/auth/discord/callback",
            "http://example.com/auth/discord/callback",
            "http://user@localhost:8000/auth/discord/callback",
            "http://localhost:8000/wrong",
            "http://localhost:8000/auth/discord/callback?next=elsewhere",
            "http://localhost:8000/auth/discord/callback#fragment",
        )
        for uri in invalid_uris:
            with self.subTest(uri=uri):
                env = self.valid_env | {"DISCORD_REDIRECT_URI": uri}
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ValueError):
                        load_web_settings()

    def test_settings_accept_both_local_hosts(self):
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                uri = f"http://{host}:8000/auth/discord/callback"
                env = self.valid_env | {"DISCORD_REDIRECT_URI": uri}
                with patch.dict(os.environ, env, clear=True):
                    settings = load_web_settings()
                self.assertEqual(settings.discord_redirect_uri, uri)

    def test_settings_errors_do_not_reveal_secret_values(self):
        exposed_secret = "never-show-this-client-secret"
        env = self.valid_env | {
            "DISCORD_CLIENT_SECRET": exposed_secret,
            "DISCORD_REDIRECT_URI": "https://example.com/callback",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as raised:
                load_web_settings()
        self.assertNotIn(exposed_secret, str(raised.exception))
        self.assertNotIn(self.valid_env["WEB_SESSION_SECRET"], str(raised.exception))

    def test_dotenv_reads_only_exact_project_root_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            project_root = parent / "project"
            project_root.mkdir()
            parent.joinpath(".env").write_text(
                "\n".join(f"{key}={value}" for key, value in self.valid_env.items()),
                encoding="utf-8",
            )

            with (
                patch.object(web_settings, "PROJECT_ROOT", project_root),
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaises(ValueError):
                    load_web_settings()

                project_root.joinpath(".env").write_text(
                    "\n".join(
                        f"{key}={value}" for key, value in self.valid_env.items()
                    ),
                    encoding="utf-8",
                )
                loaded = load_web_settings()

            self.assertEqual(
                loaded.discord_client_id,
                self.valid_env["DISCORD_CLIENT_ID"],
            )


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.settings = WebSettings(session_secret="t" * 32)
        self.client = TestClient(create_app(self.settings))

    def test_healthz_needs_no_oauth_secrets_and_leaks_nothing(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertNotIn(self.settings.session_secret, response.text)

    def test_logged_out_home_only_shows_login(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("使用 Discord 登入", response.text)
        self.assertNotIn("已登入", response.text)
        self.assertNotIn("快速記帳", response.text)
        self.assertNotIn('name="amount"', response.text)
        self.assertNotIn('name="category"', response.text)
        self.assertNotIn('name="payment_source_id"', response.text)
        self.assertNotIn("查看月曆", response.text)
        self.assertNotIn("搜尋帳目", response.text)
        self.assertNotIn("Discord ID", response.text)
        self.assertNotIn("帳務", response.text)

    def test_unrequested_openapi_pages_are_not_exposed(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_start_session_clears_oauth_state_and_keeps_only_required_values(self):
        session = {
            "_state_discord_temporary": {"data": {"state": "temporary"}},
            "access_token": "must-not-survive",
            "refresh_token": "must-not-survive",
            "email": "must-not-survive@example.invalid",
            "profile": {"username": "must-not-survive"},
        }

        start_session(session, "123456789012345678")

        self.assertEqual(
            set(session),
            {"discord_user_id", "csrf_token"},
        )
        self.assertEqual(current_user_id(session), "123456789012345678")
        self.assertTrue(session["csrf_token"])


class _WebOAuthTests:
    provider_user_id = "123456789012345678"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            db,
            "DB_NAME",
            str(Path(self.temp.name) / "web-oauth-session.db"),
        )
        self.db_patch.start()
        db.init_db()
        self.settings = WebSettings(
            session_secret="o" * 32,
            discord_client_id="111111111111111111",
            discord_client_secret="oauth-client-secret-for-tests",
            discord_redirect_uri=(
                "http://127.0.0.1:8000/auth/discord/callback"
            ),
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def _start_login(self, client=None):
        client = client or self.client
        response = client.get(
            "/login?next=https://example.invalid/&user_id=999",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertIn("state", query)
        self.assertTrue(query["state"][0])
        return query["state"][0]

    def _complete_mocked_login(self, client=None, user_id=None):
        client = client or self.client
        state = self._start_login(client)
        provider_id = self.provider_user_id if user_id is None else user_id
        with patch(
            "web.auth.fetch_discord_user_id",
            new=AsyncMock(return_value=provider_id),
        ):
            response = client.get(
                "/auth/discord/callback"
                f"?code=test-code&state={state}&user_id=999&next=https://example.invalid/",
                follow_redirects=False,
            )
        return response

    def test_login_redirect_requests_identify_without_open_redirect(self):
        response = self.client.get(
            "/login?next=https://example.invalid/&user_id=999",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        target = urlsplit(response.headers["location"])
        query = parse_qs(target.query)
        self.assertEqual(target.netloc, "discord.com")
        self.assertEqual(query["scope"], ["identify"])
        self.assertEqual(query["redirect_uri"], [self.settings.discord_redirect_uri])
        self.assertTrue(query["state"][0])
        self.assertNotIn("next", query)
        self.assertNotIn("user_id", query)

    def test_discord_oauth_client_uses_v10_api_base_url(self):
        self.assertEqual(
            self.app.state.oauth.discord.api_base_url,
            "https://discord.com/api/v10/",
        )

    def test_login_without_oauth_configuration_shows_safe_error(self):
        client = TestClient(create_app(WebSettings(session_secret="x" * 32)))

        response = client.get("/login")

        self.assertEqual(response.status_code, 503)
        self.assertIn("登入目前無法使用", response.text)
        self.assertNotIn("client_secret", response.text)

    def test_callback_state_mismatch_stops_before_token_exchange(self):
        self._start_login()
        exchange = AsyncMock(return_value={"access_token": "not-used"})
        with patch.object(self.app.state.oauth.discord, "fetch_access_token", exchange):
            response = self.client.get(
                "/auth/discord/callback?code=test-code&state=wrong-state"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("登入未完成", response.text)
        exchange.assert_not_awaited()
        self.assertIn("使用 Discord 登入", self.client.get("/").text)


class _WebLedgerTestFixture:
    user_id = "123456789012345678"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            db,
            "DB_NAME",
            str(Path(self.temp.name) / "web-quick-entry.db"),
        )
        self.spending_clock = patch("spending.today", return_value=date(2026, 9, 24))
        self.web_clock = patch(
            "web.routes.taiwan_today",
            return_value=date(2026, 9, 24),
        )
        self.db_patch.start()
        self.spending_clock.start()
        self.web_clock.start()
        db.init_db()

        self.settings = WebSettings(
            session_secret="q" * 32,
            discord_client_id="111111111111111111",
            discord_client_secret="oauth-client-secret-for-tests",
            discord_redirect_uri=(
                "http://127.0.0.1:8000/auth/discord/callback"
            ),
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.web_clock.stop()
        self.spending_clock.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def _login(self, user_id=None, client=None):
        client = client or self.client
        response = client.get("/login", follow_redirects=False)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        with patch(
            "web.auth.fetch_discord_user_id",
            new=AsyncMock(return_value=user_id or self.user_id),
        ):
            response = client.get(
                f"/auth/discord/callback?code=test-code&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)

    def _csrf_token(self):
        response = self.client.get("/")
        return re.search(
            r'name="csrf_token" value="([^"]+)"',
            response.text,
        ).group(1)

    def _valid_form(self, **changes):
        sources = life_service.get_payment_sources(self.user_id)
        values = {
            "csrf_token": self._csrf_token(),
            "amount": "120.50",
            "note": "午餐",
            "category": "餐飲",
            "payment_source_id": str(sources[0]["id"]),
            "spent_on": "2026-09-24",
        }
        values.update(changes)
        return values


class _WebExpenseFixture(_WebLedgerTestFixture):
    def _expense(self, **changes):
        values = dict(user_id=self.user_id, amount='120.50', category='餐飲', note='午餐', spent_on='2026-09-01')
        values.update(changes)
        return life_service.add_expense(**values)

    def _edit_form(self, **changes):
        values = dict(csrf_token=self._csrf_token(), expected_revision='0', spent_on='2026-09-01',
                      amount='100.25', category='交通', note='車票', payment_source_id='keep')
        values.update(changes)
        return values

    def _post_expense(self, key, form, action='edit'):
        return self.client.post(f'/expenses/{key}/{action}', content=urlencode(form, doseq=True),
                                headers={'content-type': 'application/x-www-form-urlencoded'}, follow_redirects=False)

    def _snapshot(self):
        return {row['name']: sp.rows(f"SELECT * FROM {row['name']} ORDER BY rowid")
                for row in sp.rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


class WebExpenseEditTests(_WebExpenseFixture, unittest.TestCase):
    def test_edit_get_is_owner_scoped_read_only(self):
        key = self._expense()
        before = self._snapshot()
        with patch.object(life_service, 'get_expense', side_effect=AssertionError('no unauthorized read')):
            response = self.client.get(f'/expenses/{key}/edit')
        self.assertEqual(response.status_code, 403)
        self._login()
        response = self.client.get(f'/expenses/{key}/edit?user_id=999999999999999999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('午餐', response.text)
        self.assertIn('value="120.5"', response.text)
        self.assertIn('value="keep" selected', response.text)
        self.assertEqual(self._snapshot(), before)
        foreign = self._expense(user_id='999999999999999999', note='他人秘密')
        voided = self._expense(note='撤銷秘密')
        life_service.void_expense(self.user_id, voided)
        with sp.transaction() as conn:
            other = conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,kind) VALUES(?,'2026-09-01',100,'餐飲','其他秘密','other')", (self.user_id,)).lastrowid
        before = self._snapshot()
        bodies = []
        for target in (foreign, voided, other, 99999, '0', '-1', 'abc', '١', '9223372036854775808'):
            response = self.client.get(f'/expenses/{target}/edit')
            self.assertEqual(response.status_code, 404)
            bodies.append(response.text)
            self.assertNotIn('秘密', response.text)
        self.assertEqual(len(set(bodies)), 1)
        self.assertEqual(self._snapshot(), before)

    def test_edit_post_updates_five_fields_with_prg(self):
        source = sp.add_payment_source(self.user_id, '測試卡')
        key = self._expense()
        self._login()
        form = self._edit_form(payment_source_id=str(source), spent_on='2026-08-31',
                               user_id='999999999999999999', expense_id='999999')
        before = self._snapshot()
        response = self._post_expense(key, form)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], f'/expenses/{key}/edit?return_view=search&saved=1')
        row = life_service.get_expense(self.user_id, key)
        self.assertEqual((row['cents'], row['category'], row['note'], row['spent_on'], row['payment_source_id'], row['revision']),
                         (10025, '交通', '車票', '2026-08-31', source, 1))
        self.assertEqual(len(self._snapshot()['expense_actions']), len(before['expense_actions'])+1)
        after = self._snapshot()
        refreshed = self.client.get(response.headers['location'])
        self.assertEqual(refreshed.status_code, 200)
        self.assertIn('已更改帳目', refreshed.text)
        self.assertIn('value="100.25"', refreshed.text)
        self.assertEqual(self._snapshot(), after)
        self.assertEqual(self._post_expense(key, form).status_code, 409)
        self.assertEqual(self._snapshot(), after)

    def test_edit_all_origins_keep_schedule_and_lock_auto_date(self):
        manual = self._expense()
        for kind in ('固定', '訂閱', '分期'):
            sp.add_recurring(self.user_id, kind, kind+'測試', 10, '餐飲', '2026-09', 2 if kind == '分期' else 0)
        sp.sync_recurring(self.user_id)
        entries = life_service.list_expenses(self.user_id, '2026-09')['items']
        rules = self._snapshot()['recurring_expenses']
        self._login()
        for row in entries:
            with self.subTest(source=row['source']):
                page = self.client.get(f"/expenses/{row['id']}/edit")
                self.assertEqual(page.status_code, 200)
                date_input = re.search(r'<input[^>]*name="spent_on"[^>]*>', page.text).group(0)
                self.assertEqual('readonly' in date_input, row['id'] != manual)
                form = self._edit_form(spent_on=row['spent_on'])
                with patch.object(life_service, 'sync_recurring', side_effect=AssertionError('no sync')):
                    response = self._post_expense(row['id'], form)
                self.assertEqual(response.status_code, 303)
                updated = life_service.get_expense(self.user_id, row['id'])
                self.assertEqual(tuple(updated[n] for n in ('source','recurring_id','period')), tuple(row[n] for n in ('source','recurring_id','period')))
                form = self._edit_form(expected_revision='1', spent_on='2026-08-31')
                before = self._snapshot()
                response = self._post_expense(row['id'], form)
                self.assertEqual(response.status_code, 303 if row['id'] == manual else 400)
                if row['id'] != manual:
                    self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._snapshot()['recurring_expenses'], rules)

    def test_edit_preserves_inactive_values_and_payment_snapshot(self):
        source = sp.add_payment_source(self.user_id, '歷史卡')
        key = self._expense(payment_source_id=source)
        sp.rename_payment_source(self.user_id, source, '目前卡')
        self._login()
        self.assertEqual(self._post_expense(key, self._edit_form(category='餐飲')).status_code, 303)
        self.assertEqual(life_service.get_expense(self.user_id, key)['payment_source_name'], '歷史卡')
        self.assertEqual(self._post_expense(key, self._edit_form(expected_revision='1', category='餐飲', payment_source_id=str(source))).status_code, 303)
        self.assertEqual(life_service.get_expense(self.user_id, key)['payment_source_name'], '目前卡')
        sp.set_category(self.user_id, '餐飲', False)
        sp.set_category(self.user_id, '另一停用', True)
        sp.set_category(self.user_id, '另一停用', False)
        sp.disable_payment_source(self.user_id, source)
        page = self.client.get(f'/expenses/{key}/edit')
        self.assertIn('已停用，僅可保留', page.text)
        self.assertNotIn('另一停用', page.text)
        self.assertEqual(self._post_expense(key, self._edit_form(expected_revision='2', category='餐飲')).status_code, 303)
        foreign_source = sp.add_payment_source('999999999999999999', '他人卡')
        for changes in ({'payment_source_id': str(source)}, {'payment_source_id': str(foreign_source)},
                        {'payment_source_id': ''}, {'category': '另一停用'}):
            form = self._edit_form(expected_revision='3', category='餐飲', **{k:v for k,v in changes.items() if k != 'category'})
            form.update(changes)
            before = self._snapshot()
            self.assertEqual(self._post_expense(key, form).status_code, 400)
            self.assertEqual(self._snapshot(), before)
        with sp.transaction() as conn:
            conn.execute('UPDATE expenses SET payment_source_id=NULL WHERE id=?', (key,))
        self.assertEqual(self._post_expense(key, self._edit_form(expected_revision='3', category='餐飲')).status_code, 303)
        self.assertIsNone(life_service.get_expense(self.user_id, key)['payment_source_id'])

    def test_edit_post_enforces_csrf_unique_fields_and_session_identity(self):
        key = self._expense()
        self._login()
        valid = self._edit_form()
        cases = []
        for token in (None, 'wrong', '中', [valid['csrf_token'], valid['csrf_token']]):
            form = dict(valid)
            if token is None:
                form.pop('csrf_token')
            else:
                form['csrf_token'] = token
            cases.append((form, 403))
        for field in ('amount','note','category','payment_source_id','spent_on','expected_revision'):
            for duplicate in (False, True):
                form = dict(valid)
                if duplicate:
                    form[field] = [valid[field], valid[field]]
                else:
                    form.pop(field)
                cases.append((form, 400))
        for revision in ('', '-1', '1.0', '١', '9223372036854775808'):
            cases.append(({**valid, 'expected_revision': revision}, 400))
        before = self._snapshot()
        for form, status in cases:
            response = self._post_expense(key, form)
            self.assertEqual(response.status_code, status)
            self.assertEqual(self._snapshot(), before)
        response = self.client.post(f'/expenses/{key}/edit', json=valid)
        self.assertEqual(response.status_code, 403)
        response = self._post_expense(key, {**valid, **{f'extra{n}':'x' for n in range(21)}})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_edit_invalid_values_preserve_only_safe_draft(self):
        key = self._expense()
        self._login()
        for changes in ([{'amount': n} for n in ('0','-1','NaN','Infinity','1.001','1000000000.01','1,000')] +
                        [{'note':''}, {'note':'x'*201}, {'spent_on':'2026-02-29'}, {'spent_on':'2026-09-25'},
                         {'spent_on':'20260901'}, {'category':'<script>bad</script>'}]):
            form = self._edit_form(note='<script>alert(1)</script>')
            form.update(changes)
            before = self._snapshot()
            response = self._post_expense(key, form)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('<script>', response.text)
            self.assertNotIn('x'*201, response.text)
            if 'note' not in changes:
                self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', response.text)
            self.assertIn('name="expected_revision" value="0"', response.text)
            self.assertEqual(self._snapshot(), before)

    def test_edit_conflict_never_upgrades_draft_revision(self):
        key = self._expense()
        self._login()
        form = self._edit_form(note='<script>舊草稿</script>')
        with TestClient(self.app) as second:
            self._login(client=second)
            page = second.get(f'/expenses/{key}/edit')
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            newer = {**form, 'csrf_token':token, 'amount':'99', 'category':'餐飲', 'note':'較新'}
            self.assertEqual(second.post(f'/expenses/{key}/edit', content=urlencode(newer),
                                         headers={'content-type':'application/x-www-form-urlencoded'},
                                         follow_redirects=False).status_code, 303)
        before = self._snapshot()
        response = self._post_expense(key, form)
        self.assertEqual(response.status_code, 409)
        self.assertIn('&lt;script&gt;舊草稿&lt;/script&gt;', response.text)
        self.assertNotIn('method="post"', response.text)
        self.assertIn('重新載入帳目', response.text)
        self.assertEqual(self._snapshot(), before)
        form = self._edit_form(expected_revision='1', amount='0', note='回填競態')
        real_get = life_service.get_expense
        calls = 0
        def racing_get(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                life_service.update_expense(self.user_id, key, 88, '餐飲', '競態新值', '2026-09-01')
            return real_get(*args)
        with patch.object(life_service, 'get_expense', side_effect=racing_get):
            response = self._post_expense(key, form)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('method="post"', response.text)
        self.assertEqual(real_get(self.user_id, key)['cents'], 8800)


class WebExpenseDeleteTests(_WebExpenseFixture, unittest.TestCase):
    def test_delete_confirmation_rechecks_owner_status_and_revision(self):
        key = self._expense()
        self._login()
        before = self._snapshot()
        page = self.client.get(f'/expenses/{key}/delete?expected_revision=0&return_view=calendar&month=2026-09&day=2026-09-01')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers['cache-control'], 'no-store')
        for text in ('午餐','120.5','確認刪除','取消，返回更改頁','帳目資料仍保留為軟刪除紀錄'):
            self.assertIn(text, page.text)
        self.assertIn('name="expected_revision" value="0"', page.text)
        self.assertEqual(self._snapshot(), before)
        for query in ('', '?expected_revision=-1', '?expected_revision=0&expected_revision=0'):
            self.assertEqual(self.client.get(f'/expenses/{key}/delete'+query).status_code, 400)
        life_service.update_expense(self.user_id, key, 25, '餐飲', '新值', '2026-09-01')
        page = self.client.get(f'/expenses/{key}/delete?expected_revision=0')
        self.assertEqual(page.status_code, 409)
        self.assertNotIn('method="post"', page.text)
        self.assertIn('無法完成操作', page.text)
        self.assertNotIn('登入未完成', page.text)
        self.assertIn(f'href="/expenses/{key}/edit?return_view=search"', page.text)
        foreign = self._expense(user_id='999999999999999999')
        voided = self._expense()
        life_service.void_expense(self.user_id, voided)
        bodies = [self.client.get(f'/expenses/{n}/delete?expected_revision=0') for n in (foreign, voided, 99999)]
        self.assertTrue(all(r.status_code == 404 for r in bodies))
        self.assertEqual(len({r.text for r in bodies}), 1)

    def test_delete_post_is_soft_atomic_and_uses_prg(self):
        key = self._expense()
        self._login()
        form = dict(csrf_token=self._csrf_token(), expected_revision='0', return_view='search', keyword='午餐')
        before = self._snapshot()
        statements = []
        def traced_connection():
            conn = db.get_conn()
            conn.set_trace_callback(statements.append)
            return conn
        with patch('ledger.get_conn', side_effect=traced_connection), patch.object(sp, 'get_conn', side_effect=traced_connection):
            response = self._post_expense(key, form, 'delete')
        self.assertEqual(response.status_code, 303)
        target = urlsplit(response.headers['location'])
        self.assertEqual(target.path, '/search')
        self.assertEqual(parse_qs(target.query), {'keyword':['午餐'], 'deleted':['1']})
        row = sp.rows('SELECT * FROM expenses WHERE id=?', (key,))[0]
        self.assertEqual((row['voided'], row['revision']), (1, 1))
        self.assertEqual(len(self._snapshot()['expense_actions']), len(before['expense_actions'])+1)
        self.assertFalse(any(re.search(r'\bDELETE\s+FROM\s+expenses\b', sql, re.I) for sql in statements))
        after = self._snapshot()
        self.assertEqual(self._post_expense(key, form, 'delete').status_code, 404)
        self.assertEqual(self._snapshot(), after)
        for change in ('edit','undo','void','rename'):
            key = self._expense(category='交通')
            form = dict(csrf_token=form['csrf_token'], expected_revision='0')
            self.assertEqual(self.client.get(f'/expenses/{key}/delete?expected_revision=0').status_code, 200)
            if change in ('edit','undo'):
                life_service.update_expense(self.user_id, key, 25, '交通', '新值', '2026-09-01')
                if change == 'undo':
                    life_service.undo_latest_action(self.user_id)
            elif change == 'void':
                life_service.void_expense(self.user_id, key)
            else:
                sp.rename_category(self.user_id, '交通', '交通改名')
            before = self._snapshot()
            self.assertEqual(self._post_expense(key, form, 'delete').status_code, 404 if change == 'void' else 409)
            self.assertEqual(self._snapshot(), before)

    def test_delete_post_rejects_csrf_and_identity_tampering(self):
        key = self._expense()
        foreign = self._expense(user_id='999999999999999999')
        self.assertEqual(self._post_expense(key, {}, 'delete').status_code, 403)
        self._login()
        valid = dict(csrf_token=self._csrf_token(), expected_revision='0')
        for field in ('csrf_token','expected_revision'):
            for value in (None, '', ['0','0']):
                form = dict(valid)
                if value is None:
                    form.pop(field)
                else:
                    form[field] = value
                before = self._snapshot()
                self.assertEqual(self._post_expense(key, form, 'delete').status_code, 403 if field == 'csrf_token' else 400)
                self.assertEqual(self._snapshot(), before)
        before = self._snapshot()
        self.assertEqual(self._post_expense(key, {**valid, 'csrf_token':'中'}, 'delete').status_code, 403)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.client.post(f'/expenses/{key}/delete', json=valid).status_code, 403)
        for value in ('-1','1.2','١','9223372036854775808'):
            self.assertEqual(self._post_expense(key, {**valid,'expected_revision':value}, 'delete').status_code, 400)
        before = self._snapshot()
        self.assertEqual(self._post_expense(foreign, {**valid,'user_id':'999999999999999999'}, 'delete').status_code, 404)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._post_expense(key, {**valid,'user_id':'999999999999999999','expense_id':str(foreign)}, 'delete').status_code, 303)
        self.assertEqual(life_service.get_expense('999999999999999999', foreign)['voided'], 0)

    def test_web_write_failures_roll_back_and_hide_storage_details(self):
        key = self._expense(amount='10')
        sp.set_budget(self.user_id, '2026-09', '總額', 100)
        self._login()
        form = self._edit_form(amount='120')
        for action, table, event in (('edit','expense_actions','INSERT'), ('delete','expense_actions','INSERT'),
                                     ('edit','expenses','UPDATE'), ('delete','expenses','UPDATE'),
                                     ('edit','spending_notices','INSERT')):
            with self.subTest(action=action, table=table):
                before = self._snapshot()
                with sp.transaction() as conn:
                    conn.execute(f"CREATE TRIGGER fail_web BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'private detail'); END")
                try:
                    response = self._post_expense(key, form, action)
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn('private detail', response.text)
                    self.assertNotIn('CREATE TRIGGER', response.text)
                    self.assertNotIn('location', response.headers)
                    self.assertEqual(self._snapshot(), before)
                finally:
                    with sp.transaction() as conn:
                        conn.execute('DROP TRIGGER fail_web')

    def test_existing_undo_restores_web_edit_and_void(self):
        key = self._expense()
        original = life_service.get_expense(self.user_id, key)
        self._login()
        form = self._edit_form()
        self.assertEqual(self._post_expense(key, form).status_code, 303)
        preview = life_service.preview_undo(self.user_id)
        life_service.undo_latest_action(self.user_id, preview['action_id'])
        restored = life_service.get_expense(self.user_id, key)
        for field in ('spent_on','cents','note','category','voided','payment_source_id','payment_source_name'):
            self.assertEqual(restored[field], original[field])
        self.assertEqual(restored['revision'], 2)
        self.assertEqual(self._post_expense(key, {**form,'expected_revision':'2'}, 'delete').status_code, 303)
        preview = life_service.preview_undo(self.user_id)
        life_service.undo_latest_action(self.user_id, preview['action_id'])
        self.assertEqual(life_service.get_expense(self.user_id, key)['revision'], 4)
        self.assertEqual(self._post_expense(key, {**form,'expected_revision':'4'}, 'delete').status_code, 303)
        stale_action = life_service.preview_undo(self.user_id)['action_id']
        self._expense(note='另一操作')
        with self.assertRaises(ValueError):
            life_service.undo_latest_action(self.user_id, stale_action)
        self.assertEqual(sp.rows('SELECT voided FROM expenses WHERE id=?', (key,))[0]['voided'], 1)


class WebExpenseNavigationTests(_WebExpenseFixture, unittest.TestCase):
    def test_search_and_day_details_share_edit_links(self):
        manual = self._expense(note='入口&<測試>')
        for kind in ('固定', '訂閱', '分期'):
            sp.add_recurring(self.user_id, kind, kind, 10, '餐飲', '2026-09', 2 if kind == '分期' else 0)
        sp.sync_recurring(self.user_id)
        voided = self._expense()
        life_service.void_expense(self.user_id, voided)
        other = self._expense()
        with sp.transaction() as conn:
            conn.execute("UPDATE expenses SET kind='other' WHERE id=?", (other,))
        self._expense(user_id='999999999999999999', note='他人秘密')
        self._login()
        for path, expected in (('/search?keyword=' + urlencode({'k':'入口&<測試>'})[2:], [manual]),
                               ('/calendar?month=2026-09&day=2026-09-01',
                                [row['id'] for row in life_service.list_expenses(self.user_id, '2026-09')['items']])):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            links = [unescape(link) for link in re.findall(r'href="([^"]+/edit[^\"]*)"', page.text)]
            self.assertEqual({int(urlsplit(link).path.split('/')[2]) for link in links}, set(expected))
            for link in links:
                query = parse_qs(urlsplit(link).query)
                self.assertEqual(query['return_view'], ['search' if path.startswith('/search') else 'calendar'])
                if path.startswith('/search'):
                    self.assertEqual(query['keyword'], ['入口&<測試>'])
                else:
                    self.assertEqual(query['day'], ['2026-09-01'])
                self.assertNotIn('user_id', link)
                self.assertNotIn('revision', link)
                self.assertEqual(self.client.get(link).status_code, 200)
            self.assertNotIn('name="expense_id"', page.text)
            self.assertNotIn('他人秘密', page.text)

    def test_return_context_is_validated_and_never_redirects_externally(self):
        key = self._expense()
        self._login()
        csrf = self._csrf_token()
        contexts = [
            ({'return_view':'search','keyword':'麵 & <餐>','start':'2026-08-01','end':'2026-09-24'},
             '/search?' + urlencode({'keyword':'麵 & <餐>','start':'2026-08-01','end':'2026-09-24'})),
            ({'return_view':'calendar','month':'2026-09','day':'2026-09-01'}, '/calendar?month=2026-09&day=2026-09-01'),
            ({'return_view':'search','end':'2026-09-01'}, '/search'),
            ({'return_view':['search','calendar'],'keyword':'午餐'}, '/search'),
            ({'return_view':'search','start':'2026-09-24','end':'2026-09-01'}, '/search'),
            ({'return_view':'search','start':'2026-10-01'}, '/search'),
            ({'return_view':'calendar','month':'2026-10'}, '/search'),
            ({'return_view':'calendar','month':'2026-09','day':'2026-08-01'}, '/search'),
            ({'return_view':'calendar','month':['2026-09','2026-08']}, '/search'),
            ({'return_view':'calendar','month':'invalid'}, '/search'),
            ({'return_view':'https://example.invalid'}, '/search'),
        ]
        for index, (context, expected) in enumerate(contexts):
            with self.subTest(context=context):
                context = {**context, 'next':'//example.invalid', 'user_id':'999999999999999999'}
                page = self.client.get(f'/expenses/{key}/edit?' + urlencode(context, doseq=True))
                back = unescape(re.search(r'href="([^"]+)">返回列表', page.text).group(1))
                self.assertEqual(back, expected)
                form = dict(csrf_token=csrf, expected_revision=str(index), spent_on='2026-08-31',
                            amount='100', category='餐飲', note='返回測試', payment_source_id='keep', **context)
                response = self._post_expense(key, form)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(urlsplit(response.headers['location']).path, f'/expenses/{key}/edit')
                self.assertNotIn('example.invalid', response.headers['location'])
                self.assertEqual(unescape(re.search(r'href="([^"]+)">返回列表',
                                                   self.client.get(response.headers['location']).text).group(1)), expected)
        for next_url in ('https://example.invalid', '//example.invalid', '%68%74%74%70%73://example.invalid'):
            target = self._expense()
            response = self._post_expense(target, dict(csrf_token=csrf, expected_revision='0', next=next_url), 'delete')
            self.assertEqual(response.headers['location'], '/search?deleted=1')
            self.assertIn('已刪除帳目', self.client.get(response.headers['location']).text)

    def test_soft_delete_updates_search_calendar_and_budget_totals(self):
        first = self._expense(amount='120.50', note='消失帳目')
        second = self._expense(amount='0.25', note='留存帳目')
        foreign = self._expense(user_id='999999999999999999', amount='9.99')
        sp.set_budget(self.user_id, '2026-09', '餐飲', 1000)
        sp.set_budget(self.user_id, '2026-09', '總額', 1000)
        self._login()
        form = self._edit_form(return_view='calendar', month='2026-09', day='2026-09-01')
        foreign_before = life_service.get_month_summary('999999999999999999', '2026-09')
        for target, expected in ((first, '0.25'), (second, '0')):
            response = self._post_expense(target, form, 'delete')
            self.assertEqual(response.status_code, 303)
            page = self.client.get(response.headers['location'])
            self.assertIn('已刪除帳目', page.text)
            self.assertIn(f'當日消費合計：NT${expected}', page.text)
            self.assertNotIn('消失帳目', self.client.get('/search?keyword=帳目').text)
            for view in (page.text, self.client.get('/').text):
                self.assertEqual(view.count('class="calendar-marker"'), 1 if target == first else 0)
            summary = life_service.get_month_summary(self.user_id, '2026-09')
            self.assertEqual(str(summary['total']), expected if target == first else '0.0')
            self.assertTrue(all(row['spent'] == summary['total'] for row in summary['budgets']))
            day = next(row for row in life_service.get_calendar_days(self.user_id, '2026-09') if row['date'] == '2026-09-01')
            self.assertEqual(day['cents'], 25 if target == first else 0)
        self.assertIn('此日期沒有已記錄的消費', page.text)
        self.assertEqual(life_service.get_month_summary('999999999999999999', '2026-09'), foreign_before)
        self.assertEqual(life_service.get_expense('999999999999999999', foreign)['voided'], 0)

    def test_auto_edit_void_sync_preserves_periods_and_future_schedule(self):
        manual = self._expense()
        for kind in ('固定', '訂閱', '分期'):
            sp.add_recurring(self.user_id, kind, kind, 10, '餐飲', '2026-09', 2 if kind == '分期' else 0)
        self.assertEqual(sp.sync_recurring(self.user_id), 3)
        entries = life_service.list_expenses(self.user_id, '2026-09')['items']
        rules = sp.rows('SELECT * FROM recurring_expenses ORDER BY id')
        self._login()
        form = self._edit_form()
        for row in entries:
            self.assertEqual(self._post_expense(row['id'], form).status_code, 303)
            self.assertEqual(self._post_expense(row['id'], {**form,'expected_revision':'1'}, 'delete').status_code, 303)
            stored = sp.rows('SELECT * FROM expenses WHERE id=?', (row['id'],))[0]
            self.assertEqual((stored['voided'],stored['source'],stored['recurring_id'],stored['period']),
                             (1,row['source'],row['recurring_id'],row['period']))
        undo = life_service.preview_undo(self.user_id)
        self.assertEqual(sp.sync_recurring(self.user_id, date(2026,9,24)), 0)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')), 4)
        with patch('spending.today', return_value=date(2026,10,1)):
            self.assertEqual(sp.sync_recurring(self.user_id, date(2026,10,1)), 3)
            self.assertEqual(len(life_service.list_expenses(self.user_id, '2026-10')['items']), 3)
            self.assertNotIn(manual, [row['id'] for row in life_service.list_expenses(self.user_id, '2026-10')['items']])
        self.assertEqual(sp.rows('SELECT * FROM recurring_expenses ORDER BY id'), rules)
        with self.assertRaises(ValueError):
            life_service.undo_latest_action(self.user_id, undo['action_id'])


class WebHomeCardsTests(_WebLedgerTestFixture, unittest.TestCase):
    def test_budget_and_calendar_follow_entry_mutations(self):
        foreign = '999999999999999999'
        life_service.add_expense(foreign, 321, '餐飲', '他人', '2026-09-01')
        foreign_before = life_service.get_month_summary(foreign, '2026-09')
        for month in ('2026-08', '2026-09'):
            for category, amount in (('總額', 100), ('餐飲', 50), ('交通', 30)):
                sp.set_budget(self.user_id, month, category, amount)
        self._login()
        response = self.client.post('/expenses', data=self._valid_form(amount='.29', spent_on='2026-09-01'), follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        key = life_service.list_expenses(self.user_id, '2026-09')['items'][0]['id']
        csrf = self._csrf_token()

        def check(august, september, category):
            home = self.client.get('/')
            self.assertEqual(home.status_code, 200)
            for month, expected in (('2026-08', august), ('2026-09', september)):
                summary = life_service.get_month_summary(self.user_id, month)
                calendar = life_service.get_calendar_days(self.user_id, month)
                self.assertEqual(summary['total_cents'], expected)
                self.assertEqual(summary['total_cents'], sum(day['cents'] for day in calendar))
                for row in summary['budgets']:
                    if row['category'] != '總額':
                        self.assertEqual(row['spent_cents'], expected if row['category'] == category else 0)
            self.assertEqual(home.context['home_budget']['spent'], routes._format_twd(september))
            self.assertEqual(home.context['home_calendar']['has_month_expenses'], bool(september))
            self.assertEqual(life_service.get_month_summary(foreign, '2026-09'), foreign_before)

        check(0, 29, '餐飲')
        for revision, on, august, september in ((0, '2026-09-02', 0, 2550), (1, '2026-08-31', 2550, 0), (2, '2026-09-01', 0, 2550)):
            form = dict(csrf_token=csrf, expected_revision=str(revision), amount='25.50', category='交通',
                        note='更改', payment_source_id='keep', spent_on=on, user_id=foreign)
            response = self.client.post(f'/expenses/{key}/edit', data=form, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            check(august, september, '交通')
        confirmation = self.client.get(f'/expenses/{key}/delete?expected_revision=3')
        self.assertEqual(confirmation.status_code, 200)
        response = self.client.post(f'/expenses/{key}/delete', data=dict(csrf_token=csrf, expected_revision='3'), follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        check(0, 0, '交通')
        self.assertEqual(sp.rows('SELECT voided FROM expenses WHERE id=?', (key,)), [{'voided': 1}])
        self.assertEqual(len(sp.rows('SELECT * FROM expense_actions WHERE expense_id=?', (key,))), 5)

    def test_budget_sources_and_unlimited_history(self):
        for _ in range(31):
            life_service.add_expense(self.user_id, '.29', '餐飲', '超過清單筆數', '2026-09-01')
        life_service.add_expense(self.user_id, '1.01', '交通', '無分類預算', '2026-09-02')
        for kind, amount, periods in (('固定', 10, 0), ('訂閱', 20, 0), ('分期', 30, 2)):
            sp.add_recurring(self.user_id, kind, kind, amount, '居住', '2026-09', periods)
        sp.add_recurring(self.user_id, '固定', '未入帳', 999, '居住', '2026-10')
        sp.sync_recurring(self.user_id, date(2026, 9, 24))
        life_service.add_expense('999999999999999999', 999, '餐飲', '他人', '2026-09-03')
        removed = life_service.add_expense(self.user_id, 999, '餐飲', '撤銷', '2026-09-03')
        life_service.void_expense(self.user_id, removed)
        for kind in ('income', 'investment', 'transfer'):
            key = life_service.add_expense(self.user_id, 999, '餐飲', '排除', '2026-09-03')
            with sp.transaction() as conn:
                conn.execute('UPDATE expenses SET kind=? WHERE id=?', (kind, key))
        for category, amount in (('總額', 200), ('餐飲', 20), ('居住', 100)):
            sp.set_budget(self.user_id, '2026-09', category, amount)
        sp.set_category(self.user_id, '居住', False)
        self._login()
        with patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')):
            page = self.client.get('/?user_id=999999999999999999')
        summary = life_service.get_month_summary(self.user_id, '2026-09')
        self.assertEqual((summary['total_cents'], summary['record_count']), (7000, 35))
        self.assertEqual(summary['total_cents'], sum(row['cents'] for row in life_service.get_calendar_days(self.user_id, '2026-09')))
        card = page.context['home_budget']
        self.assertEqual((card['spent'], card['used_percent'], card['remaining']), ('70', '35', '130'))
        self.assertEqual({row['name']: row['spent'] for row in card['category_budgets']}, {'餐飲': '8.99', '居住': '60'})
        self.assertIn('分類預算（2 項）', page.text)
        self.assertIn('已停用', page.text)
        self.assertEqual(page.text.count('class="calendar-marker"'), 2)

    def test_home_budget_progress_accessible_and_empty_states(self):
        self._login()
        page = self.client.get('/').text
        budget_section = page.split('id="home-budget"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('<progress', budget_section)
        self.assertNotIn('%', budget_section)
        self.assertNotIn('<details', budget_section)
        self.assertIn('尚未設定分類預算', budget_section)
        self.assertIn('href="/settings"', budget_section)
        sp.set_budget(self.user_id, '2026-09', '總額', 100)
        sp.set_budget(self.user_id, '2026-09', '餐飲', 100)
        life_service.add_expense(self.user_id, 125, '餐飲', '超支', '2026-09-01')
        page = self.client.get('/').text
        progress = re.findall(r'<progress\b([^>]*)>(.*?)</progress>', page, re.S)
        self.assertEqual(len(progress), 2)
        referenced = []
        for attributes, fallback in progress:
            self.assertIn('max="100"', attributes)
            self.assertIn('value="100"', attributes)
            self.assertIn('125%', fallback)
            label = re.search(r'aria-labelledby="([^"]+)"', attributes).group(1)
            description = re.search(r'aria-describedby="([^"]+)"', attributes).group(1)
            referenced.extend((label, description))
            self.assertRegex(page, rf'id="{label}"')
            text = re.search(rf'<p id="{description}">(.*?)</p>', page, re.S).group(1)
            self.assertIn('125%', text)
            self.assertIn('超支 25 元', text)
        self.assertEqual(len(referenced), len(set(referenced)))
        sp.set_budget(self.user_id, '2026-09', '總額', 200)
        sp.set_budget(self.user_id, '2026-09', '交通', 10)
        page = self.client.get('/').text
        self.assertRegex(page, r'<progress[^>]*value="0"')
        self.assertIn('已花 0 元', page)
        self.assertIn('已使用 0%', page)

    def test_home_category_budgets_render_all_configured_and_escape(self):
        names = ['<script>x</script>', 'A很長的分類名稱需要換行測試', '寵物', '旅遊', '教育', '醫療', '衣服', '禮物']
        for name in names:
            sp.set_category(self.user_id, name, True)
            sp.set_budget(self.user_id, '2026-09', name, 100)
        sp.set_category(self.user_id, '衣服', False)
        self._login()
        page = self.client.get('/').text
        opening = re.search(r'<details\b([^>]*)>', page)
        self.assertIsNotNone(opening)
        self.assertNotRegex(opening.group(1), r'\bopen\b')
        self.assertIn('<summary>分類預算（8 項）</summary>', page)
        category_list = page.split('<details', 1)[1].split('</details>', 1)[0]
        self.assertEqual(category_list.count('data-budget-category='), 8)
        self.assertEqual(category_list.count('<progress'), 8)
        self.assertNotIn('data-budget-category="餐飲"', category_list)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;', category_list)
        self.assertIn('已停用', category_list)
        for name in names:
            self.assertIn(name, unescape(category_list))
        rows = self.client.get('/').context['home_budget']['category_budgets']
        self.assertEqual([row['name'] for row in rows], sorted(names))

    def test_budget_markup_preserves_home_workflow(self):
        sp.set_budget(self.user_id, '2026-09', '餐飲', 1000)
        life_service.add_expense(self.user_id, 1000, '餐飲', '已用完', '2026-09-01')
        self._login()
        page = self.client.get('/').text
        self.assertIn('尚未設定總預算', page)
        self.assertIn('分類預算（1 項）', page)
        self.assertIn('預算 1,000 元', page)
        self.assertIn('已花 1,000 元', page)
        self.assertIn('剩餘 0 元（已用完）', page)
        positions = [page.index(value) for value in ('href="/search"', 'href="/settings"', 'class="entry-form"',
                                                    'id="home-budget"', 'id="home-calendar"', 'class="logout-form"')]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('查看完整月曆', page)
        sp.set_budget(self.user_id, '2026-09', '總額', 1000)
        page = self.client.get('/').text
        details = page.split('<details', 1)[1].split('</details>', 1)[0]
        self.assertIn('id="home-total-budget-label"', page.split('<details', 1)[0])
        self.assertNotIn('home-total-budget-label', details)

    def test_home_budget_visualization_context_states(self):
        for total, category in ((False, False), (True, False), (False, True), (True, True)):
            for spent in (0, 25, 100, 125):
                with self.subTest(total=total, category=category, spent=spent):
                    with sp.transaction() as conn:
                        conn.execute('DELETE FROM budgets')
                        conn.execute('DELETE FROM expenses')
                    for enabled, name in ((total, '總額'), (category, '餐飲')):
                        if enabled:
                            sp.set_budget(self.user_id, '2026-09', name, 100)
                    if spent:
                        life_service.add_expense(self.user_id, spent, '餐飲', '測試', '2026-09-01')
                    card = routes._budget_display_context(life_service.get_month_summary(self.user_id, '2026-09'), ['餐飲'])
                    self.assertEqual(card['spent'], str(spent))
                    self.assertEqual(card['total_budget'], '100' if total else None)
                    self.assertEqual(len(card['category_budgets']), int(category))
                    if not total:
                        for key in ('used_percent', 'progress_value', 'status_text', 'remaining', 'overspent'):
                            self.assertIsNone(card[key])
                    for row in ([card] if total else []) + card['category_budgets']:
                        self.assertEqual(row['used_percent'], str(spent))
                        self.assertEqual(row['progress_value'], str(min(spent, 100)))
                        self.assertEqual(row['remaining'], str(100-spent) if spent <= 100 else None)
                        self.assertEqual(row['overspent'], str(spent-100) if spent > 100 else None)
                        expected = '剩餘 0 元（已用完）' if spent == 100 else f'超支 {spent-100} 元' if spent > 100 else f'剩餘 {100-spent} 元'
                        self.assertEqual(row['status_text'], expected)
                    if category:
                        self.assertEqual(card['category_budgets'][0]['name'], '餐飲')
                        self.assertTrue(card['category_budgets'][0]['active'])
        life_service.add_expense(self.user_id, 7, '其他', '無分類預算', '2026-09-01')
        card = routes._budget_display_context(life_service.get_month_summary(self.user_id, '2026-09'), ['餐飲', '其他'])
        self.assertEqual(card['spent'], '132')
        self.assertEqual(card['category_budgets'][0]['spent'], '125')
        self.assertEqual(len(card['category_budgets']), 1)

    def test_budget_percentage_and_amount_precision(self):
        for budget, spent, percent, progress, status in (
            (1, '.29', '29', '29', '剩餘 0.71 元'),
            (100, '125', '125', '100', '超支 25 元'),
            (10000, '10000.01', '100', '100', '超支 0.01 元'),
            (1000, '100.50', '10.05', '10.05', '剩餘 899.5 元'),
            (1000, '10.05', '1.01', '1.005', '剩餘 989.95 元'),
            (1, '0', '0', '0', '剩餘 1 元'),
        ):
            with self.subTest(budget=budget, spent=spent):
                with sp.transaction() as conn:
                    conn.execute('DELETE FROM expenses')
                sp.set_budget(self.user_id, '2026-09', '總額', budget)
                if spent != '0':
                    life_service.add_expense(self.user_id, spent, '餐飲', '精度', '2026-09-01')
                summary = life_service.get_month_summary(self.user_id, '2026-09')
                # Display values must come from cents, even if legacy float fields disagree.
                summary['total'] = -999
                summary['budgets'][0].update(budget=-999, spent=-999, used_percent=-999)
                card = routes._budget_display_context(summary, ['餐飲'])
                self.assertEqual(card['used_percent'], percent)
                self.assertEqual(card['progress_value'], progress)
                self.assertEqual(card['status_text'], status)
                if spent == '100.50':
                    self.assertEqual(card['spent'], '100.5')

    def test_home_uses_core_today_once(self):
        with (
            patch.object(life_service, 'get_today', wraps=life_service.get_today) as today,
            patch.object(life_service, 'get_month_summary', wraps=life_service.get_month_summary) as summary,
            patch.object(life_service, 'get_calendar_days', wraps=life_service.get_calendar_days) as calendar,
            patch('spending.today', return_value=date(2026, 10, 1)),
            patch('web.routes.taiwan_today', return_value=date(2026, 9, 30)),
        ):
            self.assertEqual(self.client.get('/').status_code, 200)
            today.assert_not_called()
            summary.assert_not_called()
            calendar.assert_not_called()
            self._login()
            page = self.client.get('/?month=2026-09&user_id=999999999999999999').text
            self.assertIn('value="2026-10-01"', page)
            self.assertEqual(page.count('2026 年 10 月'), 3)
            today.assert_called_once_with()
            summary.assert_called_once_with(self.user_id, '2026-10')
            calendar.assert_called_once_with(self.user_id, '2026-10')

    def test_home_disabled_budget_kept_without_extra_category_query(self):
        sp.set_budget(self.user_id, '2026-09', '餐飲', 100)
        sp.set_category(self.user_id, '餐飲', False)
        self._login()
        with (
            patch.object(life_service, 'get_categories', wraps=life_service.get_categories) as categories,
            patch.object(life_service, 'get_month_summary', wraps=life_service.get_month_summary) as summary,
            patch.object(life_service, 'get_calendar_days', wraps=life_service.get_calendar_days) as calendar,
            patch.object(life_service, 'list_expenses', side_effect=AssertionError('no details')),
            patch.object(life_service, 'search_expenses', side_effect=AssertionError('no search')),
            patch.object(life_service, 'get_chart_data', side_effect=AssertionError('no chart')),
            patch.object(life_service, 'sync_recurring', side_effect=AssertionError('no sync')),
        ):
            response = self.client.get('/')
            categories.assert_called_once_with(self.user_id)
            summary.assert_called_once_with(self.user_id, '2026-09')
            calendar.assert_called_once_with(self.user_id, '2026-09')
        row = response.context['home_budget']['category_budgets'][0]
        self.assertEqual((row['name'], row['active'], row['spent'], row['progress_value']), ('餐飲', False, '0', '0'))

    def test_money_display_strips_only_trailing_zeros_across_web_pages(self):
        sp.set_budget(self.user_id, '2026-09', '總額', 3000)
        sp.set_budget(self.user_id, '2026-09', '餐飲', 1000)
        for amount, note in ((1000, '整數'), ('100.50', '尾零'), ('100.25', '小數')):
            life_service.add_expense(self.user_id, amount, '餐飲', note, '2026-09-01')
        self._login()

        home = self.client.get('/').text
        self.assertIn('已記錄支出：1,200.75 元', home)
        self.assertIn('總預算：3,000 元', home)
        self.assertIn('剩餘 1,799.25 元', home)

        settings = self.client.get('/settings').text
        category = settings.split('data-budget-category="餐飲"', 1)[1].split('</li>', 1)[0]
        self.assertIn('>1,000 元</span>', category)
        self.assertIn('value="1000"', category)

        search = self.client.get('/search?start=2026-09-01&end=2026-09-01').text
        calendar = self.client.get('/calendar?month=2026-09&day=2026-09-01').text
        for page in (search, calendar):
            for amount in ('NT$1,000', 'NT$100.5', 'NT$100.25'):
                self.assertIn(amount, page)
            self.assertNotIn('NT$100.50', page)
        self.assertIn('NT$1,200.75', calendar)

        self.assertEqual(
            sp.rows("SELECT cents FROM expenses WHERE user_id=? ORDER BY id", (self.user_id,)),
            [{'cents': 100000}, {'cents': 10050}, {'cents': 10025}],
        )
        self.assertEqual(sp.month_report(self.user_id)['total'], 1200.75)

    def test_home_information_reads_do_not_sync_or_mutate_existing_ledger(self):
        life_service.get_payment_sources(self.user_id)
        for user in (self.user_id, '999999999999999999'):
            for kind, periods in (('固定', 0), ('訂閱', 0), ('分期', 2)):
                sp.add_recurring(user, kind, kind, 10, '居住', '2026-09', periods)
        self._login()
        def snapshot():
            tables = [row['name'] for row in sp.rows(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            return {table: sp.rows(f'SELECT * FROM "{table}" ORDER BY rowid') for table in tables}
        before = snapshot()
        with patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')):
            self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(snapshot(), before)
        self.assertEqual(sp.rows('SELECT * FROM expenses'), [])

        new_client = TestClient(self.app)
        new_user = '888888888888888888'
        self._login(new_user, new_client)
        self.assertEqual(new_client.get('/').status_code, 200)
        self.assertEqual(sp.rows('SELECT name FROM payment_sources WHERE user_id=? ORDER BY name', (new_user,)),
                         [{'name': '未指定'}, {'name': '現金'}])
        for table in ('expenses', 'expense_actions', 'spending_notices', 'spending_users'):
            self.assertEqual(sp.rows(f'SELECT * FROM {table} WHERE user_id=?', (new_user,)), [])

    def test_home_card_failure_preserves_quick_entry_draft(self):
        self._login()
        form = self._valid_form(amount='bad', note='<script>draft</script>')
        for failed_read, error_type in (('get_month_summary', TypeError), ('get_calendar_days', ValueError)):
            with (
                self.subTest(failed_read=failed_read),
                patch.object(life_service, 'get_month_summary', wraps=life_service.get_month_summary) as summary,
                patch.object(life_service, 'get_calendar_days', wraps=life_service.get_calendar_days) as calendar,
            ):
                failed = summary if failed_read == 'get_month_summary' else calendar
                failed.side_effect = error_type('private SQLite C:/secret/data.db Token')
                response = self.client.post('/expenses', data=form)
                self.assertEqual(response.status_code, 400)
                self.assertIn('資料無法儲存，請檢查後再試。', response.text)
                self.assertIn('無法顯示本月資訊，請稍後重試。', response.text)
                self.assertIn('value="bad"', response.text)
                self.assertIn('value="&lt;script&gt;draft&lt;/script&gt;"', response.text)
                self.assertIn('value="2026-09-24"', response.text)
                self.assertIn('<option value="餐飲" selected>', response.text)
                self.assertIn(f'<option value="{form["payment_source_id"]}" selected>', response.text)
                self.assertNotIn('private SQLite', response.text)
                self.assertNotIn('<progress', response.text)
                self.assertNotIn('已使用 0%', response.text)
                self.assertLessEqual(summary.call_count, 1)
                self.assertLessEqual(calendar.call_count, 1)
        with (
            patch.object(life_service, 'get_month_summary', wraps=life_service.get_month_summary) as summary,
            patch.object(life_service, 'get_calendar_days', wraps=life_service.get_calendar_days) as calendar,
        ):
            response = self.client.post('/expenses', data=form | {'category': '不存在', 'payment_source_id': '999'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.text.count('disabled selected>原選擇無法使用'), 2)
        summary.assert_called_once_with(self.user_id, '2026-09')
        calendar.assert_called_once_with(self.user_id, '2026-09')
        with patch.object(life_service, 'get_month_summary', side_effect=ValueError('private')) as summary:
            response = self.client.post('/expenses', data=form | {'amount': '10', 'note': '有效'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        summary.assert_not_called()
        for _ in range(2):
            self.assertEqual(self.client.get(response.headers['location']).status_code, 200)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')), 1)

    def test_home_cards_layout_and_fixed_states(self):
        self._login()
        page = self.client.get('/').text
        positions = [page.index(value) for value in (
            'href="/search"', 'href="/settings"', 'class="entry-form"',
            'id="home-budget"', 'id="home-calendar"', 'class="logout-form"',
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('尚未設定總預算', page)
        self.assertIn('已記錄支出：0 元', page)
        self.assertIn('本月尚無已記錄的消費。', page)
        self.assertIn('查看完整月曆', page)
        self.assertEqual(page.count('class="calendar-day"'), 30)
        self.assertEqual(re.findall(r'class="calendar-weekday">(\w+)</', page),
                         ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
        life_service.add_expense(self.user_id, '120.50', '餐飲', '午餐', '2026-09-01')
        for amount, total, expected in (
            (1000, '總預算：1,000 元', '剩餘 879.5 元'),
            (100, '總預算：100 元', '超支 20.5 元'),
        ):
            sp.set_budget(self.user_id, '2026-09', '總額', amount)
            page = self.client.get('/').text
            self.assertIn(total, page)
            self.assertIn(expected, page)
        with patch.object(life_service, 'get_month_summary', side_effect=ValueError('private detail')):
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('無法顯示本月資訊，請稍後重試。', response.text)
        self.assertIn('class="entry-form"', response.text)
        self.assertNotIn('private detail', response.text)

    def test_home_mini_calendar_marks_all_recorded_consumption(self):
        life_service.add_expense(self.user_id, '120.50', '餐飲', '本人午餐', '2026-09-01')
        for kind, amount, periods in (('固定', 900, 0), ('訂閱', 80, 0), ('分期', 300, 1)):
            sp.add_recurring(self.user_id, kind, kind, amount, '居住', '2026-09', periods)
        sp.sync_recurring(self.user_id, date(2026, 9, 24))
        life_service.add_expense('999999999999999999', 999, '餐飲', '他人', '2026-09-02')
        voided = life_service.add_expense(self.user_id, 50, '餐飲', '已撤銷', '2026-09-03')
        life_service.void_expense(self.user_id, voided)
        for kind, day in (('income', '04'), ('transfer', '05'), ('investment', '06')):
            key = life_service.add_expense(self.user_id, 80, '餐飲', '排除', f'2026-09-{day}')
            with sp.transaction() as conn:
                conn.execute('UPDATE expenses SET kind=? WHERE id=?', (kind, key))
        self._login()
        home = self.client.get('/?user_id=999999999999999999').text
        self.assertIn('已記錄支出：1,400.5 元', home)
        mini = home.split('id="home-calendar"', 1)[1].split('</section>', 1)[0]
        full = self.client.get('/calendar').text
        for page in (mini, full):
            self.assertEqual(page.count('class="calendar-marker"'), 1)
            day = re.search(r'<a class="calendar-day"[^>]*day=2026-09-01[^>]*>.*?</a>', page, re.S).group()
            self.assertIn('calendar-marker', day)
            for forbidden in ('120.50', '1400.50', '本人午餐', '餐飲', '居住', '▓', '▒', '░', 'cents', self.user_id):
                self.assertNotIn(forbidden, day)

    def test_home_date_link_opens_existing_day_details(self):
        life_service.add_expense(self.user_id, 20, '餐飲', '連結明細', '2026-09-01')
        self._login()
        page = self.client.get('/').text
        for day, expected in (('01', '連結明細'), ('02', '此日期沒有已記錄的消費。')):
            target = re.search(r'href="([^"]+day=2026-09-' + day + r')"', page).group(1)
            response = self.client.get(unescape(target))
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.text)

    def test_calendar_future_dates_have_no_links(self):
        self._login()
        for target in ('/', '/calendar'):
            page = self.client.get(target).text
            self.assertIn('day=2026-09-24', page)
            for day in range(25, 31):
                self.assertNotIn(f'day=2026-09-{day}', page)
                self.assertRegex(page, rf'<span class="calendar-day"[^>]*aria-disabled="true"[^>]*aria-label="2026-09-{day}"')
        self.assertEqual(self.client.get('/calendar?month=2026-09&day=2026-09-25').status_code, 400)

    def test_home_budget_context_states_and_precision(self):
        # Catches treating category budgets as totals or using float subtraction.
        for budget, spent, remaining, overspent in (
            (1000, '120.5', '879.5', None),
            (100, '120.5', None, '20.5'),
            (100, '100', '0', None),
            (100, '0', '100', None),
            (1, '0.29', '0.71', None),
            (1000000000, '0', '1,000,000,000', None),
            (None, '0', None, None),
        ):
            with self.subTest(budget=budget, spent=spent):
                with sp.transaction() as conn:
                    conn.execute('DELETE FROM budgets')
                    conn.execute('DELETE FROM expenses')
                if budget is not None:
                    sp.set_budget(self.user_id, '2026-09', '總額', budget)
                else:
                    sp.set_budget(self.user_id, '2026-09', '餐飲', 10)
                if spent != '0':
                    life_service.add_expense(self.user_id, spent, '餐飲', '測試', '2026-09-01')
                context = routes._home_cards_context(self.user_id, as_of=date(2026, 9, 24), active_categories=life_service.get_categories(self.user_id))
                card = context['home_budget']
                self.assertIsNone(context['home_cards_error'])
                self.assertEqual(card['spent'], spent)
                self.assertEqual(card['total_budget'], format(budget, ',') if budget is not None else None)
                self.assertEqual(card['remaining'], remaining)
                self.assertEqual(card['overspent'], overspent)

    def test_home_legacy_fractional_budget_is_safe(self):
        self._login()
        for category, cents in (('總額', 100050), ('餐飲', 150), ('餐飲', -100), ('總額', 'invalid')):
            with self.subTest(category=category, cents=cents):
                with sp.transaction() as conn:
                    conn.execute('DELETE FROM budgets')
                    conn.execute('INSERT INTO budgets VALUES(?,?,?,?)', (self.user_id, '2026-09', category, cents))
                context = routes._home_cards_context(self.user_id, as_of=date(2026, 9, 24), active_categories=life_service.get_categories(self.user_id))
                self.assertEqual(context['home_cards_error'], '無法顯示本月資訊，請稍後重試。')
                self.assertIsNone(context['home_budget'])
                self.assertIsNone(context['home_calendar'])
                self.assertEqual(sp.rows('SELECT cents FROM budgets'), [{'cents': cents}])
                page = self.client.get('/').text
                self.assertIn('無法顯示本月資訊，請稍後重試。', page)
                self.assertNotIn('<progress', page)

    def test_home_month_queries_are_once_and_session_scoped(self):
        with (
            patch.object(life_service, 'get_month_summary', wraps=life_service.get_month_summary) as summary,
            patch.object(life_service, 'get_calendar_days', wraps=life_service.get_calendar_days) as calendar,
            patch.object(life_service, 'list_expenses', side_effect=AssertionError('no details')),
            patch.object(life_service, 'search_expenses', side_effect=AssertionError('no search')),
            patch.object(life_service, 'get_chart_data', side_effect=AssertionError('no chart')),
            patch.object(life_service, 'sync_recurring', side_effect=AssertionError('no sync')),
        ):
            self.assertEqual(self.client.get('/').status_code, 200)
            summary.assert_not_called()
            calendar.assert_not_called()
            self._login()
            response = self.client.get('/?user_id=999999999999999999&month=2024-02&day=2024-02-01')
            self.assertEqual(response.status_code, 200)
            summary.assert_called_once_with(self.user_id, '2026-09')
            calendar.assert_called_once_with(self.user_id, '2026-09')

    def test_home_calendar_grid_lengths_and_links(self):
        for last_day, count, blanks in (
            (date(2025, 2, 28), 28, 5), (date(2024, 2, 29), 29, 3),
            (date(2026, 9, 30), 30, 1), (date(2026, 8, 31), 31, 5),
        ):
            with self.subTest(month=last_day), patch('spending.today', return_value=last_day):
                context = routes._home_cards_context(self.user_id, as_of=last_day, active_categories=life_service.get_categories(self.user_id))
                grid = context['home_calendar']
                self.assertEqual(len(grid['calendar_days']), count)
                self.assertEqual(grid['leading_blanks'], blanks)
                self.assertFalse(any(item['is_future'] for item in grid['calendar_days']))
        context = routes._home_cards_context(self.user_id, as_of=date(2026, 9, 24), active_categories=life_service.get_categories(self.user_id))
        self.assertFalse(context['home_calendar']['calendar_days'][23]['is_future'])
        self.assertTrue(all(item['is_future'] for item in context['home_calendar']['calendar_days'][24:]))
        with patch('spending.today', return_value=date(2030, 1, 2)):
            context = routes._quick_entry_context(self.user_id, 'test', as_of=date(2030, 1, 2))
        self.assertEqual(context['values']['spent_on'], '2030-01-02')
        self.assertEqual(context['home_calendar']['month_text'], '2030-01')


class WebQuickEntryTests(_WebLedgerTestFixture, unittest.TestCase):

    def test_logged_out_home_does_not_expose_quick_entry_or_ledger_options(self):
        sp.set_category(self.user_id, "私人分類", True)
        sp.add_payment_source(self.user_id, "私人卡片")

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("使用 Discord 登入", response.text)
        self.assertNotIn("快速記帳", response.text)
        self.assertNotIn("私人分類", response.text)
        self.assertNotIn("私人卡片", response.text)

    def test_home_only_lists_the_logged_in_users_options(self):
        sp.set_category(self.user_id, "本人分類", True)
        own_source = sp.add_payment_source(self.user_id, "本人卡片")
        sp.set_category("999999999999999999", "他人分類", True)
        other_source = sp.add_payment_source("999999999999999999", "他人卡片")
        self._login()

        response = self.client.get("/")

        self.assertIn("本人分類", response.text)
        self.assertIn("本人卡片", response.text)
        self.assertIn(f'value="{own_source}"', response.text)
        self.assertNotIn("他人分類", response.text)
        self.assertNotIn("他人卡片", response.text)
        self.assertNotIn(f'value="{other_source}"', response.text)

    def test_category_defaults_to_other_then_falls_back_to_first_active(self):
        self._login()

        response = self.client.get("/")
        self.assertRegex(
            response.text,
            r'<option value="其他" selected>其他</option>',
        )

        sp.set_category(self.user_id, "其他", False)
        response = self.client.get("/")
        self.assertNotIn('value="其他"', response.text)
        self.assertRegex(
            response.text,
            r'<option value="餐飲" selected>餐飲</option>',
        )

    def test_date_defaults_to_core_taiwan_today(self):
        self._login()

        with patch("spending.today", return_value=date(2030, 1, 2)):
            response = self.client.get("/")

        self.assertIn('name="spent_on"', response.text)
        self.assertIn('value="2030-01-02"', response.text)

    def test_logged_in_home_labels_note_as_expense_item(self):
        self._login()

        response = self.client.get("/")

        self.assertIn('<label for="note">消費項目</label>', response.text)
        self.assertIn(
            'placeholder="例如：午餐、全聯、Netflix"',
            response.text,
        )
        self.assertNotIn('<label for="note">用途</label>', response.text)

    def test_recent_valid_manual_payment_source_is_preselected(self):
        source = sp.add_payment_source(self.user_id, "日常卡")
        life_service.add_expense(
            self.user_id,
            "80",
            "餐飲",
            "早餐",
            "2026-09-23",
            source,
        )
        self._login()

        response = self.client.get("/")

        self.assertRegex(
            response.text,
            rf'<option value="{source}" selected>日常卡</option>',
        )

    def test_unusable_recent_source_falls_back_to_unspecified_without_leaking(self):
        source = sp.add_payment_source(self.user_id, "停用卡")
        expense_id = life_service.add_expense(
            self.user_id,
            "80",
            "餐飲",
            "早餐",
            "2026-09-23",
            source,
        )
        sp.disable_payment_source(self.user_id, source)
        other_source = sp.add_payment_source("999999999999999999", "他人祕密卡")
        self._login()

        response = self.client.get("/")
        self.assertRegex(
            response.text,
            r'<option value="\d+" selected>未指定</option>',
        )
        self.assertNotIn("停用卡", response.text)

        for unusable_id in (999999, other_source):
            with self.subTest(unusable_id=unusable_id):
                conn = db.get_conn()
                try:
                    conn.execute(
                        "UPDATE expenses SET payment_source_id=? WHERE id=?",
                        (unusable_id, expense_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
                response = self.client.get("/")
                self.assertRegex(
                    response.text,
                    r'<option value="\d+" selected>未指定</option>',
                )
                self.assertNotIn(f'value="{unusable_id}"', response.text)
                self.assertNotIn("他人祕密卡", response.text)

    def test_valid_post_adds_one_consumption_for_session_user(self):
        self._login()
        response = self.client.post(
            "/expenses",
            data=self._valid_form(),
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        rows = sp.rows("SELECT * FROM expenses")
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (
                rows[0]["user_id"],
                rows[0]["cents"],
                rows[0]["note"],
                rows[0]["kind"],
                rows[0]["spent_on"],
            ),
            (self.user_id, 12050, "午餐", "consumption", "2026-09-24"),
        )

    def test_query_and_form_user_id_cannot_change_expense_owner(self):
        self._login()
        form = self._valid_form(user_id="999999999999999999")

        response = self.client.post(
            "/expenses?user_id=999999999999999999",
            data=form,
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        rows = sp.rows("SELECT user_id FROM expenses")
        self.assertEqual(rows, [{"user_id": self.user_id}])

    def test_other_users_payment_source_is_rejected_without_any_write(self):
        other_source = sp.add_payment_source("999999999999999999", "他人卡片")
        self._login()

        response = self.client.post(
            "/expenses",
            data=self._valid_form(payment_source_id=str(other_source)),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(sp.rows("SELECT * FROM expenses"), [])
        self.assertNotIn("他人卡片", response.text)
        self.assertNotIn(f'value="{other_source}"', response.text)

    def test_invalid_csrf_is_rejected_without_write_and_keeps_session(self):
        self._login()
        token = self._csrf_token()
        valid = self._valid_form()
        requests = (
            {key: value for key, value in valid.items() if key != "csrf_token"},
            valid | {"csrf_token": "wrong"},
            f"csrf_token={token}&csrf_token={token}&amount=1&note=x&category=餐飲"
            "&payment_source_id=1&spent_on=2026-09-24",
        )

        for submitted in requests:
            with self.subTest(submitted=type(submitted).__name__):
                if isinstance(submitted, str):
                    response = self.client.post(
                        "/expenses",
                        content=submitted.encode("utf-8"),
                        headers={
                            "content-type": "application/x-www-form-urlencoded"
                        },
                    )
                else:
                    response = self.client.post("/expenses", data=submitted)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(sp.rows("SELECT * FROM expenses"), [])
                self.assertIn("快速記帳", self.client.get("/").text)

    def test_invalid_fields_return_safe_error_preserve_draft_and_do_not_write(self):
        self._login()
        source_id = str(life_service.get_payment_sources(self.user_id)[0]["id"])
        invalid_cases = (
            ("amount", "not-money"),
            ("note", " "),
            ("spent_on", "2026-09-25"),
            ("spent_on", "not-a-date"),
            ("category", "<私人分類>"),
            ("payment_source_id", "999999"),
        )
        for field, value in invalid_cases:
            with self.subTest(field=field, value=value):
                response = self.client.post(
                    "/expenses",
                    data=self._valid_form(**{field: value}),
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("資料無法儲存，請檢查後再試。", response.text)
                self.assertNotIn("SQLite", response.text)
                self.assertNotIn("Traceback", response.text)
                self.assertNotIn(".db", response.text)
                self.assertNotIn("Token", response.text)
                self.assertNotIn(self.user_id, response.text)
                self.assertEqual(sp.rows("SELECT * FROM expenses"), [])
                expected_amount = value if field == "amount" else "120.50"
                expected_note = value if field == "note" else "午餐"
                expected_date = value if field == "spent_on" else "2026-09-24"
                self.assertIn(f'value="{expected_amount}"', response.text)
                self.assertIn(f'value="{expected_note}"', response.text)
                self.assertIn(f'value="{expected_date}"', response.text)
                if field == "category":
                    self.assertIn("原選擇無法使用", response.text)
                    self.assertNotIn("&lt;私人分類&gt;", response.text)
                elif field == "payment_source_id":
                    self.assertIn("原選擇無法使用", response.text)
                    self.assertNotIn(value, response.text)
                else:
                    self.assertIn(f'value="{source_id}"', response.text)

    def test_missing_or_duplicate_non_csrf_field_returns_400_without_write(self):
        self._login()
        valid = self._valid_form()
        missing = {key: value for key, value in valid.items() if key != "amount"}
        duplicate = (
            f"csrf_token={valid['csrf_token']}&amount=1&amount=2&note=x"
            f"&category=餐飲&payment_source_id={valid['payment_source_id']}"
            "&spent_on=2026-09-24"
        )

        for submitted in (missing, duplicate):
            with self.subTest(submitted=type(submitted).__name__):
                kwargs = {"data": submitted} if isinstance(submitted, dict) else {
                    "content": submitted.encode("utf-8"),
                    "headers": {
                        "content-type": "application/x-www-form-urlencoded"
                    },
                }
                response = self.client.post("/expenses", **kwargs)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(sp.rows("SELECT * FROM expenses"), [])

    def test_success_post_redirects_to_saved_page(self):
        self._login()
        response = self.client.post(
            "/expenses",
            data=self._valid_form(),
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/?saved=1")

    def test_saved_page_refresh_does_not_duplicate(self):
        self._login()
        response = self.client.post(
            "/expenses",
            data=self._valid_form(note="已儲存測試項目"),
            follow_redirects=False,
        )

        saved = self.client.get(response.headers["location"])
        self.assertIn("已儲存消費", saved.text)
        self.assertRegex(saved.text, r'name="amount"[^>]*value=""')
        self.assertRegex(saved.text, r'name="note"[^>]*value=""')
        self.assertIn("已記錄支出：120.5 元", saved.text)
        self.assertNotIn("已儲存測試項目", saved.text)
        self.client.get("/?saved=1")
        self.assertEqual(len(sp.rows("SELECT * FROM expenses")), 1)

    def test_logged_in_home_links_to_calendar(self):
        self._login()

        response = self.client.get("/")

        self.assertIn('href="/calendar"', response.text)
        self.assertIn("查看完整月曆", response.text)
        self.assertIn(">記錄消費</button>", response.text)
        self.assertNotIn(">儲存消費</button>", response.text)

    def test_logged_in_home_links_to_search(self):
        self._login()

        response = self.client.get("/")

        self.assertIn('href="/search"', response.text)
        self.assertIn("搜尋帳目", response.text)

    def test_web_modules_keep_database_access_behind_life_service(self):
        web_root = Path(__file__).parents[1] / "web"
        imports = {}
        for name in ("app.py", "auth.py", "routes.py"):
            tree = ast.parse(web_root.joinpath(name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            imports[name] = imported

        for name in imports:
            self.assertTrue(imports[name].isdisjoint({"spending", "db", "ledger"}))
        self.assertNotIn("life_ledger_service", imports["app.py"])
        self.assertNotIn("life_ledger_service", imports["auth.py"])
        self.assertIn("life_ledger_service", imports["routes.py"])

        service_path = Path(__file__).parents[1] / "life_ledger_service.py"
        service_tree = ast.parse(service_path.read_text(encoding="utf-8"))
        service_imports = set()
        for node in ast.walk(service_tree):
            if isinstance(node, ast.Import):
                service_imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                service_imports.add(node.module.split(".")[0])
        self.assertTrue(
            service_imports.isdisjoint(
                {"discord", "web", "db", "sqlite3", "ledger"}
            )
        )


class WebCalendarTests(_WebLedgerTestFixture, unittest.TestCase):
    def test_logged_out_calendar_does_not_expose_ledger_data(self):
        life_service.add_expense(
            self.user_id,
            "120",
            "餐飲",
            "午餐",
            "2026-09-24",
        )

        with (
            patch("web.routes.life_service.get_calendar_days") as calendar_days,
            patch("web.routes.life_service.list_expenses") as list_expenses,
        ):
            response = self.client.get("/calendar?month=2026-09")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("午餐", response.text)
        self.assertNotIn("120", response.text)
        self.assertNotIn(self.user_id, response.text)
        calendar_days.assert_not_called()
        list_expenses.assert_not_called()

    def test_calendar_defaults_to_taiwan_current_month(self):
        self._login()

        response = self.client.get("/calendar")

        self.assertEqual(response.status_code, 200)
        self.assertIn("2026 年 09 月", response.text)

    def test_calendar_rejects_invalid_future_and_mismatched_date_safely(self):
        self._login()
        targets = (
            "/calendar?month=2026-9",
            "/calendar?month=2026-13",
            "/calendar?month=2026-10",
            "/calendar?month=2026-09&day=2026-09-31",
            "/calendar?month=2026-09&day=2026-08-31",
            "/calendar?month=2026-09&day=2026-09-25",
        )

        for target in targets:
            with self.subTest(target=target):
                response = self.client.get(target)
                self.assertEqual(response.status_code, 400)
                self.assertIn("無法顯示月曆，請返回後重試。", response.text)
                for forbidden in ("SQLite", "Traceback", ".db", self.user_id):
                    self.assertNotIn(forbidden, response.text)

    def test_invalid_calendar_query_does_not_call_services(self):
        self._login()

        with (
            patch("web.routes.life_service.get_calendar_days") as calendar_days,
            patch("web.routes.life_service.list_expenses") as list_expenses,
        ):
            response = self.client.get("/calendar?month=2026-10")

        self.assertEqual(response.status_code, 400)
        calendar_days.assert_not_called()
        list_expenses.assert_not_called()

    def test_calendar_service_errors_use_fixed_safe_response(self):
        self._login()
        service_calls = (
            "get_calendar_days",
            "list_expenses",
        )

        for service_name in service_calls:
            for error_type in (ValueError, TypeError):
                with self.subTest(
                    service_name=service_name,
                    error_type=error_type.__name__,
                ):
                    with patch(
                        f"web.routes.life_service.{service_name}",
                        side_effect=error_type("internal-detail"),
                    ):
                        response = self.client.get("/calendar?month=2026-09")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("無法顯示月曆，請返回後重試。", response.text)
                    self.assertNotIn("internal-detail", response.text)

    def test_calendar_is_monday_first_for_different_month_lengths(self):
        self._login()

        september = self.client.get("/calendar?month=2026-09")
        leap_february = self.client.get("/calendar?month=2024-02")
        february = self.client.get("/calendar?month=2025-02")
        august = self.client.get("/calendar?month=2026-08")

        self.assertEqual(september.text.count('class="calendar-blank"'), 1)
        self.assertEqual(september.text.count('class="calendar-day"'), 30)
        self.assertEqual(leap_february.text.count('class="calendar-blank"'), 3)
        self.assertEqual(leap_february.text.count('class="calendar-day"'), 29)
        self.assertEqual(february.text.count('class="calendar-day"'), 28)
        self.assertEqual(august.text.count('class="calendar-day"'), 31)
        weekdays = re.findall(
            r'class="calendar-weekday">(Mon|Tue|Wed|Thu|Fri|Sat|Sun)</',
            leap_february.text,
        )
        self.assertEqual(
            weekdays,
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        )

    def test_calendar_day_marker_never_contains_amount_or_heat_level(self):
        life_service.add_expense(
            self.user_id,
            "120",
            "餐飲",
            "午餐",
            "2026-09-02",
        )
        self._login()

        response = self.client.get("/calendar?month=2026-09")
        matched_day = re.search(
            r'<a class="calendar-day"[^>]+day=2026-09-02[^>]*>(.*?)</a>',
            response.text,
            re.DOTALL,
        )

        self.assertIsNotNone(matched_day)
        day = matched_day.group(1)
        self.assertIn('class="calendar-marker"', day)
        for forbidden in ("120", "120.00", "NT$120", "▓", "▒", "░"):
            self.assertNotIn(forbidden, day)

    def test_calendar_navigation_never_links_to_a_future_month(self):
        self._login()

        august = self.client.get("/calendar?month=2026-08")
        current = self.client.get("/calendar?month=2026-09")

        self.assertIn('href="/calendar?month=2026-07"', august.text)
        self.assertIn('href="/calendar?month=2026-09"', august.text)
        self.assertIn("回到本月", august.text)
        self.assertIn('aria-label="上個月" title="上個月">←</a>', august.text)
        self.assertIn('<h1 class="calendar-nav-month">2026 年 08 月</h1>', august.text)
        self.assertIn('aria-label="下個月" title="下個月">→</a>', august.text)
        self.assertNotIn("month=2026-10", current.text)
        self.assertIn('aria-disabled="true" aria-label="下個月" title="下個月">→</span>', current.text)
        css = (Path(__file__).parents[1] / "web/static/web.css").read_text(encoding="utf-8")
        self.assertIn("width: 44px", css)
        self.assertIn("height: 44px", css)
        self.assertIn(":focus-visible", css)

    def test_selected_day_includes_all_valid_consumption_and_excludes_others(self):
        own_source = sp.add_payment_source(self.user_id, "本人卡")
        life_service.add_expense(
            self.user_id,
            "120.50",
            "餐飲",
            "午餐",
            "2026-09-01",
            own_source,
        )
        for kind, name, amount, periods in (
            ("固定", "房租", "900", 0),
            ("訂閱", "影音訂閱", "80", 0),
            ("分期", "筆電分期", "300", 1),
        ):
            sp.add_recurring(
                self.user_id,
                kind,
                name,
                amount,
                "居住",
                "2026-09",
                periods,
            )
        sp.sync_recurring(self.user_id, date(2026, 9, 24))
        life_service.add_expense(
            "999999999999999999",
            "999",
            "餐飲",
            "他人晚餐",
            "2026-09-01",
        )
        voided = life_service.add_expense(
            self.user_id,
            "50",
            "餐飲",
            "已撤銷用途",
            "2026-09-01",
        )
        life_service.void_expense(self.user_id, voided)
        self._login()

        response = self.client.get(
            "/calendar?month=2026-09&day=2026-09-01"
            "&user_id=999999999999999999&unknown=ignored"
        )

        self.assertEqual(response.status_code, 200)
        for expected in (
            "午餐",
            "房租",
            "影音訂閱",
            "筆電分期",
            "餐飲",
            "居住",
            "本人卡",
            "1,400.5",
        ):
            self.assertIn(expected, response.text)
        self.assertRegex(
            response.text,
            r'(?s)class="calendar-day selected"[^>]+day=2026-09-01.*?calendar-marker',
        )
        for forbidden in (
            "他人晚餐",
            "已撤銷用途",
            "Discord ID",
            self.user_id,
            "999999999999999999",
            "revision",
            "voided",
            "origin",
            "entry_type",
        ):
            self.assertNotIn(forbidden, response.text)

    def test_empty_month_and_empty_selected_day_use_fixed_states(self):
        self._login()

        empty_month = self.client.get("/calendar?month=2026-08")
        empty_day = self.client.get(
            "/calendar?month=2026-09&day=2026-09-03"
        )

        self.assertEqual(empty_month.status_code, 200)
        self.assertEqual(empty_month.text.count('class="calendar-day"'), 31)
        self.assertNotIn('class="calendar-marker"', empty_month.text)
        self.assertIn("本月尚無已記錄的消費。", empty_month.text)
        self.assertEqual(empty_day.status_code, 200)
        self.assertIn("2026-09-03", empty_day.text)
        self.assertIn("NT$0", empty_day.text)
        self.assertIn("此日期沒有已記錄的消費。", empty_day.text)


class WebSearchTests(_WebLedgerTestFixture, unittest.TestCase):
    def test_search_all_sources_keeps_keyword_date_and_identity(self):
        manual = life_service.add_expense(self.user_id, '.29', '餐飲', '共用午餐手動', '2026-09-01')
        for kind, periods in (('固定', 0), ('訂閱', 0), ('分期', 2)):
            sp.add_recurring(self.user_id, kind, '共用午餐'+kind, 10, '居住', '2026-09', periods)
        sp.add_recurring(self.user_id, '固定', '共用午餐預測', 999, '居住', '2026-10')
        sp.sync_recurring(self.user_id, date(2026, 9, 24))
        for owner, note, on in ((self.user_id, '共用午餐日期外', '2026-09-02'),
                                ('999999999999999999', '共用午餐他人', '2026-09-01'),
                                (self.user_id, '不同項目', '2026-09-01')):
            life_service.add_expense(owner, 999, '餐飲', note, on)
        for kind in ('voided', 'income', 'transfer', 'investment'):
            key = life_service.add_expense(self.user_id, 999, '餐飲', '共用午餐排除'+kind, '2026-09-01')
            if kind == 'voided':
                life_service.void_expense(self.user_id, key)
            else:
                with sp.transaction() as conn:
                    conn.execute('UPDATE expenses SET kind=? WHERE id=?', (kind, key))
        self._login()
        with patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')):
            response = self.client.get('/search', params=dict(keyword='共用午餐', start='2026-09-01', end='2026-09-01', user_id='999999999999999999'))
        self.assertEqual(response.status_code, 200)
        results = response.context['results']
        self.assertEqual(len(results), 4)
        expected = life_service.list_expenses_in_range(self.user_id, '2026-09-01', '2026-09-01', keyword='共用午餐')['items']
        self.assertEqual([row['note'] for row in results], [row['note'] for row in expected])
        for row in expected:
            self.assertIn(f'/expenses/{row["id"]}/edit?', response.text)
            if row['id'] != manual:
                edit = self.client.get(f'/expenses/{row["id"]}/edit')
                self.assertTrue(edit.context['auto_date'])
        for forbidden in ('日期外', '他人', '不同項目', '預測', '排除', self.user_id):
            self.assertNotIn(forbidden, response.text)

    def test_web_search_and_csv_range_share_eligibility_not_page_limits(self):
        for n in range(8):
            life_service.add_expense(self.user_id, 1, '餐飲', '匹配'+str(n), '2026-08-31' if n < 2 else '2026-09-01')
        sp.add_recurring(self.user_id, '固定', '另一項', 1, '居住', '2026-09')
        sp.sync_recurring(self.user_id, date(2026, 9, 24))
        self._login()
        all_items = life_service.list_expenses_in_range(self.user_id, '2026-08-31', '2026-09-24')['items']
        self.assertEqual(len(life_service.list_expenses(self.user_id, '2026-09', limit=2)['items']), 2)
        response = self.client.get('/search?start=2026-08-31&end=2026-09-24&limit=2')
        self.assertEqual([row['note'] for row in response.context['results']], [row['note'] for row in all_items])
        self.assertEqual(len(all_items), 9)
        subset = self.client.get('/search?keyword=匹配&start=2026-08-31&end=2026-09-24')
        self.assertEqual([row['note'] for row in subset.context['results']], [row['note'] for row in all_items if '匹配' in row['note']])
        page = self.client.get('/export')
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        download = self.client.post('/export/csv', data=dict(csrf_token=token, start='2026-08-31', end='2026-09-24', limit='2'))
        self.assertEqual(download.status_code, 200)
        rows = list(csv.reader(io.StringIO(download.content.decode('utf-8-sig'), newline='')))
        self.assertEqual([row[2] for row in rows[1:]], [row['note'] for row in all_items])

    def test_logged_out_search_does_not_call_service_or_expose_data(self):
        life_service.add_expense(
            self.user_id,
            "120",
            "餐飲",
            "私人午餐",
            "2026-09-24",
        )

        with patch("web.routes.life_service.list_expenses_in_range") as search:
            response = self.client.get("/search?keyword=午餐")

        self.assertEqual(response.status_code, 403)
        search.assert_not_called()
        for forbidden in ("私人午餐", "120", self.user_id):
            self.assertNotIn(forbidden, response.text)

    def test_blank_search_only_shows_form_without_query_or_error(self):
        self._login()

        with patch("web.routes.life_service.list_expenses_in_range") as search:
            response = self.client.get("/search")

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="keyword"', response.text)
        self.assertEqual(response.text.count('type="date"'), 2)
        self.assertIn('name="start"', response.text)
        self.assertIn('name="end"', response.text)
        self.assertNotIn("無法搜尋帳目", response.text)
        self.assertNotIn("沒有符合條件的消費", response.text)
        search.assert_not_called()

    def test_supported_search_shapes_use_session_user_and_expected_dates(self):
        self._login()
        cases = (
            (
                {"keyword": "咖啡", "user_id": "999999999999999999"},
                (self.user_id, "咖啡", None, None),
            ),
            (
                {"start": "2026-09-01"},
                (self.user_id, "", "2026-09-01", "2026-09-24"),
            ),
            (
                {"keyword": "咖啡", "end": "2026-09-20"},
                (self.user_id, "咖啡", None, "2026-09-20"),
            ),
            (
                {"start": "2026-09-01", "end": "2026-09-20"},
                (self.user_id, "", "2026-09-01", "2026-09-20"),
            ),
        )

        for params, expected in cases:
            with self.subTest(params=params):
                with patch(
                    "web.routes.life_service.list_expenses_in_range",
                    return_value={"items": [], "total": 0},
                ) as search:
                    response = self.client.get("/search", params=params)
                self.assertEqual(response.status_code, 200)
                search.assert_called_once_with(expected[0], expected[2], expected[3], keyword=expected[1])

    def test_invalid_search_conditions_are_safe_and_do_not_query(self):
        self._login()
        cases = (
            {"end": "2026-09-20"},
            {"start": "2026/09/01"},
            {"start": "2026-02-30"},
            {"start": "2026-09-25"},
            {"start": "2026-09-20", "end": "2026-09-01"},
        )

        for params in cases:
            with self.subTest(params=params):
                with patch("web.routes.life_service.list_expenses_in_range") as search:
                    response = self.client.get("/search", params=params)
                self.assertEqual(response.status_code, 400)
                search.assert_not_called()
                self.assertIn(
                    "無法搜尋帳目，請檢查條件後重試。",
                    response.text,
                )
                for value in params.values():
                    self.assertIn(f'value="{value}"', response.text)
                for forbidden in (
                    "SQLite",
                    "Traceback",
                    ".db",
                    "Token",
                    self.user_id,
                ):
                    self.assertNotIn(forbidden, response.text)

    def test_results_show_safe_fields_and_exclude_foreign_voided_and_other_kinds(self):
        source = sp.add_payment_source(self.user_id, "本人卡")
        life_service.add_expense(
            self.user_id,
            "120.50",
            "餐飲",
            "本人午餐",
            "2026-09-10",
            source,
        )
        life_service.add_expense(
            "999999999999999999",
            "999",
            "餐飲",
            "他人午餐",
            "2026-09-10",
        )
        voided = life_service.add_expense(
            self.user_id,
            "50",
            "餐飲",
            "已撤銷午餐",
            "2026-09-10",
        )
        life_service.void_expense(self.user_id, voided)
        excluded_notes = []
        for kind in ("investment", "income", "transfer"):
            note = f"排除{kind}"
            expense_id = life_service.add_expense(
                self.user_id,
                "10",
                "其他",
                note,
                "2026-09-10",
            )
            conn = db.get_conn()
            try:
                conn.execute(
                    "UPDATE expenses SET kind=? WHERE id=?",
                    (kind, expense_id),
                )
                conn.commit()
            finally:
                conn.close()
            excluded_notes.append(note)
        self._login()

        response = self.client.get(
            "/search?start=2026-09-01"
            "&user_id=999999999999999999&unknown=ignored"
        )

        self.assertEqual(response.status_code, 200)
        for expected in (
            "2026-09-10",
            "NT$120.5",
            "本人午餐",
            "餐飲",
            "本人卡",
        ):
            self.assertIn(expected, response.text)
        for forbidden in (
            "他人午餐",
            "已撤銷午餐",
            *excluded_notes,
            self.user_id,
            "999999999999999999",
            "revision",
            "voided",
            "origin",
            "entry_type",
        ):
            self.assertNotIn(forbidden, response.text)

    def test_zero_results_show_fixed_empty_state(self):
        self._login()

        response = self.client.get("/search?keyword=不存在的項目")

        self.assertEqual(response.status_code, 200)
        self.assertIn("沒有符合條件的消費。", response.text)

    def test_service_errors_use_fixed_message_and_preserve_form(self):
        self._login()

        for error_type in (TypeError, ValueError):
            with self.subTest(error_type=error_type.__name__):
                with patch(
                    "web.routes.life_service.list_expenses_in_range",
                    side_effect=error_type("private-internal-detail"),
                ):
                    response = self.client.get("/search?keyword=咖啡")
                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    "無法搜尋帳目，請檢查條件後重試。",
                    response.text,
                )
                self.assertIn('value="咖啡"', response.text)
                self.assertNotIn("private-internal-detail", response.text)

    def test_query_values_are_html_escaped(self):
        self._login()
        keyword = "<script>alert(1)</script>"

        with patch(
            "web.routes.life_service.list_expenses_in_range",
            return_value={"items": [], "total": 0},
        ):
            response = self.client.get("/search", params={"keyword": keyword})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(keyword, response.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", response.text)


class WebCsvExportTests(_WebLedgerTestFixture, unittest.TestCase):
    def _export_form(self, **changes):
        page = self.client.get('/export')
        values = dict(csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1),
                      start='2026-09-01', end='2026-09-24')
        return values | changes

    def _export_post(self, values):
        return self.client.post('/export/csv', content=urlencode(values, doseq=True),
                                headers={'content-type': 'application/x-www-form-urlencoded'}, follow_redirects=False)

    def _snapshot(self):
        tables = [row['name'] for row in sp.rows("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: sp.rows(f'SELECT * FROM "{table}" ORDER BY rowid') for table in tables}

    def test_export_page_defaults_warning_and_login(self):
        with patch.object(life_service, 'list_expenses_in_range') as query:
            for response in (self.client.get('/export'), self.client.post('/export/csv', data={'start': '2026-09-01'})):
                self.assertEqual(response.status_code, 403)
                self.assertIn('請先使用 Discord 登入。', response.text)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            query.assert_not_called()
        self._login()
        with (patch.object(life_service, 'list_expenses_in_range') as query,
              patch.object(life_service, 'get_today', wraps=life_service.get_today) as today):
            response = self.client.get('/export?start=1999-01-01&end=1999-01-02&user_id=999999999999999999')
            query.assert_not_called()
            today.assert_called_once_with()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        for name, value in (('start', '2026-09-01'), ('end', '2026-09-24')):
            self.assertRegex(response.text, rf'<input[^>]*name="{name}"[^>]*type="date"[^>]*value="{value}"[^>]*required')
        for text in ('CSV 消費帳目匯出', '未加密', '完整還原帳號', '單引號', '有效生活消費', '下載 CSV',
                     'name="csrf_token"', 'method="post" action="/export/csv"', 'href="/"'):
            self.assertIn(text, response.text)

    def test_export_entry_is_only_in_collapsed_settings_section(self):
        self._login()
        page = self.client.get('/settings').text
        self.assertEqual(re.findall(r'data-settings-section="([^"]+)"', page), ['budget', 'categories', 'payments', 'fixed', 'export'])
        section = re.search(r'<details[^>]*data-settings-section="export"[^>]*>.*?</details>', page, re.S).group()
        self.assertNotIn(' open', section.split('>', 1)[0])
        for text in ('<summary>資料匯出</summary>', 'href="/export"', '未加密', '分析用', '不是完整備份', '完整還原帳號'):
            self.assertIn(text, section)
        self.assertEqual(routes.SETTINGS_SECTIONS, {'budget', 'categories', 'payments'})
        for url in ('/', '/search?keyword=測試'):
            self.assertNotIn('href="/export', self.client.get(url).text)

    def test_csv_download_headers_and_session_scope(self):
        life_service.add_expense(self.user_id, '.29', '餐飲', '跨月本人', '2026-08-31')
        for kind, periods in (('固定', 0), ('訂閱', 0), ('分期', 2)):
            sp.add_recurring(self.user_id, kind, kind, 1, '居住', '2026-09', periods)
        sp.sync_recurring(self.user_id, date(2026, 9, 24))
        life_service.add_expense('999999999999999999', 999, '餐飲', '他人', '2026-09-01')
        self._login()
        form = self._export_form(start='2026-08-31', user_id='999999999999999999', keyword='無匹配',
                                 limit='1', offset='9999', filename='evil.csv', path='C:/secret', next='https://example.com')
        with patch.object(life_service, 'list_expenses_in_range', wraps=life_service.list_expenses_in_range) as query:
            response = self.client.post('/export/csv?user_id=999999999999999999&start=2000-01-01',
                                        data=form, follow_redirects=False)
        query.assert_called_once_with(self.user_id, '2026-08-31', '2026-09-24')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'text/csv; charset=utf-8')
        self.assertEqual(response.headers['content-disposition'], 'attachment; filename="discordbot-expenses.csv"')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'), newline='')))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1][:3], ['2026-08-31', '0.29', '跨月本人'])
        self.assertNotIn('他人', response.text)
        self.assertNotIn(self.user_id, response.text)
        self.assertEqual(self._export_post(form).content, response.content)

    def test_export_rejects_csrf_and_dates_without_query(self):
        self._login()
        form = self._export_form()
        csrf_cases = [form | {'csrf_token': token} for token in ('wrong', 'é', [form['csrf_token'], form['csrf_token']])]
        csrf_cases.append({key: value for key, value in form.items() if key != 'csrf_token'})
        with patch.object(life_service, 'list_expenses_in_range') as query:
            for invalid in csrf_cases:
                response = self._export_post(invalid)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn('content-disposition', response.headers)
                self.assertNotIn(form['start'], response.text)
            for options in ({'json': form}, {'content': b'\xff', 'headers': {'content-type': 'application/x-www-form-urlencoded'}}):
                self.assertEqual(self.client.post('/export/csv', **options).status_code, 403)
            for changes in ({'start': ''}, {'end': ''}, {'start': '2026/09/01'}, {'start': '2026-02-30'},
                            {'start': '2026-09-25'}, {'end': '2026-09-25'}, {'start': '2026-09-24', 'end': '2026-09-01'},
                            {'start': ['2026-09-01', '2026-09-02']}, {'end': ['2026-09-24', '2026-09-24']},
                            {'start': '<script>bad</script>'}):
                response = self._export_post(form | changes)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertIn('無法匯出帳目，請檢查日期後重試。', response.text)
                self.assertNotIn('<script>bad</script>', response.text)
                for name in ('start', 'end'):
                    expected = (form | changes)[name]
                    if isinstance(expected, list):
                        self.assertRegex(response.text, rf'name="{name}"[^>]*value=""')
                    elif expected == '<script>bad</script>':
                        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', response.text)
                    else:
                        self.assertIn(f'value="{expected}"', response.text)
            for name in ('start', 'end'):
                self.assertEqual(self._export_post({key: value for key, value in form.items() if key != name}).status_code, 400)
            query.assert_not_called()
        self.assertEqual(self.client.get('/export').status_code, 200)

    def test_export_empty_and_provider_errors_are_safe(self):
        self._login()
        form = self._export_form()
        with patch.object(life_service, 'list_expenses_in_range', return_value={'items': [], 'total': 999}):
            response = self._export_post(form)
        self.assertEqual(response.status_code, 200)
        self.assertIn('此日期範圍沒有可匯出的消費帳目。', response.text)
        self.assertNotIn('content-disposition', response.headers)
        self.assertNotIn('無法匯出', response.text)
        for error_type, status in ((ValueError, 400), (TypeError, 400), (routes.SQLiteError, 503)):
            with patch.object(life_service, 'list_expenses_in_range', side_effect=error_type('SECRET SQLite C:/private/data.db')):
                response = self._export_post(form)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn('content-disposition', response.headers)
            for forbidden in ('SECRET', 'SQLite', 'C:/private', self.user_id):
                self.assertNotIn(forbidden, response.text)
            self.assertIn('value="2026-09-01"', response.text)
            self.assertIn('value="2026-09-24"', response.text)
        life_service.add_expense(self.user_id, 1, '餐飲', '不能半份下載', '2026-09-01')
        with patch.object(routes, '_expense_csv', side_effect=ValueError('SECRET serialization')):
            response = self._export_post(form)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('content-disposition', response.headers)
        self.assertNotIn('SECRET', response.text)

    def test_export_over_previous_limits_is_complete_and_read_only(self):
        with sp.transaction() as conn:
            conn.executemany('INSERT INTO expenses(user_id,spent_on,cents,category,note,source) VALUES(?,?,?,?,?,?)',
                             ((self.user_id, '2026-08-31' if n % 2 else '2026-09-24', 29, '餐飲', '測試'+str(n),
                               ('manual', '固定', '訂閱', '分期')[n % 4]) for n in range(10001)))
        life_service.add_expense('999999999999999999', 999, '餐飲', '他人', '2026-09-01')
        removed = life_service.add_expense(self.user_id, 999, '餐飲', '撤銷', '2026-09-01')
        life_service.void_expense(self.user_id, removed)
        sp.add_recurring(self.user_id, '固定', '預測', 999, '餐飲', '2026-10')
        self._login()
        form = self._export_form(start='2026-08-31')
        before = self._snapshot()
        response = self._export_post(form)
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'), newline='')))
        items = life_service.list_expenses_in_range(self.user_id, form['start'], form['end'])['items']
        self.assertEqual(len(rows)-1, 10001)
        self.assertEqual([row[2] for row in rows[1:]], [item['note'] for item in items])
        self.assertEqual({item['source'] for item in items}, {'manual', '固定', '訂閱', '分期'})
        self.assertEqual(self._snapshot(), before)

    def test_export_keeps_history_snapshot_and_creates_no_files(self):
        source = sp.add_payment_source(self.user_id, '舊卡')
        life_service.add_expense(self.user_id, '.29', '餐飲', '歷史', '2026-09-01', source)
        sp.rename_payment_source(self.user_id, source, '新卡')
        sp.disable_payment_source(self.user_id, source)
        sp.set_category(self.user_id, '餐飲', False)
        self._login()
        form = self._export_form()
        before, files = self._snapshot(), set(Path(self.temp.name).iterdir())
        original_open, original_path_open = builtins.open, Path.open
        def no_write(file, mode='r', *args, **kwargs):
            if any(flag in mode for flag in 'wax+'):
                raise AssertionError('export may not write files')
            return original_open(file, mode, *args, **kwargs)
        def no_path_write(path, mode='r', *args, **kwargs):
            if any(flag in mode for flag in 'wax+'):
                raise AssertionError('export may not write files')
            return original_path_open(path, mode, *args, **kwargs)
        with (patch('builtins.open', side_effect=no_write), patch.object(Path, 'open', no_path_write),
              patch.object(tempfile, 'NamedTemporaryFile', side_effect=AssertionError('no temp file')),
              patch.object(tempfile, 'TemporaryFile', side_effect=AssertionError('no temp file')),
              patch.object(sp, 'sync_recurring', side_effect=AssertionError('no sync')),
              patch.object(life_service, 'get_payment_sources', side_effect=AssertionError('no initialization'))):
            self.assertEqual(self.client.get('/export').status_code, 200)
            response = self._export_post(form)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self._export_post(form | {'start': '2026-08-01', 'end': '2026-08-31'}).status_code, 200)
            self.assertEqual(self._export_post(form | {'start': 'bad'}).status_code, 400)
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'), newline='')))
        self.assertEqual(rows[1][2:], ['歷史', '餐飲', '舊卡'])
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(set(Path(self.temp.name).iterdir()), files)

    def test_csv_exact_amount_bom_and_csv_escaping(self):
        items = [dict(spent_on='2026-09-01', cents=cents, note='中文,項目"雙引號"\r\n下一行',
                      category='餐飲', payment_source_name='歷史,卡"名稱"', id=123, revision=456)
                 for cents in (100000, 10050, 10025, 29)]
        payload = routes._expense_csv(items)
        self.assertTrue(payload.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(payload.count(b'\xef\xbb\xbf'), 1)
        rows = list(csv.reader(io.StringIO(payload.decode('utf-8-sig'), newline='')))
        self.assertEqual(rows[0], ['日期', '金額', '消費項目', '分類', '付款方式'])
        self.assertEqual([row[1] for row in rows[1:]], ['1000', '100.5', '100.25', '0.29'])
        self.assertEqual(len(rows), 5)
        for row in rows[1:]:
            self.assertEqual(len(row), 5)
            self.assertEqual(row[0], '2026-09-01')
            self.assertEqual(row[2:], [items[0]['note'], '餐飲', items[0]['payment_source_name']])
        self.assertIn(b'\r\n', payload)

    def test_csv_formula_prefixes_preserve_original_and_numeric_fields(self):
        dangerous = ['=SUM(1,2)', '+1', '-1', '@x', '  =1', '\u3000 +1']
        dangerous += [prefix+suffix for prefix in ('\t', '\r', '\n', '\x00', '\x85', '\u0080', '\ufeff', '\u200b')
                      for suffix in ('@formula', '普通中文')]
        safe = ['', '   ', '中文,項目', '普通"引號"', '  普通中文', "'=SUM(1,2)", "'\t@already"]
        for text in dangerous + safe:
            with self.subTest(text=repr(text)):
                expected = "'"+text if text in dangerous else text
                self.assertEqual(routes._csv_safe_text(text), expected)
                item = dict(spent_on='2026-09-01', cents=100000, note=text, category=text, payment_source_name=text)
                before = dict(item)
                rows = list(csv.reader(io.StringIO(routes._expense_csv([item]).decode('utf-8-sig'), newline='')))
                self.assertEqual(rows[1], ['2026-09-01', '1000', expected, expected, expected])
                self.assertEqual(item, before)


class WebSettingsPageTests(_WebLedgerTestFixture, unittest.TestCase):
    def test_logged_out_settings_does_not_call_services_or_expose_data(self):
        targets = (
            "get_today",
            "get_month_summary",
            "get_categories",
            "get_payment_sources",
        )
        patches = [
            patch(
                f"web.routes.life_service.{name}",
                side_effect=AssertionError("service must not be called"),
            )
            for name in targets
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 403)
        self.assertIn("請先使用 Discord 登入", response.text)
        self.assertNotIn("本月預算", response.text)
        self.assertNotIn(self.user_id, response.text)

    def test_home_links_to_settings_and_page_has_five_sections(self):
        self._login()

        home = self.client.get("/")
        response = self.client.get("/settings")

        self.assertIn('href="/settings"', home.text)
        self.assertIn("設定", home.text)
        self.assertEqual(response.status_code, 200)
        self.assertIn("2026 年 09 月", response.text)
        self.assertRegex(response.text, r'<details[^>]*data-settings-section="budget"[^>]* open>')
        for section in ("categories", "payments", "fixed", "export"):
            tag = re.search(
                rf'<details[^>]*data-settings-section="{section}"[^>]*>',
                response.text,
            ).group()
            self.assertNotIn(" open", tag)
        for heading in ("預算設定", "分類管理", "付款方式管理"):
            self.assertIn(f"<summary>{heading}</summary>", response.text)
        self.assertNotRegex(response.text, r'<details[^>]*\sname=')
        css = (Path(__file__).parents[1] / "web/static/web.css").read_text(encoding="utf-8")
        self.assertIn("min-height: 44px", css)
        self.assertIn("cursor: pointer", css)
        self.assertIn(".settings-section > summary:focus-visible", css)
        self.assertIn('content: "→"', css)
        self.assertIn('content: "↓"', css)
        invalid_section = self.client.get("/settings?section=other")
        self.assertRegex(invalid_section.text, r'<details[^>]*data-settings-section="budget"[^>]* open>')
        headings = [
            response.text.index("預算設定"),
            response.text.index("分類管理"),
            response.text.index("付款方式管理"),
        ]
        self.assertEqual(headings, sorted(headings))

    def test_budget_section_reopens_after_success_and_validation_error(self):
        self._login()
        token = self._csrf_token()
        response = self.client.post(
            "/settings/budgets/set",
            data={"csrf_token": token, "category": "總額", "amount": "1000"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings?saved=1&section=budget")
        saved = self.client.get(response.headers["location"])
        self.assertRegex(saved.text, r'<details[^>]*data-settings-section="budget"[^>]* open>')
        response = self.client.post(
            "/settings/budgets/set",
            data={"csrf_token": token, "category": "總額", "amount": "1000.5"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertRegex(response.text, r'<details[^>]*data-settings-section="budget"[^>]* open>')
        self.assertIn("無法更新設定，請檢查後重試。", response.text)
        self.assertIn('id="total-budget-amount"', response.text)
        self.assertIn('value="1000.5"', response.text)

    def test_category_section_reopens_after_success_and_validation_error(self):
        self._login()
        token = self._csrf_token()
        response = self.client.post(
            "/settings/categories/add",
            data={"csrf_token": token, "name": "寵物"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings?saved=1&section=categories")
        saved = self.client.get(response.headers["location"])
        self.assertRegex(saved.text, r'<details[^>]*data-settings-section="categories"[^>]* open>')
        response = self.client.post(
            "/settings/categories/add",
            data={"csrf_token": token, "name": "總額"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertRegex(response.text, r'<details[^>]*data-settings-section="categories"[^>]* open>')
        self.assertIn("無法更新設定，請檢查後重試。", response.text)
        self.assertIn('id="new-category"', response.text)
        self.assertIn('value="總額"', response.text)

    def test_payment_section_reopens_after_success_and_validation_error(self):
        self._login()
        token = self._csrf_token()
        response = self.client.post(
            "/settings/payment-sources/add",
            data={"csrf_token": token, "name": "個人卡"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings?saved=1&section=payments")
        saved = self.client.get(response.headers["location"])
        self.assertRegex(saved.text, r'<details[^>]*data-settings-section="payments"[^>]* open>')
        response = self.client.post(
            "/settings/payment-sources/add",
            data={"csrf_token": token, "name": "現金"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertRegex(response.text, r'<details[^>]*data-settings-section="payments"[^>]* open>')
        self.assertIn("無法更新設定，請檢查後重試。", response.text)
        self.assertIn('id="new-payment-source"', response.text)
        self.assertIn('value="現金"', response.text)

    def test_settings_only_shows_the_session_users_data(self):
        sp.set_category(self.user_id, "本人分類", True)
        own_source = sp.add_payment_source(self.user_id, "本人卡")
        sp.set_budget(self.user_id, "2026-09", "本人分類", 300)
        sp.set_budget(self.user_id, "2026-09", "總額", 1000)
        other_id = "999999999999999999"
        sp.set_category(other_id, "他人分類", True)
        other_source = sp.add_payment_source(other_id, "他人卡")
        sp.set_budget(other_id, "2026-09", "他人分類", 900)
        self._login()

        response = self.client.get(
            f"/settings?user_id={other_id}&month=2025-01&unknown=ignored"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("本人分類", response.text)
        self.assertIn("本人卡", response.text)
        self.assertIn(f'value="{own_source}"', response.text)
        self.assertIn(">300 元</span>", response.text)
        self.assertIn("<p>1000 元</p>", response.text)
        total_field = response.text.split(
            'id="total-budget-amount"', 1
        )[1].split(">", 1)[0]
        self.assertIn('value="1000"', total_field)
        self.assertNotIn("元", total_field)
        category_row = response.text.split(
            'data-budget-category="本人分類"', 1
        )[1].split("</li>", 1)[0]
        category_input = category_row.split(
            'action="/settings/budgets/set"', 1
        )[1].split("</form>", 1)[0]
        self.assertIn('value="300"', category_input)
        self.assertNotIn("元", category_input)
        self.assertNotIn("300.00", response.text)
        self.assertNotIn("他人分類", response.text)
        self.assertNotIn("他人卡", response.text)
        self.assertNotIn(f'value="{other_source}"', response.text)
        self.assertNotIn("900.00", response.text)
        self.assertNotIn(other_id, response.text)

    def test_settings_marks_inactive_budget_and_reserved_sources(self):
        sp.set_category(self.user_id, "停用分類", True)
        sp.set_budget(self.user_id, "2026-09", "停用分類", 250)
        sp.set_category(self.user_id, "停用分類", False)
        self._login()

        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("停用分類", response.text)
        self.assertIn("已停用", response.text)
        self.assertIn(">250 元</span>", response.text)
        self.assertNotIn("250.00", response.text)
        self.assertIn("設為分類預算加總", response.text)
        budget_row = response.text.split(
            'data-budget-category="停用分類"', 1
        )[1].split("</li>", 1)[0]
        self.assertNotIn("/settings/budgets/set", budget_row)
        self.assertIn("/settings/budgets/clear", budget_row)
        for reserved in ("未指定", "現金"):
            row = response.text.split(f'data-source-name="{reserved}"', 1)[1].split(
                "</li>", 1
            )[0]
            self.assertNotIn("payment-sources/rename", row)
            self.assertNotIn("payment-sources/disable", row)

    def test_settings_category_action_uses更改_label(self):
        self._login()
        response = self.client.get("/settings")
        category_section = response.text.split('data-settings-section="categories"', 1)[1].split(
            'data-settings-section="payments"', 1
        )[0]
        self.assertIn(">更改</button>", category_section)
        self.assertNotIn("改名", category_section)

    def test_budget_amount_inputs_and_server_require_integers(self):
        self._login()
        token = self._csrf_token()
        page = self.client.get("/settings")
        self.assertEqual(page.status_code, 200)
        for control in ("total-budget-amount", "budget-1"):
            field = page.text.split(f'id="{control}"', 1)[1].split(">", 1)[0]
            self.assertIn('type="number"', field)
            self.assertIn('inputmode="numeric"', field)
            self.assertIn('step="1"', field)
            self.assertIn('min="1"', field)

        for amount in ("1000.5", "1000.00", "-1", "0", "abc"):
            with self.subTest(amount=amount):
                response = self.client.post(
                    "/settings/budgets/set",
                    data={"csrf_token": token, "category": "總額", "amount": amount},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(sp.rows("SELECT * FROM budgets"), [])

    def test_settings_empty_states_are_fixed(self):
        self._login()
        summary = {
            "budgets": [],
            "categories": {},
            "start": "2026-09-01",
            "end": "2026-09-24",
            "record_count": 0,
            "total": 0,
            "fixed": 0,
            "coverage": "",
            "has_records": False,
        }
        with (
            patch("web.routes.life_service.get_month_summary", return_value=summary),
            patch("web.routes.life_service.get_categories", return_value=[]),
            patch("web.routes.life_service.get_payment_sources", return_value=[]),
        ):
            response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("本月尚未設定預算。", response.text)
        self.assertIn("目前沒有啟用分類。", response.text)
        self.assertIn("目前沒有可管理的付款方式。", response.text)

    def test_budget_posts_support_floor_clear_and_category_sum(self):
        self._login()
        token = self._csrf_token()

        response = self.client.post(
            "/settings/budgets/set",
            data={"csrf_token": token, "category": "餐飲", "amount": "300"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings?saved=1&section=budget")
        self.assertEqual(
            sp.rows("SELECT category,cents FROM budgets WHERE user_id=?", (self.user_id,)),
            [{"category": "餐飲", "cents": 30000}],
        )

        response = self.client.post(
            "/settings/budgets/set",
            data={"csrf_token": token, "category": "總額", "amount": "299"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            sp.rows("SELECT category,cents FROM budgets WHERE user_id=?", (self.user_id,)),
            [{"category": "餐飲", "cents": 30000}],
        )

        for category, amount in (("總額", "500"), ("交通", "200")):
            response = self.client.post(
                "/settings/budgets/set",
                data={
                    "csrf_token": token,
                    "category": category,
                    "amount": amount,
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)

        response = self.client.post(
            "/settings/budgets/clear",
            data={"csrf_token": token, "category": "總額"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            {row["category"] for row in sp.rows(
                "SELECT category FROM budgets WHERE user_id=?",
                (self.user_id,),
            )},
            {"餐飲", "交通"},
        )

        response = self.client.post(
            "/settings/budgets/use-category-sum",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            sp.rows(
                "SELECT cents FROM budgets WHERE user_id=? AND category='總額'",
                (self.user_id,),
            ),
            [{"cents": 50000}],
        )
        saved = self.client.get(response.headers["location"])
        self.assertIn("設定已更新", saved.text)
        self.client.get("/settings?saved=1")
        self.assertEqual(len(sp.rows("SELECT * FROM budgets")), 3)

    def test_budget_posts_ignore_external_owner_and_month(self):
        other_id = "999999999999999999"
        self._login()
        token = self._csrf_token()

        response = self.client.post(
            f"/settings/budgets/set?user_id={other_id}&month=2025-01",
            data={
                "csrf_token": token,
                "category": "餐飲",
                "amount": "100",
                "user_id": other_id,
                "month": "2025-01",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            sp.rows("SELECT user_id,month,cents FROM budgets"),
            [{"user_id": self.user_id, "month": "2026-09", "cents": 10000}],
        )

    def test_budget_posts_reject_invalid_csrf_fields_and_errors_safely(self):
        self._login()
        token = self._csrf_token()
        invalid_requests = (
            ("/settings/budgets/set", {"category": "餐飲", "amount": "1"}),
            (
                "/settings/budgets/clear",
                {"csrf_token": "wrong", "category": "餐飲"},
            ),
            (
                "/settings/budgets/use-category-sum",
                f"csrf_token={token}&csrf_token={token}",
            ),
        )
        for path, submitted in invalid_requests:
            with self.subTest(path=path):
                kwargs = (
                    {"data": submitted}
                    if isinstance(submitted, dict)
                    else {
                        "content": submitted.encode("utf-8"),
                        "headers": {"content-type": "application/x-www-form-urlencoded"},
                    }
                )
                response = self.client.post(path, **kwargs)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(sp.rows("SELECT * FROM budgets"), [])
                self.assertEqual(self.client.get("/settings").status_code, 200)

        invalid_fields = (
            (
                "/settings/budgets/set",
                f"csrf_token={token}&category=餐飲&amount=1&amount=2",
            ),
            (
                "/settings/budgets/set",
                f"csrf_token={token}&category=%3Cscript%3E&amount=bad",
            ),
            (
                "/settings/budgets/use-category-sum",
                f"csrf_token={token}",
            ),
        )
        for path, body in invalid_fields:
            with self.subTest(path=path, body=body):
                response = self.client.post(
                    path,
                    content=body.encode("utf-8"),
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("無法更新設定，請檢查後重試。", response.text)
                for forbidden in (
                    "<script>",
                    "SQLite",
                    "Traceback",
                    ".db",
                    "Token",
                    self.user_id,
                ):
                    self.assertNotIn(forbidden, response.text)
                self.assertEqual(sp.rows("SELECT * FROM budgets"), [])

        response = self.client.post(
            "/settings/budgets/set",
            content=b"csrf_token=x&category=x&amount=1",
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(response.status_code, 403)

    def test_category_posts_add_reactivate_rename_disable_and_update_home(self):
        self._login()
        token = self._csrf_token()

        response = self.client.post(
            "/settings/categories/add",
            data={"csrf_token": token, "name": "寵物"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn('<option value="寵物">寵物</option>', self.client.get("/").text)

        response = self.client.post(
            "/settings/categories/disable",
            data={"csrf_token": token, "name": "寵物"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn('value="寵物"', self.client.get("/").text)
        settings = self.client.get("/settings")
        category_row = settings.text.split('data-category-name="寵物"', 1)[1].split(
            "</li>", 1
        )[0]
        self.assertIn("已停用", category_row)

        response = self.client.post(
            "/settings/categories/add",
            data={"csrf_token": token, "name": "寵物"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        response = self.client.post(
            "/settings/categories/rename?user_id=999999999999999999",
            data={
                "csrf_token": token,
                "old_name": "寵物",
                "new_name": "毛孩",
                "user_id": "999999999999999999",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("寵物", life_service.get_categories(self.user_id, True))
        self.assertIn("毛孩", life_service.get_categories(self.user_id))
        home = self.client.get("/")
        self.assertIn('<option value="毛孩">毛孩</option>', home.text)
        self.assertNotIn('value="寵物"', home.text)

    def test_category_posts_reject_reserved_duplicate_and_other_users_data(self):
        other_id = "999999999999999999"
        sp.set_category(self.user_id, "本人分類", True)
        sp.set_category(other_id, "他人分類", True)
        self._login()
        token = self._csrf_token()

        invalid = (
            (
                "/settings/categories/add",
                {"csrf_token": token, "name": "總額"},
            ),
            (
                "/settings/categories/rename",
                {
                    "csrf_token": token,
                    "old_name": "本人分類",
                    "new_name": "餐飲",
                },
            ),
            (
                "/settings/categories/disable",
                {"csrf_token": token, "name": "他人分類"},
            ),
        )
        for path, data in invalid:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("無法更新設定，請檢查後重試。", response.text)
        self.assertIn("本人分類", life_service.get_categories(self.user_id))
        self.assertIn("他人分類", life_service.get_categories(other_id))

    def test_payment_source_posts_preserve_history_and_enforce_owner_and_reserved(self):
        other_id = "999999999999999999"
        other_source = sp.add_payment_source(other_id, "他人卡")
        self._login()
        token = self._csrf_token()

        response = self.client.post(
            "/settings/payment-sources/add",
            data={"csrf_token": token, "name": "舊卡"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        source = next(
            row for row in life_service.get_payment_sources(self.user_id)
            if row["name"] == "舊卡"
        )
        expense = life_service.add_expense(
            self.user_id,
            80,
            "餐飲",
            "早餐",
            "2026-09-24",
            source["id"],
        )

        response = self.client.post(
            f"/settings/payment-sources/rename?user_id={other_id}",
            data={
                "csrf_token": token,
                "payment_source_id": str(source["id"]),
                "name": "新卡",
                "user_id": other_id,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            life_service.get_expense(self.user_id, expense)["payment_source_name"],
            "舊卡",
        )
        self.assertIn(
            "新卡",
            [row["name"] for row in life_service.get_payment_sources(self.user_id)],
        )
        self.assertEqual(
            next(row for row in life_service.get_payment_sources(other_id) if row["id"] == other_source)["name"],
            "他人卡",
        )

        response = self.client.post(
            "/settings/payment-sources/disable",
            data={"csrf_token": token, "payment_source_id": str(source["id"])},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("新卡", self.client.get("/").text)
        self.assertIn("新卡", self.client.get("/settings").text)

        reserved = next(
            row for row in life_service.get_payment_sources(self.user_id)
            if row["name"] == "現金"
        )
        for path, data in (
            (
                "/settings/payment-sources/rename",
                {
                    "csrf_token": token,
                    "payment_source_id": str(reserved["id"]),
                    "name": "不能改",
                },
            ),
            (
                "/settings/payment-sources/disable",
                {
                    "csrf_token": token,
                    "payment_source_id": str(reserved["id"]),
                },
            ),
            (
                "/settings/payment-sources/disable",
                {"csrf_token": token, "payment_source_id": str(other_source)},
            ),
        ):
            with self.subTest(path=path, data=data):
                self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.assertIn(
            "現金",
            [row["name"] for row in life_service.get_payment_sources(self.user_id)],
        )
        self.assertIn(
            other_source,
            [row["id"] for row in life_service.get_payment_sources(other_id)],
        )

    def test_category_and_payment_posts_share_csrf_field_and_safe_error_rules(self):
        self._login()
        token = self._csrf_token()
        cases = (
            ("/settings/categories/add", {"name": "寵物"}),
            (
                "/settings/categories/rename",
                {"old_name": "餐飲", "new_name": "外食"},
            ),
            ("/settings/categories/disable", {"name": "餐飲"}),
            ("/settings/payment-sources/add", {"name": "卡"}),
            (
                "/settings/payment-sources/rename",
                {"payment_source_id": "1", "name": "新卡"},
            ),
            ("/settings/payment-sources/disable", {"payment_source_id": "1"}),
        )
        for path, fields in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=fields | {"csrf_token": "wrong"})
                self.assertEqual(response.status_code, 403)
                section = "categories" if "/categories/" in path else "payments"
                self.assertRegex(
                    response.text,
                    rf'<details[^>]*data-settings-section="{section}"[^>]* open>',
                )
                self.assertIn("設定驗證失敗", response.text)
                self.assertEqual(self.client.get("/settings").status_code, 200)

        duplicate = f"csrf_token={token}&name=one&name=two"
        response = self.client.post(
            "/settings/categories/add",
            content=duplicate.encode("utf-8"),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 400)

        with patch(
            "web.routes.life_service.rename_category",
            side_effect=ValueError("private database path C:/secret/data.db"),
        ):
            response = self.client.post(
                "/settings/categories/rename",
                data={
                    "csrf_token": token,
                    "old_name": "餐飲",
                    "new_name": "外食",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("無法更新設定，請檢查後重試。", response.text)
        self.assertNotIn("private database", response.text)
        self.assertNotIn("C:/secret", response.text)

        response = self.client.post(
            "/settings/categories/add",
            data={"csrf_token": token, "name": "<script>"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.headers["location"])
        self.assertNotIn("<script>", page.text)
        self.assertIn("&lt;script&gt;", page.text)


class WebOAuthTests(_WebOAuthTests, unittest.TestCase):
    def test_callback_denial_clears_temporary_state(self):
        state = self._start_login()

        denied = self.client.get(
            f"/auth/discord/callback?error=access_denied&state={state}"
        )

        self.assertEqual(denied.status_code, 400)
        self.assertIn("登入未完成", denied.text)
        exchange = AsyncMock(return_value={"access_token": "not-used"})
        with patch.object(self.app.state.oauth.discord, "fetch_access_token", exchange):
            retried = self.client.get(
                f"/auth/discord/callback?code=test-code&state={state}"
            )
        self.assertEqual(retried.status_code, 400)
        exchange.assert_not_awaited()
        self.assertIn("使用 Discord 登入", self.client.get("/").text)

    def test_provider_failure_is_safe_and_does_not_log_in(self):
        state = self._start_login()
        provider_error = OAuthError(
            error="server_error",
            description="provider-secret-detail",
        )
        with patch.object(
            self.app.state.oauth.discord,
            "fetch_access_token",
            new=AsyncMock(side_effect=provider_error),
        ):
            response = self.client.get(
                f"/auth/discord/callback?code=test-code&state={state}"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("登入未完成", response.text)
        self.assertNotIn("provider-secret-detail", response.text)
        self.assertNotIn(self.settings.discord_client_secret, response.text)
        self.assertIn("使用 Discord 登入", self.client.get("/").text)

    def test_mocked_callback_creates_protected_session_and_cookie(self):
        response = self._complete_mocked_login()

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("max-age=604800", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertNotIn("; secure", cookie)

        home = self.client.get("/")
        self.assertIn("已登入", home.text)
        self.assertIn("登出", home.text)
        self.assertNotIn(self.provider_user_id, home.text)
        self.assertNotIn("Discord ID", home.text)
        self.assertNotIn("帳務", home.text)

    def test_query_user_id_cannot_rescue_invalid_provider_identity(self):
        for provider_id in ("", "not-a-number", "0", str(2**64)):
            with self.subTest(provider_id=provider_id):
                client = TestClient(create_app(self.settings))
                response = self._complete_mocked_login(client, provider_id)
                self.assertEqual(response.status_code, 400)
                self.assertIn("使用 Discord 登入", client.get("/").text)

    def test_query_user_id_alone_cannot_log_in(self):
        response = self.client.get("/?user_id=999")

        self.assertEqual(response.status_code, 200)
        self.assertIn("使用 Discord 登入", response.text)
        self.assertNotIn("已登入", response.text)

    def test_logout_rejects_missing_wrong_and_duplicate_csrf_and_preserves_session(self):
        self._complete_mocked_login()
        home = self.client.get("/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            home.text,
        ).group(1)

        invalid_requests = (
            {},
            {"data": {"csrf_token": "wrong-token"}},
            {
                "content": f"csrf_token={token}&csrf_token={token}",
                "headers": {"content-type": "application/x-www-form-urlencoded"},
            },
        )
        for request_kwargs in invalid_requests:
            with self.subTest(request_kwargs=request_kwargs):
                response = self.client.post(
                    "/logout",
                    follow_redirects=False,
                    **request_kwargs,
                )
                self.assertEqual(response.status_code, 403)
                self.assertIn("已登入", self.client.get("/").text)

    def test_logout_with_csrf_clears_session(self):
        self._complete_mocked_login()
        home = self.client.get("/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            home.text,
        ).group(1)

        response = self.client.post(
            "/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertIn("使用 Discord 登入", self.client.get("/").text)




class WebFixedRecurringTests(_WebLedgerTestFixture, unittest.TestCase):
    def snapshot(self):
        return {row['name']: sp.rows(f"SELECT * FROM {row['name']} ORDER BY rowid")
                for row in sp.rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def form(self, **changes):
        response = self.client.get('/fixed-expenses/new')
        self.assertEqual(response.status_code, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        values = dict(csrf_token=token, name='房租', amount='100.29', category='居住', start_month='2026-09', due_day='28')
        values.update(changes)
        return values

    def post(self, path, values):
        return self.client.post(path, content=urlencode(values, doseq=True),
                                headers={'content-type': 'application/x-www-form-urlencoded'}, follow_redirects=False)

    def key(self):
        return sp.add_recurring(self.user_id, '固定', '原固定', '10.50', '居住', '2026-09', due_day=28)

    def test_settings_entry_and_single_form_are_read_only(self):
        self._login()
        self._csrf_token()
        settings = self.client.get('/settings').text
        section = re.search(r'<details[^>]*data-settings-section="fixed"[^>]*>(.*?)</details>', settings, re.S)
        self.assertIsNotNone(section)
        self.assertNotRegex(section.group(0).split('>')[0], r'\bopen\b')
        self.assertIn('固定支出', section.group(1))
        self.assertIn('href="/fixed-expenses"', section.group(1))
        before = self.snapshot()
        page = self.client.get('/fixed-expenses/new?user_id=other&start_month=2026-01')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers['cache-control'], 'no-store')
        self.assertEqual(len(re.findall(r'<form\b', page.text)), 1)
        for name in ('name', 'amount', 'category', 'start_month', 'due_day', 'csrf_token'):
            self.assertIn(f'name="{name}"', page.text)
        self.assertNotIn('name="periods"', page.text)
        self.assertIn('2026-09', page.text); self.assertIn('2026-10', page.text)
        self.assertNotIn('value="2026-01"', page.text)
        self.assertIn('非銀行扣款', page.text)
        self.assertEqual(self.client.get('/fixed-expenses').status_code, 200)
        # Isolate new GET routes from existing settings payment-default initialization.
        self.assertEqual(self.snapshot(), before)

    def test_create_edit_pending_and_stop_via_prg_with_session_identity(self):
        self._login()
        response = self.post('/fixed-expenses/new', self.form(user_id='other', kind='訂閱', periods='10', next='https://example.com'))
        self.assertEqual(response.status_code, 303)
        key = sp.rows('SELECT * FROM recurring_expenses')[0]['id']
        stored = sp.rows('SELECT * FROM recurring_expenses')[0]
        self.assertEqual((stored['user_id'], stored['kind'], stored['periods']), (self.user_id, '固定', 0))
        self.assertEqual(sp.rows('SELECT * FROM expenses'), [])
        page = self.client.get(f'/fixed-expenses/{key}/edit')
        self.assertIn('value="100.29"', page.text)
        self.assertIn('readonly', page.text)
        values = self.form(name='新房租', amount='200.5', category='交通', due_day='5', expected_revision='0', start_month='2026-10')
        response = self.post(f'/fixed-expenses/{key}/edit', values)
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.headers['location'])
        self.assertIn('2026 年 10 月', page.text)
        listing = self.client.get('/fixed-expenses').text
        self.assertIn('房租', listing); self.assertIn('新房租', listing)
        self.assertIn('已安排', listing)
        self.assertEqual(sp.rows('SELECT * FROM recurring_expenses')[0]['start_month'], '2026-09')
        before = self.snapshot()
        confirm = self.client.get(f'/fixed-expenses/{key}/stop?expected_revision=1')
        self.assertEqual(confirm.status_code, 200)
        self.assertIn('已到期', confirm.text); self.assertIn('未到期', confirm.text)
        self.assertEqual(self.snapshot(), before)
        response = self.post(f'/fixed-expenses/{key}/stop', dict(csrf_token=values['csrf_token'], expected_revision='1'))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(sp.rows('SELECT * FROM expenses'), [])
        listing = self.client.get('/fixed-expenses').text
        self.assertIn('已停用', listing)
        self.assertIn('不再執行', listing)
        self.assertNotIn('重新啟用', listing)

    def test_foreign_missing_and_other_origins_return_same_safe_response(self):
        self._login()
        keys = [sp.add_recurring('other', '固定', 'SECRET_OTHER', 1, '居住', '2026-09')]
        keys += [sp.add_recurring(self.user_id, kind, 'SECRET_SOURCE', 1, '居住', '2026-09', periods=2 if kind == '分期' else 0)
                 for kind in ('訂閱', '分期')]
        keys.append(999999)
        values = self.form(expected_revision='0')
        before = self.snapshot()
        replies = []
        for key in keys:
            for path in (f'/fixed-expenses/{key}/edit', f'/fixed-expenses/{key}/stop?expected_revision=0'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                replies.append(response.text)
            for action in ('edit', 'stop'):
                response = self.post(f'/fixed-expenses/{key}/{action}', values)
                self.assertEqual(response.status_code, 404)
                replies.append(response.text)
        self.assertEqual(len(set(replies)), 1)
        self.assertNotIn('SECRET', replies[0])
        self.assertEqual(self.snapshot(), before)

    def test_auth_and_csrf_rejections_never_query_ledger(self):
        with patch.object(db, 'get_conn', side_effect=AssertionError('no ledger query')), \
                patch.object(sp, 'get_conn', side_effect=AssertionError('no ledger query')):
            for path in ('/fixed-expenses', '/fixed-expenses/new', '/fixed-expenses/1/edit', '/fixed-expenses/1/stop?expected_revision=0'):
                self.assertEqual(self.client.get(path).status_code, 403)
            for path in ('/fixed-expenses/new', '/fixed-expenses/1/edit', '/fixed-expenses/1/stop', '/fixed-expenses/sync'):
                self.assertEqual(self.post(path, {}).status_code, 403)
        self._login()
        values = self.form()
        for token in (None, 'wrong', ['x', 'y'], '非ASCII'):
            invalid = {**values}; invalid.pop('csrf_token')
            if token is not None: invalid['csrf_token'] = token
            # spending imported get_conn by name; forbid both references.
            with patch.object(sp, 'get_conn', side_effect=AssertionError('no ledger query')):
                for path in ('/fixed-expenses/new', '/fixed-expenses/1/edit', '/fixed-expenses/1/stop', '/fixed-expenses/sync'):
                    self.assertEqual(self.post(path, invalid).status_code, 403)
        with patch.object(sp, 'get_conn', side_effect=AssertionError('no ledger query')):
            self.assertEqual(self.client.post('/fixed-expenses/sync', json=values).status_code, 403)
        self.assertEqual(self.client.get('/fixed-expenses').status_code, 200)

    def test_invalid_inputs_preserve_safe_drafts_and_do_not_change_data(self):
        self._login()
        valid = self.form()
        for changes in ({'amount': 'NaN'}, {'amount': '1.001'}, {'amount': '-1'}, {'due_day': '0'},
                        {'due_day': '32'}, {'due_day': '1.5'}, {'category': '<script>bad</script>'},
                        {'start_month': '2026-08'}, {'start_month': '2026-11'}, {'start_month': '2026-9'},
                        {'name': ''}, {'name': 'x' * 101}, {'amount': ['1', '2']}):
            values = {**valid, **changes}; before = self.snapshot()
            response = self.post('/fixed-expenses/new', values)
            self.assertEqual(response.status_code, 400, changes)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn('<script>', response.text)
            self.assertEqual(self.snapshot(), before)
        response = self.post('/fixed-expenses/new', {**valid, 'name': '<img src=x>', 'amount': 'bad'})
        self.assertIn('&lt;img src=x&gt;', response.text)
        self.assertIn('value="bad"', response.text)
        with patch.object(life_service, 'add_fixed_recurring', side_effect=routes.SQLiteError('SECRET_PATH')):
            response = self.post('/fixed-expenses/new', valid)
        self.assertEqual(response.status_code, 503); self.assertNotIn('SECRET_PATH', response.text)

    def test_stale_edit_and_stop_do_not_overwrite_and_keep_old_draft(self):
        self._login(); key = self.key()
        values = self.form(expected_revision='0')
        life_service.update_fixed_recurring(self.user_id, key, '較新的設定', 20, '交通', 15, 0)
        before = self.snapshot()
        response = self.post(f'/fixed-expenses/{key}/edit', {**values, 'name': '舊表單草稿'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('舊表單草稿', response.text)
        self.assertIn('重新載入', response.text)
        self.assertNotIn('>儲存更改<', response.text)
        self.assertEqual(self.client.get(f'/fixed-expenses/{key}/stop?expected_revision=0').status_code, 409)
        self.assertEqual(self.post(f'/fixed-expenses/{key}/stop', values).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_fixed_sync_is_post_only_and_counts_consistently_everywhere(self):
        self._login()
        values = self.form(due_day='20')
        self.post('/fixed-expenses/new', values)
        for kind in ('訂閱', '分期'):
            sp.add_recurring(self.user_id, kind, '不由固定按鈕補記', 1, '居住', '2026-09', 2 if kind == '分期' else 0)
        self._csrf_token()
        before = self.snapshot()
        self.client.get('/fixed-expenses')
        self.assertEqual(self.snapshot(), before)
        self.client.get('/calendar?month=2026-09'); self.client.get('/')
        # Existing home initializes payment defaults; neither GET may post consumption.
        after = self.snapshot()
        for table in ('expenses', 'expense_actions', 'recurring_expenses', 'recurring_expense_versions', 'spending_notices'):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(self.client.get('/fixed-expenses/sync').status_code, 405)
        response = self.post('/fixed-expenses/sync?user_id=other&as_of=2099-01-01', {**values, 'user_id': 'other', 'as_of': '2099-01-01'})
        self.assertEqual(response.status_code, 303)
        self.assertEqual([(r['source'], r['spent_on'], r['cents']) for r in sp.rows('SELECT * FROM expenses')], [('固定', '2026-09-20', 10029)])
        self.assertEqual(life_service.get_month_summary(self.user_id, '2026-09')['total_cents'], 10029)
        calendar = life_service.get_calendar_days(self.user_id, '2026-09')
        self.assertEqual(next(r for r in calendar if r['date'] == '2026-09-20')['cents'], 10029)
        search = self.client.get('/search?start=2026-09-01&end=2026-09-24')
        self.assertIn('房租', search.text)
        exported = self.post('/export/csv', dict(csrf_token=values['csrf_token'], start='2026-09-01', end='2026-09-24'))
        records = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
        self.assertEqual(records[1], ['2026-09-20', '100.29', '房租', '居住', '未指定'])
        self.assertEqual(self.post('/fixed-expenses/sync', values).status_code, 303)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')), 1)

    def test_inactive_current_and_pending_categories_can_only_be_retained(self):
        self._login(); key = self.key()
        sp.set_category(self.user_id, '居住', False)
        sp.set_category(self.user_id, '交通', False)
        page = self.client.get(f'/fixed-expenses/{key}/edit')
        self.assertIn('已停用，僅可保留', page.text)
        values = self.form(expected_revision='0', category='居住')
        self.assertNotIn('value="居住"', self.client.get('/fixed-expenses/new').text)
        self.assertEqual(self.post(f'/fixed-expenses/{key}/edit', values).status_code, 303)
        values['expected_revision'] = '1'; values['category'] = '交通'
        self.assertEqual(self.post(f'/fixed-expenses/{key}/edit', values).status_code, 400)
        self.assertNotIn('居住', life_service.get_categories(self.user_id))

    def test_failed_stop_does_not_leave_catchup_or_partial_rule_state(self):
        self._login(); key = sp.add_recurring(self.user_id, '固定', '到期項目', 1, '居住', '2026-09', due_day=20)
        values = self.form(expected_revision='0')
        before = self.snapshot()
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_stop BEFORE UPDATE OF active ON recurring_expenses BEGIN SELECT RAISE(ABORT,'SECRET'); END")
        response = self.post(f'/fixed-expenses/{key}/stop', values)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SECRET', response.text)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
