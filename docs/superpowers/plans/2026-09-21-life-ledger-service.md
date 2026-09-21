# Life Ledger Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one Discord-independent life-ledger service façade that reuses the existing spending rules, enforces per-user access, supports reversible soft voiding, and becomes the shared entry point for existing Discord life-expense flows.

**Architecture:** `life_ledger_service.py` normalizes `user_id`, delegates validation and transactions to `spending.py`, and returns plain Python values. `spending.py` gains only a scoped list helper and transactional `void_expense`; Discord files keep presentation and interaction responsibilities while calling the façade for in-scope life-expense operations.

**Tech Stack:** Python 3.12, SQLite, existing `unittest` isolation runner, discord.py, Ruff 0.16.8

**Spec:** `docs/superpowers/specs/2026-09-21-life-ledger-service-design.md`

## Global Constraints

- Do not read or modify `token.txt`, formal `data.db`, real backups, exports, or user data.
- Do not add FastAPI, pytest, SQLModel, Alembic, React, Expo, an ORM, a repository framework, or another database.
- Keep `spending.py` as the source of money, date, category, payment, revision, transaction, rollback, and undo rules.
- Every single-expense operation accepts both `user_id` and `expense_id`; SQL ownership checks include both values.
- `void_expense` sets `voided=1` in the same transaction as its action record and never executes SQLite `DELETE`.
- Existing Discord labels, commands, buttons, messages, pagination, and results remain unchanged; no delete button or delete command is added.
- Normal search, calendar, summary, and chart reads exclude voided entries; existing historical lists explicitly opt into voided entries.
- Preserve the existing isolated `tests/run_discord_validation.py` runner; do not introduce pytest.
- Version changes from `0.10.2` to `0.11.0`; README and CHANGELOG must agree.
- All Git commits stay local; the user will push to GitHub.

## Review Focus

- Integer Discord IDs and string Web IDs must resolve to the same owner: Task 1 tests `user_id=42` against rows stored under `"42"`.
- `bool`, negative, and non-integer pagination values must be rejected before SQL construction: Task 1 tests `limit=True`, `limit=-1`, and `offset="1"`.
- Stale expense revisions and stale undo action IDs must fail without changing records: Tasks 1 and 2 compare database snapshots before and after rejection.
- Failure while writing an action, voiding an expense, or marking undo complete must roll back the entire transaction: Task 2 injects one SQLite trigger at each failure point.
- Voided rows must remain physically present, disappear from active reads, and remain visible only in historical lists: Task 2 verifies the database row and every relevant Services read.

---

## File Structure

- Create `life_ledger_service.py`: the only public façade for reusable life-expense operations; no Discord imports and no SQL.
- Modify `spending.py`: add user-scoped `list_expenses` and transactional `void_expense`; keep all existing rules and schema ownership here.
- Create `tests/test_life_ledger_service.py`: direct Services tests using a temporary database, cross-user assertions, failure injection, and plain-data return checks.
- Modify `dashboard.py`: route in-scope dashboard add/read/list/search/edit/calendar/summary/chart calls through the façade.
- Modify `spending_commands.py`: route in-scope life-expense commands and historical details through the façade while leaving recurring, budgets, AI, and notices unchanged.
- Modify `form_ui.py` and `selection_ui.py`: read category and payment choices through the façade; retain management writes in `spending.py`.
- Modify `lifestyle_ui.py`: route confirmation add, recent expenses, and single-entry rereads through the façade.
- Modify focused Discord tests only where needed to prove façade delegation; preserve all existing behavior assertions.
- Modify `README.md` and `CHANGELOG.md`: document the boundary, version `0.11.0`, upgrade notes, validation evidence, and untested Web/API/cloud behavior.

### Task 1: Plain-data façade and scoped reads

**Files:**
- Create: `life_ledger_service.py`
- Modify: `spending.py:231-243`
- Create: `tests/test_life_ledger_service.py`

**Interfaces:**
- Consumes: existing `spending.add`, `spending.get_expense`, `spending.edit`, `spending.search_expenses`, `spending.calendar_days`, `spending.month_report`, `spending.chart_data`, `spending.category_names`, `spending.payment_sources`, and `spending.recent_expenses`.
- Produces: `add_expense`, `get_expense`, `list_expenses`, `search_expenses`, `update_expense`, `get_calendar_days`, `get_month_summary`, `get_chart_data`, `get_categories`, `get_payment_sources`, and `get_recent_expenses` with the signatures in the approved spec.

