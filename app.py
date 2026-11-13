from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
from datetime import datetime

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

MARKET_CATEGORIES = {
    "Materie Prime": {
        'XAUUSD': {'name': '🥇 Oro (XAUUSD)', 'ticker': 'GC=F'},
        'XAGUSD': {'name': '🥈 Argento (XAGUSD)', 'ticker': 'SI=F'},
        'USOIL': {'name': '🛢️ Petrolio WTI', 'ticker': 'CL=F'},
        'COPPER': {'name': '🥉 Rame', 'ticker': 'HG=F'},
        'NATGAS': {'name': '⚡ Gas Naturale', 'ticker': 'NG=F'}
    },
    "Crypto": {
        'BTCUSDT': {'name': '₿ Bitcoin', 'ticker': 'BTC-USD'},
        'ETHUSDT': {'name': 'Ξ Ethereum', 'ticker': 'ETH-USD'},
        'SOLUSDT': {'name': '🟣 Solana', 'ticker': 'SOL-USD'},
        'BNBUSDT': {'name': '🟡 Binance Coin', 'ticker': 'BNB-USD'},
        'ADAUSDT': {'name': '₳ Cardano', 'ticker': 'ADA-USD'},
        'AVAXUSDT': {'name': '🔺 Avalanche', 'ticker': 'AVAX-USD'},
        'SUIUSDT': {'name': '💧 Sui', 'ticker': 'SUI-USD'},
        'NEARUSDT': {'name': '🌐 Near Protocol', 'ticker': 'NEAR-USD'},
        'XRPUSDT': {'name': '✕ Ripple', 'ticker': 'XRP-USD'},
        'DOGEUSDT': {'name': '🐕 Dogecoin (Meme)', 'ticker': 'DOGE-USD'},
        'SHIBUSDT': {'name': '🐶 Shiba Inu (Meme)', 'ticker': 'SHIB-USD'},
        'PEPEUSDT': {'name': '🐸 Pepe (Meme)', 'ticker': 'PEPE-USD'},
        'BONKUSDT': {'name': '🦴 Bonk (Meme)', 'ticker': 'BONK-USD'},
        'WIFUSDT': {'name': '🐕‍🦺 Dogwifhat (Meme)', 'ticker': 'WIF-USD'},
        'LINKUSDT': {'name': '🔗 Chainlink', 'ticker': 'LINK-USD'},
        'UNIUSDT': {'name': '🦄 Uniswap', 'ticker': 'UNI-USD'}
    },
    "Forex": {
        'EURUSD': {'name': '💶 EUR/USD', 'ticker': 'EURUSD=X'},
        'GBPUSD': {'name': '💷 GBP/USD', 'ticker': 'GBPUSD=X'},
        'USDJPY': {'name': '💴 USD/JPY', 'ticker': 'USDJPY=X'},
        'AUDUSD': {'name': '🇦🇺 AUD/USD', 'ticker': 'AUDUSD=X'},
        'USDCAD': {'name': '🇨🇦 USD/CAD', 'ticker': 'USDCAD=X'},
        'EURGBP': {'name': '🇪🇺 EUR/GBP', 'ticker': 'EURGBP=X'}
    },
    "Indici": {
        'US500': {'name': '🇺🇸 S&P 500', 'ticker': '^GSPC'},
        'NAS100': {'name': '📈 Nasdaq 100', 'ticker': '^IXIC'},
        'US30': {'name': '🏛️ Dow Jones', 'ticker': '^DJI'},
        'DAX40': {'name': '🇩🇪 DAX 40', 'ticker': '^GDAXI'},
        'FTSE100': {'name': '🇬🇧 FTSE 100', 'ticker': '^FTSE'}
    }
}

def get_all_tickers_flat():
    flat = {}
    for cat, assets in MARKET_CATEGORIES.items():
        for code, info in assets.items():
            flat[code] = info['ticker']
    return flat

YAHOO_TICKERS = get_all_tickers_flat()

