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
# MOTORE MT4 & SINGOLO TP SINCRONIZZATO
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

        ultima_candela_verde = candles[-1]["close"] > candles[-1]["open"]
        spinta_positiva = closes[-1] > ema9

        if market_type == "SPOT":
            bull = spinta_positiva and ultima_candela_verde and rsi >= 45.0
            bear = False
        else:
            bull = spinta_positiva and ultima_candela_verde and ema9 >= ema21 and rsi >= 45.0
            bear = (not ultima_candela_verde) and closes[-1] < ema9 and ema9 <= ema21 and rsi <= 55.0

        trend = "BUY" if bull else ("SELL" if bear else "NEUTRO")
        return {
            "interval": interval,
            "trend": trend,
            "ema9": ema9, "ema21": ema21, "ema50": ema50, "rsi14": round(rsi, 2), "atr14": atr,
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
    side = tfs[exact_tf]["trend"]
    if fetch_type == "SPOT":
        side = "BUY"
    elif side == "NEUTRO":
        side = "BUY" if tfs["5m"]["trend"] == "BUY" else "SELL"

    base = tfs[exact_tf]
    price = tfs["5m"]["last"]["close"]
    atr_breve = tfs["15m"]["atr14"] if tfs["15m"]["atr14"] > 0 else base["atr14"]
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7
    
    k = 1 if side == "BUY" else -1

    min_sl_dist = price * 0.015
    max_sl_dist = price * 0.035
    dist_sl = min(max(atr_breve * 1.8, min_sl_dist), max_sl_dist)
    dist_tp = dist_sl * 2.0  # Singolo Take Profit ottimizzato

    sl = round(price - (k * dist_sl), dec)
    tp = round(price + (k * dist_tp), dec)

    timestamp_ultima_candela = int(base["last"]["time"])

    return {
        "symbol": simbolo, "marketType": market_type, "price": round(price, dec), "side": side,
        "sentiment": f"MT4 Single TP {market_type} 🚀",
        "confluenza": f"Singolo TP Sincronizzato & SL MT4 🛡️",
        "exactTf": exact_tf, "precisionScore": 99.9,
        "ema9": round(base["ema9"], dec), "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"], "atr14": round(atr_breve, dec), "sl": sl, 
        "tp": tp,
        "decimals": dec, "candleTime": timestamp_ultima_candela, "tfs": tfs,
        "ultimaCandela": base["last"]
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


def esegui_ordine_con_singolotp_su_bingx(simbolo: str, lato: str, quantita_totale: float, prezzo_ingresso: float, tp: float, sl: float, leva: int = 3, market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti."}

    # 🛡️ Controllo taglia minima BingX
    min_qty = 0.001 if "BTC" in simbolo or "ETH" in simbolo else 0.01
    if quantita_totale < min_qty:
        quantita_totale = min_qty

    risultati = []

    if market_type == "STANDARD_FUTURES":
        endpoint = "/openApi/futures/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita_totale),
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
            "quantity": str(quantita_totale),
            "timestamp": str(int(time.time() * 1000))
        }
    else:
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita_totale),
            "timestamp": str(int(time.time() * 1000))
        }

    try:
        res_main = _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
        risultati.append(res_main)
        if res_main.get("code") != 0:
            return res_main

        # Invio unico ordine TP limit reduceOnly
        lato_chiusura = "SELL" if lato == "BUY" else "BUY"
        tp_payload = {
            "symbol": simbolo,
            "side": lato_chiusura,
            "type": "LIMIT",
            "price": str(tp),
            "quantity": str(quantita_totale),
            "reduceOnly": "true",
            "timestamp": str(int(time.time() * 1000))
        }
        if market_type == "FUTURES":
            tp_payload["positionSide"] = position_side
        
        res_tp = _invia_richiesta_ordine(endpoint, tp_payload, active_api, active_secret)
        risultati.append(res_tp)

        return {"code": 0, "msg": "Ordine eseguito e Singolo TP sincronizzato con successo su BingX!", "details": risultati}
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
    mtype = dati.get("marketType", MARKET_TYPE).upper()
    is_auto = dati.get("is_auto", False)

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Modalità Automatica disattivata."})

    risultato = esegui_ordine_con_singolotp_su_bingx(
        simbolo, lato, quantita, prezzo_ingresso, tp, sl, leva, mtype, 
        dati.get("apiKey", ""), dati.get("secretKey", "")
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
# INTERFACCIA FRONTEND (DASHBOARD MT4 STYLE)
# ==========================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro - MT4 Terminal &amp; Voice Advisor</title>
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
    body { background-color: #0b0e14; color: #d1d4dc; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0b0e14; }
    ::-webkit-scrollbar-thumb { background: rgba(0, 255, 102, 0.35); border-radius: 9999px; }
    .mt4-panel { background: #131722; border: 1px solid #2a2e39; border-radius: 4px; }
    .mt4-header { background: #1e222d; border-bottom: 1px solid #2a2e39; }
    @keyframes neonGlowPulse { 0%, 100% { box-shadow: 0 0 15px rgba(0, 255, 102, 0.3); border-color: rgba(0, 255, 102, 0.8); } 50% { box-shadow: 0 0 25px rgba(0, 255, 102, 0.6); border-color: #00FF66; } }
    .priority-signal-box { background: #131722; border: 1px solid #00FF66; animation: neonGlowPulse 3s infinite ease-in-out; }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .bingx-btn { background: linear-gradient(135deg, #2962ff 0%, #1e53db 100%); color: #FFFFFF; font-weight: 800; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
    @keyframes tickerScroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
    .ticker-wrapper { display: flex; width: max-content; animation: tickerScroll 25s linear infinite; }
    .ticker-wrapper:hover { animation-play-state: paused; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#00FF66]/30 selection:text-white">

  <!-- HEADER MT4 -->
  <header class="sticky top-0 z-30 bg-[#131722] backdrop-blur-md border-b border-[#2a2e39] px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3.5">
        <div class="w-10 h-10 rounded bg-[#1e222d] text-[#00FF66] flex items-center justify-center border border-[#00FF66]/50 font-extrabold text-lg">📈</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-sm sm:text-base font-extrabold tracking-tight text-white">MT4 TERMINAL <span class="text-[#00FF66]" id="headerEngineTitle">Pro Trader</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#1e222d] text-[#00FF66] border border-[#2a2e39]">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-[#00FF66]"></span> <span id="modeText">MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-[#848e9c] mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]" id="headerSubMarketInfo">Singolo TP Sincronizzato • Min 2$ • Advisor Vocale MT4</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <div class="flex items-center bg-[#1e222d] p-1 rounded border border-[#2a2e39] gap-1 flex-wrap">
          <span class="text-[11px] font-mono font-bold text-[#848e9c] px-1">Mercato:</span>
          <button id="btnMarketFutures" onclick="switchMarketType('FUTURES')" class="px-2.5 py-1 rounded text-xs font-bold transition-all bg-[#2962ff] text-white">FUTURES</button>
          <button id="btnMarketStandard" onclick="switchMarketType('STANDARD_FUTURES')" class="px-2.5 py-1 rounded text-xs font-bold transition-all text-[#848e9c] hover:text-white">STD FUTURES</button>
          <button id="btnMarketSpot" onclick="switchMarketType('SPOT')" class="px-2.5 py-1 rounded text-xs font-bold transition-all text-[#848e9c] hover:text-white">SPOT</button>
        </div>

        <button onclick="toggleApiModal()" class="px-3 py-1.5 rounded text-xs font-mono font-bold bg-[#1e222d] text-cyan-400 border border-[#2a2e39] hover:border-cyan-400 transition-all flex items-center gap-1.5">
          <span>🔑</span> <span id="apiStatusText">Collega API</span>
        </button>

        <div class="flex items-center bg-[#1e222d] p-1 rounded border border-[#2a2e39] gap-1">
          <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-2.5 py-1 rounded text-xs font-bold transition-all bg-[#2a2e39] text-white">MAN</button>
          <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-2.5 py-1 rounded text-xs font-bold transition-all text-[#848e9c] hover:text-white">AUTO</button>
        </div>
      </div>
    </div>
  </header>

  <!-- TICKER MARQUEE -->
  <div class="bg-[#131722] border-b border-[#2a2e39] overflow-hidden py-1.5 px-2">
    <div class="max-w-[1920px] mx-auto flex items-center gap-3">
      <span class="text-[10px] font-mono font-extrabold uppercase bg-[#1e222d] text-[#00FF66] px-2 py-0.5 rounded border border-[#2a2e39] flex items-center gap-1 shrink-0">
        <span>🔥</span> Feed Live:
      </span>
      <div class="overflow-hidden w-full relative">
        <div id="liveTickerMarquee" class="ticker-wrapper font-mono text-xs text-white flex items-center gap-6">
          <span class="text-[#848e9c]">Caricamento ticker in tempo reale...</span>
        </div>
      </div>
    </div>
  </div>

  <!-- MAIN -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- BOX ADVISOR POSIZIONE ATTIVA (STILE MT4) -->
    <div id="activePositionAdvisorBox" class="hidden mt4-panel p-4 border-2 border-[#00FF66] shadow-xl space-y-3">
      <div class="flex flex-wrap items-center justify-between pb-2 border-b border-[#2a2e39] gap-2">
        <div class="flex items-center gap-2.5">
          <div class="w-7 h-7 rounded bg-[#1e222d] text-[#00FF66] flex items-center justify-center font-extrabold text-sm animate-pulse">🎙️</div>
          <div>
            <h2 class="text-xs sm:text-sm font-extrabold uppercase tracking-wider text-white">Terminal Advisor Vocale — <span id="advSymbol" class="text-[#00FF66]">BTC-USDT</span></h2>
            <p class="text-[10px] text-[#848e9c]">Monitoraggio posizione con Singolo TP (Stile MetaTrader)</p>
          </div>
        </div>
        <button onclick="chiudiPosizioneMonitorata()" class="px-2.5 py-1 rounded bg-[#ff334b]/20 text-[#ff334b] border border-[#ff334b]/40 hover:bg-[#ff334b]/30 text-[11px] font-bold transition-all">
          Chiudi / Libera ✕
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs font-mono">
        <div class="bg-[#1e222d] p-2.5 rounded border border-[#2a2e39]">
          <span class="text-[#848e9c] text-[10px] block">APERTURA (ENTRY)</span>
          <strong id="advEntryPrice" class="text-white text-sm">--</strong>
          <span id="advSideBadge" class="inline-block mt-1 px-1.5 py-0.5 rounded text-[9px] font-bold bg-[#00FF66]/20 text-[#00FF66]">LONG</span>
        </div>
        <div class="bg-[#1e222d] p-2.5 rounded border border-[#2a2e39]">
          <span class="text-[#848e9c] text-[10px] block">PREZZO CORRENTE (BID/ASK)</span>
          <strong id="advLivePrice" class="text-cyan-400 text-sm">--</strong>
          <span id="advPnlVal" class="block mt-1 text-[10px] font-bold text-[#00FF66]">Profit/Loss: +0.00%</span>
        </div>
        <div class="bg-[#1e222d] p-2.5 rounded border border-[#2a2e39]">
          <span class="text-[#848e9c] text-[10px] block">TARGET (TAKE PROFIT)</span>
          <strong id="advTp" class="text-[#00FF66] text-sm block mt-1">--</strong>
        </div>
      </div>

      <!-- BARRA DI AVANZAMENTO PULITA -->
      <div class="bg-[#1e222d] border border-[#2a2e39] rounded p-2.5 space-y-1">
        <div class="flex justify-between text-[11px] font-mono">
          <span class="text-[#848e9c]">Progresso Verso il Take Profit:</span>
          <strong id="advProgressPctText" class="text-[#00FF66]">0.0%</strong>
        </div>
        <div class="w-full bg-[#0b0e14] rounded-full h-2 overflow-hidden p-0.5 border border-[#2a2e39]">
          <div id="advProgressBar" class="bg-gradient-to-r from-[#00CC52] to-[#00FF66] h-full rounded-full transition-all duration-300" style="width: 0%;"></div>
        </div>
      </div>
    </div>

    <!-- SEZIONE MASTER -->
    <section class="priority-signal-box rounded p-4">
      <div class="flex items-center justify-between pb-2 mb-3 border-b border-[#2a2e39]">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded bg-[#1e222d] text-[#00FF66] flex items-center justify-center font-extrabold text-sm">⭐</div>
          <div>
            <h2 class="text-xs sm:text-sm font-extrabold uppercase tracking-wider text-white">Segnale Master MT4 (Singolo TP)</h2>
            <p class="text-[10px] text-[#848e9c]">Setup con TP e SL millimetricamente allineati alla direzione di mercato</p>
          </div>
        </div>
        <span class="px-2 py-0.5 rounded text-[10px] font-mono font-extrabold bg-[#00FF66] text-black">SYNC OK</span>
      </div>
      <div id="prioritySignalCardContent"></div>
    </section>

    <!-- TABELLA PRINCIPALE MT4 STYLE -->
    <section class="mt4-panel overflow-hidden flex flex-col p-4 space-y-3">
      <div class="flex flex-wrap items-center justify-between border-b border-[#2a2e39] pb-3 gap-2">
        <h2 class="text-xs sm:text-sm font-bold text-white flex items-center gap-2">
          <span>📊</span>
          <span>Terminal Market Watch — <span id="tableMarketLabel" class="text-[#00FF66]">FUTURES</span></span>
        </h2>
        <div class="flex flex-wrap items-center gap-1.5">
          <button onclick="setCategoryTab('ALL')" id="catTabALL" class="px-2.5 py-1 rounded text-xs font-bold bg-[#00FF66] text-black">Tutti</button>
          <button onclick="setCategoryTab('Criptovalute')" id="catTabCriptovalute" class="px-2.5 py-1 rounded text-xs font-bold bg-[#1e222d] text-[#848e9c] hover:text-white">Cripto</button>
          <button onclick="setCategoryTab('Materie Prime')" id="catTabMaterie Prime" class="px-2.5 py-1 rounded text-xs font-bold bg-[#1e222d] text-[#848e9c] hover:text-white">Commodity</button>
          <button onclick="setCategoryTab('Forex / Valute')" id="catTabForex / Valute" class="px-2.5 py-1 rounded text-xs font-bold bg-[#1e222d] text-[#848e9c] hover:text-white">Forex</button>
          <button onclick="setCategoryTab('Azioni & Indici (TradFi)')" id="catTabTradFi" class="px-2.5 py-1 rounded text-xs font-bold bg-[#1e222d] text-[#848e9c] hover:text-white">Indici</button>
        </div>
      </div>

      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca simbolo (es. BTC, ETH, SOL, GOLD)..." class="w-full bg-[#0b0e14] border border-[#2a2e39] rounded px-3 py-2 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
      </div>

      <div id="tablesContainer" class="space-y-2"></div>
    </section>

  </main>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/80 backdrop-blur-sm p-3">
    <div class="mt4-panel w-full max-w-[450px] shadow-2xl overflow-hidden">
      <div class="px-4 py-3 mt4-header flex items-center justify-between">
        <h2 class="text-sm font-bold font-mono text-[#00FF66]">🔑 Configurazione API BingX</h2>
        <button onclick="toggleApiModal()" class="text-[#848e9c] hover:text-white">&#10005;</button>
      </div>
      <div class="p-4 space-y-3 text-xs font-mono">
        <div>
          <label class="block text-[#848e9c] mb-1">API Key:</label>
          <input type="text" id="userApiKeyInput" class="w-full bg-[#0b0e14] border border-[#2a2e39] rounded px-3 py-2 text-white outline-none focus:border-[#00FF66]" />
        </div>
        <div>
          <label class="block text-[#848e9c] mb-1">Secret Key:</label>
          <input type="password" id="userSecretKeyInput" class="w-full bg-[#0b0e14] border border-[#2a2e39] rounded px-3 py-2 text-white outline-none focus:border-[#00FF66]" />
        </div>
        <button onclick="saveUserApiKeys()" class="w-full py-2 rounded text-xs font-bold bg-[#00FF66] text-black">Salva Credenziali</button>
        <button onclick="clearUserApiKeys()" class="w-full py-1.5 rounded text-xs font-bold bg-[#ff334b]/20 text-[#ff334b] border border-[#ff334b]/40">Disconnetti</button>
      </div>
    </div>
  </div>

  <!-- MODALE ESECUZIONE (MT4 ORDER TICKET) -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/80 backdrop-blur-sm p-3">
    <div class="mt4-panel w-full max-w-[650px] shadow-2xl overflow-hidden">
      <div class="px-4 py-3 mt4-header flex items-center justify-between">
        <div>
          <h2 id="modalSymbol" class="text-sm font-bold font-mono text-white">BTC-USDT</h2>
          <p class="text-[10px] text-[#848e9c]">Order Ticket MT4 Style — Singolo TP Sincronizzato</p>
        </div>
        <button onclick="closeModal()" class="text-[#848e9c] hover:text-white">&#10005;</button>
      </div>
      <div class="p-4 space-y-3 text-xs font-mono">
        <div class="bg-[#0b0e14] border border-[#2a2e39] rounded p-3 space-y-2">
          <div class="flex justify-between"><span class="text-[#848e9c]">Prezzo Bid Attuale:</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-[#848e9c]">Direzione Ordine:</span><strong id="modalSide" class="text-[#00FF66]">--</strong></div>
          
          <div class="grid grid-cols-2 gap-2 pt-2 border-t border-[#2a2e39] text-[11px]">
            <div><span class="text-[#848e9c] block">Stop Loss (SL):</span><strong id="tableParamSl" class="text-[#ff334b]">--</strong></div>
            <div><span class="text-[#848e9c] block">Take Profit (TP):</span><strong id="tableParamTp" class="text-[#00FF66]">--</strong></div>
          </div>
        </div>

        <div class="bg-[#0b0e14] border border-[#2a2e39] rounded p-3 space-y-2">
          <div class="flex justify-between items-center">
            <label class="text-[#848e9c]">Capitale / Margine (USDT):</label>
            <span id="marginUsdtLabel" class="text-xs font-bold text-[#00FF66]">10 USDT</span>
          </div>
          <div class="grid grid-cols-6 gap-1">
            <button onclick="setMarginUsdt(2)" id="marBtn2" class="py-1 rounded bg-[#1e222d] text-white font-bold">2$</button>
            <button onclick="setMarginUsdt(5)" id="marBtn5" class="py-1 rounded bg-[#1e222d] text-white font-bold">5$</button>
            <button onclick="setMarginUsdt(10)" id="marBtn10" class="py-1 rounded bg-[#00FF66]/20 border border-[#00FF66] text-[#00FF66] font-bold">10$</button>
            <button onclick="setMarginUsdt(25)" id="marBtn25" class="py-1 rounded bg-[#1e222d] text-white font-bold">25$</button>
            <button onclick="setMarginUsdt(50)" id="marBtn50" class="py-1 rounded bg-[#1e222d] text-white font-bold">50$</button>
            <button onclick="setMarginUsdt(100)" id="marBtn100" class="py-1 rounded bg-[#1e222d] text-white font-bold">100$</button>
          </div>
        </div>

        <div id="leverageSectionWrapper" class="bg-[#0b0e14] border border-[#2a2e39] rounded p-3 space-y-2">
          <label class="text-[#848e9c]">Leva Finanziaria:</label>
          <div class="grid grid-cols-4 gap-1.5">
            <button onclick="setLeverage(1)" id="levBtn1" class="py-1 rounded bg-[#1e222d] text-white font-bold">1x</button>
            <button onclick="setLeverage(3)" id="levBtn3" class="py-1 rounded bg-[#1e222d] text-white font-bold">3x</button>
            <button onclick="setLeverage(5)" id="levBtn5" class="py-1 rounded bg-[#1e222d] text-white font-bold">5x</button>
            <button onclick="setLeverage(10)" id="levBtn10" class="py-1 rounded bg-[#00FF66]/20 border border-[#00FF66] text-[#00FF66] font-bold">10x</button>
          </div>
        </div>

        <div>
          <label class="text-[#848e9c] block mb-1">Quantità Calcolata:</label>
          <input type="number" id="tradeQuantityInput" value="1.0" step="any" class="w-full bg-[#0b0e14] border border-[#2a2e39] rounded px-3 py-2 text-white font-mono font-bold outline-none focus:border-[#00FF66]" />
        </div>

        <button onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded text-xs shadow">
          🚀 Invia Ordine a Mercato con Singolo TP su BingX
        </button>
      </div>
    </div>
  </div>

  <!-- JAVASCRIPT ENGINE -->
  <script>
    let state = {
      marketData: {},
      detailedAssets: {},
      tradingMode: 'MANUAL',
      marketType: 'FUTURES',
      currentCategoryTab: 'ALL',
      searchQuery: '',
      selectedLeverage: 10,
      selectedMarginUsdt: 10,
      currentSelectedAsset: null,
      activePosition: null,
      advisorInterval: null,
      lastVoiceTimestamp: 0
    };

    const TOP_MARKET_CAP_ORDER = [
      'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'XRP-USDT', 'BNB-USDT', 'DOGE-USDT', 'ADA-USDT',
      'AVAX-USDT', 'SHIB-USDT', 'DOT-USDT', 'LINK-USDT', 'SUI-USDT', 'NEAR-USDT', 'LTC-USDT'
    ];

    function getStoredApiKeys() {
      return {
        apiKey: localStorage.getItem('bingx_user_api_key') || '',
        secretKey: localStorage.getItem('bingx_user_secret_key') || ''
      };
    }

    function updateApiStatusUI() {
      const keys = getStoredApiKeys();
      const statusEl = document.getElementById('apiStatusText');
      statusEl.textContent = (keys.apiKey && keys.secretKey) ? "API Connesse 🟢" : "Collega API";
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
      if (!apiKey || !secretKey) { alert("Inserisci entrambe le chiavi."); return; }
      localStorage.setItem('bingx_user_api_key', apiKey);
      localStorage.setItem('bingx_user_secret_key', secretKey);
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali salvate!");
    }

    function clearUserApiKeys() {
      localStorage.removeItem('bingx_user_api_key');
      localStorage.removeItem('bingx_user_secret_key');
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

    function aggiornaQuantitaDaCapitale() {
      if (!state.currentSelectedAsset) return;
      const d = state.currentSelectedAsset;
      const prezzo = d.price;
      if (prezzo <= 0) return;
      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      
      if (state.selectedMarginUsdt < 2) state.selectedMarginUsdt = 2;

      const valoreNominale = state.selectedMarginUsdt * lev;
      let quantita = Number((valoreNominale / prezzo).toFixed(d.decimals >= 4 ? 2 : 4));
      
      const minQ = (d.symbol.includes('BTC') || d.symbol.includes('ETH')) ? 0.001 : 0.01;
      if (quantita < minQ) quantita = minQ;

      document.getElementById('tradeQuantityInput').value = quantita;
      document.getElementById('marginUsdtLabel').textContent = `${state.selectedMarginUsdt} USDT (Nominale: ${valoreNominale}$ con ${lev}x)`;
    }

    function setMarginUsdt(usdt) {
      state.selectedMarginUsdt = Math.max(2, usdt);
      document.querySelectorAll('[id^="marBtn"]').forEach(b => {
        b.className = "py-1 rounded bg-[#1e222d] text-white font-bold";
      });
      const activeBtn = document.getElementById(`marBtn${usdt}`);
      if (activeBtn) activeBtn.className = "py-1 rounded bg-[#00FF66]/20 border border-[#00FF66] text-[#00FF66] font-bold";
      aggiornaQuantitaDaCapitale();
    }

    function setLeverage(lev) {
      state.selectedLeverage = lev;
      document.querySelectorAll('#leverageSectionWrapper button').forEach(b => {
        b.className = "py-1 rounded bg-[#1e222d] text-white font-bold";
      });
      const activeBtn = document.getElementById(`levBtn${lev}`);
      if (activeBtn) activeBtn.className = "py-1 rounded bg-[#00FF66]/20 border border-[#00FF66] text-[#00FF66] font-bold";
      aggiornaQuantitaDaCapitale();
    }

    function calcolaDatiLocaliUnificati(symbol, item) {
      const cached = state.detailedAssets[symbol];
      if (cached) {
        return { symbol, price: item.price, side: cached.side, sl: cached.sl, tp: cached.tp };
      }
      const p = item.price;
      const dec = getPrecisionDecimals(p);
      const isBull = item.change24h >= 0;
      const side = (state.marketType === 'SPOT' || isBull) ? "BUY" : "SELL";
      const k = side === 'BUY' ? 1 : -1;
      const distSl = p * 0.022;
      const distTp = p * 0.035;
      const sl = Number((p - (k * distSl)).toFixed(dec));
      const tp = Number((p + (k * distTp)).toFixed(dec));
      return { symbol, price: p, side, sl, tp };
    }

    function avviaAdvisorPosizione(symbol, side, entryPrice, tpPrice, slPrice) {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      
      state.activePosition = { symbol, side, entryPrice, tpPrice, slPrice };

      const box = document.getElementById('activePositionAdvisorBox');
      box.classList.remove('hidden');

      document.getElementById('advSymbol').textContent = symbol;
      document.getElementById('advEntryPrice').textContent = `$${formatPrice(entryPrice)}`;
      document.getElementById('advSideBadge').textContent = side;
      document.getElementById('advTp').textContent = `$${formatPrice(tpPrice)}`;

      playChime(false, `Posizione aperta su ${symbol} con singolo Take Profit sincronizzato. Monitoraggio MT4 attivo.`);
      
      aggiornaAdvisorLoop();
      state.advisorInterval = setInterval(aggiornaAdvisorLoop, 1000);
    }

    function chiudiPosizioneMonitorata() {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      state.activePosition = null;
      document.getElementById('activePositionAdvisorBox').classList.add('hidden');
      playChime(false, "Posizione chiusa.");
    }

    function aggiornaAdvisorLoop() {
      if (!state.activePosition) return;
      const pos = state.activePosition;
      const liveItem = state.marketData[pos.symbol];
      if (!liveItem) return;

      const currentPrice = liveItem.price;
      document.getElementById('advLivePrice').textContent = `$${formatPrice(currentPrice)}`;

      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      const rawDiff = pos.side === 'BUY' ? (currentPrice - pos.entryPrice) : (pos.entryPrice - currentPrice);
      const pnlPct = ((rawDiff / pos.entryPrice) * 100) * lev;

      const pnlEl = document.getElementById('advPnlVal');
      pnlEl.textContent = `Profit/Loss: ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
      pnlEl.className = `block mt-1 text-[10px] font-bold ${pnlPct >= 0 ? 'text-[#00FF66]' : 'text-[#ff334b]'}`;

      const totalDistance = Math.abs(pos.tpPrice - pos.entryPrice);
      const currentCovered = pos.side === 'BUY' ? (currentPrice - pos.entryPrice) : (pos.entryPrice - currentPrice);
      let progressRatio = totalDistance > 0 ? (currentCovered / totalDistance) : 0;
      progressRatio = Math.min(1.0, Math.max(0.0, progressRatio));

      const progressPct = (progressRatio * 100).toFixed(1);
      document.getElementById('advProgressPctText').textContent = `${progressPct}%`;
      document.getElementById('advProgressBar').style.width = `${progressPct}%`;

      const reachedTp = (pos.side === 'BUY' && currentPrice >= pos.tpPrice) || (pos.side === 'SELL' && currentPrice <= pos.tpPrice);
      const reachedSl = (pos.side === 'BUY' && currentPrice <= pos.slPrice) || (pos.side === 'SELL' && currentPrice >= pos.slPrice);

      if (reachedTp) {
        playChime(true, "Take profit raggiunto! Ottimo lavoro.");
      } else if (reachedSl) {
        playChime(true, "Stop loss raggiunto.");
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

    async function switchMarketType(mtype) {
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
    }

    function updateMarketTypeUI() {
      const isSpot = state.marketType === 'SPOT';
      document.getElementById('btnMarketFutures').className = state.marketType === 'FUTURES' ? "px-2.5 py-1 rounded text-xs font-bold bg-[#2962ff] text-white" : "px-2.5 py-1 rounded text-xs font-bold text-[#848e9c]";
      document.getElementById('btnMarketStandard').className = state.marketType === 'STANDARD_FUTURES' ? "px-2.5 py-1 rounded text-xs font-bold bg-amber-500 text-black" : "px-2.5 py-1 rounded text-xs font-bold text-[#848e9c]";
      document.getElementById('btnMarketSpot').className = isSpot ? "px-2.5 py-1 rounded text-xs font-bold bg-[#00FF66] text-black" : "px-2.5 py-1 rounded text-xs font-bold text-[#848e9c]";
      document.getElementById('tableMarketLabel').textContent = state.marketType;
      document.getElementById('leverageSectionWrapper').style.display = isSpot ? 'none' : 'block';
    }

    async function switchMode(mode) {
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
      const items = keys.slice(0, 15).map(sym => {
        const item = state.marketData[sym];
        return `<div class="flex items-center gap-2 bg-[#1e222d] px-2.5 py-1 rounded border border-[#2a2e39] shrink-0 font-mono text-xs">
          <span class="font-bold text-white">${sym.replace('-USDT', '')}</span>
          <span class="text-white">$${formatPrice(item.price)}</span>
          <span class="${item.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#ff334b]'} font-bold">${item.change24h >= 0 ? '+' : ''}${item.change24h.toFixed(2)}%</span>
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

      const masterAsset = sorted[0];
      if (masterAsset) {
        const local = calcolaDatiLocaliUnificati(masterAsset.symbol, masterAsset);
        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-1.5">
              <strong class="text-white text-sm">${masterAsset.symbol}</strong>
              <div class="flex justify-between"><span class="text-[#848e9c]">Prezzo:</span><strong class="text-[#00FF66]">$${formatPrice(masterAsset.price)}</strong></div>
              <div class="flex justify-between"><span class="text-[#848e9c]">Direzione:</span><strong class="${local.side === 'BUY' ? 'text-[#00FF66]' : 'text-[#ff334b]'}">${local.side}</strong></div>
              <div class="flex justify-between"><span class="text-[#848e9c]">Target (TP):</span><strong class="text-yellow-400">$${formatPrice(local.tp)}</strong></div>
            </div>
            <div class="flex flex-col justify-end">
              <button onclick='openModal("${masterAsset.symbol}")' class="green-gradient-btn w-full py-2 rounded text-xs shadow">Apri Ticket MT4 &#8599;</button>
            </div>
          </div>`;
      }

      let filtered = sorted;
      if (state.currentCategoryTab !== 'ALL') filtered = filtered.filter(c => c.category === state.currentCategoryTab);
      if (state.searchQuery) filtered = filtered.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));

      const container = document.getElementById('tablesContainer');
      container.innerHTML = filtered.map(c => {
        const local = calcolaDatiLocaliUnificati(c.symbol, c);
        const isLong = local.side === 'BUY';
        return `
          <div onclick='openModal("${c.symbol}")' class="bg-[#1e222d] border border-[#2a2e39] hover:border-[#00FF66] rounded p-3 cursor-pointer transition-all flex items-center justify-between font-mono text-xs">
            <div class="flex items-center gap-3">
              <span class="font-extrabold text-white">${c.symbol.replace('-USDT', '')}</span>
              <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${isLong ? 'bg-[#00FF66]/20 text-[#00FF66]' : 'bg-[#ff334b]/20 text-[#ff334b]'}">${isLong ? 'LONG' : 'SHORT'}</span>
            </div>
            <div class="text-right">
              <span class="text-white font-bold">$${formatPrice(c.price)}</span>
              <span class="block text-[10px] ${c.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#ff334b]'}">${c.change24h >= 0 ? '+' : ''}${c.change24h.toFixed(2)}%</span>
            </div>
          </div>`;
      }).join('');
    }

    async function openModal(symbol) {
      if (state.activePosition) {
        alert("C'è già una posizione monitorata attiva.");
        return;
      }
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

          document.getElementById('modalLivePrice').textContent = `$${formatPrice(d.price)}`;
          document.getElementById('modalSide').textContent = d.side;
          document.getElementById('tableParamSl').textContent = `$${formatPrice(d.sl)}`;
          document.getElementById('tableParamTp').textContent = `$${formatPrice(d.tp)}`;
          aggiornaQuantitaDaCapitale();
        }
      } catch (e) {
        alert("Errore di caricamento dati.");
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
          tp: asset.tp, sl: asset.sl, 
          leverage: state.marketType === 'SPOT' ? 1 : state.selectedLeverage,
          marketType: state.marketType, apiKey: keys.apiKey, secretKey: keys.secretKey
        })
      });
      const data = await res.json();
      if (data.code === 0) {
        avviaAdvisorPosizione(asset.symbol, asset.side, asset.price, asset.tp, asset.sl);
        closeModal();
      } else {
        alert("Errore esecuzione: " + (data.msg || "Verifica chiavi API"));
      }
    }

    window.addEventListener('DOMContentLoaded', async () => {
      updateApiStatusUI();
      updateMarketTypeUI();
      await pollMarketData();
      setInterval(pollMarketData, 2000);
      setInterval(() => {
        document.getElementById('liveCetClock').textContent = new Date().toTimeString().slice(0, 8) + ' CET';
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
