import unittest
from presentation import number


class PresentationTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(number(150),'150')
        self.assertEqual(number('1234.50'),'1,234.5')
        self.assertEqual(number(-150,True),'-150')
        self.assertEqual(number(150,True),'+150')
        self.assertEqual(number(0),'0')
