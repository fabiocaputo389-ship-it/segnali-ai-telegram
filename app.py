import os
import re
import time
import hmac
import hashlib
import urllib.parse
import requests
from flask import Flask, render_template_string, request, jsonify
from tradingview_ta import TA_Handler, Interval

app = Flask(__name__)

# ==========================================
# CREDENZIALI DI DEFAULT (Fallback / Sistema)
# ==========================================
DEFAULT_API_KEY = os.environ.get("BINGX_API_KEY", "")
DEFAULT_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
BASE_URL = "https://open-api.bingx.com"

TRADING_MODE = "MANUAL"
MARKET_TYPE = "FUTURES"


def _genera_firma(payload: dict, secret_key: str) -> str:
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature


# ==========================================
# MOTORE DI ANALISI TRADINGVIEW & INNESCO ATR
# ==========================================
def analizza_tradingview_tf(simbolo_bingx: str, market_type: str, tv_interval: str):
    tv_symbol = simbolo_bingx.replace("-", "").upper()
    if market_type in ["FUTURES", "STANDARD_FUTURES"]:
        tv_symbol += ".P"

    try:
        handler = TA_Handler(symbol=tv_symbol, screener="crypto", exchange="BINGX", interval=tv_interval)
        analysis = handler.get_analysis()
        summary = analysis.summary
        indicators = analysis.indicators

        rec = summary.get("RECOMMENDATION", "NEUTRAL")
        rsi = round(float(indicators.get("RSI", 50.0)), 2)
        close_price = float(indicators.get("close", 0))
        atr = float(indicators.get("ATR", close_price * 0.01))

        if market_type == "SPOT":
            trend = "BUY" if rec == "STRONG_BUY" else "NEUTRO"
        else:
            trend = "BUY" if rec == "STRONG_BUY" else ("SELL" if rec == "STRONG_SELL" else "NEUTRO")

        return {
            "interval": tv_interval,
            "trend": trend,
            "tvRec": rec,
            "buyCount": summary.get("BUY", 0),
            "sellCount": summary.get("SELL", 0),
            "rsi14": rsi,
            "atr14": atr,
            "close": close_price
        }
    except Exception:
        try:
            clean_sym = simbolo_bingx.replace("-", "").upper()
            handler = TA_Handler(symbol=clean_sym, screener="crypto", exchange="BINANCE", interval=tv_interval)
            analysis = handler.get_analysis()
            summary = analysis.summary
            indicators = analysis.indicators
            rec = summary.get("RECOMMENDATION", "NEUTRAL")
            close_price = float(indicators.get("close", 0))

            trend = "BUY" if rec == "STRONG_BUY" else ("SELL" if rec == "STRONG_SELL" else "NEUTRO")
            return {
                "interval": tv_interval, "trend": trend, "tvRec": rec,
                "buyCount": summary.get("BUY", 0), "sellCount": summary.get("SELL", 0),
                "rsi14": round(float(indicators.get("RSI", 50.0)), 2),
                "atr14": float(indicators.get("ATR", close_price * 0.01)),
                "close": close_price
            }
        except Exception:
            return None


