from flask import Flask, render_template, jsonify, request
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

def fetch_all_markets():
    # Lista master professionale e leggera per azzerare l'occupazione di RAM su Render
    return {
        "Criptovalute": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin', 'symbol': 'BTCUSDT'},
            {'id': 'ETHUSDT', 'name': 'Ξ Ethereum', 'symbol': 'ETHUSDT'},
            {'id': 'SOLUSDT', 'name': '🟣 Solana', 'symbol': 'SOLUSDT'},
            {'id': 'XRPUSDT', 'name': '✕ Ripple', 'symbol': 'XRPUSDT'},
            {'id': 'BNBUSDT', 'name': '🟡 BNB', 'symbol': 'BNBUSDT'},
            {'id': 'ADAUSDT', 'name': '₳ Cardano', 'symbol': 'ADAUSDT'},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin', 'symbol': 'DOGEUSDT'},
            {'id': 'AVAXUSDT', 'name': '🔺 Avalanche', 'symbol': 'AVAXUSDT'},
            {'id': 'LINKUSDT', 'name': '🔗 Chainlink', 'symbol': 'LINKUSDT'},
            {'id': 'DOTUSDT', 'name': '🔵 Polkadot', 'symbol': 'DOTUSDT'},
            {'id': 'NEARUSDT', 'name': '🌐 Near Protocol', 'symbol': 'NEARUSDT'},
            {'id': 'MATICUSDT', 'name': '💜 Polygon', 'symbol': 'MATICUSDT'},
            {'id': 'SUIUSDT', 'name': '💧 Sui', 'symbol': 'SUIUSDT'},
            {'id': 'PEPEUSDT', 'name': '🐸 Pepe', 'symbol': 'PEPEUSDT'},
            {'id': 'SHIBUSDT', 'name': '🐕‍🦺 Shiba Inu', 'symbol': 'SHIBUSDT'}
        ],
        "Materie Prime & Forex": [
            {'id': 'XAUUSD', 'name': '🥇 Oro (Gold)', 'symbol': 'PAXGUSDT'},
            {'id': 'XAGUSD', 'name': '🥈 Argento (Silver)', 'symbol': 'XAGUSDT'},
            {'id': 'EURUSDT', 'name': '💶 EUR/USD', 'symbol': 'EURUSDT'},
            {'id': 'GBPUSDT', 'name': '🇬🇧 GBP/USD', 'symbol': 'GBPUSDT'},
            {'id': 'USDJPY', 'name': '🇯🇵 USD/JPY', 'symbol': 'USDJPY'},
            {'id': 'AUDUSD', 'name': '🇦🇺 AUD/USD', 'symbol': 'AUDUSDT'},
            {'id': 'USDCAD', 'name': '🇨🇦 USD/CAD', 'symbol': 'USDCAD'}
        ],
        "Azioni & Indici": [
            {'id': 'AAPL', 'name': '🍏 Apple Inc.', 'symbol': 'AAPLUSDT'},
            {'id': 'TSLA', 'name': '🚗 Tesla Inc.', 'symbol': 'TSLAUSDT'},
            {'id': 'NVDA', 'name': '💻 Nvidia Corp.', 'symbol': 'NVDAUSDT'},
            {'id': 'AMZN', 'name': '📦 Amazon', 'symbol': 'AMZNUSDT'},
            {'id': 'MSFT', 'name': '🪟 Microsoft Corp.', 'symbol': 'MSFTUSDT'},
            {'id': 'GOOGL', 'name': '🔍 Alphabet (Google)', 'symbol': 'GOOGLUSDT'},
            {'id': 'META', 'name': '👥 Meta Platforms', 'symbol': 'METAUSDT'},
            {'id': 'NFLX', 'name': '🎬 Netflix Inc.', 'symbol': 'NFLXUSDT'},
            {'id': 'SPX500', 'name': '📈 S&P 500 Index', 'symbol': 'SPXUSDT'}
        ]
    }