- [ ] **Step 1: Create isolated Services tests for add, get, update, ownership, lists, pagination, summaries, and options**

Create the test module with this setup and focused methods:

```python
import tempfile
import unittest
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import life_ledger_service as service
import spending as sp


class LifeLedgerServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_NAME", str(Path(self.temp.name) / "service.db"))
        self.clock = patch("spending.today", return_value=date(2026, 9, 21))
        self.db_patch.start()
        self.clock.start()
        db.init_db()

    def tearDown(self):
        self.clock.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_add_get_update_and_owner_scope(self):
        source = sp.add_payment_source("42", "測試卡")
        expense_id = service.add_expense(42, "120.50", "餐飲", "午餐", "2026-09-20", source)
        row = service.get_expense("42", expense_id)
        self.assertIsInstance(row, dict)
        self.assertEqual((row["user_id"], row["cents"], row["payment_source_name"]), ("42", 12050, "測試卡"))
        with self.assertRaises(ValueError):
            service.get_expense("43", expense_id)
        before = sp.rows("SELECT * FROM expenses WHERE id=?", (expense_id,))[0]
        with self.assertRaises(ValueError):
            service.update_expense("43", expense_id, 1, "餐飲", "越權", "2026-09-20")
        self.assertEqual(sp.rows("SELECT * FROM expenses WHERE id=?", (expense_id,))[0], before)
        service.update_expense("42", expense_id, 150, "交通", "車票", "2026-09-19", expected_revision=0)
        changed = service.get_expense("42", expense_id)
        self.assertEqual((changed["cents"], changed["category"], changed["revision"]), (15000, "交通", 1))
        with self.assertRaises(ValueError):
            service.update_expense("42", expense_id, 160, "交通", "過期表單", "2026-09-19", expected_revision=0)
        self.assertEqual(service.get_expense("42", expense_id), changed)

    def test_list_order_total_offset_and_validation(self):
        ids = [service.add_expense(42, n, "餐飲", f"項目{n}", "2026-09-20") for n in (1, 2, 3)]
        service.add_expense(43, 99, "餐飲", "他人", "2026-09-20")
        page = service.list_expenses(42, "2026-09", limit=2, offset=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual([row["id"] for row in page["items"]], [ids[1], ids[0]])
        for kwargs in ({"limit": True}, {"limit": -1}, {"offset": "1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                service.list_expenses(42, "2026-09", **kwargs)

    def test_search_uses_existing_validation_and_owner_scope(self):
        own = service.add_expense(42, 80, "餐飲", "早餐店", "2026-09-01")
        service.add_expense(43, 80, "餐飲", "早餐店", "2026-09-01")
        self.assertEqual([row["id"] for row in service.search_expenses(42, "早餐")], [own])
        with self.assertRaises(ValueError):
            service.search_expenses(42)

    def test_calendar_summary_chart_and_recent_are_owner_scoped(self):
        own = service.add_expense(42, 25, "餐飲", "本人", "2026-09-02")
        service.add_expense(43, 900, "餐飲", "他人", "2026-09-02")
        self.assertEqual(sum(day["cents"] for day in service.get_calendar_days(42, "2026-09")), 2500)
        self.assertEqual(service.get_month_summary(42, "2026-09")["total"], 25)
        chart = service.get_chart_data(42, "2026-09")
        self.assertEqual(chart["categories"], [("餐飲", 2500)])
        self.assertEqual([row["id"] for row in service.get_recent_expenses(42)], [own])

    def test_categories_and_payment_sources_are_owner_scoped_plain_data(self):
        sp.set_category("42", "寵物", True)
        source = sp.add_payment_source("42", "測試卡")
        self.assertIn("寵物", service.get_categories(42))
        self.assertNotIn("寵物", service.get_categories(43))
        sources = service.get_payment_sources(42)
        self.assertTrue(all(isinstance(row, dict) for row in sources))
        self.assertIn(source, [row["id"] for row in sources])
        self.assertNotIn(source, [row["id"] for row in service.get_payment_sources(43)])
```

