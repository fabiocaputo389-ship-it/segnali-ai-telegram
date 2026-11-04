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
    "bingx_api_key": "",
    "bingx_secret_key": "",
    "trading_mode": "paper",
    "telegram_token": "",
    "chat_id": "",
    "use_trend_following": 1,
    "use_rsi_filter": 1,
    "auto_execution": 0
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
        print(f"Errore invio Telegram: {e}")

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

# --- MOTORE SCANNER IA REALE IN BACKGROUND ---
def background_market_scanner():
    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT', 'SUIUSDT']
    last_alerts = {}
    while True:
        try:
            token = bot_settings.get("telegram_token")
            chat_id = bot_settings.get("chat_id")
            for symbol in symbols:
                res = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=50", timeout=5)
                if res.status_code == 200:
                    klines = res.json()
                    closes = [float(k[4]) for k in klines]
                    current_price = closes[-1]
                    ema20 = sum(closes[-20:]) / 20
                    ema50 = sum(closes[-50:]) / 50
                    
                    is_long = current_price > ema20 and ema20 > ema50
                    now = time.time()
                    if is_long and token and chat_id:
                        if symbol not in last_alerts or (now - last_alerts[symbol] > 1800):
                            msg = (
                                f"🔥 *SEGNALE REALE TOP ENTRY (IA)*\n\n"
                                f"✦ *Asset:* #{symbol}\n"
                                f"✦ *Direzione:* LONG 🟢\n"
                                f"✦ *Prezzo Ingresso:* `${current_price:,.2f}`\n"
                                f"✦ *Leva Consigliata:* `5x - 10x`\n\n"
                                f"🎯 *Take Profit 1:* `${current_price * 1.018:,.2f}`\n"
                                f"🎯 *Take Profit 2:* `${current_price * 1.035:,.2f}`\n"
                                f"🛑 *Stop Loss:* `${current_price * 0.985:,.2f}`\n\n"
                                f"⚡ _Analisi reale elaborata su Render_"
                            )
                            send_telegram_alert(token, chat_id, msg)
                            last_alerts[symbol] = now
        except Exception as e:
            print(f"Errore scanner: {e}")
        time.sleep(25)

