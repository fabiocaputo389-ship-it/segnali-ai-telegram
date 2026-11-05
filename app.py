import os
import time
import hmac
import hashlib
import requests
import sqlite3
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_default_molto_sicura')

bot_settings = {
    "bingx_api_key": os.environ.get("BINGX_API_KEY", ""),
    "bingx_secret_key": os.environ.get("BINGX_SECRET_KEY", ""),
    "trading_mode": os.environ.get("TRADING_MODE", "paper"),
    "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
    "chat_id": os.environ.get("CHAT_ID", ""),
    "auto_execution": int(os.environ.get("AUTO_EXECUTION", 0))
}

def init_db():
    conn = sqlite3.connect('trading_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_bingx_signature(secret_key, payload):
    return hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

def send_telegram_alert(token, chat_id, message):
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def get_bingx_account_balance(api_key, secret_key):
    if not api_key or not secret_key:
        return {"balance": 0.0, "equity": 0.0, "error": "API Keys mancanti"}
    url = "https://open-api.bingx.com/openApi/swap/v2/user/balance"
    timestamp = str(int(time.time() * 1000))
    params = f"timestamp={timestamp}"
    signature = get_bingx_signature(secret_key, params)
    headers = {"X-BX-APIKEY": api_key}
    full_url = f"{url}?{params}&signature={signature}"
    try:
        response = requests.get(full_url, headers=headers, timeout=5)
        data = response.json()
        if data.get("code") == 0:
            balance_info = data.get("data", {}).get("balance", {})
            return {
                "balance": float(balance_info.get("balance", 0.0)),
                "equity": float(balance_info.get("equity", 0.0))
            }
        else:
            return {"balance": 0.0, "equity": 0.0, "error": data.get("msg", "Errore API BingX")}
    except Exception as e:
        return {"balance": 0.0, "equity": 0.0, "error": str(e)}

def place_bingx_order(api_key, secret_key, symbol, side, position_side, quantity):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))
    query_string = f"quantity={quantity}&side={side}&positionSide={position_side}&symbol={symbol}&timestamp={timestamp}&type=MARKET"
    signature = get_bingx_signature(secret_key, query_string)
    headers = {"X-BX-APIKEY": api_key}
    full_url = f"{url}?{query_string}&signature={signature}"
    try:
        response = requests.post(full_url, headers=headers, timeout=5)
        res_data = response.json()
        print(f"Risposta ordine BingX REALE per {symbol}: {res_data}")
        return res_data
    except Exception as e:
        print(f"Errore esecuzione ordine BingX: {e}")
        return {"code": -1, "msg": str(e)}

# Panieri Reali
CRYPTO_FUTURES_ASSETS = [
    {"sym": "BTCUSDT", "name": "Bitcoin Futures"},
    {"sym": "ETHUSDT", "name": "Ethereum Futures"},
    {"sym": "SOLUSDT", "name": "Solana Futures"},
    {"sym": "BNBUSDT", "name": "BNB Futures"},
    {"sym": "XRPUSDT", "name": "XRP Futures"},
    {"sym": "ADAUSDT", "name": "Cardano Futures"},
    {"sym": "DOGEUSDT", "name": "Dogecoin Futures"},
    {"sym": "AVAXUSDT", "name": "Avalanche Futures"},
    {"sym": "LINKUSDT", "name": "Chainlink Futures"},
    {"sym": "SUIUSDT", "name": "Sui Futures"}
]

COMMODITIES_ASSETS = [
    {"sym": "GC=F", "name": "Oro (Gold)"},
    {"sym": "CL=F", "name": "Petrolio WTI"},
    {"sym": "SI=F", "name": "Argento (Silver)"},
    {"sym": "NG=F", "name": "Gas Naturale"}
]

live_market_data = {}
commodities_market_data = {}

def calculate_advanced_indicators(klines):
    if len(klines) < 50:
        return None
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    
    current_price = closes[-1]
    ema20 = sum(closes[-20:]) / 20
    ema50 = sum(closes[-50:]) / 50
    
    # Calcolo volatilità (ATR approssimato a 14 periodi) per TP e SL intelligenti
    true_ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(klines))]
    atr = sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else (highs[-1] - lows[-1])
    
    is_long = current_price > ema20 and ema20 > ema50
    is_short = current_price < ema20 and ema20 < ema50
    
    # Sentiment basato su forza del trend e distanza dalle EMA
    sentiment = "BULLISH FORTE" if is_long and (current_price - ema20) > (atr * 0.5) else ("BEARISH FORTE" if is_short else "NEUTRAL / CONSOLIDAMENTO")
    
    # Livelli Dinamici basati su ATR (Rapporto Rischio/Rendimento 1:2)
    sl = current_price - (atr * 1.5) if is_long else current_price + (atr * 1.5)
    tp1 = current_price + (atr * 3.0) if is_long else current_price - (atr * 3.0)
    
    return {
        "current_price": current_price,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "sl": sl,
        "tp1": tp1,
        "is_long": is_long,
        "is_short": is_short,
        "sentiment": sentiment
    }