- [ ] **Step 2: Run the new test file and confirm the missing module failure**

Run:

```powershell
python -m unittest discover -s tests -p "test_life_ledger_service.py" -v
```

Expected: import failure for `life_ledger_service` before implementation.

- [ ] **Step 3: Add the minimal scoped list helper to `spending.py`**

Place this next to `month_expenses` and keep `month_expenses` unchanged for existing out-of-scope callers:

```python
def list_expenses(user_id, month, include_voided=False, limit=None, offset=0):
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("筆數需為非負整數")
    if type(offset) is not int or offset < 0:
        raise ValueError("起始位置需為非負整數")
    start = month_date(month)
    where = "user_id=? AND spent_on>=? AND spent_on<? AND kind='consumption'"
    args = [str(user_id), start.isoformat(), next_month(start).isoformat()]
    if not include_voided:
        where += " AND voided=0"
    total = rows(f"SELECT COUNT(*) AS n FROM expenses WHERE {where}", args)[0]["n"]
    sql = f"SELECT * FROM expenses WHERE {where} ORDER BY spent_on DESC,id DESC"
    page_args = list(args)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        page_args.extend((limit, offset))
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        page_args.append(offset)
    return {"items": rows(sql, page_args), "total": total}
```

- [ ] **Step 4: Create the service façade without Discord or SQL**

Create `life_ledger_service.py` with direct delegation:

```python
"""Reusable life-expense operations with no Discord UI dependency."""
import spending as sp


def _user(value):
    return str(value)


def add_expense(user_id, amount, category, note, spent_on=None, payment_source_id=None):
    return sp.add(_user(user_id), amount, category, note, spent_on, payment_source_id)


def get_expense(user_id, expense_id):
    return sp.get_expense(_user(user_id), expense_id)


def list_expenses(user_id, month, *, include_voided=False, limit=None, offset=0):
    return sp.list_expenses(_user(user_id), month, include_voided, limit, offset)


def search_expenses(user_id, keyword="", start=None, end=None):
    return sp.search_expenses(_user(user_id), keyword, start, end)


def update_expense(user_id, expense_id, amount, category, note, spent_on,
                   payment_source_id=None, expected_revision=None):
    return sp.edit(_user(user_id), expense_id, amount, category, note, spent_on,
                   payment_source_id, expected_revision)


def get_calendar_days(user_id, month):
    return sp.calendar_days(_user(user_id), month)


def get_month_summary(user_id, month=None):
    return sp.month_report(_user(user_id), month)


def get_chart_data(user_id, month=None):
    return sp.chart_data(_user(user_id), month)


def get_categories(user_id, include_inactive=False):
    return sp.category_names(_user(user_id), include_inactive)


def get_payment_sources(user_id, include_inactive=False):
    return sp.payment_sources(_user(user_id), include_inactive)


def get_recent_expenses(user_id):
    return sp.recent_expenses(_user(user_id))
```

- [ ] **Step 5: Run the focused Services tests**

Run:

```powershell
python -m unittest discover -s tests -p "test_life_ledger_service.py" -v
```

Expected: the five Task 1 tests pass.

- [ ] **Step 6: Commit Task 1 locally**

```powershell
git add life_ledger_service.py spending.py tests/test_life_ledger_service.py
git commit -m "feat: add reusable life ledger service reads"
```

### Task 2: Transactional void and latest-action undo

**Files:**
- Modify: `spending.py:277-316`
- Modify: `life_ledger_service.py`
- Modify: `tests/test_life_ledger_service.py`

**Interfaces:**
- Consumes: existing `ledger.transaction`, `expense_actions.before_json`, `expenses.voided`, `expenses.revision`, and `spending.undo(user_id, confirm)`.
- Produces: `void_expense(user_id, expense_id, expected_revision=None)`, `preview_undo(user_id)`, and `undo_latest_action(user_id, expected_action_id=None)`.

- [ ] **Step 1: Add failing soft-void, undo, cross-user, and rollback tests**

Append these test methods to `LifeLedgerServiceTests`:

