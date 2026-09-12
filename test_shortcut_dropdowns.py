import unittest
import discord
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction, choose


from form_helpers import fill
from form_ui import MORE

async def selections(i, category='餐飲', payment='現金'):
    form=i.response.send_modal.await_args.args[0]
    fill(form.fields['category'],category)
    fill(form.fields['payment'],payment)
    return form


class ShortcutDropdownTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def test_create_edit_use_dropdowns_and_only_save_after_confirmation(self):
        from lifestyle_ui import Shortcuts
        picker=Shortcuts(self.view);i=interaction()
        await picker.new(i)
        i.response.send_message.assert_not_awaited()
        form=await selections(i)
        self.assertEqual(set(form.fields),{'name','category','payment','note','amount'})
        form.fields['name']._value='早餐';form.fields['note']._value='蛋餅'
        await form.on_submit(i)
        row=sp.shortcuts('42')[0]
        await picker.chosen_shortcut(i,row['id'])
        confirm=await selections(i,'交通','未指定')
        self.assertEqual(set(confirm.fields),{'amount','category','payment','date','note'})
        self.assertEqual(sp.recent_expenses('42'),[])
        confirm.fields['amount']._value='50'
        await confirm.on_submit(i)
        entry=sp.recent_expenses('42')[0]
        self.assertEqual((entry['category'],entry['payment_source_name']),('交通','未指定'))
        await picker.toggle(i);await picker.chosen_shortcut(i,row['id'])
        actions=i.response.edit_message.await_args.kwargs['view']
        await next(b for b in actions.children if b.label=='修改').callback(i)
        form=await selections(i,'購物','未指定')
        self.assertEqual(form.fields['note'].value,'蛋餅')
        await form.on_submit(i)
        self.assertEqual(sp.shortcut('42',row['id'])['category'],'購物')
        self.assertEqual(sp.get_expense('42',entry['id'])['category'],'交通')

    async def test_inline_additions_empty_categories_and_owner_checks(self):
        from lifestyle_ui import Shortcuts
        from selection_ui import NewCategory,NewPaymentSource
        for name in sp.category_names('42'): sp.set_category('42',name,False)
        picker=Shortcuts(self.view);i=interaction()
        await picker.new(i)
        form=i.response.send_modal.await_args.args[0]
        form.fields['name']._value='飼料';form.fields['note']._value='貓飼料'
        fill(form.fields['category'],MORE)
        await form.on_submit(i)
        categories=i.response.send_message.await_args.kwargs['view']
        self.assertFalse(await categories.interaction_check(interaction(43)))
        modal=NewCategory(categories);modal.name._value='寵物';await modal.on_submit(i)
        await choose(categories,'寵物').callback(i)
        form=i.response.send_modal.await_args.args[0]
        fill(form.fields['payment'],MORE);await form.on_submit(i)
        payments=i.response.send_message.await_args.kwargs['view']
        modal=NewPaymentSource(payments);modal.name._value='新卡';await modal.on_submit(i)
        source=next(p['id'] for p in sp.payment_sources('42') if p['name']=='新卡')
        await choose(payments,source).callback(i)
        form=i.response.send_modal.await_args.args[0]
        form.fields['name']._value='飼料';form.fields['note']._value='貓飼料'
        await form.on_submit(interaction(43))
        self.assertEqual(sp.shortcuts('42'),[])
        sp.disable_payment_source('42',source)
        await form.on_submit(i)
        self.assertEqual(sp.shortcuts('42'),[])
