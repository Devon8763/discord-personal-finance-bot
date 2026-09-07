"""Read the bot credential without embedding it in source code."""
import os
from pathlib import Path


def load_token():
    token = os.getenv('DISCORD_TOKEN', '').strip()
    if not token:
        path = Path(__file__).with_name('token.txt')
        if path.exists():
            token = path.read_text(encoding='utf-8-sig').strip()
    if not token:
        raise ValueError('尚未設定 Token。請在 bot.py 同一資料夾建立 token.txt，僅貼上完整 Bot Token 並儲存，再執行 python bot.py。')
    if any(character.isspace() for character in token) or token.startswith(('"', "'")):
        raise ValueError('Token 格式不正確：token.txt 只能放 Token 本身，不要加引號、Bot 前綴或其他文字。')
    return token
