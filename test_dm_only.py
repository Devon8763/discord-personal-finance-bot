import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
import test_phase1_ui as fixtures
from test_phase1_ui import interaction


class DMOnlyTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def test_dashboard_and_shared_views_modals_refuse_guild(self):
        from dashboard import open_dashboard
        from selection_ui import OwnedView,SafeModal
        i=interaction();i.guild=SimpleNamespace(id=1)
        self.cog.prepare=AsyncMock()
        await open_dashboard(self.cog,i)
        self.cog.prepare.assert_not_awaited()
        self.assertFalse(await self.view.interaction_check(i))
        self.assertFalse(await OwnedView(42).interaction_check(i))
        self.assertFalse(await SafeModal(title='測試').interaction_check(i))
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        from spending_commands import RecurringKindView
        await RecurringKindView(self.cog).choose(i,'訂閱')
        i.response.send_modal.assert_not_awaited()

    async def test_financial_commands_dm_only(self):
        from privacy_rules import can_run_in_channel
        for name in ('price','fund','支出','問','分析','生活清除','portfolio'):
            self.assertFalse(can_run_in_channel(name,object()))
            self.assertTrue(can_run_in_channel(name,None))
