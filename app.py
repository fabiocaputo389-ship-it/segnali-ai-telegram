import time
import hmac
import hashlib
import threading
import requests
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

BINGX_LIVE_URL = "https://open-api.bingx.com"
BINGX_VST_URL = "https://open-api-vst.bingx.com"

scanner_state = {
    "assets": [],
    "last_scan": "In attesa...",
    "total": 0,
    "config": {"env": "VST", "api_key": "", "secret_key": "", "margin": 100, "leverage": 10, "auto_bot": False},
    "logs": []
}

def add_log(msg, level="INFO"):
    t = datetime.now().strftime("%H:%M:%S")
    scanner_state["logs"].insert(0, {"time": t, "msg": msg, "level": level})
    scanner_state["logs"] = scanner_state["logs"][:40]

def background_bingx_fetcher():
    while True:
        try:
            res = requests.get(f"{BINGX_LIVE_URL}/openApi/swap/v2/quote/ticker", timeout=8).json()
            if res.get("code") == 0 and isinstance(res.get("data"), list):
                tickers = res["data"]
                scanner_state["total"] = len(tickers)
                
                valid = []
                for t in tickers:
                    sym = t.get("symbol", "")
                    if not sym.endswith("-USDT"):
                        continue
                    price = float(t.get("lastPrice", 0))
                    change = float(t.get("priceChangePercent", 0))
                    vol = float(t.get("quoteVolume", 0))
                    if price <= 0:
                        continue

                    cat = "L1"
                    if any(x in sym for x in ["AAVE", "LINK", "UNI", "PENDLE"]): cat = "DEFI"
                    elif any(x in sym for x in ["DOGE", "PEPE", "SHIB", "BONK", "WIF"]): cat = "MEME"

                    abs_chg = abs(change)
                    diamond = min(99, max(45, int(60 + abs_chg * 3.5)))
                    dollar = round(min(98.5, max(50.0, 60.0 + abs_chg * 3.2)), 1)
                    entra_ora = diamond >= 82 and dollar >= 80.0

                    decimals = 2 if price >= 10 else (4 if price >= 0.1 else 6)

                    valid.append({
                        "symbol": sym.replace("-", "/"),
                        "raw_symbol": sym,
                        "name": sym.split("-")[0],
                        "category": cat,
                        "price": price,
                        "decimals": decimals,
                        "change24h": change,
                        "diamondScore": diamond,
                        "dollarConfluence": dollar,
                        "entraOra": entra_ora
                    })

                valid.sort(key=lambda x: (x["entraOra"], x["diamondScore"]), reverse=True)
                scanner_state["assets"] = valid[:50]
                scanner_state["last_scan"] = datetime.now().strftime("%H:%M:%S CET")
        except Exception as e:
            add_log(f"Errore fetch BingX: {str(e)}", "ERROR")
        time.sleep(5)

threading.Thread(target=background_bingx_fetcher, daemon=True).start()

@app.route("/")
def index():
    return render_template_string(EXACT_HTML_TEMPLATE)

@app.route("/api/data")
def api_data():
    return jsonify(scanner_state)

@app.route("/api/trade", methods=["POST"])
def api_trade():
    data = request.json
    cfg = scanner_state["config"]
    if not cfg["api_key"] or not cfg["secret_key"]:
        return jsonify({"code": -1, "msg": "Chiavi API BingX mancanti!"})
    
    try:
        base = BINGX_LIVE_URL if cfg["env"] == "LIVE" else BINGX_VST_URL
        sym = data.get("symbol")
        price = float(data.get("price"))
        is_long = data.get("isLong")
        
        side = "BUY" if is_long else "SELL"
        pos = "LONG" if is_long else "SHORT"
        notional = float(cfg["margin"]) * int(cfg["leverage"])
        qty = round(notional / price, 3 if price > 50 else 1)
        
        atr = price * 0.015
        sl = round(price - atr if is_long else price + atr, 2)
        tp = round(price + atr * 1.8 if is_long else price - atr * 1.8, 2)

        params = {
            "symbol": sym.replace("/", "-"),
            "side": side,
            "positionSide": pos,
            "type": "MARKET",
            "quantity": str(qty),
            "stopLoss": f'{{"type":"STOP_MARKET","stopPrice":{sl},"workingType":"MARK_PRICE"}}',
            "takeProfit": f'{{"type":"TAKE_PROFIT_MARKET","stopPrice":{tp},"workingType":"MARK_PRICE"}}',
            "timestamp": str(int(time.time() * 1000))
        }

        q_str = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
        sig = hmac.new(cfg["secret_key"].encode("utf-8"), q_str.encode("utf-8"), hashlib.sha256).hexdigest()
        
        url = f"{base}/openApi/swap/v2/trade/order?{q_str}&signature={sig}"
        res = requests.post(url, headers={"X-BX-APIKEY": cfg["api_key"], "Content-Type": "application/json"}, timeout=10).json()
        
        if res.get("code") == 0:
            add_log(f"Ordine eseguito su BingX per {sym} ({pos})", "SUCCESS")
        else:
            add_log(f"Rifiuto BingX {sym}: {res.get('msg')}", "ERROR")
        return jsonify(res)
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})

