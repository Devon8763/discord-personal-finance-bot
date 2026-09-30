import unittest
from privacy_rules import can_run_in_channel


class PrivacyTests(unittest.TestCase):
    def test_private_commands(self):
        self.assertFalse(can_run_in_channel('buy',object()))
        self.assertFalse(can_run_in_channel('月報',object()))
        self.assertTrue(can_run_in_channel('月報',None))
        self.assertTrue(can_run_in_channel('help',object()))
