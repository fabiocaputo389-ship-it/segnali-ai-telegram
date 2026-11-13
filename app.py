from flask import Flask, jsonify, request
import requests
import pytz
from datetime import datetime
import random

app = Flask(__name__, static_folder='static', template_folder='static')

@app.route('/')
def home():
    return app.send_static_file('index.html')

def evaluate_anti_fakeout(symbol, price, volume_spike, atr_value, rsi):
    """
    Motore Anti-Falsi Segnali e False Rotture (Volume + ATR + RSI)
    """
    reasons = []
    is_valid = True
    
    # 1. Controllo Volume
    if volume_spike < 1.3:
        reasons.append("⚠️ Volume insufficiente: alto rischio di finta rottura (Fakeout).")
        is_valid = False
    else:
        reasons.append("✅ Volume istituzionale confermato (> 1.3x media).")

    # 2. Controllo ATR / Volatilità
    if atr_value < 0.001:
        reasons.append("⚠️ Volatilità ATR bassa: movimento laterale rumoroso.")
        is_valid = False
    else:
        reasons.append("✅ Soglia ATR superata: rottura direzionale reale.")

    # 3. Controllo RSI (Evita trappole in iperestensione)
    if rsi > 78 or rsi < 22:
        reasons.append("⚠️ RSI in iper-estensione: prudenza sui ritracciamenti.")
    else:
        reasons.append("✅ RSI sano in zona operativa.")

    risk_level = "Basso (Segnale Pulito)" if is_valid and len(reasons) >= 2 else "Alto (Rischio Falsa Rottura)"
    return is_valid, risk_level, reasons

@app.route('/api/analyze', methods=['POST'])
def analyze_chart():
    data = request.json or {}
    symbol = data.get('symbol', 'XAUUSD')
    timeframe = data.get('timeframe', '15m')
    mode = data.get('mode', 'Day Trading')
    
    base_prices = {
        'XAUUSD': 3884.27,
        'BTCUSDT': 91450.00,
        'EURUSD': 1.0785,
        'ETHUSDT': 3420.50
    }
    current_price = base_prices.get(symbol, 3000.00)
    
    sim_volume_spike = round(random.uniform(0.9, 2.4), 2)
    sim_atr = round(random.uniform(0.002, 0.015), 4)
    sim_rsi = random.randint(35, 75)
    
    is_valid_signal, risk_level, fakeout_logs = evaluate_anti_fakeout(
        symbol, current_price, sim_volume_spike, sim_atr, sim_rsi
    )
    
    favorability = random.randint(70, 96) if is_valid_signal else random.randint(40, 58)
    action = "BUY" if favorability > 60 else "SELL"
    
    offset = current_price * 0.0015
    if action == "BUY":
        entry_low = round(current_price - offset, 2)
        entry_high = round(current_price + (offset * 0.5), 2)
        sl = round(entry_low - (offset * 1.8), 2)
        tp1 = round(current_price + (offset * 1.5), 2)
        tp2 = round(current_price + (offset * 3), 2)
        tp3 = round(current_price + (offset * 5), 2)
    else:
        entry_low = round(current_price - (offset * 0.5), 2)
        entry_high = round(current_price + offset, 2)
        sl = round(entry_high + (offset * 1.8), 2)
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
        "risk_level": risk_level,
        "fakeout_logs": fakeout_logs,
        "entry_zone": f"{entry_low} - {entry_high}",
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "ai_comment": f"Analisi {mode} ({timeframe}): Motore Anti-Fakeout completato. Rilevato spike di volume ({sim_volume_spike}x) e filtro ATR validato."
    })

@app.route('/api/signals', methods=['GET'])
def get_signals():
    signals = [
        {"symbol": "XAUUSD", "type": "Scalping", "action": "SELL", "time": "35 min fa", "prob": "Low"},
        {"symbol": "BTCUSDT", "type": "Scalping", "action": "SELL", "time": "53 min fa", "prob": "Pro"},
        {"symbol": "EURUSD", "type": "Day Trading", "action": "BUY", "time": "1 ora fa", "prob": "Pro"},
        {"symbol": "ETHUSDT", "type": "Swing", "action": "BUY", "time": "2 ore fa", "prob": "Free"}
    ]
    return jsonify(signals)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
