import os
import time
import threading
import sqlite3
import hmac
import hashlib
import requests
from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    session,
    url_for,
    flash,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY", "chiave_segreta_super_sicura_vip"
)

BRAND_NAME = "SCANNER TRADING VIP"
TIMEFRAMES = ["1h", "4h"]

SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
    "ADA-USDT",
    "AVAX-USDT",
    "LINK-USDT",
    "DOT-USDT",
    "NEAR-USDT",
    "UNI-USDT",
    "RENDER-USDT",
    "SUI-USDT",
    "ARB-USDT",
    "OP-USDT",
    "IMX-USDT",
    "ICP-USDT",
    "APT-USDT",
    "FIL-USDT",
    "PEPE-USDT",
    "DOGE-USDT",
    "SHIB-USDT",
    "ATOM-USDT",
    "XLM-USDT",
    "ALGO-USDT",
    "FET-USDT",
    "INJ-USDT",
    "LTC-USDT",
    "BCH-USDT",
    "ETC-USDT",
    "SAND-USDT",
    "MANA-USDT",
    "AAVE-USDT",
    "FTM-USDT",
    "RUNE-USDT",
    "ZEC-USDT",
    "TRX-USDT",
]

current_threshold = 80

market_data_cache = {
    symbol: {
        "symbol": symbol.replace("-USDT", ""),
        "pair": symbol.replace("-USDT", "/USDT"),
        "price": 100.0,
        "rsi_1h": 50.0,
        "rsi_4h": 50.0,
        "score": 50,
        "rating": "NEUTRAL",
        "action": "NEUTRAL",
    }
    for symbol in SYMBOLS
}


# --- DATABASE SETUP ---
def init_db():
  conn = sqlite3.connect("trading_bot.db")
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bingx_api_key TEXT,
            bingx_secret_key TEXT,
            auto_trading INTEGER DEFAULT 0
        )
    """)
  conn.commit()
  conn.close()


init_db()


# --- BINGX TRADING EXECUTION (HMAC-SHA256) ---
def execute_bingx_order(api_key, secret_key, symbol, side, quantity=10.0):
  """Esegue un ordine a mercato sui Futures di BingX firmando la richiesta con HMAC-SHA256"""
  try:
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    # Parametri per ordine a mercato Futures
    params = {
        "symbol": symbol,
        "side": side,  # BUY (per Long) o SELL (per Short)
        "positionSide": "LONG" if side == "BUY" else "SHORT",
        "type": "MARKET",
        "quantity": str(quantity),
        "timestamp": timestamp,
    }

    query_string = (
        "&".join([f"{key}={params[key]}" for key in sorted(params.keys())])
        + f"&timestamp={timestamp}"
    )
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {"X-BX-APIKEY": api_key}
    full_url = f"{url}?{query_string}&signature={signature}"

    response = requests.post(full_url, headers=headers, timeout=5)
    print(f"Risposta esecuzione BingX per {symbol}: {response.text}")
    return response.status_code == 200
  except Exception as e:
    print(f"Errore esecuzione ordine BingX: {e}")
    return False


def trigger_auto_trades(symbol, action):
  """Invia l'ordine automatico al proprietario (da env) e a tutti gli utenti con auto-trading attivo"""
  side = "BUY" if action == "LONG" else "SELL"

  # 1. Esecuzione per il proprietario (configurato su Render)
  owner_api = os.environ.get("BINGX_API_KEY")
  owner_secret = os.environ.get("BINGX_SECRET_KEY")
  if owner_api and owner_secret:
    print(f"Esecuzione ordine proprietario per {symbol} ({action})...")
    execute_bingx_order(owner_api, owner_secret, symbol, side)

  # 2. Esecuzione per gli utenti registrati
  try:
    conn = sqlite3.connect("trading_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT bingx_api_key, bingx_secret_key FROM users WHERE auto_trading ="
        " 1 AND bingx_api_key IS NOT NULL AND bingx_secret_key IS NOT NULL"
    )
    users = cursor.fetchall()
    conn.close()

    for u_api, u_sec in users:
      print(f"Esecuzione ordine utente VIP per {symbol} ({action})...")
      execute_bingx_order(u_api, u_sec, symbol, side)
  except Exception as e:
    print(f"Errore recupero utenti per auto-trading: {e}")