```python
    def test_void_is_soft_and_active_reads_exclude_it(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        service.void_expense(42, expense_id, expected_revision=0)
        stored = sp.rows("SELECT * FROM expenses WHERE id=? AND user_id=?", (expense_id, "42"))
        self.assertEqual(len(stored), 1)
        self.assertEqual((stored[0]["voided"], stored[0]["revision"]), (1, 1))
        with self.assertRaises(ValueError):
            service.get_expense(42, expense_id)
        self.assertEqual(service.list_expenses(42, "2026-09")["items"], [])
        self.assertEqual([row["id"] for row in service.list_expenses(42, "2026-09", include_voided=True)["items"]], [expense_id])
        self.assertEqual(service.search_expenses(42, "晚餐"), [])
        self.assertEqual(sum(day["cents"] for day in service.get_calendar_days(42, "2026-09")), 0)
        self.assertEqual(service.get_month_summary(42, "2026-09")["total"], 0)
        self.assertEqual(service.get_chart_data(42, "2026-09")["categories"], [])

    def test_void_rejects_other_owner_and_stale_revision_without_changes(self):
        expense_id = service.add_expense(42, 30, "餐飲", "本人", "2026-09-03")
        before = sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows("SELECT * FROM expense_actions ORDER BY id")
        for user_id, revision in ((43, 0), (42, 99)):
            with self.subTest(user_id=user_id, revision=revision), self.assertRaises(ValueError):
                service.void_expense(user_id, expense_id, revision)
            self.assertEqual((sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows("SELECT * FROM expense_actions ORDER BY id")), before)

    def test_preview_and_undo_latest_restore_void(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        service.void_expense(42, expense_id)
        preview = service.preview_undo(42)
        self.assertEqual(preview["expense_id"], expense_id)
        with self.assertRaises(ValueError):
            service.undo_latest_action(42, preview["action_id"] + 1)
        self.assertEqual(sp.rows("SELECT voided FROM expenses WHERE id=?", (expense_id,))[0]["voided"], 1)
        result = service.undo_latest_action(42, preview["action_id"])
        self.assertEqual(result, preview)
        self.assertEqual(service.get_expense(42, expense_id)["voided"], 0)

    def test_void_write_failures_roll_back_action_and_expense(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        for table, event in (("expense_actions", "INSERT"), ("expenses", "UPDATE")):
            before = sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows("SELECT * FROM expense_actions ORDER BY id")
            with sp.transaction() as conn:
                conn.execute(f"CREATE TRIGGER fail_void BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'private detail'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                service.void_expense(42, expense_id)
            self.assertEqual((sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows("SELECT * FROM expense_actions ORDER BY id")), before)
            with sp.transaction() as conn:
                conn.execute("DROP TRIGGER fail_void")

    def test_undo_completion_failure_rolls_back_expense_restore(self):
        expense_id = service.add_expense(42, 30, "餐飲", "晚餐", "2026-09-03")
        service.void_expense(42, expense_id)
        action_id = service.preview_undo(42)["action_id"]
        before = sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows("SELECT * FROM expense_actions ORDER BY id")
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_undo BEFORE UPDATE ON expense_actions WHEN NEW.undone=1 BEGIN SELECT RAISE(ABORT,'private detail'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            service.undo_latest_action(42, action_id)
        self.assertEqual((sp.rows("SELECT * FROM expenses ORDER BY id"), sp.rows("SELECT * FROM expense_actions ORDER BY id")), before)
```

- [ ] **Step 2: Run the focused test file and confirm missing method failures**

Run:

```powershell
python -m unittest discover -s tests -p "test_life_ledger_service.py" -v
```

Expected: Task 1 remains green; new tests fail because the void and undo façade functions do not exist.

- [ ] **Step 3: Implement one-transaction soft void in `spending.py`**

Add this next to `edit` and reuse its error and revision semantics:

```python
def void_expense(user_id, key, expected_revision=None):
    with transaction() as conn:
        conn.row_factory = sqlite3.Row
        old = conn.execute(
            "SELECT * FROM expenses WHERE user_id=? AND id=? AND voided=0 AND kind='consumption'",
            (user_id, key),
        ).fetchone()
        if not old:
            raise ValueError("找不到自己的有效消費")
        if expected_revision is not None and old["revision"] != expected_revision:
            raise ValueError("此筆帳目已變動，請重新選取後修改")
        conn.execute(
            "INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)",
            (user_id, key, json.dumps(dict(old))),
        )
        changed = conn.execute(
            "UPDATE expenses SET voided=1,revision=revision+1 "
            "WHERE user_id=? AND id=? AND voided=0 AND kind='consumption'",
            (user_id, key),
        )
        if changed.rowcount != 1:
            raise ValueError("找不到自己的有效消費")
```

