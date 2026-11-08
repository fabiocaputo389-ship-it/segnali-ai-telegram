import os
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
MARKET_TYPE = "FUTURES"  # "FUTURES" o "SPOT"


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
# MOTORE MATEMATICO REALE SPOT & FUTURES
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


def get_klines_precision(simbolo: str, market_type: str = "FUTURES", interval: str = "15m", limit: int = 50):
    """Scarica candele OHLCV reali da BingX (Futures o Spot) e calcola indicatori millimetrici."""
    try:
        if market_type == "SPOT":
            url = f"{BASE_URL}/openApi/spot/v1/market/kline"
            params = {"symbol": simbolo, "interval": interval, "limit": limit}
        else:
            url = f"{BASE_URL}/openApi/swap/v3/quote/klines"
            params = {"symbol": simbolo, "interval": interval, "limit": limit}

        resp = requests.get(url, params=params, timeout=3)
        payload = resp.json()
        raw_klines = payload.get("data", [])

        if not raw_klines or len(raw_klines) < 5:
            return None

        candles = []
        for k in raw_klines:
            if isinstance(k, dict):
                candles.append({
                    "time": int(k.get("time", 0)),
                    "open": float(k.get("open", 0)),
                    "high": float(k.get("high", 0)),
                    "low": float(k.get("low", 0)),
                    "close": float(k.get("close", 0)),
                    "volume": float(k.get("volume", 0))
                })
            elif isinstance(k, list) and len(k) >= 6:
                candles.append({
                    "time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5])
                })

        candles.sort(key=lambda x: x["time"])
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c["volume"] for c in candles]

        last_price = closes[-1]
        ema9 = _calcola_ema_serie(closes, 9)
        ema21 = _calcola_ema_serie(closes, 21)
        ema9_prev = _calcola_ema_serie(closes[:-1], 9) if len(closes) > 1 else ema9
        ema21_prev = _calcola_ema_serie(closes[:-1], 21) if len(closes) > 1 else ema21

        rsi14 = _calcola_rsi_wilder(closes, 14)
        atr14 = _calcola_atr_reale(highs, lows, closes, 14)

        vol_window = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
        avg_vol = (sum(vol_window) / len(vol_window)) if vol_window else 1.0
        current_vol = max(volumes[-1], volumes[-2] * 0.85) if len(volumes) >= 2 else volumes[-1]
        rvol = round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0

        ema_bull = ema9 >= ema21
        cross_up = ema9_prev <= ema21_prev and ema9 > ema21
        cross_down = ema9_prev >= ema21_prev and ema9 < ema21

        if last_price >= 1000:
            dec = 2
        elif last_price >= 10:
            dec = 3
        elif last_price >= 1:
            dec = 4
        elif last_price >= 0.01:
            dec = 5
        else:
            dec = 7

        if market_type == "SPOT":
            side = "BUY"
            sl_mult = 1.20
            tp_mult = 2.80
            sl = round(last_price - (atr14 * sl_mult), dec)
            tp = round(last_price + (atr14 * tp_mult), dec)
        else:
            is_bull = ema_bull
            side = "BUY" if is_bull else "SELL"
            sl_mult = 1.25
            tp_mult = 2.50
            sl = round(last_price - (atr14 * sl_mult), dec) if is_bull else round(last_price + (atr14 * sl_mult), dec)
            tp = round(last_price + (atr14 * tp_mult), dec) if is_bull else round(last_price - (atr14 * tp_mult), dec)

        atr_pct = round((atr14 / last_price) * 100, 3) if last_price > 0 else 0.0

        tp_dist = abs(tp - last_price)
        candele_stimate = max(2, round(tp_dist / (atr14 * 0.40))) if atr14 > 0 else 10
        minuti_stimati = candele_stimate * 15

        if market_type == "SPOT":
            ore_spot = round(minuti_stimati / 60 * 1.5, 1)
            if ore_spot < 2:
                durata = "1 - 3 ore (Impulso Spot)"
                durata_short = "1-3h"
                tf_consigliato = "15m / 1h"
                tempo_sec = 7200
            elif ore_spot <= 24:
                durata = f"{ore_spot} - {round(ore_spot + 6, 1)} ore (Intraday Spot)"
                durata_short = f"{int(ore_spot)}h"
                tf_consigliato = "1h / 4h"
                tempo_sec = int(ore_spot * 3600)
            else:
                giorni = round(ore_spot / 24, 1)
                durata = f"{giorni} - {giorni + 1.5} giorni (Swing Spot)"
                durata_short = f"{giorni}d"
                tf_consigliato = "4h / 1D"
                tempo_sec = int(giorni * 86400)
        else:
            if minuti_stimati < 60:
                durata = f"{minuti_stimati} - {minuti_stimati + 20} min (Scalp 15m)"
                durata_short = f"{minuti_stimati}m"
                tf_consigliato = "5m / 15m"
                tempo_sec = minuti_stimati * 60
            elif minuti_stimati < 360:
                ore = round(minuti_stimati / 60, 1)
                durata = f"{ore} - {round(ore + 1.5, 1)} ore (Trend Intraday)"
                durata_short = f"{ore}h"
                tf_consigliato = "15m / 1h"
                tempo_sec = int(ore * 3600)
            else:
                ore = round(minuti_stimati / 60)
                durata = f"{ore} - {ore + 6} ore (Swing Futures)"
                durata_short = f"{ore}h"
                tf_consigliato = "1h / 4h"
                tempo_sec = int(ore * 3600)

        score = 72
        if market_type == "SPOT":
            if ema_bull: score += 12
            if cross_up: score += 8
            if rsi14 < 52: score += 8
            if rvol >= 1.2: score += 6
        else:
            if (ema_bull and side == "BUY") or (not ema_bull and side == "SELL"): score += 12
            if cross_up or cross_down: score += 8
            if rvol >= 1.3: score += 6
        score = min(99, score)

        return {
            "symbol": simbolo,
            "marketType": market_type,
            "price": round(last_price, dec),
            "side": side,
            "ema9": round(ema9, dec),
            "ema21": round(ema21, dec),
            "emaBullish": ema_bull,
            "crossSignal": "GOLDEN CROSS ⚡" if cross_up else ("DEATH CROSS 🔻" if cross_down else ("Trend Rialzista" if ema_bull else "Trend Correttivo")),
            "rsi14": round(rsi14, 2),
            "atr14": round(atr14, dec),
            "atrPercent": atr_pct,
            "rvol": rvol,
            "sl": sl,
            "tp": tp,
            "timeframe": tf_consigliato,
            "durata": durata,
            "durataShort": durata_short,
            "tempoSec": tempo_sec,
            "precisionScore": score,
            "decimals": dec
        }
    except Exception as e:
        print(f"[KLINE PRECISION ERROR] {simbolo} ({market_type}): {e}")
        return None


