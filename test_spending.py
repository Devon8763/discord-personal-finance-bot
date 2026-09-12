import json
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
import db
import spending as sp
import query_plan


class SpendingTests(unittest.TestCase):
    def test_custom_categories_archive_restore_and_scope(self):
        sp.set_category('a','寵物',True)
        sp.add('a',300,'寵物','飼料')
        with self.assertRaises(ValueError):
            sp.add('b',300,'寵物','飼料')
        sp.set_category('a','寵物',False)
        with self.assertRaises(ValueError):
            sp.add('a',300,'寵物','飼料')
        self.assertEqual(sp.month_report('a')['categories']['寵物']['amount'],300)
        sp.set_category('a','寵物',True)
        self.assertIn('寵物',sp.category_names('a'))
        sp.set_category('a','餐飲',False)
        self.assertNotIn('餐飲',sp.category_names('a'))
        self.assertIn('餐飲',sp.category_names('b'))

    def test_custom_reminders_default_restore_and_no_repeat(self):
        self.assertEqual(sp.reminder_levels('a'),[80,100])
        sp.set_budget('a','2026-09','總額',1000)
        sp.set_reminders('a',[50,100,50])
        sp.add('a',600,'餐飲','test')
        self.assertEqual(len(sp.notices('a')),1)
        self.assertIn('50%',sp.notices('a')[0]['body'])
        sp.set_reminders('a',[75,100])
        self.assertEqual(sp.notices('a'),[])
        sp.add('a',200,'餐飲','test')
        notice = sp.notices('a')[0]
        sp.delivered(notice['id'],'a')
        sp.set_reminders('a',[75,100])
        self.assertEqual(sp.notices('a'),[])
        sp.set_reminders('a')
        self.assertEqual(sp.reminder_levels('a'),[80,100])
        self.assertIn('80%',sp.notices('a')[0]['body'])
        for levels in ([],[0],[1.5],[1001]):
            with self.assertRaises(ValueError):
                sp.set_reminders('a',levels)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db,'DB_NAME',str(Path(self.temp.name)/'test.db'))
        self.db_patch.start()
        self.clock = patch('spending.today',return_value=date(2026,9,7))
        self.clock.start()
        db.init_db()

    def tearDown(self):
        self.clock.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_expense_edit_undo_isolation_and_precision(self):
        key = sp.add('a','150.25','餐飲','午餐')
        with self.assertRaises(ValueError):
            sp.edit('b',key,'200','餐飲','test','2026-09-07')
        sp.edit('a',key,'200','購物','修正','2026-09-06')
        self.assertEqual(sp.month_report('a')['total'],200)
        action,_ = sp.undo('a')
        sp.undo('a',action)
        self.assertEqual(sp.month_report('a')['total'],150.25)
        with self.assertRaises(ValueError):
            sp.undo('a',action)
        action,_ = sp.undo('a')
        sp.undo('a',action)
        self.assertEqual(sp.month_report('a')['total'],0)
        self.assertEqual(sp.month_report('b')['record_count'],0)
        for value in ('NaN','Infinity','0','-10','1.001'):
            with self.assertRaises(ValueError):
                sp.add('a',value,'餐飲','bad')

    def test_budget_alerts_once_and_category_constraints(self):
        sp.set_budget('a','2026-09','總額','1000')
        sp.set_budget('a','2026-09','餐飲','600')
        with self.assertRaises(ValueError):
            sp.set_budget('a','2026-09','交通','500')
        with self.assertRaises(ValueError):
            sp.set_budget('a','2026-09','總額','500')
        sp.add('a','800','其他','test')
        self.assertEqual(len(sp.notices('a')),1)
        notice = sp.notices('a')[0]
        sp.delivered(notice['id'],'b')
        self.assertEqual(len(sp.notices('a')),1)
        sp.delivered(notice['id'],'a')
        sp.add('a','100','其他','test')
        self.assertEqual(sp.notices('a'),[])
        sp.add('a','200','其他','test')
        self.assertEqual(len(sp.notices('a')),1)
        self.assertIn('100%',sp.notices('a')[0]['body'])

    def test_recurring_restart_catchup_finish_and_undo(self):
        key = sp.add_recurring('a','分期','筆電','3000','購物','2026-09',3,5)
        self.assertEqual(sp.sync_recurring('a'),1)
        self.assertEqual(sp.sync_recurring('a'),0)
        self.assertEqual(sp.month_report('a')['fixed'],3000)
        self.assertEqual(sp.sync_recurring('a',date(2026,12,5)),2)
        self.assertEqual(sp.sync_recurring('a',date(2027,1,1)),0)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')),3)
        action,_ = sp.undo('a')
        sp.undo('a',action)
        self.assertEqual(sp.sync_recurring('a',date(2027,1,1)),0)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses WHERE voided=0')),2)

    def test_stop_preserves_existing_and_future_start(self):
        key = sp.add_recurring('a','訂閱','影音','390','娛樂','2026-10')
        self.assertEqual(sp.sync_recurring('a'),0)
        with self.assertRaises(ValueError):
            sp.stop_recurring('b',key)
        sp.stop_recurring('a',key)
        self.assertEqual(sp.sync_recurring('a',date(2026,11,1)),0)
        for start in ('2026-08','2026-12'):
            with self.assertRaises(ValueError):
                sp.add_recurring('a','固定','房租','8000','居住',start)

    def test_trend_equal_days_and_share_not_amount(self):
        sp.add('a','100','餐飲','本月','2026-09-01')
        sp.add('a','100','其他','本月','2026-09-02')
        sp.add('a','100','餐飲','前月','2026-08-01')
        sp.add('a','300','其他','前月','2026-08-02')
        sp.add('a','999','其他','排除','2026-08-08')
        data = sp.trends('a','月',3)
        self.assertEqual(data['periods'][1]['end'],'2026-08-07')
        change = next(c for c in data['latest_changes'] if c['category']=='餐飲')
        self.assertEqual(change['amount_change'],0)
        self.assertEqual(change['share_percentage_point_change'],25)
        self.assertFalse(data['periods'][2]['has_records'])
        weekly = sp.trends('a','週',3)
        self.assertEqual(weekly['periods'][0]['start'],weekly['periods'][0]['end'])

    def test_short_month_and_no_records_unknown(self):
        with patch('spending.today',return_value=date(2026,3,31)):
            data = sp.trends('a','月',3)
        self.assertEqual(data['periods'][0]['end'],'2026-03-28')
        self.assertTrue(all(x['share_percentage_point_change'] is None for x in data['latest_changes']))

    def test_clear_is_independent_and_migration_preserves_data(self):
        sp.add('a',100,'餐飲','test')
        sp.add('b',200,'餐飲','test')
        with sp.transaction() as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('a','2330',900,10)")
        db.init_db()
        sp.clear('a')
        self.assertEqual(sp.month_report('a')['record_count'],0)
        self.assertEqual(sp.month_report('b')['total'],200)
        self.assertEqual(len(sp.rows('SELECT * FROM assets')),1)

    def test_query_plan_rejects_arbitrary_fields_and_write_intent(self):
        plan = dict(intent='spending',month='2026-09',category='餐飲',unit='月',count=3)
        self.assertEqual(query_plan.validate(json.dumps(plan)),plan)
        for change in ({'user_id':'b'},{'intent':'delete'},{'count':100},{'month':'2026-99'}):
            with self.assertRaises(ValueError):
                query_plan.validate(json.dumps({**plan,**change}))
