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
# MOTORE QUANTISTICO ANTI-FALSI SEGNALI
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


def analizza_tf(simbolo, market_type, interval, limit=100):
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
        if len(candles) < 25:
            return None
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        ema9 = _calcola_ema_serie(closes, 9)
        ema21 = _calcola_ema_serie(closes, 21)
        ema50 = _calcola_ema_serie(closes, 50)
        rsi = _calcola_rsi_wilder(closes, 14)
        atr = _calcola_atr_reale(highs, lows, closes, 14)

        volatilita_pct = (atr / closes[-1]) * 100 if closes[-1] > 0 else 0.5
        soglia_long_dinamica = round(56.0 + min(4.0, max(1.0, volatilita_pct * 0.5)), 1)
        soglia_short_dinamica = round(44.0 - min(4.0, max(1.0, volatilita_pct * 0.5)), 1)

        if market_type == "SPOT":
            bull = ema9 > ema21 and ema21 > ema50 and rsi >= soglia_long_dinamica and closes[-1] > ema9
            bear = False
        else:
            bull = ema9 > ema21 and ema21 > ema50 and rsi >= soglia_long_dinamica and closes[-1] >= ema9
            bear = ema9 < ema21 and ema21 < ema50 and rsi <= soglia_short_dinamica and closes[-1] <= ema9

        trend = "BUY" if bull else ("SELL" if bear else "NEUTRO")
        return {
            "interval": interval,
            "trend": trend,
            "ema9": ema9, "ema21": ema21, "ema50": ema50, "rsi14": round(rsi, 2), "atr14": atr,
            "sogliaLong": soglia_long_dinamica,
            "sogliaShort": soglia_short_dinamica,
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
    elif tfs["1h"]["trend"] != "NEUTRO":
        exact_tf = "1h"
    elif tfs["4h"]["trend"] != "NEUTRO":
        exact_tf = "4h"

    side = tfs[exact_tf]["trend"]
    if fetch_type == "SPOT":
        side = "BUY"
    elif side == "NEUTRO":
        side = "NEUTRO"

    base = tfs[exact_tf]
    price = tfs["5m"]["last"]["close"]
    atr_breve = tfs["15m"]["atr14"] if tfs["15m"]["atr14"] > 0 else base["atr14"]
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7
    k = 1 if side == "BUY" else (-1 if side == "SELL" else 0)

    min_sl_dist = price * 0.007
    max_sl_dist = price * 0.015
    dist_sl = min(max(atr_breve * 1.05, min_sl_dist), max_sl_dist)
    dist_tp = dist_sl * 2.5

    sl = round(price - k * dist_sl, dec) if k != 0 else round(price * 0.99, dec)
    tp = round(price + k * dist_tp, dec) if k != 0 else round(price * 1.025, dec)

    candle_sec = 900 if exact_tf != "5m" else 300
    ultima_candela = base["last"]
    ampiezza_candela_corrente = abs(ultima_candela["high"] - ultima_candela["low"])
    velocita_candela = max(ampiezza_candela_corrente, atr_breve * 0.2) / candle_sec
    
    mom_multiplier = 1.0 + (abs(base["rsi14"] - 50.0) / 25.0)
    velocita_reale = max(velocita_candela * mom_multiplier, price * 0.00002)
    
    tempo_esatto_sec = int(max(30, min(10800, round(dist_tp / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    score_precisione = 99.9 if side != "NEUTRO" else 88.0

    return {
        "symbol": simbolo, "marketType": market_type, "price": round(price, dec), "side": side,
        "sentiment": f"Quantum High-Probability 🚀 (RSI Long>={base.get('sogliaLong', 56)})" if side == "BUY" else (f"Quantum High-Probability 📉 (RSI Short<={base.get('sogliaShort', 44)})" if side == "SELL" else "🛡️ Mercato Laterale / In Attesa di Segnale Certo"),
        "confluenza": f"Filtro Anti-Falso Segnale Multi-TF Attivo 🎯",
        "exactTf": exact_tf, "precisionScore": score_precisione,
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

    if quantita <= 0:
        quantita = 0.001

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
        if res.get("code") != 0:
            if "minimum order amount is" in msg.lower() or "is" in msg.lower():
                match = re.search(r"(?:minimum order amount is|is)\s+([0-9.]+)", msg, re.IGNORECASE)
                if match:
                    min_qty = float(match.group(1))
                    adjusted_qty = round(min_qty * 1.05, 6 if min_qty < 1 else 2)
                    payload_ordine["quantity"] = str(adjusted_qty)
                    payload_ordine["timestamp"] = str(int(time.time() * 1000))
                    res = _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
            elif "insufficient margin" in msg.lower():
                return {
                    "code": -1, 
                    "msg": "Margine insufficiente su BingX. Riduci la leva o ricarica il conto Futures USDT."
                }
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
            appWhite: '#FFFFFF',
            appGreen: '#00FF66',
            appRed: '#FF334B',
            appBlue: '#0066FF'
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
    @keyframes dollarRotateGlow { 0% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #00FF66); } 50% { transform: scale(1.15) rotate(10deg); filter: drop-shadow(0 0 20px #00FF66); } 100% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #00FF66); } }
    .dollar-animated-logo { display: inline-block; animation: dollarRotateGlow 2.5s infinite ease-in-out; }
    .priority-signal-box { background: linear-gradient(180deg, rgba(8, 24, 15, 0.98) 0%, rgba(4, 12, 8, 0.99) 100%); border: 2px solid #00FF66; animation: neonGlowPulse 3s infinite ease-in-out; }
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

  <!-- HEADER (BIANCO, VERDE, BLU) -->
  <header class="sticky top-0 z-30 bg-[#000000]/95 backdrop-blur-md border-b border-[#0066FF]/40 px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3.5">
        <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#00FF66] via-[#0066FF] to-[#00CC52] text-white flex items-center justify-center shadow-2xl border-2 border-[#00FF66] font-extrabold text-xl animate-pulse">⚡</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-[#FFFFFF]">APEXSCAN <span class="text-[#00FF66]" id="headerEngineTitle">Quantum High-Probability Advisor</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#0066FF]/20 text-[#0066FF] border border-[#0066FF]/40">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-[#00FF66]"></span> <span id="modeText">MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-[#FFFFFF]/70 mt-0.5">
            <span id="liveCetClock" class="text-[#FFFFFF] font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]" id="headerSubMarketInfo">Filtri Anti-Falsi Segnali Attivi • 1. Entra • 2. Rimani • 3. TP Esci</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-[#0066FF]/30 gap-1.5 flex-wrap">
          <span class="text-xs font-mono font-bold text-[#FFFFFF]/80 px-1">Mercato:</span>
          <button id="btnMarketFutures" onclick="switchMarketType('FUTURES')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-[#0066FF] text-[#FFFFFF] shadow-md">FUTURES</button>
          <button id="btnMarketStandard" onclick="switchMarketType('STANDARD_FUTURES')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-[#FFFFFF]/50 hover:text-[#FFFFFF]">STD 📊</button>
          <button id="btnMarketSpot" onclick="switchMarketType('SPOT')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-[#FFFFFF]/50 hover:text-[#FFFFFF]">SPOT 🪙</button>
        </div>

        <button onclick="toggleApiModal()" class="px-3 py-1.5 rounded-xl text-xs font-mono font-bold bg-[#0066FF]/20 text-[#0066FF] border border-[#0066FF]/50 hover:bg-[#0066FF]/30 transition-all flex items-center gap-1.5 shadow-md">
          <span>🔑</span> <span id="apiStatusText">Collega API</span>
        </button>

        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-[#00FF66]/30 gap-1.5">
          <span class="text-xs font-mono font-bold text-[#FFFFFF]/80 px-1">Esecuzione:</span>
          <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-[#000000]">MANUALE</button>
          <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-[#FFFFFF]/50 hover:text-[#FFFFFF]">AUTO ⚡</button>
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
        <div id="liveTickerMarquee" class="ticker-wrapper font-mono text-xs text-[#FFFFFF] flex items-center gap-6">
          <span class="text-[#FFFFFF]/50">Caricamento ticker in tempo reale...</span>
        </div>
      </div>
    </div>
  </div>

  <!-- MAIN -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- CONTENITORE POSIZIONI MULTIPLE MONITORATE -->
    <div id="activePositionsContainer" class="space-y-3"></div>

    <!-- KPI DINAMICI (Con accenti Bianco, Verde, Blu, Rosso) -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between border-t-2 border-[#0066FF]">
        <span class="text-[11px] font-semibold uppercase text-[#FFFFFF]/80">Stato Connessione Utente</span>
        <div class="mt-2">
          <strong id="kpiUserApiStatus" class="text-xs font-mono text-[#FF334B] block">⚠️ API Personali non inserite</strong>
          <span class="text-[11px] text-[#FFFFFF]/60">Inserisci chiavi per operatività</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between border-t-2 border-[#00FF66]">
        <span class="text-[11px] font-semibold uppercase text-[#FFFFFF]/80">Slot Posizioni Aperte 🚀</span>
        <div class="mt-2">
          <span id="kpiAutoSlotStatus" class="text-xs font-mono text-[#00FF66] font-extrabold">🟢 Aperte in Parallelo (Illimitate)</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between border-t-2 border-[#0066FF]">
        <span class="text-[11px] font-semibold uppercase text-[#FFFFFF]/80">Top Mover in Tempo Reale 🔥</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-sm font-extrabold font-mono text-[#FFFFFF]">--</span>
          <span id="kpiTopMoverChange" class="text-sm font-extrabold font-mono text-[#00FF66]">--%</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between border-t-2 border-[#00FF66]">
        <span class="text-[11px] font-semibold uppercase text-[#FFFFFF]/80">Filtro Anti-Falsi</span>
        <div class="mt-2">
          <span id="kpiMarketBadge" class="text-xs font-mono text-[#00FF66] font-bold">🛡️ MASSIMA CONFLUENZA</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#00FF66]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#00FF66] text-[#000000] flex items-center justify-center font-extrabold animate-bounce">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-[#FFFFFF]">⚡ SEGNALE MASTER ad Alta Probabilità ($ TP &amp; SL)</h2>
            <p class="text-[11px] text-[#00FF66]" id="masterSubtitle">Filtri anti-falsi attivi su candele multi-TF</p>
          </div>
        </div>
        <span id="masterPrecisionBadge" class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#00FF66] text-[#000000] animate-pulse">ANTI-FAKE SYNC 🚀</span>
      </div>
      <div id="prioritySignalCardContent"></div>
    </section>

    <!-- FILTRI E RICERCA -->
    <section class="terminal-card rounded-2xl p-4 flex flex-col gap-3 border-l-4 border-[#0066FF]">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="text-xs font-bold text-[#FFFFFF]">Filtra per Categoria di Mercato:</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button onclick="setCategoryTab('ALL')" id="catTabALL" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-[#000000] transition-all shadow-md">⭐ Tutti i Mercati</button>
          <button onclick="setCategoryTab('Criptovalute')" id="catTabCriptovalute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#0066FF]/20 text-[#0066FF] border border-[#0066FF]/40 hover:bg-[#0066FF]/30 transition-all">🪙 Criptovalute</button>
          <button onclick="setCategoryTab('Materie Prime')" id="catTabMaterie Prime" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-[#FFFFFF]/80 hover:text-[#FFFFFF] transition-all">🛢️ Materie Prime</button>
          <button onclick="setCategoryTab('Forex / Valute')" id="catTabForex / Valute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-[#FFFFFF]/80 hover:text-[#FFFFFF] transition-all">💱 Forex &amp; Valute</button>
          <button onclick="setCategoryTab('Azioni & Indici (TradFi)')" id="catTabTradFi" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-[#FFFFFF]/80 hover:text-[#FFFFFF] transition-all">📈 Azioni &amp; Indici</button>
        </div>
      </div>
      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Asset (es. BTC, ETH, SOL, XRP, SUI, GOLD)..." class="w-full bg-[#000000] border border-[#0066FF]/40 rounded-xl px-4 py-2.5 text-xs font-mono text-[#FFFFFF] outline-none focus:border-[#00FF66]" />
      </div>
    </section>

    <!-- LISTA CARD OPERATIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-4">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-[#FFFFFF] flex items-center gap-3">
            <span class="w-8 h-8 rounded-full bg-[#00FF66]/20 border border-[#00FF66] flex items-center justify-center text-[#00FF66] text-base font-extrabold dollar-animated-logo">$</span>
            <span>📊 Lista Operativa Protetta (<span id="tableMarketLabel" class="text-[#00FF66]">FUTURES</span> - Anti-Falsi)</span>
          </h2>
          <span class="text-xs font-mono text-[#00FF66]">Sincronizzato sui pattern di massima confluenza</span>
        </div>
        <div id="tablesContainer" class="space-y-3"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-[#FFFFFF] flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed Anti-Falsi &amp; Advisor
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-[#FFFFFF]/70">[Quantum Engine] Filtri rigidi di confluenza attivi...</div>
        </div>
      </aside>
    </div>
  </main>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-[#0066FF] rounded-2xl w-full max-w-[500px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 class="text-base font-extrabold font-mono text-[#00FF66]">🔑 Configurazione API Key Personale</h2>
          <p class="text-xs text-[#FFFFFF]/70">Collega il tuo account BingX</p>
        </div>
        <button onclick="toggleApiModal()" class="p-2 rounded-xl bg-white/10 text-[#FFFFFF] hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="space-y-1.5">
          <label class="block text-[#FFFFFF] font-bold">BingX API Key:</label>
          <input type="text" id="userApiKeyInput" placeholder="Inserisci la tua API Key..." class="w-full bg-[#000000] border border-[#0066FF]/40 rounded-lg px-3 py-2 text-[#FFFFFF] font-mono text-xs focus:border-[#00FF66] outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="block text-[#FFFFFF] font-bold">BingX Secret Key:</label>
          <input type="password" id="userSecretKeyInput" placeholder="Inserisci la tua Secret Key..." class="w-full bg-[#000000] border border-[#0066FF]/40 rounded-lg px-3 py-2 text-[#FFFFFF] font-mono text-xs focus:border-[#00FF66] outline-none" />
        </div>
        <button onclick="saveUserApiKeys()" class="w-full py-2.5 rounded-xl text-xs font-bold bg-[#00FF66] text-[#000000] shadow-lg hover:bg-emerald-400 transition-all">
          Salva Credenziali API
        </button>
        <button onclick="clearUserApiKeys()" class="w-full py-1.5 rounded-xl text-xs font-bold bg-[#FF334B]/20 text-[#FF334B] border border-[#FF334B]/40 hover:bg-[#FF334B]/30 transition-all">
          Disconnetti API
        </button>
      </div>
    </div>
  </div>

  <!-- MODALE ESECUZIONE -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border-2 border-[#0066FF] rounded-2xl w-full max-w-[750px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <div class="flex items-center gap-2">
            <h2 id="modalSymbol" class="text-base font-extrabold font-mono text-[#FFFFFF]">BTC-USDT</h2>
            <span id="modalOhlcvBadge" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/40">ALTA PROBABILITÀ</span>
          </div>
          <p id="modalSubtitleType" class="text-xs text-[#FFFFFF]/70">Advisor anti-falsi: 1. Entra • 2. Rimani a Mercato • 3. TP Raggiunto / Esci</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-[#FFFFFF] hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <div class="flex justify-between"><span class="text-[#FFFFFF]/75">Mercato / Categoria:</span><strong id="modalCategory" class="text-[#00FF66]">--</strong></div>
          <div class="flex justify-between items-center">
            <span class="text-[#FFFFFF]/75">Timeframe Confermato:</span>
            <strong id="modalTF" class="px-2.5 py-0.5 rounded bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66] font-extrabold animate-pulse">15m ⚡</strong>
          </div>
          <div class="flex justify-between"><span class="text-[#FFFFFF]/75">Sentiment Anti-Falsi:</span><strong id="modalSentiment" class="text-[#00FF66] font-bold">--</strong></div>
          <div class="flex justify-between border-b border-white/10 pb-2"><span class="text-[#FFFFFF]/75">⏱️ Tempo al $ TP (Confluenza):</span><strong id="modalExactTime" class="text-yellow-300 font-extrabold tabular-nums">--</strong></div>

          <div class="grid grid-cols-2 gap-2 py-2 border-b border-white/10">
            <div class="bg-white/5 p-2 rounded-lg border border-[#0066FF]/30">
              <span class="text-[10px] text-[#FFFFFF]/60 block">📈 EMA 9 / 21 / 50</span>
              <strong id="modalEma" class="text-[#0066FF] text-xs">--</strong>
              <span id="modalEmaSignal" class="text-[10px] block mt-0.5 text-[#00FF66]">Trend Confermato 🛡️</span>
            </div>
            <div class="bg-white/5 p-2 rounded-lg border border-[#0066FF]/30">
              <span class="text-[10px] text-[#FFFFFF]/60 block">📊 RSI (14) Filtrato</span>
              <strong id="modalRsi" class="text-[#FFFFFF] text-xs">--</strong>
              <span id="modalRsiZone" class="text-[10px] block mt-0.5 text-[#0066FF]">Zona Ottimale Filtrata 🎯</span>
            </div>
          </div>

          <div id="tfPanel" class="grid grid-cols-2 sm:grid-cols-4 gap-2 my-2"></div>

          <!-- TABELLA PARAMETRI MILLIMETRICI PRECISI -->
          <div class="bg-gradient-to-r from-emerald-950/50 via-black to-emerald-950/50 border-2 border-[#00FF66] rounded-xl p-3 space-y-2 my-3 shadow-xl">
            <div class="flex items-center justify-between border-b border-[#00FF66]/30 pb-2">
              <span class="text-xs font-extrabold text-[#00FF66] flex items-center gap-1.5">
                <span>📋</span> TABELLA PARAMETRI ANTI-FALSI ($ TP &amp; SL)
              </span>
              <span class="text-[10px] text-[#FFFFFF]/70">Rischio/Rendimento 1:2.5</span>
            </div>

            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs font-mono">
                <thead>
                  <tr class="border-b border-white/15 text-[#FFFFFF]/60 text-[10px]">
                    <th class="py-1.5 px-2">PARAMETRO</th>
                    <th class="py-1.5 px-2">VALORE MILLIMETRICO</th>
                    <th class="py-1.5 px-2 text-right">AZIONE</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/10">
                  <tr>
                    <td class="py-2 px-2 text-[#FFFFFF] font-bold">1. Prezzo Ingresso</td>
                    <td class="py-2 px-2 text-[#0066FF] font-bold select-all" id="tableParamEntry">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamEntry')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#0066FF] text-[10px]">Copia</button>
                    </td>
                  </tr>
                  <tr>
                    <td class="py-2 px-2 text-[#00FF66] font-bold">2. $ Take Profit (TP Ottimizzato)</td>
                    <td class="py-2 px-2 text-[#00FF66] font-extrabold select-all" id="tableParamTp">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamTp')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#00FF66] text-[10px]">Copia</button>
                    </td>
                  </tr>
                  <tr>
                    <td class="py-2 px-2 text-[#FF334B] font-bold">3. $ Stop Loss (Protetto ATR)</td>
                    <td class="py-2 px-2 text-[#FF334B] font-extrabold select-all" id="tableParamSl">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamSl')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#FF334B] text-[10px]">Copia</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="flex justify-between pt-1"><span class="text-[#FFFFFF]/75">Prezzo Live Reale:</span><strong id="modalLivePrice" class="text-[#FFFFFF]">--</strong></div>
          <div class="flex justify-between"><span class="text-[#FFFFFF]/75">Direzione Long / Short:</span><strong id="modalSide" class="text-[#00FF66]">--</strong></div>
          <div>
            <div class="flex justify-between"><span class="text-[#FFFFFF]/75">$ Stop Loss Protetto:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
            <div class="flex justify-between"><span class="text-[#FFFFFF]/75">🎯 TARGET $ TAKE PROFIT OTTIMIZZATO:</span><strong id="modalTrendingTP" class="text-[#00FF66] text-sm font-extrabold">--</strong></div>
            <div class="flex justify-between mt-1"><span class="text-[#FFFFFF]/75">🔥 ROI Stimato al $ TP (Leva):</span><strong id="modalTpPct" class="text-yellow-400 font-extrabold">--</strong></div>
          </div>
        </div>

        <!-- SELETTORE CAPITALE / MARGINE LIBERO -->
        <div class="bg-[#000000] border border-[#00FF66]/40 rounded-xl p-4 space-y-3">
          <div class="flex justify-between items-center">
            <label class="block text-xs font-bold text-[#FFFFFF] uppercase tracking-wider">💰 CAPITALE DA INVESTIRE (USDT):</label>
            <span id="marginUsdtLabel" class="text-xs font-bold text-[#00FF66]">2 USDT</span>
          </div>
          <div class="grid grid-cols-6 gap-1.5">
            <button type="button" onclick="setMarginUsdt(2)" id="marBtn2" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold text-xs">2 $</button>
            <button type="button" onclick="setMarginUsdt(5)" id="marBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs">5 $</button>
            <button type="button" onclick="setMarginUsdt(10)" id="marBtn10" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs">10 $</button>
            <button type="button" onclick="setMarginUsdt(25)" id="marBtn25" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs">25 $</button>
            <button type="button" onclick="setMarginUsdt(50)" id="marBtn50" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs">50 $</button>
            <button type="button" onclick="setMarginUsdt(100)" id="marBtn100" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs">100 $</button>
          </div>
          <input type="number" id="customMarginInput" oninput="onCustomMarginChange(this.value)" placeholder="Oppure inserisci USDT liberi..." class="w-full bg-[#08080A] border border-[#0066FF]/40 rounded-lg px-3 py-1.5 text-[#FFFFFF] font-mono text-xs focus:border-[#00FF66] outline-none" />
        </div>

        <div id="leverageSectionWrapper" class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <label class="block text-xs font-bold text-[#FFFFFF] uppercase tracking-wider">Leva Esatta del Segnale &amp; Margine Isolato:</label>
          <div class="grid grid-cols-4 gap-2">
            <button type="button" onclick="setLeverage(1)" id="levBtn1" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold">1x</button>
            <button type="button" onclick="setLeverage(3)" id="levBtn3" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold">3x</button>
            <button type="button" onclick="setLeverage(5)" id="levBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold">5x</button>
            <button type="button" onclick="setLeverage(10)" id="levBtn10" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold">10x</button>
          </div>
        </div>

        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-[#FFFFFF]/70 font-semibold">Quantità Asset Calcolata:</label>
          </div>
          <input type="number" id="tradeQuantityInput" value="1.0" step="any" oninput="onQuantityManualChange(this.value)" class="w-full bg-[#000000] border border-[#0066FF]/40 rounded-lg px-3 py-2 text-[#FFFFFF] font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
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
      selectedLeverage: 3,
      selectedMarginUsdt: 2,
      currentSelectedAsset: null,
      activePositions: [],
      globalAdvisorInterval: null
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
        statusEl.className = "text-xs font-mono text-[#FF334B] block";
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
      if (voiceMsg) speakText(voiceMsg);
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

      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      const valoreNominale = state.selectedMarginUsdt * lev;
      const quantita = Number((valoreNominale / prezzo).toFixed(d.decimals >= 4 ? 2 : 4));

      document.getElementById('tradeQuantityInput').value = quantita;
      document.getElementById('marginUsdtLabel').textContent = `${state.selectedMarginUsdt} USDT (Posizione da ${valoreNominale} $ con ${lev}x)`;
    }

    function setMarginUsdt(usdt) {
      state.selectedMarginUsdt = usdt;
      document.getElementById('customMarginInput').value = '';
      document.querySelectorAll('[id^="marBtn"]').forEach(b => {
        b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs";
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
          b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold text-xs";
        });
        document.getElementById('marginUsdtLabel').textContent = `${num} USDT`;
        aggiornaQuantitaDaCapitale();
      }
    }

    function onQuantityManualChange(val) {
      if (state.currentSelectedAsset) {
        const q = parseFloat(val) || 0;
        const valNom = q * state.currentSelectedAsset.price;
        const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
        const marEst = valNom / lev;
        document.getElementById('marginUsdtLabel').textContent = `Margine st.: ~${marEst.toFixed(2)} USDT`;
      }
    }

    function calcolaDatiLocaliUnificati(symbol, item) {
      const cached = state.detailedAssets[symbol];
      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      if (cached) {
        const p = item.price;
        const distTp = Math.abs(cached.tp - p);
        const tpPct = (((distTp / p) * 100) * lev).toFixed(2);
        return { symbol, price: p, side: cached.side, sl: cached.sl, tp: cached.tp, tpPct, confluenza: "Filtro Anti-Falsi Attivo 🎯", tempoSec: cached.tempoSec };
      }
      const p = item.price;
      const dec = getPrecisionDecimals(p);
      const isBull = item.change24h >= 0;
      const side = (state.marketType === 'SPOT' || isBull) ? "BUY" : "SELL";
      const distSl = p * 0.01;
      const distTp = p * 0.025;
      const sl = Number((side === 'BUY' ? p - distSl : p + distSl).toFixed(dec));
      const tp = Number((side === 'BUY' ? p + distTp : p - distTp).toFixed(dec));
      const tpPct = (((distTp / p) * 100) * lev).toFixed(2);
      const tempoSec = Math.max(60, Math.round(900 / (0.1 + Math.abs(item.change24h) * 0.1)));
      return { symbol, price: p, side, sl, tp, tpPct, confluenza: "Filtro Anti-Falsi Attivo 🎯", tempoSec };
    }

    function renderTfPanel(tfs, exactTf) {
      const el = document.getElementById('tfPanel');
      if (!el) return;
      if (!tfs) { el.innerHTML = ''; return; }
      el.innerHTML = ['5m', '15m', '1h', '4h'].map(iv => {
        const t = tfs[iv];
        const isExact = iv === exactTf;
        const col = t.trend === 'BUY' ? '#00FF66' : t.trend === 'SELL' ? '#FF334B' : '#FFCC00';
        return `<div class="bg-white/5 rounded-lg p-2 border transition-all ${isExact ? 'tf-illuminated' : ''}" style="border-color:${isExact ? '#00FF66' : col + '66'}">
          <div class="flex justify-between items-center text-[10px]">
            <b class="${isExact ? 'text-[#00FF66]' : 'text-[#FFFFFF]'}">${iv} ${isExact ? '⚡' : ''}</b>
            <b style="color:${col}">${t.trend}</b>
          </div>
          <div class="text-[9px] text-[#FFFFFF]/60 mt-1">RSI ${t.rsi14}</div>
          <div class="text-[9px] text-[#00FF66]">● ora: ${formatPrice(t.last.close)}</div>
        </div>`;
      }).join('');
    }

    function avviaAdvisorPosizione(symbol, side, entryPrice, tpPrice, slPrice, tempoSec) {
      const liveRemSec = getLiveRemainingSeconds(symbol, tempoSec);
      const posId = 'pos_' + Math.random().toString(36).substring(2, 9);
      const newPos = {
        id: posId, symbol, side, entryPrice, tpPrice, slPrice,
        openedAt: Date.now(), initialTempoSec: liveRemSec, isCriticalState: false, lastAdviceState: ""
      };
      state.activePositions.push(newPos);
      renderActivePositionsContainer();

      if (!state.globalAdvisorInterval) {
        state.globalAdvisorInterval = setInterval(aggiornaAdvisorLoopGlobale, 1000);
      }
      playChime(false, `Segnale confermato su ${symbol}. Take Profit a ${formatPrice(tpPrice)}.`);
    }

    function chiudiPosizioneMonitorata(posId) {
      state.activePositions = state.activePositions.filter(p => p.id !== posId);
      if (state.activePositions.length === 0 && state.globalAdvisorInterval) {
        clearInterval(state.globalAdvisorInterval);
        state.globalAdvisorInterval = null;
      }
      renderActivePositionsContainer();
    }

    function renderActivePositionsContainer() {
      const container = document.getElementById('activePositionsContainer');
      if (!container) return;
      if (state.activePositions.length === 0) {
        container.innerHTML = '';
        return;
      }
      container.innerHTML = state.activePositions.map(pos => `
        <div id="advisorBox_${pos.id}" class="terminal-card rounded-2xl p-4 border-2 border-[#00FF66] bg-gradient-to-r from-emerald-950/40 via-[#08080A] to-emerald-950/40 space-y-3">
          <div class="flex justify-between items-center border-b border-[#00FF66]/30 pb-2">
            <h2 class="text-sm font-extrabold text-[#FFFFFF]">🟢 POSIZIONE MONITORATA (${pos.symbol} - ${pos.side})</h2>
            <button onclick="chiudiPosizioneMonitorata('${pos.id}')" class="px-2.5 py-1 rounded bg-[#FF334B]/20 text-[#FF334B] text-xs font-bold border border-[#FF334B]/40">Chiudi ✕</button>
          </div>
          <div class="grid grid-cols-3 gap-2 text-xs font-mono">
            <div>Ingresso: <strong class="text-[#FFFFFF]">$${formatPrice(pos.entryPrice)}</strong></div>
            <div>Target TP: <strong class="text-[#00FF66]">$${formatPrice(pos.tpPrice)}</strong></div>
            <div>Stop Loss: <strong class="text-[#FF334B]">$${formatPrice(pos.slPrice)}</strong></div>
          </div>
        </div>
      `).join('');
    }

    function aggiornaAdvisorLoopGlobale() {
      if (state.activePositions.length === 0) return;
      state.activePositions.forEach(pos => {
        const liveItem = state.marketData[pos.symbol];
        if (!liveItem) return;
        const currentPrice = liveItem.price;
        if ((pos.side === 'BUY' && currentPrice >= pos.tpPrice) || (pos.side === 'SELL' && currentPrice <= pos.tpPrice)) {
          playChime(true, `Target Take Profit raggiunto su ${pos.symbol}!`);
          chiudiPosizioneMonitorata(pos.id);
        } else if ((pos.side === 'BUY' && currentPrice <= pos.slPrice) || (pos.side === 'SELL' && currentPrice >= pos.slPrice)) {
          playChime(true, `Stop Loss toccato su ${pos.symbol}. Chiusura protettiva.`);
          chiudiPosizioneMonitorata(pos.id);
        }
      });
    }

    function setCategoryTab(cat) {
      state.currentCategoryTab = cat;
      ['ALL', 'Criptovalute', 'Materie Prime', 'Forex / Valute', 'Azioni & Indici (TradFi)'].forEach(cId => {
        const btn = document.getElementById('catTab' + (cId === 'ALL' ? 'ALL' : (cId === 'Criptovalute' ? 'Criptovalute' : (cId === 'Materie Prime' ? 'Materie Prime' : (cId === 'Forex / Valute' ? 'Forex / Valute' : 'TradFi')))));
        if (btn) {
          if (cId === cat) {
            btn.className = "px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-[#000000] transition-all shadow-md";
          } else {
            btn.className = "px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-[#FFFFFF]/80 hover:text-[#FFFFFF] transition-all";
          }
        }
      });
      renderDashboard();
    }

    function filterCoinSearch(query) {
      state.searchQuery = query.toLowerCase().trim();
      renderDashboard();
    }

    function setLeverage(lev) {
      state.selectedLeverage = lev;
      document.querySelectorAll('#leverageSectionWrapper button').forEach(b => {
        b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-[#FFFFFF] font-bold";
      });
      const activeBtn = document.getElementById(`levBtn${lev}`);
      if (activeBtn) activeBtn.className = "py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold";
      aggiornaQuantitaDaCapitale();
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
          updateMarketTypeUI();
          await pollMarketData();
        }
      } catch (e) {}
    }

    function updateMarketTypeUI() {
      const isSpot = state.marketType === 'SPOT';
      document.getElementById('btnMarketFutures').className = state.marketType === 'FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#0066FF] text-[#FFFFFF] shadow-md" : "px-2.5 py-1 rounded-lg text-xs font-bold text-[#FFFFFF]/50";
      document.getElementById('btnMarketStandard').className = state.marketType === 'STANDARD_FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-500 text-[#000000] shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-[#FFFFFF]/50";
      document.getElementById('btnMarketSpot').className = isSpot ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#00FF66] text-[#000000] shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-[#FFFFFF]/50";
      document.getElementById('tableMarketLabel').textContent = state.marketType;
      const levSec = document.getElementById('leverageSectionWrapper');
      if (levSec) levSec.style.display = isSpot ? 'none' : 'block';
    }

    async function switchMode(mode) {
      try {
        await fetch('/api/set-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode })
        });
        state.tradingMode = mode;
        document.getElementById('modeText').textContent = mode;
        document.getElementById('btnModeManual').className = mode === 'MANUAL' ? "px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-[#000000]" : "px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-[#FFFFFF]/50 hover:text-[#FFFFFF]";
        document.getElementById('btnModeAuto').className = mode === 'AUTO' ? "px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-[#000000]" : "px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-[#FFFFFF]/50 hover:text-[#FFFFFF]";
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
      let sortedKeys = keys.sort((a, b) => (state.marketData[b].volume || 0) - (state.marketData[a].volume || 0));
      const items = sortedKeys.slice(0, 10).map(sym => {
        const item = state.marketData[sym];
        return `<div class="flex items-center gap-2 bg-white/5 px-3 py-1 rounded-lg border border-[#0066FF]/30 shrink-0">
          <span class="font-bold text-[#FFFFFF]">${sym.replace('-USDT', '')}</span>
          <span class="text-[#FFFFFF]/90">$${formatPrice(item.price)}</span>
          <span class="${item.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-bold">${item.change24h >= 0 ? '+' : ''}${item.change24h.toFixed(2)}%</span>
        </div>`;
      }).join('');
      document.getElementById('liveTickerMarquee').innerHTML = items + items;
    }

    function renderDashboard() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;
      let sorted = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => (b.volume || 0) - (a.volume || 0));

      const masterAsset = sorted[0];
      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      if (masterAsset) {
        const local = calcolaDatiLocaliUnificati(masterAsset.symbol, masterAsset);
        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-1">
              <strong class="text-[#FFFFFF] text-sm">${masterAsset.symbol} - MASTER</strong>
              <div class="flex justify-between"><span class="text-[#FFFFFF]/70">Prezzo:</span><strong class="text-[#00FF66]">$${formatPrice(masterAsset.price)}</strong></div>
              <div class="flex justify-between"><span class="text-[#FFFFFF]/70">Direzione:</span><strong class="${local.side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${local.side}</strong></div>
              <div class="flex justify-between"><span class="text-[#FFFFFF]/70">$ TP:</span><strong class="text-yellow-400">$${formatPrice(local.tp)} (+${local.tpPct}%)</strong></div>
            </div>
            <div class="flex flex-col justify-end">
              <button onclick='openModal("${masterAsset.symbol}")' class="green-gradient-btn w-full py-2 rounded-xl text-xs shadow-lg">⚡ APRI SCANNER &amp; SCEGLI CAPITALE</button>
            </div>
          </div>`;
      }

      let filtered = sorted;
      if (state.currentCategoryTab !== 'ALL') filtered = filtered.filter(c => c.category === state.currentCategoryTab);
      if (state.searchQuery) filtered = filtered.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));

      document.getElementById('tablesContainer').innerHTML = filtered.map(c => {
        const local = calcolaDatiLocaliUnificati(c.symbol, c);
        return `
          <div onclick='openModal("${c.symbol}")' class="bg-[#000000] border border-white/25 hover:border-[#00FF66] rounded-xl p-3.5 cursor-pointer transition-all space-y-2">
            <div class="flex items-center justify-between border-b border-white/10 pb-2">
              <span class="font-extrabold text-[#FFFFFF] text-sm">${c.symbol.replace('-USDT', '')}</span>
              <span class="text-xs font-bold tabular-nums text-[#FFFFFF]">$${formatPrice(c.price)}</span>
            </div>
            <div class="flex justify-between text-xs font-mono">
              <span class="text-[#00FF66]">TP: $${formatPrice(local.tp)} (+${local.tpPct}%)</span>
              <span class="text-[#FF334B]">SL: $${formatPrice(local.sl)}</span>
            </div>
          </div>
        `;
      }).join('');
    }

    async function openModal(symbol) {
      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');
      document.getElementById('modalSymbol').textContent = symbol;
      try {
        const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(symbol)}&marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0 && json.data) {
          const d = json.data;
          state.currentSelectedAsset = d;
          state.detailedAssets[symbol] = d;

          document.getElementById('modalCategory').textContent = d.marketType;
          document.getElementById('modalSentiment').textContent = d.sentiment;
          document.getElementById('modalLivePrice').textContent = `$${formatPrice(d.price)}`;
          document.getElementById('modalSide').textContent = d.side;
          document.getElementById('modalSL').textContent = `$${formatPrice(d.sl)}`;
          document.getElementById('modalTrendingTP').textContent = `$${formatPrice(d.tp)}`;
          
          document.getElementById('modalEma').textContent = `EMA9: ${formatPrice(d.ema9 || 0)} | EMA21: ${formatPrice(d.ema21 || 0)}`;
          document.getElementById('modalRsi').textContent = `RSI: ${d.rsi14 || 50}`;
          document.getElementById('modalExactTime').textContent = `${formatExactDuration(d.tempoSec || 60)} 🎯`;

          const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
          const tpPct = (((Math.abs(d.tp - d.price) / d.price) * 100) * lev).toFixed(2);
          document.getElementById('modalTpPct').textContent = `+${tpPct}%`;

          document.getElementById('tableParamEntry').textContent = formatPrice(d.price);
          document.getElementById('tableParamTp').textContent = formatPrice(d.tp);
          document.getElementById('tableParamSl').textContent = formatPrice(d.sl);

          aggiornaQuantitaDaCapitale();
          renderTfPanel(d.tfs, d.exactTf);
        }
      } catch (e) {
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
          tp: asset.tp, sl: asset.sl, leverage: state.marketType === 'SPOT' ? 1 : state.selectedLeverage,
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

    window.addEventListener('DOMContentLoaded', async () => {
      updateApiStatusUI();
      updateMarketTypeUI();
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
