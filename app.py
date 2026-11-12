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
    """Genera la firma HMAC SHA256 richiesta dalle API di BingX."""
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature


# ==========================================
# GESTIONE ORARIA & NOTIFICA ORARIO GIUSTO
# ==========================================
def verifica_finestra_temporale_sicura() -> tuple[bool, str, str, str, str]:
    """
    Ritorna: (is_safe, messaggio_stato, borsa_riferimento, orario_inizio, orario_fine)
    """
    try:
        tz_roma = pytz.timezone("Europe/Rome")
        adesso = datetime.now(tz_roma)
        ora_minuto = adesso.strftime("%H:%M")
        
        if "01:00" <= ora_minuto < "09:00":
            return True, "🟢 Sessione Asiatica (Accumulazione / Laterale)", "Borsa di Tokyo / Sydney (01:00 - 09:00)", "01:00", "09:00"
        if "08:45" <= ora_minuto < "09:30":
            return False, "⚠️ Apertura Borse Europee / Borsa Italiana (Alta volatilità)", "Borsa Italiana / Europa (09:00 - 09:01)", "09:00", "14:30"
        if "09:30" <= ora_minuto < "14:15":
            return True, "🟡 Mattinata Europea: Liquidità elevata e trend puliti", "Borsa di Londra & Milano (09:30 - 14:30)", "09:30", "14:30"
        if "14:15" <= ora_minuto < "15:20":
            return True, "🟡 Sovrapposizione Forex Londra/NY & Pre-Market USA [Orario ideale]", "Mercato Forex (14:00-17:00) / Pre-Market USA", "14:00", "17:00"
        if "15:20" <= ora_minuto <= "16:00":
            return False, "⚠️ Apertura Wall Street NYSE/NASDAQ (Massima volatilità)", "Mercati USA - NYSE & NASDAQ (15:30)", "15:30", "22:00"
        if "16:00" <= ora_minuto < "21:30":
            return True, "🟡 Sessione Pomeridiana USA: Consolidamento trend principali", "Mercati USA NYSE/NASDAQ (16:00 - 22:00)", "16:00", "22:00"
        
        return False, "⚠️ Chiusura USA / Calo drastico dei volumi notturni", "Mercati Chiusi / Cripto 24-7 (22:00 - 01:00)", "22:00", "01:00"
    except Exception:
        return True, "🟡 Finestra operativa standard", "Mercato Globale 24/7", "09:30", "22:00"


