"""Compact user-facing numbers and one shared coverage note."""
from decimal import Decimal

NOTE = 'ℹ️ 僅依已記錄資料統計。'


def number(value, signed=False):
    result = format(Decimal(str(value)), '+,.2f' if signed else ',.2f')
    return result.rstrip('0').rstrip('.')


def discord_spending_error(error):
    """Keep existing command instructions at the Discord presentation boundary."""
    text = str(error)
    if text.startswith('分類請選：'):
        return text + '；可用 !分類新增 建立'
    return {
        '日常支出不能填未來日期': '日常支出不能填未來日期；固定負擔請用 !固定新增',
        '請設定1～10個整數百分比，範圍1～1000': '請設定1～10個整數百分比，範圍1～1000，例如 !提醒設定 50 80 100',
        '操作已變動，請重新確認': '操作已變動，請重新輸入 !記帳撤銷',
        '尚未到此月份；請查看未來固定負擔': '尚未到此月份；未來固定負擔請查看 !固定清單',
    }.get(text, text)
