"""Synthetic Python / browser-format interoperability; never use a real ledger."""
import json
import base64
import copy
import shutil
import subprocess
import unittest
from datetime import date
from pathlib import Path

import test_portable_life_backup as existing


def synthetic_payload(helper):
    owner = helper.seed()
    sp = existing.sp
    with sp.transaction() as conn:
        conn.execute("UPDATE expenses SET cents=5000000000000001,revision=9007199254740993,note=? WHERE user_id=? AND spent_on='2024-12-31'",
                     ('歷史用途' * 60 + '<img src=x onerror=alert(1)>', owner))
        conn.execute("UPDATE expenses SET cents=5000000000000002,revision=9223372036854775807 WHERE user_id=? AND spent_on='2029-01-01'", (owner,))
        conn.execute("UPDATE recurring_expenses SET revision=9007199254740994 WHERE user_id=? AND kind='固定'", (owner,))
        conn.execute("UPDATE recurring_expenses SET periods=9007199254740997 WHERE user_id=? AND kind='分期'", (owner,))
        conn.execute("UPDATE spending_shortcuts SET position=9007199254740995 WHERE user_id=?", (owner,))
        conn.execute("INSERT INTO budgets VALUES(?,?,?,0)", (owner, '2019-12', '總額'))
        conn.execute("INSERT INTO spending_categories VALUES(?,?,1)", (owner, '</script><img src=x onerror=alert(2)>'))
        key = conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,source,voided) VALUES(?,?,?,?,?,'固定',1)",
                           (owner, '2020-02-29', 29, '歷史無override', '')).lastrowid
        conn.execute("INSERT INTO expense_actions(user_id,expense_id,before_json,undone) VALUES(?,?,'null',1)", (owner, key))
    return helper.bk.export_backup(owner)