def background_real_market_scanner():
    last_alerts = {}
    while True:
        try:
            token = bot_settings.get("telegram_token")
            chat_id = bot_settings.get("chat_id")
            auto_exec = bot_settings.get("auto_execution", 0)
            trading_mode = bot_settings.get("trading_mode", "paper")
            api_key = bot_settings.get("bingx_api_key")
            secret_key = bot_settings.get("bingx_secret_key")
            
            # Scansione Cripto Futures Reali con Intelligenza Avanzata
            for asset in CRYPTO_FUTURES_ASSETS:
                symbol = asset["sym"]
                res = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=60", timeout=5)
                if res.status_code == 200:
                    klines = res.json()
                    ind = calculate_advanced_indicators(klines)
                    if not ind:
                        continue
                    live_market_data[symbol] = ind
                    current_price = ind["current_price"]
                    now = time.time()
                    
                    if ind["is_long"] and (symbol not in last_alerts or now - last_alerts[symbol] > 1800):
                        msg = (
                            f"🧠 *SEGNALE IA AVANZATO (REALE)*\n\n"
                            f"✦ *Asset:* #{symbol}\n"
                            f"✦ *Direzione:* LONG 🟢\n"
                            f"✦ *Sentiment:* {ind['sentiment']}\n"
                            f"✦ *Prezzo Reale:* ${current_price:,.4f}\n\n"
                            f"🎯 *Take Profit (TP):* ${ind['tp1']:,.4f}\n"
                            f"🛑 *Stop Loss (SL):* ${ind['sl']:,.4f}\n"
                            f"⚖️ *Rischio/Rendimento:* 1:2 Dinamico\n\n"
                            f"⚡ _Elaborazione basata su ATR e Volatilità Reale_"
                        )
                        if token and chat_id:
                            send_telegram_alert(token, chat_id, msg)
                        if auto_exec == 1 and trading_mode == "real" and api_key and secret_key:
                            qty = 0.001 if "BTC" in symbol else (0.01 if "ETH" in symbol else 1.0)
                            place_bingx_order(api_key, secret_key, symbol, "BUY", "LONG", qty)
                        last_alerts[symbol] = now

            # Scansione Materie Prime Reali
            headers = {'User-Agent': 'Mozilla/5.0'}
            for comm in COMMODITIES_ASSETS:
                sym = comm["sym"]
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
                resp = requests.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("chart", {}).get("result", [])
                    if result:
                        meta = result[0].get("meta", {})
                        price = meta.get("regularMarketPrice", 0.0)
                        prev_close = meta.get("chartPreviousClose", price)
                        change = ((price - prev_close) / prev_close) * 100 if prev_close else 0.0
                        commodities_market_data[sym] = {
                            "name": comm["name"],
                            "price": price,
                            "change": change
                        }
        except Exception as e:
            print(f"Errore scanner avanzato: {e}")
        time.sleep(30)

