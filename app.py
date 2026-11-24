import os
import re
import time
import hmac
import hashlib
import urllib.parse
import requests
from datetime import datetime, timezone
import pytz
from flask import Flask, render_template, render_template_string, request, jsonify

app = Flask(__name__, template_folder="templates")

# ==========================================
# CREDENZIALI DA VARIABILI D'AMBIENTE RENDER
# ==========================================
def _leggi_env_chiave(nomi: list[str]) -> str:
    for nome in nomi:
        val = os.environ.get(nome)
        if val and val.strip():
            return val.strip()
    return ""


DEFAULT_API_KEY = _leggi_env_chiave(["BINGX_API_KEY", "API_KEY", "BINGX_KEY"])
DEFAULT_SECRET_KEY = _leggi_env_chiave(["BINGX_SECRET_KEY", "BINGX_API_SECRET", "SECRET_KEY"])
BASE_URL = "https://open-api.bingx.com"

MIN_ORDER_USDT = 2.0
TRADING_MODE = "MANUAL"
MARKET_TYPE = "FUTURES"  # "FUTURES", "SPOT" o "STANDARD_FUTURES"


def _normalizza_importo_minimo(valore) -> float:
    try:
        num = float(valore if valore is not None else MIN_ORDER_USDT)
    except (TypeError, ValueError):
        num = MIN_ORDER_USDT
    if num != num:  # NaN check
        return MIN_ORDER_USDT
    return max(MIN_ORDER_USDT, num)


def _risolvi_credenziali(api_key: str = "", secret_key: str = "") -> tuple[str, str, str]:
    env_api = _leggi_env_chiave(["BINGX_API_KEY", "API_KEY", "BINGX_KEY"]) or DEFAULT_API_KEY
    env_secret = _leggi_env_chiave(["BINGX_SECRET_KEY", "BINGX_API_SECRET", "SECRET_KEY"]) or DEFAULT_SECRET_KEY

    clean_api = (api_key or "").strip()
    clean_secret = (secret_key or "").strip()

    if clean_api and clean_secret:
        return clean_api, clean_secret, "custom"
    if env_api and env_secret:
        return env_api, env_secret, "render_env"
    return "", "", "none"


def _genera_firma(payload: dict, secret_key: str) -> str:
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature


def _signed_get(endpoint: str, extra_params: dict, api_key: str, secret_key: str) -> dict:
    payload = dict(extra_params or {})
    payload["timestamp"] = str(int(time.time() * 1000))
    query_string = _genera_firma(payload, secret_key)
    headers = {"X-BX-APIKEY": api_key}
    resp = requests.get(f"{BASE_URL}{endpoint}?{query_string}", headers=headers, timeout=6)
    return resp.json()


def _signed_post(endpoint: str, payload_ordine: dict, active_api: str, active_secret: str) -> dict:
    url = BASE_URL + endpoint
    query_string = _genera_firma(payload_ordine, active_secret)
    headers = {"X-BX-APIKEY": active_api}
    response = requests.post(f"{url}?{query_string}", headers=headers, timeout=6)
    return response.json()


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
# MOTORE QUANTISTICO, TELEMETRIA & ANTI-FALSI
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


