import tempfile
import ast
import unittest
from pathlib import Path
from unittest.mock import patch
from config import load_token


class TokenTests(unittest.TestCase):
    def test_main_uses_config_loader(self):
        from unittest.mock import Mock
        tree = ast.parse(Path('bot.py').read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.If) and '__name__' in ast.unparse(n.test))
        module = ast.Module(body=[main], type_ignores=[])
        bot = Mock()
        init_db = Mock()
        with patch('config.load_token', return_value='fake-test-value') as loader:
            exec(compile(module, 'bot.py', 'exec'), {'__name__': '__main__', 'bot': bot, 'init_db': init_db})
        loader.assert_called_once_with()
        init_db.assert_called_once_with()
        bot.run.assert_called_once_with('fake-test-value')

    def test_persisted_token_and_environment_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('config.__file__', str(Path(directory) / 'config.py')):
                Path(directory, 'token.txt').write_text('local-test-value\n', encoding='utf-8-sig')
                with patch.dict('os.environ', {'DISCORD_TOKEN': ''}):
                    self.assertEqual(load_token(), 'local-test-value')
                with patch.dict('os.environ', {'DISCORD_TOKEN': 'environment-test-value'}):
                    self.assertEqual(load_token(), 'environment-test-value')

    def test_missing_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('config.__file__', str(Path(directory) / 'config.py')), patch.dict('os.environ', {'DISCORD_TOKEN': ''}):
                with self.assertRaises(ValueError):
                    load_token()
                for value in ('', 'Bot example', '"example"', 'one\ntwo'):
                    Path(directory, 'token.txt').write_text(value, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_token()
