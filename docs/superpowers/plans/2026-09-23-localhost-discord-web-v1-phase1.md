# Localhost Discord Web v1 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立只監聽 `127.0.0.1` 的 FastAPI／Jinja2 Web 骨架，以 Discord OAuth `identify` 登入保護首頁 placeholder，提供 7 天 session 與具 CSRF 防護的登出。

**Architecture:** `web/settings.py` 只處理 Web 環境設定；`web/app.py` 建立 FastAPI、SessionMiddleware、模板與靜態檔；`web/auth.py` 封裝 Authlib Discord OAuth、session、CSRF 與安全錯誤；`web/routes.py` 只組合 HTML 路由。這一階段不 import 或呼叫 `db.py`、`spending.py`、`life_ledger_service.py`，也不初始化 SQLite。

**Tech Stack:** Python 3.12、FastAPI、Starlette SessionMiddleware、Jinja2、Authlib、itsdangerous、python-dotenv、Uvicorn、unittest、FastAPI TestClient／httpx。

**Spec:** `docs/superpowers/specs/2026-09-23-localhost-discord-web-v1-design.md`

## Global Constraints

- 僅實作登入骨架與受保護首頁 placeholder；不實作記帳、搜尋、月曆、預算、分類、付款方式、JSON API 或資料庫讀寫。
- 只使用伺服器產生 HTML 與最小 CSS；不加入 React、ORM、Alembic、Repository、依賴注入容器或微服務。
- Discord OAuth 使用 Authlib authorization-code flow，scope 僅 `identify`；state 由 Authlib 與 signed session 驗證。
- OAuth 進行期間允許 Authlib 在 signed session 暫存 state 等協定資料；callback 成功或失敗後必須清除這些暫存值。登入完成後 session 只保存 Discord user ID 與 CSRF token，不保存 access token、refresh token、email 或 profile。
- session cookie 使用 `HttpOnly`、`SameSite=Lax`、`Max-Age=604800`，localhost HTTP 使用 `Secure=False`。
- `WEB_SESSION_SECRET` 空白、短於 32 字元或為常見範例值時拒絕啟動；正式環境同時要求 Discord Client ID／Secret 與合法 callback URI。
- callback URI 只允許 `http://127.0.0.1:.../auth/discord/callback` 或 `http://localhost:.../auth/discord/callback`，不得含 credentials、query 或 fragment。
- 所有 POST 驗證 CSRF；不接受 `next` 或任何外部 redirect 參數；任何 query／form `user_id` 都不得影響登入身分。
- Web 模組不得 import `config.py`，不得讀取 `token.txt`，不得 import／呼叫 `db.py`、`spending.py` 或 `life_ledger_service.py`。
- 依使用者要求，本輪不 commit、不 push；每項任務以 `git diff --check` 與狀態檢查取代 commit。

## Review Focus

- SessionMiddleware 的 `max_age` 必須精確設為 `604800`；不另建 `expires_at` 或第二套過期判斷，無登入 Cookie 時 `GET /` 顯示登入頁。
- 惡意 callback `state`：必須在換 token 前由 Authlib session 流程拒絕，且不建立 session。
- Provider 回傳非數字、空白或超出 Discord snowflake 範圍的 ID：必須顯示安全錯誤，不保存 session。
- 登出 CSRF 值缺少、重複或不符：必須回 403 且保留原 session。
- query／form 注入 `user_id`、`next` 或錯誤 redirect：不得改變 session 身分或造成 open redirect。

---

### Task 1: Resolve and pin Web dependencies, then validate settings

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Modify: `.env.example`
- Create: `web/__init__.py`
- Create: `web/settings.py`
- Create: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: process environment and optional project-root `.env` through python-dotenv.
- Produces: immutable `WebSettings(session_secret, discord_client_id, discord_client_secret, discord_redirect_uri)` and `load_web_settings() -> WebSettings`.

- [ ] **Step 1: Resolve new packages in the isolated Python 3.12 environment**

Run the resolver without editing requirements first:

```powershell
python -m pip install fastapi uvicorn jinja2 authlib itsdangerous python-dotenv httpx
python -m pip show fastapi uvicorn jinja2 authlib itsdangerous python-dotenv httpx
```

Record each installed `Version:` exactly, then add exact `==` pins for FastAPI, Uvicorn, Jinja2, Authlib, itsdangerous and python-dotenv to `requirements.txt`; add exact `httpx==...` to `requirements-dev.txt`. Do not alter existing pins.

- [ ] **Step 2: Write settings tests first**

