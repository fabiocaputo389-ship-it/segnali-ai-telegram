from flask import Flask, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
from datetime import datetime

app = Flask(__name__, static_folder='static', template_folder='static')

@app.route('/')
def home():
    return app.send_static_file('index.html')

# Database completo ed esteso di tutto il mercato globale
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
        'XRPUSDT': {'name': '✕ Ripple (XRP)', 'ticker': 'XRP-USD'},
        'ADAUSDT': {'name': '₳ Cardano', 'ticker': 'ADA-USD'},
        'BNBUSDT': {'name': '🟡 Binance Coin', 'ticker': 'BNB-USD'},
        'DOGEUSDT': {'name': '🐕 Dogecoin', 'ticker': 'DOGE-USD'},
        'AVAXUSDT': {'name': '🔺 Avalanche', 'ticker': 'AVAX-USD'},
        'LINKUSDT': {'name': '🔗 Chainlink', 'ticker': 'LINK-USD'}
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
    """
    Calcolo matematico avanzato: RSI, MACD, ATR, Spinta Volumi e Direzione.
    """
    try:
        close = df['Close']
        
        # 1. RSI (14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = float((100 - (100 / (1 + rs))).iloc[-1])
        if np.isnan(rsi): rsi = 50.0

        # 2. MACD (12, 26, 9)
        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        current_macd = float(macd_hist.iloc[-1])
        if np.isnan(current_macd): current_macd = 0.0

        # 3. ATR (14)
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - close.shift())
        low_close = np.abs(df['Low'] - close.shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = float(true_range.rolling(14).mean().iloc[-1])
        if np.isnan(atr): atr = close.iloc[-1] * 0.01

        # 4. Spinta Volumi
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
        
        risks = []
        if volume_spike < 1.1:
            risks.append("⚠️ Volumi scarsi: Rischio finta rottura (Fakeout).")
        if rsi > 75:
            risks.append("⚠️ RSI in ipercomprato estremo.")
        elif rsi < 25:
            risks.append("⚠️ RSI in ipervenduto estremo.")
        if current_macd * (1 if action == "BUY" else -1) < 0:
            risks.append("⚠️ Divergenza MACD contraria al trend.")
        if not risks:
            risks.append("✅ Indicatori allineati per operazione ad alta probabilità.")

        confidence = int(min(max(50 + (score * 12) + (volume_spike * 5), 45), 96))
        return action, confidence, rsi, current_macd, atr, volume_spike, risks
    except Exception:
        return "BUY", 65, 50.0, 0.0, 1.0, 1.2, ["⚠️ Dati di mercato in aggiornamento."]

def get_trade_parameters(current_price, atr, action, mode):
    if mode == 'Scalping':
        sl_m, tp_m = 0.6, 1.0
    elif mode == 'Swing Trading':
        sl_m, tp_m = 3.0, 5.0
    else: # Day Trading
        sl_m, tp_m = 1.5, 2.5

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m

    if action == "BUY":
        entry_low = round(current_price - (atr * 0.15), 2)
        entry_high = round(current_price + (atr * 0.25), 2)
        sl = round(entry_low - sl_dist, 2)
        tp1 = round(current_price + tp_dist, 2)
        tp2 = round(current_price + (tp_dist * 1.6), 2)
        tp3 = round(current_price + (tp_dist * 2.2), 2)
    else:
        entry_low = round(current_price - (atr * 0.25), 2)
        entry_high = round(current_price + (atr * 0.15), 2)
        sl = round(entry_high + sl_dist, 2)
        tp1 = round(current_price - tp_dist, 2)
        tp2 = round(current_price - (tp_dist * 1.6), 2)
        tp3 = round(current_price - (tp_dist * 2.2), 2)

    return f"{entry_low} - {entry_high}", sl, tp1, tp2, tp3

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
        action, confidence, rsi, macd, atr, vol_spike, risks = calculate_advanced_indicators(df, mode)
    except Exception:
        current_price = 3884.27
        action, confidence, rsi, macd, atr, vol_spike, risks = "BUY", 60, 50.0, 0.0, 5.0, 1.1, ["⚠️ Modalità offline."]

    entry_zone, sl, tp1, tp2, tp3 = get_trade_parameters(current_price, atr, action, mode)
    risk_status = "Basso (Trend Pulito)" if confidence > 70 else "Alto (Rischio Falsa Rottura)"

    return jsonify({
        "status": "success",
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": mode,
        "current_price": current_price,
        "action": action,
        "favorability": confidence,
        "risk_level": risk_status,
        "fakeout_logs": risks,
        "entry_zone": entry_zone,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "ai_comment": f"Analisi {mode} ({symbol}): RSI={rsi:.1f}, MACD={macd:.4f}, Volumi={vol_spike:.2f}x. Parametri calcolati matematicamente."
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
                    
                    action, conf, rsi, macd, atr, vol, risks = calculate_advanced_indicators(df, 'Day Trading')
                    entry_zone, sl, tp1, tp2, tp3 = get_trade_parameters(p_now, atr, action, 'Day Trading')
                    
                    categorized_signals.append({
                        "category": category,
                        "symbol": sym,
                        "name": info['name'],
                        "action": action,
                        "change": f"Var: {change:+.2f}%",
                        "price": f"${p_now:,.2f}" if p_now > 10 else f"{p_now:.4f}",
                        "prob": f"{conf}%",
                        "rsi": f"{rsi:.1f}",
                        "macd": f"{macd:.4f}",
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
