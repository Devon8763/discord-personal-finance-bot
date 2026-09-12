from stock_name_map import STOCK_NAME_MAP

def get_price(symbol):
    try:
        import yfinance as yf

        display_symbol = symbol

        # 🔥 支援上市 + 上櫃
        if symbol.isdigit():
            query_symbols = [symbol + ".TW", symbol + ".TWO"]
        else:
            query_symbols = [symbol]

        stock = None
        data = None

        # 🔥 嘗試 TW / TWO
        for qs in query_symbols:
            stock = yf.Ticker(qs)
            data = stock.history(period="2d")

            if not data.empty:
                break

        # ❗ 完全抓不到（不存在）
        if data is None or data.empty:
            return None

        # 🔥 info 可能會壞 → 防呆
        try:
            info = stock.info
        except:
            info = {}

        name = STOCK_NAME_MAP.get(symbol) or info.get("shortName", symbol)
        currency = info.get("currency", "")

        # ❗ 有資料但不足兩天
        if len(data) < 2:
            return {
                "symbol": display_symbol,
                "name": name,
                "currency": currency,
                "price": "抓不到",
                "change": "-",
                "percent": "-"
            }

        price = round(data["Close"].iloc[-1], 2)
        prev_close = data["Close"].iloc[-2]

        change = round(price - prev_close, 2)
        percent = round((change / prev_close) * 100, 2)

        return {
            "symbol": display_symbol,
            "name": name,
            "currency": currency,
            "price": price,
            "change": change,
            "percent": percent
        }

    except Exception as e:
        print("get_price錯誤：", e)
        return None