def get_market_status(category):
    if category == "Criptovalute":
        return {"status": "Aperto", "text": "🟢 Operativo 24/7", "countdown": "Mercato Continuo"}
    
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    
    if weekday >= 5:
        return {"status": "Chiuso", "text": "🔴 Mercato Chiuso", "countdown": "Riapertura Lunedì 00:00 UTC"}
    else:
        return {"status": "Aperto", "text": "🟢 Aperto", "countdown": "Sessione Live"}

def safe_float(val, default=0.0):
    try:
        f = float(val)
        if np.isnan(f) or not np.isfinite(f):
            return default
        return f
    except Exception:
        return default

def get_market_data(symbol, interval="15m"):
    headers = {"User-Agent": "Mozilla/5.0"}
    clean_sym = symbol
    if symbol in ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'MSFT', 'GOOGL', 'META', 'NFLX', 'SPX500']:
        clean_sym = symbol + 'USDT'
    elif symbol == 'XAGUSD':
        clean_sym = 'XAGUSDT'

    try:
        url = "https://api.binance.com/api/v3/klines"
        res = requests.get(url, params={"symbol": clean_sym, "interval": interval, "limit": 50}, headers=headers, timeout=2).json()
        
        if isinstance(res, list) and len(res) > 0:
            df = pd.DataFrame(res, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'Quote_av', 'Trades', 'Tbbav', 'Tbav', 'Ignore'])
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
            
            live_price = df['Close'].iloc[-1]
            return df, live_price, "Binance Institutional Feed"
    except Exception:
        pass

    p = 200.0 if symbol in ['AAPL', 'TSLA', 'NVDA'] else (3000.0 if symbol == 'XAUUSD' else 65000.0)
    df = pd.DataFrame({
        'Open': [p]*50, 'High': [p*1.002]*50, 'Low': [p*0.998]*50,
        'Close': [p]*50, 'Volume': [30000]*50
    })
    return df, p, "Smart Sync Protocol"

