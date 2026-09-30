"""Safe defaults for shared-server operation."""

PUBLIC_COMMANDS={'help','guide','privacy','ping','記帳說明'}


def can_run_in_channel(command,guild):
    return guild is None or command in PUBLIC_COMMANDS


async def private_interaction(interaction):
    if getattr(interaction,'guild',None) is None:return True
    await interaction.response.send_message('財務與資料操作僅限 Bot 私訊；請私訊 Bot 後輸入 !help。',ephemeral=True)
    return False