# --- INDICATORI & SCANNER ---
def calculate_rsi(prices, period=14):
  if len(prices) < period + 1:
    return 50.0
  gains, losses = [], []
  for i in range(1, len(prices)):
    diff = prices[i] - prices[i - 1]
    if diff >= 0:
      gains.append(diff)
      losses.append(0)
    else:
      gains.append(0)
      losses.append(abs(diff))
  avg_gain = sum(gains[-period:]) / period
  avg_loss = sum(losses[-period:]) / period
  if avg_loss == 0:
    return 100.0
  rs = avg_gain / avg_loss
  return round(100 - (100 / (1 + rs)), 2)


def calculate_atr(candles, period=14):
  if len(candles) < period + 1:
    return 0.0
  trs = []
  for i in range(1, len(candles)):
    high = float(candles[i]["high"])
    low = float(candles[i]["low"])
    prev_close = float(candles[i - 1]["close"])
    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
    trs.append(tr)
  return sum(trs[-period:]) / period


def fetch_bingx_klines(symbol, interval):
  try:
    url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval={interval}&limit=30"
    res = requests.get(url, timeout=5)
    if res.status_code == 200:
      data = res.json().get("data", [])
      if isinstance(data, list) and len(data) > 0:
        return sorted(data, key=lambda x: int(x["time"]))
  except Exception as e:
    print(f"Errore API BingX {symbol} [{interval}]: {e}")
  return []


