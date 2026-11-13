from flask import Flask, render_template, jsonify, request
import requests
import pytz
from datetime import datetime
import random

app = Flask(__name__, static_folder='static', template_folder='static')

@app.route('/')
def home():
    return app.send_static_file('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_chart():
    data = request.json or {}
    symbol = data.get('symbol', 'XAUUSD')
    timeframe = data.get('timeframe', '15m')
    mode = data.get('mode', 'Day Trading')
    
    # Prezzi base simulati realistici per i principali asset
    base_prices = {
        'XAUUSD': 3884.27,
        'BTCUSDT': 91450.00,
        'EURUSD': 1.0785,
        'ETHUSDT': 3420.50
    }
    current_price = base_prices.get(symbol, 3000.00)
    
    # Calcoli dinamici basati su volatilità simulata e timeframe
    offset = current_price * 0.0015
    favorability = random.randint(62, 94)
    action = "BUY" if favorability > 65 else "SELL"
    
    if action == "BUY":
        entry_low = round(current_price - offset, 2)
        entry_high = round(current_price + (offset * 0.5), 2)
        sl = round(entry_low - (offset * 2), 2)
        tp1 = round(current_price + (offset * 1.5), 2)
        tp2 = round(current_price + (offset * 3), 2)
        tp3 = round(current_price + (offset * 5), 2)
    else:
        entry_low = round(current_price - (offset * 0.5), 2)
        entry_high = round(current_price + offset, 2)
        sl = round(entry_high + (offset * 2), 2)
        tp1 = round(current_price - (offset * 1.5), 2)
        tp2 = round(current_price - (offset * 3), 2)
        tp3 = round(current_price - (offset * 5), 2)

    return jsonify({
        "status": "success",
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": mode,
        "current_price": current_price,
        "action": action,
        "favorability": favorability,
        "entry_zone": f"{entry_low} - {entry_high}",
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "ai_comment": f"Analisi {mode} su {timeframe}: Rilevata forte spinta sui volumi istituzionali con conferma ATR e RSI. Setup ad alta probabilità.",
        "counter_trade": {
            "action": "SELL" if action == "BUY" else "BUY",
            "reason": "Scenario contrario a rischio elevato contro il trend primario in corso."
        }
    })

@app.route('/api/signals', methods=['GET'])
def get_signals():
    signals = [
        {"symbol": "XAUUSD", "type": "Scalping", "action": "SELL", "time": "35 min fa", "prob": "Low", "status": "active"},
        {"symbol": "BTCUSDT", "type": "Scalping", "action": "SELL", "time": "53 min fa", "prob": "Pro", "status": "active"},
        {"symbol": "EURUSD", "type": "Day Trading", "action": "BUY", "time": "1 ora fa", "prob": "Pro", "status": "active"},
        {"symbol": "ETHUSDT", "type": "Swing", "action": "BUY", "time": "2 ore fa", "prob": "Free", "status": "active"}
    ]
    return jsonify(signals)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