- [ ] **Step 4: Add Services void and latest-action wrappers**

Append to `life_ledger_service.py`:

```python
def void_expense(user_id, expense_id, expected_revision=None):
    return sp.void_expense(_user(user_id), expense_id, expected_revision)


def preview_undo(user_id):
    action_id, expense_id = sp.undo(_user(user_id))
    return {"action_id": action_id, "expense_id": expense_id}


def undo_latest_action(user_id, expected_action_id=None):
    owner = _user(user_id)
    action_id = expected_action_id
    if action_id is None:
        action_id, _ = sp.undo(owner)
    undone_action_id, expense_id = sp.undo(owner, action_id)
    return {"action_id": undone_action_id, "expense_id": expense_id}
```

This deliberately preserves `spending.undo` as the only undo implementation. When no expected ID is provided, the preview and confirm calls remain race-safe because `spending.undo` rejects a changed latest action.

- [ ] **Step 5: Run Services and existing write-reliability tests**

Run:

```powershell
python -m unittest discover -s tests -p "test_life_ledger_service.py" -v
python -m unittest discover -s tests -p "test_write_reliability.py" -v
```

Expected: all focused tests pass, including trigger-induced rollback assertions.

- [ ] **Step 6: Commit Task 2 locally**

```powershell
git add spending.py life_ledger_service.py tests/test_life_ledger_service.py
git commit -m "feat: add reversible expense void service"
```

### Task 3: Route Discord forms, dashboard, and selectors through Services

**Files:**
- Modify: `dashboard.py`
- Modify: `form_ui.py`
- Modify: `selection_ui.py`
- Modify: `lifestyle_ui.py`
- Modify: `tests/test_phase1_ui.py`
- Modify: `tests/test_search_expenses.py`

**Interfaces:**
- Consumes: all Task 1 read/write façade functions; Task 2 does not add a Discord delete control.
- Produces: existing Discord form, dashboard, search, calendar, chart, recent-entry, category-option, and payment-option behavior backed by `life_ledger_service`.

- [ ] **Step 1: Add façade-delegation assertions to existing UI tests**

Import `life_ledger_service as life_service` in `tests/test_phase1_ui.py` and wrap the existing add and edit actions:

```python
with patch("dashboard.life_service.add_expense", wraps=life_service.add_expense) as add_expense:
    await entry.on_submit(i)
add_expense.assert_called_once()

with patch("dashboard.life_service.update_expense", wraps=life_service.update_expense) as update_expense:
    await modal.on_submit(i)
update_expense.assert_called_once()
```

Import the same module in `tests/test_search_expenses.py` and wrap the existing successful search submit:

```python
with patch("dashboard.life_service.search_expenses", wraps=life_service.search_expenses) as search_expenses:
    await modal.on_submit(i)
search_expenses.assert_called_once()
```

Expected before production edits: these tests fail because `dashboard.life_service` does not exist.

- [ ] **Step 2: Import the façade and replace only in-scope dashboard calls**

Add:

```python
import life_ledger_service as life_service
```

Use these exact mappings in `dashboard.py`:

```python
report = life_service.get_month_summary(user_id, month)
listing = life_service.list_expenses(user_id, month, include_voided=True, limit=6, offset=page * 6)
key = life_service.add_expense(str(self.view.owner), values["amount"], values["category"], values["note"], values["date"], values["payment"])
life_service.update_expense(str(self.view.owner), self.expense["id"], values["amount"], values["category"], values["note"], values["date"], values["payment"], self.expense["revision"])
entries = life_service.search_expenses(str(self.owner), **values)
entry = life_service.get_expense(str(self.owner), key)
days = life_service.get_calendar_days(str(owner), month)
entries = life_service.list_expenses(str(view.owner), month)["items"]
data = life_service.get_chart_data(str(self.owner), self.month)
```

For the historical dashboard list, calculate pages from `listing["total"]` and render `listing["items"]`; keep its existing “已撤銷” label. Keep `sp.today`, budgets, recurring entries, reminders, shortcuts, AI, and other out-of-scope calls unchanged.

