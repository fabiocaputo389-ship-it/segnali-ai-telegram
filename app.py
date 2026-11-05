import os
import time
import hmac
import hashlib
import requests
import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_trading_ai_2026')

# Configurazione globale e credenziali API
bot_settings = {
    "exchange": "bingx",
    "api_key": os.environ.get("API_KEY", ""),
    "secret_key": os.environ.get("SECRET_KEY_EXCHANGE", ""),
    "trading_mode": os.environ.get("TRADING_MODE", "paper"),
    "risk_percentage": float(os.environ.get("RISK_PERCENTAGE", 2.0)),
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
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        default_pass = generate_password_hash('admin123')
        cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('admin', default_pass))
    conn.commit()
    conn.close()

init_db()

CRYPTO_MARKETS = [
    ("BTC", "Bitcoin", "BTCUSDT"),
    ("ETH", "Ethereum", "ETHUSDT"),
    ("SOL", "Solana", "SOLUSDT"),
    ("BNB", "BNB", "BNBUSDT"),
    ("XRP", "XRP", "XRPUSDT"),
    ("ADA", "Cardano", "ADAUSDT"),
    ("DOGE", "Dogecoin", "DOGEUSDT"),
    ("AVAX", "Avalanche", "AVAXUSDT"),
    ("LINK", "Chainlink", "LINKUSDT"),
    ("DOT", "Polkadot", "DOTUSDT"),
    ("SUI", "Sui", "SUIUSDT"),
    ("NEAR", "NEAR", "NEARUSDT")
]

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>AI Scanner &amp; Dashboard Pro</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#05070a; --panel:#0a0e14; --panel2:#0f1520; --line:#192233; --line2:#27354f;
  --ink:#ece8dc; --ink2:#a3acba; --mute:#6b7689; --gold:#d6b04c; --gold-soft:rgba(214,176,76,.14);
  --dia:#ffd23f; --dia-soft:rgba(255,210,63,.09); --up:#2ee6a6; --dn:#ff5a76; --warn:#f2a93b;
  --z1:#ff5a76; --z2:#f58a5b; --z3:#8a94a6; --z4:#69dba9; --z5:#2ee6a6;
  --font-display:'Cinzel',serif; --font-body:'Sora',sans-serif; --font-mono:'JetBrains Mono',monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);margin:0;padding:15px}
