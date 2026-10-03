import builtins
import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ScraperStartupTests(unittest.TestCase):
    def test_import_defers_market_library_until_quote(self):
        original_import = builtins.__import__
        market = Mock()
        market.Ticker.return_value.history.return_value.empty = True
        calls = []

        def importing(name, *args, **kwargs):
            if name == 'yfinance':
                calls.append(name)
                return market
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=importing):
            module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scraper.py'))
            self.assertEqual(calls, [])
            self.assertIsNone(module['get_price']('2330'))
            self.assertEqual(calls, ['yfinance'])
            self.assertEqual([c.args[0] for c in market.Ticker.call_args_list], ['2330.TW', '2330.TWO'])