- [ ] **Step 3: Route category and payment option reads through the façade**

Add the façade import to `form_ui.py` and `selection_ui.py`, then replace only these reads:

```python
life_service.get_categories(str(self.owner))
life_service.get_payment_sources(str(self.owner))
life_service.get_categories(str(i.user.id))
life_service.get_payment_sources(str(self.owner), include_inactive=True)
```

Leave `sp.set_category`, `sp.add_payment_source`, `sp.rename_payment_source`, and `sp.disable_payment_source` unchanged because management writes are outside the public Services scope.

- [ ] **Step 4: Route recent-confirmation flows through the façade**

Add the façade import to `lifestyle_ui.py` and replace the existing calls with:

```python
key = life_service.add_expense(str(i.user.id), values["amount"], values["category"], values["note"], values["date"], values["payment"])
entries = life_service.get_recent_expenses(str(dashboard.owner))
row = life_service.get_expense(str(dashboard.owner), key)
```

Keep shortcut management and recommendations on `spending.py`.

- [ ] **Step 5: Run focused UI characterization tests**

Run:

```powershell
python -m unittest discover -s tests -p "test_phase1_ui.py" -v
python -m unittest discover -s tests -p "test_search_expenses.py" -v
python -m unittest discover -s tests -p "test_form_new_options.py" -v
python -m unittest discover -s tests -p "test_shortcut_dropdowns.py" -v
```

Expected: existing UI results pass and the new delegation assertions observe Services calls.

- [ ] **Step 6: Commit Task 3 locally**

```powershell
git add dashboard.py form_ui.py selection_ui.py lifestyle_ui.py tests/test_phase1_ui.py tests/test_search_expenses.py
git commit -m "refactor: route Discord ledger UI through services"
```

### Task 4: Route life-expense text commands through Services

**Files:**
- Modify: `spending_commands.py`
- Modify: `tests/test_discord_spending.py`

**Interfaces:**
- Consumes: `add_expense`, `update_expense`, `preview_undo`, `undo_latest_action`, `get_month_summary`, `list_expenses`, and `get_categories`.
- Produces: unchanged `!支出`, `!支出補登`, `!支出修改`, `!記帳撤銷`, `!月報`, and `!支出明細` behavior through Services.

- [ ] **Step 1: Add command delegation and historical-list assertions to the existing command test**

Import `life_ledger_service as life_service` in `tests/test_discord_spending.py`. Wrap the test's existing `!支出 150 餐飲 午餐 加飲料` invocation:

```python
with patch("spending_commands.life_service.add_expense", wraps=life_service.add_expense) as add_expense:
    await command("!支出 150 餐飲 午餐 加飲料")
add_expense.assert_called_once()
```

At the end of the same isolated test, add a behavior assertion that `!支出明細` still contains `（已撤銷）` after voiding an entry through Services:

```python
expense_id = life_service.add_expense("42", 10, "餐飲", "撤銷歷史", sp.today().isoformat())
life_service.void_expense("42", expense_id)
details = await command(f"!支出明細 {month}")
self.assertIn("（已撤銷）", details.send.await_args.args[0])
```

Expected before production edits: the patch targets fail because `spending_commands.life_service` does not exist.

- [ ] **Step 2: Replace only in-scope command calls**

Add the façade import and use these mappings:

```python
key = life_service.add_expense(str(ctx.author.id), amount, cat, note)
key = life_service.add_expense(str(ctx.author.id), amount, cat, note, on)
life_service.update_expense(str(ctx.author.id), key, amount, cat, note, on)
data = life_service.get_month_summary(str(ctx.author.id), month)
listing = life_service.list_expenses(str(ctx.author.id), month, include_voided=True, limit=20, offset=(page - 1) * 20)
categories = life_service.get_categories(str(ctx.author.id))
```

Preserve the existing undo two-step text exactly:

```python
if confirm is None:
    result = life_service.preview_undo(str(ctx.author.id))
else:
    result = life_service.undo_latest_action(str(ctx.author.id), confirm)
action, key = result["action_id"], result["expense_id"]
```

Keep recurring expenses, budgets, notices, AI classification/query paths, shortcuts, fixed data, and investment behavior on their existing modules.

