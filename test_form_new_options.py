import unittest
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction
from form_helpers import fill
from dashboard import EntryModal


class NewOptionTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def add_option(self,form,key,name):
        label='＋新增分類' if key=='category' else '＋新增付款來源'
        fill(form.fields[key],label)
        i=interaction();await form.on_submit(i)
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        view=i.response.send_message.await_args.kwargs['view']
        button=next(b for b in view.children if b.label==label)
        stranger=interaction(43);await button.callback(stranger)
        stranger.response.send_modal.assert_not_awaited()
        await button.callback(i)
        modal=i.response.send_modal.await_args.args[0]
        fill(modal.name,name)
        await modal.on_submit(interaction(43))
        await modal.on_submit(i)
        result=i.response.edit_message.await_args.kwargs['view']
        self.assertEqual(sp.recent_expenses('42'),[])
        self.assertEqual(sp.recent_expenses('43'),[])
        back=next(b for b in result.children if b.label=='返回原表單')
        stranger=interaction(43);await back.callback(stranger)
        stranger.response.send_modal.assert_not_awaited()
        await back.callback(i)
        return i.response.send_modal.await_args.args[0]

    async def test_add_category_and_payment_preserve_draft_and_select_new_values(self):
        form=EntryModal(self.view,'expense')
        fill(form.fields['amount'],'85.5');fill(form.fields['note'],'早餐')
        fill(form.fields['date'],'2026-09-08')
        fill(form.fields['payment'],'＋新增付款來源')
        form=await self.add_option(form,'category','早午餐')
        self.assertEqual(form.fields['category'].value,'早午餐')
        form=await self.add_option(form,'payment','交通卡')
        source=next(p for p in sp.payment_sources('42') if p['name']=='交通卡')
        self.assertEqual(form.fields['payment'].value,source['id'])
        self.assertEqual(form.fields['note'].value,'早餐')
        self.assertEqual(form.fields['date'].value,'2026-09-08')
        self.assertNotIn('早午餐',sp.category_names('43'))
        self.assertNotIn('交通卡',[p['name'] for p in sp.payment_sources('43')])
        await form.on_submit(interaction())
        row=sp.recent_expenses('42')[0]
        self.assertEqual((row['cents'],row['category'],row['payment_source_name']),(8550,'早午餐','交通卡'))

    async def test_new_option_visible_with_empty_or_large_lists(self):
        from form_ui import FormSelect
        for size in (0,1,23,24,25,40):
            for kind,label in (('category','＋新增分類'),('payment','＋新增付款來源')):
                field=FormSelect([(str(n),n) for n in range(size)],default=size-1,extend=kind)
                self.assertLessEqual(len(field.options),25)
                self.assertIn(label,[o.label for o in field.options])
                if size:self.assertEqual(field.value,size-1)

    async def test_added_payment_still_revalidated_before_expense_saved(self):
        form=EntryModal(self.view,'expense')
        fill(form.fields['amount'],'80');fill(form.fields['note'],'午餐')
        fill(form.fields['category'],'餐飲')
        form=await self.add_option(form,'payment','新支付')
        sp.disable_payment_source('42',form.fields['payment'].value)
        await form.on_submit(interaction())
        self.assertEqual(sp.recent_expenses('42'),[])