class BrowserPortableBackupTests(unittest.TestCase):
    def setUp(self):
        self.helper = existing.PortableLifeBackupTests('test_full_roundtrip_preserves_meaning_settings_and_history')
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.payload = synthetic_payload(self.helper)

    def test_fixture_is_the_actual_python_export(self):
        fixture = Path(__file__).parent / 'fixtures' / 'portable_life_ledger.json'
        self.assertEqual(fixture.read_bytes(), self.payload)
        parsed = self.helper.bk.read_backup(self.payload)['data']
        self.assertEqual(len(parsed), 9)
        self.assertEqual(parsed['shortcuts'][0]['position'], 9007199254740995)
        self.assertEqual(parsed['recurring_rules'][2]['periods'], 9007199254740997)

    def test_node_lossless_roundtrip_then_python_restore_and_core_semantics(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the explicit interoperability test')
        script = Path(__file__).parent / 'portable_backup_roundtrip.mjs'
        result = subprocess.run([node, str(script)], input=self.payload, capture_output=True, check=True)
        self.verify(result.stdout)

    def test_node_acceptance_matches_python_v1_contract(self):
        bk = self.helper.bk
        original = bk.read_backup(self.payload)
        def encode(value):
            return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()
        cases = [self.payload]
        # Every required section and every record field is checked against Python.
        for section, rows in original['data'].items():
            changed = copy.deepcopy(original)
            del changed['data'][section]
            cases.append(encode(changed))
            records = rows if isinstance(rows, list) else [rows]
            if not records:
                continue
            for field in records[0]:
                changed = copy.deepcopy(original)
                record = changed['data'][section][0] if isinstance(rows, list) else changed['data'][section]
                del record[field]
                cases.append(encode(changed))
        mutations = [
            lambda d: d.update(owner='injected'),
            lambda d: d['data']['expenses'][0].update(revision=True),
            lambda d: d['data']['expenses'][0].update(revision=9223372036854775808),
            lambda d: d['data']['expenses'][0].update(revision=-1),
            lambda d: d['data']['expenses'][0].update(revision=1.0),
            lambda d: d['data']['expenses'][0].update(cents='01'),
            lambda d: d['data']['expenses'][0].update(cents='9223372036854775808'),
            lambda d: d['data']['expenses'][0].update(payment_source_id=[]),
            lambda d: d['data']['expenses'][0].update(spent_on='2025-02-29'),
            lambda d: d['data']['expenses'][1].update(period='2025-02'),
            lambda d: d['data']['expenses'].append({**d['data']['expenses'][1], 'id': 'duplicate_month'}),
            lambda d: d['data']['recurring_versions'].append({**d['data']['recurring_versions'][0], 'id': 'duplicate_version'}),
            lambda d: d['data']['recurring_rules'][2].update(periods=0),
            lambda d: d['data']['shortcuts'][0].update(position=9223372036854775807),
            lambda d: d['data']['shortcuts'][0].update(id=d['data']['expenses'][0]['id']),
            lambda d: d['data']['actions'][0].update(expense_id='missing'),
            lambda d: d['data']['actions'][5]['before'].update(source='固定'),
            lambda d: d['data']['actions'][5]['before'].update(id='wrong'),
            lambda d: d['data']['settings'].update(reminder_levels=[]),
            lambda d: d['data']['settings'].update(reminder_levels=[80, 80]),
            lambda d: d['data']['settings'].update(reminder_levels=[True]),
            lambda d: d['data']['settings'].update(recording_started_on='0001-01-01'),
            lambda d: d['data']['categories'][0].update(name='\u0085'),
            lambda d: d['data']['categories'][0].update(name='\ufeff'),
            lambda d: d['data']['categories'][0].update(name='😀' * 4096),
            lambda d: d['data']['expenses'][0].update(note='\ud800'),
            lambda d: d['data']['expenses'][0].update(note='\0'),
            lambda d: d['data']['expenses'][0].update(note='字' * 4097),
        ]
        for mutate in mutations:
            changed = copy.deepcopy(original)
            mutate(changed)
            cases.append(encode(changed))
        cases += [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e2}',
                  b'{"__proto__":{},"__proto__":{}}', b'\xef\xbb\xbf' + self.payload,
                  b'\xff', b'[' * 13 + b']' * 13, b' ' * (16 * 1024 * 1024 + 1)]
        cases += [self.payload.replace(b'"version":1', replacement) for replacement in (
            b'"version":1,"version":1', b'"version":1,"v\\u0065rsion":1',
            b'"version":1,"__proto__":null', b'"version":1,"__proto__":0',
            b'"version":1,"__proto__":true', b'"version":1,"__proto__":"text"',
            b'"version":1,"\\u005f\\u005fproto__":null')]
        cases += [self.payload.replace(b'"settings":', b'"settings":' + encode(original['data']['settings']) + b',"settings":'),
                  self.payload.replace(b'"active":1', b'"active":1,"active":1', 1),
                  self.payload.replace(b'"source":"manual"', b'"source":"manual","__proto__":null', 1)]
        expected = []
        for payload in cases:
            try:
                bk.read_backup(payload)
                expected.append(True)
            except bk.PortableBackupError:
                expected.append(False)
        script = Path(__file__).parent / 'portable_backup_roundtrip.mjs'
        result = subprocess.run([shutil.which('node'), str(script), '--validate-cases'],
                                input=json.dumps([base64.b64encode(payload).decode() for payload in cases]).encode(),
                                capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), expected)

    def verify(self, payload):
        bk, sp = self.helper.bk, existing.sp
        original = bk.read_backup(self.payload)
        self.assertEqual(bk.read_backup(payload), original)
        before = self.helper.snapshot()
        bk.restore_backup('target', payload)
        self.assertEqual(bk.read_backup(bk.export_backup('target')), original)
        after = self.helper.snapshot()
        for table in ('assets', 'ai_preferences', 'users'):
            self.assertEqual(before[table], after[table])
        source = 'source-discord-identity'
        for month in ('2019-12', '2024-12', '2025-01', '2025-02'):
            self.assertEqual(sp.month_report(source, month), sp.month_report('target', month))
            if month == '2019-12':
                for owner in (source, 'target'):
                    zero = sp.month_report(owner, month)['budgets'][0]
                    self.assertEqual((zero['budget_cents'], zero['spent_cents'], zero['remaining_cents']), (0, 0, 0))
                    self.assertIsNone(zero['used_percent'])
            self.assertEqual(sp.chart_data(source, month), sp.chart_data('target', month))
        def comparison(owner):
            return sp.expense_comparison(owner, '2024-12-01', '2025-01-31', '2025-02-01', '2025-03-31', as_of=date(2025, 2, 28))
        self.assertEqual(comparison(source), comparison('target'))
        for owner in (source, 'target'):
            action, entry = sp.undo(owner)
            old = sp.get_expense(owner, entry)['revision']
            sp.undo(owner, action)
            row = sp.get_expense(owner, entry)
            self.assertEqual((row['note'], row['revision']), ('更改前用途', old + 1))
            self.assertEqual(sp.sync_recurring(owner, date(2025, 1, 31)), 0)
            self.assertEqual(sp.sync_recurring(owner, date(2025, 2, 28)), 3)
            self.assertEqual(sp.sync_recurring(owner, date(2025, 2, 28)), 0)
        self.assertEqual(bk.read_backup(bk.export_backup(source)), bk.read_backup(bk.export_backup('target')))


if __name__ == '__main__':
    unittest.main()
