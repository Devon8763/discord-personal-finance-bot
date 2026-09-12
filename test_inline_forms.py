import unittest
import discord
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction


from form_helpers import fill


class InlineFormTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    def check_payload(self,modal):
        payload=modal.to_dict()
        self.assertLessEqual(len(payload['components']),5)
        for row in payload['components']:
            self.assertEqual(row['type'],18) # Discord modal Label wrapper.
        self.assertTrue(any(r['component']['type']==3 for r in payload['components']))

    async def test_entry_budget_edit_all_use_one_modal(self):
        from dashboard import start_entry,EditExpenseModal
        i=interaction();await start_entry(self.view,i,'expense')
        i.response.send_message.assert_not_awaited()
        modal=i.response.send_modal.await_args.args[0]
        self.check_payload(modal)
        fill(modal.fields['category'],'餐飲');fill(modal.fields['payment'],'現金')
        fill(modal.fields['amount'],'80');fill(modal.fields['note'],'午餐')
        await modal.on_submit(i)
        row=sp.recent_expenses('42')[0]
        self.assertEqual(row['payment_source_name'],'現金')
        edit=EditExpenseModal(self.view,row);self.check_payload(edit)
        fill(edit.fields['payment'],'未指定');fill(edit.fields['category'],'交通')
        await edit.on_submit(i)
        self.assertEqual(sp.get_expense('42',row['id'])['payment_source_name'],'未指定')
        await start_entry(self.view,i,'budget')
        budget=i.response.send_modal.await_args.args[0];self.check_payload(budget)
        fill(budget.fields['amount'],'1000');await budget.on_submit(i)
        self.assertEqual(sp.month_report('42')['budgets'][0]['budget'],1000)

    async def test_shortcut_and_recurring_do_not_start_wizards(self):
        from lifestyle_ui import Shortcuts
        from spending_commands import RecurringModal
        i=interaction();picker=Shortcuts(self.view);await picker.new(i)
        modal=i.response.send_modal.await_args.args[0];self.check_payload(modal)
        fill(modal.fields['category'],'餐飲');fill(modal.fields['payment'],'現金')
        fill(modal.fields['name'],'早餐');fill(modal.fields['note'],'蛋餅')
        await modal.on_submit(i)
        await picker.chosen_shortcut(i,sp.shortcuts('42')[0]['id'])
        confirm=i.response.send_modal.await_args.args[0];self.check_payload(confirm)
        self.assertEqual(sp.recent_expenses('42'),[])
        fill(confirm.fields['amount'],'20');await confirm.on_submit(i)
        self.assertEqual(sp.month_report('42')['total'],20)
        recurring=RecurringModal(self.cog,owner=42);self.check_payload(recurring)
        self.assertIsInstance(recurring.category,discord.ui.Select)
        self.assertIsInstance(recurring.plan,discord.ui.Select)

    async def test_discord_label_submission_decodes_dropdown_values(self):
        from dashboard import EntryModal
        modal=EntryModal(self.view,'expense')
        values={'category':'餐飲','payment':'現金','amount':'125','note':'晚餐','date':'2026-09-09'}
        components=[]
        for key,field in modal.fields.items():
            if isinstance(field,discord.ui.Select):
                index=next(n for n,(label,value) in enumerate(field.choices) if label==values[key])
                payload={'type':3,'custom_id':field.custom_id,'values':[str(index)]}
            else:
                payload={'type':4,'custom_id':field.custom_id,'value':values[key]}
            components.append({'type':18,'component':payload})
        i=interaction();modal._refresh(i,components,{})
        await modal.on_submit(i)
        row=sp.recent_expenses('42')[0]
        self.assertEqual((row['cents'],row['payment_source_name'],row['spent_on']),(12500,'現金','2026-09-09'))

    async def test_overflow_preserves_draft_and_selected_item(self):
        from dashboard import EntryModal
        from form_ui import MORE
        for n in range(30): sp.set_category('42',f'分類{n:02}',True)
        modal=EntryModal(self.view,'expense')
        fill(modal.fields['amount'],'99');fill(modal.fields['note'],'保留內容')
        fill(modal.fields['category'],MORE)
        i=interaction();await modal.on_submit(i)
        picker=i.response.send_message.await_args.kwargs['view']
        self.assertFalse(await picker.interaction_check(interaction(99)))
        target=picker.items[-1][1]
        await picker.chosen(i,target)
        reopened=i.response.send_modal.await_args.args[0]
        self.assertEqual(reopened.fields['category'].value,target)
        self.assertEqual(reopened.fields['note'].value,'保留內容')
        self.assertLessEqual(len(reopened.fields['category'].options),25)
        self.assertEqual(sp.recent_expenses('42'),[])
        await reopened.on_submit(i)
        self.assertEqual(sp.recent_expenses('42')[0]['cents'],9900)

    async def test_edit_keeps_inactive_history_but_repeat_requires_active_source(self):
        from dashboard import EditExpenseModal
        from lifestyle_ui import ConfirmExpense
        source=sp.add_payment_source('42','舊卡')
        sp.add('42','90','餐飲','午餐',payment_source_id=source)
        row=sp.recent_expenses('42')[0]
        sp.disable_payment_source('42',source)
        edit=EditExpenseModal(self.view,row)
        fill(edit.fields['note'],'修改用途');await edit.on_submit(interaction())
        self.assertEqual(sp.get_expense('42',row['id'])['payment_source_name'],'舊卡')
        repeat=ConfirmExpense(self.view,row);i=interaction()
        await repeat.on_submit(i)
        self.assertEqual(len(sp.recent_expenses('42')),1)
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        fill(repeat.fields['payment'],'現金');await repeat.on_submit(interaction())
        self.assertEqual(len(sp.recent_expenses('42')),2)