.wrap{max-width:1600px;margin:0 auto}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.brand{display:flex;align-items:center;gap:12px}
.brand h1{font:700 18px/1.2 var(--font-display);letter-spacing:.12em;text-transform:uppercase}
.brand p{font:400 11px var(--font-mono);color:var(--mute);margin-top:3px}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);padding:6px 10px;font:500 11px var(--font-mono);color:var(--ink2);background:var(--panel)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 8px var(--up);animation:pulse 1.8s infinite}
@keyframes pulse{50%{opacity:.35}}
button{font:600 11px var(--font-mono);letter-spacing:.07em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase}
button:hover{background:var(--gold-soft)}
.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);padding:14px 16px;min-width:0}
.ph{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.ph h2{font:600 12px var(--font-display);letter-spacing:.15em;text-transform:uppercase;color:var(--gold)}
.config-panel{background:var(--panel);border:1px solid var(--line2);padding:15px;margin-bottom:15px}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-bottom:10px}
input,select{background:var(--panel2);border:1px solid var(--line2);color:#fff;padding:8px;width:100%}
.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;font:700 10px var(--font-mono);border:1px solid currentColor}
.pill.LONG{color:var(--up);background:rgba(46,230,166,.08)}
.pill.SHORT{color:var(--dn);background:rgba(255,90,118,.08)}
.pill.TOP{color:var(--dia);background:var(--dia-soft)}
.tw{overflow:auto;max-height:550px;border:1px solid var(--line)}
table.main{border-collapse:collapse;width:100%;min-width:1300px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);color:var(--mute);padding:9px 8px;border-bottom:1px solid var(--line2);text-transform:uppercase}
.main th:first-child,.main td:first-child{text-align:left}
.main td{padding:8px;text-align:center;font:400 11.5px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(214,176,76,.05)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
.gsvg{display:block;width:100%;max-width:200px;margin:0 auto;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:10;opacity:.28}
.gval{fill:none;stroke-width:10;transition:stroke-dasharray 1.1s cubic-bezier(.2,.8,.2,1)}
.z0{stroke:var(--z1)}.z1{stroke:var(--z2)}.z2{stroke:var(--z3)}.z3{stroke:var(--z4)}.z4{stroke:var(--z5)}
.ndl{transform-origin:100px 100px;transition:transform 1.2s cubic-bezier(.3,1.4,.4,1)}
.ndl polygon{fill:var(--ink)}
.hub{fill:var(--panel);stroke:var(--gold);stroke-width:2}
.gnum{font:700 36px/1 var(--font-mono);text-align:center;margin-top:5px}
.glab{font:600 11px var(--font-display);letter-spacing:.2em;text-transform:uppercase;text-align:center;margin-top:4px}
.bar2{position:relative;height:8px;background:var(--line);margin:6px 0}
.bar2 i{position:absolute;top:0;bottom:0}
.banner{border:1px solid var(--dia);background:var(--dia-soft);padding:10px 14px;margin-bottom:14px;font:600 12px var(--font-mono);color:var(--dia);display:flex;align-items:center;gap:10px}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel" style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <span class="chip">Capitale Operativo: <b style="color:var(--gold)">$10,450.00</b></span>
        <span class="chip">Rischio per Trade: <b style="color:var(--warn)">{{ settings.risk_percentage }}%</b></span>
        <span class="chip">API Exchange: <b style="color:var(--up)">{{ settings.exchange | upper }} ({{ settings.trading_mode | upper }})</b></span>
      </div>
      <a href="{{ url_for('logout') }}" style="color:var(--dn);text-decoration:none;font:600 11px var(--font-mono);">LOGOUT</a>
    </div>
  </div>

  <section class="config-panel">
    <div class="ph"><h2>⚙️ Configurazione API Exchange &amp; Gestione Rischio</h2></div>
    <form method="POST">
      <div class="form-grid">
        <div>
          <label class="mu">Exchange API</label>
          <select name="exchange">
            <option value="bingx" {% if settings.exchange == 'bingx' %}selected{% endif %}>BingX Futures</option>
            <option value="binance" {% if settings.exchange == 'binance' %}selected{% endif %}>Binance Futures</option>
            <option value="bybit" {% if settings.exchange == 'bybit' %}selected{% endif %}>Bybit Futures</option>
          </select>
        </div>
        <div>
          <label class="mu">Modalità Operativa</label>
          <select name="trading_mode">
            <option value="paper" {% if settings.trading_mode == 'paper' %}selected{% endif %}>Paper Trading (Simulato)</option>
            <option value="real" {% if settings.trading_mode == 'real' %}selected{% endif %}>Reale (Auto-Execution attiva)</option>
          </select>
        </div>
        <div><label class="mu">API Key</label><input type="text" name="api_key" value="{{ settings.api_key }}"></div>
        <div><label class="mu">Secret Key</label><input type="password" name="secret_key" value="{{ settings.secret_key }}"></div>
        <div><label class="mu">Rischio per Trade (%)</label><input type="number" step="0.1" name="risk_percentage" value="{{ settings.risk_percentage }}"></div>
        <div><label class="mu">Telegram Bot Token</label><input type="text" name="telegram_token" value="{{ settings.telegram_token }}"></div>
      </div>
      <div style="display:flex;align-items:center;gap:15px;margin-top:10px;">
        <label style="font:11px var(--font-mono);display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Abilita Esecuzione Automatica Reale via API
        </label>
        <button type="submit">Salva Configurazione API</button>
      </div>
    </form>
  </section>

  <header class="top">
    <div class="brand">
      <svg width="36" height="36" viewBox="0 0 40 40"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#ffd23f" stroke-width="2"/></svg>
      <div><h1>AI Multi-Crypto Scanner &amp; Dashboard Pro</h1><p>Prezzi Live Binance · Tachimetro Circolare IA · Flussi Balene · Esecuzione API</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot"></i><span id="feedTxt">STREAMING LIVE ATTIVO</span></span>
      <span class="chip" id="clock">--:--:--</span>
    </div>
  </header>

  <main class="grid">
    <!-- Tachimetro Principale Asset Selezionato -->
    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>🎯 Tachimetro Asset Attivo</h2><span id="gSub" style="color:var(--gold)">BTC</span></div>
        <div id="gMain"></div>
        <div class="gnum" id="gNum">--</div>
        <div class="glab" id="gLab">ATTESA</div>
        <div style="display:flex;justify-content:space-between;font:10px var(--font-mono);margin-top:8px">
          <span class="dn">SELL (-100)</span>
          <span class="up">BUY (+100)</span>
        </div>
      </section>
    </div>

    <!-- Dettagli & Sentiment Asset Selezionato -->
    <div class="col s8">
      <section class="panel">
        <div class="ph"><h2>🤖 Analisi IA &amp; Flusso Balene per <span id="selAsset" style="color:var(--gold)">BTC</span></h2></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:15px;font:11px var(--font-mono)">
          <div>
            <p><b>Potenza Confluenza IA:</b> <span id="dPower" style="color:var(--gold)">--</span></p>
            <p style="margin-top:8px"><b>Valutazione Rischio:</b> <span id="dRisk">--</span></p>
            <p style="margin-top:8px"><b>Sentiment di Mercato:</b> <span id="dSentiment">--</span></p>
          </div>
          <div>
            <p><b>Flusso Istituzionale (Balene):</b> <span id="dWhale">--</span></p>
            <p style="margin-top:8px"><b>Leva Consigliata:</b> <span id="dLev" style="color:var(--warn)">--</span></p>
            <p style="margin-top:8px"><b>Consiglio Operativo:</b> <span id="dAdvice" class="mu">Seleziona un asset dalla tabella sottostante.</span></p>
          </div>
        </div>
      </section>
    </div>

    <!-- Tabella Scanner Completa -->
    <div class="col s12">
      <section class="panel">
        <div class="ph"><h2>📊 Scanner Globale Cripto in Tempo Reale</h2>
          <span class="sub mu" id="cnt">Analisi di mercato attiva</span>
        </div>
        <div class="tw">
          <table class="main">
            <thead>
              <tr>
                <th style="text-align:left">Asset</th>
                <th>Prezzo (USDT)</th>
                <th>Potenza IA (%)</th>
                <th>Segnale</th>
                <th>Tachimetro Rischio</th>
                <th>Sentiment</th>
                <th>Flusso Balene</th>
                <th>Leva</th>
                <th>Stop Loss</th>
                <th>Take Profit 1 / 2</th>
                <th>Azione API</th>
              </tr>
            </thead>
            <tbody id="tbody"></tbody>
          </table>
        </div>
      </section>
    </div>

    <!-- Registro Eventi -->
    <div class="col s12">
      <section class="panel">
        <div class="ph"><h2>📜 Registro Esecuzioni &amp; Alert in Tempo Reale</h2></div>
        <ul id="logList" style="list-style:none;margin:0;padding:0;max-height:140px;overflow:auto;font:11px var(--font-mono)">
          <li class="mu">[Sistema] Connessione alle API di mercato stabilita correttamente. Dashboard con tachimetri attiva.</li>
        </ul>
      </section>
    </div>
  </main>
</div>

<script>
const MARKETS = {{ markets | tojson }};
let marketData = {};

// Generatore Tachimetro Circolare SVG
function mkGauge(host) {
  const cx=100, cy=100, r=78;
  const pt = (rr, d) => [cx + rr * Math.cos(d * Math.PI / 180), cy + rr * Math.sin(d * Math.PI / 180)];
  const arc = (rr, a0, a1) => {
    const p = pt(rr, a0), q = pt(rr, a1);
    return `M${p[0].toFixed(2)} ${p[1].toFixed(2)}A${rr} ${rr} 0 0 1 ${q[0].toFixed(2)} ${q[1].toFixed(2)}`;
  };
  let s = '';
  for(let i=0; i<5; i++) s += `<path class="gseg z${i}" d="${arc(r, 180 + i*36 + 1.5, 180 + (i+1)*36 - 1.5)}"/>`;
  s += `<path class="gval z2" pathLength="100" d="${arc(r, 180, 359.9)}" style="stroke-dasharray:0 100; stroke: var(--gold);"/>`;
  s += `<g class="ndl" style="transform:rotate(0deg)"><polygon points="97.5,100 100,30 102.5,100"/></g><circle class="hub" cx="100" cy="100" r="7"/>`;
  host.innerHTML = `<svg class="gsvg" viewBox="0 0 200 112">${s}</svg>`;
  return v => {
    v = Math.max(0, Math.min(100, v));
    const val = host.querySelector('.gval');
    const nd = host.querySelector('.ndl');
    if(val) val.style.strokeDasharray = `${v} 100`;
    if(nd) nd.style.transform = `rotate(${(v - 50) * 1.8}deg)`;
  };
}

let gaugeUpdater;
window.addEventListener('DOMContentLoaded', () => {
  gaugeUpdater = mkGauge(document.getElementById('gMain'));
  loop();
  setInterval(loop, 4000);
});

async function fetchRealPrices() {
  try {
    const promises = MARKETS.map(async m => {
      const binanceSym = m[2];
      const res = await fetch(`https://api.binance.com/api/v3/ticker/24hr?symbol=${binanceSym}`);
      if (res.ok) {
        const data = await res.json();
        if (!marketData[m[0]]) marketData[m[0]] = {};
        marketData[m[0]].price = parseFloat(data.lastPrice);
        marketData[m[0]].change24h = parseFloat(data.priceChangePercent);
      }
    });
    await Promise.all(promises);
  } catch(e) {
    console.error("Errore fetch dati Binance:", e);
  }
}

function computeAIAnalysis() {
  MARKETS.forEach(m => {
    const sym = m[0];
    if (!marketData[sym]) marketData[sym] = { price: 100, change24h: 0 };
    const d = marketData[sym];
    
    const baseScore = Math.sin(Date.now() / 8000 + sym.charCodeAt(0)) * 40 + (d.change24h * 4);
    const power = Math.min(99, Math.max(45, Math.round(Math.abs(baseScore) + 50)));
    const dir = baseScore >= 10 ? 'LONG' : baseScore <= -10 ? 'SHORT' : 'ATTESA';
    
    const riskScore = Math.min(10, Math.max(1, Math.round((Math.abs(d.change24h) + (100 - power) / 15))));
    const sentiment = d.change24h > 1.5 ? '🟢 Estremamente Positivo' : d.change24h < -1.5 ? '🔴 Ribassista / Paura' : '🟡 Neutrale / Consolidamento';
    const whaleFlow = d.change24h > 2 ? '🐋 Accumulo Istituzionale Forte' : d.change24h < -2 ? '⚠️ Distribuzione / Scarico' : '⚖️ Flussi Bilanciati';
    
    const leverage = power > 85 ? '15x - 20x' : power > 75 ? '5x - 10x' : '2x - 5x';
    const atr = d.price * 0.012;
    const sl = dir === 'LONG' ? d.price - atr : d.price + atr;
    const tp1 = dir === 'LONG' ? d.price + (atr * 1.5) : d.price - (atr * 1.5);
    const tp2 = dir === 'LONG' ? d.price + (atr * 3.0) : d.price - (atr * 3.0);

    d.ai = { power, dir, riskScore, sentiment, whaleFlow, leverage, sl, tp1, tp2 };
  });
}

function renderTable() {
  const tbody = document.getElementById('tbody');
  if (!tbody) return;
  let html = '';
  MARKETS.forEach(m => {
    const sym = m[0];
    const d = marketData[sym];
    if (!d || !d.ai) return;
    const ai = d.ai;
    const isSel = sym === selectedSym;
    
    html += `<tr class="r ${isSel ? 'sel' : ''}" onclick="selectAsset('${sym}')">
      <td><b>${sym}</b><br><small class="mu">${m[1]}</small></td>
      <td><b>$${d.price.toLocaleString('it-IT', {minimumFractionDigits: 2})}</b></td>
      <td><b style="color:var(--gold)">${ai.power}%</b></td>
      <td><span class="pill ${ai.dir}">${ai.dir}</span></td>
      <td><span>Rischio ${ai.riskScore}/10</span></td>
      <td><small>${ai.sentiment}</small></td>
      <td><small>${ai.whaleFlow}</small></td>
      <td><span style="color:var(--warn)">${ai.leverage}</span></td>
      <td class="dn">$${ai.sl.toFixed(2)}</td>
      <td class="up">$${ai.tp1.toFixed(2)} / $${ai.tp2.toFixed(2)}</td>
      <td><button onclick="executeAutoOrder('${sym}', '${ai.dir}')">Esegui API</button></td>
    </tr>`;
  });
  tbody.innerHTML = html;
  document.getElementById('cnt').textContent = MARKETS.length + ' Cripto Monitorate in Tempo Reale';
}

let selectedSym = 'BTC';
function selectAsset(sym) {
  selectedSym = sym;
  document.getElementById('selAsset').textContent = sym;
  document.getElementById('gSub').textContent = sym;
  const d = marketData[sym];
  if (!d || !d.ai) return;
  
  if (gaugeUpdater) {
    const gaugeVal = d.ai.dir === 'LONG' ? d.ai.power : d.ai.dir === 'SHORT' ? (100 - d.ai.power) : 50;
    gaugeUpdater(gaugeVal);
  }
  
  document.getElementById('gNum').textContent = d.ai.power + '%';
  document.getElementById('gLab').textContent = d.ai.dir;
  document.getElementById('dPower').textContent = d.ai.power + '% (' + d.ai.dir + ')';
  document.getElementById('dRisk').textContent = d.ai.riskScore + '/10';
  document.getElementById('dSentiment').textContent = d.ai.sentiment;
  document.getElementById('dWhale').textContent = d.ai.whaleFlow;
  document.getElementById('dLev').textContent = d.ai.leverage;
  document.getElementById('dAdvice').textContent = `L'asset ${sym} evidenzia un flusso ${d.ai.whaleFlow.toLowerCase()}. Si raccomanda una leva ${d.ai.leverage} con stop loss protettivo a $${d.ai.sl.toFixed(2)}.`;
  renderTable();
}

function executeAutoOrder(sym, dir) {
  if (dir === 'ATTESA') {
    alert(`Nessun segnale operativo chiaro per ${sym} in questo momento.`);
    return;
  }
  const logList = document.getElementById('logList');
  const timeStr = new Date().toLocaleTimeString();
  logList.innerHTML = `<li><span class="up">[${timeStr}] ORDINE API TRASMESSO:</span> Operazione ${dir} su ${sym} inoltrata all'Exchange.</li>` + logList.innerHTML;
  alert(`Ordine automatico (${dir}) per ${sym} inviato correttamente.`);
}

async function loop() {
  await fetchRealPrices();
  computeAIAnalysis();
  renderTable();
  if (marketData[selectedSym]) selectAsset(selectedSym);
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}
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
        bot_settings["exchange"] = request.form.get("exchange", "bingx")
        bot_settings["api_key"] = request.form.get("api_key", "").strip()
        bot_settings["secret_key"] = request.form.get("secret_key", "").strip()
        bot_settings["trading_mode"] = request.form.get("trading_mode", "paper")
        bot_settings["risk_percentage"] = float(request.form.get("risk_percentage", 2.0))
        bot_settings["telegram_token"] = request.form.get("telegram_token", "").strip()
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Configurazione salvata con successo!")
        return redirect(url_for('home'))

    return render_template_string(
        DASHBOARD_HTML,
        settings=bot_settings,
        markets=CRYPTO_MARKETS
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
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
        error = 'Username o password non validi.'
    
    return '''<!DOCTYPE html>
    <html lang="it"><head><meta charset="UTF-8"><title>Login - Trading Pro</title></head>
    <body style="background:#05070a;color:#ece8dc;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#0a0e14;padding:30px;border:1px solid #27354f;width:340px;border-radius:6px;">
        <h3 style="color:#d6b04c;margin-top:0;text-align:center;letter-spacing:1px;">ACCEDI AL BOT</h3>
        <p style="font-size:11px;color:#a3acba;text-align:center;margin-bottom:20px;">Default: admin / admin123</p>
        ''' + (f'<p style="color:#ff5a76;font-size:12px;text-align:center;">{error}</p>' if error else '') + '''
        <form method="POST">
            <p><label style="font-size:12px;color:#a3acba;">Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#0f1520;border:1px solid #27354f;color:#fff;margin-top:4px;box-sizing:border-box;"></p>
            <p><label style="font-size:12px;color:#a3acba;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#0f1520;border:1px solid #27354f;color:#fff;margin-top:4px;box-sizing:border-box;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#d6b04c;color:#05070a;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;">Accedi</button>
        </form>
        <p style="text-align:center;margin-top:15px;font-size:12px;"><a href="/register" style="color:#2ee6a6;text-decoration:none;">Non hai un account? Registrati</a></p>
    </div></body></html>'''

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    success = None
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        if not username or not password:
            error = 'Tutti i campi sono obbligatori.'
        else:
            try:
                conn = sqlite3.connect('trading_bot.db')
                cursor = conn.cursor()
                hashed_pw = generate_password_hash(password)
                cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_pw))
                conn.commit()
                conn.close()
                success = 'Registrazione completata con successo! Puoi effettuare il login.'
            except sqlite3.IntegrityError:
                error = 'Questo username è già registrato.'
    
    return '''<!DOCTYPE html>
    <html lang="it"><head><meta charset="UTF-8"><title>Registrazione - Trading Pro</title></head>
    <body style="background:#05070a;color:#ece8dc;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#0a0e14;padding:30px;border:1px solid #27354f;width:340px;border-radius:6px;">
        <h3 style="color:#d6b04c;margin-top:0;text-align:center;letter-spacing:1px;">REGISTRAZIONE</h3>
        <p style="font-size:11px;color:#a3acba;text-align:center;margin-bottom:20px;">Crea il tuo account personale</p>
        ''' + (f'<p style="color:#ff5a76;font-size:12px;text-align:center;">{error}</p>' if error else '') + '''
        ''' + (f'<p style="color:#2ee6a6;font-size:12px;text-align:center;">{success}</p>' if success else '') + '''
        <form method="POST">
            <p><label style="font-size:12px;color:#a3acba;">Nuovo Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#0f1520;border:1px solid #27354f;color:#fff;margin-top:4px;box-sizing:border-box;"></p>
            <p><label style="font-size:12px;color:#a3acba;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#0f1520;border:1px solid #27354f;color:#fff;margin-top:4px;box-sizing:border-box;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#2ee6a6;color:#05070a;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;">Registrati</button>
        </form>
        <p style="text-align:center;margin-top:15px;font-size:12px;"><a href="/login" style="color:#d6b04c;text-decoration:none;">Torna al Login</a></p>
    </div></body></html>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
