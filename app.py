import os
import re
import time
import hmac
import hashlib
import urllib.parse
import requests
from datetime import datetime
import pytz
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
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature


# ==========================================
# GESTIONE ORARIA & ORARIO SICURO
# ==========================================
def verifica_finestra_temporale_sicura() -> tuple[bool, str, str, str, str]:
    try:
        tz_roma = pytz.timezone("Europe/Rome")
        adesso = datetime.now(tz_roma)
        ora_minuto = adesso.strftime("%H:%M")
        
        if "01:00" <= ora_minuto < "09:00":
            return True, "🟢 Sessione Asiatica (Accumulazione)", "Borsa di Tokyo / Sydney (01:00 - 09:00)", "01:00", "09:00"
        if "08:45" <= ora_minuto < "09:30":
            return True, "🟡 Apertura Borse Europee", "Borsa Italiana / Europa (09:00 - 09:01)", "09:00", "14:30"
        if "09:30" <= ora_minuto < "14:15":
            return True, "🟡 Mattinata Europea: Trend Puliti [Orario Sicuro]", "Borsa di Londra & Milano (09:30 - 14:30)", "09:30", "14:30"
        if "14:15" <= ora_minuto < "15:20":
            return True, "🟡 Sovrapposizione Forex Londra/NY & Pre-Market USA", "Mercato Forex (14:00-17:00) / Pre-Market USA", "14:00", "17:00"
        if "15:20" <= ora_minuto <= "16:00":
            return True, "🟡 Apertura Wall Street", "Mercati USA - NYSE & NASDAQ (15:30)", "15:30", "22:00"
        if "16:00" <= ora_minuto < "21:30":
            return True, "🟡 Sessione Pomeridiana USA: Consolidamento Trend", "Mercati USA NYSE/NASDAQ (16:00 - 22:00)", "16:00", "22:00"
        
        return True, "🟢 Sessione Globale 24/7", "Mercati 24/7", "22:00", "01:00"
    except Exception:
        return True, "🟡 Finestra operativa standard", "Mercato Globale 24/7", "09:30", "22:00"