Add `WebAuthTests` cases that patch `os.environ` and call `load_web_settings()`:

```python
def test_settings_reject_missing_short_and_example_session_secrets(self):
    for secret in ('', 'too-short', 'change-me'):
        with self.subTest(secret=secret), patch.dict(os.environ, self.valid_env | {'WEB_SESSION_SECRET': secret}, clear=True):
            with self.assertRaises(ValueError):
                load_web_settings()

def test_settings_reject_non_local_or_malformed_redirects(self):
    for uri in (
        'https://127.0.0.1:8000/auth/discord/callback',
        'http://0.0.0.0:8000/auth/discord/callback',
        'http://example.com/auth/discord/callback',
        'http://user@localhost:8000/auth/discord/callback',
        'http://localhost:8000/wrong',
    ):
        with self.subTest(uri=uri), patch.dict(os.environ, self.valid_env | {'DISCORD_REDIRECT_URI': uri}, clear=True):
            with self.assertRaises(ValueError):
                load_web_settings()
```

Also verify valid `127.0.0.1` and `localhost` URIs load, Client ID is decimal, OAuth secrets are required by `load_web_settings()`, exception text never contains actual secret values, and dotenv loading reads only the exact project-root `.env` path rather than searching parent directories.

- [ ] **Step 3: Run the focused test and verify RED**

```powershell
python -m unittest tests.test_web_auth -v
```

Expected: import failure because `web.settings` does not exist.

- [ ] **Step 4: Implement the smallest settings module**

Use a frozen dataclass and `urlsplit`; keep test injection possible by allowing direct `WebSettings(...)` construction while `load_web_settings()` remains strict:

```python
@dataclass(frozen=True)
class WebSettings:
    session_secret: str
    discord_client_id: str = ''
    discord_client_secret: str = ''
    discord_redirect_uri: str = 'http://127.0.0.1:8000/auth/discord/callback'


def load_web_settings():
    load_dotenv(dotenv_path=PROJECT_ROOT / '.env', override=False)
    # Read, strip, validate, and return WebSettings. Error text names fields only.
```

Define `PROJECT_ROOT = Path(__file__).resolve().parents[1]`. Reject empty OAuth fields, non-decimal Client ID, weak/example session secrets, and redirect URIs outside the exact local callback boundary. The settings tests use a temporary exact dotenv path with a parent `.env` decoy to prove no upward search occurs.

- [ ] **Step 5: Extend `.env.example` with blank values only**

Add comments explaining that only the Web factory reads project `.env`, then add:

```dotenv
WEB_SESSION_SECRET=
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://127.0.0.1:8000/auth/discord/callback
```

- [ ] **Step 6: Run focused tests and inspect status**

```powershell
python -m unittest tests.test_web_auth -v
git diff --check
git status --short
```

Expected: settings tests pass; only approved spec, plan, requirements, `.env.example`, `web/`, and Web test paths are modified/untracked.

---

### Task 2: App factory, public health route, login page, and protected placeholder

**Files:**
- Create: `web/app.py`
- Create: `web/auth.py`
- Create: `web/routes.py`
- Create: `web/templates/base.html`
- Create: `web/templates/login.html`
- Create: `web/templates/home_placeholder.html`
- Create: `web/templates/error.html`
- Create: `web/static/web.css`
- Modify: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: `WebSettings` and FastAPI `Request.session`.
- Produces: `create_app(settings: WebSettings | None = None) -> FastAPI`, `current_user_id(request) -> str | None`, `start_session(request, user_id) -> None`, `clear_session(request) -> None`, and `validate_csrf(request, supplied_token) -> bool`.

- [ ] **Step 1: Add failing page and session tests**

Create an app with a strong test session secret and blank OAuth credentials, then verify:

```python
def test_healthz_needs_no_oauth_secrets_and_leaks_nothing(self):
    response = self.client.get('/healthz')
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json(), {'status': 'ok'})
    self.assertNotIn(self.settings.session_secret, response.text)

def test_logged_out_home_only_shows_login(self):
    response = self.client.get('/')
    self.assertEqual(response.status_code, 200)
    self.assertIn('使用 Discord 登入', response.text)
    self.assertNotIn('已登入', response.text)
    self.assertNotIn('Discord ID', response.text)
    self.assertNotIn('帳務', response.text)
```

