import time
import hmac
import hashlib
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# Configurazione endpoint BingX (VST o LIVE)
BINGX_VST_URL = "https://open-api-vst.bingx.com"
BINGX_LIVE_URL = "https://open-api.bingx.com"

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/prices", methods=["GET"])
def get_prices():
    try:
        # Prende i prezzi reali dei perpetual futures da BingX
        res = requests.get(f"{BINGX_LIVE_URL}/openApi/swap/v2/quote/ticker", timeout=5)
        data = res.json()
        if data.get("code") == 0:
            return jsonify(data["data"])
        return jsonify([])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/bingx-order", methods=["POST"])
def bingx_order():
    try:
        req_data = request.json
        env = req_data.get("env", "VST")
        api_key = req_data.get("apiKey")
        secret_key = req_data.get("secretKey")
        params = req_data.get("params", {})

        base_url = BINGX_LIVE_URL if env == "LIVE" else BINGX_VST_URL
        params["timestamp"] = str(int(time.time() * 1000))

        # Ordinamento dei parametri per la firma HMAC-SHA256
        query_string = "&".join([f"{key}={params[key]}" for key in sorted(params.keys())])
        
        # Firma HMAC-SHA256
        signature = hmac.new(
            secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        url = f"{base_url}/openApi/swap/v2/trade/order?{query_string}&signature={signature}"
        
        headers = {
            "X-BX-APIKEY": api_key,
            "Content-Type": "application/json"
        }

        response = requests.post(url, headers=headers, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)}), 500

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro • BingX Full Python Engine</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] },
          colors: { neonGreen: '#00FF66', neonRed: '#FF334B' }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .terminal-box { background: #08080A; border: 1px solid #1E1E24; }
  </style>
</head>
<body class="min-h-screen flex flex-col p-4 gap-4 max-w-[1920px] mx-auto w-full">
  <header class="flex flex-wrap items-center justify-between gap-3 bg-black/95 border border-[#1E1E24] p-4 rounded-2xl">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-xl bg-white text-black flex items-center justify-center font-extrabold">⚡</div>
      <div>
        <h1 class="text-base font-extrabold text-white">ApexScan <span class="text-neonGreen">PRO BINGX</span></h1>
        <p class="text-[11px] font-mono text-slate-400" id="liveClock">--:--:-- CET • Python Backend Attivo</p>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <button onclick="openBingxModal()" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white text-black">⚙️ API BingX</button>
    </div>
  </header>

  <section class="terminal-box rounded-2xl overflow-hidden p-4">
    <div class="flex justify-between items-center mb-3">
      <h2 class="text-sm font-bold text-white">Screener Perpetuals Reali (Python Bridge)</h2>
      <button onclick="fetchPrices()" class="px-3 py-1 rounded bg-white/10 text-xs font-mono">↻ Aggiorna Prezzi</button>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left text-xs font-mono">
        <thead>
          <tr class="border-b border-[#1E1E24] text-[10px] text-slate-400 uppercase">
            <th class="py-2">Contratto</th>
            <th class="py-2 text-right">Prezzo Spot</th>
            <th class="py-2 text-right">24h %</th>
            <th class="py-2 text-center">💎 Diamante</th>
            <th class="py-2 text-center">💲 Dollaro</th>
            <th class="py-2 text-right">Entry</th>
            <th class="py-2 text-right">SL</th>
            <th class="py-2 text-right">TP1</th>
            <th class="py-2 text-right">TP2</th>
            <th class="py-2 text-right">Azione</th>
          </tr>
        </thead>
        <tbody id="tableBody" class="divide-y divide-[#121216]"></tbody>
      </table>
    </div>
  </section>

  <!-- MODALE CONFIG -->
  <div id="modal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-4">
    <div class="terminal-box rounded-2xl w-full max-w-md p-5 space-y-3 text-xs">
      <h2 class="text-sm font-bold text-white">Configurazione API BingX</h2>
      <select id="cfgEnv" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono">
        <option value="VST">Demo VST</option>
        <option value="LIVE">Live Reale</option>
      </select>
      <input type="password" id="cfgApiKey" placeholder="API Key" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono"/>
      <input type="password" id="cfgSecretKey" placeholder="Secret Key" class="w-full bg-black border border-[#1E1E24] rounded p-2 text-white font-mono"/>
      <div class="flex justify-end gap-2 pt-2">
        <button onclick="closeBingxModal()" class="px-3 py-1.5 bg-[#121216] text-slate-300 rounded">Annulla</button>
        <button onclick="saveConfig()" class="px-3 py-1.5 bg-white text-black font-bold rounded">Salva</button>
      </div>
    </div>
  </div>

  <script>
    const ASSETS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT', 'ADA-USDT', 'AVAX-USDT', 'LINK-USDT'];
    let marketData = {};
    let config = { env: 'VST', apiKey: '', secretKey: '' };

    async function fetchPrices() {
      try {
        const res = await fetch('/api/prices');
        const data = await res.json();
        if (Array.isArray(data)) {
          marketData = {};
          data.forEach(item => { marketData[item.symbol] = item; });
          render();
        }
      } catch (e) { console.error(e); }
    }

    function render() {
      const tbody = document.getElementById('tableBody');
      tbody.innerHTML = ASSETS.map(sym => {
        const item = marketData[sym] || { lastPrice: '0', priceChangePercent: '0' };
        const price = parseFloat(item.lastPrice) || 100;
        const change = parseFloat(item.priceChangePercent) || 0;
        const isPos = change >= 0;
        const diamond = Math.min(99, Math.max(50, Math.round(65 + Math.abs(change) * 4)));
        const dollar = Number(Math.min(98, Math.max(50, 60 + Math.abs(change) * 3.5)).toFixed(1));
        const entraOra = diamond >= 80;

        const atr = price * 0.015;
        const sl = isPos ? price - atr : price + atr;
        const tp1 = isPos ? price + atr * 1.8 : price - atr * 1.8;
        const tp2 = isPos ? price + atr * 3.2 : price - atr * 3.2;

        return `
          <tr class="hover:bg-[#121216]">
            <td class="py-2.5 font-bold text-white">${sym}</td>
            <td class="py-2.5 text-right font-bold">$${price.toFixed(2)}</td>
            <td class="py-2.5 text-right ${isPos ? 'text-neonGreen' : 'text-neonRed'}">${isPos?'+':''}${change.toFixed(2)}%</td>
            <td class="py-2.5 text-center">${entraOra ? '<span class="text-neonGreen font-bold">💎 ENTRA ORA ('+diamond+'%)</span>' : '💎 '+diamond+'%'}</td>
            <td class="py-2.5 text-center text-white">💲 ${dollar}%</td>
            <td class="py-2.5 text-right text-white">$${price.toFixed(2)}</td>
            <td class="py-2.5 text-right text-neonRed">$${sl.toFixed(2)}</td>
            <td class="py-2.5 text-right text-neonGreen">$${tp1.toFixed(2)}</td>
            <td class="py-2.5 text-right text-neonGreen">$${tp2.toFixed(2)}</td>
            <td class="py-2.5 text-right">
              <button onclick="executeOrder('${sym}', ${price}, ${isPos})" class="px-2 py-1 bg-white text-black font-bold text-[10px] rounded hover:bg-neonGreen">⚡ Esegui</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    async function executeOrder(symbol, price, isLong) {
      if (!config.apiKey || !config.secretKey) {
        alert('Inserisci prima le chiavi API BingX!');
        openBingxModal();
        return;
      }
      const side = isLong ? 'BUY' : 'SELL';
      const posSide = isLong ? 'LONG' : 'SHORT';
      const atr = price * 0.015;
      const sl = isLong ? price - atr : price + atr;
      const tp1 = isLong ? price + atr * 1.8 : price - atr * 1.8;

      const params = {
        symbol: symbol,
        side: side,
        positionSide: posSide,
        type: 'MARKET',
        quantity: (100 / price).toFixed(3),
        stopLoss: JSON.stringify({ type: 'STOP_MARKET', stopPrice: Number(sl.toFixed(2)), workingType: 'MARK_PRICE' }),
        takeProfit: JSON.stringify({ type: 'TAKE_PROFIT_MARKET', stopPrice: Number(tp1.toFixed(2)), workingType: 'MARK_PRICE' })
      };

      try {
        const res = await fetch('/api/bingx-order', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ env: config.env, apiKey: config.apiKey, secretKey: config.secretKey, params })
        });
        const data = await res.json();
        if (data.code === 0) alert('✅ Ordine inviato con successo su BingX!');
        else alert('❌ Errore BingX: ' + data.msg);
      } catch (e) { alert('Errore di rete: ' + e.message); }
    }

    function openBingxModal() { document.getElementById('modal').classList.remove('hidden'); document.getElementById('modal').classList.add('flex'); }
    function closeBingxModal() { document.getElementById('modal').classList.add('hidden'); document.getElementById('modal').classList.remove('flex'); }
    function saveConfig() {
      config.env = document.getElementById('cfgEnv').value;
      config.apiKey = document.getElementById('cfgApiKey').value.trim();
      config.secretKey = document.getElementById('cfgSecretKey').value.trim();
      closeBingxModal();
      alert('Configurazione salvata!');
    }

    setInterval(() => {
      document.getElementById('liveClock').textContent = new Date().toLocaleTimeString('it-IT') + ' CET';
    }, 1000);

    fetchPrices();
    setInterval(fetchPrices, 5000);
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
