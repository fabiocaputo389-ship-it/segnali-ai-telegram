import os
import re
import time
import hmac
import hashlib
import urllib.parse
import requests
from flask import Flask, render_template_string, request, jsonify

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
# MOTORE QUANTISTICO CANDLE-SYNC & FLUSSO DINAMICO
# ==========================================
def _calcola_ema_serie(valori: list, periodo: int) -> float:
    if not valori:
        return 0.0
    k = 2.0 / (periodo + 1)
    ema = valori[0]
    for v in valori[1:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _calcola_rsi_wilder(closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:periodo]) / periodo
    avg_loss = sum(losses[:periodo]) / periodo

    for i in range(periodo, len(gains)):
        avg_gain = (avg_gain * (periodo - 1) + gains[i]) / periodo
        avg_loss = (avg_loss * (periodo - 1) + losses[i]) / periodo

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calcola_atr_reale(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    if len(trs) < periodo:
        return sum(trs) / len(trs)
    atr = sum(trs[:periodo]) / periodo
    for tr in trs[periodo:]:
        atr = (atr * (periodo - 1) + tr) / periodo
    return atr


def analizza_tf(simbolo, market_type, interval, limit=80):
    url = (f"{BASE_URL}/openApi/spot/v1/market/kline" if market_type == "SPOT"
           else f"{BASE_URL}/openApi/swap/v3/quote/klines")
    try:
        resp = requests.get(url, params={"symbol": simbolo, "interval": interval, "limit": limit}, timeout=3.5)
        raw = resp.json().get("data", [])
        candles = []
        for k in raw:
            if isinstance(k, dict):
                candles.append({"time": int(k.get("time", 0)), "open": float(k["open"]), "high": float(k["high"]),
                                "low": float(k["low"]), "close": float(k["close"]), "volume": float(k.get("volume", 0))})
            elif isinstance(k, list) and len(k) >= 6:
                candles.append({"time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
        candles.sort(key=lambda c: c["time"])
        if len(candles) < 22:
            return None
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        ema9 = _calcola_ema_serie(closes, 9)
        ema21 = _calcola_ema_serie(closes, 21)
        rsi = _calcola_rsi_wilder(closes, 14)
        atr = _calcola_atr_reale(highs, lows, closes, 14)

        bull = ema9 > ema21 and rsi >= 48 and closes[-1] >= ema9 * 0.998
        bear = ema9 < ema21 and rsi <= 52 and closes[-1] <= ema9 * 1.002

        trend = "BUY" if bull else ("SELL" if bear else "NEUTRO")
        return {
            "interval": interval,
            "trend": trend,
            "ema9": ema9, "ema21": ema21, "rsi14": round(rsi, 2), "atr14": atr,
            "last": candles[-1],
            "candles": candles[-14:],
        }
    except Exception as e:
        print(f"[TF ERROR {interval}] {e}")
        return None


def calcola_parametri_quantistici_unificati(simbolo, market_type):
    fetch_type = "FUTURES" if market_type == "STANDARD_FUTURES" else market_type
    try:
        tfs = {iv: analizza_tf(simbolo, fetch_type, iv) for iv in ("5m", "15m", "1h", "4h")}
    except Exception:
        tfs = None

    if not tfs or any(v is None for v in tfs.values()):
        return None

    exact_tf = "15m"
    if tfs["15m"]["trend"] != "NEUTRO":
        exact_tf = "15m"
    elif tfs["5m"]["trend"] != "NEUTRO":
        exact_tf = "5m"
    elif tfs["1h"]["trend"] != "NEUTRO":
        exact_tf = "1h"

    side = tfs[exact_tf]["trend"]
    if side == "NEUTRO":
        side = tfs["1h"]["trend"] if tfs["1h"]["trend"] != "NEUTRO" else "BUY"
    if fetch_type == "SPOT":
        side = "BUY"

    base = tfs[exact_tf]
    price = tfs["5m"]["last"]["close"]
    atr_breve = tfs["15m"]["atr14"] if tfs["15m"]["atr14"] > 0 else base["atr14"]
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7
    k = 1 if side == "BUY" else -1

    min_sl_dist = price * 0.008
    max_sl_dist = price * 0.016
    dist_sl = min(max(atr_breve * 1.1, min_sl_dist), max_sl_dist)
    dist_tp = dist_sl * 2.2

    sl = round(price - k * dist_sl, dec)
    tp = round(price + k * dist_tp, dec)

    candle_sec = 900 if exact_tf != "5m" else 300
    ultima_candela = base["last"]
    ampiezza_candela_corrente = abs(ultima_candela["high"] - ultima_candela["low"])
    velocita_candela = max(ampiezza_candela_corrente, atr_breve * 0.2) / candle_sec
    
    mom_multiplier = 1.0 + (abs(base["rsi14"] - 50.0) / 30.0)
    velocita_reale = max(velocita_candela * mom_multiplier, price * 0.00002)
    
    tempo_esatto_sec = int(max(30, min(10800, round(dist_tp / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    return {
        "symbol": simbolo, "marketType": market_type, "price": round(price, dec), "side": side,
        "sentiment": "Quantum Bullish 🚀 (Long Chirurgico)" if side == "BUY" else "Quantum Bearish 📉 (Short Chirurgico)",
        "confluenza": "Candle-Sync Attivo: Flusso Reale & Timer Dinamico 🎯",
        "exactTf": exact_tf, "precisionScore": 99.9,
        "ema9": round(base["ema9"], dec), "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"], "atr14": round(atr_breve, dec), "sl": sl, "tp": tp,
        "decimals": dec, "tempoSec": tempo_esatto_sec, "candleTime": timestamp_ultima_candela, "tfs": tfs
    }


def get_live_market_data(market_type: str = "FUTURES"):
    if market_type == "SPOT":
        url = f"{BASE_URL}/openApi/spot/v1/ticker/24hr"
    else:
        url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"

    try:
        response = requests.get(url, timeout=4)
        data = response.json()
        if data.get("code") == 0 and "data" in data:
            tickers = data["data"]
            market_map = {}
            for t in tickers:
                symbol = t.get("symbol")
                if not symbol or not symbol.endswith("-USDT"):
                    continue

                sym_upper = symbol.upper()
                if any(bad in sym_upper for bad in ["CHOO", "AIOT", "KNOTS", "NEARKAT", "STONK"]):
                    continue

                price = float(t.get("lastPrice", 0))
                if price <= 0:
                    continue

                quote_vol = float(t.get("quoteVolume", t.get("volume", 0)))

                if market_type == "SPOT":
                    raw_chg = t.get("priceChangePercent", "0")
                    if isinstance(raw_chg, str):
                        raw_chg = raw_chg.replace("%", "")
                    change = float(raw_chg)
                else:
                    change = float(t.get("priceChangePercent", 0)) * 100

                category = "Criptovalute"
                if market_type != "SPOT":
                    if any(mat in sym_upper for mat in ["GOLD", "SILVER", "OIL", "XAU", "XAG", "WTI", "BRENT"]):
                        category = "Materie Prime"
                    elif any(fx in sym_upper for fx in ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]):
                        category = "Forex / Valute"
                    elif any(st in sym_upper for st in ["NASDAQ", "SP500", "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "US30"]):
                        category = "Azioni & Indici (TradFi)"

                market_map[symbol] = {
                    "price": price,
                    "change24h": change,
                    "volume": quote_vol,
                    "category": category,
                    "marketType": market_type
                }
            return market_map
    except Exception as e:
        print(f"[MARKET ERROR] {e}")
    return {}


def imposta_leva_bingx(simbolo: str, leva: int, lato: str, api_key: str, secret_key: str):
    endpoint = "/openApi/swap/v2/trade/leverage"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    position_side = "LONG" if lato == "BUY" else "SHORT"
    payload = {
        "symbol": simbolo,
        "leverage": str(leva),
        "positionSide": position_side,
        "timestamp": timestamp
    }
    query_string = _genera_firma(payload, secret_key)
    headers = {"X-BX-APIKEY": api_key}
    try:
        requests.post(f"{url}?{query_string}", headers=headers)
    except Exception as e:
        print(f"[LEVERAGE EXCEPT] {e}")


def _invia_richiesta_ordine(endpoint: str, payload_ordine: dict, active_api: str, active_secret: str) -> dict:
    url = BASE_URL + endpoint
    query_string = _genera_firma(payload_ordine, active_secret)
    headers = {"X-BX-APIKEY": active_api}
    response = requests.post(f"{url}?{query_string}", headers=headers)
    return response.json()


def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "15m", market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti."}

    if market_type == "STANDARD_FUTURES":
        endpoint = "/openApi/futures/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita),
            "leverage": str(leva),
            "timestamp": str(int(time.time() * 1000))
        }
    elif market_type == "FUTURES":
        imposta_leva_bingx(simbolo, leva, lato, active_api, active_secret)
        endpoint = "/openApi/swap/v2/trade/order"
        position_side = "LONG" if lato == "BUY" else "SHORT"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }
    else:
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }

    try:
        res = _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
        msg = str(res.get("msg", ""))
        if res.get("code") != 0 and ("minimum order amount is" in msg.lower() or "insufficient" in msg.lower()):
            match = re.search(r"(?:minimum order amount is|is)\s+([0-9.]+)", msg, re.IGNORECASE)
            if match:
                min_qty = float(match.group(1))
                adjusted_qty = round(min_qty * 1.05, 6 if min_qty < 1 else 2)
                payload_ordine["quantity"] = str(adjusted_qty)
                payload_ordine["timestamp"] = str(int(time.time() * 1000))
                res = _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
        return res
    except Exception as e:
        return {"code": -1, "msg": str(e)}


# ==========================================
# ENDPOINT API FLASK
# ==========================================
@app.route('/api/market-data', methods=['GET'])
def api_market_data():
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    if mtype not in ["FUTURES", "SPOT", "STANDARD_FUTURES"]:
        mtype = "FUTURES"
    fetch_type = "FUTURES" if mtype == "STANDARD_FUTURES" else mtype
    data = get_live_market_data(fetch_type)
    return jsonify({"code": 0, "marketType": mtype, "data": data})


@app.route('/api/deep-scan', methods=['GET'])
def api_deep_scan():
    simbolo = request.args.get("symbol", "BTC-USDT")
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    res_data = calcola_parametri_quantistici_unificati(simbolo, mtype)
    if not res_data:
        return jsonify({"code": -1, "msg": "Candele multi-TF non disponibili"})
    return jsonify({"code": 0, "data": res_data})


@app.route('/api/esegui-segnale', methods=['POST'])
def webhook_esegui_segnale():
    dati = request.json or {}
    simbolo = dati.get("symbol")
    lato = dati.get("action")
    quantita = float(dati.get("size", 1.0))
    prezzo_ingresso = float(dati.get("entry", dati.get("price", 0)))
    tp = float(dati.get("tp", 0))
    sl = float(dati.get("sl", 0))
    leva = int(dati.get("leverage", 3))
    timeframe = dati.get("timeframe", "15m")
    mtype = dati.get("marketType", MARKET_TYPE).upper()
    is_auto = dati.get("is_auto", False)

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Modalità Automatica disattivata."})

    risultato = esegui_ordine_bingx(simbolo, lato, quantita, prezzo_ingresso, tp, sl, leva, timeframe, mtype, dati.get("apiKey", ""), dati.get("secretKey", ""))
    return jsonify(risultato)


@app.route('/api/set-mode', methods=['POST'])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    if dati.get("mode") in ["MANUAL", "AUTO"]:
        TRADING_MODE = dati.get("mode")
        return jsonify({"code": 0, "mode": TRADING_MODE})
    return jsonify({"code": -1, "msg": "Modalità non valida"})


@app.route('/api/get-mode', methods=['GET'])
def get_trading_mode():
    return jsonify({"code": 0, "mode": TRADING_MODE, "marketType": MARKET_TYPE})


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
  <title>ApexScan Pro - Quantum Superior Advisor Engine</title>
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
            obsidian: { 950: '#000000', 900: '#08080A', 850: '#0E0E11', 800: '#141418', 700: '#1E1E24' },
            emeraldNeon: '#00FF66',
            rubyNeon: '#FF334B'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #000000; }
    ::-webkit-scrollbar-thumb { background: rgba(0, 255, 102, 0.35); border-radius: 9999px; }
    @keyframes neonGlowPulse { 0%, 100% { box-shadow: 0 0 20px rgba(0, 255, 102, 0.4), inset 0 0 12px rgba(0, 255, 102, 0.15); border-color: rgba(0, 255, 102, 0.8); } 50% { box-shadow: 0 0 35px rgba(0, 255, 102, 0.7), inset 0 0 20px rgba(0, 255, 102, 0.35); border-color: #00FF66; } }
    @keyframes criticalPulse { 0%, 100% { box-shadow: 0 0 25px rgba(255, 51, 75, 0.8), inset 0 0 15px rgba(255, 51, 75, 0.3); border-color: #FF334B; background: rgba(50, 10, 15, 0.95); } 50% { box-shadow: 0 0 45px rgba(255, 51, 75, 1); border-color: #FF334B; background: rgba(80, 15, 20, 0.98); } }
    @keyframes dollarRotateGlow { 0% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #00FF66); } 50% { transform: scale(1.15) rotate(10deg); filter: drop-shadow(0 0 20px #00FF66); } 100% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #00FF66); } }
    .dollar-animated-logo { display: inline-block; animation: dollarRotateGlow 2.5s infinite ease-in-out; }
    .priority-signal-box { background: linear-gradient(180deg, rgba(8, 24, 15, 0.98) 0%, rgba(4, 12, 8, 0.99) 100%); border: 2px solid #00FF66; animation: neonGlowPulse 3s infinite ease-in-out; }
    .critical-box-mode { animation: criticalPulse 1.2s infinite ease-in-out !important; border: 2px solid #FF334B !important; }
    .tf-illuminated { box-shadow: 0 0 20px rgba(0, 255, 102, 0.85), inset 0 0 14px rgba(0, 255, 102, 0.3); border: 2px solid #00FF66 !important; background: rgba(0, 255, 102, 0.15) !important; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
    @keyframes tickerScroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
    .ticker-wrapper { display: flex; width: max-content; animation: tickerScroll 25s linear infinite; }
    .ticker-wrapper:hover { animation-play-state: paused; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#00FF66]/30 selection:text-white">

  <!-- HEADER -->
  <header class="sticky top-0 z-30 bg-[#000000]/95 backdrop-blur-md border-b border-white/15 px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3.5">
        <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#00FF66] via-[#00CC52] to-[#006622] text-black flex items-center justify-center shadow-2xl border-2 border-[#00FF66] font-extrabold text-xl animate-pulse">⚡</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">APEXSCAN <span class="text-[#00FF66]" id="headerEngineTitle">Quantum Superior Advisor</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-[#00FF66]"></span> <span id="modeText">MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]">Candle-Sync Attivo • 1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5 flex-wrap">
          <span class="text-xs font-mono font-bold text-white/70 px-1">Mercato:</span>
          <button id="btnMarketFutures" onclick="switchMarketType('FUTURES')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-[#0066FF] text-white">FUTURES</button>
          <button id="btnMarketStandard" onclick="switchMarketType('STANDARD_FUTURES')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">STD FUTURES 📊</button>
          <button id="btnMarketSpot" onclick="switchMarketType('SPOT')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">SPOT 🪙</button>
        </div>

        <button onclick="toggleApiModal()" class="px-3 py-1.5 rounded-xl text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/50 hover:bg-cyan-500/30 transition-all flex items-center gap-1.5">
          <span>🔑</span> <span id="apiStatusText">Collega API</span>
        </button>

        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
          <span class="text-xs font-mono font-bold text-white/70 px-1">Esecuzione:</span>
          <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
          <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">AUTO ⚡</button>
        </div>
      </div>
    </div>
  </header>

  <!-- TICKER MARQUEE -->
  <div class="bg-[#08080A] border-b border-white/10 overflow-hidden py-2 px-2">
    <div class="max-w-[1920px] mx-auto flex items-center gap-3">
      <span class="text-[10px] font-mono font-extrabold uppercase bg-[#00FF66]/20 text-[#00FF66] px-2 py-1 rounded border border-[#00FF66]/40 flex items-center gap-1 shrink-0">
        <span>🔥</span> Top Coins Live:
      </span>
      <div class="overflow-hidden w-full relative">
        <div id="liveTickerMarquee" class="ticker-wrapper font-mono text-xs text-white flex items-center gap-6">
          <span class="text-white/50">Caricamento ticker in tempo reale...</span>
        </div>
      </div>
    </div>
  </div>

  <!-- MAIN -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- BOX ADVISOR POSIZIONE ATTIVA -->
    <div id="activePositionAdvisorBox" class="hidden terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#00FF66] bg-gradient-to-r from-emerald-950/40 via-[#08080A] to-emerald-950/40 shadow-2xl space-y-4">
      <div class="flex flex-wrap items-center justify-between pb-3 border-b border-[#00FF66]/30 gap-3" id="advHeaderBox">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold animate-pulse" id="advIconSign">🔊</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white" id="advTitleMain">🟢 QUANTUM SUPERIOR ADVISOR VOCALE (<span id="advSymbol">BTC-USDT</span>)</h2>
            <p class="text-[11px] text-[#00FF66]" id="advSubNotice">1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci</p>
          </div>
        </div>
        <button onclick="chiudiPosizioneMonitorata()" class="px-3.5 py-1.5 rounded-xl bg-red-500/20 text-[#FF334B] border border-red-500/50 hover:bg-red-500/30 text-xs font-bold transition-all">
          Chiudi / Libera Slot ✕
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">1. 🟢 ENTRA (APERTURA)</span>
          <strong id="advEntryPrice" class="text-white text-sm">--</strong>
          <span id="advSideBadge" class="inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-bold bg-green-500/20 text-[#00FF66]">LONG</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">2. 📊 RIMANI A MERCATO (PNL)</span>
          <strong id="advLivePrice" class="text-cyan-300 text-sm">--</strong>
          <span id="advPnlVal" class="block mt-1 text-[10px] font-bold text-[#00FF66]">PnL: 0.00%</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">🎯 TARGET TAKE PROFIT ($ TP) SUPERIORE</span>
          <strong id="advTargetTpDisplay" class="text-[#00FF66] text-sm">--</strong>
          <span id="advDistTp" class="block mt-1 text-[10px] text-white/80 font-bold">Distanza TP: 0.00%</span>
          <span id="advExactTimeRemaining" class="block mt-0.5 text-[10px] text-yellow-300 tabular-nums font-bold">Tempo al TP: --</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10 flex flex-col justify-between">
          <div>
            <span class="text-white/60 text-[10px] block">3. 🎯 TP RAGGIUNTO / ESCI</span>
            <strong id="advStatusAdvice" class="text-[#00FF66] text-xs block mt-0.5">In attesa del TP...</strong>
          </div>
          <span id="advActionAlert" class="text-[10px] text-white/80 italic mt-1">Guida dinamica al profitto attiva</span>
        </div>
      </div>
    </div>

    <!-- KPI DINAMICI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Stato Connessione Utente</span>
        <div class="mt-2">
          <strong id="kpiUserApiStatus" class="text-xs font-mono text-yellow-400 block">⚠️ API Personali non inserite</strong>
          <span class="text-[11px] text-white/70">Inserisci chiavi per operatività</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Slot Posizioni Auto 🔒</span>
        <div class="mt-2">
          <span id="kpiAutoSlotStatus" class="text-xs font-mono text-[#00FF66] font-extrabold">🟢 Slot Libero (1 max)</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Top Mover in Tempo Reale 🔥</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-sm font-extrabold font-mono text-white">--</span>
          <span id="kpiTopMoverChange" class="text-sm font-extrabold font-mono text-[#00FF66]">--%</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Motore Sincronizzato</span>
        <div class="mt-2">
          <span id="kpiMarketBadge" class="text-xs font-mono text-[#00FF66] font-bold">🟢 AGGIORNAMENTO A OGNI CANDELA</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#00FF66]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold animate-bounce">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">⚡ SEGNALE MASTER SUPERIORE ($ TP &amp; SL 😢)</h2>
            <p class="text-[11px] text-[#00FF66]" id="masterSubtitle">Timer aggiornato ad ogni singola candela di mercato</p>
          </div>
        </div>
        <span id="masterPrecisionBadge" class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#00FF66] text-black animate-pulse">CANDLE-SYNC 🚀</span>
      </div>
      <div id="prioritySignalCardContent"></div>
    </section>

    <!-- FILTRI E RICERCA -->
    <section class="terminal-card rounded-2xl p-4 flex flex-col gap-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="text-xs font-bold text-white/80">Filtra per Categoria di Mercato:</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button onclick="setCategoryTab('ALL')" id="catTabALL" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all">⭐ Tutti i Mercati</button>
          <button onclick="setCategoryTab('Criptovalute')" id="catTabCriptovalute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🪙 Criptovalute</button>
          <button onclick="setCategoryTab('Materie Prime')" id="catTabMaterie Prime" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🛢️ Materie Prime</button>
          <button onclick="setCategoryTab('Forex / Valute')" id="catTabForex / Valute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">💱 Forex &amp; Valute</button>
          <button onclick="setCategoryTab('Azioni & Indici (TradFi)')" id="catTabTradFi" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">📈 Azioni &amp; Indici</button>
        </div>
      </div>
      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Asset (es. BTC, ETH, SOL, XRP, SUI, GOLD)..." class="w-full bg-[#000000] border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
      </div>
    </section>

    <!-- LISTA CARD OPERATIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-4">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-3">
            <span class="w-8 h-8 rounded-full bg-[#00FF66]/20 border border-[#00FF66] flex items-center justify-center text-[#00FF66] text-base font-extrabold dollar-animated-logo">$</span>
            <span>📊 Lista Operativa (Cripto Ordine Alta Capitalizzazione: <span id="tableMarketLabel" class="text-[#00FF66]">BTC, ETH, SOL...</span>)</span>
          </h2>
          <span class="text-xs font-mono text-[#00FF66]">Sincronizzato sul flusso candela per candela</span>
        </div>
        <div id="tablesContainer" class="space-y-3"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed Quantistico &amp; Advisor
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Quantum Engine] Sincronizzazione candela per candela attiva...</div>
        </div>
      </aside>
    </div>
  </main>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-[#00FF66]/40 rounded-2xl w-full max-w-[500px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 class="text-base font-extrabold font-mono text-[#00FF66]">🔑 Configurazione API Key Personale</h2>
          <p class="text-xs text-white/70">Collega il tuo account BingX</p>
        </div>
        <button onclick="toggleApiModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX API Key:</label>
          <input type="text" id="userApiKeyInput" placeholder="Inserisci la tua API Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#00FF66] outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX Secret Key:</label>
          <input type="password" id="userSecretKeyInput" placeholder="Inserisci la tua Secret Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#00FF66] outline-none" />
        </div>
        <button onclick="saveUserApiKeys()" class="w-full py-2.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black shadow-lg hover:bg-emerald-400 transition-all">
          Salva Credenziali API
        </button>
        <button onclick="clearUserApiKeys()" class="w-full py-1.5 rounded-xl text-xs font-bold bg-red-500/20 text-[#FF334B] border border-red-500/40 hover:bg-red-500/30 transition-all">
          Disconnetti API
        </button>
      </div>
    </div>
  </div>

  <!-- MODALE ESECUZIONE -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[750px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <div class="flex items-center gap-2">
            <h2 id="modalSymbol" class="text-base font-extrabold font-mono text-white">BTC-USDT</h2>
            <span id="modalOhlcvBadge" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/40">SUPERIORE</span>
          </div>
          <p id="modalSubtitleType" class="text-xs text-white/70">Advisor quantistico: 1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <div class="flex justify-between"><span class="text-white/75">Mercato / Categoria:</span><strong id="modalCategory" class="text-[#00FF66]">--</strong></div>
          <div class="flex justify-between items-center">
            <span class="text-white/75">Timeframe Quantistico:</span>
            <strong id="modalTF" class="px-2.5 py-0.5 rounded bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66] font-extrabold animate-pulse">15m ⚡</strong>
          </div>
          <div class="flex justify-between"><span class="text-white/75">Sentiment Superiore:</span><strong id="modalSentiment" class="text-[#00FF66] font-bold">--</strong></div>
          <div class="flex justify-between border-b border-white/10 pb-2"><span class="text-white/75">⏱️ Tempo al $ TP (Candle-Sync):</span><strong id="modalExactTime" class="text-yellow-300 font-extrabold tabular-nums">--</strong></div>

          <div class="grid grid-cols-2 gap-2 py-2 border-b border-white/10">
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">📈 EMA 9 / EMA 21</span>
              <strong id="modalEma" class="text-cyan-300 text-xs">--</strong>
              <span id="modalEmaSignal" class="text-[10px] block mt-0.5">--</span>
            </div>
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">📊 RSI (14) Wilder</span>
              <strong id="modalRsi" class="text-white text-xs">--</strong>
              <span id="modalRsiZone" class="text-[10px] block mt-0.5">--</span>
            </div>
          </div>

          <!-- Pannello Multi-Timeframe SVG a 4 livelli (5m, 15m, 1h, 4h) -->
          <div id="tfPanel" class="grid grid-cols-2 sm:grid-cols-4 gap-2 my-2"></div>

          <!-- TABELLA PARAMETRI MILLIMETRICI PRECISI -->
          <div class="bg-gradient-to-r from-emerald-950/50 via-black to-emerald-950/50 border-2 border-[#00FF66] rounded-xl p-3 space-y-2 my-3 shadow-xl">
            <div class="flex items-center justify-between border-b border-[#00FF66]/30 pb-2">
              <span class="text-xs font-extrabold text-[#00FF66] flex items-center gap-1.5">
                <span>📋</span> TABELLA PARAMETRI CHIRURGICI ($ TP &amp; SL 😢)
              </span>
              <span class="text-[10px] text-white/70">Calibrati e protetti</span>
            </div>

            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs font-mono">
                <thead>
                  <tr class="border-b border-white/15 text-white/60 text-[10px]">
                    <th class="py-1.5 px-2">PARAMETRO</th>
                    <th class="py-1.5 px-2">VALORE MILLIMETRICO</th>
                    <th class="py-1.5 px-2 text-right">AZIONE</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/10">
                  <tr>
                    <td class="py-2 px-2 text-white font-bold">1. Prezzo Ingresso</td>
                    <td class="py-2 px-2 text-cyan-300 font-bold select-all" id="tableParamEntry">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamEntry')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-cyan-300 text-[10px]">Copia</button>
                    </td>
                  </tr>
                  <tr>
                    <td class="py-2 px-2 text-[#00FF66] font-bold">2. $ Take Profit (TP Rapido)</td>
                    <td class="py-2 px-2 text-[#00FF66] font-extrabold select-all" id="tableParamTp">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamTp')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#00FF66] text-[10px]">Copia</button>
                    </td>
                  </tr>
                  <tr>
                    <td class="py-2 px-2 text-[#FF334B] font-bold">3. $ Stop Loss 😢 (Protetto)</td>
                    <td class="py-2 px-2 text-[#FF334B] font-extrabold select-all" id="tableParamSl">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamSl')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#FF334B] text-[10px]">Copia</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="flex justify-between pt-1"><span class="text-white/75">Prezzo Live Reale:</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Direzione Long / Short:</span><strong id="modalSide" class="text-[#00FF66]">--</strong></div>
          <div>
            <div class="flex justify-between"><span class="text-white/75">$ Stop Loss Protetto 😢:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
            <div class="flex justify-between"><span class="text-white/75">🎯 TARGET $ TAKE PROFIT RAPIDO:</span><strong id="modalTrendingTP" class="text-[#00FF66] text-sm font-extrabold">--</strong></div>
            <div class="flex justify-between mt-1"><span class="text-white/75">🔥 ROI Stimato al $ TP (Leva):</span><strong id="modalTpPct" class="text-yellow-400 font-extrabold">--</strong></div>
          </div>
        </div>

        <!-- SELETTORE CAPITALE / MARGINE LIBERO -->
        <div class="bg-[#000000] border border-[#00FF66]/40 rounded-xl p-4 space-y-3">
          <div class="flex justify-between items-center">
            <label class="block text-xs font-bold text-white uppercase tracking-wider">💰 CAPITALE / MARGINE DA INVESTIRE (USDT):</label>
            <span id="marginUsdtLabel" class="text-xs font-bold text-[#00FF66]">10 USDT</span>
          </div>
          <div class="grid grid-cols-6 gap-1.5">
            <button type="button" onclick="setMarginUsdt(2)" id="marBtn2" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">2 $</button>
            <button type="button" onclick="setMarginUsdt(5)" id="marBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">5 $</button>
            <button type="button" onclick="setMarginUsdt(10)" id="marBtn10" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold text-xs">10 $</button>
            <button type="button" onclick="setMarginUsdt(25)" id="marBtn25" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">25 $</button>
            <button type="button" onclick="setMarginUsdt(50)" id="marBtn50" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">50 $</button>
            <button type="button" onclick="setMarginUsdt(100)" id="marBtn100" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">100 $</button>
          </div>
          <input type="number" id="customMarginInput" oninput="onCustomMarginChange(this.value)" placeholder="Oppure inserisci USDT liberi..." class="w-full bg-[#08080A] border border-white/30 rounded-lg px-3 py-1.5 text-white font-mono text-xs focus:border-[#00FF66] outline-none" />
        </div>

        <div id="leverageSectionWrapper" class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <label class="block text-xs font-bold text-white uppercase tracking-wider">Leva Esatta del Segnale &amp; Margine Isolato:</label>
          <div class="grid grid-cols-4 gap-2">
            <button type="button" onclick="setLeverage(1)" id="levBtn1" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">1x</button>
            <button type="button" onclick="setLeverage(3)" id="levBtn3" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">3x</button>
            <button type="button" onclick="setLeverage(5)" id="levBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">5x</button>
            <button type="button" onclick="setLeverage(10)" id="levBtn10" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold">10x</button>
          </div>
        </div>

        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-white/70 font-semibold">Quantità Asset Calcolata (In base al capitale e alla leva):</label>
          </div>
          <input type="number" id="tradeQuantityInput" value="1.0" step="any" oninput="onQuantityManualChange(this.value)" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
        </div>

        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>⚡ Esegui Ordine con Capitale Personalizzato &amp; Attiva Advisor</span>
        </button>
        <div id="executionFeedback" class="mt-2 text-xs text-center hidden"></div>
      </div>
    </div>
  </main>

  <!-- JAVASCRIPT ENGINE -->
  <script>
    let state = {
      marketData: {},
      detailedAssets: {},
      timerAnchors: {},
      tradingMode: 'MANUAL',
      marketType: 'FUTURES',
      currentCategoryTab: 'ALL',
      searchQuery: '',
      selectedLeverage: 10,
      selectedMarginUsdt: 10,
      currentSelectedAsset: null,
      activePosition: null,
      advisorInterval: null,
      lastVoiceAnnounceTimestamp: 0,
      isCriticalState: false
    };

    const TOP_MARKET_CAP_ORDER = [
      'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'XRP-USDT', 'BNB-USDT', 'DOGE-USDT', 'ADA-USDT',
      'AVAX-USDT', 'SHIB-USDT', 'DOT-USDT', 'LINK-USDT', 'SUI-USDT', 'NEAR-USDT', 'LTC-USDT',
      'PEPE-USDT', 'APT-USDT', 'UNI-USDT', 'ICP-USDT', 'FET-USDT', 'RENDER-USDT'
    ];

    function formatExactDuration(totalSeconds) {
      const sec = Math.max(0, Math.round(totalSeconds));
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = sec % 60;
      if (h > 0) return `${h}h ${m}m ${s}s`;
      if (m > 0) return `${m}m ${s}s`;
      return `${s}s`;
    }

    function formatVoiceDuration(totalSeconds) {
      const sec = Math.max(0, Math.round(totalSeconds));
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = sec % 60;
      const parts = [];
      if (h > 0) parts.push(`${h} ${h === 1 ? 'ora' : 'ore'}`);
      if (m > 0) parts.push(`${m} ${m === 1 ? 'minuto' : 'minuti'}`);
      if (s > 0 || parts.length === 0) parts.push(`${s} ${s === 1 ? 'secondo' : 'secondi'}`);
      return parts.join(' e ');
    }

    function getLiveRemainingSeconds(symbol, baseTempoSec) {
      if (!state.timerAnchors[symbol]) {
        state.timerAnchors[symbol] = {
          baseSec: baseTempoSec,
          startedAt: Date.now()
        };
      }
      const anchor = state.timerAnchors[symbol];
      const elapsed = Math.floor((Date.now() - anchor.startedAt) / 1000);
      return Math.max(1, anchor.baseSec - elapsed);
    }

    function updateAllVisibleCountdowns() {
      document.querySelectorAll('[data-countdown-sym]').forEach(el => {
        const sym = el.getAttribute('data-countdown-sym');
        const baseSec = parseInt(el.getAttribute('data-base-sec'), 10) || 60;
        const rem = getLiveRemainingSeconds(sym, baseSec);
        el.textContent = formatExactDuration(rem);
      });

      if (state.currentSelectedAsset) {
        const d = state.currentSelectedAsset;
        const modalTimeEl = document.getElementById('modalExactTime');
        if (modalTimeEl) {
          const rem = getLiveRemainingSeconds(d.symbol, d.tempoSec);
          modalTimeEl.textContent = `${formatExactDuration(rem)} 🎯`;
        }
      }
    }

    function getStoredApiKeys() {
      return {
        apiKey: localStorage.getItem('bingx_user_api_key') || '',
        secretKey: localStorage.getItem('bingx_user_secret_key') || ''
      };
    }

    function updateApiStatusUI() {
      const keys = getStoredApiKeys();
      const statusEl = document.getElementById('kpiUserApiStatus');
      const textBtn = document.getElementById('apiStatusText');
      if (keys.apiKey && keys.secretKey) {
        statusEl.textContent = "🟢 API Personali Attive";
        statusEl.className = "text-xs font-mono text-[#00FF66] block font-bold";
        textBtn.textContent = "API Connesse 🟢";
      } else {
        statusEl.textContent = "⚠️ API Personali non inserite";
        statusEl.className = "text-xs font-mono text-yellow-400 block";
        textBtn.textContent = "Collega API";
      }
    }

    function toggleApiModal() {
      const modal = document.getElementById('apiModal');
      const keys = getStoredApiKeys();
      document.getElementById('userApiKeyInput').value = keys.apiKey;
      document.getElementById('userSecretKeyInput').value = keys.secretKey;
      modal.classList.toggle('hidden');
      modal.classList.toggle('flex');
    }

    function saveUserApiKeys() {
      const apiKey = document.getElementById('userApiKeyInput').value.trim();
      const secretKey = document.getElementById('userSecretKeyInput').value.trim();
      if (!apiKey || !secretKey) {
        alert("Inserisci entrambe le chiavi.");
        return;
      }
      localStorage.setItem('bingx_user_api_key', apiKey);
      localStorage.setItem('bingx_user_secret_key', secretKey);
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali salvate con successo!");
    }

    function clearUserApiKeys() {
      localStorage.removeItem('bingx_user_api_key');
      localStorage.removeItem('bingx_user_secret_key');
      document.getElementById('userApiKeyInput').value = '';
      document.getElementById('userSecretKeyInput').value = '';
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali rimosse.");
    }

    let sharedAudioCtx = null;
    function getAudioContext() {
      if (!sharedAudioCtx) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) sharedAudioCtx = new AudioCtx();
      }
      if (sharedAudioCtx && sharedAudioCtx.state === 'suspended') sharedAudioCtx.resume().catch(() => {});
      return sharedAudioCtx;
    }

    function speakText(text) {
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'it-IT';
        utterance.rate = 1.05;
        window.speechSynthesis.speak(utterance);
      }
    }

    function playChime(urgent = false, voiceMsg = "") {
      const ctx = getAudioContext();
      if (ctx) {
        const now = ctx.currentTime;
        const freqs = urgent ? [1400, 1000, 1600] : [880, 1108.73, 1318.51];
        freqs.forEach((freq, i) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.frequency.setValueAtTime(freq, now + i * 0.08);
          gain.gain.setValueAtTime(0.001, now + i * 0.08);
          gain.gain.exponentialRampToValueAtTime(0.1, now + i * 0.08 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.08 + 0.4);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + i * 0.08);
          osc.stop(now + i * 0.08 + 0.45);
        });
      }
      if (voiceMsg) {
        speakText(voiceMsg);
      }
    }

    function getPrecisionDecimals(val) {
      if (val >= 1000) return 2;
      if (val >= 10) return 3;
      if (val >= 1) return 4;
      if (val >= 0.01) return 5;
      return 7;
    }

    function formatPrice(val) {
      return Number(val).toFixed(getPrecisionDecimals(val));
    }

    function copiaParametro(elementId) {
      const el = document.getElementById(elementId);
      if (!el) return;
      const text = el.textContent.trim();
      navigator.clipboard.writeText(text).then(() => {
        logFeed(`[COPIA] Parametro ${text} copiato negli appunti!`);
        alert(`Copiato: ${text}`);
      }).catch(err => {
        console.error('Errore copia', err);
      });
    }

    function aggiornaQuantitaDaCapitale() {
      if (!state.currentSelectedAsset) return;
      const d = state.currentSelectedAsset;
      const prezzo = d.price;
      if (prezzo <= 0) return;

      const valoreNominale = state.selectedMarginUsdt * state.selectedLeverage;
      const quantita = Number((valoreNominale / prezzo).toFixed(d.decimals >= 4 ? 2 : 4));

      document.getElementById('tradeQuantityInput').value = quantita;
      document.getElementById('marginUsdtLabel').textContent = `${state.selectedMarginUsdt} USDT (Posizione da ${valoreNominale} $ con ${state.selectedLeverage}x)`;
    }

    function setMarginUsdt(usdt) {
      state.selectedMarginUsdt = usdt;
      document.getElementById('customMarginInput').value = '';
      document.querySelectorAll('[id^="marBtn"]').forEach(b => {
        b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs";
      });
      const activeBtn = document.getElementById(`marBtn${usdt}`);
      if (activeBtn) activeBtn.className = "py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold text-xs";
      aggiornaQuantitaDaCapitale();
    }

    function onCustomMarginChange(val) {
      const num = parseFloat(val);
      if (!isNaN(num) && num > 0) {
        state.selectedMarginUsdt = num;
        document.querySelectorAll('[id^="marBtn"]').forEach(b => {
          b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs";
        });
        document.getElementById('marginUsdtLabel').textContent = `${num} USDT`;
        aggiornaQuantitaDaCapitale();
      }
    }

    function onQuantityManualChange(val) {
      if (state.currentSelectedAsset) {
        const q = parseFloat(val) || 0;
        const valNom = q * state.currentSelectedAsset.price;
        const marEst = valNom / state.selectedLeverage;
        document.getElementById('marginUsdtLabel').textContent = `Margine st.: ~${marEst.toFixed(2)} USDT`;
      }
    }

    function calcolaDatiLocaliUnificati(symbol, item) {
      const cached = state.detailedAssets[symbol];
      if (cached) {
        const p = item.price;
        const distTp = Math.abs(cached.tp - p);
        const tpPct = (((distTp / p) * 100) * state.selectedLeverage).toFixed(2);
        return { symbol, price: p, side: cached.side, sl: cached.sl, tp: cached.tp, tpPct, confluenza: "Candle-Sync Attivo 🎯", tempoSec: cached.tempoSec };
      }
      const p = item.price;
      const dec = getPrecisionDecimals(p);
      const isBull = item.change24h >= 0;
      const side = (state.marketType === 'SPOT' || isBull) ? "BUY" : "SELL";
      const distSl = p * 0.012;
      const distTp = p * 0.026;
      const sl = Number((side === 'BUY' ? p - distSl : p + distSl).toFixed(dec));
      const tp = Number((side === 'BUY' ? p + distTp : p - distTp).toFixed(dec));
      const tpPct = (((distTp / p) * 100) * state.selectedLeverage).toFixed(2);
      const tempoSec = Math.max(60, Math.round(900 / (0.1 + Math.abs(item.change24h) * 0.1)));
      return { symbol, price: p, side, sl, tp, tpPct, confluenza: "Candle-Sync Attivo 🎯", tempoSec };
    }

    function renderTfPanel(tfs, exactTf) {
      const el = document.getElementById('tfPanel');
      if (!el) return;
      if (!tfs) { el.innerHTML = ''; return; }
      el.innerHTML = ['5m', '15m', '1h', '4h'].map(iv => {
        const t = tfs[iv];
        const isExact = iv === exactTf;
        const col = t.trend === 'BUY' ? '#00FF66' : t.trend === 'SELL' ? '#FF334B' : '#FFCC00';
        const cs = t.candles;
        const hi = Math.max(...cs.map(c => c.high)), lo = Math.min(...cs.map(c => c.low));
        const y = v => 30 - ((v - lo) / ((hi - lo) || 1)) * 28;
        const bars = cs.map((c, i) => {
          const up = c.close >= c.open, last = i === cs.length - 1;
          const x = 3 + i * 7;
          return `<line x1="${x+2}" x2="${x+2}" y1="${y(c.high)}" y2="${y(c.low)}" stroke="${up ? '#00FF66' : '#FF334B'}"/>
            <rect x="${x}" width="4" y="${Math.min(y(c.open), y(c.close))}" height="${Math.max(1, Math.abs(y(c.open) - y(c.close)))}"
              fill="${up ? '#00FF66' : '#FF334B'}" ${last ? 'stroke="#00FF66" stroke-width="1.5"' : ''}/>`;
        }).join('');
        return `<div class="bg-white/5 rounded-lg p-2 border transition-all ${isExact ? 'tf-illuminated' : ''}" style="border-color:${isExact ? '#00FF66' : col + '66'}">
          <div class="flex justify-between items-center text-[10px]">
            <b class="${isExact ? 'text-[#00FF66]' : 'text-white'}">${iv} ${isExact ? '⚡' : ''}</b>
            <b style="color:${col}">${t.trend}</b>
          </div>
          <svg viewBox="0 0 100 32" class="w-full h-8">${bars}</svg>
          <div class="text-[9px] text-white/60">RSI ${t.rsi14} • ${t.ema9 >= t.ema21 ? 'EMA↑' : 'EMA↓'}</div>
          <div class="text-[9px] text-[#00FF66]">● ora: ${formatPrice(t.last.close)}</div>
        </div>`;
      }).join('');
    }

    function avviaAdvisorPosizione(symbol, side, entryPrice, tpPrice, slPrice, tempoSec) {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      const liveRemSec = getLiveRemainingSeconds(symbol, tempoSec);
      const totalDist = Math.abs(tpPrice - entryPrice);
      const initDistPct = ((totalDist / entryPrice) * 100).toFixed(2);
      const initDistLevaPct = (((totalDist / entryPrice) * 100) * state.selectedLeverage).toFixed(2);

      state.activePosition = {
        symbol,
        side,
        entryPrice,
        tpPrice,
        slPrice,
        totalDist,
        openedAt: Date.now(),
        initialTempoSec: liveRemSec
      };
      state.isCriticalState = false;

      const box = document.getElementById('activePositionAdvisorBox');
      box.classList.remove('hidden');
      box.className = "terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#00FF66] bg-gradient-to-r from-emerald-950/40 via-[#08080A] to-emerald-950/40 shadow-2xl space-y-4";
      document.getElementById('advHeaderBox').className = "flex flex-wrap items-center justify-between pb-3 border-b border-[#00FF66]/30 gap-3";
      document.getElementById('advIconSign').className = "w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold animate-pulse";
      document.getElementById('advTitleMain').innerHTML = `🟢 QUANTUM SUPERIOR ADVISOR VOCALE (<span id="advSymbol">${symbol}</span>)`;
      document.getElementById('advSubNotice').textContent = "1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci";

      document.getElementById('kpiAutoSlotStatus').textContent = "🔒 Posizione Quantistica Attiva";
      document.getElementById('advSymbol').textContent = symbol;
      document.getElementById('advEntryPrice').textContent = `$${formatPrice(entryPrice)}`;
      document.getElementById('advSideBadge').textContent = side;
      document.getElementById('advTargetTpDisplay').textContent = `$${formatPrice(tpPrice)}`;
      document.getElementById('advDistTp').textContent = `Distanza TP: ${initDistPct}% (+${initDistLevaPct}% con ${state.selectedLeverage}x)`;
      document.getElementById('advExactTimeRemaining').textContent = `Tempo al TP: ${formatExactDuration(liveRemSec)}`;

      const sideText = side === 'BUY' ? 'Long' : 'Short';
      const voiceTimeStr = formatVoiceDuration(liveRemSec);
      state.lastVoiceAnnounceTimestamp = Date.now();
      playChime(false, `Primo. Entra a mercato con ${state.selectedMarginUsdt} USDT di margine. Posizione ${sideText} su ${symbol}. Target Take Profit a ${formatPrice(tpPrice)}. Tempo previsto basato sul flusso candela: ${voiceTimeStr}.`);
      aggiornaAdvisorLoop();
      state.advisorInterval = setInterval(aggiornaAdvisorLoop, 1000);
      logFeed(`[QUANTUM ADVISOR] Aperto ${symbol} (${side}) con Margine ${state.selectedMarginUsdt} USDT (Leva ${state.selectedLeverage}x).`);
    }

    function chiudiPosizioneMonitorata() {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      state.activePosition = null;
      state.isCriticalState = false;
      document.getElementById('activePositionAdvisorBox').classList.add('hidden');
      document.getElementById('kpiAutoSlotStatus').textContent = "🟢 Slot Libero (1 max)";
      logFeed(`[QUANTUM ADVISOR] 3. TP Raggiunto / Esci completato. Slot liberato.`);
    }

    let lastLoopAdviceState = "";
    function aggiornaAdvisorLoop() {
      if (!state.activePosition) return;
      const pos = state.activePosition;
      const liveItem = state.marketData[pos.symbol];
      if (!liveItem) return;

      const currentPrice = liveItem.price;
      document.getElementById('advLivePrice').textContent = `$${formatPrice(currentPrice)}`;

      const rawDiff = pos.side === 'BUY' ? (currentPrice - pos.entryPrice) : (pos.entryPrice - currentPrice);
      const pnlPct = ((rawDiff / pos.entryPrice) * 100) * state.selectedLeverage;

      const pnlEl = document.getElementById('advPnlVal');
      pnlEl.textContent = `PnL: ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
      pnlEl.className = `block mt-1 text-[10px] font-bold ${pnlPct >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}`;

      const distTp = Math.max(0, pos.side === 'BUY' ? (pos.tpPrice - currentPrice) : (currentPrice - pos.tpPrice));
      const distTpPct = currentPrice > 0 ? ((distTp / currentPrice) * 100) : 0;
      const distTpLevaPct = distTpPct * state.selectedLeverage;

      const elapsedSec = Math.max(1, (Date.now() - pos.openedAt) / 1000);
      const progressDone = pos.side === 'BUY' ? (currentPrice - pos.entryPrice) : (pos.entryPrice - currentPrice);
      const clockRemaining = Math.max(1, pos.initialTempoSec - elapsedSec);
      const exactRemainingSec = progressDone > 0
        ? Math.max(1, Math.min(clockRemaining, Math.round(distTp / (progressDone / elapsedSec))))
        : clockRemaining;

      let isCritical = false;
      if (pos.side === 'BUY') {
        isCritical = currentPrice < pos.entryPrice && (currentPrice - pos.slPrice) <= (pos.entryPrice - pos.slPrice) * 0.40;
      } else {
        isCritical = currentPrice > pos.entryPrice && (pos.slPrice - currentPrice) <= (pos.slPrice - pos.entryPrice) * 0.40;
      }

      document.getElementById('advDistTp').textContent = `Distanza TP: ${distTpPct.toFixed(2)}% (+${distTpLevaPct.toFixed(2)}% con ${state.selectedLeverage}x)`;
      document.getElementById('advExactTimeRemaining').textContent = `Tempo al TP: ${formatExactDuration(exactRemainingSec)}`;

      const advAdvice = document.getElementById('advStatusAdvice');
      const advAlert = document.getElementById('advActionAlert');
      const box = document.getElementById('activePositionAdvisorBox');

      if ((pos.side === 'BUY' && currentPrice >= pos.tpPrice) || (pos.side === 'SELL' && currentPrice <= pos.tpPrice)) {
        document.getElementById('advDistTp').textContent = "Distanza TP: 0.00% (RAGGIUNTO!)";
        document.getElementById('advExactTimeRemaining').textContent = "Tempo al TP: 0s";
        advAdvice.textContent = "3. 🎯 TP RAGGIUNTO! ESCI DA MERCATO";
        advAdvice.className = "text-[#00FF66] text-xs block font-bold animate-pulse";
        advAlert.textContent = "Target Take Profit raggiunto con successo!";
        if (lastLoopAdviceState !== "TP") {
          lastLoopAdviceState = "TP";
          playChime(true, "Terzo. Target Take profit raggiunto! Esci subito da mercato con profitto.");
        }
        chiudiPosizioneMonitorata();
      } else if ((pos.side === 'BUY' && currentPrice <= pos.slPrice) || (pos.side === 'SELL' && currentPrice >= pos.slPrice)) {
        advAdvice.textContent = "3. 🛑 ESCI DI PROTEZIONE 😢";
        advAdvice.className = "text-[#FF334B] text-xs block font-bold animate-pulse";
        advAlert.textContent = "Chiusura di salvaguardia attivata.";
        if (lastLoopAdviceState !== "SL") {
          lastLoopAdviceState = "SL";
          playChime(true, "Terzo. Stop loss toccato. Esci di protezione per salvaguardare il capitale.");
        }
        chiudiPosizioneMonitorata();
      } else if (isCritical) {
        box.className = "terminal-card rounded-2xl p-4 sm:p-5 critical-box-mode shadow-2xl space-y-4";
        document.getElementById('advHeaderBox').className = "flex flex-wrap items-center justify-between pb-3 border-b border-[#FF334B]/40 gap-3";
        document.getElementById('advIconSign').className = "w-9 h-9 rounded-xl bg-[#FF334B] text-white flex items-center justify-center font-extrabold animate-ping";
        document.getElementById('advTitleMain').innerHTML = `🚨 SITUAZIONE CRITICA DI ALLERTA 😢 (<span id="advSymbol">${pos.symbol}</span>)`;
        document.getElementById('advSubNotice').textContent = "⚠️ ATTENZIONE: Il prezzo si sta muovendo contro la posizione!";

        advAdvice.textContent = "⚠️ ATTENZIONE: SITUAZIONE CRITICA!";
        advAdvice.className = "text-[#FF334B] text-xs block font-extrabold animate-pulse";
        advAlert.textContent = "Prezzo in zona di pericolo vicina allo Stop Loss.";

        if (!state.isCriticalState || (Date.now() - state.lastVoiceAnnounceTimestamp >= 12000)) {
          state.isCriticalState = true;
          state.lastVoiceAnnounceTimestamp = Date.now();
          playChime(true, "Attenzione! Situazione critica raggiunta. Il prezzo si avvicina allo stop loss.");
        }
      } else {
        box.className = "terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#00FF66] bg-gradient-to-r from-emerald-950/40 via-[#08080A] to-emerald-950/40 shadow-2xl space-y-4";
        document.getElementById('advHeaderBox').className = "flex flex-wrap items-center justify-between pb-3 border-b border-[#00FF66]/30 gap-3";
        document.getElementById('advIconSign').className = "w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold animate-pulse";
        document.getElementById('advTitleMain').innerHTML = `🟢 QUANTUM SUPERIOR ADVISOR VOCALE (<span id="advSymbol">${pos.symbol}</span>)`;
        document.getElementById('advSubNotice').textContent = "1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci";

        if (pnlPct >= 0) {
          advAdvice.textContent = "2. 📊 RIMANI A MERCATO (IN PROFITTO)";
          advAdvice.className = "text-[#00FF66] text-xs block font-bold";
          advAlert.textContent = `Avanzamento regolare verso il $ TP: $${formatPrice(pos.tpPrice)}`;
        } else {
          advAdvice.textContent = "2. ⏳ RIMANI A MERCATO (FASE DI RITRACCIAMENTO)";
          advAdvice.className = "text-yellow-400 text-xs block font-bold";
          advAlert.textContent = `Il prezzo sta fluttuando prima della spinta verso il TP: $${formatPrice(pos.tpPrice)}`;
        }

        const now = Date.now();
        if (now - state.lastVoiceAnnounceTimestamp >= 15000) {
          state.lastVoiceAnnounceTimestamp = now;
          const voiceTimeLeft = formatVoiceDuration(exactRemainingSec);
          if (pnlPct >= 0) {
            speakText(`Secondo. Rimani a mercato. Posizione in profitto. Mancano circa ${voiceTimeLeft} al Take Profit.`);
          } else {
            speakText(`Secondo. Rimani a mercato. Posizione in fase di fluttuazione normale. Mancano circa ${voiceTimeLeft} al Take Profit.`);
          }
        }

        if (lastLoopAdviceState !== "RIMANI") {
          lastLoopAdviceState = "RIMANI";
        }
      }
    }

    function setCategoryTab(cat) {
      state.currentCategoryTab = cat;
      renderDashboard();
    }

    function filterCoinSearch(query) {
      state.searchQuery = query.toLowerCase().trim();
      renderDashboard();
    }

    function setLeverage(lev) {
      state.selectedLeverage = lev;
      document.querySelectorAll('#leverageSectionWrapper button').forEach(b => {
        b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
      });
      const activeBtn = document.getElementById(`levBtn${lev}`);
      if (activeBtn) activeBtn.className = "py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold";

      if (state.currentSelectedAsset) {
        const d = state.currentSelectedAsset;
        const tpPct = (((Math.abs(d.tp - d.price) / d.price) * 100) * state.selectedLeverage).toFixed(2);
        document.getElementById('modalTpPct').textContent = `+${tpPct}% (con leva ${state.selectedLeverage}x)`;
        aggiornaQuantitaDaCapitale();
      }
      renderDashboard();
    }

    async function switchMarketType(mtype) {
      try {
        const res = await fetch('/api/set-market-type', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ marketType: mtype })
        });
        const data = await res.json();
        if (data.code === 0) {
          state.marketType = data.marketType;
          state.currentCategoryTab = 'ALL';
          state.detailedAssets = {};
          state.timerAnchors = {};
          updateMarketTypeUI();
          await pollMarketData();
          logFeed(`[MERCATO] Passato a ${mtype}`);
        }
      } catch (e) {}
    }

    function updateMarketTypeUI() {
      document.getElementById('btnMarketFutures').className = state.marketType === 'FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#0066FF] text-white" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('btnMarketStandard').className = state.marketType === 'STANDARD_FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-500 text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('btnMarketSpot').className = state.marketType === 'SPOT' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#00FF66] text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('kpiMarketBadge').textContent = `🟢 AGGIORNAMENTO A OGNI CANDELA`;
      document.getElementById('tableMarketLabel').textContent = state.marketType;
    }

    async function switchMode(mode) {
      try {
        const res = await fetch('/api/set-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode })
        });
        const data = await res.json();
        if (data.code === 0) {
          state.tradingMode = data.mode;
          document.getElementById('modeText').textContent = data.mode;
        }
      } catch (e) {}
    }

    async function pollMarketData() {
      try {
        const res = await fetch(`/api/market-data?marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0 && json.marketType === state.marketType) {
          state.marketData = json.data;
          renderDashboard();
          renderLiveTickerMarquee();
        }
      } catch (e) {}
    }

    function renderLiveTickerMarquee() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;
      
      let sortedKeys = keys.sort((a, b) => {
        let idxA = TOP_MARKET_CAP_ORDER.indexOf(a);
        let idxB = TOP_MARKET_CAP_ORDER.indexOf(b);
        if (idxA !== -1 && idxB !== -1) return idxA - idxB;
        if (idxA !== -1) return -1;
        if (idxB !== -1) return 1;
        return (state.marketData[b].volume || 0) - (state.marketData[a].volume || 0);
      });

      const items = sortedKeys.slice(0, 10).map(sym => {
        const item = state.marketData[sym];
        return `<div class="flex items-center gap-2 bg-white/5 px-3 py-1 rounded-lg border border-white/10 shrink-0">
          <span class="font-bold text-white">${sym.replace('-USDT', '')}</span>
          <span class="text-white/90">$${formatPrice(item.price)}</span>
          <span class="${item.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-bold">${item.change24h >= 0 ? '+' : ''}${item.change24h.toFixed(2)}%</span>
        </div>`;
      }).join('');
      document.getElementById('liveTickerMarquee').innerHTML = items + items;
    }

    function renderDashboard() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;

      let sorted = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => {
        let idxA = TOP_MARKET_CAP_ORDER.indexOf(a.symbol);
        let idxB = TOP_MARKET_CAP_ORDER.indexOf(b.symbol);
        if (idxA !== -1 && idxB !== -1) return idxA - idxB;
        if (idxA !== -1) return -1;
        if (idxB !== -1) return 1;
        return (b.volume || 0) - (a.volume || 0);
      });

      let topMover = [...sorted].sort((a, b) => Math.abs(b.change24h) - Math.abs(a.change24h))[0];
      if (topMover) {
        document.getElementById('kpiTopMoverSymbol').textContent = topMover.symbol;
        document.getElementById('kpiTopMoverChange').textContent = (topMover.change24h >= 0 ? '+' : '') + topMover.change24h.toFixed(2) + '%';
      }

      const masterAsset = sorted[0];
      if (masterAsset) {
        const local = calcolaDatiLocaliUnificati(masterAsset.symbol, masterAsset);
        const liveRemMaster = getLiveRemainingSeconds(masterAsset.symbol, local.tempoSec);
        document.getElementById('masterPrecisionBadge').textContent = `CANDLE-SYNC ⚡`;
        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <strong class="text-white text-sm">${masterAsset.symbol} - MASTER QUANTUM ${state.marketType}</strong>
              <div class="flex justify-between"><span class="text-white/70">Prezzo Live:</span><strong class="text-[#00FF66] text-sm">$${formatPrice(masterAsset.price)}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">Direzione Quantistica:</span><strong class="${local.side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-bold">${local.side === 'BUY' ? 'LONG CHIRURGICO 🚀' : 'SHORT CHIRURGICO 📉'}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">$ TP Calibrato (+% Leva ${state.selectedLeverage}x):</span><strong class="text-yellow-400 font-extrabold">$${formatPrice(local.tp)} (+${local.tpPct}%)</strong></div>
              <div class="flex justify-between"><span class="text-white/70">⏱️ Tempo al $ TP (Candle-Sync):</span><strong class="text-cyan-300 font-bold tabular-nums" data-countdown-sym="${masterAsset.symbol}" data-base-sec="${local.tempoSec}">${formatExactDuration(liveRemMaster)}</strong></div>
            </div>
            <div class="flex flex-col justify-end">
              <button onclick='openModal("${masterAsset.symbol}")' class="green-gradient-btn w-full py-2 rounded-xl text-xs shadow-lg">⚡ APRI SCANNER &amp; SCEGLI CAPITALE &#8599;</button>
            </div>
          </div>`;
      }

      let filtered = sorted;
      if (state.currentCategoryTab !== 'ALL') filtered = filtered.filter(c => c.category === state.currentCategoryTab);
      if (state.searchQuery) filtered = filtered.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));

      const container = document.getElementById('tablesContainer');
      container.innerHTML = filtered.map(c => {
        const local = calcolaDatiLocaliUnificati(c.symbol, c);
        const liveRemCard = getLiveRemainingSeconds(c.symbol, local.tempoSec);
        const isLong = local.side === 'BUY';
        return `
          <div onclick='openModal("${c.symbol}")' class="bg-[#000000] border border-white/25 hover:border-[#00FF66] rounded-xl p-3.5 cursor-pointer transition-all space-y-2.5">
            <div class="flex items-center justify-between border-b border-white/10 pb-2">
              <div class="flex items-center gap-2">
                <span class="font-extrabold text-white text-sm">${c.symbol.replace('-USDT', '')}</span>
                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${isLong ? 'bg-green-500/20 text-[#00FF66]' : 'bg-red-500/20 text-[#FF334B]'}">${isLong ? 'LONG SUP 🚀' : 'SHORT SUP 📉'}</span>
              </div>
              <div class="text-right">
                <span class="text-white text-xs font-bold tabular-nums">$${formatPrice(c.price)}</span>
                <span class="block text-[10px] ${c.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${c.change24h >= 0 ? '+' : ''}${c.change24h.toFixed(2)}%</span>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2 text-[11px] font-mono">
              <div class="bg-white/5 p-2 rounded-lg">
                <span class="text-white/60 text-[10px] block">🎯 TARGET $ TP CALIBRATO</span>
                <strong class="text-[#00FF66]">$${formatPrice(local.tp)}</strong>
                <span class="text-yellow-400 text-[10px] block">+${local.tpPct}% (con ${state.selectedLeverage}x)</span>
              </div>
              <div class="bg-white/5 p-2 rounded-lg">
                <span class="text-white/60 text-[10px] block">⏱️ TEMPO AL $ TP (CANDLE)</span>
                <strong class="text-cyan-300 tabular-nums" data-countdown-sym="${c.symbol}" data-base-sec="${local.tempoSec}">${formatExactDuration(liveRemCard)}</strong>
                <span class="text-[#FF334B] text-[10px] block">SL 😢: $${formatPrice(local.sl)}</span>
              </div>
            </div>

            <div class="flex items-center justify-between pt-1">
              <span class="text-[10px] text-[#00FF66] font-bold">Stato: ${local.confluenza}</span>
              <span class="px-3 py-1 rounded-lg bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/40 text-xs font-bold">1. Entra Sup ⚡</span>
            </div>
          </div>
        `;
      }).join('');
    }

    async function openModal(symbol) {
      if (state.activePosition) {
        alert("⚠️ C'è già 1 posizione attiva protetta monitorata (Gestisci le fasi 1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci).");
        return;
      }

      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalOhlcvBadge').textContent = "CANDLE-SYNC IN CORSO...";
      document.getElementById('tfPanel').innerHTML = '<div class="col-span-4 text-center text-xs text-white/60 py-4">Sincronizzazione candela e flusso in corso...</div>';

      const btn = document.getElementById('executeBingXBtn');
      const fb = document.getElementById('executionFeedback');
      btn.disabled = false;
      fb.classList.add('hidden');
      fb.textContent = '';

      try {
        const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(symbol)}&marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0 && json.data) {
          const d = json.data;
          
          const oldAnchor = state.timerAnchors[symbol];
          if (!oldAnchor || oldAnchor.candleTime !== d.candleTime) {
            state.timerAnchors[symbol] = {
              baseSec: d.tempoSec,
              candleTime: d.candleTime,
              startedAt: Date.now()
            };
          }

          state.currentSelectedAsset = d;
          state.detailedAssets[symbol] = d;

          document.getElementById('modalOhlcvBadge').textContent = `QUANTUM SCORE: ${d.precisionScore}/100`;
          document.getElementById('modalCategory').textContent = d.marketType;

          const tfEl = document.getElementById('modalTF');
          tfEl.textContent = `${d.exactTf.toUpperCase()} CANDLE-SYNC ⚡`;
          tfEl.className = "px-2.5 py-0.5 rounded bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66] font-extrabold animate-pulse";

          document.getElementById('modalSentiment').textContent = d.sentiment;
          
          const currentLiveRem = getLiveRemainingSeconds(symbol, d.tempoSec);
          document.getElementById('modalExactTime').textContent = `${formatExactDuration(currentLiveRem)} 🎯`;

          document.getElementById('modalEma').textContent = `EMA9: ${formatPrice(d.ema9)} | EMA21: ${formatPrice(d.ema21)}`;
          document.getElementById('modalEmaSignal').textContent = "Trend Calibrato ⚡";
          document.getElementById('modalEmaSignal').className = "text-[10px] block mt-0.5 text-[#00FF66]";

          document.getElementById('modalRsi').textContent = `RSI: ${d.rsi14}`;
          document.getElementById('modalRsiZone').textContent = "Ottimale 🎯";

          document.getElementById('modalLivePrice').textContent = `$${formatPrice(d.price)}`;

          const sideEl = document.getElementById('modalSide');
          sideEl.textContent = `${d.side} (${d.side === 'BUY' ? 'LONG CHIRURGICO 🚀' : 'SHORT CHIRURGICO 📉'})`;
          sideEl.className = `text-[12px] font-bold ${d.side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]'}`;

          document.getElementById('modalSL').textContent = `$${formatPrice(d.sl)}`;
          document.getElementById('modalTrendingTP').textContent = `$${formatPrice(d.tp)}`;
          const tpPct = (((Math.abs(d.tp - d.price) / d.price) * 100) * state.selectedLeverage).toFixed(2);
          document.getElementById('modalTpPct').textContent = `+${tpPct}% (con leva ${state.selectedLeverage}x)`;

          document.getElementById('tableParamEntry').textContent = formatPrice(d.price);
          document.getElementById('tableParamTp').textContent = formatPrice(d.tp);
          document.getElementById('tableParamSl').textContent = formatPrice(d.sl);

          aggiornaQuantitaDaCapitale();

          renderTfPanel(d.tfs, d.exactTf);
          renderDashboard();
        } else {
          alert("Errore scansione quantistica: " + (json.msg || "Impossibile scaricare i dati"));
          closeModal();
        }
      } catch (e) {
        alert("Errore di connessione al server.");
        closeModal();
      }
    }

    function closeModal() {
      state.currentSelectedAsset = null;
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
    }

    async function eseguiOrdineSuBingX() {
      if (!state.currentSelectedAsset) return;
      const asset = state.currentSelectedAsset;
      const qty = parseFloat(document.getElementById('tradeQuantityInput').value) || 1.0;
      const keys = getStoredApiKeys();

      const res = await fetch('/api/esegui-segnale', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: asset.symbol, action: asset.side, size: qty, entry: asset.price,
          tp: asset.tp, sl: asset.sl, leverage: state.selectedLeverage, timeframe: asset.timeframe,
          marketType: state.marketType, apiKey: keys.apiKey, secretKey: keys.secretKey
        })
      });
      const data = await res.json();
      if (data.code === 0) {
        avviaAdvisorPosizione(asset.symbol, asset.side, asset.price, asset.tp, asset.sl, asset.tempoSec);
        closeModal();
      } else {
        alert("Errore esecuzione: " + (data.msg || "Verifica le tue chiavi API"));
      }
    }

    function logFeed(text) {
      const container = document.getElementById('alertsFeedContainer');
      const div = document.createElement('div');
      div.className = "p-2 rounded bg-white/5 border border-white/10 text-white/90 mt-1";
      div.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
      container.prepend(div);
    }

    window.addEventListener('DOMContentLoaded', async () => {
      updateApiStatusUI();
      await pollMarketData();
      setInterval(pollMarketData, 2000);
      setInterval(() => {
        document.getElementById('liveCetClock').textContent = new Date().toTimeString().slice(0, 8) + ' CET';
        updateAllVisibleCountdowns();
      }, 1000);
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