Do not construct or decode SessionMiddleware cookies in tests. Establish the authenticated TestClient session through the real `/login` → mocked `/auth/discord/callback` route flow added in Task 3, then verify it sees exactly the placeholder and logout form. For Task 2's initial RED test, keep only public and logged-out page behavior until that callback helper exists.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
python -m unittest tests.test_web_auth -v
```

Expected: failure because `create_app`, routes, and templates do not exist.

- [ ] **Step 3: Implement app factory and session helpers**

`create_app()` must load strict environment settings only when `settings is None`; injected settings allow health/page tests without OAuth secrets. Configure:

```python
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie='discordbot_web',
    max_age=7 * 24 * 60 * 60,
    same_site='lax',
    https_only=False,
)
```

Mount `/static`, keep templates under `web/templates`, store settings and OAuth client on `app.state`, and include one HTML router. Do not define a module-level app that loads secrets during import; Uvicorn uses the factory.

Session helpers use `secrets.token_urlsafe(32)` and `secrets.compare_digest`. `start_session()` first clears the OAuth transaction state, then stores only `discord_user_id` and `csrf_token`; `current_user_id()` validates the stored decimal Discord ID and clears malformed sessions. Session expiry is handled only by SessionMiddleware `max_age=604800`. Never return or render the ID.

- [ ] **Step 4: Add minimal server-rendered templates and CSS**

- `login.html`: title plus one `/login` link labelled `使用 Discord 登入`.
- `home_placeholder.html`: only `已登入` plus a POST `/logout` form containing hidden `csrf_token`.
- `error.html`: safe title, safe action text, and link back to `/`.
- `base.html` and `web.css`: readable centered panel with no final dashboard design and no JavaScript.

- [ ] **Step 5: Verify cookie attributes through real TestClient behavior**

After a session is created by the real callback route with the provider exchange mocked, assert the actual `Set-Cookie` header contains `Max-Age=604800`, `HttpOnly`, `SameSite=lax`, and does not contain `Secure`. Test `start_session()` separately with an ordinary dict preloaded with Authlib state and token-like keys, then assert the resulting dict contains exactly `discord_user_id` and `csrf_token`; this verifies cleanup without depending on Starlette's private Cookie format.

- [ ] **Step 6: Run focused tests and inspect imports**

```powershell
python -m unittest tests.test_web_auth -v
rg -n "^(import|from) (db|spending|life_ledger_service|config)" web tests/test_web_auth.py
git diff --check
```

Expected: Web tests pass; `rg` prints nothing.

---

### Task 3: Discord OAuth state handling, safe callback, and CSRF logout

**Files:**
- Modify: `web/auth.py`
- Modify: `web/routes.py`
- Modify: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: Authlib `OAuth`, Discord authorize/token/API endpoints, request session, and form-urlencoded logout body.
- Produces: `create_oauth(settings)`, `begin_discord_login(request)`, `fetch_discord_user_id(request) -> Awaitable[str]`, `complete_discord_login(request) -> Awaitable[str]`, `AuthFailure`, and CSRF-protected logout behavior.

- [ ] **Step 1: Add failing login-start tests**

Verify `GET /login` returns a redirect to Discord containing `scope=identify`, the configured callback URI and a non-empty state, never accepts/reflects `next`, and lets Authlib temporarily store state in the signed session. A test app without OAuth credentials must show the safe error page instead of raising or exposing configuration.

- [ ] **Step 2: Add failing callback tests**

Use `GET /login` to establish Authlib state. For mismatch, call the real `fetch_discord_user_id()` path with the wrong state while mocking only the outbound token exchange; assert Authlib rejects before the exchange, the response is a safe error page, and no login session is created.

For user denial and provider failure, simulate `error=access_denied` and an `OAuthError`/HTTP failure. Assert status is safe, body omits provider exception details and secrets, Authlib's temporary state is cleared, and the home remains logged out.

For success, patch only the minimal async `fetch_discord_user_id()` function to return literal Discord ID `123456789012345678`, then run the real callback route and assert it redirects to `/` and produces an authenticated home. Separately call `start_session()` with a plain dict containing temporary Authlib state and token-like fields; assert cleanup leaves exactly the provider ID and one CSRF token, with no token, refresh token, email, username or profile keys.

Add invalid-provider-ID cases for empty, non-decimal and values outside `0 < id < 2**64`; each must return the safe error page without a session.

- [ ] **Step 3: Add failing identity-injection and logout tests**

Verify query/form `user_id=999` cannot create or replace identity: after the mocked provider returns ID `123456789012345678`, that provider ID is the only stored ID.

For logout, extract the real hidden CSRF token from the authenticated HTML after the mocked callback has established the TestClient session. POST without a token, with a wrong token, and with duplicate token fields; each returns 403 and preserves the authenticated session. POST the exact token once; expect 303 to `/`, cleared cookie/session, and subsequent `GET /` shows login. No test manually forges a signed Cookie.

- [ ] **Step 4: Run the focused file and verify RED**

```powershell
python -m unittest tests.test_web_auth -v
```

Expected: OAuth and logout cases fail because protocol and CSRF handlers are not implemented.

- [ ] **Step 5: Implement Authlib Discord integration**

Register one Authlib client with endpoints:

```python
oauth.register(
    name='discord',
    client_id=settings.discord_client_id,
    client_secret=settings.discord_client_secret,
    access_token_url='https://discord.com/api/oauth2/token',
    authorize_url='https://discord.com/oauth2/authorize',
    api_base_url='https://discord.com/api/',
    client_kwargs={'scope': 'identify'},
)
```

`begin_discord_login()` clears stale session contents, then calls `authorize_redirect(request, settings.discord_redirect_uri)` so Authlib may temporarily store OAuth state. `fetch_discord_user_id()` calls `authorize_access_token(request)` so Authlib validates state, then fetches `users/@me` and immediately reduces the response to a validated decimal ID; it never returns or stores the token dict.

Catch state, denial and provider errors at the OAuth boundary and raise `AuthFailure` with fixed Traditional Chinese messages only. On every callback failure, clear the session before rendering the safe error page. On success, `start_session()` also clears the session before writing only the Discord ID and CSRF token. Do not log exception text, token responses or provider payloads.

- [ ] **Step 6: Implement POST logout parsing and CSRF**

Avoid another dependency: require `application/x-www-form-urlencoded`, read `request.body()`, parse with `urllib.parse.parse_qs(..., keep_blank_values=True)`, require exactly one `csrf_token`, then call `secrets.compare_digest`. Reject with 403 and render `error.html` without clearing session; success clears session and redirects to `/` with 303.

- [ ] **Step 7: Run focused and full Web tests**

```powershell
python -m unittest tests.test_web_auth -v
python tests/run_discord_validation.py
```

Expected: all Web cases and the complete isolated suite pass without network or SQLite access.

---

### Task 4: Documentation, dynamic patch version, and final verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Verify: `.gitignore`
- Verify: `tests/run_discord_validation.py`

**Interfaces:**
- Consumes: the completed Phase 1 Web skeleton and actual verification output.
- Produces: documented local startup/configuration and a patch-version release record derived from the latest CHANGELOG entry at implementation time.

- [ ] **Step 1: Update README**

Immediately before editing, read the current top version in `CHANGELOG.md`, increment only its patch component, and use that calculated version in both README and CHANGELOG. Add a short Web v1 Phase 1 section stating it is login-only and cannot yet read or write accounting data. Document required variable names without values, the exact Discord Developer Portal callback:

```text
http://127.0.0.1:8000/auth/discord/callback
```

Document local startup with the factory and fixed host:

```powershell
python -m uvicorn web.app:create_app --factory --host 127.0.0.1 --port 8000
```

State that `.env` must never be committed and that real Discord OAuth remains a manual verification item. Do not document `0.0.0.0` as an alternative.

- [ ] **Step 2: Add the Traditional Chinese CHANGELOG entry**

Add the calculated patch version dated `2026-09-23` above the prior latest entry, recording only completed facts: Web skeleton/routes, Authlib OAuth scope/state, seven-day cookie/CSRF, exact dependency pins, no SQLite access/schema changes, startup steps, actual test counts, and unverified real Discord OAuth/formal database. Do not record secrets or values.

- [ ] **Step 3: Run all required verification commands exactly**

```powershell
python -m ruff check .
python tests/run_discord_validation.py
python -m unittest tests.test_web_auth -v
git diff --check
git status --short
```

Also run:

```powershell
git check-ignore -v .env .venv
git diff --name-only
git diff --cached --name-only
```

Expected: Ruff and all tests exit 0; `.env`/`.venv` are ignored; staged list is empty; no database, Token, backup, export or user-data path appears.

- [ ] **Step 4: Final read-only audit without commit or push**

Confirm:

- `web/` and `tests/test_web_auth.py` do not import `db`, `spending`, `life_ledger_service` or `config`.
- no route accepts `user_id` or `next` as an identity/redirect input.
- app factory default rejects missing/weak secrets; injected test settings can serve `/healthz` without OAuth credentials.
- only the Discord `identify` scope is requested.
- no module-level app starts a server or initializes a database.
- branch remains `codex/localhost-web-auth`, all changes remain uncommitted, and no push occurred.