def _classifica_categoria_e_label(symbol: str, market_type: str) -> tuple[str, str]:
    sym_upper = (symbol or "").upper()
    if any(k in sym_upper for k in ["PAXG", "XAU", "GOLD"]):
        return "Materie Prime", f"Oro / Gold ({symbol})"
    if any(k in sym_upper for k in ["XPT", "PLATINUM"]):
        return "Materie Prime", f"Platino / Platinum ({symbol})"
    if any(k in sym_upper for k in ["XAG", "SILVER"]):
        return "Materie Prime", f"Argento / Silver ({symbol})"
    if any(k in sym_upper for k in ["WTI", "BRENT", "OIL", "NATGAS", "COPPER"]):
        return "Materie Prime", f"Commodity ({symbol})"
    if any(fx in sym_upper for fx in ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]):
        return "Forex / Valute", f"Forex ({symbol})"
    if any(st in sym_upper for st in ["NASDAQ", "SP500", "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "US30", "COIN", "META", "GOOG"]):
        return "Azioni & Indici (TradFi)", f"TradFi ({symbol})"
    return "Criptovalute", symbol


def _calcola_finestra_candela(tf_str: str = "15m", now_ts: float | None = None) -> dict:
    ts = int(now_ts if now_ts is not None else time.time())
    tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
    durata = tf_map.get(tf_str, 900)
    apertura_ts = (ts // durata) * durata
    chiusura_ts = apertura_ts + durata
    residuo_sec = max(1, chiusura_ts - ts)
    return {
        "timeframe": tf_str,
        "tfSeconds": durata,
        "openUtc": datetime.fromtimestamp(apertura_ts, tz=timezone.utc).strftime("%H:%M:%S"),
        "closeUtc": datetime.fromtimestamp(chiusura_ts, tz=timezone.utc).strftime("%H:%M:%S"),
        "remainingSec": residuo_sec,
    }


def analizza_tf(simbolo, market_type, interval, limit=100):
    url = (
        f"{BASE_URL}/openApi/spot/v1/market/kline"
        if market_type == "SPOT"
        else f"{BASE_URL}/openApi/swap/v3/quote/klines"
    )
    try:
        resp = requests.get(
            url, params={"symbol": simbolo, "interval": interval, "limit": limit}, timeout=4.0
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

        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        current_vol = volumes[-1]
        is_volume_confirmed = current_vol >= (avg_vol * 0.65)

        last_candle = candles[-1]
        candle_range = last_candle["high"] - last_candle["low"]
        body_range = abs(last_candle["close"] - last_candle["open"])
        is_clean_breakout = candle_range == 0 or (body_range / candle_range >= 0.25)

        anti_false_valid = is_volume_confirmed and is_clean_breakout

        if market_type == "SPOT":
            bull = ema9 > ema21 and rsi >= 48.0 and anti_false_valid
            bear = False
        else:
            bull = ema9 > ema21 and rsi >= 48.0 and anti_false_valid
            bear = ema9 < ema21 and rsi <= 52.0 and anti_false_valid

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


def _calcola_certezza_reale(tfs: dict, side: str, base_tf: str) -> float:
    all_trends = [tfs[k]["trend"] for k in ("5m", "15m", "1h", "4h")]
    concordi_all = sum(1 for tr in all_trends if tr == side)
    htf_concordi = sum(1 for tr in (tfs["1h"]["trend"], tfs["4h"]["trend"]) if tr == side)
    rsi_val = float(tfs[base_tf]["rsi14"])
    rsi_aligned = (rsi_val >= 52.0) if side == "BUY" else (rsi_val <= 48.0)
    anti_false_count = sum(1 for k in ("5m", "15m", "1h", "4h") if tfs[k].get("antiFalseValid"))

    score = 52.0 + (concordi_all * 8.0) + (htf_concordi * 5.0) + (3.5 if rsi_aligned else 0.0) + (anti_false_count * 1.5)
    return round(min(97.5, max(54.0, score)), 1)


def _formatta_quantita_contratto(qty: float, price: float) -> str:
    q = max(0.001, float(qty or 0.0))
    if price >= 5000:
        return f"{q:.3f}"
    if price >= 50:
        return f"{q:.2f}"
    if price >= 1:
        return f"{q:.1f}"
    return f"{int(max(1, round(q)))}"


def calcola_parametri_quantistici_unificati(simbolo, market_type, style="Scalping", margin_usdt=2.0, leverage=10):
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

    # Richiede allineamento reale tra 1h e 4h (no forzatura RSI quando 1h e 4h divergono o sono neutri)
    if trend_1h == trend_4h and trend_1h in ("BUY", "SELL"):
        side = trend_1h
        if trend_5m == trend_15m == side:
            score_precisione = 100.0
            favorabilita = 88.0
        elif trend_15m == side or trend_5m == side:
            score_precisione = 84.0
            favorabilita = 80.0
        else:
            score_precisione = 76.0
            favorabilita = 75.0
    else:
        side = "NEUTRO"
        score_precisione = 50.0
        favorabilita = 50.0

    certainty = _calcola_certezza_reale(tfs, side if side in ("BUY", "SELL") else "BUY", style_base_tf) if side in ("BUY", "SELL") else 50.0
    base = tfs[style_base_tf]
    price = tfs["5m"]["last"]["close"]
    atr_reale = base["atr14"] if base["atr14"] > 0 else (price * 0.01)
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7

    if side == "BUY":
        min_recente = min(base["lows"][-5:])
        sl = round(min(min_recente, price - (atr_reale * 1.8)), dec)
        rischio_unit = max(price - sl, price * 0.003)
        tp1 = round(price + (rischio_unit * 1.5), dec)
        tp2 = round(price + (rischio_unit * 2.1), dec)
        tp3 = round(price + (rischio_unit * 3.2), dec)
        reward_unit = max(tp2 - price, rischio_unit * 1.5)

        counter_side = "SELL"
        counter_sl = round(price + (atr_reale * 1.8), dec)
        counter_tp = round(price - (rischio_unit * 1.5), dec)
        counter_desc = (
            f"La vendita sarebbe contro il trend 1h/4h rialzista ({price}) "
            f"e quindi un setup a bassa probabilità e più rischioso."
        )
    elif side == "SELL":
        max_recente = max(base["highs"][-5:])
        sl = round(max(max_recente, price + (atr_reale * 1.8)), dec)
        rischio_unit = max(sl - price, price * 0.003)
        tp1 = round(price - (rischio_unit * 1.5), dec)
        tp2 = round(price - (rischio_unit * 2.1), dec)
        tp3 = round(price - (rischio_unit * 3.2), dec)
        reward_unit = max(price - tp2, rischio_unit * 1.5)

        counter_side = "BUY"
        counter_sl = round(price - (atr_reale * 1.8), dec)
        counter_tp = round(price + (rischio_unit * 1.5), dec)
        counter_desc = (
            f"L'acquisto sarebbe contro il trend 1h/4h ribassista ({price}) "
            f"e quindi un setup a bassa probabilità e più rischioso."
        )
    else:
        sl = round(price - (atr_reale * 1.8), dec)
        tp1 = round(price + (atr_reale * 1.5), dec)
        tp2 = round(price + (atr_reale * 2.1), dec)
        tp3 = round(price + (atr_reale * 3.2), dec)
        rischio_unit = max(price - sl, price * 0.003)
        reward_unit = max(tp2 - price, rischio_unit * 1.5)

        counter_side = "NEUTRO"
        counter_sl = sl
        counter_tp = tp1
        counter_desc = (
            "Timeframe 1h e 4h non allineati o in fase neutra: attendere conferma strutturale "
            "prima di aprire posizioni direzionali."
        )

    eff_margin = _normalizza_importo_minimo(margin_usdt)
    eff_lev = _calcola_leva_dinamica(eff_margin, leverage, market_type)
    notional = eff_margin * eff_lev
    raw_qty = (notional / price) if price > 0 else 0.0
    size_qty = float(_formatta_quantita_contratto(raw_qty, price))

    rr_ratio = round(reward_unit / rischio_unit, 2) if rischio_unit > 0 else 1.5
    p_win = min(0.95, max(0.51, certainty / 100.0)) if side != "NEUTRO" else 0.50
    p_loss = 1.0 - p_win
    payoff_win = notional * (reward_unit / price)
    payoff_loss = notional * (rischio_unit / price)
    ev_usd = round((p_win * payoff_win) - (p_loss * payoff_loss), 4) if side != "NEUTRO" else 0.0
    kelly_pct = round(max(1.0, min(25.0, ((p_win * rr_ratio - p_loss) / max(rr_ratio, 0.1)) * 100.0)), 1) if side != "NEUTRO" else 0.0

    candele_eta = max(2, min(8, int(round((rr_ratio * 1.6)))))
    minuti_eta = candele_eta * 15
    target_eta_ts = int(time.time()) + (minuti_eta * 60)
    target_eta_utc = datetime.fromtimestamp(target_eta_ts, tz=timezone.utc).strftime("%H:%M UTC")

    entry_zone_min = round(price - (atr_reale * 0.2), dec)
    entry_zone_max = round(price + (atr_reale * 0.2), dec)

    candle_sec = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}.get(style_base_tf, 900)
    ultima_candela = base["last"]
    velocita_reale = max(atr_reale * 0.25 / candle_sec, price * 0.000015)
    dist_tp_val = abs(tp2 - price)
    tempo_esatto_sec = int(max(60, min(14400, round(dist_tp_val / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    category, display_label = _classifica_categoria_e_label(simbolo, market_type)
    candle_window = _calcola_finestra_candela(style_base_tf)
    is_safe, time_msg, borsa_rif, start_t, end_t = verifica_finestra_temporale_sicura()
    momento_giusto = is_safe and (side != "NEUTRO")

    return {
        "symbol": simbolo,
        "displayLabel": display_label,
        "category": category,
        "marketType": market_type,
        "style": style,
        "price": round(price, dec),
        "side": side,
        "htfBias": "BULLISH" if side == "BUY" else ("BEARISH" if side == "SELL" else "NEUTRAL"),
        "size": size_qty,
        "leverage": eff_lev,
        "marginUsdt": round(eff_margin, 2),
        "rr": rr_ratio,
        "evUsd": ev_usd,
        "kellyPct": kelly_pct,
        "certaintyPct": certainty,
        "etaMinutes": minuti_eta,
        "etaCandles": candele_eta,
        "etaUtc": target_eta_utc,
        "candle15m": candle_window,
        "entryZoneMin": entry_zone_min,
        "entryZoneMax": entry_zone_max,
        "sentiment": f"Filtro 1h/4h Attivo ({side})" if side != "NEUTRO" else "In attesa di allineamento 1h/4h (NEUTRO)",
        "favorability": favorabilita,
        "confluenza": f"{time_msg}",
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

                category, display_label = _classifica_categoria_e_label(symbol, market_type)

                market_map[symbol] = {
                    "price": price,
                    "change24h": change,
                    "volume": quote_vol,
                    "category": category,
                    "displayLabel": display_label,
                    "marketType": market_type,
                }
            return market_map
    except Exception as e:
        print(f"[MARKET ERROR] {e}")
    return {}


def _calcola_leva_dinamica(capitale: float, leva_richiesta: int, market_type: str) -> int:
    if market_type == "SPOT":
        return 1
    if capitale > 0 and capitale <= 2.0 and int(leva_richiesta or 0) == 10:
        return 20
    return max(1, int(leva_richiesta or 10))


def imposta_leva_bingx(simbolo: str, leva: int, lato: str, api_key: str, secret_key: str):
    position_side = "LONG" if lato == "BUY" else "SHORT"
    for pos_side in (position_side, "BOTH"):
        try:
            _signed_post(
                "/openApi/swap/v2/trade/leverage",
                {
                    "symbol": simbolo,
                    "leverage": str(leva),
                    "positionSide": pos_side,
                    "timestamp": str(int(time.time() * 1000)),
                },
                api_key,
                secret_key,
            )
        except Exception as e:
            print(f"[LEVERAGE EXCEPT] {e}")


def _piazza_sl_tp_swap_reali(
    simbolo: str,
    lato_ingresso: str,
    quantita_str: str,
    prezzo_sl: float,
    prezzo_tp: float,
    active_api: str,
    active_secret: str,
) -> dict:
    close_side = "SELL" if lato_ingresso == "BUY" else "BUY"
    position_side = "LONG" if lato_ingresso == "BUY" else "SHORT"
    out = {}

    if prezzo_sl and float(prezzo_sl) > 0:
        try:
            sl_res = _signed_post(
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": simbolo,
                    "side": close_side,
                    "positionSide": position_side,
                    "type": "STOP_MARKET",
                    "stopPrice": str(prezzo_sl),
                    "quantity": quantita_str,
                    "workingType": "MARK_PRICE",
                    "timestamp": str(int(time.time() * 1000)),
                },
                active_api,
                active_secret,
            )
            out["slOrder"] = sl_res
        except Exception as e:
            out["slOrderError"] = str(e)

    if prezzo_tp and float(prezzo_tp) > 0:
        try:
            tp_res = _signed_post(
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": simbolo,
                    "side": close_side,
                    "positionSide": position_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": str(prezzo_tp),
                    "quantity": quantita_str,
                    "workingType": "MARK_PRICE",
                    "timestamp": str(int(time.time() * 1000)),
                },
                active_api,
                active_secret,
            )
            out["tpOrder"] = tp_res
        except Exception as e:
            out["tpOrderError"] = str(e)

    return out


def _scala_quantita_fallback(qty_val: float, prezzo_ingresso: float, tentativo: int) -> str:
    step_table = [1.0, 0.5, 0.25, 0.1, 0.05]
    fattore = step_table[min(tentativo, len(step_table) - 1)]
    nuova_qty = qty_val * fattore
    if prezzo_ingresso >= 5000:
        return f"{max(0.001, round(nuova_qty, 3)):.3f}"
    if prezzo_ingresso >= 50:
        return f"{max(0.01, round(nuova_qty, 2)):.2f}"
    if prezzo_ingresso >= 1:
        return f"{max(0.1, round(nuova_qty, 1)):.1f}"
    return f"{int(max(1, round(nuova_qty)))}"


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
    margin_usdt: float = MIN_ORDER_USDT,
):
    active_api, active_secret, _ = _risolvi_credenziali(api_key, secret_key)

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti (verifica variabili d'ambiente su Render)."}

    eff_margin = _normalizza_importo_minimo(margin_usdt)
    eff_lev = _calcola_leva_dinamica(eff_margin, leva, market_type)

    base_qty = float(quantita or 0.0)
    if base_qty <= 0 and prezzo_ingresso > 0:
        base_qty = (eff_margin * eff_lev) / float(prezzo_ingresso)

    qty_str = _formatta_quantita_contratto(base_qty, float(prezzo_ingresso or 0.0))

    if market_type == "STANDARD_FUTURES":
        endpoint = "/openApi/futures/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": qty_str,
            "leverage": str(eff_lev),
            "timestamp": str(int(time.time() * 1000)),
        }
    elif market_type == "FUTURES":
        imposta_leva_bingx(simbolo, eff_lev, lato, active_api, active_secret)
        endpoint = "/openApi/swap/v2/trade/order"
        position_side = "LONG" if lato == "BUY" else "SHORT"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": str(int(time.time() * 1000)),
        }
    else:
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": str(int(time.time() * 1000)),
        }

    try:
        res = _signed_post(endpoint, payload_ordine, active_api, active_secret)
        last_err_msg = str(res.get("msg", "")) if isinstance(res, dict) else ""

        for tentativo in range(1, 5):
            if res.get("code") == 0:
                break
            msg = str(res.get("msg", "")).lower()
            last_err_msg = str(res.get("msg", ""))
            match_min = re.search(r"(?:minimum order amount is|is)\s+([0-9.]+)", msg, re.IGNORECASE)
            if match_min:
                min_qty = float(match_min.group(1))
                qty_str = _formatta_quantita_contratto(min_qty * 1.05, float(prezzo_ingresso or 0.0))
            elif any(k in msg for k in ["insufficient", "balance", "margin", "precision", "step", "amount"]):
                qty_str = _scala_quantita_fallback(base_qty, float(prezzo_ingresso or 0.0), tentativo)
            else:
                break

            payload_ordine["quantity"] = qty_str
            payload_ordine["timestamp"] = str(int(time.time() * 1000))
            res = _signed_post(endpoint, payload_ordine, active_api, active_secret)

        if isinstance(res, dict):
            res["appliedLeverage"] = eff_lev
            res["appliedMarginUsdt"] = eff_margin
            res["appliedQuantity"] = qty_str
            res["bingxRaw"] = {
                "code": res.get("code"),
                "msg": res.get("msg") or last_err_msg,
                "endpoint": endpoint,
                "quantitySent": qty_str,
            }
            if res.get("code") != 0 and res.get("msg"):
                res["msg"] = f"BingX [{res.get('code')}]: {res.get('msg')} (Qty tentata: {qty_str}, Importo: ${eff_margin:.2f}, Leva: {eff_lev}x)"
            if res.get("code") == 0 and market_type == "FUTURES":
                res["protectionOrders"] = _piazza_sl_tp_swap_reali(
                    simbolo, lato, qty_str, prezzo_sl, prezzo_tp, active_api, active_secret
                )
        return res
    except Exception as e:
        return {"code": -1, "msg": f"Errore esecuzione BingX: {e}", "bingxRaw": {"error": str(e)}}


def chiudi_posizione_bingx_reale(
    simbolo: str,
    lato_posizione: str = "",
    quantita: float = 0.0,
    market_type: str = "FUTURES",
    api_key: str = "",
    secret_key: str = "",
) -> dict:
    active_api, active_secret, _ = _risolvi_credenziali(api_key, secret_key)

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti per chiudere la posizione."}

    if market_type == "SPOT":
        return {"code": -1, "msg": "Chiusura automatica swap non applicabile a SPOT."}

    target_side = lato_posizione.upper()
    target_qty = float(quantita or 0.0)

    if not target_side or target_qty <= 0:
        try:
            pos_res = _signed_get("/openApi/swap/v2/user/positions", {}, active_api, active_secret)
            if pos_res.get("code") == 0 and isinstance(pos_res.get("data"), list):
                for p in pos_res["data"]:
                    if p.get("symbol") == simbolo:
                        amt = abs(float(p.get("positionAmt", 0) or 0))
                        if amt > 0:
                            target_qty = amt
                            target_side = str(p.get("positionSide", "LONG")).upper()
                            break
        except Exception as e:
            return {"code": -1, "msg": f"Errore lettura posizione: {e}"}

    if target_qty <= 0:
        return {"code": 0, "msg": f"Nessuna posizione aperta trovata su BingX per {simbolo}."}

    close_side = "SELL" if target_side in ("BUY", "LONG") else "BUY"
    pos_side = "LONG" if target_side in ("BUY", "LONG") else "SHORT"

    return _signed_post(
        "/openApi/swap/v2/trade/order",
        {
            "symbol": simbolo,
            "side": close_side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": str(target_qty),
            "reduceOnly": "true",
            "timestamp": str(int(time.time() * 1000)),
        },
        active_api,
        active_secret,
    )


def get_bingx_account_telemetry(api_key: str, secret_key: str) -> dict:
    active_api, active_secret, source = _risolvi_credenziali(api_key, secret_key)
    if not active_api or not active_secret:
        return {
            "connected": False,
            "keySource": "none",
            "walletBalance": 0.0,
            "availableMargin": 0.0,
            "unrealizedPnl": 0.0,
            "netPnlToday": 0.0,
            "feesFundingToday": 0.0,
            "grossPnlToday": 0.0,
            "netPnlMonth": 0.0,
            "positions": [],
            "msg": "Imposta BINGX_API_KEY e BINGX_SECRET_KEY su Render oppure inseriscile in Chiavi API.",
        }

    wallet_balance = 0.0
    available_margin = 0.0
    unrealized_pnl = 0.0
    positions_list = []

    try:
        bal_res = _signed_get("/openApi/swap/v2/user/balance", {}, active_api, active_secret)
        if bal_res.get("code") == 0:
            bal_obj = bal_res.get("data", {}).get("balance", bal_res.get("data", {}))
            if isinstance(bal_obj, list):
                bal_obj = next((b for b in bal_obj if b.get("asset") == "USDT"), bal_obj[0] if bal_obj else {})
            if isinstance(bal_obj, dict):
                wallet_balance = float(bal_obj.get("balance", bal_obj.get("equity", 0)) or 0)
                raw_avail = bal_obj.get("availableMargin", bal_obj.get("crossWalletBalance", bal_obj.get("free", None)))
                available_margin = float(raw_avail if raw_avail is not None else wallet_balance)
                unrealized_pnl = float(bal_obj.get("unrealizedProfit", 0) or 0)
    except Exception as e:
        print(f"[BALANCE ERROR] {e}")

    try:
        pos_res = _signed_get("/openApi/swap/v2/user/positions", {}, active_api, active_secret)
        if pos_res.get("code") == 0 and isinstance(pos_res.get("data"), list):
            for p in pos_res["data"]:
                amt = abs(float(p.get("positionAmt", 0) or 0))
                if amt > 0:
                    upnl = float(p.get("unrealizedProfit", 0) or 0)
                    positions_list.append({
                        "symbol": p.get("symbol"),
                        "side": p.get("positionSide", "LONG"),
                        "size": amt,
                        "leverage": int(float(p.get("leverage", 1) or 1)),
                        "entryPrice": float(p.get("avgPrice", p.get("entryPrice", 0)) or 0),
                        "markPrice": float(p.get("markPrice", 0) or 0),
                        "unrealizedPnl": round(upnl, 4),
                    })
            if unrealized_pnl == 0.0 and positions_list:
                unrealized_pnl = sum(x["unrealizedPnl"] for x in positions_list)
    except Exception as e:
        print(f"[POSITIONS ERROR] {e}")

    now_utc = datetime.now(timezone.utc)
    start_day_ms = int(datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc).timestamp() * 1000)
    start_month_ms = int(datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc).timestamp() * 1000)

    realized_today = 0.0
    fees_funding_today = 0.0
    realized_month = 0.0
    fees_funding_month = 0.0

    try:
        inc_res = _signed_get(
            "/openApi/swap/v2/user/income",
            {"startTime": start_month_ms, "limit": 500},
            active_api,
            active_secret,
        )
        if inc_res.get("code") == 0 and isinstance(inc_res.get("data"), list):
            for item in inc_res["data"]:
                inc_val = float(item.get("income", 0) or 0)
                inc_type = str(item.get("incomeType", "")).upper()
                ts_val = int(item.get("time", 0) or 0)
                is_fee_or_funding = any(k in inc_type for k in ["FEE", "FUNDING", "COMMISSION"])

                if ts_val >= start_month_ms:
                    if is_fee_or_funding:
                        fees_funding_month += inc_val
                    else:
                        realized_month += inc_val

                if ts_val >= start_day_ms:
                    if is_fee_or_funding:
                        fees_funding_today += inc_val
                    else:
                        realized_today += inc_val
    except Exception as e:
        print(f"[INCOME ERROR] {e}")

    gross_pnl_today = round(realized_today, 4)
    fees_today_rounded = round(fees_funding_today, 4)
    net_pnl_today = round(realized_today + fees_funding_today, 4)
    net_pnl_month = round(realized_month + fees_funding_month, 4)

    return {
        "connected": True,
        "keySource": source,
        "walletBalance": round(wallet_balance, 4),
        "availableMargin": round(available_margin, 4),
        "unrealizedPnl": round(unrealized_pnl, 4),
        "netPnlToday": net_pnl_today,
        "feesFundingToday": fees_today_rounded,
        "grossPnlToday": gross_pnl_today,
        "netPnlMonth": net_pnl_month,
        "positions": positions_list,
    }


# ==========================================
# ENDPOINT API FLASK
# ==========================================
@app.route("/api/live-ticker", methods=["GET"])
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
    margin_usdt = _normalizza_importo_minimo(request.args.get("margin", MIN_ORDER_USDT))
    leverage = int(float(request.args.get("leverage", 10) or 10))
    res_data = calcola_parametri_quantistici_unificati(
        simbolo, mtype, style=style, margin_usdt=margin_usdt, leverage=leverage
    )
    if not res_data:
        return jsonify({"code": -1, "msg": "Candele multi-TF non disponibili"})
    return jsonify({"code": 0, "data": res_data})


@app.route("/api/account-telemetry", methods=["POST"])
def api_account_telemetry():
    dati = request.json or {}
    telemetry = get_bingx_account_telemetry(dati.get("apiKey", ""), dati.get("secretKey", ""))
    return jsonify({"code": 0, "data": telemetry})


@app.route("/api/close-position", methods=["POST"])
def api_close_position():
    dati = request.json or {}
    res = chiudi_posizione_bingx_reale(
        simbolo=dati.get("symbol", ""),
        lato_posizione=dati.get("side", ""),
        quantita=float(dati.get("size", 0.0) or 0.0),
        market_type=dati.get("marketType", MARKET_TYPE).upper(),
        api_key=dati.get("apiKey", ""),
        secret_key=dati.get("secretKey", ""),
    )
    return jsonify(res)


@app.route("/api/esegui-segnale", methods=["POST"])
def webhook_esegui_segnale():
    dati = request.json or {}
    simbolo = dati.get("symbol")
    lato = dati.get("action")
    quantita = float(dati.get("size", 0.0) or 0.0)
    prezzo_ingresso = float(dati.get("entry", dati.get("price", 0)) or 0.0)
    tp = float(dati.get("tp", 0) or 0.0)
    sl = float(dati.get("sl", 0) or 0.0)
    leva = int(dati.get("leverage", 10) or 10)
    timeframe = dati.get("timeframe", "15m")
    mtype = dati.get("marketType", MARKET_TYPE).upper()
    is_auto = dati.get("is_auto", False)
    margin_usdt = _normalizza_importo_minimo(dati.get("marginUsdt", MIN_ORDER_USDT))

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Modalità Automatica disattivata."})

    if lato not in ("BUY", "SELL"):
        return jsonify({"code": -98, "msg": "Segnale NEUTRO o direzione non valida: ordine bloccato."})

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
        margin_usdt=margin_usdt,
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
    _, _, key_source = _risolvi_credenziali("", "")
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return jsonify({
        "code": 0,
        "mode": TRADING_MODE,
        "marketType": MARKET_TYPE,
        "minOrderUsdt": MIN_ORDER_USDT,
        "envKeysConfigured": key_source == "render_env",
        "keySource": key_source,
        "utcNow": now_utc,
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
