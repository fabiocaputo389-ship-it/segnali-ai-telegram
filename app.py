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
MARKET_TYPE = "FUTURES"  # "FUTURES", "SPOT" o "STANDARD_FUTURES"


def _genera_firma(payload: dict, secret_key: str) -> str:
    """Genera la firma HMAC SHA256 richiesta dalle API di BingX."""
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature


# ==========================================
# MOTORE DI LETTURA TRADINGVIEW (SENZA LOGIN)
# ==========================================
def analizza_tradingview_tf(simbolo_bingx: str, market_type: str, tv_interval: str):
    """
    Interroga direttamente gli indicatori di TradingView senza bisogno di account.
    """
    # Converte il simbolo da BTC-USDT a BTCUSDT per TradingView
    tv_symbol = simbolo_bingx.replace("-", "").upper()
    if market_type in ["FUTURES", "STANDARD_FUTURES"]:
        tv_symbol += ".P" # Suffisso per i contratti Perpetui su TradingView

    try:
        handler = TA_Handler(
            symbol=tv_symbol,
            screener="crypto",
            exchange="BINGX",
            interval=tv_interval
        )
        analysis = handler.get_analysis()
        summary = analysis.summary # Es: {'RECOMMENDATION': 'STRONG_BUY', 'BUY': 15, 'SELL': 2, 'NEUTRAL': 9}
        indicators = analysis.indicators

        rec = summary.get("RECOMMENDATION", "NEUTRAL")
        rsi = round(float(indicators.get("RSI", 50.0)), 2)
        ema9 = float(indicators.get("EMA10", indicators.get("close", 0)))
        ema21 = float(indicators.get("EMA20", indicators.get("close", 0)))
        ema50 = float(indicators.get("EMA50", indicators.get("close", 0)))
        close_price = float(indicators.get("close", 0))
        atr = float(indicators.get("ATR", close_price * 0.01))

        # Filtro Anti-Falsi Segnali: accetta SOLO STRONG_BUY o STRONG_SELL confermati
        if market_type == "SPOT":
            trend = "BUY" if rec == "STRONG_BUY" else "NEUTRO"
        else:
            if rec == "STRONG_BUY":
                trend = "BUY"
            elif rec == "STRONG_SELL":
                trend = "SELL"
            else:
                trend = "NEUTRO"

        return {
            "interval": tv_interval,
            "trend": trend,
            "tvRec": rec,
            "buyCount": summary.get("BUY", 0),
            "sellCount": summary.get("SELL", 0),
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "rsi14": rsi,
            "atr14": atr,
            "close": close_price
        }
    except Exception as e:
        # Fallback su BINANCE se la coppia non è listata su BINGX su TradingView
        try:
            clean_sym = simbolo_bingx.replace("-", "").upper()
            handler = TA_Handler(
                symbol=clean_sym,
                screener="crypto",
                exchange="BINANCE",
                interval=tv_interval
            )
            analysis = handler.get_analysis()
            summary = analysis.summary
            indicators = analysis.indicators
            rec = summary.get("RECOMMENDATION", "NEUTRAL")
            close_price = float(indicators.get("close", 0))

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
                "ema9": float(indicators.get("EMA10", close_price)),
                "ema21": float(indicators.get("EMA20", close_price)),
                "ema50": float(indicators.get("EMA50", close_price)),
                "rsi14": round(float(indicators.get("RSI", 50.0)), 2),
                "atr14": float(indicators.get("ATR", close_price * 0.01)),
                "close": close_price
            }
        except Exception as e2:
            print(f"[TV ERROR {simbolo_bingx} {tv_interval}] {e2}")
            return None


def calcola_parametri_da_tradingview(simbolo: str, market_type: str):
    """
    Combina l'analisi multi-timeframe di TradingView (5m, 15m, 1h, 4h).
    """
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

    # Cerca il timeframe con il segnale forte (STRONG_BUY / STRONG_SELL)
    exact_tf = "15m"
    if tfs["15m"]["trend"] != "NEUTRO":
        exact_tf = "15m"
    elif tfs["1h"]["trend"] != "NEUTRO":
        exact_tf = "1h"
    elif tfs["5m"]["trend"] != "NEUTRO":
        exact_tf = "5m"
    elif tfs["4h"]["trend"] != "NEUTRO":
        exact_tf = "4h"

    base = tfs[exact_tf]
    side = base["trend"]
    price = tfs["5m"]["close"]
    atr = base["atr14"] if base["atr14"] > 0 else price * 0.01
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7

    k = 1 if side == "BUY" else (-1 if side == "SELL" else 0)
    dist_sl = min(max(atr * 1.1, price * 0.006), price * 0.015)
    dist_tp = dist_sl * 2.5

    sl = round(price - k * dist_sl, dec) if k != 0 else round(price * 0.99, dec)
    tp = round(price + k * dist_tp, dec) if k != 0 else round(price * 1.025, dec)

    score = 99.9 if side != "NEUTRO" else 85.0
    tv_details = f"TV: {base['tvRec']} ({base['buyCount']} Buy / {base['sellCount']} Sell)"

    return {
        "symbol": simbolo,
        "marketType": market_type,
        "price": round(price, dec),
        "side": side,
        "sentiment": f"TradingView {base['tvRec']} 🎯 ({tv_details})",
        "confluenza": f"Conferma Multi-TF TradingView ({exact_tf}) ⚡",
        "exactTf": exact_tf,
        "precisionScore": score,
        "ema9": round(base["ema9"], dec),
        "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"],
        "atr14": round(atr, dec),
        "sl": sl,
        "tp": tp,
        "decimals": dec,
        "tempoSec": 900,
        "tfs": tfs
    }