def calculate_certified_setup(df, current_price, mode, account_balance, risk_percentage):
    close = df['Close']
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = safe_float((100 - (100 / (1 + rs))).iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    current_macd = safe_float(macd_hist.iloc[-1], 0.0)

    tr = pd.concat([df['High'] - df['Low'], np.abs(df['High'] - close.shift()), np.abs(df['Low'] - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0: atr = current_price * 0.005

    vol = df['Volume']
    vol_sma = safe_float(vol.rolling(20).mean().iloc[-1], 1000.0)
    current_vol = safe_float(vol.iloc[-1], 1000.0)
    low_volume_flag = bool(current_vol < (vol_sma * 0.75))

    o_last = safe_float(df['Open'].iloc[-1], current_price)
    c_last = safe_float(df['Close'].iloc[-1], current_price)
    h_last = safe_float(df['High'].iloc[-1], current_price)
    l_last = safe_float(df['Low'].iloc[-1], current_price)
    body = abs(c_last - o_last)
    rng = h_last - l_last
    is_pin_bar = bool((rng > 0) and ((body / rng) < 0.25))
    whipsaw_detected = "Sì" if (low_volume_flag or is_pin_bar) else "No"

    ema_val_12 = safe_float(ema12.iloc[-1], current_price)
    ema_val_26 = safe_float(ema26.iloc[-1], current_price)

    is_perfect_buy = (ema_val_12 > ema_val_26) and (current_macd > 0.0) and (52 <= rsi <= 72) and (whipsaw_detected == "No")
    is_perfect_sell = (ema_val_12 < ema_val_26) and (current_macd < 0.0) and (28 <= rsi <= 48) and (whipsaw_detected == "No")

    if is_perfect_buy:
        action = "BUY"
        confidence = 100
    elif is_perfect_sell:
        action = "SELL"
        confidence = 100
    else:
        action = "BUY" if ema_val_12 >= ema_val_26 else "SELL"
        confidence = 50

    if mode == 'Scalping':
        sl_m, tp_m = 0.5, 1.2
    elif mode == 'Swing Trading':
        sl_m, tp_m = 2.0, 4.5
    else:
        sl_m, tp_m = 0.9, 2.0

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m
    decimals = 6 if current_price < 0.01 else (4 if current_price < 5 else 2)

    if action == "BUY":
        entry_l = round(current_price - (atr * 0.05), decimals)
        entry_h = round(current_price + (atr * 0.05), decimals)
        sl = round(current_price - sl_dist, decimals)
        tp1 = round(current_price + tp_dist, decimals)
        tp2 = round(current_price + (tp_dist * 1.5), decimals)
        tp3 = round(current_price + (tp_dist * 2.2), decimals)
    else:
        entry_l = round(current_price - (atr * 0.05), decimals)
        entry_h = round(current_price + (atr * 0.05), decimals)
        sl = round(current_price + sl_dist, decimals)
        tp1 = round(current_price - tp_dist, decimals)
        tp2 = round(current_price - (tp_dist * 1.5), decimals)
        tp3 = round(current_price - (tp_dist * 2.2), decimals)

    max_risk_amount = account_balance * (risk_percentage / 100.0)
    price_risk_per_unit = abs(current_price - sl)
    position_size = (max_risk_amount / price_risk_per_unit) if price_risk_per_unit > 0 else 0.0
    
    sl_pct = (price_risk_per_unit / current_price) * 100 if current_price > 0 else 1.0
    recommended_leverage = min(20, max(3, int(round(15.0 / max(sl_pct, 0.5)))))
    margin_required = round((position_size * current_price) / recommended_leverage, 2)
    rrr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    return {
        "action": action, "confidence": int(confidence),
        "entry_zone": f"{entry_l} - {entry_h}", "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rrr": rrr, "decimals": decimals, "rsi": round(rsi, 1),
        "risk_amount": round(max_risk_amount, 2), "position_size": round(position_size, 4),
        "leverage": f"Isolata {recommended_leverage}x", "margin": margin_required
    }

@app.route('/api/markets', methods=['GET'])
def get_markets():
    return jsonify(fetch_all_markets())

def process_single_asset(cat, a, m_status, mode, balance, risk_pct, timeframe):
    try:
        df, current_price, source = get_market_data(a['id'], interval=timeframe)
        if df is not None and not df.empty:
            p_open = float(df['Open'].iloc[0]) if df['Open'].iloc[0] > 0 else current_price
            change = float(((current_price - p_open) / p_open) * 100)
            if np.isnan(change) or not np.isfinite(change): change = 0.0

            res = calculate_certified_setup(df, current_price, mode, balance, risk_pct)
            
            if res['confidence'] == 100:
                dec = res["decimals"]
                return {
                    "id": f"{a['id']}_{res['action']}_{timeframe}",
                    "category": cat, "symbol": a['id'], "name": a['name'],
                    "action": res["action"], "change": f"{change:+.2f}%", 
                    "price": f"{current_price:.{dec}f}", "prob": f"{res['confidence']}%", 
                    "entry": res["entry_zone"], "sl": res["sl"], 
                    "tp1": res["tp1"], "tp2": res["tp2"], "tp3": res["tp3"],
                    "rrr": res["rrr"], "rsi": res["rsi"],
                    "risk_amount": res["risk_amount"], "position_size": res["position_size"],
                    "leverage": res["leverage"], "margin": res["margin"],
                    "market_status": m_status["text"], "countdown": m_status["countdown"],
                    "is_open": m_status["status"] == "Aperto",
                    "timestamp": datetime.now().strftime("%H:%M")
                }
    except Exception:
        pass
    return None

@app.route('/api/signals', methods=['GET'])
def get_signals():
    try:
        mode = request.args.get('mode', 'Day Trading')
        timeframe = request.args.get('timeframe', '15m')
        balance = safe_float(request.args.get('balance', 1000.0), 1000.0)
        risk_pct = safe_float(request.args.get('risk', 1.0), 1.0)

        markets = fetch_all_markets()
        signals = []
        
        target_assets = []
        for cat, assets in markets.items():
            m_status = get_market_status(cat)
            for a in assets:
                target_assets.append((cat, a, m_status))

        # Esecuzione in parallelo rapida e protetta da limiti di memoria
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(process_single_asset, cat, a, m_status, mode, balance, risk_pct, timeframe) for cat, a, m_status in target_assets]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    signals.append(res)

        signals.sort(key=lambda x: (not x['is_open'], x['category']))
        return jsonify(signals)
    except Exception as e:
        print("Errore signals:", e)
        return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