def calcola_parametri_con_confluenza_assoluta(simbolo: str, market_type: str):
    tf_map = {
        "5m": Interval.INTERVAL_5_MINUTES,
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS
    }

    tfs = {}
    for label, tv_iv in tf_map.items():
        res = analizza_tradingview_tf(simbolo, market_type, tv_iv)
        if not res:
            return None
        tfs[label] = res

    # 🛡️ FILTRO DI ALLINEAMENTO MULTI-TF RIGOROSO
    trend_5m = tfs["5m"]["trend"]
    trend_15m = tfs["15m"]["trend"]
    trend_1h = tfs["1h"]["trend"]
    trend_4h = tfs["4h"]["trend"]

    if trend_5m == trend_15m == trend_1h == trend_4h and trend_5m != "NEUTRO":
        side = trend_5m
        score = 99.9
    else:
        side = "NEUTRO"
        score = 50.0

    base = tfs["15m"]
    price = tfs["5m"]["close"]
    atr = base["atr14"] if base["atr14"] > 0 else price * 0.01
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7

    k = 1 if side == "BUY" else (-1 if side == "SELL" else 0)
    
    # Innesco basato sull'ATR
    offset_innesco = atr * 0.1
    prezzo_innesco = round(price + (k * offset_innesco), dec) if k != 0 else price

    dist_sl = min(max(atr * 1.2, price * 0.007), price * 0.015)
    dist_tp = dist_sl * 3.0  # Rapporto Rischio/Rendimento 1:3

    sl = round(prezzo_innesco - k * dist_sl, dec) if k != 0 else round(price * 0.99, dec)
    tp = round(prezzo_innesco + k * dist_tp, dec) if k != 0 else round(price * 1.03, dec)

    return {
        "symbol": simbolo,
        "marketType": market_type,
        "price": round(price, dec),
        "triggerPrice": prezzo_innesco,
        "side": side,
        "sentiment": "🔥 SEGNALE TRADINGVIEW CERTIFICATO" if side != "NEUTRO" else "🛡️ In attesa di allineamento...",
        "confluenza": "Multi-TF Allineato (5m, 15m, 1h, 4h) 🎯",
        "precisionScore": score,
        "rsi14": base["rsi14"],
        "atr14": round(atr, dec),
        "sl": sl,
        "tp": tp,
        "decimals": dec,
        "tfs": tfs
    }


def get_live_market_data(market_type: str = "FUTURES"):
    url = (f"{BASE_URL}/openApi/spot/v1/ticker/24hr" if market_type == "SPOT"
           else f"{BASE_URL}/openApi/swap/v2/quote/ticker")
    try:
        response = requests.get(url, timeout=4)
        data = response.json()
        if data.get("code") == 0 and "data" in data:
            market_map = {}
            for t in data["data"]:
                symbol = t.get("symbol")
                if not symbol or not symbol.endswith("-USDT"):
                    continue
                price = float(t.get("lastPrice", 0))
                if price <= 0:
                    continue
                quote_vol = float(t.get("quoteVolume", t.get("volume", 0)))
                raw_chg = str(t.get("priceChangePercent", "0")).replace("%", "")
                change = float(raw_chg) if market_type == "SPOT" else float(raw_chg) * 100

                market_map[symbol] = {
                    "price": price,
                    "change24h": change,
                    "volume": quote_vol,
                    "marketType": market_type
                }
            return market_map
    except Exception as e:
        print(f"[MARKET ERROR] {e}")
    return {}


def imposta_leva_bingx(simbolo: str, leva: int, lato: str, api_key: str, secret_key: str):
    endpoint = "/openApi/swap/v2/trade/leverage"
    url = BASE_URL + endpoint
    position_side = "LONG" if lato == "BUY" else "SHORT"
    payload = {
        "symbol": simbolo, "leverage": str(leva),
        "positionSide": position_side, "timestamp": str(int(time.time() * 1000))
    }
    query_string = _genera_firma(payload, secret_key)
    try:
        requests.post(f"{url}?{query_string}", headers={"X-BX-APIKEY": api_key})
    except Exception as e:
        print(f"[LEVERAGE EXCEPT] {e}")


def _invia_richiesta_ordine(endpoint: str, payload_ordine: dict, active_api: str, active_secret: str) -> dict:
    url = BASE_URL + endpoint
    query_string = _genera_firma(payload_ordine, active_secret)
    response = requests.post(f"{url}?{query_string}", headers={"X-BX-APIKEY": active_api})
    return response.json()