def run_scanner_background():
  telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
  channel_id = os.environ.get("CHANNEL_ID")

  print(f"Scanner {BRAND_NAME} avviato con Auto-Trading integrato...")
  last_signal_time = {symbol: 0 for symbol in SYMBOLS}
  COOLDOWN_SECONDS = 3600

  while True:
    global current_threshold, market_data_cache
    current_time = time.time()

    for symbol in SYMBOLS:
      try:
        candles_1h = fetch_bingx_klines(symbol, "1h")
        candles_4h = fetch_bingx_klines(symbol, "4h")

        if candles_1h and candles_4h:
          closes_1h = [float(c["close"]) for c in candles_1h]
          closes_4h = [float(c["close"]) for c in candles_4h]

          current_price = closes_1h[-1]
          rsi_1h = calculate_rsi(closes_1h)
          rsi_4h = calculate_rsi(closes_4h)
          atr = calculate_atr(candles_1h)

          action, score, rating = "NEUTRAL", 50, "NEUTRAL"

          if rsi_1h <= 35 and rsi_4h <= 55:
            action = "LONG"
            score = min(
                99, max(60, int(50 + (35 - rsi_1h) * 1.2 + (55 - rsi_4h) * 0.5))
            )
            rating = (
                "STRONG BUY" if rsi_1h <= 25 and rsi_4h <= 45 else "BUY"
            )
          elif rsi_1h >= 65 and rsi_4h >= 45:
            action = "SHORT"
            score = min(
                99,
                max(
                    60, int(50 + (rsi_1h - 65) * 1.2 + (rsi_4h - 45) * 0.5)
                ),
            )
            rating = (
                "STRONG SELL" if rsi_1h >= 75 and rsi_4h >= 55 else "SELL"
            )

          market_data_cache[symbol] = {
              "symbol": symbol.replace("-USDT", ""),
              "pair": symbol.replace("-USDT", "/USDT"),
              "price": current_price,
              "rsi_1h": rsi_1h,
              "rsi_4h": rsi_4h,
              "score": score,
              "rating": rating,
              "action": action,
          }

          if (
              score >= current_threshold
              and action != "NEUTRAL"
              and telegram_token
              and channel_id
          ):
            if current_time - last_signal_time[symbol] > COOLDOWN_SECONDS:
              last_signal_time[symbol] = current_time

              # Esegui auto-trading automatico
              trigger_auto_trades(symbol, action)

              pair_name = symbol.replace("-USDT", " / USDT")
              decimals = 5 if current_price < 1 else 2
              base_step = atr if atr > 0 else current_price * 0.015

              if action == "LONG":
                sl = current_price - (base_step * 1.2)
                tp1 = current_price + (base_step * 2.0)
                tp2 = current_price + (base_step * 3.5)
                dir_text = "🟢 LONG (ACQUISTO)"
              else:
                sl = current_price + (base_step * 1.2)
                tp1 = current_price - (base_step * 2.0)
                tp2 = current_price - (base_step * 3.5)
                dir_text = "🔴 SHORT (VENDITA)"

              sl_pct = ((sl - current_price) / current_price) * 100
              tp1_pct = ((tp1 - current_price) / current_price) * 100
              tp2_pct = ((tp2 - current_price) / current_price) * 100

              telegram_url = (
                  f"https://api.telegram.org/bot{telegram_token}/sendMessage"
              )
              message = (
                  f"🔥 **SEGNALE AUTO-TRADING {BRAND_NAME}** 🔥\n\n"
                  f"🔹 **Asset:** {pair_name}\n"
                  f"⏰ **Timeframe:** 1H + 4H (Confluenza)\n"
                  f"📊 **Direzione:** {dir_text}\n"
                  f"⭐ **Rating IA:** {rating} (Confluenza: {score}%)\n"
                  f"📈 **RSI 1H:** {rsi_1h} | **RSI 4H:** {rsi_4h}\n"
                  f"⚡ **Prezzo Live:** ${current_price:.{decimals}f}\n\n"
                  f"🛑 **Stop Loss:** ${sl:.{decimals}f} ({sl_pct:.2f}%)\n"
                  f"🎯 **TP 1:** ${tp1:.{decimals}f} (+{abs(tp1_pct):.2f}%)\n"
                  f"🎯 **TP 2:** ${tp2:.{decimals}f} (+{abs(tp2_pct):.2f}%)\n\n"
                  "🚀 *Eseguito in automatico sugli account collegati!*"
              )

              requests.post(
                  telegram_url,
                  json={
                      "chat_id": channel_id,
                      "text": message,
                      "parse_mode": "Markdown",
                  },
              )
              time.sleep(1)
      except Exception as e:
        print(f"Errore scanner {symbol}: {e}")
      time.sleep(0.3)
    time.sleep(15)


scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