def get_live_market_data(market_type: str = "FUTURES"):
    """Recupera la lista dei prezzi live per popolare la tabella."""
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
                    "category": "Criptovalute",
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
        "symbol": simbolo,
        "leverage": str(leva),
        "positionSide": position_side,
        "timestamp": str(int(time.time() * 1000))
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


def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "15m", market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti."}

    if lato not in ["BUY", "SELL"]:
        return {"code": -1, "msg": "Segnale NEUTRO: operazione bloccata dal filtro anti-falsi."}

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
    res_data = calcola_parametri_da_tradingview(simbolo, mtype)
    if not res_data:
        return jsonify({"code": -1, "msg": "Dati TradingView momentaneamente non disponibili per questo asset"})
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
        timeframe=dati.get("timeframe", "15m"),
        market_type=dati.get("marketType", MARKET_TYPE).upper(),
        api_key=dati.get("apiKey", ""),
        secret_key=dati.get("secretKey", "")
    )
    return jsonify(risultato)


@app.route('/api/set-mode', methods=['POST'])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    if dati.get("mode") in ["MANUAL", "AUTO"]:
        TRADING_MODE = dati.get("mode")
        return jsonify({"code": 0, "mode": TRADING_MODE})
    return jsonify({"code": -1, "msg": "Modalità non valida"})


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
  <title>ApexScan Pro - TradingView Live Scanner</title>
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
      <h1 class="text-lg font-extrabold text-white">APEXSCAN <span class="text-[#00FF66]">TradingView Direct Engine</span></h1>
      <p class="text-xs font-mono text-white/70">Lettura diretta indicatori TradingView (Senza Account) • Esecuzione su BingX</p>
    </div>
    <div class="flex items-center gap-2">
      <button onclick="switchMarketType('FUTURES')" id="btnMarketFutures" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0066FF] text-white">FUTURES</button>
      <button onclick="switchMarketType('SPOT')" id="btnMarketSpot" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-white/10 text-white">SPOT</button>
      <button onclick="toggleApiModal()" class="px-3 py-1.5 rounded-lg text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/50">🔑 API BingX</button>
    </div>
  </header>

  <!-- CONTENITORE POSIZIONI ATTIVE -->
  <div id="activePositionsContainer" class="space-y-2"></div>

  <!-- RICERCA E LISTA ASSET -->
  <section class="terminal-card rounded-2xl p-4 space-y-4">
    <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca asset su TradingView (es. BTC, ETH, SOL, XRP)..." class="w-full bg-black border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
    <div id="tablesContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"></div>
  </section>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-3">
    <div class="bg-[#08080A] border border-[#00FF66]/40 rounded-2xl w-full max-w-[450px] p-5 space-y-4 font-mono text-xs">
      <div class="flex justify-between items-center">
        <h2 class="text-sm font-bold text-[#00FF66]">🔑 Chiavi API BingX</h2>
        <button onclick="toggleApiModal()" class="text-white">✕</button>
      </div>
      <input type="text" id="userApiKeyInput" placeholder="API Key BingX" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white" />
      <input type="password" id="userSecretKeyInput" placeholder="Secret Key BingX" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white" />
      <button onclick="saveUserApiKeys()" class="w-full py-2 rounded-xl font-bold bg-[#00FF66] text-black">Salva Chiavi</button>
    </div>
  </div>

  <!-- MODALE ANALISI TRADINGVIEW -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-3">
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[650px] p-5 space-y-4 font-mono text-xs">
      <div class="flex justify-between items-center border-b border-white/15 pb-3">
        <div>
          <h2 id="modalSymbol" class="text-base font-extrabold text-white">BTC-USDT</h2>
          <span id="modalSentiment" class="text-[#00FF66] font-bold">Scansione TradingView...</span>
        </div>
        <button onclick="closeModal()" class="p-1.5 rounded-lg bg-white/10 text-white">✕</button>
      </div>

      <!-- 4 Timeframe TradingView -->
      <div id="tfPanel" class="grid grid-cols-2 sm:grid-cols-4 gap-2"></div>

      <div class="bg-black border border-white/15 rounded-xl p-3 space-y-2">
        <div class="flex justify-between"><span>Prezzo Live:</span><strong id="modalLivePrice">--</strong></div>
        <div class="flex justify-between"><span>Segnale TradingView:</span><strong id="modalSide">--</strong></div>
        <div class="flex justify-between"><span>Take Profit ($ TP):</span><strong id="modalTrendingTP" class="text-[#00FF66]">--</strong></div>
        <div class="flex justify-between"><span>Stop Loss ($ SL):</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-white/70 mb-1">Quantità Asset:</label>
          <input type="number" id="tradeQuantityInput" value="0.01" step="any" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white" />
        </div>
        <div>
          <label class="block text-white/70 mb-1">Leva (Futures):</label>
          <select id="leverageSelect" class="w-full bg-black border border-white/30 rounded-lg px-3 py-2 text-white">
            <option value="3">3x</option>
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
    let state = {
      marketData: {},
      marketType: 'FUTURES',
      searchQuery: '',
      currentSelectedAsset: null
    };

    function toggleApiModal() {
      const m = document.getElementById('apiModal');
      m.classList.toggle('hidden');
      m.classList.toggle('flex');
    }

    function saveUserApiKeys() {
      localStorage.setItem('bingx_user_api_key', document.getElementById('userApiKeyInput').value.trim());
      localStorage.setItem('bingx_user_secret_key', document.getElementById('userSecretKeyInput').value.trim());
      toggleApiModal();
      alert("Chiavi salvate!");
    }

    async function switchMarketType(mtype) {
      state.marketType = mtype;
      await fetch('/api/set-market-type', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ marketType: mtype })
      });
      pollMarketData();
    }

    function filterCoinSearch(q) {
      state.searchQuery = q.toLowerCase().trim();
      renderDashboard();
    }

    async function pollMarketData() {
      const res = await fetch(`/api/market-data?marketType=${state.marketType}`);
      const json = await res.json();
      if (json.code === 0) {
        state.marketData = json.data;
        renderDashboard();
      }
    }

    function renderDashboard() {
      const keys = Object.keys(state.marketData);
      let sorted = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] }))
        .sort((a, b) => (b.volume || 0) - (a.volume || 0));

      if (state.searchQuery) {
        sorted = sorted.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));
      }

      document.getElementById('tablesContainer').innerHTML = sorted.slice(0, 30).map(c => `
        <div onclick='openModal("${c.symbol}")' class="bg-black border border-white/20 hover:border-[#00FF66] rounded-xl p-3 cursor-pointer flex justify-between items-center">
          <div>
            <strong class="text-white text-sm">${c.symbol}</strong>
            <span class="block text-[10px] text-[#00FF66]">Clicca per analisi TradingView</span>
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
      document.getElementById('modalSentiment').textContent = "Lettura indicatori TradingView in corso...";
      document.getElementById('tfPanel').innerHTML = '';

      const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(symbol)}&marketType=${state.marketType}`);
      const json = await res.json();

      if (json.code === 0 && json.data) {
        const d = json.data;
        state.currentSelectedAsset = d;

        document.getElementById('modalSentiment').textContent = d.sentiment;
        document.getElementById('modalLivePrice').textContent = `$${d.price}`;
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
      } else {
        alert(json.msg || "Errore lettura TradingView");
        closeModal();
      }
    }

    function closeModal() {
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
    }

    async function eseguiOrdineSuBingX() {
      const asset = state.currentSelectedAsset;
      if (!asset) return;
      if (asset.side === 'NEUTRO') {
        alert("Segnale NEUTRO su TradingView: operazione bloccata per evitare falsi segnali.");
        return;
      }

      const res = await fetch('/api/esegui-segnale', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: asset.symbol,
          action: asset.side,
          size: parseFloat(document.getElementById('tradeQuantityInput').value),
          entry: asset.price,
          tp: asset.tp,
          sl: asset.sl,
          leverage: parseInt(document.getElementById('leverageSelect').value),
          marketType: state.marketType,
          apiKey: localStorage.getItem('bingx_user_api_key') || '',
          secretKey: localStorage.getItem('bingx_user_secret_key') || ''
        })
      });
      const data = await res.json();
      if (data.code === 0) {
        alert("Ordine eseguito su BingX!");
        closeModal();
      } else {
        alert("Errore: " + data.msg);
      }
    }

    window.addEventListener('DOMContentLoaded', () => {
      pollMarketData();
      setInterval(pollMarketData, 3000);
    });
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