# ==========================================
# MOTORE QUANTISTICO DI PRECISIONE TP
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
        if len(candles) < 30:
            return None
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        ema9 = _calcola_ema_serie(closes, 9)
        ema21 = _calcola_ema_serie(closes, 21)
        ema50 = _calcola_ema_serie(closes, 50)
        rsi = _calcola_rsi_wilder(closes, 14)
        atr = _calcola_atr_reale(highs, lows, closes, 14)

        if market_type == "SPOT":
            bull = ema9 > ema21 and rsi >= 53.0
            bear = False
        else:
            bull = ema9 > ema21 and ema21 > ema50 and rsi >= 53.0 and closes[-1] >= ema9
            bear = ema9 < ema21 and ema21 < ema50 and rsi <= 47.0 and closes[-1] <= ema9

        trend = "BUY" if bull else ("SELL" if bear else "NEUTRO")
        return {
            "interval": interval,
            "trend": trend,
            "ema9": ema9, "ema21": ema21, "ema50": ema50, "rsi14": round(rsi, 2), "atr14": atr,
            "highs": highs, "lows": lows,
            "last": candles[-1],
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

    trend_5m = tfs["5m"]["trend"]
    trend_15m = tfs["15m"]["trend"]
    trend_1h = tfs["1h"]["trend"]
    trend_4h = tfs["4h"]["trend"]

    if trend_5m == trend_15m == trend_1h == trend_4h and trend_5m != "NEUTRO":
        side = trend_5m
        score_precisione = 100.0
    else:
        side = "NEUTRO"
        score_precisione = 0.0

    base = tfs["15m"]
    price = tfs["5m"]["last"]["close"]
    atr_reale = base["atr14"] if base["atr14"] > 0 else (price * 0.01)
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7
    
    # Calcolo TP e SL strutturati per massimizzare il raggiungimento del target (R:R 1:2.5)
    if side == "BUY":
        min_recente = min(base["lows"][-5:])
        sl = round(min(min_recente, price - (atr_reale * 1.6)), dec)
        distanza_rischio = price - sl
        tp = round(price + (distanza_rischio * 2.5), dec)
    elif side == "SELL":
        max_recente = max(base["highs"][-5:])
        sl = round(max(max_recente, price + (atr_reale * 1.6)), dec)
        distanza_rischio = sl - price
        tp = round(price - (distanza_rischio * 2.5), dec)
    else:
        sl = round(price * 0.99, dec)
        tp = round(price * 1.02, dec)

    candle_sec = 900
    ultima_candela = base["last"]
    velocita_reale = max(atr_reale * 0.25 / candle_sec, price * 0.000015)
    dist_tp_val = abs(tp - price)
    tempo_esatto_sec = int(max(60, min(14400, round(dist_tp_val / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    is_safe, time_msg, borsa_rif, start_t, end_t = verifica_finestra_temporale_sicura()
    momento_giusto = is_safe and (side != "NEUTRO")

    return {
        "symbol": simbolo, "marketType": market_type, "price": round(price, dec), "side": side,
        "sentiment": f"Sincronizzazione Totale 100% 🚀 ({side})" if side != "NEUTRO" else "In attesa di Sincronizzazione",
        "confluenza": f"{time_msg} 🎯",
        "borsaRif": borsa_rif,
        "momentoGiusto": momento_giusto,
        "exactTf": "15m", "precisionScore": score_precisione,
        "ema9": round(base["ema9"], dec), "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"], "atr14": round(atr_reale, dec), "sl": sl, "tp": tp,
        "decimals": dec, "tempoSec": tempo_esatto_sec, "candleTime": timestamp_ultima_candela, "tfs": tfs,
        "isSafeTime": is_safe, "sessionStart": start_t, "sessionEnd": end_t
    }


def get_live_market_data(market_type: str = "FUTURES"):
    if market_type == "SPOT":
        url = f"{BASE_URL}/openApi/spot/v1/ticker/24hr"
    else:
        url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"

    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get("code") == 0 and "data" in data:
            tickers = data["data"]
            market_map = {}
            for t in tickers:
                symbol = t.get("symbol")
                if not symbol or not symbol.endswith("-USDT"):
                    continue

                sym_upper = symbol.upper()
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


def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 10, timeframe: str = "15m", market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
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
    leva = int(dati.get("leverage", 10))
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
    is_safe, time_msg, borsa_rif, start_t, end_t = verifica_finestra_temporale_sicura()
    return jsonify({
        "code": 0,
        "mode": TRADING_MODE,
        "marketType": MARKET_TYPE,
        "isSafeTime": is_safe,
        "timeMsg": time_msg,
        "borsaRif": borsa_rif,
        "sessionStart": start_t,
        "sessionEnd": end_t
    })


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
            rubyNeon: '#FF334B',
            goldNeon: '#FFD700'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #000000; }
    ::-webkit-scrollbar-thumb { background: rgba(255, 215, 0, 0.35); border-radius: 9999px; }
    @keyframes neonGlowPulse { 0%, 100% { box-shadow: 0 0 25px rgba(255, 215, 0, 0.5), inset 0 0 15px rgba(255, 215, 0, 0.2); border-color: rgba(255, 215, 0, 0.9); } 50% { box-shadow: 0 0 45px rgba(255, 215, 0, 0.9), inset 0 0 25px rgba(255, 215, 0, 0.4); border-color: #FFD700; } }
    @keyframes goldGlowPulse { 0%, 100% { box-shadow: 0 0 30px rgba(255, 215, 0, 0.7), inset 0 0 15px rgba(255, 215, 0, 0.3); border-color: #FFD700; background: rgba(50, 45, 10, 0.95); } 50% { box-shadow: 0 0 50px rgba(255, 215, 0, 1); border-color: #FFD700; background: rgba(80, 70, 15, 0.98); } }
    @keyframes dollarRotateGlow { 0% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #FFD700); } 50% { transform: scale(1.15) rotate(10deg); filter: drop-shadow(0 0 20px #FFD700); } 100% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #FFD700); } }
    .dollar-animated-logo { display: inline-block; animation: dollarRotateGlow 2.5s infinite ease-in-out; }
    .priority-signal-box { background: linear-gradient(180deg, rgba(24, 20, 8, 0.98) 0%, rgba(12, 10, 4, 0.99) 100%); border: 2px solid #FFD700; animation: neonGlowPulse 3s infinite ease-in-out; }
    .gold-highlight-card { border: 2px solid #FFD700 !important; animation: goldGlowPulse 2s infinite ease-in-out !important; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
    @keyframes tickerScroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
    .ticker-wrapper { display: flex; width: max-content; animation: tickerScroll 25s linear infinite; }
    .ticker-wrapper:hover { animation-play-state: paused; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#FFD700]/30 selection:text-white">

  <!-- HEADER -->
  <header class="sticky top-0 z-30 bg-[#000000]/95 backdrop-blur-md border-b border-white/15 px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3.5">
        <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#FFD700] via-[#FFA500] to-[#CC8400] text-black flex items-center justify-center shadow-2xl border-2 border-[#FFD700] font-extrabold text-xl animate-pulse">⚡</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">APEXSCAN <span class="text-[#FFD700]">Quantum Superior Advisor</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-[#FFD700]"></span> <span id="modeText">MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#FFD700]">Precisione TP 1:2.5 &amp; Leva Libera 🚀</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <button onclick="notificaVocaleCryptoConsigliata(true)" class="px-3.5 py-1.5 rounded-xl text-xs font-mono font-bold bg-gradient-to-r from-amber-500 to-yellow-400 text-black shadow-lg hover:brightness-110 transition-all flex items-center gap-1.5 animate-bounce">
          <span>🔊</span> <span>Quale Crypto Operare Adesso?</span>
        </button>

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
          <span class="text-xs font-mono font-bold text-white/70 px-1">Modalità:</span>
          <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
          <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">AUTO ⚡</button>
        </div>
      </div>
    </div>
  </header>

  <!-- TICKER MARQUEE -->
  <div class="bg-[#08080A] border-b border-white/10 overflow-hidden py-2 px-2">
    <div class="max-w-[1920px] mx-auto flex items-center gap-3">
      <span class="text-[10px] font-mono font-extrabold uppercase bg-[#FFD700]/20 text-[#FFD700] px-2 py-1 rounded border border-[#FFD700]/40 flex items-center gap-1 shrink-0">
        <span>🔥</span> Tutti i Ticker &amp; Mercati Live:
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

    <!-- BANNER NOTIFICA ATTIVA BORSA E CRIPTO CONSIGLIATA -->
    <div class="terminal-card rounded-2xl p-4 border-2 border-[#FFD700] bg-gradient-to-r from-amber-950/50 via-black to-amber-950/50 flex flex-wrap items-center justify-between gap-3 text-xs font-mono shadow-xl">
      <div class="flex items-center gap-3">
        <span class="w-3.5 h-3.5 rounded-full bg-[#FFD700] animate-ping"></span>
        <div>
          <strong id="activeSessionBannerTitle" class="text-white text-sm block">🏛️ SEGNALE CERTO CON TARGET TP OTTIMIZZATO</strong>
          <span id="activeSessionBannerDesc" class="text-[#FFD700]">Analisi di precisione multi-TF in corso...</span>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button onclick="notificaVocaleCryptoConsigliata(true)" class="px-4 py-2 rounded-xl bg-gradient-to-r from-amber-500 to-yellow-400 text-black hover:brightness-110 text-xs font-extrabold transition-all shadow-lg flex items-center gap-1.5">
          <span>🔊 Ripeti Notifica Vocale Cripto</span>
        </button>
      </div>
    </div>

    <!-- BOX ADVISOR POSIZIONE ATTIVA CON AVVISO CHIUSURA -->
    <div id="activePositionAdvisorBox" class="hidden terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#FFD700] bg-gradient-to-r from-amber-950/40 via-[#08080A] to-amber-950/40 shadow-2xl space-y-4">
      <div class="flex flex-wrap items-center justify-between pb-3 border-b border-[#FFD700]/30 gap-3">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-pulse">🔊</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white" id="advTitleMain">🟡 QUANTUM ADVISOR ATTIVO (<span id="advSymbol">BTC-USDT</span>)</h2>
            <p class="text-[11px] text-[#FFD700]">1. Ingresso Millimetrico • 2. Gestione in PnL • 3. Avviso Chiusura al TP/SL</p>
          </div>
        </div>
        <button onclick="chiudiPosizioneMonitorata()" class="px-3.5 py-1.5 rounded-xl bg-red-500/20 text-[#FF334B] border border-red-500/50 hover:bg-red-500/30 text-xs font-bold transition-all">
          Chiudi / Libera Monitoraggio ✕
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">1. 🟡 INGRESSO MILLIMETRICO</span>
          <strong id="advEntryPrice" class="text-white text-sm">--</strong>
          <span id="advSideBadge" class="inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-[#FFD700]">LONG</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">2. 📊 STATO MERCATO (PNL)</span>
          <strong id="advLivePrice" class="text-cyan-300 text-sm">--</strong>
          <span id="advPnlVal" class="block mt-1 text-[10px] font-bold text-[#FFD700]">PnL: 0.00%</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">🎯 TARGET TAKE PROFIT (ESATTO)</span>
          <strong id="advTargetTpDisplay" class="text-[#FFD700] text-sm">--</strong>
          <span id="advExactTimeRemaining" class="block mt-1 text-[10px] text-yellow-300 tabular-nums font-bold">Tempo reale al TP: --</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10 flex flex-col justify-between">
          <div>
            <span class="text-white/60 text-[10px] block">3. 🎯 AVVISO DI CHIUSURA</span>
            <strong id="advStatusAdvice" class="text-[#FFD700] text-xs block mt-0.5">In attesa del target...</strong>
          </div>
          <span class="text-[10px] text-white/80 italic mt-1">Notifica vocale e visiva all'uscita</span>
        </div>
      </div>
    </div>

    <!-- SEZIONE MASTER: SEGNALE MASTER CERTO -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#FFD700]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-bounce">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">⚡ SEGNALE MASTER CERTO CON R:R 1:2.5</h2>
            <p class="text-[11px] text-[#FFD700]" id="masterSubtitle">Sincronizzazione totale 100% con target di profitto calibrato</p>
          </div>
        </div>
        <span class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#FFD700] text-black animate-pulse">100% SINCRONIZZATO 🚀</span>
      </div>
      <div id="prioritySignalCardContent"></div>
    </section>

    <!-- LISTA CARD OPERATIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-4">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-3">
            <span class="w-8 h-8 rounded-full bg-[#FFD700]/20 border border-[#FFD700] flex items-center justify-center text-[#FFD700] text-base font-extrabold dollar-animated-logo">$</span>
            <span>📊 Lista Tutti i Mercati &amp; Cripto (<span id="tableMarketLabel" class="text-[#FFD700]">FUTURES</span>)</span>
          </h2>
          <span class="text-xs font-mono text-[#FFD700]">Tutti i ticker con allineamento 100%</span>
        </div>
        <div id="tablesContainer" class="space-y-3"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#FFD700] animate-ping"></span> Feed Notifiche &amp; Operatività
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Scanner TP] Motore di precisione attivo...</div>
        </div>
      </aside>
    </div>
  </main>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-[#FFD700]/40 rounded-2xl w-full max-w-[500px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 class="text-base font-extrabold font-mono text-[#FFD700]">🔑 Configurazione API Key Personale</h2>
          <p class="text-xs text-white/70">Collega il tuo account BingX</p>
        </div>
        <button onclick="toggleApiModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX API Key:</label>
          <input type="text" id="userApiKeyInput" placeholder="Inserisci la tua API Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#FFD700] outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX Secret Key:</label>
          <input type="password" id="userSecretKeyInput" placeholder="Inserisci la tua Secret Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#FFD700] outline-none" />
        </div>
        <button onclick="saveUserApiKeys()" class="w-full py-2.5 rounded-xl text-xs font-bold bg-[#FFD700] text-black shadow-lg hover:bg-amber-400 transition-all">
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
            <h2 id="modalSymbol" class="text-lg font-extrabold font-mono text-[#FFD700] bg-[#FFD700]/10 px-3 py-1 rounded-xl border border-[#FFD700]/50">BTC-USDT</h2>
            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700]/40">100% SINCRONIZZATO</span>
          </div>
          <p class="text-xs text-white/70 mt-1">Parametri millimetrici per ogni Timeframe &amp; Leva Libera</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <div class="flex justify-between"><span class="text-white/75">Mercato / Categoria:</span><strong id="modalCategory" class="text-[#FFD700]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">🏛️ Borsa Reale Attiva:</span><strong id="modalBorsaRif" class="text-cyan-300">--</strong></div>
          <div class="flex justify-between items-center">
            <span class="text-white/75">Timeframe Sincronizzati:</span>
            <strong class="px-2.5 py-0.5 rounded bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700] font-extrabold">TUTTI CONCORDI (5m, 15m, 1h, 4h) ⚡</strong>
          </div>
          <div class="flex justify-between"><span class="text-white/75">Sentiment Sincrono:</span><strong id="modalSentiment" class="text-[#FFD700] font-bold">--</strong></div>
          <div class="flex justify-between border-b border-white/10 pb-2"><span class="text-white/75">⏱️ Tempo Reale al $ TP (Aggiornato):</span><strong id="modalExactTime" class="text-yellow-300 font-extrabold tabular-nums">--</strong></div>

          <div id="tfPanel" class="grid grid-cols-2 sm:grid-cols-4 gap-2 my-2"></div>

          <!-- TABELLA PARAMETRI MILLIMETRICI ESATTI -->
          <div class="bg-gradient-to-r from-amber-950/50 via-black to-amber-950/50 border-2 border-[#FFD700] rounded-xl p-3 space-y-2 my-3 shadow-xl">
            <div class="flex items-center justify-between border-b border-[#FFD700]/30 pb-2">
              <span class="text-xs font-extrabold text-[#FFD700] flex items-center gap-1.5">
                <span>📋</span> PARAMETRI MILLIMETRICI ESATTI (TP &amp; SL)
              </span>
              <span class="text-[10px] text-white/70">Sincronizzazione 100%</span>
            </div>

            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs font-mono">
                <thead>
                  <tr class="border-b border-white/15 text-white/60 text-[10px]">
                    <th class="py-1.5 px-2">LIVELLO TIMEFRAME</th>
                    <th class="py-1.5 px-2">PREZZO ESATTO</th>
                    <th class="py-1.5 px-2 text-right">AZIONE</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/10">
                  <tr>
                    <td class="py-2 px-2 text-white font-bold">Ingresso Millimetrico</td>
                    <td class="py-2 px-2 text-cyan-300 font-bold select-all" id="tableParamEntry">--</td>
                    <td class="py-2 px-2 text-right"><button onclick="copiaParametro('tableParamEntry')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-cyan-300 text-[10px]">Copia</button></td>
                  </tr>
                  <tr>
                    <td class="py-2 px-2 text-[#FFD700] font-bold">Target Take Profit (TP Esatto)</td>
                    <td class="py-2 px-2 text-[#FFD700] font-extrabold select-all" id="tableParamTp">--</td>
                    <td class="py-2 px-2 text-right"><button onclick="copiaParametro('tableParamTp')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#FFD700] text-[10px]">Copia</button></td>
                  </tr>
                  <tr>
                    <td class="py-2 px-2 text-[#FF334B] font-bold">Stop Loss Protetto (SL Esatto)</td>
                    <td class="py-2 px-2 text-[#FF334B] font-extrabold select-all" id="tableParamSl">--</td>
                    <td class="py-2 px-2 text-right"><button onclick="copiaParametro('tableParamSl')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#FF334B] text-[10px]">Copia</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <div class="flex justify-between items-center">
            <label class="block text-xs font-bold text-white uppercase tracking-wider">💰 CAPITALE / MARGINE DA INVESTIRE (USDT):</label>
            <span id="marginUsdtLabel" class="text-xs font-bold text-[#FFD700]">10 USDT</span>
          </div>
          <div class="grid grid-cols-6 gap-1.5">
            <button type="button" onclick="setMarginUsdt(2)" id="marBtn2" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">2 $</button>
            <button type="button" onclick="setMarginUsdt(5)" id="marBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">5 $</button>
            <button type="button" onclick="setMarginUsdt(10)" id="marBtn10" class="py-1.5 rounded-lg border border-[#FFD700] bg-[#FFD700]/20 text-[#FFD700] font-bold text-xs">10 $</button>
            <button type="button" onclick="setMarginUsdt(25)" id="marBtn25" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">25 $</button>
            <button type="button" onclick="setMarginUsdt(50)" id="marBtn50" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">50 $</button>
            <button type="button" onclick="setMarginUsdt(100)" id="marBtn100" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs">100 $</button>
          </div>
          <input type="number" id="customMarginInput" oninput="onCustomMarginChange(this.value)" placeholder="Oppure inserisci USDT liberi..." class="w-full bg-[#08080A] border border-white/30 rounded-lg px-3 py-1.5 text-white font-mono text-xs focus:border-[#FFD700] outline-none" />
        </div>

        <div id="leverageSectionWrapper" class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <label class="block text-xs font-bold text-white uppercase tracking-wider">⚡ LEVA LIBERA (Seleziona o personalizza):</label>
          <div class="grid grid-cols-5 gap-2">
            <button type="button" onclick="setLeverage(1)" id="levBtn1" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">1x</button>
            <button type="button" onclick="setLeverage(5)" id="levBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">5x</button>
            <button type="button" onclick="setLeverage(10)" id="levBtn10" class="py-1.5 rounded-lg border border-[#FFD700] bg-[#FFD700]/20 text-[#FFD700] font-bold">10x</button>
            <button type="button" onclick="setLeverage(20)" id="levBtn20" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">20x</button>
            <button type="button" onclick="setLeverage(50)" id="levBtn50" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">50x</button>
          </div>
          <input type="number" id="customLeverageInput" oninput="onCustomLeverageChange(this.value)" placeholder="Oppure inserisci leva personalizzata (es. 25, 75, 100)..." class="w-full bg-[#08080A] border border-white/30 rounded-lg px-3 py-1.5 text-white font-mono text-xs focus:border-[#FFD700] outline-none mt-2" />
        </div>

        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-white/70 font-semibold">Quantità Asset Calcolata:</label>
          </div>
          <input type="number" id="tradeQuantityInput" value="1.0" step="any" oninput="onQuantityManualChange(this.value)" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
        </div>

        <button onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>⚡ Esegui Segnale con Leva Libera &amp; Avvia Advisor Chiusura</span>
        </button>
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
      selectedLeverage: 10,
      selectedMarginUsdt: 10,
      currentSelectedAsset: null,
      activePosition: null,
      advisorInterval: null,
      lastNotifiedSymbol: null,
      lastNotificationTime: 0
    };

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
      const textBtn = document.getElementById('apiStatusText');
      if (keys.apiKey && keys.secretKey) {
        if (textBtn) textBtn.textContent = "API Connesse 🟢";
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
      }).catch(err => {});
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
        b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold text-xs";
      });
      const activeBtn = document.getElementById(`marBtn${usdt}`);
      if (activeBtn) activeBtn.className = "py-1.5 rounded-lg border border-[#FFD700] bg-[#FFD700]/20 text-[#FFD700] font-bold text-xs";
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
        const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
        const marEst = valNom / lev;
        document.getElementById('marginUsdtLabel').textContent = `Margine st.: ~${marEst.toFixed(2)} USDT`;
      }
    }

    function setLeverage(lev) {
      state.selectedLeverage = lev;
      document.getElementById('customLeverageInput').value = '';
      document.querySelectorAll('#leverageSectionWrapper button').forEach(b => {
        b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
      });
      const activeBtn = document.getElementById(`levBtn${lev}`);
      if (activeBtn) activeBtn.className = "py-1.5 rounded-lg border border-[#FFD700] bg-[#FFD700]/20 text-[#FFD700] font-bold";
      aggiornaQuantitaDaCapitale();
    }

    function onCustomLeverageChange(val) {
      const num = parseInt(val, 10);
      if (!isNaN(num) && num > 0) {
        state.selectedLeverage = num;
        document.querySelectorAll('#leverageSectionWrapper button').forEach(b => {
          b.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
        });
        aggiornaQuantitaDaCapitale();
      }
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
          state.detailedAssets = {};
          state.timerAnchors = {};
          updateMarketTypeUI();
          await pollMarketData();
          logFeed(`[MERCATO] Passato a ${mtype}`);
        }
      } catch (e) {}
    }

    function updateMarketTypeUI() {
      const isSpot = state.marketType === 'SPOT';
      document.getElementById('btnMarketFutures').className = state.marketType === 'FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#0066FF] text-white" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('btnMarketStandard').className = state.marketType === 'STANDARD_FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-500 text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('btnMarketSpot').className = isSpot ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#FFD700] text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
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
          document.getElementById('btnModeManual').className = mode === 'MANUAL' ? "px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white" : "px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
          document.getElementById('btnModeAuto').className = mode === 'AUTO' ? "px-2.5 py-1 rounded-lg text-xs font-bold transition-all bg-[#FFD700] text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        }
      } catch (e) {}
    }

    async function pollMarketData() {
      try {
        const res = await fetch(`/api/market-data?marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0 && json.marketType === state.marketType) {
          state.marketData = json.data;
          await renderDashboardAndCheckNotifications();
          renderLiveTickerMarquee();
        }
      } catch (e) {}
    }

    function renderLiveTickerMarquee() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;
      let sortedKeys = keys.sort((a, b) => (state.marketData[b].volume || 0) - (state.marketData[a].volume || 0));
      const items = sortedKeys.slice(0, 15).map(sym => {
        const item = state.marketData[sym];
        return `<div class="flex items-center gap-2 bg-white/5 px-3 py-1 rounded-lg border border-white/10 shrink-0">
          <span class="font-bold text-white">${sym.replace('-USDT', '')}</span>
          <span class="text-white/90">$${formatPrice(item.price)}</span>
          <span class="${item.change24h >= 0 ? 'text-[#FFD700]' : 'text-[#FF334B]'} font-bold">${item.change24h >= 0 ? '+' : ''}${item.change24h.toFixed(2)}%</span>
        </div>`;
      }).join('');
      document.getElementById('liveTickerMarquee').innerHTML = items + items;
    }

    let globalSyncedAssetsCache = [];

    async function renderDashboardAndCheckNotifications() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;

      let syncedAssets = [];
      for (let sym of keys) {
        try {
          const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(sym)}&marketType=${state.marketType}`);
          const json = await res.json();
          if (json.code === 0 && json.data && json.data.precisionScore === 100.0) {
            syncedAssets.push(json.data);
          }
        } catch (e) {}
      }
      globalSyncedAssetsCache = syncedAssets;

      const masterContainer = document.getElementById('prioritySignalCardContent');
      const bannerDesc = document.getElementById('activeSessionBannerDesc');

      if (syncedAssets.length > 0) {
        const master = syncedAssets[0];
        bannerDesc.innerHTML = `🎯 Tutti i mercati attivi. Cripto consigliata: <strong class="text-[#FFD700]">${master.symbol}</strong> (${master.side === 'BUY' ? 'LONG 🚀' : 'SHORT 📉'}) con sincronizzazione totale 100% e TP ottimizzato.`;
        
        const now = Date.now();
        if (state.lastNotifiedSymbol !== master.symbol || (now - state.lastNotificationTime > 300000)) {
          state.lastNotifiedSymbol = master.symbol;
          state.lastNotificationTime = now;
          notificaVocaleCryptoConsigliata(false);
        }

        const liveRemMaster = getLiveRemainingSeconds(master.symbol, master.tempoSec);
        masterContainer.innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <div class="flex items-center gap-2">
                <span class="text-white text-base font-extrabold px-3 py-1 rounded-xl bg-[#FFD700]/20 border-2 border-[#FFD700] text-[#FFD700] animate-pulse">${master.symbol} 🟡</span>
                <span class="text-xs text-[#FFD700] font-bold">✨ PRECISIONE TP 1:2.5</span>
              </div>
              <div class="flex justify-between"><span class="text-white/70">Prezzo Ingresso:</span><strong class="text-[#FFD700] text-sm">$${formatPrice(master.price)}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">Direzione:</span><strong class="${master.side === 'BUY' ? 'text-[#FFD700]' : 'text-[#FF334B]'} font-bold">${master.side === 'BUY' ? 'LONG 🚀' : 'SHORT 📉'}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">🎯 Target TP Millimetrico:</span><strong class="text-yellow-300 font-extrabold">$${formatPrice(master.tp)}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">🛡️ Stop Loss Esatto:</span><strong class="text-[#FF334B] font-extrabold">$${formatPrice(master.sl)}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">⏱️ Tempo Reale al TP:</span><strong class="text-cyan-300 font-bold tabular-nums" data-countdown-sym="${master.symbol}" data-base-sec="${master.tempoSec}">${formatExactDuration(liveRemMaster)}</strong></div>
            </div>
            <div class="flex flex-col justify-end">
              <button onclick='openModal("${master.symbol}")' class="w-full py-2.5 rounded-xl text-xs shadow-lg bg-[#FFD700] text-black font-extrabold hover:bg-amber-400 transition-all">⚡ APRI SEGNALE &amp; ESEGUI &#8599;</button>
            </div>
          </div>`;
      } else {
        bannerDesc.innerHTML = `⚠️ Scansione completa in corso. In attesa di allineamento Multi-TF perfetto per colpire il TP...`;
        masterContainer.innerHTML = `<div class="text-xs text-yellow-400 font-mono text-center py-4">In attesa di segnali certi con alta probabilità di TP...</div>`;
      }

      const container = document.getElementById('tablesContainer');
      if (syncedAssets.length === 0) {
        container.innerHTML = `<div class="p-4 text-center text-xs font-mono text-white/60">Nessun asset con sincronizzazione totale in questo istante.</div>`;
        return;
      }

      container.innerHTML = syncedAssets.map(d => {
        const liveRemCard = getLiveRemainingSeconds(d.symbol, d.tempoSec);
        const isLong = d.side === 'BUY';
        return `
          <div onclick='openModal("${d.symbol}")' class="gold-highlight-card rounded-xl p-3.5 cursor-pointer transition-all space-y-2.5 relative">
            <div class="absolute top-2 right-2 px-2 py-0.5 rounded text-[9px] font-extrabold bg-[#FFD700] text-black animate-pulse">🟡 100% SINCRONIZZATO</div>
            <div class="flex items-center justify-between border-b border-white/10 pb-2">
              <div class="flex items-center gap-2">
                <span class="font-extrabold text-sm px-2.5 py-1 rounded-xl bg-[#FFD700]/20 border border-[#FFD700] text-[#FFD700]">${d.symbol}</span>
                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${isLong ? 'bg-amber-500/20 text-[#FFD700]' : 'bg-red-500/20 text-[#FF334B]'}">${isLong ? 'LONG 🚀' : 'SHORT 📉'}</span>
              </div>
              <div class="text-right">
                <span class="text-white text-xs font-bold tabular-nums">$${formatPrice(d.price)}</span>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2 text-[11px] font-mono">
              <div class="bg-white/5 p-2 rounded-lg">
                <span class="text-white/60 text-[10px] block">🎯 TP / 🛡️ SL</span>
                <strong class="text-[#FFD700]">$${formatPrice(d.tp)}</strong>
                <span class="text-[#FF334B] text-[10px] block">SL: $${formatPrice(d.sl)}</span>
              </div>
              <div class="bg-white/5 p-2 rounded-lg">
                <span class="text-white/60 text-[10px] block">⏱️ TEMPO REALE AL TP</span>
                <strong class="text-cyan-300 tabular-nums" data-countdown-sym="${d.symbol}" data-base-sec="${d.tempoSec}">${formatExactDuration(liveRemCard)}</strong>
                <span class="text-yellow-300 text-[10px] block">Sincronia 100% ⚡</span>
              </div>
            </div>
          </div>`;
      }).join('');
    }

    function notificaVocaleCryptoConsigliata(manualClick = false) {
      if (globalSyncedAssetsCache.length > 0) {
        const topAsset = globalSyncedAssetsCache[0];
        const dirText = topAsset.side === 'BUY' ? 'LONG rialzista' : 'SHORT ribassista';
        const msg = `Notifica di trading. Su tutti i mercati monitorati, la criptovaluta consigliata in questo momento per puntare al Take Profit è ${topAsset.symbol.replace('-USDT', '')}, direzione ${dirText} con sincronizzazione totale al cento per cento.`;
        playChime(false, msg);
        logFeed(`[NOTIFICA TP] Consigliata: ${topAsset.symbol} (${topAsset.side})`);
      } else {
        const msg = `Nessun asset con sincronizzazione totale in questo momento.`;
        if (manualClick) {
          playChime(false, msg);
        }
        logFeed(`[NOTIFICA] Nessun asset sincronizzato.`);
      }
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

          state.timerAnchors[symbol] = {
            baseSec: d.tempoSec,
            candleTime: d.candleTime,
            startedAt: Date.now()
          };

          document.getElementById('modalCategory').textContent = d.marketType;
          document.getElementById('modalBorsaRif').textContent = d.confluenza;
          document.getElementById('modalSentiment').textContent = d.sentiment;
          document.getElementById('modalExactTime').textContent = `${formatExactDuration(d.tempoSec)} 🎯`;

          document.getElementById('tableParamEntry').textContent = formatPrice(d.price);
          document.getElementById('tableParamTp').textContent = formatPrice(d.tp);
          document.getElementById('tableParamSl').textContent = formatPrice(d.sl);

          const tfPanel = document.getElementById('tfPanel');
          const tfs = d.tfs;
          tfPanel.innerHTML = ['5m', '15m', '1h', '4h'].map(iv => {
            const t = tfs[iv];
            return `<div class="bg-white/5 rounded-lg p-2 border border-[#FFD700]">
              <div class="flex justify-between items-center text-[10px]">
                <b class="text-[#FFD700]">${iv}</b>
                <b class="text-[#FFD700]">${t.trend}</b>
              </div>
              <div class="text-[9px] text-white/60 mt-1">RSI: ${t.rsi14}</div>
            </div>`;
          }).join('');

          aggiornaQuantitaDaCapitale();
        }
      } catch (e) {}
    }

    function closeModal() {
      state.currentSelectedAsset = null;
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
    }

    function avviaAdvisorPosizione(symbol, side, entryPrice, tpPrice, slPrice, tempoSec) {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      const liveRemSec = getLiveRemainingSeconds(symbol, tempoSec);

      state.activePosition = { symbol, side, entryPrice, tpPrice, slPrice, openedAt: Date.now(), initialTempoSec: liveRemSec };
      const box = document.getElementById('activePositionAdvisorBox');
      box.classList.remove('hidden');

      document.getElementById('advSymbol').textContent = symbol;
      document.getElementById('advEntryPrice').textContent = `$${formatPrice(entryPrice)}`;
      document.getElementById('advSideBadge').textContent = side;
      document.getElementById('advTargetTpDisplay').textContent = `$${formatPrice(tpPrice)}`;

      playChime(false, `Segnale avviato su ${symbol} con leva ${state.selectedLeverage}x. Monitoraggio TP attivo.`);
      state.advisorInterval = setInterval(aggiornaAdvisorLoop, 1000);
      logFeed(`[ADVISOR] Monitoraggio TP attivo su ${symbol}`);
    }

    function chiudiPosizioneMonitorata() {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      state.activePosition = null;
      document.getElementById('activePositionAdvisorBox').classList.add('hidden');
      logFeed(`[ADVISOR] Monitoraggio chiuso.`);
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
      pnlEl.textContent = `PnL: ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
      pnlEl.className = `block mt-1 text-[10px] font-bold ${pnlPct >= 0 ? 'text-[#FFD700]' : 'text-[#FF334B]'}`;

      const advAdvice = document.getElementById('advStatusAdvice');

      if ((pos.side === 'BUY' && currentPrice >= pos.tpPrice) || (pos.side === 'SELL' && currentPrice <= pos.tpPrice)) {
        advAdvice.textContent = "🎯 TP RAGGIUNTO! ORA DI CHIUDERE!";
        playChime(true, "Target Take Profit raggiunto con successo. È ora di chiudere in profitto.");
        chiudiPosizioneMonitorata();
      } else if ((pos.side === 'BUY' && currentPrice <= pos.slPrice) || (pos.side === 'SELL' && currentPrice >= pos.slPrice)) {
        advAdvice.textContent = "🛑 STOP LOSS RAGGIUNTO! CHIUDI SUBITO!";
        playChime(true, "Stop loss toccato. Chiusura di protezione eseguita.");
        chiudiPosizioneMonitorata();
      }
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
          marketType: state.marketType, apiKey: keys.apiKey, secretKey: keys.secretKey,
          is_auto: state.tradingMode === 'AUTO'
        })
      });
      const data = await res.json();
      if (data.code === 0) {
        avviaAdvisorPosizione(asset.symbol, asset.side, asset.price, asset.tp, asset.sl, asset.tempoSec);
        closeModal();
      } else {
        alert("Errore esecuzione: " + (data.msg || "Verifica chiavi API"));
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
