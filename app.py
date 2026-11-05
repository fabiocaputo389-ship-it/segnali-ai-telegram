import os
import random
import time
from flask import Flask, jsonify, request, render_template

app = Flask(__name__, template_folder='.')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/signals')
def api_signals():
    current_time = time.time()
    assets = [
        {
            "sym": "BTCUSDT",
            "name": "Bitcoin",
            "cls": "cry",
            "price": 68450.25,
            "ch24": 0.0345,
            "comb": 85,
            "conf": 82,
            "top": True,
            "open": True,
            "anom": None,
            "sig": {
                "state": "ACTIVE",
                "dir": 1,
                "entry": 68200.0,
                "tp1": 69500.0,
                "tp2": 71000.0,
                "sl": 67300.0,
                "sl_pct": 0.013,
                "lev": [5, 10],
                "expires": current_time + 1800,
                "vetoed": False,
                "ai": {
                    "verdict": "CONFERMA",
                    "adjust": 3,
                    "reason": "Forte trend rialzista supportato da volumi crescenti e sentiment positivo su crypto.",
                    "risk": "Moderato"
                }
            },
            "news": {
                "ns": 0.65,
                "head": "Bitcoin supera resistenze chiave con forti flussi istituzionali",
                "src": "CoinDesk",
                "age": 15,
                "link": "",
                "n": 12
            },
            "flows": {
                "bull": 68.5,
                "ob": 0.35,
                "fund": 0.012,
                "vol": 34500000000,
                "oi": [0.045, 10]
            },
            "tf": {
                "15": {"score": 45, "rsi": 62.4, "hist": 1.2, "e2050": 0.008, "pb": 0.82, "vr": 1.4, "atrp": 0.004},
                "60": {"score": 55, "rsi": 65.1, "hist": 2.5, "e2050": 0.015, "pb": 0.88, "vr": 1.8, "atrp": 0.008},
                "240": {"score": 50, "rsi": 58.9, "hist": 3.1, "e2050": 0.022, "pb": 0.75, "vr": 1.6, "atrp": 0.012}
            },
            "adx": 32.4,
            "vol24": 34500000000
        },
        {
            "sym": "ETHUSDT",
            "name": "Ethereum",
            "cls": "cry",
            "price": 3520.10,
            "ch24": 0.0185,
            "comb": 72,
            "conf": 75,
            "top": False,
            "open": True,
            "anom": None,
            "sig": {
                "state": "ACTIVE",
                "dir": 1,
                "entry": 3500.0,
                "tp1": 3620.0,
                "tp2": 3750.0,
                "sl": 3440.0,
                "sl_pct": 0.017,
                "lev": [5, 10],
                "expires": current_time + 2100,
                "vetoed": False,
                "ai": {
                    "verdict": "CONFERMA",
                    "adjust": 0,
                    "reason": "Allineamento positivo sui timeframe principali.",
                    "risk": "Moderato"
                }
            },
            "news": {
                "ns": 0.40,
                "head": "Crescono le fee e l'utilizzo della rete Layer 2",
                "src": "Decrypt",
                "age": 42,
                "link": "",
                "n": 8
            },
            "flows": {
                "bull": 60.0,
                "ob": 0.15,
                "fund": 0.008,
                "vol": 18500000000,
                "oi": [0.02, 10]
            },
            "tf": {
                "15": {"score": 30, "rsi": 58.0, "hist": 0.5, "e2050": 0.005, "pb": 0.65, "vr": 1.1, "atrp": 0.006},
                "60": {"score": 40, "rsi": 60.2, "hist": 1.1, "e2050": 0.010, "pb": 0.70, "vr": 1.3, "atrp": 0.010},
                "240": {"score": 35, "rsi": 55.4, "hist": 1.8, "e2050": 0.014, "pb": 0.62, "vr": 1.2, "atrp": 0.015}
            },
            "adx": 28.1,
            "vol24": 18500000000
        },
        {
            "sym": "AAPL",
            "name": "Apple Inc.",
            "cls": "stk",
            "price": 225.50,
            "ch24": -0.0045,
            "comb": -45,
            "conf": 62,
            "top": False,
            "open": False,
            "anom": None,
            "sig": {
                "state": "EXPIRED",
                "dir": -1,
                "entry": 227.0,
                "tp1": 220.0,
                "tp2": 215.0,
                "sl": 230.0,
                "sl_pct": 0.013,
                "lev": [2, 5],
                "expires": current_time - 300,
                "vetoed": False,
                "ai": None
            },
            "news": {
                "ns": -0.15,
                "head": "Leggero calo nelle vendite di smartphone in Asia",
                "src": "Bloomberg",
                "age": 120,
                "link": "",
                "n": 5
            },
            "flows": {
                "bull": 45.0,
                "ob": None,
                "fund": None,
                "vol": None,
                "oi": None
            },
            "tf": {
                "15": {"score": -20, "rsi": 44.0, "hist": -0.2, "e2050": -0.002, "pb": 0.35, "vr": 0.9, "atrp": 0.003},
                "60": {"score": -30, "rsi": 41.5, "hist": -0.8, "e2050": -0.006, "pb": 0.30, "vr": 1.0, "atrp": 0.007},
                "240": {"score": -25, "rsi": 45.0, "hist": -0.5, "e2050": -0.004, "pb": 0.40, "vr": 0.9, "atrp": 0.010}
            },
            "adx": 21.0,
            "vol24": 9800000000
        }
    ]

    sent = [
        {"t": current_time - 3600, "v": [0.4, 0.1, -0.2]},
        {"t": current_time - 2400, "v": [0.5, 0.2, -0.1]},
        {"t": current_time - 1200, "v": [0.6, 0.1, 0.0]},
        {"t": current_time, "v": [0.65, 0.15, 0.1]}
    ]

    fng = {"value": 72, "label": "Greed"}
    
    track = {
        "winrate": 0.68,
        "exp_r": 1.25,
        "n": 42,
        "open": 3,
        "breakeven": 0.4,
        "bands": {
            "80-100": {"n": 15, "winrate": 0.80, "exp_r": 1.8},
            "60-79": {"n": 27, "winrate": 0.61, "exp_r": 0.9}
        },
        "recent": [
            {"sym": "BTCUSDT", "dir": 1, "status": "TP1", "conf": 85, "r": 1.5},
            {"sym": "ETHUSDT", "dir": 1, "status": "TP1", "conf": 78, "r": 1.5},
            {"sym": "AAPL", "dir": -1, "status": "SL", "conf": 65, "r": -1.0}
        ]
    }

    log = [
        {"t": current_time - 600, "type": "TOP", "txt": "Diamante giallo attivo su BTCUSDT (confluenza >80%)"},
        {"t": current_time - 1200, "type": "AI", "txt": "Verificata posizione su BTCUSDT: CONFERMA"},
        {"t": current_time - 1800, "type": "LONG", "txt": "Nuovo segnale operativo aperto su BTCUSDT @ 68200"}
    ]

    ai_summary = {
        "t": current_time - 300,
        "text": "Il mercato mostra una prevalenza rialzista guidata dal settore crypto, con BTC in forte spinta sopra i supporti chiave. Le azioni mantengono una fase di consolidamento laterale."
    }

    return jsonify({
        "server_time": current_time,
        "mode": "live",
        "ai_enabled": True,
        "config": {"valid": 2700, "model": "claude-3-5-sonnet"},
        "status": {
            "bitget": {"ok": True},
            "yahoo": {"ok": True},
            "news": {"ok": True},
            "fng": {"ok": True},
            "ai": {"ok": True}
        },
        "fng": fng,
        "assets": assets,
        "sent": sent,
        "track": track,
        "log": log,
        "ai_summary": ai_summary
    })

