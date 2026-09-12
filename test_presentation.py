import unittest
from presentation import number, json_response, summary_text, AIResponseError


class PresentationTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(number(150),'150')
        self.assertEqual(number('1234.50'),'1,234.5')
        self.assertEqual(number(-150,True),'-150')
        self.assertEqual(number(150,True),'+150')
        self.assertEqual(number(0),'0')

    def test_json_errors_are_friendly(self):
        for raw in ('','not json','[]','{"x":'):
            with self.assertRaises(AIResponseError) as caught:
                json_response(raw)
            self.assertNotIn('Expecting',str(caught.exception))
        self.assertEqual(json_response('```json\n{"category":"餐飲"}\n```'),{'category':'餐飲'})

    def test_summary_bounded_and_split(self):
        text = summary_text('{"points":["餐飲增加。","購物減少。","預算先維持。"]}')
        self.assertEqual(text.count('\n\n'),2)
        self.assertEqual(text.count('•'),3)
        self.assertNotIn('未記錄',summary_text('{"points":["未記錄不等於零。","餐飲最多。"]}'))
