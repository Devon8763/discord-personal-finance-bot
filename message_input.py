"""Process one command per line while retaining Discord's command parser."""
from copy import copy
from datetime import datetime, timedelta, timezone

BATCH_SPENDING_COMMANDS = {'!支出','!支出補登','!支出修改','!預算','!固定新增','!固定停用',
                           '!分類新增','!分類刪除','!提醒設定','!提醒重設'}


def command_lines(content):
    content = content.strip()
    lines = content.splitlines()
    if len(lines) >= 2 and lines[0].startswith('```') and lines[-1].strip() == '```':
        lines = lines[1:-1]
    lines = [line.strip() for line in lines if line.strip()]
    if not lines or not lines[0].startswith('!'):
        return []
    if len(lines) > 50:
        raise ValueError('每則訊息最多 50 行指令，請分批傳送；本批尚未執行。')
    if any(not line.startswith('!') for line in lines):
        raise ValueError('每行請放一條完整的 !指令，不要混入說明文字；本批尚未執行。')
    return lines


async def process_message(bot, message):
    if message.author.bot:
        return
    try:
        lines = command_lines(message.content)
    except ValueError as error:
        await message.channel.send(str(error))
        return
    lines = group_watch_lines(lines)
    index = 0
    while index < len(lines):
        line = lines[index]
        end = index + 1
        if (getattr(message, 'guild', None) is None and hasattr(bot, 'process_spending_batch')
                and line.split()[0] in BATCH_SPENDING_COMMANDS):
            while end < len(lines) and lines[end].split()[0] in BATCH_SPENDING_COMMANDS:
                end += 1
            if end-index > 1:
                await bot.process_spending_batch(message, lines[index:end])
                index = end
                continue
        single = copy(message)
        single.content = line
        await bot.process_commands(single)
        index += 1


def group_watch_lines(lines):
    """Combine only adjacent watch commands; retain intervening command order."""
    grouped = []
    for line in lines:
        parts = line.split(maxsplit=1)
        if (len(parts) == 2 and parts[0] == '!watch' and grouped
                and grouped[-1].split(maxsplit=1)[0] == '!watch'
                and len(grouped[-1].split(maxsplit=1)) == 2):
            grouped[-1] += ' ' + parts[1]
        else:
            grouped.append(line)
    return grouped


def display_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return '時間未知'
