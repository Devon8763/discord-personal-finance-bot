import unittest
from privacy_rules import local_ollama_url,can_run_in_channel


class PrivacyTests(unittest.TestCase):
    def test_private_commands(self):
        self.assertFalse(can_run_in_channel('buy',object()))
        self.assertFalse(can_run_in_channel('月報',object()))
        self.assertTrue(can_run_in_channel('月報',None))
        self.assertTrue(can_run_in_channel('help',object()))

    def test_local_endpoint_only(self):
        for url in ('http://127.0.0.1:11434','http://localhost:11434/','http://[::1]:11434'):
            self.assertEqual(local_ollama_url(url),url.rstrip('/'))
        for url in ('https://example.com','http://127.0.0.1.evil.com','http://user:pass@localhost','ftp://localhost','http://localhost/path','http://localhost?next=elsewhere'):
            with self.assertRaises(ValueError):
                local_ollama_url(url)