@app.route('/api/chart')
def api_chart():
    sym = request.args.get('sym', 'BTCUSDT')
    tf = request.args.get('tf', '15')
    
    now = time.time()
    step_sec = 900 if tf == '15' else (3600 if tf == '60' else 14400)
    n_points = 90
    
    base_price = 68000.0 if 'BTC' in sym else (3500.0 if 'ETH' in sym else 225.0)
    
    t_list, o_list, h_list, l_list, c_list, v_list = [], [], [], [], [], []
    e20_list, e50_list, rsi_list, score_list = [], [], [], []
    
    curr = base_price
    for i in range(n_points):
        t = now - (n_points - i) * step_sec
        o = curr
        change = (random.random() - 0.48) * (curr * 0.008)
        c = o + change
        h = max(o, c) + random.random() * (curr * 0.004)
        l = min(o, c) - random.random() * (curr * 0.004)
        v = random.randint(1000000, 15000000)
        
        curr = c
        t_list.append(t)
        o_list.append(o)
        h_list.append(h)
        l_list.append(l)
        c_list.append(c)
        v_list.append(v)
        e20_list.append(c * 0.998)
        e50_list.append(c * 0.995)
        rsi_list.append(50 + random.random() * 25 - 10)
        score_list.append(random.randint(-40, 60))

    return jsonify({
        "t": t_list,
        "o": o_list,
        "h": h_list,
        "l": l_list,
        "c": c_list,
        "v": v_list,
        "e20": e20_list,
        "e50": e50_list,
        "rsi": rsi_list,
        "score": score_list
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
