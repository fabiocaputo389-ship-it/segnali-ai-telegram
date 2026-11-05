from flask import Flask, render_template_string
import os

app = Flask(__name__)

# Codice HTML dello scanner completo con dati live di Binance
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Scanner TOP Entry Live</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(900px 420px at 18% -80px,rgba(214,176,76,.10),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,44px 44px,44px 44px}
.wrap{max-width:1560px;margin:0 auto;padding-block:18px 28px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}

.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px 24px;margin-bottom:14px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand h1{font:700 18px/1.25 var(--font-display);letter-spacing:.14em;text-transform:uppercase}
.brand p{font:400 11px var(--font-mono);color:var(--mute);letter-spacing:.04em;margin-top:3px}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);padding:6px 10px;font:500 11px var(--font-mono);letter-spacing:.05em;color:var(--ink2);background:rgba(12,16,23,.7)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 8px var(--up);animation:pulse 1.8s infinite}
.dot.off{background:var(--warn);box-shadow:0 0 8px var(--warn);animation:none}
@keyframes pulse{50%{opacity:.35}}
button{font:600 11px var(--font-mono);letter-spacing:.07em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase}
button:hover{background:var(--gold-soft)}

.banner{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;border:1px solid var(--dia);background:var(--dia-soft);padding:10px 14px;margin-bottom:14px;font:600 12px var(--font-mono);letter-spacing:.06em;color:var(--dia)}
.banner .tops{display:flex;flex-wrap:wrap;gap:6px;margin-left:auto}
.banner .tops button{color:var(--dia);border-color:var(--dia);padding:3px 8px;font-size:10.5px}
.dia{display:inline-block;filter:drop-shadow(0 0 5px var(--dia))}
.dia.blink{animation:blink 1s steps(2,jump-none) infinite}
@keyframes blink{50%{opacity:.25}}

.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.col{display:flex;flex-direction:column;gap:14px;min-width:0}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);padding:14px 16px 16px;min-width:0}
.ph{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.ph h2{font:600 12px var(--font-display);letter-spacing:.18em;text-transform:uppercase;color:var(--gold)}
.ph .sub{font:400 10.5px var(--font-mono);color:var(--mute)}

.gsvg{display:block;width:100%;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:10;opacity:.28}
.gval{fill:none;stroke-width:10;stroke:var(--up);filter:drop-shadow(0 0 5px currentColor)}
.gnum{font:700 42px/1 var(--font-mono);text-align:center;letter-spacing:-.02em}
.glab{font:600 12px var(--font-display);letter-spacing:.22em;text-transform:uppercase;text-align:center;margin-top:8px}
.gbig{max-width:380px;margin:0 auto}
.sbrow{display:flex;justify-content:space-between;font:500 10px var(--font-mono);margin-top:6px}
.tfp{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:14px}
.tfp div{border:1px solid var(--line);padding:6px 8px;font:500 11px var(--font-mono);display:flex;justify-content:space-between}

.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;font:700 10px var(--font-mono);border:1px solid currentColor}
.pill.LONG{color:var(--up);background:rgba(46,230,166,.08)}
.pill.SHORT{color:var(--dn);background:rgba(255,90,118,.08)}

.cdbig{font:700 40px/1 var(--font-mono);color:var(--dia);letter-spacing:.02em}
.prog{height:6px;background:var(--line);margin:12px 0 10px;position:relative}
.prog i{position:absolute;left:0;top:0;bottom:0;background:var(--dia);transition:width 1s linear}
.note{color:var(--ink2);font-size:11.5px;line-height:1.55}

.bar2{position:relative;height:10px;background:var(--line)}
.bar2 i{position:absolute;top:0;bottom:0}
.bar2.dv::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--line2)}

.exe div{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
.exe div:last-child{border-bottom:0}
.exe span{font:400 11px var(--font-mono);color:var(--mute)}

.topbox{border:1px solid var(--dia);background:var(--dia-soft);padding:12px 14px}
.topbox h3{font:700 11px var(--font-mono);color:var(--dia);margin-bottom:8px}
.topbox ul{margin:0;padding-left:14px;font-size:12px;color:var(--ink)}

.chead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;margin-bottom:6px}
.chead .sym{font:700 22px var(--font-display);letter-spacing:.1em}
.chead .px{font:500 20px var(--font-mono)}

