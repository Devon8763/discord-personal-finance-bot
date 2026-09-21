import unittest
from portfolio import normalize_symbol, positive, value_position


class ValuationTests(unittest.TestCase):
    def test_suffix(self):
        for symbol in ('6488.TWO', '6488.two', '6488'):
            self.assertEqual(normalize_symbol(symbol), '6488')
        self.assertEqual(normalize_symbol('brk-b'), 'BRK-B')

    def test_invalid_price(self):
        for price in (0, -1, float('nan'), float('inf'), None):
            self.assertFalse(positive(price))
            with self.assertRaises(ValueError):
                value_position('test', 1000, 10, price, 'TWD')

    def test_total_profit_includes_quantity(self):
        result = value_position('test', 1000, 10, 120, 'TWD')
        self.assertEqual(result['profit'], 200)
        self.assertEqual(result['percent'], 20)

    def test_loss(self):
        result = value_position('test', 1000, 10, 80, 'USD')
        self.assertEqual(result['profit'], -200)
        self.assertEqual(result['percent'], -20)


if __name__ == '__main__':
    unittest.main()