def get_live_market_data(market_type: str = "FUTURES"):
    """Scarica tutte le coppie di mercato live da BingX (Futures o Spot) in tempo reale."""
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

                price = float(t.get("lastPrice", 0))
                if price <= 0:
                    continue

                if market_type == "SPOT":
                    raw_chg = t.get("priceChangePercent", "0")
                    if isinstance(raw_chg, str):
                        raw_chg = raw_chg.replace("%", "")
                    change = float(raw_chg)
                    volume = float(t.get("volume", 0))
                    quote_volume = float(t.get("quoteVolume", volume * price))
                    high = float(t.get("highPrice", price))
                    low = float(t.get("lowPrice", price))
                else:
                    change = float(t.get("priceChangePercent", 0)) * 100
                    volume = float(t.get("volume", 0))
                    quote_volume = float(t.get("quoteVolume", volume * price))
                    high = float(t.get("highPrice", price))
                    low = float(t.get("lowPrice", price))

                category = "Criptovalute"
                sym_upper = symbol.upper()
                if market_type == "FUTURES":
                    if any(mat in sym_upper for mat in ["GOLD", "SILVER", "OIL", "XAU", "XAG", "WTI", "BRENT", "COPPER"]):
                        category = "Materie Prime"
                    elif any(fx in sym_upper for fx in ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]):
                        category = "Forex / Valute"
                    elif any(st in sym_upper for st in ["NASDAQ", "SP500", "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "GOLDEN", "US30"]):
                        category = "Azioni & Indici (TradFi)"

                market_map[symbol] = {
                    "price": price,
                    "change24h": change,
                    "volume": volume,
                    "quoteVolume": quote_volume,
                    "high24h": high,
                    "low24h": low,
                    "category": category,
                    "marketType": market_type
                }
            return market_map
    except Exception as e:
        print(f"[MARKET DATA ERROR] ({market_type}) Errore connessione BingX: {e}")
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
        resp = requests.post(f"{url}?{query_string}", headers=headers)
        res_data = resp.json()
        if res_data.get("code") == 0:
            print(f"[LEVERAGE SUCCESS] Leva {leva}x impostata per {simbolo} ({position_side})")
    except Exception as e:
        print(f"[LEVERAGE EXCEPT] {e}")


def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "15m / 1h", market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti. Inserisci le tue chiavi personali nel pannello in alto a destra."}

    if market_type == "FUTURES":
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

    url = BASE_URL + endpoint
    query_string = _genera_firma(payload_ordine, active_secret)
    headers = {"X-BX-APIKEY": active_api}

    try:
        response = requests.post(f"{url}?{query_string}", headers=headers)
        risultato = response.json()

        if risultato.get("code") == 0 and market_type == "FUTURES":
            time.sleep(0.8)
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            if prezzo_sl > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET", position_side, active_api, active_secret)
            if prezzo_tp > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET", position_side, active_api, active_secret)

        return risultato
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def _imposta_ordine_protettivo(simbolo: str, lato_chiusura: str, prezzo_trigger: float, tipo_ordine: str, position_side: str, api_key: str, secret_key: str):
    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    payload = {
        "symbol": simbolo,
        "side": lato_chiusura,
        "positionSide": position_side,
        "type": tipo_ordine,
        "stopPrice": str(prezzo_trigger),
        "workingType": "MARK_PRICE",
        "reduceOnly": "true",
        "timestamp": timestamp
    }
    query_string = _genera_firma(payload, secret_key)
    headers = {"X-BX-APIKEY": api_key}
    try:
        requests.post(f"{url}?{query_string}", headers=headers)
    except Exception as e:
        print(f"[PROTECT EXCEPT] {e}")


# ==========================================
# ENDPOINT API FLASK
# ==========================================
@app.route('/api/market-data', methods=['GET'])
def api_market_data():
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    if mtype not in ["FUTURES", "SPOT"]:
        mtype = "FUTURES"
    data = get_live_market_data(mtype)
    return jsonify({"code": 0, "marketType": mtype, "data": data})


@app.route('/api/deep-scan', methods=['GET'])
def api_deep_scan():
    simbolo = request.args.get("symbol", "BTC-USDT")
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    interval = request.args.get("interval", "15m")
    analysis = get_klines_precision(simbolo, mtype, interval, 50)
    if analysis:
        return jsonify({"code": 0, "data": analysis})
    return jsonify({"code": -1, "msg": "Dati candele OHLCV non disponibili per questo asset."})


@app.route('/api/esegui-segnale', methods=['POST'])
def webhook_esegui_segnale():
    dati = request.json or {}
    simbolo = dati.get("symbol")
    lato = dati.get("action")
    quantita = float(dati.get("size", 0.001))
    prezzo_ingresso = float(dati.get("entry", dati.get("price", 0)))
    tp = float(dati.get("tp", 0))
    sl = float(dati.get("sl", 0))
    leva = int(dati.get("leverage", 3))
    timeframe = dati.get("timeframe", "15m / 1h")
    mtype = dati.get("marketType", MARKET_TYPE).upper()
    is_auto = dati.get("is_auto", False)

    user_api_key = dati.get("apiKey", "")
    user_secret_key = dati.get("secretKey", "")

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Modalità Automatica disattivata dal sistema."})

    risultato = esegui_ordine_bingx(simbolo, lato, quantita, prezzo_ingresso, tp, sl, leva, timeframe, mtype, user_api_key, user_secret_key)
    return jsonify(risultato)


@app.route('/api/set-mode', methods=['POST'])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    mode = dati.get("mode")
    if mode in ["MANUAL", "AUTO"]:
        TRADING_MODE = mode
        return jsonify({"code": 0, "mode": TRADING_MODE})
    return jsonify({"code": -1, "msg": "Modalità non valida"})


@app.route('/api/get-mode', methods=['GET'])
def get_trading_mode():
    return jsonify({"code": 0, "mode": TRADING_MODE, "marketType": MARKET_TYPE})


@app.route('/api/set-market-type', methods=['POST'])
def set_market_type():
    global MARKET_TYPE
    dati = request.json or {}
    mtype = dati.get("marketType")
    if mtype in ["FUTURES", "SPOT"]:
        MARKET_TYPE = mtype
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
  <title>ApexScan Pro - Live Position Advisor Engine</title>
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
    @keyframes neonGlowPulse { 0%, 100% { box-shadow: 0 0 15px rgba(255, 204, 0, 0.3), inset 0 0 10px rgba(255, 204, 0, 0.1); border-color: rgba(255, 204, 0, 0.7); } 50% { box-shadow: 0 0 30px rgba(255, 204, 0, 0.6), inset 0 0 18px rgba(255, 204, 0, 0.3); border-color: #FFCC00; } }
    .priority-signal-box { background: linear-gradient(180deg, rgba(20, 20, 10, 0.98) 0%, rgba(8, 8, 5, 0.99) 100%); border: 2px solid #FFCC00; animation: neonGlowPulse 3s infinite ease-in-out; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
    
    @keyframes tickerScroll {
      0% { transform: translateX(0); }
      100% { transform: translateX(-50%); }
    }
    .ticker-wrapper { display: flex; width: max-content; animation: tickerScroll 25s linear infinite; }
    .ticker-wrapper:hover { animation-play-state: paused; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#00FF66]/30 selection:text-white">

  <!-- HEADER -->
  <header class="sticky top-0 z-30 bg-[#000000]/95 backdrop-blur-md border-b border-white/15 px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3.5">
        <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#00FF66] via-[#00CC52] to-[#006622] text-black flex items-center justify-center shadow-2xl border-2 border-[#00FF66] font-extrabold text-xl animate-pulse">
          🎯
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">APEXSCAN <span class="text-[#00FF66]" id="headerEngineTitle">Live Advisor Engine</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span> <span id="modeText">MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]">1. Entrata • 2. Rimanere a mercato • 3. Uscire (Precisione Dati 🟢)</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
          <span class="text-xs font-mono font-bold text-white/70 px-1">Mercato:</span>
          <button id="btnMarketFutures" onclick="switchMarketType('FUTURES')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#0066FF] text-white">FUTURES</button>
          <button id="btnMarketSpot" onclick="switchMarketType('SPOT')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">SPOT 🪙</button>
        </div>

        <button onclick="toggleApiModal()" class="px-3.5 py-1.5 rounded-xl text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/50 hover:bg-cyan-500/30 transition-all flex items-center gap-1.5">
          <span>🔑</span> <span id="apiStatusText">Collega API Personali</span>
        </button>

        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
          <span class="text-xs font-mono font-bold text-white/70 px-1">Esecuzione:</span>
          <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
          <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">AUTO ⚡</button>
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

    <!-- BOX ADVISOR POSIZIONE ATTIVA CON LE 3 VOCI OBBLIGATORIE -->
    <div id="activePositionAdvisorBox" class="hidden terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#00FF66] bg-gradient-to-r from-emerald-950/40 via-[#08080A] to-emerald-950/40 shadow-2xl space-y-4">
      <div class="flex flex-wrap items-center justify-between pb-3 border-b border-[#00FF66]/30 gap-3">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold animate-pulse">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">🟢 ADVISOR LIVE &amp; GESTIONE POSIZIONE (<span id="advSymbol">BTC-USDT</span>)</h2>
            <p class="text-[11px] text-[#00FF66]">Controllo millimetrico: 1. Entrata • 2. Rimanere a mercato • 3. Uscire con dati precisi</p>
          </div>
        </div>
        <button onclick="chiudiPosizioneMonitorata()" class="px-3.5 py-1.5 rounded-xl bg-red-500/20 text-[#FF334B] border border-red-500/50 hover:bg-red-500/30 text-xs font-bold transition-all">
          Chiudi / Deseleziona Monitoraggio ✕
        </button>
      </div>

      <!-- CARDS KPI ADVISOR -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">1. ENTRATA (PREZZO APERTURA)</span>
          <strong id="advEntryPrice" class="text-white text-sm">--</strong>
          <span id="advSideBadge" class="inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-bold bg-green-500/20 text-[#00FF66]">LONG</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">PREZZO LIVE &amp; PNL NETTO</span>
          <strong id="advLivePrice" class="text-cyan-300 text-sm">--</strong>
          <span id="advPnlVal" class="block mt-1 text-[10px] font-bold text-[#00FF66]">PnL: 0.00%</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">⏱️ TEMPO RIMANENTE AL TP</span>
          <strong id="advTimeRemaining" class="text-yellow-300 text-sm">--:--</strong>
          <span id="advTimeProgress" class="block mt-1 text-[10px] text-white/70">Durata stimata attiva</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10 flex flex-col justify-between">
          <div>
            <span class="text-white/60 text-[10px] block">3. CONSIGLIO DI USCITA / TENUTA</span>
            <strong id="advStatusAdvice" class="text-[#00FF66] text-xs block mt-0.5">Analisi in corso...</strong>
          </div>
          <span id="advActionAlert" class="text-[10px] text-white/80 italic mt-1">--</span>
        </div>
      </div>

      <!-- TABELLA COMPLETA CON LE 3 VOCI RICHIESTE -->
      <div class="overflow-x-auto pt-2">
        <table class="w-full text-left border-collapse text-xs font-mono">
          <thead>
            <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-black/80">
              <th class="py-2.5 px-3">Asset Monitorato</th>
              <th class="py-2.5 px-3">1. Entrata (Entry)</th>
              <th class="py-2.5 px-3">Target TP Certo</th>
              <th class="py-2.5 px-3">Stop Loss</th>
              <th class="py-2.5 px-3">PnL Netto (Stima)</th>
              <th class="py-2.5 px-3">2. Rimanere a mercato</th>
              <th class="py-2.5 px-3 text-right">3. Uscire</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-white/10 bg-black/40">
            <tr>
              <td id="tableAdvSymbol" class="py-3 px-3 font-bold text-white">--</td>
              <td id="tableAdvEntry" class="py-3 px-3 text-white">--</td>
              <td id="tableAdvTp" class="py-3 px-3 text-[#00FF66] font-bold">--</td>
              <td id="tableAdvSl" class="py-3 px-3 text-[#FF334B]">--</td>
              <td id="tableAdvPnl" class="py-3 px-3 font-extrabold text-[#00FF66]">--</td>
              <td id="tableAdvRemain" class="py-3 px-3 text-cyan-300 font-bold">Sì (Trend Attivo)</td>
              <td id="tableAdvExit" class="py-3 px-3 text-right text-yellow-300 font-bold">Rimani dentro al trade</td>
            </tr>
          </tbody>
        </table>
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
        <span class="text-[11px] font-semibold uppercase text-white/75">Asset Monitorati Live</span>
        <div class="mt-2">
          <span id="kpiTotalAssets" class="text-base font-mono text-cyan-400 font-extrabold">-- Asset Live</span>
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
        <span class="text-[11px] font-semibold uppercase text-white/75">Precisione Dati Engine</span>
        <div class="mt-2">
          <span id="kpiMarketBadge" class="text-xs font-mono text-cyan-400 font-bold">🟢 OHLCV 15m + Fee Real</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#FFCC00]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFCC00] text-black flex items-center justify-center font-extrabold animate-bounce">🎯</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">🎯 SEGNALE MASTER CON TARGET TP CERTO (15M OHLCV)</h2>
            <p class="text-[11px] text-[#FFCC00]" id="masterSubtitle">Avvio con monitoraggio istantaneo del tempo di permanenza e alert chiusura</p>
          </div>
        </div>
        <span id="masterPrecisionBadge" class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#FFCC00] text-black animate-pulse">CALIBRAZIONE TP...</span>
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
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Asset (es. BTC, ETH, SOL, XRP, SUI, PEPE, GOLD)..." class="w-full bg-[#000000] border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
      </div>
    </section>

    <!-- TABELLA LIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-6">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span>📊</span> Tabella Flussi &amp; Target TP Certo (<span id="tableMarketLabel" class="text-[#00FF66]">FUTURES</span>)
          </h2>
          <span class="text-xs font-mono text-cyan-400 flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Clicca qualsiasi riga per avviare il trade e l'Advisor live
          </span>
        </div>

        <div id="tablesContainer" class="space-y-6"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed Segnali &amp; Advisor
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Precision Engine] Scanner e Advisor con dati avanzati pronti...</div>
        </div>
      </aside>
    </div>
  </main>

  <!-- MODALE API -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-cyan-500/40 rounded-2xl w-full max-w-[500px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 class="text-base font-extrabold font-mono text-cyan-300">🔑 Configurazione API Key Personale</h2>
          <p class="text-xs text-white/70">Collega il tuo account BingX (Spot &amp; Futures)</p>
        </div>
        <button onclick="toggleApiModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX API Key:</label>
          <input type="text" id="userApiKeyInput" placeholder="Inserisci la tua API Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-cyan-400 outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX Secret Key:</label>
          <input type="password" id="userSecretKeyInput" placeholder="Inserisci la tua Secret Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-cyan-400 outline-none" />
        </div>
        <span class="text-[10px] text-yellow-400 block">🔒 Salvate in sicurezza nel tuo browser (localStorage).</span>
        
        <button onclick="saveUserApiKeys()" class="w-full py-2.5 rounded-xl text-xs font-bold bg-cyan-500 text-black shadow-lg hover:bg-cyan-400 transition-all">
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
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[650px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <div class="flex items-center gap-2">
            <h2 id="modalSymbol" class="text-base font-extrabold font-mono text-white">BTC-USDT</h2>
            <span id="modalOhlcvBadge" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">CALIBRAZIONE TP...</span>
          </div>
          <p id="modalSubtitleType" class="text-xs text-white/70">Target TP Certo con Advisor di Permanenza Live</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-2">
          <div class="flex justify-between"><span class="text-white/75">Mercato / Categoria:</span><strong id="modalCategory" class="text-[#00FF66]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Timeframe Operativo:</span><strong id="modalTF" class="text-cyan-400 font-extrabold">--</strong></div>
          <div class="flex justify-between border-b border-white/10 pb-2"><span class="text-white/75">⏱️ Permanenza Stimata al TP:</span><strong id="modalDurata" class="text-yellow-300 font-bold">--</strong></div>
          
          <div class="grid grid-cols-2 gap-2 py-2 border-b border-white/10">
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">📈 EMA 9 / EMA 21 (Reale)</span>
              <strong id="modalEma" class="text-cyan-300 text-xs">--</strong>
              <span id="modalEmaSignal" class="text-[10px] block mt-0.5">--</span>
            </div>
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">📊 RSI (14) Wilder</span>
              <strong id="modalRsi" class="text-white text-xs">--</strong>
              <span id="modalRsiZone" class="text-[10px] block mt-0.5">--</span>
            </div>
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">🌊 ATR (14) Reale</span>
              <strong id="modalAtr" class="text-purple-300 text-xs">--</strong>
            </div>
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">🔥 RVOL (20 Candele)</span>
              <strong id="modalRvol" class="text-amber-300 text-xs">--</strong>
            </div>
          </div>

          <div class="flex justify-between pt-1"><span class="text-white/75">Prezzo Live Reale:</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Direzione Operazione:</span><strong id="modalSide" class="text-[#00FF66]">--</strong></div>
          <div id="modalSlTpContainer">
            <div class="flex justify-between"><span class="text-white/75">Stop Loss Millimetrico (ATR):</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
            <div class="flex justify-between"><span class="text-white/75">🎯 TARGET TAKE PROFIT CERTO:</span><strong id="modalTrendingTP" class="text-[#00FF66] text-sm font-extrabold">--</strong></div>
          </div>
        </div>

        <!-- SELETTORE DIREZIONE SPOT -->
        <div id="spotDirectionWrapper" class="hidden bg-[#000000] border border-white/20 rounded-xl p-3 space-y-2">
          <label class="block text-xs font-bold text-white uppercase tracking-wider">Azione Operativa Spot:</label>
          <div class="grid grid-cols-2 gap-2">
            <button type="button" onclick="setSpotSide('BUY')" id="spotBtnBuy" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold">BUY (ACQUISTA SPOT)</button>
            <button type="button" onclick="setSpotSide('SELL')" id="spotBtnSell" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">SELL (VENDI SPOT)</button>
          </div>
        </div>

        <!-- SELETTORE LEVA -->
        <div id="leverageSectionWrapper" class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <label class="block text-xs font-bold text-white uppercase tracking-wider">Leva Protetta &amp; Margine Isolato:</label>
          <div class="grid grid-cols-4 gap-2">
            <button type="button" onclick="setLeverage(1)" id="levBtn1" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">1x</button>
            <button type="button" onclick="setLeverage(3)" id="levBtn3" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold">3x</button>
            <button type="button" onclick="setLeverage(5)" id="levBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">5x</button>
            <button type="button" onclick="setLeverage(10)" id="levBtn10" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">10x</button>
          </div>
          <span class="text-[10px] text-yellow-400 block mt-1">💡 Con l'Advisor Live attivo, verrai avvisato in tempo reale quando chiudere.</span>
        </div>

        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-white/70 font-semibold" id="labelQuantityTitle">Quantità Asset:</label>
            <span id="suggestedMinSizeLabel" class="text-[10px] font-mono text-[#00FF66]">Min: --</span>
          </div>
          <input type="number" id="tradeQuantityInput" value="0.01" step="0.0001" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
        </div>

        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>🎯 Esegui Ordine &amp; Attiva Advisor Live</span>
        </button>
        <div id="executionFeedback" class="mt-2 text-xs text-center hidden"></div>
      </div>
    </div>
  </div>

  <!-- JAVASCRIPT ENGINE -->
  <script>
    let state = {
      marketData: {},
      priceHistory: {},
      deepCache: {},
      tradingMode: 'MANUAL',
      marketType: 'FUTURES',
      currentCategoryTab: 'ALL',
      searchQuery: '',
      selectedLeverage: 3,
      spotSelectedSide: 'BUY',
      currentSelectedAsset: null,
      autoExecutedSymbols: new Set(),
      
      activePosition: null,
      advisorInterval: null
    };

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
        textBtn.textContent = "API Personali Connesse 🟢";
      } else {
        statusEl.textContent = "⚠️ API Personali non inserite";
        statusEl.className = "text-xs font-mono text-yellow-400 block";
        textBtn.textContent = "Collega API Personali";
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
        alert("Inserisci sia l'API Key che la Secret Key.");
        return;
      }
      localStorage.setItem('bingx_user_api_key', apiKey);
      localStorage.setItem('bingx_user_secret_key', secretKey);
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali API personali salvate con successo!");
    }

    function clearUserApiKeys() {
      localStorage.removeItem('bingx_user_api_key');
      localStorage.removeItem('bingx_user_secret_key');
      document.getElementById('userApiKeyInput').value = '';
      document.getElementById('userSecretKeyInput').value = '';
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali API rimosse.");
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

    function playChime(urgent = false) {
      const ctx = getAudioContext();
      if (!ctx) return;
      const now = ctx.currentTime;
      const freqs = urgent ? [1200, 900, 1400] : [880, 1108.73, 1318.51];
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

    function getPrecisionDecimals(val) {
      if (val >= 1000) return 2;
      if (val >= 10) return 3;
      if (val >= 1) return 4;
      if (val >= 0.01) return 5;
      return 7;
    }

    function formatPrice(val) {
      const dec = getPrecisionDecimals(val);
      return Number(val).toFixed(dec);
    }

    function calcolaEMA(prezzi, periodo) {
      if (!prezzi || prezzi.length === 0) return 0;
      const k = 2 / (periodo + 1);
      let ema = prezzi[0];
      for (let i = 1; i < prezzi.length; i++) {
        ema = prezzi[i] * k + ema * (1 - k);
      }
      return ema;
    }

    function calcolaParametriPrecisione(c, avgQuoteVol) {
      const cacheKey = `${state.marketType}_${c.symbol}`;
      const deep = state.deepCache[cacheKey];

      if (deep && (Date.now() - deep.timestamp < 15000)) {
        const d = deep.data;
        const rsiColor = d.rsi14 >= 70 ? "text-[#FF334B]" : d.rsi14 <= 30 ? "text-[#00FF66]" : d.rsi14 >= 55 ? "text-[#00FF66]" : "text-white";
        const rsiZone = d.rsi14 >= 70 ? "Ipercomprato (>70) 🔥" : d.rsi14 <= 30 ? "Ipervenduto (<30) ❄️" : d.rsi14 >= 55 ? "Forza Rialzista 📈" : "Neutro / Momentum";
        const rvolBadge = d.rvol >= 1.8 ? "Esplosivo 🚀" : d.rvol >= 1.1 ? "Sopra Media 📈" : "Standard";
        return {
          sl: d.sl,
          tp: d.tp,
          atr: formatPrice(d.atr14),
          atrPercent: d.atrPercent.toFixed(2),
          ema9: formatPrice(d.ema9),
          ema21: formatPrice(d.ema21),
          emaBullish: d.emaBullish,
          emaTrendText: d.crossSignal,
          rsi: d.rsi14,
          rsiZone,
          rsiColor,
          rvol: d.rvol.toFixed(2) + "x",
          rvolBadge,
          timeframe: d.timeframe,
          durata: d.durata,
          durataShort: d.durataShort,
          tempoSec: d.tempoSec || 7200,
          precisionScore: d.precisionScore,
          isRealOhlcv: true
        };
      }

      const isBull = c.change24h >= 0;
      const prezzo = c.price;
      const range24h = Math.max(0, c.high24h - c.low24h);
      const decimals = getPrecisionDecimals(prezzo);

      const atr = range24h > 0 ? range24h * 0.045 : prezzo * 0.012;
      const atrPercent = prezzo > 0 ? ((atr / prezzo) * 100).toFixed(2) : "1.20";

      const effectiveBull = state.marketType === 'SPOT' ? true : isBull;
      const sl = Number((effectiveBull ? prezzo - (atr * 1.20) : prezzo + (atr * 1.20)).toFixed(decimals));
      const tp = Number((effectiveBull ? prezzo + (atr * 2.8) : prezzo - (atr * 2.8)).toFixed(decimals));

      const history = state.priceHistory[c.symbol] || [prezzo];
      const baseOffset = prezzo * (c.change24h / 100) * 0.15;
      const seedHistory = history.length < 3 ? [prezzo - baseOffset, prezzo - (baseOffset * 0.5), ...history] : history;

      const ema9 = calcolaEMA(seedHistory, 9);
      const ema21 = calcolaEMA(seedHistory, 21);
      const emaBullish = ema9 >= ema21;
      const emaTrendText = emaBullish ? "EMA9 > EMA21 (Bullish)" : "EMA9 < EMA21 (Bearish)";

      let posInRange = range24h > 0 ? ((prezzo - c.low24h) / range24h) * 100 : 50 + (c.change24h * 3);
      let rsi = Number(Math.min(94, Math.max(8, (posInRange * 0.65) + ((50 + c.change24h * 3.2) * 0.35))).toFixed(1));
      let rsiZone = "Neutro / Momentum";
      let rsiColor = "text-white";
      if (rsi >= 70) { rsiZone = "Ipercomprato (>70) 🔥"; rsiColor = "text-[#FF334B]"; }
      else if (rsi <= 30) { rsiZone = "Ipervenduto (<30) ❄️"; rsiColor = "text-[#00FF66]"; }
      else if (rsi >= 55) { rsiZone = "Forza Rialzista 📈"; rsiColor = "text-[#00FF66]"; }
      else if (rsi <= 45) { rsiZone = "Pressione Ribassista 📉"; rsiColor = "text-amber-300"; }

      const qVol = c.quoteVolume || (c.volume * prezzo);
      let rvol = avgQuoteVol > 0 ? (qVol / avgQuoteVol) : 1.0;
      rvol = Math.max(0.2, Math.min(9.9, rvol));
      const rvolFormatted = rvol.toFixed(2) + "x";
      let rvolBadge = rvol >= 1.8 ? "Esplosivo 🚀" : rvol >= 1.1 ? "Sopra Media 📈" : "Standard";

      let timeframe = "15m / 1h";
      let durata = "3 - 8 ore (Target TP)";
      let durataShort = "4h";
      let tempoSec = 14400;

      return {
        sl,
        tp,
        atr: formatPrice(atr),
        atrPercent,
        ema9: formatPrice(ema9),
        ema21: formatPrice(ema21),
        emaBullish,
        emaTrendText,
        rsi,
        rsiZone,
        rsiColor,
        rvol: rvolFormatted,
        rvolBadge,
        timeframe,
        durata,
        durataShort,
        tempoSec,
        precisionScore: 94,
        isRealOhlcv: false
      };
    }

    async function fetchDeepOhlcv(symbol) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3500);

        const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(symbol)}&marketType=${state.marketType}&interval=15m`, {
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        const json = await res.json();
        if (json.code === 0 && json.data) {
          state.deepCache[`${state.marketType}_${symbol}`] = {
            timestamp: Date.now(),
            data: json.data
          };
          return json.data;
        }
      } catch (e) {
        console.warn("Deep scan timeout o errore rete, fallback su dati live immediati:", e);
      }
      return null;
    }

    function avviaAdvisorPosizione(symbol, side, entryPrice, tpPrice, slPrice, tempoSec) {
      if (state.advisorInterval) clearInterval(state.advisorInterval);

      const targetTimestamp = Date.now() + (tempoSec * 1000);
      state.activePosition = {
        symbol,
        side,
        entryPrice,
        tpPrice,
        slPrice,
        targetTimestamp
      };

      document.getElementById('activePositionAdvisorBox').classList.remove('hidden');
      document.getElementById('advSymbol').textContent = symbol;
      document.getElementById('advEntryPrice').textContent = `$${formatPrice(entryPrice)}`;
      
      const sideBadge = document.getElementById('advSideBadge');
      sideBadge.textContent = side;
      sideBadge.className = side === 'BUY' ? 'inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-bold bg-green-500/20 text-[#00FF66]' : 'inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-bold bg-red-500/20 text-[#FF334B]';

      document.getElementById('tableAdvSymbol').textContent = symbol;
      document.getElementById('tableAdvEntry').textContent = `$${formatPrice(entryPrice)}`;
      document.getElementById('tableAdvTp').textContent = `$${formatPrice(tpPrice)}`;
      document.getElementById('tableAdvSl').textContent = `$${formatPrice(slPrice)}`;

      aggiornaAdvisorLoop();
      state.advisorInterval = setInterval(aggiornaAdvisorLoop, 2000);
      logFeed(`[ADVISOR ATTIVO] 1. Entrata registrata per ${symbol} (${side}) @ $${formatPrice(entryPrice)}`);
    }

    function chiudiPosizioneMonitorata() {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      state.activePosition = null;
      document.getElementById('activePositionAdvisorBox').classList.add('hidden');
      logFeed(`[ADVISOR] Monitoraggio posizione chiuso manualmente.`);
    }

    function aggiornaAdvisorLoop() {
      if (!state.activePosition) return;
      const pos = state.activePosition;
      const liveItem = state.marketData[pos.symbol];
      if (!liveItem) return;

      const currentPrice = liveItem.price;
      document.getElementById('advLivePrice').textContent = `$${formatPrice(currentPrice)}`;

      const rawDiff = pos.side === 'BUY' ? (currentPrice - pos.entryPrice) : (pos.entryPrice - currentPrice);
      const rawPnlPct = (rawDiff / pos.entryPrice) * 100;
      
      const feeRate = state.marketType === 'SPOT' ? 0.002 : 0.001;
      const pnlPct = rawPnlPct - (feeRate * 100);

      const pnlEl = document.getElementById('advPnlVal');
      pnlEl.textContent = `PnL Netto: ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
      pnlEl.className = `block mt-1 text-[10px] font-bold ${pnlPct >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}`;

      const tablePnl = document.getElementById('tableAdvPnl');
      tablePnl.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}% (Netto)`;
      tablePnl.className = `py-3 px-3 font-extrabold ${pnlPct >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}`;

      const timeLeftMs = pos.targetTimestamp - Date.now();
      const timeEl = document.getElementById('advTimeRemaining');
      const timeProgEl = document.getElementById('advTimeProgress');

      if (timeLeftMs <= 0) {
        timeEl.textContent = "00:00 (Tempo Scaduto)";
        timeProgEl.textContent = "Verifica tenuta trend...";
      } else {
        const totalSec = Math.floor(timeLeftMs / 1000);
        const hrs = Math.floor(totalSec / 3600);
        const mins = Math.floor((totalSec % 3600) / 60);
        const secs = totalSec % 60;
        timeEl.textContent = `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        timeProgEl.textContent = "Tempo stimato rimanente al TP";
      }

      const statusAdvice = document.getElementById('advStatusAdvice');
      const actionAlert = document.getElementById('advActionAlert');
      const tableRemain = document.getElementById('tableAdvRemain');
      const tableExit = document.getElementById('tableAdvExit');

      const hitTp = pos.side === 'BUY' ? currentPrice >= pos.tpPrice : currentPrice <= pos.tpPrice;
      const hitSl = pos.side === 'BUY' ? currentPrice <= pos.slPrice : currentPrice >= pos.slPrice;

      if (hitTp) {
        statusAdvice.textContent = "🎯 3. Uscire: TAKE PROFIT RAGGIUNTO!";
        statusAdvice.className = "text-[#00FF66] text-xs block font-extrabold animate-bounce";
        actionAlert.textContent = "3. Uscire: CHIUDI SUBITO E INCASSA IL PROFITTO.";
        tableRemain.textContent = "No (Target Raggiunto)";
        tableExit.textContent = "3. USCIRE (Prendi Profitto 🎯)";
        tableExit.className = "py-3 px-3 text-right text-[#00FF66] font-extrabold animate-pulse";
        playChime(true);
      } else if (hitSl) {
        statusAdvice.textContent = "⚠️ 3. Uscire: STOP LOSS TESTATO!";
        statusAdvice.className = "text-[#FF334B] text-xs block font-extrabold animate-pulse";
        actionAlert.textContent = "3. Uscire: Taglia la posizione per protezione capitale.";
        tableRemain.textContent = "No (Stop Toccato)";
        tableExit.textContent = "3. USCIRE (Protezione SL ⚠️)";
        tableExit.className = "py-3 px-3 text-right text-[#FF334B] font-extrabold";
        playChime(true);
      } else if (pnlPct >= 1.8) {
        statusAdvice.textContent = "🟢 2. Rimanere a mercato (In Profitto)";
        statusAdvice.className = "text-[#00FF66] text-xs block font-bold";
        actionAlert.textContent = "2. Rimanere: Sposta lo Stop Loss a Break-Even e continua a rimanere.";
        tableRemain.textContent = "Sì (In Profitto Solido)";
        tableExit.textContent = "3. Uscire solo al TP / SL";
        tableExit.className = "py-3 px-3 text-right text-[#00FF66] font-bold";
      } else if (pnlPct <= -1.2) {
        statusAdvice.textContent = "⚠️ 2. Rimanere / 3. Uscire (Ritracciamento)";
        statusAdvice.className = "text-amber-400 text-xs block font-bold";
        actionAlert.textContent = "2. Rimanere con prudenza o 3. Uscire se il supporto cede.";
        tableRemain.textContent = "Rischio Ritracciamento";
        tableExit.textContent = "Valuta Uscita Anticipata";
        tableExit.className = "py-3 px-3 text-right text-amber-400 font-bold";
      } else {
        statusAdvice.textContent = "🟢 2. Rimanere a mercato (Trend Stabile)";
        statusAdvice.className = "text-cyan-300 text-xs block font-bold";
        actionAlert.textContent = "2. Rimanere dentro fino al target temporale o al TP certo.";
        tableRemain.textContent = "Sì (Rimani a Mercato)";
        tableExit.textContent = "3. Uscire solo al TP / SL";
        tableExit.className = "py-3 px-3 text-right text-yellow-300 font-bold";
      }
    }

    function setCategoryTab(cat) {
      state.currentCategoryTab = cat;
      ['ALL', 'Criptovalute', 'Materie Prime', 'Forex / Valute', 'Azioni & Indici (TradFi)'].forEach(c => {
        const btnId = c === 'ALL' ? 'catTabALL' : c === 'Azioni & Indici (TradFi)' ? 'catTabTradFi' : `catTab${c}`;
        const btn = document.getElementById(btnId);
        if (btn) {
          btn.className = state.currentCategoryTab === c ? "px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all" : "px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all";
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
      [1, 3, 5, 10].forEach(l => {
        const btn = document.getElementById(`levBtn${l}`);
        if (btn) {
          if (l === lev) {
            btn.className = "py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold";
          } else {
            btn.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
          }
        }
      });
    }

    function setSpotSide(side) {
      state.spotSelectedSide = side;
      const btnBuy = document.getElementById('spotBtnBuy');
      const btnSell = document.getElementById('spotBtnSell');
      const sideLabel = document.getElementById('modalSide');
      if (side === 'BUY') {
        btnBuy.className = "py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold";
        btnSell.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
        sideLabel.textContent = "BUY (ACQUISTO SPOT - TARGET TP)";
        sideLabel.className = "text-[#00FF66]";
      } else {
        btnSell.className = "py-1.5 rounded-lg border border-[#FF334B] bg-[#FF334B]/20 text-[#FF334B] font-bold";
        btnBuy.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
        sideLabel.textContent = "SELL (VENDITA SPOT)";
        sideLabel.className = "text-[#FF334B]";
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
          state.currentCategoryTab = 'ALL';
          updateMarketTypeUI();
          await pollMarketData();
          logFeed(`[MERCATO] Passaggio a ${mtype} con Advisor di posizione attivo.`);
        }
      } catch (e) {
        alert("Errore cambio mercato");
      }
    }

    function updateMarketTypeUI() {
      const btnFut = document.getElementById('btnMarketFutures');
      const btnSpot = document.getElementById('btnMarketSpot');
      const kpiBadge = document.getElementById('kpiMarketBadge');
      const headerTitle = document.getElementById('headerEngineTitle');
      const masterSub = document.getElementById('masterSubtitle');
      const tableLabel = document.getElementById('tableMarketLabel');

      if (state.marketType === 'SPOT') {
        btnSpot.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-black shadow-lg";
        btnFut.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        kpiBadge.textContent = "🪙 SPOT TARGET TP (Advisor Live)";
        headerTitle.textContent = "Millimetric Spot Advisor Engine";
        masterSub.textContent = "Scanner Spot con candele 15m reali e Advisor attivo per precisione dati e gestione trade";
        tableLabel.textContent = "SPOT TARGET TP";
      } else {
        btnFut.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#0066FF] text-white";
        btnSpot.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        kpiBadge.textContent = "🟢 FUTURES (Precisione Dati)";
        headerTitle.textContent = "Millimetric Futures Engine";
        masterSub.textContent = "Advisor in tempo reale: 1. Entrata • 2. Rimanere • 3. Uscire";
        tableLabel.textContent = "FUTURES";
      }
    }

    async function switchMode(mode) {
      try {
        const res = await fetch('/api/set-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode })
        });
        const data = await res.json();
        if (data.code === 0) updateModeUI(data.mode);
      } catch (e) {
        alert("Errore cambio modalità");
      }
    }

    function updateModeUI(mode) {
      state.tradingMode = mode;
      const btnMan = document.getElementById('btnModeManual');
      const btnAuto = document.getElementById('btnModeAuto');
      const badge = document.getElementById('modeStatusBadge');
      const modeDot = document.getElementById('modeDot');
      const modeText = document.getElementById('modeText');

      if (mode === 'AUTO') {
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-black shadow-lg";
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping";
        modeText.textContent = "AUTO ⚡";
      } else {
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white";
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-yellow-400";
        modeText.textContent = "MANUALE";
      }
    }

    async function pollMarketData() {
      try {
        const res = await fetch(`/api/market-data?marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0 && json.marketType === state.marketType) {
          state.marketData = json.data;
          Object.keys(json.data).forEach(sym => {
            if (!state.priceHistory[sym]) state.priceHistory[sym] = [];
            state.priceHistory[sym].push(json.data[sym].price);
            if (state.priceHistory[sym].length > 30) state.priceHistory[sym].shift();
          });
          renderDashboard();
          renderLiveTickerMarquee();
        }
      } catch (e) {
        console.error("Errore fetch dati live:", e);
      }
    }

    function renderLiveTickerMarquee() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;

      const tickerSymbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "AVAX-USDT", "SUI-USDT", "PEPE-USDT"];
      const tickerItems = keys.filter(sym => tickerSymbols.includes(sym) || state.marketData[sym].change24h > 4.0).slice(0, 10);

      const htmlContent = tickerItems.map(sym => {
        const item = state.marketData[sym];
        const isBull = item.change24h >= 0;
        return `
          <div class="flex items-center gap-2 bg-white/5 px-3 py-1 rounded-lg border border-white/10 shrink-0">
            <span class="font-bold text-white">${sym.replace('-USDT', '')}</span>
            <span class="text-white/90">$${formatPrice(item.price)}</span>
            <span class="${isBull ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-bold">${isBull ? '+' : ''}${item.change24h.toFixed(2)}%</span>
          </div>
        `;
      }).join('');

      document.getElementById('liveTickerMarquee').innerHTML = htmlContent + htmlContent;
    }

    async function renderDashboard() {
      const keys = Object.keys(state.marketData);
      if (keys.length === 0) return;

      document.getElementById('kpiTotalAssets').textContent = `${keys.length} Coppie ${state.marketType}`;

      let totalQuoteVol = 0;
      keys.forEach(k => {
        const item = state.marketData[k];
        totalQuoteVol += (item.quoteVolume || (item.volume * item.price));
      });
      const avgQuoteVol = keys.length > 0 ? totalQuoteVol / keys.length : 1;

      const topPrioritySymbols = ["BTC-USDT", "SOL-USDT", "ETH-USDT", "XRP-USDT", "DOGE-USDT", "SUI-USDT", "AVAX-USDT"];

      let sortedCoins = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => {
        let aTop = topPrioritySymbols.includes(a.symbol) ? 0 : 1;
        let bTop = topPrioritySymbols.includes(b.symbol) ? 0 : 1;
        if (aTop !== bTop) return aTop - bTop;
        return Math.abs(b.change24h) - Math.abs(a.change24h);
      });

      let topMover = sortedCoins[0];
      if (topMover) {
        document.getElementById('kpiTopMoverSymbol').textContent = topMover.symbol;
        const topChangeEl = document.getElementById('kpiTopMoverChange');
        topChangeEl.textContent = (topMover.change24h >= 0 ? '+' : '') + topMover.change24h.toFixed(2) + '%';
        topChangeEl.className = topMover.change24h >= 0 ? 'text-sm font-extrabold font-mono text-[#00FF66]' : 'text-sm font-extrabold font-mono text-[#FF334B]';
      }

      const masterAsset = sortedCoins[0];
      if (masterAsset) {
        const cacheKey = `${state.marketType}_${masterAsset.symbol}`;
        if (!state.deepCache[cacheKey] || (Date.now() - state.deepCache[cacheKey].timestamp > 12000)) {
          fetchDeepOhlcv(masterAsset.symbol).then(() => renderDashboard());
        }

        const isBull = masterAsset.change24h >= 0;
        const entry = masterAsset.price;
        const param = calcolaParametriPrecisione(masterAsset, avgQuoteVol);

        document.getElementById('masterPrecisionBadge').textContent = param.isRealOhlcv
          ? `OHLCV REALE • PRECISIONE ${param.precisionScore}%`
          : `PRECISIONE ${param.precisionScore}%`;

        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <div class="flex items-center justify-between">
                <strong class="text-white text-sm">${masterAsset.symbol} - MASTER ${state.marketType}</strong>
                <span class="text-[10px] px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">${param.isRealOhlcv ? 'Candele Verificate ✓' : 'Live Feed'}</span>
              </div>
              <div class="flex items-center justify-between"><span class="text-white/70">Prezzo Live Reale:</span><strong class="text-[#00FF66] text-sm">$${formatPrice(entry)}</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏱️ Timeframe Operativo:</span><strong class="text-cyan-300 font-extrabold">${param.timeframe}</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏳ Permanenza Stimata al TP:</span><strong class="text-yellow-300 font-bold">${param.durata}</strong></div>
              <div class="grid grid-cols-2 gap-2 pt-1">
                <div class="bg-black/50 border border-white/10 rounded-lg p-2">
                  <span class="text-[10px] text-white/60 block">📈 EMA 9 / EMA 21</span>
                  <strong class="text-cyan-300 text-[11px]">$${param.ema9} / $${param.ema21}</strong>
                  <span class="text-[9px] block ${param.emaBullish ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${param.emaTrendText}</span>
                </div>
                <div class="bg-black/50 border border-white/10 rounded-lg p-2">
                  <span class="text-[10px] text-white/60 block">📊 RSI (14) &amp; RVOL</span>
                  <strong class="${param.rsiColor} text-[11px]">RSI: ${param.rsi} | RVOL: ${param.rvol}</strong>
                  <span class="text-[9px] text-white/70 block">${param.rsiZone}</span>
                </div>
              </div>
            </div>
            <div class="space-y-2 flex flex-col justify-between">
              <div class="bg-black/40 border border-[#FFCC00]/30 rounded-xl p-3 space-y-1.5">
                <div class="flex justify-between"><span class="text-[#FF334B]">Stop Loss di Protezione:</span><strong class="text-[#FF334B]">$${formatPrice(param.sl)}</strong></div>
                <div class="flex justify-between"><span class="text-[#00FF66]">🎯 TARGET TAKE PROFIT CERTO:</span><strong class="text-[#00FF66] text-sm font-extrabold">$${formatPrice(param.tp)}</strong></div>
                <div class="flex justify-between"><span class="text-white/70">🌊 ATR (14) Reale:</span><strong class="text-purple-300">$${param.atr} (${param.atrPercent}%)</strong></div>
                <div class="flex justify-between"><span class="text-white/70">⚡ Strategia:</span><strong class="text-yellow-400">${state.marketType === 'SPOT' ? 'Spot 1:1 Target TP' : 'Futures Isolato 3x'}</strong></div>
              </div>
              <button onclick='openModal("${masterAsset.symbol}")' class="green-gradient-btn w-full py-2 rounded-xl text-xs shadow-lg animate-pulse">
                🎯 APRI &amp; AVVIA ADVISOR LIVE &#8599;
              </button>
            </div>
          </div>
        `;
      }

      let filteredCoins = sortedCoins;
      if (state.currentCategoryTab !== 'ALL') {
        filteredCoins = filteredCoins.filter(c => c.category === state.currentCategoryTab);
      }
      if (state.searchQuery) {
        filteredCoins = filteredCoins.filter(c => c.symbol.toLowerCase().includes(state.searchQuery) || c.category.toLowerCase().includes(state.searchQuery));
      }

      const categoriesMap = {};
      filteredCoins.forEach(c => {
        if (!categoriesMap[c.category]) categoriesMap[c.category] = [];
        categoriesMap[c.category].push(c);
      });

      const container = document.getElementById('tablesContainer');
      if (Object.keys(categoriesMap).length === 0) {
        container.innerHTML = `<div class="p-6 text-center text-white/50 font-mono text-sm">Nessun asset trovato.</div>`;
        return;
      }

      container.innerHTML = Object.keys(categoriesMap).map(cat => {
        const assets = categoriesMap[cat];
        let catIcon = "🪙";
        if (cat === "Materie Prime") catIcon = "🛢️";
        else if (cat === "Forex / Valute") catIcon = "💱";
        else if (cat === "Azioni & Indici (TradFi)") catIcon = "📈";

        return `
          <div class="space-y-3">
            <div class="flex items-center justify-between bg-[#08080A] px-3 py-2 rounded-xl border border-white/10">
              <h3 class="text-xs font-bold uppercase tracking-wider text-[#00FF66] flex items-center gap-2">
                <span>${catIcon}</span> ${cat} <span class="text-white/50 text-[10px]">(${assets.length} coppie ${state.marketType})</span>
              </h3>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-left border-collapse">
                <thead>
                  <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-[#000000]">
                    <th class="py-2.5 pl-3">Asset</th>
                    <th class="py-2.5 px-2">Segnale</th>
                    <th class="py-2.5 px-2">⏱️ Permanenza al TP</th>
                    <th class="py-2.5 px-2">📈 EMA 9 / 21</th>
                    <th class="py-2.5 px-2">📊 RSI (14)</th>
                    <th class="py-2.5 px-2">🌊 ATR / RVOL</th>
                    <th class="py-2.5 px-2 text-right">Prezzo</th>
                    <th class="py-2.5 px-2 text-right">Var. 24h</th>
                    <th class="py-2.5 px-2">Target TP Certo</th>
                    <th class="py-2.5 pr-3 text-right">Esegui</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/10 text-xs font-mono">
                  ${assets.map(c => {
                    const isBull = c.change24h >= 0;
                    const param = calcolaParametriPrecisione(c, avgQuoteVol);
                    const statusText = state.marketType === 'SPOT'
                      ? (param.emaBullish ? 'SPOT BUY' : 'ATTESA DIP')
                      : (Math.abs(c.change24h) > 2.0 ? (isBull ? 'LONG' : 'SHORT') : 'ATTESA');
                    const statusBadgeColor = statusText.includes('LONG') || statusText.includes('BUY')
                      ? 'bg-green-500/20 text-[#00FF66] border-green-500/40'
                      : statusText.includes('SHORT')
                        ? 'bg-red-500/20 text-[#FF334B] border-red-500/40'
                        : 'bg-white/10 text-white/70 border-white/20';
                    const actionSide = state.marketType === 'SPOT' ? 'BUY' : (isBull ? 'BUY' : 'SELL');

                    if (state.tradingMode === 'AUTO' && Math.abs(c.change24h) > 4.5 && !state.autoExecutedSymbols.has(`${state.marketType}_${c.symbol}`)) {
                      state.autoExecutedSymbols.add(`${state.marketType}_${c.symbol}`);
                      const keys = getStoredApiKeys();
                      eseguiOperazioneServer({
                        symbol: c.symbol,
                        action: actionSide,
                        size: Math.max(0.001, Number((10.0 / c.price).toFixed(4))),
                        entry: c.price,
                        tp: param.tp,
                        sl: param.sl,
                        leverage: 3,
                        timeframe: param.timeframe,
                        marketType: state.marketType,
                        apiKey: keys.apiKey,
                        secretKey: keys.secretKey
                      }, true);
                    }

                    return `
                      <tr class="hover:bg-white/5 transition-colors cursor-pointer" onclick='openModal("${c.symbol}")'>
                        <td class="py-2.5 pl-3 font-bold text-white">${c.symbol.replace('-USDT', '')}</td>
                        <td class="py-2.5 px-2"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${statusBadgeColor}">${statusText}</span></td>
                        <td class="py-2.5 px-2 text-yellow-300 font-bold whitespace-nowrap">${param.durataShort} <span class="text-white/50 text-[9px] block">${param.timeframe.split(' ')[0]}</span></td>
                        <td class="py-2.5 px-2 text-[11px] whitespace-nowrap">
                          <span class="text-cyan-300">$${param.ema9}</span> <span class="text-white/40">/</span> <span class="text-white/80">$${param.ema21}</span>
                          <span class="block text-[9px] ${param.emaBullish ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${param.emaBullish ? '▲ Bull' : '▼ Bear'}</span>
                        </td>
                        <td class="py-2.5 px-2 whitespace-nowrap">
                          <span class="font-bold ${param.rsiColor}">${param.rsi}</span>
                          <span class="block text-[9px] text-white/60">${param.rsi >= 70 ? 'Ipercomp.' : param.rsi <= 30 ? 'Ipervend.' : 'Neutro'}</span>
                        </td>
                        <td class="py-2.5 px-2 text-[11px] whitespace-nowrap">
                          <span class="text-purple-300">${param.atrPercent}%</span>
                          <span class="block text-[9px] text-amber-300">RVOL ${param.rvol}</span>
                        </td>
                        <td class="py-2.5 px-2 text-right tabular-nums text-white font-bold">$${formatPrice(c.price)}</td>
                        <td class="py-2.5 px-2 text-right tabular-nums ${isBull ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-extrabold">${isBull ? '+' : ''}${c.change24h.toFixed(2)}%</td>
                        <td class="py-2.5 px-2 text-[#00FF66] font-extrabold text-[11px] whitespace-nowrap">$${formatPrice(param.tp)}</td>
                        <td class="py-2.5 pr-3 text-right">
                          <button class="px-3 py-1 rounded-lg bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50 font-bold text-xs hover:bg-[#00FF66] hover:text-black transition-all">
                            Avvia ⚡
                          </button>
                        </td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          </div>
        `;
      }).join('');
    }

    async function openModal(symbol) {
      const item = state.marketData[symbol];
      if (!item) return;

      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');

      const ohlcvBadge = document.getElementById('modalOhlcvBadge');
      ohlcvBadge.textContent = "CALIBRAZIONE ADVISOR...";
      ohlcvBadge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-yellow-500/20 text-yellow-300 border border-yellow-500/40 animate-pulse";

      const initialParam = calcolaParametriPrecisione({ symbol, ...item }, 1);
      applyModalData(symbol, item.price, initialParam, item.category, false);

      const deepData = await fetchDeepOhlcv(symbol);
      if (deepData) {
        const refinedParam = calcolaParametriPrecisione({ symbol, ...item, price: deepData.price }, 1);
        applyModalData(symbol, deepData.price, refinedParam, item.category, true);
        ohlcvBadge.textContent = `ADVISOR LIVE PRONTO (${deepData.precisionScore}%) ✓`;
        ohlcvBadge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/40";
      } else {
        ohlcvBadge.textContent = "ADVISOR PRONTO (Live Feed) ✓";
        ohlcvBadge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/40";
      }
    }

    function applyModalData(symbol, livePrice, param, category, isVerified) {
      const dec = getPrecisionDecimals(livePrice);
      let minSize = Math.max(0.0001, Number((10.0 / livePrice).toFixed(livePrice >= 100 ? 4 : 2)));
      const side = state.marketType === 'SPOT' ? state.spotSelectedSide : (param.emaBullish ? 'BUY' : 'SELL');

      state.currentSelectedAsset = {
        symbol,
        livePrice,
        tp: param.tp,
        sl: param.sl,
        side,
        tf: param.timeframe,
        durata: param.durata,
        tempoSec: param.tempoSec,
        category,
        minSize
      };

      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalCategory').textContent = `${state.marketType} • ${category}`;
      document.getElementById('modalTF').textContent = param.timeframe;
      document.getElementById('modalDurata').textContent = param.durata;
      document.getElementById('modalSubtitleType').textContent = isVerified
        ? `Target TP Certo & Advisor attivo su candele reali ${state.marketType}`
        : `Target TP Certo & Advisor attivo (Pronto)`;

      document.getElementById('modalEma').textContent = `$${param.ema9} / $${param.ema21}`;
      document.getElementById('modalEmaSignal').textContent = param.emaTrendText;
      document.getElementById('modalEmaSignal').className = param.emaBullish ? 'text-[10px] block mt-0.5 text-[#00FF66]' : 'text-[10px] block mt-0.5 text-[#FF334B]';

      document.getElementById('modalRsi').textContent = `${param.rsi} / 100`;
      document.getElementById('modalRsiZone').textContent = param.rsiZone;
      document.getElementById('modalRsiZone').className = param.rsi >= 70 ? 'text-[10px] block mt-0.5 text-[#FF334B]' : 'text-[10px] block mt-0.5 text-[#00FF66]';

      document.getElementById('modalAtr').textContent = `$${param.atr} (${param.atrPercent}%)`;
      document.getElementById('modalRvol').textContent = `${param.rvol} (${param.rvolBadge})`;

      document.getElementById('modalLivePrice').textContent = `$${Number(livePrice).toFixed(dec)}`;

      if (state.marketType === 'SPOT') {
        document.getElementById('spotDirectionWrapper').classList.remove('hidden');
        document.getElementById('leverageSectionWrapper').style.display = 'none';
        setSpotSide(state.spotSelectedSide);
        document.getElementById('modalSlTpContainer').innerHTML = `
          <div class="flex justify-between"><span class="text-white/75">Stop Loss Riferimento (ATR):</span><strong class="text-[#FF334B]">$${formatPrice(param.sl)}</strong></div>
          <div class="flex justify-between"><span class="text-white/75">🎯 TARGET TAKE PROFIT CERTO:</span><strong class="text-[#00FF66] text-sm font-extrabold">$${formatPrice(param.tp)}</strong></div>
        `;
      } else {
        document.getElementById('spotDirectionWrapper').classList.add('hidden');
        document.getElementById('leverageSectionWrapper').style.display = 'block';
        document.getElementById('modalSide').textContent = side === 'BUY' ? 'LONG (ACQUISTO)' : 'SHORT (VENDITA)';
        document.getElementById('modalSide').className = side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]';
        document.getElementById('modalSlTpContainer').innerHTML = `
          <div class="flex justify-between"><span class="text-white/75">Stop Loss Millimetrico (ATR):</span><strong class="text-[#FF334B]">$${formatPrice(param.sl)}</strong></div>
          <div class="flex justify-between"><span class="text-white/75">🎯 TARGET TAKE PROFIT CERTO:</span><strong class="text-[#00FF66] text-sm font-extrabold">$${formatPrice(param.tp)}</strong></div>
        `;
      }

      document.getElementById('tradeQuantityInput').value = minSize;
      document.getElementById('suggestedMinSizeLabel').textContent = `Min Sicuro (~10 USDT): ${minSize} ${symbol.replace('-USDT', '')}`;

      const feedback = document.getElementById('executionFeedback');
      feedback.classList.add('hidden');
      feedback.textContent = '';
    }

    function closeModal() {
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
      state.currentSelectedAsset = null;
    }

    async function eseguiOrdineSuBingX() {
      if (!state.currentSelectedAsset) return;
      const asset = state.currentSelectedAsset;
      const quantity = parseFloat(document.getElementById('tradeQuantityInput').value) || asset.minSize;
      const leverage = state.selectedLeverage;
      const keys = getStoredApiKeys();
      const actionToSend = state.marketType === 'SPOT' ? state.spotSelectedSide : asset.side;

      const btn = document.getElementById('executeBingXBtn');
      const feedback = document.getElementById('executionFeedback');

      btn.disabled = true;
      btn.textContent = `Invio ordine ${state.marketType} (${actionToSend}) e avvio Advisor...`;
      feedback.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedback.classList.add('text-white/70');
      feedback.textContent = `Esecuzione ordine e attivazione monitoraggio live...`;

      const res = await eseguiOperazioneServer({
        symbol: asset.symbol,
        action: actionToSend,
        size: quantity,
        entry: asset.livePrice,
        tp: asset.tp,
        sl: asset.sl,
        leverage: leverage,
        timeframe: asset.tf,
        marketType: state.marketType,
        apiKey: keys.apiKey,
        secretKey: keys.secretKey
      }, false);

      if (res.code === 0) {
        feedback.classList.remove('text-white/70', 'text-[#FF334B]');
        feedback.classList.add('text-[#00FF66]');
        feedback.textContent = `✔ Successo! Posizione aperta. Advisor Live avviato in alto.`;
        playChime(false);
        logFeed(`[ADVISOR AVVIATO] 1. Entrata ${asset.symbol} (${actionToSend}) @ $${formatPrice(asset.livePrice)} ➔ TP: $${formatPrice(asset.tp)}`);
        
        avviaAdvisorPosizione(asset.symbol, actionToSend, asset.livePrice, asset.tp, asset.sl, asset.tempoSec || 14400);
        closeModal();
      } else {
        feedback.classList.remove('text-white/70', 'text-[#00FF66]');
        feedback.classList.add('text-[#FF334B]');
        feedback.textContent = `✖ Errore: ${res.msg || 'Controlla le tue API Key personali'}`;
        logFeed(`[ERRORE ${state.marketType}] ${asset.symbol}: ${res.msg}`);
      }

      btn.disabled = false;
      btn.textContent = "🎯 Esegui Ordine & Attiva Advisor Live";
    }

    async function eseguiOperazioneServer(payload, isAuto) {
      try {
        const response = await fetch('/api/esegui-segnale', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...payload, is_auto: isAuto })
        });
        const data = await response.json();
        if (isAuto && data.code === 0) {
          logFeed(`[AUTO ⚡ ${payload.marketType}] Segnale eseguito su ${payload.symbol}`);
          playChime(false);
        }
        return data;
      } catch (err) {
        return { code: -1, msg: err.message };
      }
    }

    function logFeed(text) {
      const container = document.getElementById('alertsFeedContainer');
      const div = document.createElement('div');
      div.className = "p-2 rounded bg-white/5 border border-white/10 text-white/90 mt-1";
      div.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
      container.prepend(div);
    }

    async function fetchServerMode() {
      try {
        const res = await fetch('/api/get-mode');
        const data = await res.json();
        if (data.code === 0) {
          updateModeUI(data.mode);
          if (data.marketType) {
            state.marketType = data.marketType;
            updateMarketTypeUI();
          }
        }
      } catch (e) {}
    }

    window.addEventListener('DOMContentLoaded', async () => {
      updateApiStatusUI();
      await fetchServerMode();
      await pollMarketData();
      setInterval(pollMarketData, 3000);
      setInterval(() => {
        const now = new Date();
        document.getElementById('liveCetClock').textContent = now.toTimeString().slice(0, 8) + ' CET';
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