scanner_thread = threading.Thread(target=background_real_market_scanner, daemon=True)
scanner_thread.start()

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Dashboard IA & Mercato Reale</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{background:#06080c;color:#ece8dc;font-family:sans-serif;margin:0;padding:20px;}
.wrap{max-width:1200px;margin:0 auto;}
.panel{background:#0c1017;border:1px solid #1b2433;padding:20px;margin-bottom:20px;}
h1,h2{color:#d6b04c;}
table{width:100%;border-collapse:collapse;margin-top:10px;}
th,td{padding:10px;text-align:left;border-bottom:1px solid #1b2433;font-size:14px;}
th{color:#6b7689;font-size:12px;text-transform:uppercase;}
.up{color:#2ee6a6;}.dn{color:#ff5a76;}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:15px;}
input,select{background:#111722;border:1px solid #2a3750;color:#fff;padding:8px;width:100%;}
button{background:#d6b04c;color:#06080c;border:none;padding:10px 20px;font-weight:bold;cursor:pointer;}
.chip{display:inline-block;padding:5px 10px;background:#111722;border:1px solid #2a3750;margin-right:10px;font-size:12px;}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel">
    <h1>🚀 IA Trading Dashboard (Analisi Avanzata Reale)</h1>
    <div>
      <span class="chip">Balance Reale: <b style="color:#d6b04c">${{ "%.2f"|format(account.balance) }}</b></span>
      <span class="chip">Equity Reale: <b style="color:#2ee6a6">${{ "%.2f"|format(account.equity) }}</b></span>
      <a href="{{ url_for('logout') }}" style="float:right;color:#ff5a76;text-decoration:none;font-weight:bold;">Logout</a>
    </div>
  </div>

  <section class="panel">
    <h2>⚙️ Configurazione Account & Bot</h2>
    <form method="POST">
      <div class="form-grid">
        <div>
          <label>Modalità Trading</label>
          <select name="trading_mode">
            <option value="paper" {% if settings.trading_mode == 'paper' %}selected{% endif %}>Paper Trading</option>
            <option value="real" {% if settings.trading_mode == 'real' %}selected{% endif %}>Trading Reale (BingX)</option>
          </select>
        </div>
        <div>
          <label>BingX API Key</label>
          <input type="text" name="bingx_api_key" value="{{ settings.bingx_api_key }}">
        </div>
        <div>
          <label>BingX Secret Key</label>
          <input type="password" name="bingx_secret_key" value="{{ settings.bingx_secret_key }}">
        </div>
        <div>
          <label>Telegram Token</label>
          <input type="text" name="telegram_token" value="{{ settings.telegram_token }}">
        </div>
        <div>
          <label>Telegram Chat ID</label>
          <input type="text" name="chat_id" value="{{ settings.chat_id }}">
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:15px;">
        <label><input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Esecuzione Automatica Reale</label>
        <button type="submit">Salva Impostazioni</button>
      </div>
    </form>
  </section>

  <section class="panel">
    <h2>🛢️ Materie Prime (Prezzi Reali)</h2>
    <table>
      <thead>
        <tr><th>Materia Prima</th><th>Prezzo (USD)</th><th>Var. 24h</th></tr>
      </thead>
      <tbody>
        {% for sym, data in commodities.items() %}
          <tr>
            <td><b>{{ data.name }}</b></td>
            <td>${{ "%.2f"|format(data.price) }}</td>
            <td class="{{ 'up' if data.change >= 0 else 'dn' }}">{{ "%.2f"|format(data.change) }}%</td>
          </tr>
        {% else %}
          <tr><td colspan="3" style="color:#6b7689">Caricamento in corso...</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2>📊 Cripto Futures (Analisi IA & Livelli Dinamici)</h2>
    <table>
      <thead>
        <tr><th>Asset</th><th>Prezzo Reale</th><th>Sentiment IA</th><th>TP Stimato</th><th>SL Stimato</th><th>Trend</th></tr>
      </thead>
      <tbody>
        {% for asset in assets %}
          {% set data = market_data.get(asset.sym) %}
          <tr>
            <td><b>{{ asset.sym }}</b></td>
            <td>{% if data %}${{ "%.4f"|format(data.current_price) }}{% else %}Caricamento...{% endif %}</td>
            <td><span style="color:#d6b04c">{% if data %}{{ data.sentiment }}{% else %}—{% endif %}</span></td>
            <td class="up">{% if data %}${{ "%.4f"|format(data.tp1) }}{% else %}—{% endif %}</td>
            <td class="dn">{% if data %}${{ "%.4f"|format(data.sl) }}{% else %}—{% endif %}</td>
            <td>
              {% if data %}
                {% if data.is_long %}<span class="up">▲ LONG</span>
                {% elif data.is_short %}<span class="dn">▼ SHORT</span>
                {% else %}<span style="color:#6b7689">NEUTRO</span>{% endif %}
              {% else %}<span style="color:#6b7689">In attesa...</span>{% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </section>
</div>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    global bot_settings
    if request.method == 'POST':
        bot_settings["bingx_api_key"] = request.form.get("bingx_api_key", "").strip()
        bot_settings["bingx_secret_key"] = request.form.get("bingx_secret_key", "").strip()
        bot_settings["trading_mode"] = request.form.get("trading_mode", "paper")
        bot_settings["telegram_token"] = request.form.get("telegram_token", "").strip()
        bot_settings["chat_id"] = request.form.get("chat_id", "").strip()
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Impostazioni salvate!")
        return redirect(url_for('home'))

    account_data = {"balance": 0.00, "equity": 0.00}
    if bot_settings["trading_mode"] == "real" and bot_settings["bingx_api_key"] and bot_settings["bingx_secret_key"]:
        bingx_res = get_bingx_account_balance(bot_settings["bingx_api_key"], bot_settings["bingx_secret_key"])
        if "error" not in bingx_res:
            account_data["balance"] = bingx_res["balance"]
            account_data["equity"] = bingx_res["equity"]

    return render_template_string(
        DASHBOARD_HTML, 
        username=session['username'], 
        settings=bot_settings, 
        account=account_data,
        assets=CRYPTO_FUTURES_ASSETS,
        market_data=live_market_data,
        commodities=commodities_market_data
    )

@app.route('/login', methods=['GET', 'PORT'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect('trading_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['username'] = username
            return redirect(url_for('home'))
        flash('Credenziali non valide.')
    content = '''
    <div style="max-width:380px;margin:50px auto;background:#0c1017;padding:25px;border:1px solid #2a3750;color:#fff;">
        <h3 style="color:#d6b04c;text-align:center;">ACCESSO</h3>
        <form method="POST">
            <p><label>Username</label><br><input type="text" name="username" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;margin-top:5px;"></p>
            <p><label>Password</label><br><input type="password" name="password" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;margin-top:5px;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#d6b04c;color:#06080c;border:none;font-weight:bold;margin-top:10px;cursor:pointer;">Accedi</button>
        </form>
    </div>
    '''
    return render_template_string(content)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
