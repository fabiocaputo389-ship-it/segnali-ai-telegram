import os
import json
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

# Configurazione chiavi BingX (impostabili anche tramite variabili d'ambiente su Render)
app.config['BINGX_API_KEY'] = os.getenv('BINGX_API_KEY', 'LA_TUA_API_KEY')
app.config['BINGX_SECRET_KEY'] = os.getenv('BINGX_SECRET_KEY', 'LA_TUA_SECRET_KEY')

BINGX_BASE_URL = "https://open-api.bingx.com"
STATE_FILE = "autotrade_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"autotrade_active": False, "leverage": 20, "capital": 2.0}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def generate_bingx_sign(secret_key, query_string):
    """Genera la firma HMAC SHA256 per le API di BingX"""
    return hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def bingx_signed_request(method, path, params=None):
    """Esegue richieste firmate alle API Futures USD-M di BingX"""
    api_key = app.config.get('BINGX_API_KEY')
    secret_key = app.config.get('BINGX_SECRET_KEY')
    
    if not api_key or not secret_key or api_key == 'LA_TUA_API_KEY':
        return {"code": -1, "msg": "API Key o Secret Key non configurate."}
        
    timestamp = str(int(time.time() * 1000))
    if params is None:
        params = {}
    params['timestamp'] = timestamp
    
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = generate_bingx_sign(secret_key, query_string)
    
    url = f"{BINGX_BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    
    try:
        if method.upper() == 'GET':
            res = requests.get(url, headers=headers, timeout=10)
        elif method.upper() == 'POST':
            res = requests.post(url, headers=headers, timeout=10)
        else:
            return {"code": -1, "msg": "Metodo non supportato"}
        return res.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/state', methods=['GET', 'POST'])
def handle_state():
    state = load_state()
    if request.method == 'POST':
        data = request.json or {}
        if 'autotrade_active' in data:
            state['autotrade_active'] = bool(data['autotrade_active'])
        if 'leverage' in data:
            state['leverage'] = int(data['leverage'])
        if 'capital' in data:
            state['capital'] = float(data['capital'])
        save_state(state)
    return jsonify(state)

@app.route('/api/bingx/live-account', methods=['GET'])
def api_bingx_live_account():
    """Recupera saldo, equity e posizioni attive reali da BingX"""
    balance_res = bingx_signed_request('GET', '/openApi/swap/v2/user/balance')
    positions_res = bingx_signed_request('GET', '/openApi/swap/v2/user/positions')
    
    return jsonify({
        "success": True,
        "balance": balance_res.get('data', {}),
        "positions": positions_res.get('data', [])
    })

@app.route('/api/bingx/sync-performance', methods=['GET'])
def api_sync_performance():
    """Scarica la cronologia trade per popolare report giornalieri e mensili senza zeri"""
    params = {"limit": 50}
    trades_res = bingx_signed_request('GET', '/openApi/swap/v1/user/trades', params=params)
    
    if trades_res.get('code') != 0:
        return jsonify({
            "success": False,
            "error": trades_res.get('msg', 'Errore nel recupero cronologia trade'),
            "raw": trades_res
        })
        
    data = trades_res.get('data', {})
    trades = data.get('trades', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    
    total_trades = len(trades)
    wins = 0
    losses = 0
    total_pnl = 0.0
    
    daily_stats = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    monthly_stats = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    
    current_time = time.time() * 1000
    one_day_ms = 24 * 60 * 60 * 1000
    
    for trade in trades:
        pnl = float(trade.get('realizedProfit', trade.get('profit', 0)))
        total_pnl += pnl
        
        is_win = pnl > 0
        if is_win:
            wins += 1
        elif pnl < 0:
            losses += 1
            
        trade_time = float(trade.get('time', trade.get('tradeTime', current_time)))
        
        # Filtro Ultime 24 ore (Giornaliero)
        if (current_time - trade_time) <= one_day_ms:
            daily_stats["trades"] += 1
            daily_stats["pnl"] += pnl
            if is_win: daily_stats["wins"] += 1
            elif pnl < 0: daily_stats["losses"] += 1
            
        # Filtro Mensile
        monthly_stats["trades"] += 1
        monthly_stats["pnl"] += pnl
        if is_win: monthly_stats["wins"] += 1
        elif pnl < 0: monthly_stats["losses"] += 1

    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    daily_win_rate = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
    
    return jsonify({
        "success": True,
        "overall": {
            "totalTrades": total_trades,
            "wins": wins,
            "losses": losses,
            "winRate": round(win_rate, 1),
            "totalPnl": round(total_pnl, 4)
        },
        "daily": {
            "totalTrades": daily_stats["trades"],
            "wins": daily_stats["wins"],
            "losses": daily_stats["losses"],
            "winRate": round(daily_win_rate, 1),
            "totalPnl": round(daily_stats["pnl"], 4)
        },
        "monthly": {
            "totalTrades": monthly_stats["trades"],
            "wins": monthly_stats["wins"],
            "losses": monthly_stats["losses"],
            "totalPnl": round(monthly_stats["pnl"], 4)
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0' if 'RENDER' in os.environ else '127.0.0.1', port=port, debug=True)