# ==========================================
# MOTORE QUANTISTICO DINAMICO & AUTO-ADATTIVO
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
        soglia_long_dinamica = round(54.0 + min(3.5, max(0.5, volatilita_pct * 0.4)), 1)
        soglia_short_dinamica = round(46.0 - min(3.5, max(0.5, volatilita_pct * 0.4)), 1)

        if market_type == "SPOT":
            bull = ema9 > ema21 and rsi >= soglia_long_dinamica
            bear = False
        else:
            bull = ema9 > ema21 and ema21 >= ema50 * 0.998 and rsi >= soglia_long_dinamica and closes[-1] >= ema9 * 0.997
            bear = ema9 < ema21 and ema21 <= ema50 * 1.002 and rsi <= soglia_short_dinamica and closes[-1] <= ema9 * 1.003

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
    elif tfs["5m"]["trend"] != "NEUTRO":
        exact_tf = "5m"
    elif tfs["1h"]["trend"] != "NEUTRO":
        exact_tf = "1h"

    side = tfs[exact_tf]["trend"]
    if fetch_type == "SPOT":
        side = "BUY"
    elif side == "NEUTRO":
        side = tfs["1h"]["trend"] if tfs["1h"]["trend"] != "NEUTRO" else "BUY"

    base = tfs[exact_tf]
    price = tfs["5m"]["last"]["close"]
    atr_breve = tfs["15m"]["atr14"] if tfs["15m"]["atr14"] > 0 else base["atr14"]
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7
    k = 1 if side == "BUY" else -1

    min_sl_dist = price * 0.006
    max_sl_dist = price * 0.014
    dist_sl = min(max(atr_breve * 0.95, min_sl_dist), max_sl_dist)
    dist_tp = dist_sl * 2.4

    sl = round(price - k * dist_sl, dec)
    tp = round(price + k * dist_tp, dec)

    candle_sec = 900 if exact_tf != "5m" else 300
    ultima_candela = base["last"]
    ampiezza_candela_corrente = abs(ultima_candela["high"] - ultima_candela["low"])
    velocita_candela = max(ampiezza_candela_corrente, atr_breve * 0.2) / candle_sec
    
    mom_multiplier = 1.0 + (abs(base["rsi14"] - 50.0) / 25.0)
    velocita_reale = max(velocita_candela * mom_multiplier, price * 0.00002)
    
    tempo_esatto_sec = int(max(30, min(10800, round(dist_tp / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    is_safe, time_msg, borsa_rif, start_t, end_t = verifica_finestra_temporale_sicura()
    score_precisione = 99.8 if (base["trend"] != "NEUTRO" and is_safe) else (85.0 if is_safe else 60.0)
    momento_giusto = is_safe and base["trend"] != "NEUTRO"

    return {
        "symbol": simbolo, "marketType": market_type, "price": round(price, dec), "side": side,
        "sentiment": f"Quantum {market_type} 🚀 (RSI Dinamico Long>={base.get('sogliaLong', 55)})" if side == "BUY" else f"Quantum {market_type} 📉 (RSI Dinamico Short<={base.get('sogliaShort', 45)})",
        "confluenza": f"{time_msg} 🎯",
        "borsaRif": borsa_rif,
        "momentoGiusto": momento_giusto,
        "exactTf": exact_tf, "precisionScore": score_precisione,
        "ema9": round(base["ema9"], dec), "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"], "atr14": round(atr_breve, dec), "sl": sl, "tp": tp,
        "decimals": dec, "tempoSec": tempo_esatto_sec, "candleTime": timestamp_ultima_candela, "tfs": tfs,
        "isSafeTime": is_safe, "sessionStart": start_t, "sessionEnd": end_t
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
        return jsonify({"code": -99, "msg": "Modalità Automatica disattivata. Attiva la modalità AUTO per l'esecuzione autonoma."})

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
    @keyframes criticalPulse { 0%, 100% { box-shadow: 0 0 25px rgba(255, 51, 75, 0.8), inset 0 0 15px rgba(255, 51, 75, 0.3); border-color: #FF334B; background: rgba(50, 10, 15, 0.95); } 50% { box-shadow: 0 0 45px rgba(255, 51, 75, 1); border-color: #FF334B; background: rgba(80, 15, 20, 0.98); } }
    @keyframes dollarRotateGlow { 0% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #FFD700); } 50% { transform: scale(1.15) rotate(10deg); filter: drop-shadow(0 0 20px #FFD700); } 100% { transform: scale(1) rotate(0deg); filter: drop-shadow(0 0 8px #FFD700); } }
    .dollar-animated-logo { display: inline-block; animation: dollarRotateGlow 2.5s infinite ease-in-out; }
    .priority-signal-box { background: linear-gradient(180deg, rgba(24, 20, 8, 0.98) 0%, rgba(12, 10, 4, 0.99) 100%); border: 2px solid #FFD700; animation: neonGlowPulse 3s infinite ease-in-out; }
    .gold-highlight-card { border: 2px solid #FFD700 !important; animation: goldGlowPulse 2s infinite ease-in-out !important; }
    .critical-box-mode { animation: criticalPulse 1.2s infinite ease-in-out !important; border: 2px solid #FF334B !important; }
    .tf-illuminated { box-shadow: 0 0 20px rgba(255, 215, 0, 0.85), inset 0 0 14px rgba(255, 215, 0, 0.3); border: 2px solid #FFD700 !important; background: rgba(255, 215, 0, 0.15) !important; }
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
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">APEXSCAN <span class="text-[#FFD700]" id="headerEngineTitle">Quantum Superior Advisor</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-[#FFD700]"></span> <span id="modeText">MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#FFD700]" id="headerSubMarketInfo">Manuale & Auto • 1. Entra • 2. Rimani • 3. TP Esci</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 flex-wrap">
        <button onclick="leggiNotificaOrarioGiusto()" class="px-3 py-1.5 rounded-xl text-xs font-mono font-bold bg-gradient-to-r from-amber-500 to-yellow-400 text-black shadow-lg hover:brightness-110 transition-all flex items-center gap-1.5 animate-bounce">
          <span>🔔</span> <span>Notifica Orario Giusto 🔊</span>
        </button>

        <button onclick="leggiTabellaOrariItalia()" class="px-3 py-1.5 rounded-xl text-xs font-mono font-bold bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700]/40 hover:bg-[#FFD700]/30 transition-all flex items-center gap-1.5">
          <span>🔊</span> <span>Audio Orari Italia</span>
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
        <span>🔥</span> Cripto in Oro Live:
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

    <!-- BANNER ORARIO E NOTIFICA ORARIO GIUSTO -->
    <div id="timeSafetyBanner" class="terminal-card rounded-2xl p-4 border border-[#FFD700]/50 bg-amber-950/20 flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
      <div class="flex items-center gap-3">
        <span id="timeBannerDot" class="w-3 h-3 rounded-full bg-[#FFD700] animate-pulse"></span>
        <div>
          <strong id="timeBannerTitle" class="text-white text-sm block">SESSIONE DI TRADING &amp; NOTIFICA ORARIO GIUSTO (CET): ATTIVO 🟡</strong>
          <span id="timeBannerText" class="text-[#FFD700]">Cripto ed asset evidenziati nei momenti ottimali.</span>
        </div>
      </div>
      <div class="flex items-center gap-3 bg-black/60 px-4 py-2 rounded-xl border border-white/10 flex-wrap">
        <div>
          <span class="text-[10px] text-white/60 block">🏛️ BORSA ATTIVA (CET)</span>
          <strong id="bannerBorsaLabel" class="text-[#FFD700] font-bold">Borsa di Londra</strong>
        </div>
        <span class="text-white/40">|</span>
        <div>
          <span class="text-[10px] text-white/60 block">🎯 MOMENTO APERTURA POSIZIONI</span>
          <strong id="bannerMomentoLabel" class="text-[#FFD700] font-bold animate-pulse">FAVOREVOLE IN ORO 🟡</strong>
        </div>
        <button onclick="leggiNotificaOrarioGiusto()" class="px-3 py-1.5 rounded-lg bg-gradient-to-r from-amber-500 to-yellow-400 text-black hover:brightness-110 text-xs font-extrabold transition-all shadow-md flex items-center gap-1">
          <span>🔔 Notifica Orario 🔊</span>
        </button>
        <button onclick="mostraFinestraGuidaOraria()" class="px-3 py-1.5 rounded-lg bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700]/40 hover:bg-[#FFD700]/30 text-xs font-bold transition-all">
          📢 Tabella CET
        </button>
      </div>
    </div>

    <!-- TABELLA: ORARI PRINCIPALI DEI MERCATI IN ITALIA (ORA ITALIANA - SCRITTO & VOCALE) -->
    <section class="terminal-card rounded-2xl p-4 space-y-3 border border-[#FFD700]/40">
      <div class="flex flex-wrap items-center justify-between border-b border-white/15 pb-2 gap-2">
        <h2 class="text-xs font-extrabold text-[#FFD700] uppercase tracking-wider flex items-center gap-2">
          <span>🇮🇹 Orari Principali dei Mercati in Italia (Ora Italiana - Scritto &amp; Vocale)</span>
        </h2>
        <div class="flex items-center gap-2">
          <button onclick="leggiNotificaOrarioGiusto()" class="px-3 py-1.5 rounded-lg bg-amber-500 text-black hover:bg-amber-400 text-[11px] font-extrabold transition-all flex items-center gap-1.5 shadow-md">
            <span>🔔</span> Notifica Orario Giusto
          </button>
          <button onclick="leggiTabellaOrariItalia()" class="px-3 py-1.5 rounded-lg bg-[#FFD700] text-black hover:bg-amber-400 text-[11px] font-extrabold transition-all flex items-center gap-1.5 shadow-md">
            <span>🔊</span> Ascolta Guida Orari Italia
          </button>
        </div>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs font-mono">
          <thead>
            <tr class="border-b border-white/15 text-white/60 text-[10px]">
              <th class="py-2 px-2">MERCATO / BORSA</th>
              <th class="py-2 px-2">ORARI UFFICIALI (ORA ITALIANA)</th>
              <th class="py-2 px-2">DETTAGLI OPERATIVI &amp; VOLATILITÀ</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-white/10 text-white/90">
            <tr>
              <td class="py-2.5 px-2 text-white font-bold">🇮🇹 Borsa Italiana (Milano / Euronext)</td>
              <td class="py-2.5 px-2 text-cyan-300 font-semibold">
                • Negoziazione continua: 09:01 – 17:30<br>
                • Sessione serale (TAH): 18:00 – 20:30
              </td>
              <td class="py-2.5 px-2 text-white/80">
                Orari standard azionario e derivati italiani. Maggiori dettagli sugli orari nazionali sono disponibili su <strong class="text-cyan-300">Directa</strong>.
              </td>
            </tr>
            <tr class="bg-amber-950/30 border-l-4 border-[#FFD700]">
              <td class="py-2.5 px-2 text-[#FFD700] font-bold">🇺🇸 Mercati USA (NYSE e NASDAQ)</td>
              <td class="py-2.5 px-2 text-[#FFD700] font-semibold">
                • Apertura: Ore 15:30 italiane<br>
                • Chiusura: Ore 22:00 italiane
              </td>
              <td class="py-2.5 px-2 text-white/90">
                Con l'ora solare/legale può esserci un piccolo scostamento stagionale, ma lo standard è 15:30 – 22:00. Panoramica completa consultabile su <strong class="text-[#FFD700]">Marco Casario</strong>.
              </td>
            </tr>
            <tr class="bg-amber-950/30 border-l-4 border-[#FFD700]">
              <td class="py-2.5 px-2 text-[#FFD700] font-bold">💱 Mercato Forex</td>
              <td class="py-2.5 px-2 text-[#FFD700] font-semibold">
                • Aperto 24 ore su 24, 5 giorni a settimana<br>
                • Da domenica sera a venerdì sera
              </td>
              <td class="py-2.5 px-2 text-white/90">
                I momenti di maggiore volatilità e volume coincidono con la <strong>sovrapposizione tra Londra e New York</strong> (pomeriggio italiano, indicativamente <strong>dalle 14:00 alle 17:00</strong>).
              </td>
            </tr>
            <tr>
              <td class="py-2.5 px-2 text-[#00FF66] font-bold">🪙 Criptovalute</td>
              <td class="py-2.5 px-2 text-[#00FF66] font-semibold">
                • Operative 24 ore su 24, 7 giorni su 7
              </td>
              <td class="py-2.5 px-2 text-white/80">
                Mercato continuo h24 senza interruzioni (salvo brevi manutenzioni tecniche dei broker o exchange).
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- TABELLA REALE DI BORSA (CET) -->
    <section class="terminal-card rounded-2xl p-4 space-y-3">
      <div class="flex items-center justify-between border-b border-white/15 pb-2">
        <h2 class="text-xs font-extrabold text-[#FFD700] uppercase tracking-wider flex items-center gap-2">
          <span>📋 Tabella Reale Ufficiale: Orari di Borsa (CET) &amp; Note Operative in Oro</span>
        </h2>
        <span class="text-[10px] text-white/60 font-mono">Aggiornata in Tempo Reale</span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs font-mono">
          <thead>
            <tr class="border-b border-white/15 text-white/60 text-[10px]">
              <th class="py-2 px-2">SESSIONE / BORSA</th>
              <th class="py-2 px-2">ORARIO NELLA TABELLA (CET)</th>
              <th class="py-2 px-2">ORARIO REALE DI BORSA (CET)</th>
              <th class="py-2 px-2">NOTE OPERATIVE &amp; MOMENTO ORO</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-white/10 text-white/90">
            <tr>
              <td class="py-2 px-2 text-white font-bold">Sessione Asiatica (Tokyo / Sydney)</td>
              <td class="py-2 px-2 text-cyan-300">01:00 – 09:00</td>
              <td class="py-2 px-2 text-white/80">01:00 – 09:00 (Tokyo apre alle 01:00)</td>
              <td class="py-2 px-2 text-white/80">Volatilità bassa, movimenti laterali.</td>
            </tr>
            <tr class="bg-red-950/25">
              <td class="py-2 px-2 text-[#FF334B] font-bold">⚠️ Apertura Borse Europee</td>
              <td class="py-2 px-2 text-[#FF334B]">08:45 – 09:30</td>
              <td class="py-2 px-2 text-[#FF334B]">09:00 (Francoforte, Milano, Londra)</td>
              <td class="py-2 px-2 text-[#FF334B]"><strong>Sconsigliato:</strong> Alta volatilità nei primi 15-30 minuti.</td>
            </tr>
            <tr class="bg-amber-950/40 border-l-4 border-[#FFD700]">
              <td class="py-2 px-2 text-[#FFD700] font-bold">🟡 Mattinata Europea</td>
              <td class="py-2 px-2 text-[#FFD700]">09:30 – 14:15</td>
              <td class="py-2 px-2 text-[#FFD700]">09:30 – 14:30</td>
              <td class="py-2 px-2 text-[#FFD700]"><strong>Momento Ottimale in ORO:</strong> Liquidità elevata e trend più puliti.</td>
            </tr>
            <tr>
              <td class="py-2 px-2 text-white font-bold">Pre-Market USA</td>
              <td class="py-2 px-2 text-cyan-300">14:15 – 15:20</td>
              <td class="py-2 px-2 text-cyan-300">14:30 – 15:30</td>
              <td class="py-2 px-2 text-white/80">Rilascio dati macroeconomici USA (spesso alle 14:30). Studio livelli.</td>
            </tr>
            <tr class="bg-red-950/25">
              <td class="py-2 px-2 text-[#FF334B] font-bold">⚠️ Apertura Wall Street</td>
              <td class="py-2 px-2 text-[#FF334B]">15:20 – 16:00</td>
              <td class="py-2 px-2 text-[#FF334B]">15:30 (Apertura ufficiale NYSE)</td>
              <td class="py-2 px-2 text-[#FF334B]"><strong>Sconsigliato:</strong> Massima volatilità e false rotture fino alle 16:00.</td>
            </tr>
            <tr class="bg-amber-950/40 border-l-4 border-[#FFD700]">
              <td class="py-2 px-2 text-[#FFD700] font-bold">🟡 Sessione Pomeridiana USA</td>
              <td class="py-2 px-2 text-[#FFD700]">16:00 – 21:30</td>
              <td class="py-2 px-2 text-[#FFD700]">16:00 – 22:00</td>
              <td class="py-2 px-2 text-[#FFD700]"><strong>Momento Ottimale in ORO:</strong> Consolidamento dei trend principali.</td>
            </tr>
            <tr class="bg-red-950/25">
              <td class="py-2 px-2 text-[#FF334B] font-bold">⚠️ Chiusura USA / Notturna</td>
              <td class="py-2 px-2 text-[#FF334B]">21:30 – 01:15</td>
              <td class="py-2 px-2 text-[#FF334B]">22:00 – 01:00</td>
              <td class="py-2 px-2 text-[#FF334B]"><strong>Sconsigliato:</strong> Calo drastico dei volumi dopo le 22:00.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- BOX ADVISOR POSIZIONE ATTIVA -->
    <div id="activePositionAdvisorBox" class="hidden terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#FFD700] bg-gradient-to-r from-amber-950/40 via-[#08080A] to-amber-950/40 shadow-2xl space-y-4">
      <div class="flex flex-wrap items-center justify-between pb-3 border-b border-[#FFD700]/30 gap-3" id="advHeaderBox">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-pulse" id="advIconSign">🔊</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white" id="advTitleMain">🟡 QUANTUM ADVISOR ATTIVO IN ORO (<span id="advSymbol">BTC-USDT</span>)</h2>
            <p class="text-[11px] text-[#FFD700]" id="advSubNotice">1. Ingresso • 2. Gestione in PnL • 3. TP Raggiunto / Esci</p>
          </div>
        </div>
        <button onclick="chiudiPosizioneMonitorata()" class="px-3.5 py-1.5 rounded-xl bg-red-500/20 text-[#FF334B] border border-red-500/50 hover:bg-red-500/30 text-xs font-bold transition-all">
          Chiudi / Libera Monitoraggio ✕
        </button>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">1. 🟡 INGRESSO ORO</span>
          <strong id="advEntryPrice" class="text-white text-sm">--</strong>
          <span id="advSideBadge" class="inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-[#FFD700]">LONG</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">2. 📊 STATO MERCATO (PNL)</span>
          <strong id="advLivePrice" class="text-cyan-300 text-sm">--</strong>
          <span id="advPnlVal" class="block mt-1 text-[10px] font-bold text-[#FFD700]">PnL: 0.00%</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10">
          <span class="text-white/60 text-[10px] block">🎯 TARGET TAKE PROFIT ($ TP)</span>
          <strong id="advTargetTpDisplay" class="text-[#FFD700] text-sm">--</strong>
          <span id="advDistTp" class="block mt-1 text-[10px] text-white/80 font-bold">Distanza TP: 0.00%</span>
          <span id="advExactTimeRemaining" class="block mt-0.5 text-[10px] text-yellow-300 tabular-nums font-bold">Tempo al TP: --</span>
        </div>
        <div class="bg-black/50 p-3 rounded-xl border border-white/10 flex flex-col justify-between">
          <div>
            <span class="text-white/60 text-[10px] block">3. 🎯 USCITA / TP RAGGIUNTO</span>
            <strong id="advStatusAdvice" class="text-[#FFD700] text-xs block mt-0.5">In attesa del TP...</strong>
          </div>
          <span id="advActionAlert" class="text-[10px] text-white/80 italic mt-1">Guida attiva per manuale e auto</span>
        </div>
      </div>
    </div>

    <!-- KPI DINAMICI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Stato Connessione Utente</span>
        <div class="mt-2">
          <strong id="kpiUserApiStatus" class="text-xs font-mono text-yellow-400 block">⚠️ API Personali non inserite</strong>
          <span class="text-[11px] text-white/70">Necessarie per Auto e Manuale</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Modalità Operativa</span>
        <div class="mt-2">
          <span id="kpiAutoSlotStatus" class="text-xs font-mono text-[#FFD700] font-extrabold">🟡 Pronto (Manuale / Auto)</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Top Mover in Oro 🔥</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-sm font-extrabold font-mono text-white">--</span>
          <span id="kpiTopMoverChange" class="text-sm font-extrabold font-mono text-[#FFD700]">--%</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Motore Sincronizzato</span>
        <div class="mt-2">
          <span id="kpiMarketBadge" class="text-xs font-mono text-[#FFD700] font-bold">🟡 RSI DINAMICO ORO</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#FFD700]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-bounce">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">⚡ SEGNALE MASTER SUPERIORE IN ORO ($ TP &amp; SL 😢)</h2>
            <p class="text-[11px] text-[#FFD700]" id="masterSubtitle">Cripto ed asset evidenziati in base agli orari italiani e internazionali</p>
          </div>
        </div>
        <span id="masterPrecisionBadge" class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#FFD700] text-black animate-pulse">CANDLE-SYNC ORO 🚀</span>
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
          <button onclick="setCategoryTab('ALL')" id="catTabALL" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#FFD700] text-black transition-all">⭐ Tutti i Mercati</button>
          <button onclick="setCategoryTab('Criptovalute')" id="catTabCriptovalute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🪙 Criptovalute</button>
          <button onclick="setCategoryTab('Materie Prime')" id="catTabMaterie Prime" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🛢️ Materie Prime</button>
          <button onclick="setCategoryTab('Forex / Valute')" id="catTabForex / Valute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">💱 Forex &amp; Valute</button>
          <button onclick="setCategoryTab('Azioni & Indici (TradFi)')" id="catTabTradFi" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">📈 Azioni &amp; Indici</button>
        </div>
      </div>
      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Cripto o Asset in Oro (es. BTC, ETH, SOL, RENDER)..." class="w-full bg-[#000000] border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#FFD700]" />
      </div>
    </section>

    <!-- LISTA CARD OPERATIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-4">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-3">
            <span class="w-8 h-8 rounded-full bg-[#FFD700]/20 border border-[#FFD700] flex items-center justify-center text-[#FFD700] text-base font-extrabold dollar-animated-logo">$</span>
            <span>📊 Lista Cripto &amp; Asset in Oro (<span id="tableMarketLabel" class="text-[#FFD700]">FUTURES</span> - Manuale & Auto)</span>
          </h2>
          <span class="text-xs font-mono text-[#FFD700]">Le cripto ottimali brillano in ORO nei momenti ideali</span>
        </div>
        <div id="tablesContainer" class="space-y-3"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#FFD700] animate-ping"></span> Feed Quantistico &amp; Advisor Oro
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Quantum Engine] Advisor Orari Italia &amp; Notifiche attivo...</div>
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

  <!-- MODALE GUIDA ORARIO & MERCATI IN ITALIA (POPUP AUTOMATICO SCRITTO & VOCALE) -->
  <div id="finestraGuidaOrariaModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-md p-3 overflow-y-auto">
    <div class="bg-[#08080A] border-2 border-[#FFD700] rounded-2xl w-full max-w-[680px] shadow-2xl overflow-hidden my-auto p-5 space-y-4">
      <div class="flex items-center justify-between border-b border-[#FFD700]/30 pb-3">
        <div class="flex items-center gap-3">
          <span class="w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-pulse">⏰</span>
          <div>
            <h2 class="text-base font-extrabold font-mono text-[#FFD700]">ORARI PRINCIPALI DEI MERCATI IN ITALIA</h2>
            <p class="text-xs text-white/70">Notifica Orario Giusto &amp; Guida Vocale (Ora Italiana)</p>
          </div>
        </div>
        <button onclick="chiudiFinestraGuidaOraria()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>

      <div class="space-y-3 text-xs font-mono">
        <div class="bg-black/60 p-3.5 rounded-xl border border-white/15 space-y-2">
          <div class="flex justify-between items-center"><span class="text-white/70">Stato Reale Orario:</span><strong id="modalPopupStatoOrario" class="text-[#FFD700]">Verifica in corso...</strong></div>
          <div class="flex justify-between items-center"><span class="text-white/70">Borsa / Mercato Attivo:</span><strong id="modalPopupBorsa" class="text-cyan-300">--</strong></div>
          <div class="flex justify-between items-center"><span class="text-white/70">Momento Apertura Posizioni:</span><strong id="modalPopupMomento" class="text-[#FFD700] font-extrabold">--</strong></div>
        </div>

        <div class="p-3 bg-amber-950/30 border border-[#FFD700]/40 rounded-xl space-y-2 text-white/90">
          <strong class="text-[#FFD700] block">🔔 Notifica Orario Ideale di Trading (Ora Italiana):</strong>
          <p>• <strong>Mattina:</strong> Dalle 09:30 alle 14:15 (Sessione Europea attiva, liquidità stabile e trend puliti).</p>
          <p>• <strong>Pomeriggio (Top Volatilità):</strong> Dalle 14:00 alle 17:00 (Sovrapposizione Londra e New York) e dalle 16:00 alle 21:30 (Sessione USA).</p>
          <p>• <strong>Evitare:</strong> Apertura Borsa Italiana (08:45 - 09:30) e apertura Wall Street (15:20 - 16:00) per via di false partenze e alta volatilità.</p>
        </div>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <button onclick="leggiNotificaOrarioGiusto()" class="w-full py-2.5 rounded-xl text-xs shadow-lg bg-amber-500 text-black font-extrabold hover:bg-amber-400 transition-all flex items-center justify-center gap-1.5">
          <span>🔔</span> Ascolta Notifica Orario Giusto
        </button>
        <button onclick="chiudiFinestraGuidaOraria()" class="w-full py-2.5 rounded-xl text-xs shadow-lg bg-[#FFD700] text-black font-bold hover:bg-amber-400 transition-all">
          Procedi al Trading in Oro ⚡
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
            <span id="modalOhlcvBadge" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700]/40">SUPERIORE ORO</span>
          </div>
          <p id="modalSubtitleType" class="text-xs text-white/70 mt-1">Advisor quantistico in Oro: 1. Ingresso • 2. Gestione PnL • 3. TP Raggiunto</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div id="modalMomentoGiustoBanner" class="p-3 rounded-xl border text-center font-bold text-xs">
          Verifica momento di mercato...
        </div>

        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <div class="flex justify-between"><span class="text-white/75">Mercato / Categoria:</span><strong id="modalCategory" class="text-[#FFD700]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">🏛️ Borsa Reale Attiva:</span><strong id="modalBorsaRif" class="text-cyan-300">--</strong></div>
          <div class="flex justify-between items-center">
            <span class="text-white/75">Timeframe Quantistico:</span>
            <strong id="modalTF" class="px-2.5 py-0.5 rounded bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700] font-extrabold animate-pulse">15m ⚡</strong>
          </div>
          <div class="flex justify-between"><span class="text-white/75">Sentiment Superiore:</span><strong id="modalSentiment" class="text-[#FFD700] font-bold">--</strong></div>
          <div class="flex justify-between border-b border-white/10 pb-2"><span class="text-white/75">⏱️ Tempo al $ TP (Candle-Sync):</span><strong id="modalExactTime" class="text-yellow-300 font-extrabold tabular-nums">--</strong></div>

          <div class="grid grid-cols-2 gap-2 py-2 border-b border-white/10">
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">📈 EMA 9 / EMA 21</span>
              <strong id="modalEma" class="text-cyan-300 text-xs">--</strong>
              <span id="modalEmaSignal" class="text-[10px] block mt-0.5">--</span>
            </div>
            <div class="bg-white/5 p-2 rounded-lg">
              <span class="text-[10px] text-white/60 block">📊 RSI (14) Dinamico</span>
              <strong id="modalRsi" class="text-white text-xs">--</strong>
              <span id="modalRsiZone" class="text-[10px] block mt-0.5">--</span>
            </div>
          </div>

          <div id="tfPanel" class="grid grid-cols-2 sm:grid-cols-4 gap-2 my-2"></div>

          <div class="bg-gradient-to-r from-amber-950/50 via-black to-amber-950/50 border-2 border-[#FFD700] rounded-xl p-3 space-y-2 my-3 shadow-xl">
            <div class="flex items-center justify-between border-b border-[#FFD700]/30 pb-2">
              <span class="text-xs font-extrabold text-[#FFD700] flex items-center gap-1.5">
                <span>📋</span> TABELLA PARAMETRI CHIRURGICI IN ORO ($ TP &amp; SL 😢)
              </span>
              <span class="text-[10px] text-white/70">Validi per Manuale e Auto</span>
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
                    <td class="py-2 px-2 text-[#FFD700] font-bold">2. $ Take Profit (TP Rapido)</td>
                    <td class="py-2 px-2 text-[#FFD700] font-extrabold select-all" id="tableParamTp">--</td>
                    <td class="py-2 px-2 text-right">
                      <button onclick="copiaParametro('tableParamTp')" class="bg-white/10 hover:bg-white/25 px-2 py-1 rounded text-[#FFD700] text-[10px]">Copia</button>
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
          <div class="flex justify-between"><span class="text-white/75">Direzione Long / Short:</span><strong id="modalSide" class="text-[#FFD700]">--</strong></div>
          <div>
            <div class="flex justify-between"><span class="text-white/75">$ Stop Loss Protetto 😢:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
            <div class="flex justify-between"><span class="text-white/75">🎯 TARGET $ TAKE PROFIT RAPIDO:</span><strong id="modalTrendingTP" class="text-[#FFD700] text-sm font-extrabold">--</strong></div>
            <div class="flex justify-between mt-1"><span class="text-white/75">🔥 ROI Stimato al $ TP (Leva):</span><strong id="modalTpPct" class="text-yellow-400 font-extrabold">--</strong></div>
          </div>
        </div>

        <div class="bg-[#000000] border border-[#FFD700]/40 rounded-xl p-4 space-y-3">
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
          <label class="block text-xs font-bold text-white uppercase tracking-wider">Leva Esatta del Segnale &amp; Margine Isolato:</label>
          <div class="grid grid-cols-4 gap-2">
            <button type="button" onclick="setLeverage(1)" id="levBtn1" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">1x</button>
            <button type="button" onclick="setLeverage(3)" id="levBtn3" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">3x</button>
            <button type="button" onclick="setLeverage(5)" id="levBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">5x</button>
            <button type="button" onclick="setLeverage(10)" id="levBtn10" class="py-1.5 rounded-lg border border-[#FFD700] bg-[#FFD700]/20 text-[#FFD700] font-bold">10x</button>
          </div>
        </div>

        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-white/70 font-semibold">Quantità Asset Calcolata (In base al capitale e alla leva):</label>
          </div>
          <input type="number" id="tradeQuantityInput" value="1.0" step="any" oninput="onQuantityManualChange(this.value)" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
        </div>

        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>⚡ Esegui Ordine in Oro (Manuale o Auto) &amp; Avvia Advisor</span>
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
      isCriticalState: false,
      lastSessionSafeState: null
    };

    const TOP_MARKET_CAP_ORDER = [
      'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'XRP-USDT', 'BNB-USDT', 'DOGE-USDT', 'ADA-USDT',
      'AVAX-USDT', 'SHIB-USDT', 'DOT-USDT', 'LINK-USDT', 'SUI-USDT', 'NEAR-USDT', 'LTC-USDT',
      'PEPE-USDT', 'APT-USDT', 'UNI-USDT', 'ICP-USDT', 'FET-USDT', 'RENDER-USDT'
    ];

    function leggiNotificaOrarioGiusto() {
      const oraCorrente = new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
      const testoNotifica = `Notifica orario di trading per le ore ${oraCorrente}. I momenti migliori per iniziare a fare trading in Italia sono: la mattina dalle 9 e 30 alle 14 e 15, durante la sessione europea; il pomeriggio dalle 14 alle 17, grazie alla sovrapposizione tra Londra e New York; e la sessione americana dalle 16 alle 21 e 30. Evitare l'apertura della borsa italiana tra le 8 e 45 e le 9 e 30, e l'apertura di Wall Street tra le 15 e 20 e le 16, per via dell'alta volatilità e dei falsi segnali.`;
      playChime(false, testoNotifica);
      logFeed(`[NOTIFICA ORARIO GIUSTO] Annuncio vocale orario eseguito per le ${oraCorrente}.`);
    }

    function leggiTabellaOrariItalia() {
      const testoVocale = "Orari principali dei mercati in Italia, ora italiana. " +
        "Borsa Italiana a Milano: negoziazione continua dalle 9 e 01 alle 17 e 30; sessione serale dalle 18 alle 20 e 30. " +
        "Mercati USA, NYSE e NASDAQ: apertura alle ore 15 e 30 italiane, chiusura alle ore 22 italiane. " +
        "Mercato Forex: aperto 24 ore su 24, 5 giorni a settimana, da domenica sera a venerdì sera. I momenti di maggiore volatilità coincidono con la sovrapposizione tra Londra e New York, dalle 14 alle 17. " +
        "Criptovalute: operative 24 ore su 24, 7 giorni su 7.";
      playChime(false, testoVocale);
      logFeed("[GUIDA VOCALE ITALIA] Lettura completa orari principali dei mercati in Italia.");
    }

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
        statusEl.className = "text-xs font-mono text-[#FFD700] block font-bold";
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

    function mostraFinestraGuidaOraria() {
      const modal = document.getElementById('finestraGuidaOrariaModal');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }

    function chiudiFinestraGuidaOraria() {
      const modal = document.getElementById('finestraGuidaOrariaModal');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
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

    function calcolaDatiLocaliUnificati(symbol, item) {
      const cached = state.detailedAssets[symbol];
      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      if (cached) {
        const p = item.price;
        const distTp = Math.abs(cached.tp - p);
        const tpPct = (((distTp / p) * 100) * lev).toFixed(2);
        return { symbol, price: p, side: cached.side, sl: cached.sl, tp: cached.tp, tpPct, confluenza: "RSI Dinamico & Candela-Sync 🎯", tempoSec: cached.tempoSec };
      }
      const p = item.price;
      const dec = getPrecisionDecimals(p);
      const isBull = item.change24h >= 0;
      const side = (state.marketType === 'SPOT' || isBull) ? "BUY" : "SELL";
      const distSl = p * 0.01;
      const distTp = p * 0.024;
      const sl = Number((side === 'BUY' ? p - distSl : p + distSl).toFixed(dec));
      const tp = Number((side === 'BUY' ? p + distTp : p - distTp).toFixed(dec));
      const tpPct = (((distTp / p) * 100) * lev).toFixed(2);
      const tempoSec = Math.max(60, Math.round(900 / (0.1 + Math.abs(item.change24h) * 0.1)));
      return { symbol, price: p, side, sl, tp, tpPct, confluenza: "RSI Dinamico & Candela-Sync 🎯", tempoSec };
    }

    function renderTfPanel(tfs, exactTf) {
      const el = document.getElementById('tfPanel');
      if (!el) return;
      if (!tfs) { el.innerHTML = ''; return; }
      el.innerHTML = ['5m', '15m', '1h', '4h'].map(iv => {
        const t = tfs[iv];
        const isExact = iv === exactTf;
        const col = t.trend === 'BUY' ? '#FFD700' : t.trend === 'SELL' ? '#FF334B' : '#FFCC00';
        const cs = t.candles;
        const hi = Math.max(...cs.map(c => c.high)), lo = Math.min(...cs.map(c => c.low));
        const y = v => 30 - ((v - lo) / ((hi - lo) || 1)) * 28;
        const bars = cs.map((c, i) => {
          const up = c.close >= c.open, last = i === cs.length - 1;
          const x = 3 + i * 7;
          return `<line x1="${x+2}" x2="${x+2}" y1="${y(c.high)}" y2="${y(c.low)}" stroke="${up ? '#FFD700' : '#FF334B'}"/>
            <rect x="${x}" width="4" y="${Math.min(y(c.open), y(c.close))}" height="${Math.max(1, Math.abs(y(c.open) - y(c.close)))}"
              fill="${up ? '#FFD700' : '#FF334B'}" ${last ? 'stroke="#FFD700" stroke-width="1.5"' : ''}/>`;
        }).join('');
        return `<div class="bg-white/5 rounded-lg p-2 border transition-all ${isExact ? 'tf-illuminated' : ''}" style="border-color:${isExact ? '#FFD700' : col + '66'}">
          <div class="flex justify-between items-center text-[10px]">
            <b class="${isExact ? 'text-[#FFD700]' : 'text-white'}">${iv} ${isExact ? '⚡' : ''}</b>
            <b style="color:${col}">${t.trend}</b>
          </div>
          <svg viewBox="0 0 100 32" class="w-full h-8">${bars}</svg>
          <div class="text-[9px] text-white/60">RSI ${t.rsi14} (Soglia din: ${t.sogliaLong || 55})</div>
          <div class="text-[9px] text-[#FFD700]">● ora: ${formatPrice(t.last.close)}</div>
        </div>`;
      }).join('');
    }

    function avviaAdvisorPosizione(symbol, side, entryPrice, tpPrice, slPrice, tempoSec) {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      const liveRemSec = getLiveRemainingSeconds(symbol, tempoSec);
      const totalDist = Math.abs(tpPrice - entryPrice);
      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      const initDistPct = ((totalDist / entryPrice) * 100).toFixed(2);
      const initDistLevaPct = (((totalDist / entryPrice) * 100) * lev).toFixed(2);

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
      box.className = "terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#FFD700] bg-gradient-to-r from-amber-950/40 via-[#08080A] to-amber-950/40 shadow-2xl space-y-4";
      document.getElementById('advHeaderBox').className = "flex flex-wrap items-center justify-between pb-3 border-b border-[#FFD700]/30 gap-3";
      document.getElementById('advIconSign').className = "w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-pulse";
      document.getElementById('advTitleMain').innerHTML = `🟡 QUANTUM ADVISOR ATTIVO IN ORO (<span id="advSymbol">${symbol}</span>)`;
      document.getElementById('advSubNotice').textContent = "1. Ingresso • 2. Gestione in PnL • 3. TP Raggiunto";

      document.getElementById('kpiAutoSlotStatus').textContent = "🔒 Monitoraggio Attivo Oro";
      document.getElementById('advSymbol').textContent = symbol;
      document.getElementById('advEntryPrice').textContent = `$${formatPrice(entryPrice)}`;
      document.getElementById('advSideBadge').textContent = side;
      document.getElementById('advTargetTpDisplay').textContent = `$${formatPrice(tpPrice)}`;
      document.getElementById('advDistTp').textContent = `Distanza TP: ${initDistPct}% (+${initDistLevaPct}% con ${lev}x)`;
      document.getElementById('advExactTimeRemaining').textContent = `Tempo al TP: ${formatExactDuration(liveRemSec)}`;

      const sideText = side === 'BUY' ? 'Long' : 'Short';
      const voiceTimeStr = formatVoiceDuration(liveRemSec);
      state.lastVoiceAnnounceTimestamp = Date.now();
      playChime(false, `Primo. Ingresso registrato su ${symbol}. Posizione ${sideText} con ${state.selectedMarginUsdt} USDT di margine. Target Take Profit a ${formatPrice(tpPrice)}. Tempo previsto: ${voiceTimeStr}.`);
      aggiornaAdvisorLoop();
      state.advisorInterval = setInterval(aggiornaAdvisorLoop, 1000);
      logFeed(`[QUANTUM ADVISOR ORO] Monitoraggio avviato su ${symbol} (${side}) in modalità ${state.tradingMode}.`);
    }

    function chiudiPosizioneMonitorata() {
      if (state.advisorInterval) clearInterval(state.advisorInterval);
      state.activePosition = null;
      state.isCriticalState = false;
      document.getElementById('activePositionAdvisorBox').classList.add('hidden');
      document.getElementById('kpiAutoSlotStatus').textContent = "🟡 Pronto (Manuale / Auto)";
      logFeed(`[QUANTUM ADVISOR ORO] Monitoraggio chiuso.`);
    }

    let lastLoopAdviceState = "";
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

      const distTp = Math.max(0, pos.side === 'BUY' ? (pos.tpPrice - currentPrice) : (currentPrice - pos.tpPrice));
      const distTpPct = currentPrice > 0 ? ((distTp / currentPrice) * 100) : 0;
      const distTpLevaPct = distTpPct * lev;

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

      document.getElementById('advDistTp').textContent = `Distanza TP: ${distTpPct.toFixed(2)}% (+${distTpLevaPct.toFixed(2)}% con ${lev}x)`;
      document.getElementById('advExactTimeRemaining').textContent = `Tempo al TP: ${formatExactDuration(exactRemainingSec)}`;

      const advAdvice = document.getElementById('advStatusAdvice');
      const advAlert = document.getElementById('advActionAlert');
      const box = document.getElementById('activePositionAdvisorBox');

      if ((pos.side === 'BUY' && currentPrice >= pos.tpPrice) || (pos.side === 'SELL' && currentPrice <= pos.tpPrice)) {
        document.getElementById('advDistTp').textContent = "Distanza TP: 0.00% (RAGGIUNTO!)";
        document.getElementById('advExactTimeRemaining').textContent = "Tempo al TP: 0s";
        advAdvice.textContent = "3. 🎯 TP RAGGIUNTO! USCITA ESEGUITA";
        advAdvice.className = "text-[#FFD700] text-xs block font-bold animate-pulse";
        advAlert.textContent = "Target Take Profit raggiunto con successo!";
        if (lastLoopAdviceState !== "TP") {
          lastLoopAdviceState = "TP";
          playChime(true, "Terzo. Target Take profit raggiunto! Uscita da mercato completata con profitto.");
        }
        chiudiPosizioneMonitorata();
      } else if ((pos.side === 'BUY' && currentPrice <= pos.slPrice) || (pos.side === 'SELL' && currentPrice >= pos.slPrice)) {
        advAdvice.textContent = "3. 🛑 STOP LOSS RAGGIUNTO 😢";
        advAdvice.className = "text-[#FF334B] text-xs block font-bold animate-pulse";
        advAlert.textContent = "Chiusura di salvaguardia attivata.";
        if (lastLoopAdviceState !== "SL") {
          lastLoopAdviceState = "SL";
          playChime(true, "Terzo. Stop loss toccato. Chiusura di protezione eseguita.");
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
        box.className = "terminal-card rounded-2xl p-4 sm:p-5 border-2 border-[#FFD700] bg-gradient-to-r from-amber-950/40 via-[#08080A] to-amber-950/40 shadow-2xl space-y-4";
        document.getElementById('advHeaderBox').className = "flex flex-wrap items-center justify-between pb-3 border-b border-[#FFD700]/30 gap-3";
        document.getElementById('advIconSign').className = "w-9 h-9 rounded-xl bg-[#FFD700] text-black flex items-center justify-center font-extrabold animate-pulse";
        document.getElementById('advTitleMain').innerHTML = `🟡 QUANTUM ADVISOR ATTIVO IN ORO (<span id="advSymbol">${pos.symbol}</span>)`;
        document.getElementById('advSubNotice').textContent = "1. Ingresso • 2. Gestione in PnL • 3. TP Raggiunto";

        if (pnlPct >= 0) {
          advAdvice.textContent = "2. 📊 GESTIONE PNL (IN PROFITTO)";
          advAdvice.className = "text-[#FFD700] text-xs block font-bold";
          advAlert.textContent = `Avanzamento regolare verso il $ TP: $${formatPrice(pos.tpPrice)}`;
        } else {
          advAdvice.textContent = "2. ⏳ GESTIONE PNL (FASI DI RITRACCIAMENTO)";
          advAdvice.className = "text-yellow-400 text-xs block font-bold";
          advAlert.textContent = `Il prezzo fluttua prima della spinta verso il TP: $${formatPrice(pos.tpPrice)}`;
        }

        const now = Date.now();
        if (now - state.lastVoiceAnnounceTimestamp >= 15000) {
          state.lastVoiceAnnounceTimestamp = now;
          const voiceTimeLeft = formatVoiceDuration(exactRemainingSec);
          if (pnlPct >= 0) {
            speakText(`Secondo. Posizione in profitto. Mancano circa ${voiceTimeLeft} al Take Profit.`);
          } else {
            speakText(`Secondo. Posizione in fase di fluttuazione normale. Mancano circa ${voiceTimeLeft} al Take Profit.`);
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
      if (activeBtn) activeBtn.className = "py-1.5 rounded-lg border border-[#FFD700] bg-[#FFD700]/20 text-[#FFD700] font-bold";

      if (state.currentSelectedAsset) {
        const d = state.currentSelectedAsset;
        const actualLev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
        const tpPct = (((Math.abs(d.tp - d.price) / d.price) * 100) * actualLev).toFixed(2);
        document.getElementById('modalTpPct').textContent = `+${tpPct}% (con leva ${actualLev}x)`;
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
          logFeed(`[MERCATO] Passato automaticamente a ${mtype}`);
        }
      } catch (e) {}
    }

    function updateMarketTypeUI() {
      const isSpot = state.marketType === 'SPOT';
      document.getElementById('btnMarketFutures').className = state.marketType === 'FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#0066FF] text-white" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('btnMarketStandard').className = state.marketType === 'STANDARD_FUTURES' ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-500 text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      document.getElementById('btnMarketSpot').className = isSpot ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-[#FFD700] text-black shadow-lg" : "px-2.5 py-1 rounded-lg text-xs font-bold text-white/50 hover:text-white";
      
      document.getElementById('kpiMarketBadge').textContent = isSpot ? "🟡 RSI SPOT ORO" : "🟡 RSI FUTURES ORO";
      document.getElementById('tableMarketLabel').textContent = state.marketType;
      document.getElementById('headerSubMarketInfo').textContent = isSpot ? "Spot (Manuale & Auto) • 1. Entra • 2. Rimani • 3. TP Esci" : "Futures (Manuale & Auto) • 1. Entra • 2. Rimani • 3. TP Esci";
      
      const levSec = document.getElementById('leverageSectionWrapper');
      if (levSec) levSec.style.display = isSpot ? 'none' : 'block';
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
          logFeed(`[MODALITÀ] Impostata su ${mode}`);
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

        const modeRes = await fetch('/api/get-mode');
        const modeJson = await modeRes.json();
        if (modeJson.code === 0) {
          const banner = document.getElementById('timeSafetyBanner');
          const bannerText = document.getElementById('timeBannerText');
          const bannerDot = document.getElementById('timeBannerDot');
          const bannerTitle = document.getElementById('timeBannerTitle');
          const borsaLbl = document.getElementById('bannerBorsaLabel');
          const momentoLbl = document.getElementById('bannerMomentoLabel');

          borsaLbl.textContent = modeJson.borsaRif;

          if (modeJson.isSafeTime) {
            banner.className = "terminal-card rounded-2xl p-4 border border-[#FFD700]/50 bg-amber-950/20 flex flex-wrap items-center justify-between gap-3 text-xs font-mono";
            bannerDot.className = "w-3 h-3 rounded-full bg-[#FFD700] animate-pulse";
            bannerTitle.textContent = "SESSIONE DI TRADING & NOTIFICA ORARIO GIUSTO: OTTIMALE IN ORO 🚀";
            bannerText.className = "text-[#FFD700]";
            bannerText.textContent = modeJson.timeMsg;
            momentoLbl.textContent = "FAVOREVOLE IN ORO 🟡";
            momentoLbl.className = "text-[#FFD700] font-bold animate-pulse";

            document.getElementById('modalPopupStatoOrario').textContent = modeJson.timeMsg;
            document.getElementById('modalPopupBorsa').textContent = modeJson.borsaRif;
            document.getElementById('modalPopupMomento').textContent = "🟡 FAVOREVOLE PER APRIRE POSIZIONI";

            if (state.lastSessionSafeState === false || state.lastSessionSafeState === null) {
              state.lastSessionSafeState = true;
              playChime(false, `Notifica orario giusto. Finestra temporale ottimale aperta presso la ${modeJson.borsaRif}. Il momento per aprire posizioni è favorevole.`);
              logFeed("[NOTIFICA ORARIO] Finestra sicura aperta. Avviso vocale eseguito.");
            }
          } else {
            banner.className = "terminal-card rounded-2xl p-4 border border-[#FF334B]/60 bg-red-950/30 flex flex-wrap items-center justify-between gap-3 text-xs font-mono";
            bannerDot.className = "w-3 h-3 rounded-full bg-[#FF334B] animate-ping";
            bannerTitle.textContent = "⚠️ ORARIO CRITICO / PRUDENZA MASSIMA 🛑";
            bannerText.className = "text-[#FF334B] font-bold";
            bannerText.textContent = `${modeJson.timeMsg} (Sconsigliato aprire nuovi trade)`;
            momentoLbl.textContent = "ATTESA / RISCHIO ⚠️";
            momentoLbl.className = "text-[#FF334B] font-bold";

            document.getElementById('modalPopupStatoOrario').textContent = modeJson.timeMsg;
            document.getElementById('modalPopupBorsa').textContent = modeJson.borsaRif;
            document.getElementById('modalPopupMomento').textContent = "⚠️ PRUDENZA / ATTENDERE FINESTRA";

            if (state.lastSessionSafeState === true || state.lastSessionSafeState === null) {
              state.lastSessionSafeState = false;
              playChime(true, "Notifica orario. Raggiunto orario critico o di alta volatilità. Sconsigliato aprire nuove posizioni.");
              logFeed("[NOTIFICA ORARIO] Orario critico rilevato. Avviso vocale eseguito.");
            }
          }
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
          <span class="${item.change24h >= 0 ? 'text-[#FFD700]' : 'text-[#FF334B]'} font-bold">${item.change24h >= 0 ? '+' : ''}${item.change24h.toFixed(2)}%</span>
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
      const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
      if (masterAsset) {
        const local = calcolaDatiLocaliUnificati(masterAsset.symbol, masterAsset);
        const liveRemMaster = getLiveRemainingSeconds(masterAsset.symbol, local.tempoSec);
        document.getElementById('masterPrecisionBadge').textContent = `${state.marketType} SYNC ORO ⚡`;
        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <div class="flex items-center gap-2">
                <span class="text-white text-base font-extrabold px-3 py-1 rounded-xl bg-[#FFD700]/20 border-2 border-[#FFD700] text-[#FFD700] animate-pulse">${masterAsset.symbol} 🟡</span>
                <span class="text-xs text-[#FFD700] font-bold">✨ MASTER CRIPTO IN ORO (ORA ITALIANA)</span>
              </div>
              <div class="flex justify-between"><span class="text-white/70">Prezzo Live:</span><strong class="text-[#FFD700] text-sm">$${formatPrice(masterAsset.price)}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">Direzione Quantistica:</span><strong class="${local.side === 'BUY' ? 'text-[#FFD700]' : 'text-[#FF334B]'} font-bold">${local.side === 'BUY' ? 'LONG ORO 🚀' : 'SHORT CHIRURGICO 📉'}</strong></div>
              <div class="flex justify-between"><span class="text-white/70">$ TP Calibrato (+% ${state.marketType === 'SPOT' ? 'Spot' : 'Leva ' + lev + 'x'}):</span><strong class="text-yellow-300 font-extrabold">$${formatPrice(local.tp)} (+${local.tpPct}%)</strong></div>
              <div class="flex justify-between"><span class="text-white/70">⏱️ Tempo al $ TP (Candle-Sync):</span><strong class="text-cyan-300 font-bold tabular-nums" data-countdown-sym="${masterAsset.symbol}" data-base-sec="${local.tempoSec}">${formatExactDuration(liveRemMaster)}</strong></div>
            </div>
            <div class="flex flex-col justify-end">
              <button onclick='openModal("${masterAsset.symbol}")' class="w-full py-2.5 rounded-xl text-xs shadow-lg bg-[#FFD700] text-black font-extrabold hover:bg-amber-400 transition-all">⚡ APRI SCANNER ORO &amp; SCEGLI CAPITALE &#8599;</button>
            </div>
          `;
      }

      let filtered = sorted;
      if (state.currentCategoryTab !== 'ALL') filtered = filtered.filter(c => c.category === state.currentCategoryTab);
      if (state.searchQuery) filtered = filtered.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));

      const container = document.getElementById('tablesContainer');
      container.innerHTML = filtered.map(c => {
        const local = calcolaDatiLocaliUnificati(c.symbol, c);
        const liveRemCard = getLiveRemainingSeconds(c.symbol, local.tempoSec);
        const isLong = local.side === 'BUY';
        
        const isGoldHighlight = isLong && c.change24h >= 0;
        const cardClass = isGoldHighlight ? "gold-highlight-card rounded-xl p-3.5 cursor-pointer transition-all space-y-2.5 relative" : "bg-[#000000] border border-white/25 hover:border-[#FFD700] rounded-xl p-3.5 cursor-pointer transition-all space-y-2.5";

        return `
          <div onclick='openModal("${c.symbol}")' class="${cardClass}">
            ${isGoldHighlight ? '<div class="absolute top-2 right-2 px-2 py-0.5 rounded text-[9px] font-extrabold bg-[#FFD700] text-black animate-pulse">🟡 MOMENTO ORO</div>' : ''}
            <div class="flex items-center justify-between border-b border-white/10 pb-2">
              <div class="flex items-center gap-2">
                <span class="font-extrabold text-sm px-2.5 py-1 rounded-xl border ${isGoldHighlight ? 'bg-[#FFD700]/20 border-[#FFD700] text-[#FFD700]' : 'bg-white/10 border-white/20 text-white'}">${c.symbol}</span>
                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${isLong ? 'bg-amber-500/20 text-[#FFD700]' : 'bg-red-500/20 text-[#FF334B]'}">${isLong ? 'LONG ORO 🚀' : 'SHORT 📉'}</span>
              </div>
              <div class="text-right">
                <span class="text-white text-xs font-bold tabular-nums">$${formatPrice(c.price)}</span>
                <span class="block text-[10px] ${c.change24h >= 0 ? 'text-[#FFD700]' : 'text-[#FF334B]'}">${c.change24h >= 0 ? '+' : ''}${c.change24h.toFixed(2)}%</span>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2 text-[11px] font-mono">
              <div class="bg-white/5 p-2 rounded-lg">
                <span class="text-white/60 text-[10px] block">🎯 TARGET $ TP CALIBRATO</span>
                <strong class="text-[#FFD700]">$${formatPrice(local.tp)}</strong>
                <span class="text-yellow-300 text-[10px] block">+${local.tpPct}% (${state.marketType === 'SPOT' ? 'Spot' : lev + 'x'})</span>
              </div>
              <div class="bg-white/5 p-2 rounded-lg">
                <span class="text-white/60 text-[10px] block">⏱️ TEMPO AL $ TP (CANDLE)</span>
                <strong class="text-cyan-300 tabular-nums" data-countdown-sym="${c.symbol}" data-base-sec="${local.tempoSec}">${formatExactDuration(liveRemCard)}</strong>
                <span class="text-[#FF334B] text-[10px] block">SL 😢: $${formatPrice(local.sl)}</span>
              </div>
            </div>

            <div class="flex items-center justify-between pt-1">
              <span class="text-[10px] text-[#FFD700] font-bold">Stato: ${local.confluenza}</span>
              <span class="px-3 py-1 rounded-lg bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700]/40 text-xs font-bold">1. Esegui in Oro ⚡</span>
            </div>
          </div>
        `;
      }).join('');
    }

    async function openModal(symbol) {
      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalOhlcvBadge').textContent = "CANDLE-SYNC ORO IN CORSO...";
      document.getElementById('tfPanel').innerHTML = '<div class="col-span-4 text-center text-xs text-white/60 py-4">Sincronizzazione candela in corso...</div>';

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
          document.getElementById('modalBorsaRif').textContent = d.confluenza;

          const bannerMomento = document.getElementById('modalMomentoGiustoBanner');
          if (d.momentoGiusto) {
            bannerMomento.className = "p-3 rounded-xl border border-[#FFD700] bg-amber-950/40 text-[#FFD700] font-extrabold text-xs animate-pulse";
            bannerMomento.innerHTML = `🟡 MOMENTO PERFETTO IN ORO PER APRIRE POSIZIONI! (Borsa reale attiva e trend allineato)`;
          } else {
            bannerMomento.className = "p-3 rounded-xl border border-[#FF334B] bg-red-950/40 text-[#FF334B] font-extrabold text-xs";
            bannerMomento.innerHTML = `⚠️ ATTENZIONE: Momento di mercato non ottimale. Prudenza consigliata.`;
          }

          const tfEl = document.getElementById('modalTF');
          tfEl.textContent = `${d.exactTf.toUpperCase()} CANDLE-SYNC ORO ⚡`;
          tfEl.className = "px-2.5 py-0.5 rounded bg-[#FFD700]/20 text-[#FFD700] border border-[#FFD700] font-extrabold animate-pulse";

          document.getElementById('modalSentiment').textContent = d.sentiment;
          
          const currentLiveRem = getLiveRemainingSeconds(symbol, d.tempoSec);
          document.getElementById('modalExactTime').textContent = `${formatExactDuration(currentLiveRem)} 🎯`;

          document.getElementById('modalEma').textContent = `EMA9: ${formatPrice(d.ema9)} | EMA21: ${formatPrice(d.ema21)}`;
          document.getElementById('modalEmaSignal').textContent = "Trend Calibrato Oro ⚡";
          document.getElementById('modalEmaSignal').className = "text-[10px] block mt-0.5 text-[#FFD700]";

          document.getElementById('modalRsi').textContent = `RSI: ${d.rsi14}`;
          document.getElementById('modalRsiZone').textContent = "Ottimale Dinamico 🎯";

          document.getElementById('modalLivePrice').textContent = `$${formatPrice(d.price)}`;

          const sideEl = document.getElementById('modalSide');
          sideEl.textContent = `${d.side} (${d.side === 'BUY' ? 'LONG ORO 🚀' : 'SHORT CHIRURGICO 📉'})`;
          sideEl.className = `text-[12px] font-bold ${d.side === 'BUY' ? 'text-[#FFD700]' : 'text-[#FF334B]'}`;

          document.getElementById('modalSL').textContent = `$${formatPrice(d.sl)}`;
          document.getElementById('modalTrendingTP').textContent = `$${formatPrice(d.tp)}`;
          const lev = state.marketType === 'SPOT' ? 1 : state.selectedLeverage;
          const tpPct = (((Math.abs(d.tp - d.price) / d.price) * 100) * lev).toFixed(2);
          document.getElementById('modalTpPct').textContent = `+${tpPct}% (${state.marketType === 'SPOT' ? 'Spot' : 'con leva ' + lev + 'x'})`;

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
          tp: asset.tp, sl: asset.sl, leverage: state.marketType === 'SPOT' ? 1 : state.selectedLeverage, timeframe: asset.timeframe,
          marketType: state.marketType, apiKey: keys.apiKey, secretKey: keys.secretKey,
          is_auto: state.tradingMode === 'AUTO'
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
      updateMarketTypeUI();
      await pollMarketData();
      setInterval(pollMarketData, 2000);
      setInterval(() => {
        document.getElementById('liveCetClock').textContent = new Date().toTimeString().slice(0, 8) + ' CET';
        updateAllVisibleCountdowns();
      }, 1000);

      setTimeout(mostraFinestraGuidaOraria, 800);
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
