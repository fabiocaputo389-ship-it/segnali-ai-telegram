import os
import re
import time
import hmac
import hashlib
import urllib.parse
import requests
from datetime import datetime
import pytz
from flask import Flask, render_template, render_template_string, request, jsonify

app = Flask(__name__, template_folder="templates")

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
# MOTORE QUANTISTICO & ANTI-FALSI SEGNALI
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
    url = (
        f"{BASE_URL}/openApi/spot/v1/market/kline"
        if market_type == "SPOT"
        else f"{BASE_URL}/openApi/swap/v3/quote/klines"
    )
    try:
        resp = requests.get(
            url, params={"symbol": simbolo, "interval": interval, "limit": limit}, timeout=3.5
        )
        raw = resp.json().get("data", [])
        candles = []
        for k in raw:
            if isinstance(k, dict):
                candles.append({
                    "time": int(k.get("time", 0)),
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k.get("volume", 0)),
                })
            elif isinstance(k, list) and len(k) >= 6:
                candles.append({
                    "time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
        candles.sort(key=lambda c: c["time"])
        if len(candles) < 30:
            return None

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c["volume"] for c in candles]

        ema9 = _calcola_ema_serie(closes, 9)
        ema21 = _calcola_ema_serie(closes, 21)
        ema50 = _calcola_ema_serie(closes, 50)
        rsi = _calcola_rsi_wilder(closes, 14)
        atr = _calcola_atr_reale(highs, lows, closes, 14)

        # Filtro Anti Falsi Segnali e Rotture (conferma volumi + integrità del corpo candela)
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        current_vol = volumes[-1]
        is_volume_confirmed = current_vol >= (avg_vol * 0.70)

        last_candle = candles[-1]
        candle_range = last_candle["high"] - last_candle["low"]
        body_range = abs(last_candle["close"] - last_candle["open"])
        is_clean_breakout = candle_range == 0 or (body_range / candle_range >= 0.30)

        anti_false_valid = is_volume_confirmed and is_clean_breakout

        if market_type == "SPOT":
            bull = ema9 > ema21 and rsi >= 50.0 and anti_false_valid
            bear = False
        else:
            bull = ema9 > ema21 and rsi >= 50.0 and anti_false_valid
            bear = ema9 < ema21 and rsi <= 50.0 and anti_false_valid

        trend = "BUY" if bull else ("SELL" if bear else "NEUTRO")
        return {
            "interval": interval,
            "trend": trend,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "rsi14": round(rsi, 2),
            "atr14": atr,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
            "last": candles[-1],
            "antiFalseValid": anti_false_valid,
        }
    except Exception as e:
        print(f"[TF ERROR {interval}] {e}")
        return None


def calcola_parametri_quantistici_unificati(simbolo, market_type, style="Scalping"):
    fetch_type = "FUTURES" if market_type == "STANDARD_FUTURES" else market_type
    try:
        tfs = {iv: analizza_tf(simbolo, fetch_type, iv) for iv in ("5m", "15m", "1h", "4h")}
    except Exception:
        tfs = None

    if not tfs or any(v is None for v in tfs.values()):
        return None

    style_base_tf = {
        "Scalping": "5m",
        "Day Trading": "15m",
        "Swing Trading": "1h",
        "Long Term": "4h",
    }.get(style, "15m")

    trend_5m = tfs["5m"]["trend"]
    trend_15m = tfs["15m"]["trend"]
    trend_1h = tfs["1h"]["trend"]
    trend_4h = tfs["4h"]["trend"]

    if trend_5m == trend_15m == trend_1h == trend_4h and trend_5m != "NEUTRO":
        side = trend_5m
        score_precisione = 100.0
        favorabilita = 88.0
    elif trend_5m == trend_15m and trend_5m != "NEUTRO":
        side = trend_5m
        score_precisione = 75.0
        favorabilita = 68.0
    else:
        side = "BUY" if tfs[style_base_tf]["rsi14"] >= 50 else "SELL"
        score_precisione = 50.0
        favorabilita = 58.0

    base = tfs[style_base_tf]
    price = tfs["5m"]["last"]["close"]
    atr_reale = base["atr14"] if base["atr14"] > 0 else (price * 0.01)
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7

    if side == "BUY":
        min_recente = min(base["lows"][-5:])
        sl = round(min(min_recente, price - (atr_reale * 1.5)), dec)
        distanza_rischio = max(price - sl, price * 0.002)
        tp1 = round(price + (distanza_rischio * 1.5), dec)
        tp2 = round(price + (distanza_rischio * 2.5), dec)
        tp3 = round(price + (distanza_rischio * 4.0), dec)

        counter_side = "SELL"
        counter_sl = round(price + (atr_reale * 1.5), dec)
        counter_tp = round(price - (distanza_rischio * 1.5), dec)
        counter_desc = (
            f"La vendita sarebbe contro il movimento rialzista in corso ({price}) "
            f"e quindi un setup a bassa probabilità e più rischioso."
        )
    else:
        max_recente = max(base["highs"][-5:])
        sl = round(max(max_recente, price + (atr_reale * 1.5)), dec)
        distanza_rischio = max(sl - price, price * 0.002)
        tp1 = round(price - (distanza_rischio * 1.5), dec)
        tp2 = round(price - (distanza_rischio * 2.5), dec)
        tp3 = round(price - (distanza_rischio * 4.0), dec)

        counter_side = "BUY"
        counter_sl = round(price - (atr_reale * 1.5), dec)
        counter_tp = round(price + (distanza_rischio * 1.5), dec)
        counter_desc = (
            f"L'acquisto sarebbe contro il movimento ribassista in corso ({price}) "
            f"e quindi un setup a bassa probabilità e più rischioso."
        )

    entry_zone_min = round(price - (atr_reale * 0.2), dec)
    entry_zone_max = round(price + (atr_reale * 0.2), dec)

    candle_sec = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}.get(style_base_tf, 900)
    ultima_candela = base["last"]
    velocita_reale = max(atr_reale * 0.25 / candle_sec, price * 0.000015)
    dist_tp_val = abs(tp2 - price)
    tempo_esatto_sec = int(max(60, min(14400, round(dist_tp_val / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    is_safe, time_msg, borsa_rif, start_t, end_t = verifica_finestra_temporale_sicura()
    momento_giusto = is_safe and (side != "NEUTRO")

    return {
        "symbol": simbolo,
        "marketType": market_type,
        "style": style,
        "price": round(price, dec),
        "side": side,
        "entryZoneMin": entry_zone_min,
        "entryZoneMax": entry_zone_max,
        "sentiment": f"Anti-Falsi Filtro Attivo 🛡️ ({side})" if side != "NEUTRO" else "In attesa di Sincronizzazione",
        "favorability": favorabilita,
        "confluenza": f"{time_msg} 🎯",
        "borsaRif": borsa_rif,
        "momentoGiusto": momento_giusto,
        "exactTf": style_base_tf,
        "precisionScore": score_precisione,
        "ema9": round(base["ema9"], dec),
        "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"],
        "atr14": round(atr_reale, dec),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp": tp2,
        "counterTrade": {
            "side": counter_side,
            "entry": round(price, dec),
            "sl": counter_sl,
            "tp": counter_tp,
            "description": counter_desc,
        },
        "decimals": dec,
        "tempoSec": tempo_esatto_sec,
        "candleTime": timestamp_ultima_candela,
        "tfs": tfs,
        "isSafeTime": is_safe,
        "sessionStart": start_t,
        "sessionEnd": end_t,
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
                    "marketType": market_type,
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
        "timestamp": timestamp,
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


def esegui_ordine_bingx(
    simbolo: str,
    lato: str,
    quantita: float,
    prezzo_ingresso: float,
    prezzo_tp: float,
    prezzo_sl: float,
    leva: int = 10,
    timeframe: str = "15m",
    market_type: str = "FUTURES",
    api_key: str = "",
    secret_key: str = "",
):
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
            "timestamp": str(int(time.time() * 1000)),
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
            "timestamp": str(int(time.time() * 1000)),
        }
    else:
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000)),
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
@app.route("/api/market-data", methods=["GET"])
def api_market_data():
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    if mtype not in ["FUTURES", "SPOT", "STANDARD_FUTURES"]:
        mtype = "FUTURES"
    fetch_type = "FUTURES" if mtype == "STANDARD_FUTURES" else mtype
    data = get_live_market_data(fetch_type)
    return jsonify({"code": 0, "marketType": mtype, "data": data})


@app.route("/api/deep-scan", methods=["GET"])
def api_deep_scan():
    simbolo = request.args.get("symbol", "BTC-USDT")
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    style = request.args.get("style", "Scalping")
    res_data = calcola_parametri_quantistici_unificati(simbolo, mtype, style=style)
    if not res_data:
        return jsonify({"code": -1, "msg": "Candele multi-TF non disponibili"})
    return jsonify({"code": 0, "data": res_data})


@app.route("/api/esegui-segnale", methods=["POST"])
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

    risultato = esegui_ordine_bingx(
        simbolo,
        lato,
        quantita,
        prezzo_ingresso,
        tp,
        sl,
        leva,
        timeframe,
        mtype,
        dati.get("apiKey", ""),
        dati.get("secretKey", ""),
    )
    return jsonify(risultato)


@app.route("/api/set-mode", methods=["POST"])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    if dati.get("mode") in ["MANUAL", "AUTO"]:
        TRADING_MODE = dati.get("mode")
        return jsonify({"code": 0, "mode": TRADING_MODE})
    return jsonify({"code": -1, "msg": "Modalità non valida"})


@app.route("/api/get-mode", methods=["GET"])
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
        "sessionEnd": end_t,
    })


@app.route("/api/set-market-type", methods=["POST"])
def set_market_type():
    global MARKET_TYPE
    dati = request.json or {}
    if dati.get("marketType") in ["FUTURES", "SPOT", "STANDARD_FUTURES"]:
        MARKET_TYPE = dati.get("marketType")
        return jsonify({"code": 0, "marketType": MARKET_TYPE})
    return jsonify({"code": -1, "msg": "Mercato non valido"})


@app.route("/")
def home():
    template_path = os.path.join(app.template_folder or "templates", "index.html")
    if os.path.exists(template_path):
        return render_template("index.html")
    return render_template_string("<h1>Crea il file templates/index.html per visualizzare la dashboard.</h1>")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