# --- WEB DASHBOARD HTML & ROUTES ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ brand_name }} - Platform</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background-color: #0b0e14; color: #d1d5db; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid #1f2937; margin-bottom: 20px; flex-wrap: wrap; gap: 10px;}
        .logo { font-size: 20px; font-weight: bold; color: #38bdf8; text-transform: uppercase; }
        .nav-links a { color: #38bdf8; text-decoration: none; margin-left: 15px; font-weight: 600; background: #111827; padding: 8px 12px; border-radius: 6px; border: 1px solid #374151;}
        .panel { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 20px; margin-bottom: 25px; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; font-size: 13px; color: #9ca3af; margin-bottom: 5px; }
        .form-group input, .form-group select { width: 100%; background: #0b0e14; border: 1px solid #374151; color: #fff; padding: 10px; border-radius: 6px; }
        .btn { background: #2563eb; color: #fff; border: none; padding: 10px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; }
        .btn-green { background: #059669; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 12px; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .score-long { background: rgba(16, 185, 129, 0.2); color: #10b981; padding: 3px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;}
        .score-short { background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 3px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;}
        .score-neutral { background: rgba(107, 114, 128, 0.2); color: #9ca3af; padding: 3px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;}
        .flash { background: #1e3a8a; color: #93c5fd; padding: 10px; border-radius: 6px; margin-bottom: 15px; }
    </style>
    <meta http-equiv="refresh" content="5">
</head>
<body>
    <div class="header">
        <div class="logo">⚡ {{ brand_name }} (Auto-Trading VIP)</div>
        <div class="nav-links">
            {% if session.get('user_id') %}
                <span>Utente: <strong>{{ session.get('username') }}</strong></span>
                <a href="/settings">⚙️ Configura API BingX</a>
                <a href="/logout" style="color: #ef4444;">Esci</a>
            {% else %}
                <a href="/login">Accedi</a>
                <a href="/register">Registrati</a>
            {% endif %}
        </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    {% block content %}{% endblock %}
</body>
</html>
"""

INDEX_HTML = (
    HTML_TEMPLATE.replace(
        "{% block content %}{% endblock %}",
        """
    <div class="panel" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;">
        <div>
            <h3 style="color: #fff; margin-bottom: 5px;">Pannello di Controllo Scanner</h3>
            <p style="font-size: 13px; color: #9ca3af;">Soglia Confluenza attuale: <strong style="color: #f97316;">{{ threshold }}%</strong></p>
        </div>
        <form action="/set-threshold" method="POST" style="display: flex; gap: 10px; align-items: center;">
            <input type="number" name="threshold" min="40" max="99" value="{{ threshold }}" style="width: 70px; text-align: center; background: #0b0e14; border: 1px solid #374151; color: #fff; padding: 8px; border-radius: 6px;">
            <button type="submit" class="btn">Aggiorna</button>
        </form>
    </div>

    <h3 style="margin-bottom: 15px; color: #fff; font-size: 16px;">Crypto Assets Live (Multi-TF BingX)</h3>
    <div class="grid">
        {% for symbol, data in market_data.items() %}
        <div class="card">
            <div class="card-header">
                <span style="font-weight: bold; color: #fff;">{{ data.symbol }}</span>
                <span class="{% if data.action == 'LONG' %}score-long{% elif data.action == 'SHORT' %}score-short{% else %}score-neutral{% endif %}">
                    {{ data.score }}% {{ data.rating }}
                </span>
            </div>
            <div style="font-size: 12px; color: #9ca3af; display: flex; flex-direction: column; gap: 4px;">
                <div style="display: flex; justify-content: space-between;"><span>Prezzo:</span><strong style="color: #fff;">${{ "%.4f"|format(data.price) if data.price < 1 else "%.2f"|format(data.price) }}</strong></div>
                <div style="display: flex; justify-content: space-between;"><span>RSI 1H:</span><strong style="color: #38bdf8;">{{ data.rsi_1h }}</strong></div>
                <div style="display: flex; justify-content: space-between;"><span>RSI 4H:</span><strong style="color: #f97316;">{{ data.rsi_4h }}</strong></div>
            </div>
        </div>
        {% endfor %}
    </div>
""",
)

AUTH_HTML = HTML_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
    <div class="panel" style="max-width: 400px; margin: 40px auto;">
        <h3 style="color: #fff; margin-bottom: 20px; text-align: center;">{{ title }}</h3>
        <form method="POST">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit" class="btn" style="width: 100%;">{{ title }}</button>
        </form>
    </div>
""",
)

SETTINGS_HTML = HTML_TEMPLATE.replace(
    "{% block content %}{% endblock %}",
    """
    <div class="panel" style="max-width: 500px; margin: 20px auto;">
        <h3 style="color: #fff; margin-bottom: 15px;">Configurazione API BingX & Auto-Trading</h3>
        <p style="font-size: 13px; color: #9ca3af; margin-bottom: 20px;">Inserisci le tue chiavi API di BingX (assicurati di abilitare solo il trading Futures e di disabilitare i prelievi).</p>
        <form method="POST">
            <div class="form-group">
                <label>BingX API Key</label>
                <input type="text" name="api_key" value="{{ user.bingx_api_key or '' }}" placeholder="Inserisci API Key">
            </div>
            <div class="form-group">
                <label>BingX Secret Key</label>
                <input type="password" name="secret_key" value="{{ user.bingx_secret_key or '' }}" placeholder="Inserisci Secret Key">
            </div>
            <div class="form-group">
                <label>Stato Auto-Trading</label>
                <select name="auto_trading">
                    <option value="0" {% if user.auto_trading == 0 %}selected{% endif %}>Disattivato</option>
                    <option value="1" {% if user.auto_trading == 1 %}selected{% endif %}>Attivato (Automatico sui Segnali)</option>
                </select>
            </div>
            <button type="submit" class="btn btn-green" style="width: 100%;">Salva Configurazione</button>
        </form>
    </div>
""",
)


@app.route("/")
def home():
  global current_threshold, market_data_cache, BRAND_NAME
  return render_template_string(
      INDEX_HTML,
      brand_name=BRAND_NAME,
      threshold=current_threshold,
      market_data=market_data_cache,
  )


@app.route("/set-threshold", methods=["POST"])
def set_threshold():
  global current_threshold
  try:
    val = int(request.form.get("threshold"))
    if 40 <= val <= 99:
      current_threshold = val
  except ValueError:
    pass
  return redirect(url_for("home"))


@app.route("/register", methods=["GET", "POST"])
def register():
  if request.method == "POST":
    username = request.form.get("username")
    password = generate_password_hash(request.form.get("password"))
    try:
      conn = sqlite3.connect("trading_bot.db")
      cursor = conn.cursor()
      cursor.execute(
          "INSERT INTO users (username, password) VALUES (?, ?)",
          (username, password),
      )
      conn.commit()
      conn.close()
      flash("Registrazione completata! Effettua il login.")
      return redirect(url_for("login"))
    except Exception:
      flash("Username già esistente.")
  return render_template_string(
      AUTH_HTML, brand_name=BRAND_NAME, title="Registrazione"
  )


@app.route("/login", methods=["GET", "POST"])
def login():
  if request.method == "POST":
    username = request.form.get("username")
    password = request.form.get("password")
    conn = sqlite3.connect("trading_bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, password FROM users WHERE username = ?", (username,)
    )
    user = cursor.fetchone()
    conn.close()
    if user and check_password_hash(user[1], password):
      session["user_id"] = user[0]
      session["username"] = username
      flash("Login effettuato con successo!")
      return redirect(url_for("home"))
    flash("Credenziali non valide.")
  return render_template_string(AUTH_HTML, brand_name=BRAND_NAME, title="Login")


@app.route("/logout")
def logout():
  session.clear()
  flash("Logout effettuato.")
  return redirect(url_for("home"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
  if "user_id" not in session:
    return redirect(url_for("login"))

  conn = sqlite3.connect("trading_bot.db")
  cursor = conn.cursor()

  if request.method == "POST":
    api_key = request.form.get("api_key")
    secret_key = request.form.get("secret_key")
    auto_trading = int(request.form.get("auto_trading", 0))
    cursor.execute(
        "UPDATE users SET bingx_api_key = ?, bingx_secret_key = ?, auto_trading"
        " = ? WHERE id = ?",
        (api_key, secret_key, auto_trading, session["user_id"]),
    )
    conn.commit()
    flash("Impostazioni salvate con successo!")

  cursor.execute(
      "SELECT bingx_api_key, bingx_secret_key, auto_trading FROM users WHERE id"
      " = ?",
      (session["user_id"],,),
  )
  u_data = cursor.fetchone()
  conn.close()

  user_obj = {
      "bingx_api_key": u_data[0],
      "bingx_secret_key": u_data[1],
      "auto_trading": u_data[2],
  }
  return render_template_string(
      SETTINGS_HTML, brand_name=BRAND_NAME, user=user_obj
  )


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