@app.route("/api/config", methods=["POST"])
def api_config():
    d = request.json
    cfg = scanner_state["config"]
    if "env" in d: cfg["env"] = d["env"]
    if d.get("apiKey"): cfg["api_key"] = d["apiKey"].strip()
    if d.get("secretKey"): cfg["secret_key"] = d["secretKey"].strip()
    if "margin" in d: cfg["margin"] = float(d["margin"])
    if "leverage" in d: cfg["leverage"] = int(d["leverage"])
    if "autoBot" in d: cfg["auto_bot"] = bool(d["autoBot"])
    add_log("Configurazione API BingX aggiornata.", "INFO")
    return jsonify({"status": "ok"})

EXACT_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro • Diamond &amp; Dollar Full-View Edition</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] },
          colors: {
            obsidian: { 950: '#000000', 900: '#08080A', 800: '#121216' },
            neonGreen: '#00FF66',
            neonRed: '#FF334B'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #000000; }
    ::-webkit-scrollbar-thumb { background: #26262C; border-radius: 9999px; }
    @keyframes pulseGlow { 0%, 100% { box-shadow: 0 0 12px rgba(0, 255, 102, 0.3); } 50% { box-shadow: 0 0 24px rgba(0, 255, 102, 0.7); } }
    .live-glow { animation: pulseGlow 2s infinite ease-in-out; }
    .terminal-box { background: #08080A; border: 1px solid #1E1E24; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-white selection:text-black">

  <!-- HEADER -->
  <header class="sticky top-0 z-40 bg-black/95 backdrop-blur-md border-b border-[#1E1E24] px-4 py-3">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-white text-black flex items-center justify-center font-extrabold text-base">⚡</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base font-extrabold tracking-tight text-white">ApexScan <span class="text-neonGreen">PRO</span></h1>
            <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neonGreen/10 text-neonGreen border border-neonGreen/30 live-glow">
              <span class="w-1.5 h-1.5 rounded-full bg-neonGreen animate-ping"></span> BINGX LIVE REAL
            </span>
          </div>
          <p class="text-[11px] font-mono text-slate-400 mt-0.5" id="liveClock">--:--:-- CET • BingX Perpetual Live Feed</p>
        </div>
      </div>

      <!-- FILTRI DI CATEGORIA -->
      <div class="flex items-center bg-[#08080A] p-1 rounded-xl border border-[#1E1E24] overflow-x-auto text-xs" id="marketFilter">
        <button data-cat="ALL" class="cat-btn px-3 py-1.5 rounded-lg font-bold bg-white text-black transition-all">Tutto</button>
        <button data-cat="L1" class="cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all">Layer 1</button>
        <button data-cat="DEFI" class="cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all">DeFi</button>
        <button data-cat="MEME" class="cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all">Meme &amp; Alts</button>
      </div>

      <div class="flex items-center gap-2">
        <input type="text" id="searchInput" placeholder="Cerca ticker (es. BTC, SOL)..." class="bg-[#08080A] border border-[#1E1E24] focus:border-white rounded-xl px-3 py-1.5 text-xs text-white outline-none w-44"/>
        <button onclick="openConfigModal()" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white text-black hover:bg-slate-200">⚙️ API BingX</button>
      </div>
    </div>
  </header>

  <!-- MAIN CONTAINER -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-4 py-4 flex flex-col gap-4">

    <!-- KPI GLOBALI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Contratti BingX Live</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTotal" class="text-2xl font-extrabold font-mono text-white">0</span>
          <span class="text-xs font-mono text-neonGreen font-semibold">Reali 100%</span>
        </div>
      </div>
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Segnali 💎 ENTRA ORA</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiEnterNow" class="text-2xl font-extrabold font-mono text-neonGreen">0</span>
          <span class="text-xs font-mono text-slate-300">Confluenza alta</span>
        </div>
      </div>
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Stato API BingX</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiApiStatus" class="text-lg font-extrabold font-mono text-white">Pronto</span>
          <span id="kpiEnvTag" class="text-xs font-mono text-slate-400">VST</span>
        </div>
      </div>
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Ultimo Scan Server</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="lastScanTime" class="text-lg font-extrabold font-mono text-white">--:--:--</span>
          <span class="text-xs font-mono text-neonGreen">Attivo</span>
        </div>
      </div>
    </section>

    <!-- GRIGLIA PRINCIPALE: SCREENER CRYPTO ESTESO CON PARAMETRI IN CHIARO -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-12 terminal-box rounded-2xl overflow-hidden flex flex-col">
        <div class="px-4 py-3.5 border-b border-[#1E1E24] flex items-center justify-between bg-[#08080A]">
          <div class="flex items-center gap-2">
            <h2 class="text-sm font-bold text-white">Screener Completo • Tutti i Parametri Operativi in Chiaro &amp; Esecuzione BingX</h2>
            <span id="cryptoCountBadge" class="px-2 py-0.5 rounded text-xs font-mono bg-white/10 text-white">0 asset</span>
          </div>
          <span class="text-[11px] font-mono text-slate-400">Diamante 💎 &amp; Dollaro 💲 integrati</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-[#1E1E24] text-[10px] font-bold uppercase tracking-wider text-slate-400 bg-black">
                <th class="py-3 pl-4">Asset</th>
                <th class="py-3 px-3">Cat.</th>
                <th class="py-3 px-3 text-right">Prezzo Reale</th>
                <th class="py-3 px-3 text-right">24h %</th>
                <th class="py-3 px-3 text-center">💎 Diamante</th>
                <th class="py-3 px-3 text-center">💲 Dollaro</th>
                <th class="py-3 px-3 text-right">Optimal Entry</th>
                <th class="py-3 px-3 text-right">Stop Loss (SL)</th>
                <th class="py-3 px-3 text-right">Take Profit 1 (TP1)</th>
                <th class="py-3 px-3 text-right">Take Profit 2 (TP2)</th>
                <th class="py-3 px-3 text-right pr-4">Azione BingX</th>
              </tr>
            </thead>
            <tbody id="cryptoTableBody" class="divide-y divide-[#121216] text-xs font-mono"></tbody>
          </table>
        </div>
      </section>
    </div>

    <section class="terminal-box rounded-2xl overflow-hidden p-4">
      <h3 class="text-xs font-bold uppercase text-white mb-2">Log Attività BingX</h3>
      <div id="logsBox" class="bg-black p-3 rounded-xl font-mono text-[11px] space-y-1 max-h-36 overflow-y-auto text-slate-400"></div>
    </section>
  </main>

  <!-- MODALE CONFIG -->
  <div id="configModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-4">
    <div class="terminal-box rounded-2xl w-full max-w-md p-5 space-y-3 text-xs">
      <h2 class="text-sm font-bold text-white">Configurazione API BingX</h2>
      <select id="cfgEnv" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono">
        <option value="VST">Demo VST</option>
        <option value="LIVE">Live Reale</option>
      </select>
      <input type="password" id="cfgApiKey" placeholder="API Key" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono"/>
      <input type="password" id="cfgSecretKey" placeholder="Secret Key" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono"/>
      <div class="grid grid-cols-2 gap-2">
        <input type="number" id="cfgMargin" value="100" placeholder="Margine USDT" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono"/>
        <input type="number" id="cfgLev" value="10" placeholder="Leva" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono"/>
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button onclick="closeConfigModal()" class="px-3 py-1.5 bg-[#121216] text-slate-300 rounded">Annulla</button>
        <button onclick="saveConfig()" class="px-3 py-1.5 bg-white text-black font-bold rounded">Salva</button>
      </div>
    </div>
  </div>

  <!-- SCRIPT ENGINE -->
  <script>
    let globalData = { assets: [], logs: [], config: {} };
    let currentCategory = 'ALL';

    function formatPrice(val, decimals) {
      return '$' + val.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }

    async function fetchData() {
      try {
        const res = await fetch('/api/data');
        globalData = await res.json();

        document.getElementById('kpiTotal').textContent = globalData.total || 0;
        document.getElementById('kpiEnterNow').textContent = globalData.assets.filter(a => a.entraOra).length;
        document.getElementById('lastScanTime').textContent = globalData.last_scan;
        
        const cfg = globalData.config;
        document.getElementById('kpiEnvTag').textContent = cfg.env;
        document.getElementById('kpiApiStatus').textContent = cfg.api_key ? 'Connesso' : 'Pronto';

        renderTable();
        renderLogs();
      } catch (e) { console.error(e); }
    }

    function renderTable() {
      const tbody = document.getElementById('cryptoTableBody');
      const searchVal = document.getElementById('searchInput').value.toLowerCase();
      
      const filtered = globalData.assets.filter(c => {
        if (currentCategory !== 'ALL' && c.category !== currentCategory) return false;
        if (searchVal && !c.symbol.toLowerCase().includes(searchVal) && !c.name.toLowerCase().includes(searchVal)) return false;
        return true;
      });

      document.getElementById('cryptoCountBadge').textContent = `${filtered.length} asset`;

      if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-6 text-slate-500">Sincronizzazione contratti BingX in corso...</td></tr>`;
        return;
      }

      tbody.innerHTML = filtered.map(c => {
        const isPos = c.change24h >= 0;
        const atr = c.price * 0.015;
        const entry = c.price;
        const sl = isPos ? entry - atr : entry + atr;
        const tp1 = isPos ? entry + (atr * 1.8) : entry - (atr * 1.8);
        const tp2 = isPos ? entry + (atr * 3.2) : entry - (atr * 3.2);

        const diamondBadge = c.entraOra 
          ? `<span class="px-2 py-0.5 rounded bg-neonGreen/15 text-neonGreen font-bold border border-neonGreen/30">💎 ENTRA ORA (${c.diamondScore}%)</span>`
          : `<span class="text-slate-400">💎 ${c.diamondScore}%</span>`;

        return `
          <tr class="hover:bg-[#121216] transition-colors">
            <td class="py-3 pl-4 font-bold text-white">
              ${c.symbol}
              <span class="text-[10px] block text-slate-400 font-normal">${c.name}</span>
            </td>
            <td class="py-3 px-3">
              <span class="px-2 py-0.5 rounded text-[10px] bg-white/5 text-slate-300 border border-[#1E1E24]">${c.category}</span>
            </td>
            <td class="py-3 px-3 text-right font-bold text-white tabular-nums">${formatPrice(c.price, c.decimals)}</td>
            <td class="py-3 px-3 text-right tabular-nums ${isPos ? 'text-neonGreen' : 'text-neonRed'}">
              ${isPos ? '+' : ''}${c.change24h.toFixed(2)}%
            </td>
            <td class="py-3 px-3 text-center">
              ${diamondBadge}
            </td>
            <td class="py-3 px-3 text-center">
              <span class="text-white font-extrabold">💲 ${c.dollarConfluence}%</span>
            </td>
            <td class="py-3 px-3 text-right text-neonGreen font-bold tabular-nums">
              ${formatPrice(entry, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right text-neonRed font-bold tabular-nums">
              ${formatPrice(sl, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right text-neonGreen font-bold tabular-nums">
              ${formatPrice(tp1, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right text-neonGreen font-bold tabular-nums">
              ${formatPrice(tp2, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right pr-4">
              <button onclick="executeTrade('${c.symbol}', ${c.price}, ${isPos})" class="px-2.5 py-1 bg-white text-black hover:bg-neonGreen font-bold text-[10px] rounded transition-all">⚡ Esegui</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderLogs() {
      const box = document.getElementById('logsBox');
      box.innerHTML = globalData.logs.map(l => `<div>[${l.time}] ${l.msg}</div>`).join('');
    }

    async function executeTrade(symbol, price, isLong) {
      const res = await (await fetch('/api/trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, price, isLong })
      })).json();
      alert(res.code === 0 ? '✅ Ordine inviato con successo su BingX!' : '❌ Errore: ' + res.msg);
    }

    function openConfigModal() { document.getElementById('configModal').classList.remove('hidden'); document.getElementById('configModal').classList.add('flex'); }
    function closeConfigModal() { document.getElementById('configModal').classList.add('hidden'); document.getElementById('configModal').classList.remove('flex'); }

    async function saveConfig() {
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          env: document.getElementById('cfgEnv').value,
          apiKey: document.getElementById('cfgApiKey').value,
          secretKey: document.getElementById('cfgSecretKey').value,
          margin: document.getElementById('cfgMargin').value,
          leverage: document.getElementById('cfgLev').value
        })
      });
      closeConfigModal();
      alert('Configurazione API salvata!');
      fetchData();
    }

    window.addEventListener('DOMContentLoaded', () => {
      fetchData();
      setInterval(fetchData, 4000);

      setInterval(() => {
        document.getElementById('liveClock').textContent = new Date().toLocaleTimeString('it-IT') + ' CET • BingX Perpetual Live';
      }, 1000);

      document.querySelectorAll('.cat-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          document.querySelectorAll('.cat-btn').forEach(b => b.className = 'cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all');
          e.target.className = 'cat-btn px-3 py-1.5 rounded-lg font-bold bg-white text-black transition-all';
          currentCategory = e.target.getAttribute('data-cat');
          renderTable();
        });
      });

      document.getElementById('searchInput').addEventListener('input', renderTable);
    });
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