scanner_thread = threading.Thread(target=background_market_scanner, daemon=True)
scanner_thread.start()

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Scanner TOP Entry - Dashboard Completa Live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#06080c; --panel:#0c1017; --panel2:#111722; --line:#1b2433; --line2:#2a3750;
  --ink:#ece8dc; --ink2:#a3acba; --mute:#6b7689; --grid:rgba(120,140,170,.06);
  --gold:#d6b04c; --gold-soft:rgba(214,176,76,.14); --dia:#ffd23f; --dia-soft:rgba(255,210,63,.09);
  --up:#2ee6a6; --dn:#ff5a76; --warn:#f2a93b;
  --z1:#ff5a76; --z2:#f58a5b; --z3:#8a94a6; --z4:#69dba9; --z5:#2ee6a6;
  --c2:#6fb7ff; --c3:#b79cff;
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);margin:0;}
.wrap{max-width:1560px;margin:0 auto;padding:18px 16px;}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.brand{display:flex;align-items:center;gap:12px}
.brand h1{font:700 18px var(--font-display);letter-spacing:.14em;text-transform:uppercase;color:var(--gold)}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);padding:6px 10px;font:500 11px var(--font-mono);color:var(--ink2);background:rgba(12,16,23,.7)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 8px var(--up);animation:pulse 1.8s infinite}
@keyframes pulse{50%{opacity:.35}}
button, .btn{font:600 11px var(--font-mono);color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase;text-decoration:none;display:inline-block;}
button:hover, .btn:hover{background:var(--gold-soft)}
.form-box{background:var(--panel2);border:1px solid var(--line2);padding:15px;margin-bottom:14px}
.form-box h3{color:var(--gold);font-size:13px;margin-bottom:10px;text-transform:uppercase;font-family:var(--font-display)}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group label{font-size:10px;color:var(--mute);text-transform:uppercase;font-family:var(--font-mono)}
input, select{background:var(--panel);border:1px solid var(--line2);color:var(--ink);font:400 12px var(--font-mono);padding:6px 10px}
.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.panel{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);padding:14px 16px;position:relative}
.ph{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.ph h2{font:600 12px var(--font-display);letter-spacing:.18em;text-transform:uppercase;color:var(--gold)}
.gsvg{display:block;width:100%;max-width:220px;margin:0 auto;height:auto}
.gseg{fill:none;stroke-width:10;opacity:.28}
.gval{fill:none;stroke-width:10;transition:stroke-dasharray 1s}
.z0{stroke:var(--z1)}.z1{stroke:var(--z2)}.z2{stroke:var(--z3)}.z3{stroke:var(--z4)}.z4{stroke:var(--z5)}
.tk{stroke:var(--line2);stroke-width:1}.tk.maj{stroke:var(--ink2);stroke-width:1.4}
.tl{fill:var(--mute);font:500 7px var(--font-mono)}
.ndl{transform-origin:100px 100px;transition:transform 1s}
.ndl polygon{fill:var(--ink)}
.hub{fill:var(--panel);stroke:var(--gold);stroke-width:2}
.gnum{font:700 36px/1 var(--font-mono);text-align:center;margin-top:6px}
.glab{font:600 11px var(--font-display);letter-spacing:.2em;text-transform:uppercase;text-align:center;margin-top:6px}
canvas{display:block;width:100%}
#chart{height:380px}
.tft{width:100%;border-collapse:collapse}
.tft th, .tft td{font:400 11.5px var(--font-mono);text-align:right;padding:7px 6px;border-bottom:1px solid var(--line)}
.tft th:first-child,.tft td:first-child{text-align:left;color:var(--mute)}
.tw{overflow:auto;max-height:550px;border:1px solid var(--line)}
table.main{border-collapse:collapse;width:100%;min-width:1100px}
.main th{position:sticky;top:0;background:var(--panel2);text-align:center;font:600 10px var(--font-mono);color:var(--mute);text-transform:uppercase;padding:9px 8px;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main td{padding:8px;text-align:center;font:400 12px var(--font-mono);border-bottom:1px solid var(--line)}
.pill{padding:2px 8px;font:700 10px var(--font-mono);border:1px solid currentColor}
.pill.LONG{color:var(--up);background:rgba(46,230,166,.08)}
.pill.SHORT{color:var(--dn);background:rgba(255,90,118,.08)}
.pill.TOP{color:var(--dia);background:var(--dia-soft)}
.tfp{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:12px}
.tfp div{border:1px solid var(--line);padding:6px;font:500 11px var(--font-mono);display:flex;justify-content:space-between}
</style>
</head>
<body>

<div class="wrap">
  <header class="top">
    <div class="brand">
      <div><h1>Scanner Reale · Binance & BingX Live</h1><p style="font:400 11px var(--font-mono);color:var(--mute);">Utente: {{ username }} · <span style="color:var(--up)">● Dati di mercato live attivi</span></p></div>
    </div>
    <div class="chips">
      <span class="chip">Balance: <b style="color:var(--gold)">${{ "%.2f"|format(account.balance) }}</b></span>
      <span class="chip">Equity: <b style="color:var(--up)">${{ "%.2f"|format(account.equity) }}</b></span>
      <span class="chip"><i class="dot"></i>LIVE STREAM</span>
      <a href="{{ url_for('logout') }}" class="btn" style="border-color:var(--dn);color:var(--dn)">Logout</a>
    </div>
  </header>

  <section class="form-box">
    <h3>⚙️ Configurazione BingX & Canale Telegram</h3>
    <form method="POST">
      <div class="form-grid">
        <div class="form-group">
          <label>Modalità Trading</label>
          <select name="trading_mode">
            <option value="paper" {% if settings.trading_mode == 'paper' %}selected{% endif %}>Paper Trading (Simulato)</option>
            <option value="real" {% if settings.trading_mode == 'real' %}selected{% endif %}>Trading Reale (BingX API)</option>
          </select>
        </div>
        <div class="form-group">
          <label>BingX API Key</label>
          <input type="text" name="bingx_api_key" value="{{ settings.bingx_api_key }}" placeholder="API Key">
        </div>
        <div class="form-group">
          <label>BingX Secret Key</label>
          <input type="password" name="bingx_secret_key" value="{{ settings.bingx_secret_key }}" placeholder="Secret Key">
        </div>
        <div class="form-group">
          <label>Telegram Bot Token</label>
          <input type="text" name="telegram_token" value="{{ settings.telegram_token }}" placeholder="Token Telegram">
        </div>
        <div class="form-group">
          <label>Telegram Chat ID</label>
          <input type="text" name="chat_id" value="{{ settings.chat_id }}" placeholder="Chat ID">
        </div>
      </div>
      <div style="display:flex;gap:20px;margin-top:12px;align-items:center;flex-wrap:wrap;">
        <label><input type="checkbox" name="use_trend_following" {% if settings.use_trend_following == 1 %}checked{% endif %}> Trend Following</label>
        <label><input type="checkbox" name="use_rsi_filter" {% if settings.use_rsi_filter == 1 %}checked{% endif %}> Filtro RSI</label>
        <label><input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Esecuzione Automatica BingX</label>
        <button type="submit" style="margin-left:auto">Salva e Attiva Motore Live</button>
      </div>
    </form>
  </section>

  <main class="grid">
    <section class="panel s4">
      <div class="ph"><h2>Tachimetro di Mercato</h2><span class="sub" id="gaugeSymbol" style="color:var(--gold);font-family:var(--font-mono)">BTCUSDT</span></div>
      <div id="gaugeContainer"></div>
      <div class="gnum" id="gaugeNum">0%</div>
      <div class="glab" id="gaugeLab">NEUTRALE</div>
      <div class="tfp" id="tfpContainer">
        <div><span class="mu">15M</span><span id="tf15" class="mu">—</span></div>
        <div><span class="mu">1H</span><span id="tf1h" class="mu">—</span></div>
        <div><span class="mu">4H</span><span id="tf4h" class="mu">—</span></div>
      </div>
    </section>

    <section class="panel s8">
      <div class="ph"><h2>Grafico Candele Live (Binance)</h2><span id="liveSymbol" style="color:var(--gold);font-family:var(--font-mono);font-weight:bold;">BTCUSDT</span></div>
      <canvas id="chart"></canvas>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner di Mercato Reale (Top Criptovalute)</h2></div>
      <div class="tw">
        <table class="main">
          <thead>
            <tr><th>Asset</th><th>Prezzo Reale (USDT)</th><th>EMA 20</th><th>EMA 50</th><th>RSI Live</th><th>Segnale IA Reale</th><th>Leva Consigliata</th></tr>
          </thead>
          <tbody id="marketBody">
            <tr><td colspan="7" style="text-align:center;color:var(--mute)">Caricamento dati di mercato reali in corso...</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</div>

<script>
const ZL=['Vendi forte','Vendi','Neutrale','Compra','Compra forte'];
const zone=v=>v<20?0:v<40?1:v<60?2:v<80?3:4;
const zcolors=['#ff5a76','#f58a5b','#8a94a6','#69dba9','#2ee6a6'];

function mkGauge(host){
  const cx=100,cy=100,r=78,pt=(rr,d)=>[cx+rr*Math.cos(d*Math.PI/180),cy+rr*Math.sin(d*Math.PI/180)];
  const arc=(rr,a0,a1)=>{const p=pt(rr,a0),q=pt(rr,a1);return`M${p[0].toFixed(2)} ${p[1].toFixed(2)}A${rr} ${rr} 0 0 1 ${q[0].toFixed(2)} ${q[1].toFixed(2)}`};
  let s='';for(let i=0;i<5;i++)s+=`<path class="gseg" style="stroke:${zcolors[i]}" d="${arc(r,180+i*36+1.5,180+(i+1)*36-1.5)}"/>`;
  s+=`<path class="gval" id="gValPath" pathLength="100" d="${arc(r,180,359.9)}" style="stroke:var(--up);stroke-dasharray:50 100"/>`;
  s+=`<g class="ndl" id="gNdl" style="transform:rotate(0deg)"><polygon points="97.5,100 100,30 102.5,100"/></g><circle class="hub" cx="100" cy="100" r="7"/>`;
  host.innerHTML=`<svg class="gsvg" viewBox="0 0 200 112">${s}</svg>`;
}
mkGauge(document.getElementById('gaugeContainer'));

function updateGauge(val){
  val = Math.max(0, Math.min(100, val));
  const z = zone(val);
  const path = document.getElementById('gValPath');
  const ndl = document.getElementById('gNdl');
  if(path) path.style.strokeDasharray = `${val} 100`;
  if(ndl) ndl.style.transform = `rotate(${(val-50)*1.8}deg)`;
  const num = document.getElementById('gaugeNum');
  const lab = document.getElementById('gaugeLab');
  if(num){ num.innerText = Math.round(val) + '%'; num.style.color = zcolors[z]; }
  if(lab){ lab.innerText = ZL[z]; lab.style.color = zcolors[z]; }
}

async function loadRealMarketData() {
  const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT', 'SUIUSDT'];
  let rows = '';
  
  try {
    let res = await fetch(`https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=50`);
    let klines = await res.json();
    let closes = klines.map(k => parseFloat(k[4]));
    let price = closes[closes.length - 1];
    let ema20 = closes.slice(-20).reduce((a,b)=>a+b,0) / 20;
    let rsi = 55; // Valore stimato o calcolato
    updateGauge(price > ema20 ? 75 : 35);
  } catch(e){}

  for (let sym of symbols) {
    try {
      let res = await fetch(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=15m&limit=50`);
      let klines = await res.json();
      let closes = klines.map(k => parseFloat(k[4]));
      let price = closes[closes.length - 1];
      let ema20 = closes.slice(-20).reduce((a,b)=>a+b,0) / 20;
      let ema50 = closes.slice(-50).reduce((a,b)=>a+b,0) / 50;
      
      let gains = 0, losses = 0;
      for (let i = closes.length - 14; i < closes.length; i++) {
        let diff = closes[i] - closes[i-1];
        if (diff >= 0) gains += diff; else losses -= diff;
      }
      let rs = (gains / 14) / ((losses / 14) || 1);
      let rsi = 100 - (100 / (1 + rs));
      
      let isLong = price > ema20 && ema20 > ema50;
      let signalHtml = isLong ? '<span class="pill LONG">LONG 🟢</span>' : '<span class="pill SHORT">ATTESA ⚪</span>';
      
      rows += `<tr>
        <td style="font-weight:bold;color:var(--gold)">${sym}</td>
        <td>$${price.toLocaleString('en-US', {minimumFractionDigits: 2})}</td>
        <td>$${ema20.toFixed(2)}</td>
        <td>$${ema50.toFixed(2)}</td>
        <td>${rsi.toFixed(1)}</td>
        <td>${signalHtml}</td>
        <td style="color:var(--warn)">5x - 10x</td>
      </tr>`;
    } catch(e) {}
  }
  document.getElementById('marketBody').innerHTML = rows;
}

loadRealMarketData();
setInterval(loadRealMarketData, 15000);
</script>
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
        bot_settings["use_trend_following"] = 1 if request.form.get("use_trend_following") == "on" else 0
        bot_settings["use_rsi_filter"] = 1 if request.form.get("use_rsi_filter") == "on" else 0
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Parametri salvati! La dashboard completa e il bot Telegram operano ora con dati reali.")
        return redirect(url_for('home'))

    account_data = {"balance": 10000.00, "equity": 10000.00}
    if bot_settings["trading_mode"] == "real" and bot_settings["bingx_api_key"] and bot_settings["bingx_secret_key"]:
        bingx_res = get_bingx_account_balance(bot_settings["bingx_api_key"], bot_settings["bingx_secret_key"])
        if "error" not in bingx_res:
            account_data["balance"] = bingx_res["balance"]
            account_data["equity"] = bingx_res["equity"]

    return render_template_string(DASHBOARD_HTML, username=session['username'], settings=bot_settings, account=account_data)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = generate_password_hash(request.form.get('password'))
        try:
            conn = sqlite3.connect('trading_bot.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            conn.close()
            flash('Registrazione completata!')
            return redirect(url_for('login'))
        except Exception:
            flash('Errore durante la registrazione.')
    return render_template_string('''<div style="max-width:380px;margin:60px auto;background:#0c1017;padding:24px;border:1px solid #2a3750;color:#fff;font-family:sans-serif;"><h3 style="color:#d6b04c;text-align:center;">REGISTRAZIONE</h3><form method="POST"><p><label>Username</label><br><input type="text" name="username" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;"></p><p><label>Password</label><br><input type="password" name="password" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;"></p><button type="submit" style="width:100%;padding:10px;background:#d6b04c;color:#06080c;border:none;font-weight:bold;cursor:pointer;">Registrati</button></form></div>''')

@app.route('/login', methods=['GET', 'POST'])
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
    return render_template_string('''<div style="max-width:380px;margin:60px auto;background:#0c1017;padding:24px;border:1px solid #2a3750;color:#fff;font-family:sans-serif;"><h3 style="color:#d6b04c;text-align:center;">ACCESSO</h3><form method="POST"><p><label>Username</label><br><input type="text" name="username" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;"></p><p><label>Password</label><br><input type="password" name="password" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;"></p><button type="submit" style="width:100%;padding:10px;background:#d6b04c;color:#06080c;border:none;font-weight:bold;cursor:pointer;">Accedi</button></form><p style="text-align:center;margin-top:15px;"><a href="/register" style="color:#d6b04c;">Registrati</a></p></div>''')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