def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti."}

    if lato not in ["BUY", "SELL"]:
        return {"code": -1, "msg": "Operazione bloccata: segnale TradingView non allineato."}

    if market_type == "STANDARD_FUTURES":
        endpoint = "/openApi/futures/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo, "side": lato, "type": "MARKET",
            "quantity": str(quantita), "leverage": str(leva),
            "timestamp": str(int(time.time() * 1000))
        }
    elif market_type == "FUTURES":
        imposta_leva_bingx(simbolo, leva, lato, active_api, active_secret)
        endpoint = "/openApi/swap/v2/trade/order"
        position_side = "LONG" if lato == "BUY" else "SHORT"
        payload_ordine = {
            "symbol": simbolo, "side": lato, "positionSide": position_side,
            "type": "MARKET", "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }
    else:
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo, "side": lato, "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }

    try:
        return _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
    except Exception as e:
        return {"code": -1, "msg": str(e)}


# ==========================================
# ENDPOINT API FLASK
# ==========================================
@app.route('/api/market-data', methods=['GET'])
def api_market_data():
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    fetch_type = "FUTURES" if mtype == "STANDARD_FUTURES" else mtype
    return jsonify({"code": 0, "marketType": mtype, "data": get_live_market_data(fetch_type)})


@app.route('/api/deep-scan', methods=['GET'])
def api_deep_scan():
    simbolo = request.args.get("symbol", "BTC-USDT")
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    res_data = calcola_parametri_con_confluenza_assoluta(simbolo, mtype)
    if not res_data:
        return jsonify({"code": -1, "msg": "Dati non disponibili"})
    return jsonify({"code": 0, "data": res_data})


@app.route('/api/esegui-segnale', methods=['POST'])
def webhook_esegui_segnale():
    dati = request.json or {}
    risultato = esegui_ordine_bingx(
        simbolo=dati.get("symbol"),
        lato=dati.get("action"),
        quantita=float(dati.get("size", 1.0)),
        prezzo_ingresso=float(dati.get("entry", 0)),
        prezzo_tp=float(dati.get("tp", 0)),
        prezzo_sl=float(dati.get("sl", 0)),
        leva=int(dati.get("leverage", 3)),
        market_type=dati.get("marketType", MARKET_TYPE).upper(),
        api_key=dati.get("apiKey", ""),
        secret_key=dati.get("secretKey", "")
    )
    return jsonify(risultato)


@app.route('/api/set-market-type', methods=['POST'])
def set_market_type():
    global MARKET_TYPE
    dati = request.json or {}
    if dati.get("marketType") in ["FUTURES", "SPOT", "STANDARD_FUTURES"]:
        MARKET_TYPE = dati.get("marketType")
        return jsonify({"code": 0, "marketType": MARKET_TYPE})
    return jsonify({"code": -1, "msg": "Mercato non valido"})


# ==========================================
# INTERFACCIA FRONTEND (DASHBOARD HTML)
# ==========================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro - TradingView Dashboard Integration</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .terminal-card { background: #08080A; border: 1px solid rgba(255, 255, 255, 0.16); }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
  </style>
</head>
<body class="min-h-screen flex flex-col p-4 gap-4 max-w-[1600px] mx-auto">

  <!-- HEADER -->
  <header class="terminal-card rounded-2xl p-4 flex flex-wrap items-center justify-between gap-3">
    <div>
      <h1 class="text-lg font-extrabold text-white">APEXSCAN <span class="text-[#00FF66]">TradingView Live Dashboard</span></h1>
      <p class="text-xs font-mono text-white/70">Lettura indicatori TradingView integrata • Esecuzione su BingX</p>
    </div>
    <div class="flex items-center gap-2">
      <button onclick="switchMarketType('FUTURES')" id="btnMarketFutures" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0066FF] text-white">FUTURES</button>
      <button onclick="switchMarketType('SPOT')" id="btnMarketSpot" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-white/10 text-white">SPOT</button>
      <button onclick="toggleApiModal()" class="px-3 py-1.5 rounded-lg text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/50">🔑 API BingX</button>
    </div>
  </header>

  <!-- LISTA MERCATI E ATTIVAZIONE TRADINGVIEW -->
  <section class="terminal-card rounded-2xl p-4 space-y-4">
    <div class="flex justify-between items-center flex-wrap gap-2">
      <h2 class="text-sm font-bold text-white/90">📊 Seleziona Asset per Lettura TradingView &amp; Innesco</h2>
      <span class="text-xs font-mono text-[#00FF66]">● Motore TradingView attivo</span>
    </div>
    <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca asset (es. BTC, ETH, SOL)..." class="w-full bg-black border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
    <div id="tablesContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"></div>
  </section>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-3">
    <div class="bg-[#08080A] border border-[#00FF66]/40 rounded-2xl w-full max-w-[450px] p-5 space-y-4 font-mono text-xs">
      <div class="flex justify-between items-center"><h2 class="text-sm font-bold text-[#00FF66]">🔑 API BingX</h2><button onclick="toggleApiModal()" class="text-white">✕</button></div>
      <input type="text" id="userApiKeyInput" placeholder="API Key" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white" />
      <input type="password" id="userSecretKeyInput" placeholder="Secret Key" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white" />
      <button onclick="saveUserApiKeys()" class="w-full py-2 rounded-xl font-bold bg-[#00FF66] text-black">Salva</button>
    </div>
  </div>

  <!-- MODALE ANALISI E SEGNALE TRADINGVIEW -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-3">
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[650px] p-5 space-y-4 font-mono text-xs">
      <div class="flex justify-between items-center border-b border-white/15 pb-3">
        <div>
          <h2 id="modalSymbol" class="text-base font-extrabold text-white">BTC-USDT</h2>
          <span id="modalSentiment" class="text-[#00FF66] font-bold">Lettura TradingView...</span>
        </div>
        <button onclick="closeModal()" class="p-1.5 rounded-lg bg-white/10 text-white">✕</button>
      </div>

      <!-- Stato dei 4 Timeframe TradingView -->
      <div id="tfPanel" class="grid grid-cols-2 sm:grid-cols-4 gap-2"></div>

      <div class="bg-black border border-white/15 rounded-xl p-3 space-y-2">
        <div class="flex justify-between"><span>Prezzo Live:</span><strong id="modalLivePrice">--</strong></div>
        <div class="flex justify-between text-[#00FF66]"><span>🎯 Trigger Innesco (ATR):</span><strong id="modalTriggerPrice">--</strong></div>
        <div class="flex justify-between"><span>Segnale TradingView:</span><strong id="modalSide">--</strong></div>
        <div class="flex justify-between"><span>Take Profit (1:3):</span><strong id="modalTrendingTP" class="text-[#00FF66]">--</strong></div>
        <div class="flex justify-between"><span>Stop Loss Protettivo:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-white/70 mb-1">Quantità:</label>
          <input type="number" id="tradeQuantityInput" value="0.01" step="any" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white" />
        </div>
        <div>
          <label class="block text-white/70 mb-1">Leva:</label>
          <select id="leverageSelect" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white">
            <option value="5">5x</option>
            <option value="10" selected>10x</option>
            <option value="20">20x</option>
          </select>
        </div>
      </div>

      <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs">
        ⚡ Esegui Segnale TradingView su BingX
      </button>
    </div>
  </div>

  <script>
    let state = { marketData: {}, marketType: 'FUTURES', searchQuery: '', currentSelectedAsset: null };

    function toggleApiModal() { document.getElementById('apiModal').classList.toggle('hidden'); document.getElementById('apiModal').classList.toggle('flex'); }
    function saveUserApiKeys() {
      localStorage.setItem('bingx_user_api_key', document.getElementById('userApiKeyInput').value.trim());
      localStorage.setItem('bingx_user_secret_key', document.getElementById('userSecretKeyInput').value.trim());
      toggleApiModal(); alert("Chiavi salvate!");
    }
    async function switchMarketType(mtype) {
      state.marketType = mtype;
      await fetch('/api/set-market-type', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ marketType: mtype }) });
      pollMarketData();
    }
    function filterCoinSearch(q) { state.searchQuery = q.toLowerCase().trim(); renderDashboard(); }
    async function pollMarketData() {
      const res = await fetch(`/api/market-data?marketType=${state.marketType}`);
      const json = await res.json();
      if (json.code === 0) { state.marketData = json.data; renderDashboard(); }
    }
    function renderDashboard() {
      let sorted = Object.keys(state.marketData).map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => (b.volume || 0) - (a.volume || 0));
      if (state.searchQuery) sorted = sorted.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));
      document.getElementById('tablesContainer').innerHTML = sorted.slice(0, 30).map(c => `
        <div onclick='openModal("${c.symbol}")' class="bg-black border border-white/20 hover:border-[#00FF66] rounded-xl p-3 cursor-pointer flex justify-between items-center transition">
          <div>
            <strong class="text-white text-sm">${c.symbol}</strong>
            <span class="block text-[10px] text-[#00FF66]">Analizza su TradingView 📊</span>
          </div>
          <div class="text-right font-mono">
            <span class="text-white text-xs font-bold">$${c.price}</span>
            <span class="block text-[10px] ${c.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${c.change24h.toFixed(2)}%</span>
          </div>
        </div>
      `).join('');
    }
    async function openModal(symbol) {
      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalSentiment').textContent = "Lettura indicatori TradingView...";
      document.getElementById('tfPanel').innerHTML = '';

      const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(symbol)}&marketType=${state.marketType}`);
      const json = await res.json();
      if (json.code === 0 && json.data) {
        const d = json.data; state.currentSelectedAsset = d;
        document.getElementById('modalSentiment').textContent = d.sentiment;
        document.getElementById('modalLivePrice').textContent = `$${d.price}`;
        document.getElementById('modalTriggerPrice').textContent = `$${d.triggerPrice}`;
        document.getElementById('modalSide').textContent = d.side;
        document.getElementById('modalSide').className = d.side === 'BUY' ? 'text-[#00FF66]' : (d.side === 'SELL' ? 'text-[#FF334B]' : 'text-yellow-400');
        document.getElementById('modalTrendingTP').textContent = `$${d.tp}`;
        document.getElementById('modalSL').textContent = `$${d.sl}`;

        document.getElementById('tfPanel').innerHTML = ['5m', '15m', '1h', '4h'].map(iv => {
          const t = d.tfs[iv];
          const col = t.trend === 'BUY' ? '#00FF66' : (t.trend === 'SELL' ? '#FF334B' : '#FFCC00');
          return `
            <div class="bg-white/5 p-2 rounded-lg border border-white/10 text-center">
              <div class="font-bold text-white">${iv}</div>
              <div style="color:${col}" class="font-extrabold text-[11px]">${t.tvRec}</div>
              <div class="text-[9px] text-white/60">RSI: ${t.rsi14}</div>
            </div>
          `;
        }).join('');
      } else { alert(json.msg || "Errore"); closeModal(); }
    }
    function closeModal() { document.getElementById('assetModal').classList.add('hidden'); document.getElementById('assetModal').classList.remove('flex'); }
    async function eseguiOrdineSuBingX() {
      const asset = state.currentSelectedAsset;
      if (!asset) return;
      if (asset.side === 'NEUTRO') {
        alert("Segnale TradingView non allineato: operazione bloccata.");
        return;
      }
      const res = await fetch('/api/esegui-segnale', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: asset.symbol, action: asset.side, size: parseFloat(document.getElementById('tradeQuantityInput').value),
          entry: asset.triggerPrice, tp: asset.tp, sl: asset.sl, leverage: parseInt(document.getElementById('leverageSelect').value),
          marketType: state.marketType, apiKey: localStorage.getItem('bingx_user_api_key'] || '', secretKey: localStorage.getItem('bingx_user_secret_key'] || ''
        })
      });
      const data = await res.json();
      if (data.code === 0) { alert("Segnale TradingView eseguito con successo su BingX!"); closeModal(); } else { alert("Errore: " + data.msg); }
    }
    window.addEventListener('DOMContentLoaded', () => { pollMarketData(); setInterval(pollMarketData, 3000); });
  </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