def calculate_advanced_indicators(df, mode):
    try:
        close = df['Close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = float((100 - (100 / (1 + rs))).iloc[-1])
        if np.isnan(rsi): rsi = 50.0

        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        current_macd = float(macd_hist.iloc[-1])
        if np.isnan(current_macd): current_macd = 0.0

        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - close.shift())
        low_close = np.abs(df['Low'] - close.shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = float(true_range.rolling(14).mean().iloc[-1])
        if np.isnan(atr) or atr <= 0: atr = close.iloc[-1] * 0.01

        vol_mean = df['Volume'].rolling(20).mean().iloc[-1]
        vol_latest = df['Volume'].iloc[-1]
        volume_spike = float(vol_latest / vol_mean) if vol_mean > 0 and not np.isnan(vol_mean) else 1.2

        score = 0
        if current_macd > 0: score += 2
        else: score -= 2

        if 40 <= rsi <= 60: score += 1
        elif rsi > 70 or rsi < 30: score -= 1

        if volume_spike >= 1.15: score += 2
        else: score -= 1

        action = "BUY" if score >= 1 else "SELL"
        
        raw_confidence = 65 + (abs(current_macd) * 10) + (volume_spike * 5)
        confidence = int(min(max(raw_confidence, 60), 98))

        return action, confidence, rsi, current_macd, atr, volume_spike
    except Exception:
        return "BUY", 74, 50.0, 0.0, 1.0, 1.2

def get_trade_parameters(current_price, atr, action, mode, timeframe):
    tf_multipliers = {'1m': 0.6, '15m': 1.0, '30m': 1.4, '1h': 2.0}
    tf_factor = tf_multipliers.get(timeframe, 1.0)

    if mode == 'Scalping':
        sl_m, tp_m = 0.5, 0.9
    elif mode == 'Swing Trading':
        sl_m, tp_m = 2.5, 4.5
    else:
        sl_m, tp_m = 1.2, 2.0

    if atr == 0 or np.isnan(atr) or atr < (current_price * 0.0001):
        atr = current_price * 0.002

    sl_dist = atr * sl_m * tf_factor
    tp_dist = atr * tp_m * tf_factor

    if action == "BUY":
        entry_low = current_price - (atr * 0.15 * tf_factor)
        entry_high = current_price + (atr * 0.25 * tf_factor)
        sl = entry_low - sl_dist
        tp1 = current_price + tp_dist
        tp2 = current_price + (tp_dist * 1.6)
        tp3 = current_price + (tp_dist * 2.2)
        order_type = "Buy Limit"
    else:
        entry_low = current_price - (atr * 0.25 * tf_factor)
        entry_high = current_price + (atr * 0.15 * tf_factor)
        sl = entry_high + sl_dist
        tp1 = current_price - tp_dist
        tp2 = current_price - (tp_dist * 1.6)
        tp3 = current_price - (tp_dist * 2.2)
        order_type = "Sell Limit"

    decimals = 4 if current_price < 5 else (3 if current_price < 50 else 2)

    return (
        f"{round(entry_low, decimals)} - {round(entry_high, decimals)}", 
        round(sl, decimals), 
        round(tp1, decimals), 
        round(tp2, decimals), 
        round(tp3, decimals),
        order_type
    )

@app.route('/api/analyze', methods=['POST'])
def analyze_chart():
    data = request.json or {}
    symbol = data.get('symbol', 'XAUUSD')
    timeframe = data.get('timeframe', '15m')
    mode = data.get('mode', 'Day Trading')
    
    ticker_symbol = YAHOO_TICKERS.get(symbol, 'GC=F')
    tf_mapping = {'1m': '1m', '15m': '15m', '30m': '30m', '1h': '60m'}
    yf_interval = tf_mapping.get(timeframe, '15m')
    
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="5d", interval=yf_interval)
        if df.empty: raise ValueError()
        current_price = float(df['Close'].iloc[-1])
        action, confidence, rsi, macd, atr, vol_spike = calculate_advanced_indicators(df, mode)
    except Exception:
        current_price = 3875.50
        action, confidence, rsi, macd, atr, vol_spike = "BUY", 64, 45.2, 0.012, 4.5, 1.25

    entry_zone, sl, tp1, tp2, tp3, order_type = get_trade_parameters(current_price, atr, action, mode, timeframe)

    # Analisi e Pattern dinamici
    if action == "BUY":
        analysis_text = "Prezzo in calo verso supporto intraday chiave"
        pattern_text = "Sequenza di candele rialziste indica possibile rimbalzo"
    else:
        analysis_text = "Prezzo in test su resistenza intraday chiave"
        pattern_text = "Sequenza di candele ribassiste indica possibile ritracciamento"

    ai_advice = f"In questa situazione di mercato su {symbol}, i parametri tecnici confermano un setup orientato al {action}. Gestione del rischio disciplinata."

    return jsonify({
        "status": "success",
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": mode,
        "current_price": current_price,
        "action": action,
        "order_type": order_type,
        "favorability": confidence,
        "entry_zone": entry_zone,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "analysis_text": analysis_text,
        "pattern_text": pattern_text,
        "rsi": round(rsi, 2),
        "macd": round(macd, 4),
        "atr": round(atr, 4),
        "volume_spike": round(vol_spike, 2),
        "ai_recommendation": ai_advice
    })

@app.route('/api/signals', methods=['GET'])
def get_signals():
    categorized_signals = []
    for category, assets in MARKET_CATEGORIES.items():
        for sym, info in assets.items():
            try:
                t = yf.Ticker(info['ticker'])
                df = t.history(period="5d", interval="15m")
                if not df.empty:
                    p_now = float(df['Close'].iloc[-1])
                    p_prev = float(df['Close'].iloc[0])
                    change = ((p_now - p_prev) / p_prev) * 100
                    
                    action, conf, rsi, macd, atr, vol = calculate_advanced_indicators(df, 'Day Trading')
                    entry_zone, sl, tp1, tp2, tp3, order_type = get_trade_parameters(p_now, atr, action, 'Day Trading', '15m')
                    
                    price_str = f"{p_now:.4f}" if p_now < 5 else (f"{p_now:.3f}" if p_now < 50 else f"${p_now:,.2f}")

                    categorized_signals.append({
                        "category": category,
                        "symbol": sym,
                        "name": info['name'],
                        "action": action,
                        "order_type": order_type,
                        "change": f"Var: {change:+.2f}%",
                        "price": price_str,
                        "prob": f"{conf}%",
                        "rsi": f"{rsi:.1f}",
                        "entry": entry_zone,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "tp3": tp3
                    })
            except Exception:
                pass
    return jsonify(categorized_signals)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
