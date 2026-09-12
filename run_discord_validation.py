"""Run offline tests with the optional isolated validation dependencies."""
import sys
import unittest
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
# Prefer installed dependencies; fall back to an existing local package directory.
try:
    from discord.ext import commands
except ImportError:
    for directory in (root / '.venv/Lib/site-packages', root / '.validation-packages'):
        try:
            if (directory / 'discord/ext/commands/__init__.py').is_file():
                sys.path.insert(0,str(directory))
                break
        except OSError:
            continue
    # An inaccessible namespace package must not mask the real package.
    for name in list(sys.modules):
        if name == 'discord' or name.startswith('discord.'):
            del sys.modules[name]
    from discord.ext import commands

import db
production = Path(db.DB_NAME).resolve()
connect = db.get_conn
def isolated_connection():
    if Path(db.DB_NAME).resolve() == production:
        raise RuntimeError('測試禁止連線正式 data.db，請使用隔離資料庫')
    return connect()
db.get_conn = isolated_connection
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover(str(root/'tests')))
sys.exit(not result.wasSuccessful())