- [ ] **Step 3: Run command and core spending regressions**

Run:

```powershell
python -m unittest discover -s tests -p "test_discord_spending.py" -v
python -m unittest discover -s tests -p "test_spending.py" -v
python -m unittest discover -s tests -p "test_daily_flow.py" -v
```

Expected: command wording and data results remain unchanged; the new delegation assertions pass.

- [ ] **Step 4: Commit Task 4 locally**

```powershell
git add spending_commands.py tests/test_discord_spending.py
git commit -m "refactor: route ledger commands through services"
```

### Task 5: Documentation, version, and full verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Verify: `pyproject.toml`
- Verify: `.github/workflows/ci.yml`
- Verify: `.gitignore`

**Interfaces:**
- Consumes: all completed service and Discord changes.
- Produces: version `0.11.0`, developer boundary documentation, exact local validation commands, and delivery evidence.

- [ ] **Step 1: Update README version and developer boundary**

Set the current version line to `0.11.0`. Add a short “生活記帳 Services 層” developer subsection stating:

```markdown
### 生活記帳 Services 層

Discord 的表單、按鈕、指令與看板只負責互動與顯示；可重用的生活消費操作由 `life_ledger_service.py` 提供，並沿用 `spending.py` 的驗證、交易、rollback、revision 與使用者隔離規則。這是未來網站版的程式邊界準備，目前沒有 FastAPI、HTTP API、網頁、登入或 Discord OAuth。
```

Keep the existing Ruff and isolated validation commands unchanged.

- [ ] **Step 2: Add the `0.11.0` Traditional Chinese CHANGELOG entry**

Place it above `0.10.2` and record all of the following as completed facts only after verification:

- Added `life_ledger_service.py` and list its public interfaces.
- Added user-scoped soft void with action history and latest-action undo; no SQLite `DELETE` and no schema migration.
- Routed the specified Discord forms, dashboard, and commands through Services without new UI or wording changes.
- Added direct isolated Services tests for CRUD, cross-user refusal, revision/action conflicts, soft void, rollback injection, active-read filtering, summaries, categories, payment sources, and pagination.
- Upgrade steps: stop the Bot, perform the existing pre-update backup, update code, restart; no dependency or database migration.
- Verification: record actual Ruff output and the actual total printed by `tests/run_discord_validation.py`.
- Untested: real Discord desktop/mobile interaction, GitHub-hosted Actions run, future Web/API, real Ollama, formal database, backup, export, and user data.

- [ ] **Step 3: Verify ignore and CI boundaries without reading sensitive files**

Run:

```powershell
git check-ignore -v token.txt data.db backups export.zip .ruff_cache .tmp
git check-ignore .github/workflows/ci.yml pyproject.toml requirements-dev.txt
```

Expected: the first command identifies ignore rules; the second command prints nothing and exits nonzero because required CI/config files are not ignored.

- [ ] **Step 4: Run Ruff with no automatic fixes**

Run:

```powershell
python -m ruff check .
```

Expected: `All checks passed!` and exit code 0. Do not use `--fix`.

- [ ] **Step 5: Run the complete existing isolated validation suite**

Run:

```powershell
python tests/run_discord_validation.py
```

Expected: exit code 0 and final `OK`; the runner must continue to use a temporary database and must not start the production Bot.

- [ ] **Step 6: Inspect the final diff and staged path safety**

Run:

```powershell
git diff --stat
git status --short
git diff --name-only
```

Confirm the diff contains only the files named by this plan plus the approved spec clarification. Confirm no `.db`, `.sqlite`, `.zip`, `.png`, `.lnk`, token, backup, export, or user-data path is staged.

- [ ] **Step 7: Commit Task 5 locally**

```powershell
git add README.md CHANGELOG.md docs/superpowers/specs/2026-09-21-life-ledger-service-design.md docs/superpowers/plans/2026-09-21-life-ledger-service.md
git commit -m "docs: release life ledger services 0.11.0"
```

- [ ] **Step 8: Verify local commit state without pushing**

Run:

```powershell
git log --oneline origin/main..main
git status --short --branch
```

Expected: the new local commits appear ahead of `origin/main`; only the user-retained untracked PNG files may remain, and no `git push` is executed.