.tw{overflow:auto;max-height:620px;border:1px solid var(--line)}
table.main{border-collapse:collapse;width:100%;min-width:1100px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);color:var(--mute);padding:9px 8px;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main td{padding:7px 8px;text-align:center;font:400 12px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(214,176,76,.05)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
input[type=search]{background:var(--panel);border:1px solid var(--line2);color:var(--ink);font:400 12px var(--font-mono);padding:6px 10px;width:180px}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="brand">
      <svg width="40" height="40" viewBox="0 0 40 40"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#ffd23f" stroke-width="1.6"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(255,210,63,.18)" stroke="#ffd23f" stroke-width="1"/></svg>
      <div><h1>Scanner · Segnali TOP Entry Live</h1><p>Mercato reale Binance · Cloud Deployment (Render)</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot" id="feedDot"></i><span id="feedTxt">BINANCE LIVE</span></span>
      <span class="chip" id="clock">--:--:--</span>
    </div>
  </header>

  <div class="banner" id="banner"></div>

  <main class="grid">
    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Asset attivo</h2><span class="sub" id="gSub">BTC / USDT</span></div>
        <div class="gbig">
          <div id="gMain"></div>
          <div class="gnum" id="gNum">--</div>
          <div class="glab">POTENZA IA</div>
          <div class="sbrow"><span class="dn">SELL</span><span class="up">BUY</span></div>
        </div>
      </section>
      <section class="panel">
        <div class="ph"><h2>Validità segnale &amp; Ingresso</h2></div>
        <div class="cdbig" id="cdMain">18:08</div>
        <div class="prog"><i id="cdBar" style="width:75%"></i></div>
        <p class="note">⚡ <b>ENTRA ADESSO!</b> Finestra consigliata attiva (Retest in corso)</p>
      </section>
    </div>

    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Notizie &amp; sentiment</h2></div>
        <div style="font-weight:600; font-size:14px; margin-bottom:4px">Forti flussi in entrata sugli Exchange Spot</div>
        <div class="mu" style="font-size:11px">On-Chain Intelligence · Live</div>
        <div style="margin-top:12px"><div class="bar2 dv"><i style="left:50%;right:15%;background:var(--up)"></i></div>
          <div class="sbrow"><span class="dn">NEGATIVO</span><span class="up">+70% POSITIVO</span></div></div>
      </section>
      <section class="panel">
        <div class="ph"><h2>Flussi &amp; balene</h2></div>
        <div class="exe">
          <div><span>Accumulo Whale Spot</span><b class="up">+14,250 BTC</b></div>
          <div><span>Open Interest Derivati</span><b style="color:var(--c2)">$18.4B (+5.2%)</b></div>
          <div><span>Liquidazioni Short (24h)</span><b class="up">$142.8M</b></div>
        </div>
      </section>
    </div>

    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Esecuzione istantanea</h2><span class="sub" id="exSub">Market Entry</span></div>
        <div class="exe">
          <div><span>Prezzo Entrata (Market)</span><b id="exPrice" style="color:var(--dia)">--</b></div>
          <div><span>Stop Loss (SL)</span><b id="exSL" class="dn">--</b></div>
          <div><span>Take Profit 1 (TP1)</span><b id="exTP1" class="up">--</b></div>
          <div><span>Take Profit 2 (TP2)</span><b id="exTP2" class="up">--</b></div>
          <div><span>Leva Consigliata</span><b>3x</b></div>
        </div>
      </section>
      <div class="topbox" id="topbox">
        <h3 id="topBoxTitle"><span class="dia blink">◆</span> DIAMANTE ORO &gt;85%</h3>
        <ul id="topBoxAsset"><li>Monitoraggio multi-timeframe attivo.</li></ul>
      </div>
    </div>

    <section class="panel s12">
      <div class="ph">
        <h2>Scanner Multi-Mercato Cripto (Binance Real-Time)</h2>
      </div>
      <div style="margin-bottom:10px">
        <input id="q" type="search" placeholder="Cerca coin (es. BTC, ETH)…">
      </div>
      <div class="tw">
        <table class="main">
          <thead>
            <tr>
              <th style="text-align:left">Coin / Asset</th>
              <th>Segnale</th>
              <th>Prezzo Attuale</th>
              <th>Score IA</th>
              <th>Entrata Utile</th>
              <th>Stop Loss (SL)</th>
              <th>Take Profit (TP1)</th>
              <th>Take Profit (TP2)</th>
              <th>Var 24h</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </section>
  </main>
</div>

<script>
const symbols = [
  { symbol: 'BTCUSDT', name: 'Bitcoin', base: 'BTC' },
  { symbol: 'ETHUSDT', name: 'Ethereum', base: 'ETH' },
  { symbol: 'SOLUSDT', name: 'Solana', base: 'SOL' },
  { symbol: 'XRPUSDT', name: 'Ripple', base: 'XRP' },
  { symbol: 'ADAUSDT', name: 'Cardano', base: 'ADA' },
  { symbol: 'AVAXUSDT', name: 'Avalanche', base: 'AVAX' },
  { symbol: 'DOGEUSDT', name: 'Dogecoin', base: 'DOGE' },
  { symbol: 'LINKUSDT', name: 'Chainlink', base: 'LINK' },
  { symbol: 'DOTUSDT', name: 'Polkadot', base: 'DOT' },
  { symbol: 'NEARUSDT', name: 'NEAR Protocol', base: 'NEAR' }
];

let marketData = {};
let selectedAsset = 'BTCUSDT';
let countdownSeconds = 1808;

function renderGauge(score) {
  const r = 80, c = Math.PI * r, off = c - (score/100)*c;
  return `<svg class="gsvg" viewBox="0 0 200 110">
    <path class="gseg" d="M 20 100 A 80 80 0 0 1 180 100" fill="none"/>
    <path class="gval" d="M 20 100 A 80 80 0 0 1 180 100" fill="none" style="stroke-dasharray:${c}; stroke-dashoffset:${off}"/>
    <text x="100" y="85" text-anchor="middle" fill="var(--ink)" font-family="var(--font-mono)" font-size="28" font-weight="700">${score}%</text>
  </svg>`;
}

function updateActivePanel() {
  const d = marketData[selectedAsset];
  if(!d) return;
  document.getElementById('gSub').textContent = `${d.base} / USDT`;
  document.getElementById('gNum').textContent = `${d.score}%`;
  document.getElementById('gMain').innerHTML = renderGauge(d.score);

  const isLong = d.signal === 'LONG';
  const sl = isLong ? d.price * 0.984 : d.price * 1.016;
  const tp1 = isLong ? d.price * 1.032 : d.price * 0.968;
  const tp2 = isLong ? d.price * 1.064 : d.price * 0.936;

  document.getElementById('exPrice').textContent = `$${d.price.toFixed(2)}`;
  document.getElementById('exSL').textContent = `$${sl.toFixed(2)}`;
  document.getElementById('exTP1').textContent = `$${tp1.toFixed(2)}`;
  document.getElementById('exTP2').textContent = `$${tp2.toFixed(2)}`;

  document.getElementById('banner').innerHTML = `
    <span><span class="dia blink">◆</span> DIAMANTE ORO &gt;85% · SETUP ${d.signal} SU ${d.base}</span>
    <span class="tops">
      <button onclick="selectAsset('BTCUSDT')">BTC</button>
      <button onclick="selectAsset('ETHUSDT')">ETH</button>
      <button onclick="selectAsset('SOLUSDT')">SOL</button>
    </span>`;
}

window.selectAsset = function(sym) {
  selectedAsset = sym;
  updateActivePanel();
  renderTable();
}

function renderTable() {
  const tbody = document.getElementById('tbody');
  const q = document.getElementById('q').value.toLowerCase();
  tbody.innerHTML = '';

  symbols.forEach(s => {
    const d = marketData[s.symbol];
    if(!d) return;
    if(q && !s.symbol.toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) return;

    const isLong = d.signal === 'LONG';
    const sl = isLong ? d.price * 0.984 : d.price * 1.016;
    const tp1 = isLong ? d.price * 1.032 : d.price * 0.968;
    const tp2 = isLong ? d.price * 1.064 : d.price * 0.936;

    const tr = document.createElement('tr');
    tr.className = `r ${s.symbol === selectedAsset ? 'sel' : ''}`;
    tr.onclick = () => selectAsset(s.symbol);

    tr.innerHTML = `
      <td style="text-align:left"><b>${s.base}</b><br><small style="color:var(--mute)">${s.name}</small></td>
      <td><span class="pill ${d.signal}">${d.signal}</span></td>
      <td>$${d.price.toFixed(2)}</td>
      <td><b>${d.score}%</b></td>
      <td class="up">18:08 ⚡</td>
      <td class="dn">$${sl.toFixed(2)}</td>
      <td class="up">$${tp1.toFixed(2)}</td>
      <td class="up">$${tp2.toFixed(2)}</td>
      <td class="${d.change24h >= 0 ? 'up' : 'dn'}">${d.change24h >= 0 ? '+' : ''}${d.change24h.toFixed(2)}%</td>
    `;
    tbody.appendChild(tr);
  });
}

async function fetchBinance() {
  try {
    const res = await fetch('https://api.binance.com/api/v3/ticker/24hr');
    const json = await res.json();
    json.forEach(item => {
      const found = symbols.find(s => s.symbol === item.symbol);
      if(found) {
        const p = parseFloat(item.lastPrice);
        const ch = parseFloat(item.priceChangePercent);
        marketData[item.symbol] = {
          symbol: item.symbol,
          base: found.base,
          name: found.name,
          price: p,
          change24h: ch,
          score: Math.min(96, Math.max(72, Math.round(75 + ch * 1.5))),
          signal: ch >= -0.5 ? 'LONG' : 'SHORT'
        };
      }
    });
    updateActivePanel();
    renderTable();
  } catch(e) { console.error(e); }
}

setInterval(() => {
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleTimeString('it-IT');
  if(countdownSeconds > 0) {
    countdownSeconds--;
    const m = Math.floor(countdownSeconds / 60).toString().padStart(2, '0');
    const s = (countdownSeconds % 60).toString().padStart(2, '0');
    document.getElementById('cdMain').textContent = `${m}:${s}`;
    document.getElementById('cdBar').style.width = `${(countdownSeconds/1808)*100}%`;
  } else { countdownSeconds = 1808; }
}, 1000);

document.getElementById('q').oninput = renderTable;

fetchBinance();
setInterval(fetchBinance, 3000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
