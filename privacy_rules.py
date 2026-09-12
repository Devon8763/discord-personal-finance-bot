"""Safe defaults for shared-server operation and local model access."""
from urllib.parse import urlsplit
import ipaddress

PUBLIC_COMMANDS={'help','guide','privacy','ping','記帳說明'}


def can_run_in_channel(command,guild):
    return guild is None or command in PUBLIC_COMMANDS


async def private_interaction(interaction):
    if getattr(interaction,'guild',None) is None:return True
    await interaction.response.send_message('財務與資料操作僅限 Bot 私訊；請私訊 Bot 後輸入 !help。',ephemeral=True)
    return False


def local_ollama_url(url):
    parsed=urlsplit(url)
    host=parsed.hostname
    try:
        local=host=='localhost' or ipaddress.ip_address(host).is_loopback
    except (ValueError,TypeError):
        local=False
    if (parsed.scheme not in ('http','https') or not local or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('','/')):
        raise ValueError('本地 AI 僅允許 localhost／127.0.0.1／::1 位址，請搭配已下載的本地模型。')
    return url.rstrip('/')
