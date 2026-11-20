import hashlib
import hmac
import math
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "").strip()
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "").strip()

DIRECTION_COOLDOWN_SEC = 120.0
_LAST_CONFIRMED_DIRECTION = {}
_LIVE_PRICE_CACHE = {}

STATE_LOCK = threading.Lock()
STATE = {
    "auto_trade": False,
    "real_mode": True,
    "scans_count": 0,
    "session_pnl": 0.0,
    "telemetry_logs": [],
    "closed_trades": [],
    "last_bingx_sync": 0.0,
}

MARKET_CATALOG = {
    "Criptovalute Globali": [
        {"symbol": "BTCUSDT", "bx": "BTC-USDT", "name": "Bitcoin (BTC-USDT)", "precision": 1},
        {"symbol": "ETHUSDT", "bx": "ETH-USDT", "name": "Ethereum (ETH-USDT)", "precision": 2},
        {"symbol": "SOLUSDT", "bx": "SOL-USDT", "name": "Solana (SOL-USDT)", "precision": 2},
        {"symbol": "XRPUSDT", "bx": "XRP-USDT", "name": "Ripple (XRP-USDT)", "precision": 4},
        {"symbol": "BNBUSDT", "bx": "BNB-USDT", "name": "BNB (BNB-USDT)", "precision": 2},
        {"symbol": "ADAUSDT", "bx": "ADA-USDT", "name": "Cardano (ADA-USDT)", "precision": 4},
        {"symbol": "DOGEUSDT", "bx": "DOGE-USDT", "name": "Dogecoin (DOGE-USDT)", "precision": 5},
        {"symbol": "AVAXUSDT", "bx": "AVAX-USDT", "name": "Avalanche (AVAX-USDT)", "precision": 2},
        {"symbol": "LINKUSDT", "bx": "LINK-USDT", "name": "Chainlink (LINK-USDT)", "precision": 3},
        {"symbol": "SUIUSDT", "bx": "SUI-USDT", "name": "Sui (SUI-USDT)", "precision": 4},
    ],
    "Materie Prime & Forex": [
        {"symbol": "PAXGUSDT", "bx": "PAXG-USDT", "name": "Oro (GOLD(XAU)-USDT)", "precision": 2},
        {"symbol": "XAGUSDT", "bx": "XAG-USDT", "name": "Argento (SILVER(XAG)-USDT)", "precision": 3},
        {"symbol": "WTIUSDT", "bx": "WTI-USDT", "name": "Petrolio WTI (WTI-USDT)", "precision": 2},
        {"symbol": "NATGASUSDT", "bx": "NATGAS-USDT", "name": "Gas Naturale (NATGAS-USDT)", "precision": 3},
        {"symbol": "COPPERUSDT", "bx": "COPPER-USDT", "name": "Rame (COPPER-USDT)", "precision": 3},
        {"symbol": "GBPUSDT", "bx": "GBP-USDT", "name": "GBP/USD (GBPUSD-USDT)", "precision": 4},
        {"symbol": "EURUSDT", "bx": "EUR-USDT", "name": "EUR/USD (EURUSD-USDT)", "precision": 4},
        {"symbol": "USDJPY", "bx": "USDJPY-USDT", "name": "USD/JPY (USDJPY-USDT)", "precision": 2},
    ],
    "Azioni & Indici Globali": [
        {"symbol": "AAPLUSDT", "bx": "AAPL-USDT", "name": "Apple Inc. (AAPL-USDT)", "precision": 2},
        {"symbol": "TSLAUSDT", "bx": "TSLA-USDT", "name": "Tesla Inc. (TSLA-USDT)", "precision": 2},
        {"symbol": "NVDAUSDT", "bx": "NVDA-USDT", "name": "Nvidia Corp. (NVDA-USDT)", "precision": 2},
        {"symbol": "AMZNUSDT", "bx": "AMZN-USDT", "name": "Amazon (AMZN-USDT)", "precision": 2},
        {"symbol": "MSFTUSDT", "bx": "MSFT-USDT", "name": "Microsoft (MSFT-USDT)", "precision": 2},
        {"symbol": "GOOGLUSDT", "bx": "GOOGL-USDT", "name": "Alphabet (GOOGL-USDT)", "precision": 2},
    ],
}

ASSET_LOOKUP = {}
for _cat, _items in MARKET_CATALOG.items():
    for _it in _items:
        ASSET_LOOKUP[_it["symbol"]] = {**_it, "category": _cat}


def safe_float(val, default=0.0):
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return float(default)
        out = float(val)
        return float(default) if (math.isnan(out) or math.isinf(out)) else out
    except Exception:
        return float(default)


def canonicalize_symbol_key(sym: str) -> str:
    s = (sym or "").upper().replace("-", "").replace("/", "").replace(":USDT", "").strip()
    if s.endswith("USDT") and len(s) > 4:
        return s
    return f"{s}USDT" if s else "BTCUSDT"


def resolve_bingx_symbol(symbol: str):
    key = canonicalize_symbol_key(symbol)
    meta = ASSET_LOOKUP.get(key)
    if meta:
        return meta["bx"], meta["precision"]
    base = key[:-4] if key.endswith("USDT") else key
    return f"{base}-USDT", 2


def get_market_calibration_spec(symbol: str, category: str, price: float):
    key = canonicalize_symbol_key(symbol)
    meta = ASSET_LOOKUP.get(key)
    if meta:
        decimals = meta["precision"]
    elif price >= 1000:
        decimals = 1
    elif price >= 10:
        decimals = 2
    elif price >= 1:
        decimals = 4
    else:
        decimals = 5

    if category == "Criptovalute Globali":
        return decimals, 0.0045, 1.8
    if category == "Materie Prime & Forex":
        return decimals, 0.0035, 1.8
    return decimals, 0.0040, 1.8


def quantize_to_tick(value: float, decimals: int, tick_size: float) -> float:
    if tick_size <= 0:
        return round(value, decimals)
    quantized = round(value / tick_size) * tick_size
    return round(quantized, decimals)


def align_tp_sl_to_price(action: str, entry: float, raw_sl: float, raw_tp: float, sl_m: float, tp_m: float, decimals: int):
    tick = 10 ** (-decimals)
    min_step = max(tick * 2, entry * 0.0015)
    if action == "BUY":
        sl_val = min(raw_sl, entry - min_step)
        tp_val = max(raw_tp, entry + min_step * 1.5)
    elif action == "SELL":
        sl_val = max(raw_sl, entry + min_step)
        tp_val = min(raw_tp, entry - min_step * 1.5)
    else:
        sl_val = min(raw_sl, entry - min_step)
        tp_val = max(raw_tp, entry + min_step * 1.5)
    return quantize_to_tick(sl_val, decimals, tick), quantize_to_tick(tp_val, decimals, tick)


def compute_order_quantity(bx_symbol: str, price: float, raw_qty: float, balance: float, leverage: int) -> float:
    if price <= 0:
        return 0.0
    max_notional = max(balance, 2.0) * max(leverage, 1) * 0.90
    capped_qty = min(max(raw_qty, 0.0), max_notional / price)
    if "BTC" in bx_symbol:
        step = 0.0001
    elif "ETH" in bx_symbol or "PAXG" in bx_symbol:
        step = 0.001
    elif price >= 50:
        step = 0.01
    elif price >= 1:
        step = 0.1
    else:
        step = 1.0
    qty = max(step, math.floor(capped_qty / step) * step)
    dec = len(str(step).split(".")[1]) if "." in str(step) else 0
    return round(qty, dec)


def get_session_statuses():
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()
    hour_float = now_utc.hour + now_utc.minute / 60.0

    crypto_open = True
    commodities_open = weekday < 5
    equities_open = (weekday < 5) and (14.5 <= hour_float <= 21.0)

    return {
        "Criptovalute Globali": {
            "status": "APERTO (24/7)",
            "is_open": crypto_open,
            "hours": "24/7 Continuo (UTC)",
            "badge_color": "#00ff66",
        },
        "Materie Prime & Forex": {
            "status": "APERTO (Live)" if commodities_open else "CHIUSO (Weekend)",
            "is_open": commodities_open,
            "hours": "Lun-Ven Sessione Globale UTC",
            "badge_color": "#00ff66" if commodities_open else "#ff2a4b",
        },
        "Azioni & Indici Globali": {
            "status": "APERTO (NYSE/NASDAQ)" if equities_open else "CHIUSO (Fuori Orario)",
            "is_open": equities_open,
            "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC (Lun-Ven)",
            "badge_color": "#00ff66" if equities_open else "#ff2a4b",
        },
    }


def sign_bingx_query(params: dict) -> str:
    query_str = urlencode(sorted(params.items()))
    sig = hmac.new(BINGX_API_SECRET.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_str}&signature={sig}"


def bingx_signed_request(method: str, path: str, params: dict):
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return None, "API Key / Secret BingX non configurate"
    req_params = dict(params or {})
    req_params["timestamp"] = int(time.time() * 1000)
    signed_qs = sign_bingx_query(req_params)
    url = f"{BINGX_BASE_URL}{path}?{signed_qs}"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    try:
        if method.upper() == "POST":
            resp = requests.post(url, headers=headers, timeout=4.0)
        else:
            resp = requests.get(url, headers=headers, timeout=4.0)
        data = resp.json()
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {data}"
        if isinstance(data, dict) and data.get("code") not in (0, "0", None):
            return None, str(data.get("msg") or data)
        return data, None
    except Exception as exc:
        return None, str(exc)


def sync_bingx_real_closed_trades():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return
    with STATE_LOCK:
        if time.time() - STATE.get("last_bingx_sync", 0.0) < 10.0:
            return
        STATE["last_bingx_sync"] = time.time()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (31 * 86400 * 1000)
    payload, err = bingx_signed_request(
        "GET",
        "/openApi/swap/v2/user/income",
        {"startTime": start_ms, "endTime": now_ms, "limit": 200},
    )
    if err or not isinstance(payload, dict):
        return

    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return

    parsed_trades = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        inc_type = str(item.get("incomeType") or "").upper()
        if inc_type not in ("REALIZED_PNL", "PROFIT", "TRADE_PNL"):
            continue
        pnl_val = safe_float(item.get("income"), 0.0)
        if pnl_val == 0.0:
            continue
        t_ms = int(safe_float(item.get("time"), now_ms))
        dt_utc = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
        trade_id = str(item.get("tranId") or item.get("orderId") or f"bx_{t_ms}_{pnl_val}")
        parsed_trades.append({
            "id": trade_id,
            "symbol": canonicalize_symbol_key(item.get("symbol") or "BTCUSDT"),
            "realized_pnl": round(pnl_val, 4),
            "closed_date": dt_utc.strftime("%Y-%m-%d"),
            "closed_month": dt_utc.strftime("%Y-%m"),
            "closed_at": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

    with STATE_LOCK:
        existing_ids = {t.get("id") for t in STATE["closed_trades"]}
        for tr in parsed_trades:
            if tr["id"] not in existing_ids:
                STATE["closed_trades"].append(tr)
                existing_ids.add(tr["id"])
        STATE["session_pnl"] = round(sum(safe_float(t.get("realized_pnl")) for t in STATE["closed_trades"]), 4)


def fetch_bingx_real_positions():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return [], "API Key / Secret BingX non configurate"
    payload, err = bingx_signed_request("GET", "/openApi/swap/v2/user/positions", {})
    if err or not isinstance(payload, dict):
        return [], err or "Errore lettura posizioni BingX"
    raw_pos = payload.get("data") or []
    if not isinstance(raw_pos, list):
        return [], None

    active = []
    for p in raw_pos:
        if not isinstance(p, dict):
            continue
        amt = abs(safe_float(p.get("positionAmt"), 0.0))
        if amt <= 0:
            continue
        side_raw = str(p.get("positionSide") or "").upper()
        if side_raw in ("LONG", "BUY"):
            action = "BUY"
        elif side_raw in ("SHORT", "SELL"):
            action = "SELL"
        else:
            action = "BUY" if safe_float(p.get("positionAmt"), 0.0) > 0 else "SELL"

        active.append({
            "symbol": canonicalize_symbol_key(p.get("symbol") or ""),
            "bx_symbol": p.get("symbol") or "",
            "action": action,
            "entry": safe_float(p.get("avgPrice"), 0.0),
            "size": amt,
            "leverage": f"{int(safe_float(p.get('leverage'), 1))}x",
            "unrealized_pnl": round(safe_float(p.get("unrealizedProfit"), 0.0), 4),
        })
    return active, None


def fetch_bingx_ticker(bx_symbol: str):
    cached = _LIVE_PRICE_CACHE.get(bx_symbol)
    if cached and (time.time() - cached["ts"]) < 2.0:
        return cached["price"]
    try:
        url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker"
        resp = requests.get(url, params={"symbol": bx_symbol}, timeout=2.5)
        if resp.status_code == 200:
            payload = resp.json()
            data = payload.get("data")
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                price = safe_float(data.get("lastPrice"), 0.0)
                if price > 0:
                    _LIVE_PRICE_CACHE[bx_symbol] = {"price": price, "ts": time.time()}
                    return price
    except Exception:
        pass
    return None


def fetch_bingx_klines(bx_symbol: str, interval: str, limit: int = 80):
    tf_map = {"5m": "5m", "15m": "15m", "1h": "1h"}
    bx_int = tf_map.get(interval, "15m")
    try:
        url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/klines"
        resp = requests.get(url, params={"symbol": bx_symbol, "interval": bx_int, "limit": limit}, timeout=3.0)
        if resp.status_code == 200:
            rows = resp.json().get("data") or []
            if isinstance(rows, list) and len(rows) >= 35:
                parsed = []
                for r in rows[-limit:]:
                    if isinstance(r, dict):
                        o = safe_float(r.get("open"))
                        h = safe_float(r.get("high"))
                        l = safe_float(r.get("low"))
                        c = safe_float(r.get("close"))
                        v = safe_float(r.get("volume"), 0.0)
                    elif isinstance(r, (list, tuple)) and len(r) >= 6:
                        o = safe_float(r[1])
                        h = safe_float(r[2])
                        l = safe_float(r[3])
                        c = safe_float(r[4])
                        v = safe_float(r[5], 0.0)
                    else:
                        continue
                    if c > 0 and h >= l > 0:
                        parsed.append({"Open": o or c, "High": h, "Low": l, "Close": c, "Volume": max(v, 0.0)})
                if len(parsed) >= 35:
                    return pd.DataFrame(parsed)
    except Exception:
        pass
    return None


def get_real_market_dataframe(meta: dict, timeframe: str):
    bx_sym = meta["bx"]
    live_price = fetch_bingx_ticker(bx_sym)
    klines_df = fetch_bingx_klines(bx_sym, timeframe, limit=80)

    if klines_df is not None and len(klines_df) >= 35:
        price_out = live_price if (live_price and live_price > 0) else safe_float(klines_df["Close"].iloc[-1], 0.0)
        if price_out > 0:
            klines_df.iloc[-1, klines_df.columns.get_loc("Close")] = price_out
            klines_df.iloc[-1, klines_df.columns.get_loc("High")] = max(klines_df.iloc[-1]["High"], price_out)
            klines_df.iloc[-1, klines_df.columns.get_loc("Low")] = min(klines_df.iloc[-1]["Low"], price_out)
        return klines_df, price_out

    return None, (live_price or 0.0)


def build_profile_config(profile: str, timeframe: str, min_score: float, low_vol_mode: bool = False):
    p = (profile or "balanced").lower()
    if p == "low_vol" or low_vol_mode:
        return {
            "profile": "low_vol",
            "low_vol_mode": True,
            "timeframe": timeframe,
            "min_score": max(65.0, min(min_score, 74.0)),
            "rsi_buy_min": 44.0,
            "rsi_buy_max": 70.0,
            "rsi_sell_min": 30.0,
            "rsi_sell_max": 56.0,
            "sl_m": 0.95,
            "tp_m": 1.65,
            "min_vol_ratio": 0.85,
            "min_hits": 4,
            "min_rrr": 1.25,
            "chop_atr_mult": 0.06,
            "chop_vol_mult": 0.55,
            "cooldown_sec": 45.0,
            "flip_min_cert": 75.0,
            "check_fakeout": False,
        }
    if p == "conservative":
        return {
            "profile": "conservative",
            "low_vol_mode": False,
            "timeframe": timeframe,
            "min_score": max(min_score, 82.0),
            "rsi_buy_min": 50.0,
            "rsi_buy_max": 64.0,
            "rsi_sell_min": 36.0,
            "rsi_sell_max": 50.0,
            "sl_m": 1.3,
            "tp_m": 2.6,
            "min_vol_ratio": 1.15,
            "min_hits": 5,
            "min_rrr": 1.5,
            "chop_atr_mult": 0.12,
            "chop_vol_mult": 0.70,
            "cooldown_sec": DIRECTION_COOLDOWN_SEC,
            "flip_min_cert": 83.3,
            "check_fakeout": True,
        }
    if p == "aggressive":
        return {
            "profile": "aggressive",
            "low_vol_mode": False,
            "timeframe": timeframe,
            "min_score": max(min_score, 76.0),
            "rsi_buy_min": 48.0,
            "rsi_buy_max": 67.0,
            "rsi_sell_min": 33.0,
            "rsi_sell_max": 52.0,
            "sl_m": 1.1,
            "tp_m": 2.2,
            "min_vol_ratio": 1.05,
            "min_hits": 5,
            "min_rrr": 1.5,
            "chop_atr_mult": 0.12,
            "chop_vol_mult": 0.70,
            "cooldown_sec": DIRECTION_COOLDOWN_SEC,
            "flip_min_cert": 83.3,
            "check_fakeout": True,
        }
    return {
        "profile": "balanced",
        "low_vol_mode": False,
        "timeframe": timeframe,
        "min_score": max(min_score, 80.0),
        "rsi_buy_min": 49.0,
        "rsi_buy_max": 65.0,
        "rsi_sell_min": 35.0,
        "rsi_sell_max": 51.0,
        "sl_m": 1.2,
        "tp_m": 2.4,
        "min_vol_ratio": 1.10,
        "min_hits": 5,
        "min_rrr": 1.5,
        "chop_atr_mult": 0.12,
        "chop_vol_mult": 0.70,
        "cooldown_sec": DIRECTION_COOLDOWN_SEC,
        "flip_min_cert": 83.3,
        "check_fakeout": True,
    }


def detect_market_regime_and_advice(df, current_price, category, is_open, cfg):
    close = df["Close"]
    ema20 = safe_float(close.ewm(span=20, adjust=False).mean().iloc[-1], current_price)
    ema50 = safe_float(close.ewm(span=50, adjust=False).mean().iloc[-1], current_price)
    diff_pct = ((ema20 - ema50) / ema50 * 100.0) if ema50 > 0 else 0.0

    if not is_open:
        regime = "MERCATO CHIUSO"
    elif abs(diff_pct) >= 0.45:
        regime = "TREND RIALZISTA" if diff_pct > 0 else "TREND RIBASSISTA"
    else:
        regime = "ASSORBIMENTO / LATERALE"

    return {
        "regime": regime,
        "ema_spread_pct": round(diff_pct, 3),
        "recommended_params": {
            "timeframe": cfg["timeframe"],
            "profile": cfg["profile"],
            "min_score": cfg["min_score"],
        },
    }


def compute_reversal_and_fakeout_metrics(
    df,
    current_price,
    rsi,
    curr_ema12,
    curr_ema26,
    current_macd,
    prev_macd,
    vol_ratio,
    min_vol_ratio,
    low_vol_mode: bool = False,
):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0:
        atr = current_price * 0.005

    last_open = safe_float(df["Open"].iloc[-1], current_price)
    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    body_size = abs(current_price - last_open)
    upper_wick = max(0.0, last_high - max(current_price, last_open))
    lower_wick = max(0.0, min(current_price, last_open) - last_low)

    prev_high_20 = safe_float(high.iloc[-21:-1].max(), last_high) if len(df) >= 22 else last_high
    prev_low_20 = safe_float(low.iloc[-21:-1].min(), last_low) if len(df) >= 22 else last_low

    wick_body_mult = 2.6 if low_vol_mode else 1.8
    wick_atr_mult = 0.55 if low_vol_mode else 0.35

    fake_bull_breakout = (last_high > prev_high_20) and (current_price <= prev_high_20 * 1.0005)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price >= prev_low_20 * 0.9995)
    wick_rejection_top = (upper_wick > body_size * wick_body_mult) and (upper_wick > atr * wick_atr_mult)
    wick_rejection_bottom = (lower_wick > body_size * wick_body_mult) and (lower_wick > atr * wick_atr_mult)

    false_breakout_detected = fake_bull_breakout or fake_bear_breakout or wick_rejection_top or wick_rejection_bottom

    fakeout_flags = []
    if fake_bull_breakout:
        fakeout_flags.append("Falsa rottura massimo 20 candele")
    if fake_bear_breakout:
        fakeout_flags.append("Falsa rottura minimo 20 candele")
    if wick_rejection_top:
        fakeout_flags.append("Rejection ombra superiore")
    if wick_rejection_bottom:
        fakeout_flags.append("Rejection ombra inferiore")
    if vol_ratio < min_vol_ratio:
        fakeout_flags.append(f"Volume contenuto ({vol_ratio}x < {min_vol_ratio}x)")

    dec = 5 if current_price < 10 else 2
    bull_exhaustion = (rsi >= 68.0) or (curr_ema12 < curr_ema26 and current_macd < prev_macd)
    bear_exhaustion = (rsi <= 32.0) or (curr_ema12 > curr_ema26 and current_macd > prev_macd)

    if curr_ema12 >= curr_ema26 and bull_exhaustion:
        rev_dir = "SELL"
        rev_prob = min(88.0, round(32.0 + max(0.0, (rsi - 60.0) * 1.8) + (12.0 if current_macd < prev_macd else 0.0), 1))
        rev_trigger = round(min(current_price - atr * 0.65, curr_ema26 * 0.999), dec)
        rev_reason = "Esaurimento spinta rialzista / possibile inversione SHORT"
    elif curr_ema12 < curr_ema26 and bear_exhaustion:
        rev_dir = "BUY"
        rev_prob = min(88.0, round(32.0 + max(0.0, (40.0 - rsi) * 1.8) + (12.0 if current_macd > prev_macd else 0.0), 1))
        rev_trigger = round(max(current_price + atr * 0.65, curr_ema26 * 1.001), dec)
        rev_reason = "Esaurimento spinta ribassista / possibile inversione LONG"
    else:
        rev_dir = "SELL" if curr_ema12 >= curr_ema26 else "BUY"
        rev_prob = round(min(45.0, max(12.0, abs(50.0 - rsi) * 1.1)), 1)
        rev_trigger = round(curr_ema26, dec)
        rev_reason = "Assorbimento / trend principale in corso"

    return {
        "false_breakout": false_breakout_detected,
        "fakeout_label": "ASSORBIMENTO / WICK" if (false_breakout_detected or len(fakeout_flags) > 0) else "PULITO (ROTTURA VALIDA)",
        "fakeout_reason": " • ".join(fakeout_flags) if fakeout_flags else "Nessuna falsa rottura o anomalia rilevata.",
        "reversal_to": rev_dir,
        "reversal_prob_pct": rev_prob,
        "reversal_trigger": rev_trigger,
        "reversal_summary": f"Se inverte → {rev_dir} ({rev_prob}%) @ {rev_trigger}",
        "reversal_reason": rev_reason,
    }


def compute_math_profit_model(certainty_pct, rrr_val, pos_size, tp_dist, sl_dist, roundtrip_fee, risk_pct, min_rrr=1.5):
    base_p = max(0.38, min(0.80, (certainty_pct / 100.0) * 0.80))
    if rrr_val >= 2.0:
        p_win = round(min(0.82, base_p + 0.04), 3)
    elif rrr_val < min_rrr:
        p_win = round(max(0.32, base_p - 0.05), 3)
    else:
        p_win = round(base_p, 3)

    p_loss = round(1.0 - p_win, 3)
    gross_win = pos_size * tp_dist
    gross_loss = pos_size * sl_dist
    net_win = round(gross_win - roundtrip_fee, 4)
    total_loss = round(gross_loss + roundtrip_fee, 4)
    ev_net = round((p_win * net_win) - (p_loss * total_loss), 4)

    b_ratio = (net_win / total_loss) if total_loss > 0 else 0.0
    kelly_raw = ((b_ratio * p_win) - p_loss) / b_ratio if b_ratio > 0 else 0.0
    safe_kelly_pct = round(max(0.0, min(kelly_raw * 0.5, risk_pct / 100.0)) * 100.0, 2)
    math_approved = (ev_net > 0.0) and (rrr_val >= min_rrr) and (net_win > 0.0)

    return {
        "p_win_pct": round(p_win * 100.0, 1),
        "p_loss_pct": round(p_loss * 100.0, 1),
        "net_win_usd": net_win,
        "total_loss_usd": total_loss,
        "ev_net_usd": ev_net,
        "kelly_pct": safe_kelly_pct,
        "math_approved": math_approved,
        "math_badge": "EV+ APPROVATO" if math_approved else "EV- / R:R BASSO",
    }


def calculate_setup(df, current_price, symbol, balance, risk_pct, cfg, category, is_open):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    regime_info = detect_market_regime_and_advice(df, current_price, category, is_open, cfg)

    low_vol_mode = bool(cfg.get("low_vol_mode", False))
    rsi_buy_min = cfg["rsi_buy_min"]
    rsi_buy_max = cfg["rsi_buy_max"]
    rsi_sell_min = cfg["rsi_sell_min"]
    rsi_sell_max = cfg["rsi_sell_max"]
    sl_m = cfg["sl_m"]
    tp_m = cfg["tp_m"]
    min_vol_ratio = cfg["min_vol_ratio"]
    min_score = cfg["min_score"]
    min_hits = int(cfg.get("min_hits", 5))
    min_rrr = safe_float(cfg.get("min_rrr", 1.5), 1.5)
    chop_atr_mult = safe_float(cfg.get("chop_atr_mult", 0.12), 0.12)
    chop_vol_mult = safe_float(cfg.get("chop_vol_mult", 0.70), 0.70)
    cooldown_sec = safe_float(cfg.get("cooldown_sec", DIRECTION_COOLDOWN_SEC), DIRECTION_COOLDOWN_SEC)
    flip_min_cert = safe_float(cfg.get("flip_min_cert", 83.3), 83.3)
    check_fakeout = bool(cfg.get("check_fakeout", True))
    timeframe = cfg["timeframe"]
    profile = cfg["profile"]

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = safe_float((100 - (100 / (1 + (gain / loss)))).iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    current_macd = safe_float(macd_hist.iloc[-1], 0.0)
    prev_macd = safe_float(macd_hist.iloc[-2], 0.0)
    curr_ema12 = safe_float(ema12.iloc[-1], current_price)
    curr_ema26 = safe_float(ema26.iloc[-1], current_price)
    curr_ema50 = safe_float(ema50.iloc[-1], current_price)

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0:
        atr = current_price * 0.005

    vol_curr = safe_float(volume.iloc[-1], 0.0)
    vol_avg20 = safe_float(volume.rolling(20).mean().iloc[-1], 0.0)
    vol_ratio = round(vol_curr / vol_avg20, 2) if vol_avg20 > 0 else 0.0

    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    prev_high_20 = safe_float(high.iloc[-21:-1].max(), last_high) if len(df) >= 22 else last_high
    prev_low_20 = safe_float(low.iloc[-21:-1].min(), last_low) if len(df) >= 22 else last_low

    fake_bull_breakout = (last_high > prev_high_20) and (current_price < prev_high_20)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price > prev_low_20)
    last_range = abs(last_high - last_low)
    is_chop = (vol_avg20 > 0 and vol_curr < vol_avg20 * chop_vol_mult) or (last_range < atr * chop_atr_mult)
    confirmed_vol = (vol_avg20 <= 0) or (vol_curr >= vol_avg20 * min_vol_ratio)

    bull_factors = [
        curr_ema12 > curr_ema26,
        curr_ema26 >= curr_ema50 * 0.995,
        current_price >= curr_ema50 * 0.988,
        current_macd >= prev_macd,
        rsi_buy_min <= rsi <= rsi_buy_max,
        vol_ratio >= min_vol_ratio,
    ]
    bear_factors = [
        curr_ema12 < curr_ema26,
        curr_ema26 <= curr_ema50 * 1.005,
        current_price <= curr_ema50 * 1.012,
        current_macd <= prev_macd,
        rsi_sell_min <= rsi <= rsi_sell_max,
        vol_ratio >= min_vol_ratio,
    ]

    bull_hits = sum(1 for f in bull_factors if f)
    bear_hits = sum(1 for f in bear_factors if f)

    bull_certainty = round((bull_hits / 6.0) * 100.0, 1)
    bear_certainty = round((bear_hits / 6.0) * 100.0, 1)

    bull_ok = (
        not is_chop
        and confirmed_vol
        and (not check_fakeout or not fake_bull_breakout)
        and (curr_ema12 > curr_ema26)
        and (rsi_buy_min <= rsi <= rsi_buy_max)
        and bull_hits >= min_hits
    )
    bear_ok = (
        not is_chop
        and confirmed_vol
        and (not check_fakeout or not fake_bear_breakout)
        and (curr_ema12 < curr_ema26)
        and (rsi_sell_min <= rsi <= rsi_sell_max)
        and bear_hits >= min_hits
    )

    clean_sym = canonicalize_symbol_key(symbol)
    now_ts = time.time()
    prev_dir_info = _LAST_CONFIRMED_DIRECTION.get(clean_sym)

    candidate_action = "NEUTRAL"
    if bull_ok and not bear_ok:
        candidate_action = "BUY"
    elif bear_ok and not bull_ok:
        candidate_action = "SELL"

    if candidate_action in ["BUY", "SELL"] and prev_dir_info:
        last_side = prev_dir_info.get("action")
        last_ts = safe_float(prev_dir_info.get("ts"), 0.0)
        if last_side in ["BUY", "SELL"] and last_side != candidate_action:
            elapsed = now_ts - last_ts
            opp_cert = bull_certainty if candidate_action == "BUY" else bear_certainty
            if elapsed < cooldown_sec and opp_cert < flip_min_cert:
                candidate_action = "NEUTRAL"

    if candidate_action == "BUY":
        certainty_val = bull_certainty
        confluence_hits = bull_hits
        score = round(min(99.0, max(min_score, 68.0 + bull_hits * 4.5)), 1)
        action, sent, col = "BUY", "LONG CONFERMATO", "#00ff66"
        inv = f"🚀 Preset {timeframe}/{profile} LONG"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "BUY", "ts": now_ts}
    elif candidate_action == "SELL":
        certainty_val = bear_certainty
        confluence_hits = bear_hits
        score = round(min(99.0, max(min_score, 68.0 + bear_hits * 4.5)), 1)
        action, sent, col = "SELL", "SHORT CONFERMATO", "#ff2a4b"
        inv = f"📉 Preset {timeframe}/{profile} SHORT"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "SELL", "ts": now_ts}
    else:
        certainty_val = max(bull_certainty, bear_certainty)
        confluence_hits = max(bull_hits, bear_hits)
        action, score, sent, col = "NEUTRAL", 55.0, "FILTRATO / NO-TRADE", "#ffffff"
        inv = "🛡️ Filtro Assorbimento & Precisione Attivo"

    if certainty_val >= 80.0:
        certainty_level = "ALTA"
    elif certainty_val >= 60.0:
        certainty_level = "MEDIA"
    else:
        certainty_level = "BASSA"

    confluence_label = f"{confluence_hits}/6 Fattori"

    bx_symbol, bx_precision = resolve_bingx_symbol(symbol)
    mkt_decimals, min_sl_pct, min_tp_vs_sl = get_market_calibration_spec(symbol, category, current_price)
    decimals = bx_precision if bx_precision is not None else mkt_decimals
    tick_size = 10 ** (-decimals)

    effective_min_tp_vs_sl = min(min_tp_vs_sl, min_rrr) if low_vol_mode else min_tp_vs_sl
    raw_sl_dist = max(atr * sl_m, current_price * min_sl_pct)
    raw_tp_dist = max(atr * tp_m, raw_sl_dist * effective_min_tp_vs_sl)
    entry = quantize_to_tick(current_price, decimals, tick_size)

    if action == "BUY":
        sl, tp1 = align_tp_sl_to_price("BUY", entry, entry - raw_sl_dist, entry + raw_tp_dist, sl_m, tp_m, decimals)
        target_rialzista = quantize_to_tick(entry + abs(tp1 - entry) * 2.0, decimals, tick_size)
        target_ribassista = sl
    elif action == "SELL":
        sl, tp1 = align_tp_sl_to_price("SELL", entry, entry + raw_sl_dist, entry - raw_tp_dist, sl_m, tp_m, decimals)
        target_rialzista = sl
        target_ribassista = quantize_to_tick(entry - abs(entry - tp1) * 2.0, decimals, tick_size)
    else:
        sl = quantize_to_tick(entry - raw_sl_dist, decimals, tick_size)
        tp1 = quantize_to_tick(entry + raw_tp_dist, decimals, tick_size)
        target_rialzista = tp1
        target_ribassista = sl

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp1 - entry)
    rrr_val = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    tf_min = 5 if timeframe == "5m" else (15 if timeframe == "15m" else 60)
    candle_tf_label = f"Candela {timeframe}"
    recent_movement = max(abs(close.diff().tail(5)).mean(), atr * 0.15, current_price * 0.0001)
    candles_needed = max(1, int(np.ceil(tp_dist / recent_movement)))
    est_min = candles_needed * tf_min
    duration = (
        f"~{est_min} min ({candles_needed} candele)"
        if est_min < 60
        else f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    )
    tp_eta_utc = datetime.fromtimestamp(now_ts + (est_min * 60), tz=timezone.utc).strftime("%H:%M UTC")
    tp_eta_text = f"{duration} • {candle_tf_label} • ETA ~{tp_eta_utc}"

    if balance <= 2.0 and (profile == "aggressive" or low_vol_mode):
        lev_int = 20
    elif symbol in ["BTCUSDT", "ETHUSDT"]:
        lev_int = 8
    elif category == "Criptovalute Globali":
        lev_int = 10
    else:
        lev_int = 5

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = max(abs(entry - sl), tick_size)
    pos_size = compute_order_quantity(bx_symbol, entry, max_risk / risk_unit, max(balance, 2.0), lev_int)
    margin = round((pos_size * entry) / lev_int, 2)

    roi_raw = round((tp_dist / entry) * lev_int * 100, 1) if entry > 0 else 0.0
    roi_pct = f"+{roi_raw}%" if action == "BUY" else f"-{roi_raw}%" if action == "SELL" else "0.0%"

    notional = pos_size * entry
    roundtrip_fee = round(notional * 0.001, 4)
    expected_gross_profit = round(pos_size * tp_dist, 4)
    expected_net_profit = round(expected_gross_profit - roundtrip_fee, 4)

    anti_fake = compute_reversal_and_fakeout_metrics(
        df=df,
        current_price=current_price,
        rsi=rsi,
        curr_ema12=curr_ema12,
        curr_ema26=curr_ema26,
        current_macd=current_macd,
        prev_macd=prev_macd,
        vol_ratio=vol_ratio,
        min_vol_ratio=min_vol_ratio,
        low_vol_mode=low_vol_mode,
    )

    math_model = compute_math_profit_model(
        certainty_pct=certainty_val,
        rrr_val=rrr_val,
        pos_size=pos_size,
        tp_dist=tp_dist,
        sl_dist=sl_dist,
        roundtrip_fee=roundtrip_fee,
        risk_pct=risk_pct,
        min_rrr=min_rrr,
    )

    block_by_fakeout = check_fakeout and anti_fake["false_breakout"]
    if action in ["BUY", "SELL"] and (block_by_fakeout or not math_model["math_approved"]):
        action = "NEUTRAL"
        score = 55.0
        sent = "BLOCCATO ANTI-FAKEOUT / EV"
        col = "#ffd166"
        inv = "🛡️ Blocco Matematico / EV"
        filter_reason = (
            f"Bloccato da filtro EV/Fakeout: {anti_fake['fakeout_reason']} | "
            f"EV netto ${math_model['ev_net_usd']} (R:R 1:{rrr_val} vs min 1:{min_rrr})."
        )
        trigger_condition = f"Attesa EV netto > 0 e R:R >= {min_rrr}."
    elif not is_open:
        filter_reason = f"Borsa {category} chiusa — in attesa riapertura sessione."
        trigger_condition = f"Attivazione alla riapertura con >= {min_hits}/6 fattori e Score >= {min_score}%."
    elif (
        prev_dir_info
        and (now_ts - safe_float(prev_dir_info.get("ts"), 0.0)) < cooldown_sec
        and prev_dir_info.get("action") in ["BUY", "SELL"]
        and action == "NEUTRAL"
    ):
        rem_cd = int(max(1, cooldown_sec - (now_ts - safe_float(prev_dir_info.get("ts"), 0.0))))
        filter_reason = (
            f"Protezione anti-inversione ({prev_dir_info.get('action')} recente): "
            f"attesa {rem_cd}s o certezza >={flip_min_cert}% per cambiare direzione."
        )
        trigger_condition = f"Cambio direzione consentito dopo {rem_cd}s oppure con conferma >= {min_hits}/6 fattori."
    elif action == "NEUTRAL":
        filter_reason = (
            f"In attesa di {min_hits}/6 fattori (attuale: {confluence_hits}/6, Certezza {certainty_val}%, "
            f"RSI {round(rsi, 1)}, Vol {vol_ratio}x vs {min_vol_ratio}x, chop={is_chop})."
        )
        trigger_condition = (
            f"LONG con EMA12>EMA26, RSI [{rsi_buy_min}-{rsi_buy_max}], Vol>={min_vol_ratio}x e >={min_hits}/6 fattori; "
            f"SHORT con EMA12<EMA26, RSI [{rsi_sell_min}-{rsi_sell_max}], Vol>={min_vol_ratio}x e >={min_hits}/6 fattori."
        )
    else:
        filter_reason = (
            f"Preso in carico: {action} ({profile}) con certezza {certainty_val}% ({certainty_level}, {confluence_label}) "
            f"e EV netto +${math_model['ev_net_usd']}."
        )
        trigger_condition = f"Segnale {action} confermato ({certainty_val}% certezza) con SL {sl_m}x ATR e TP1 {tp_m}x ATR."

    vol_pct = round((atr / entry) * 100, 2) if entry > 0 else 0.0
    vol_regime = "ALTA VOLATILITÀ" if vol_pct >= 1.2 else ("ASSORBIMENTO / BASSA VOL" if vol_pct < 0.35 else "MODERATA")
    trend_state = (
        "BULLISH EMA/MACD"
        if (curr_ema12 > curr_ema26 and current_macd >= 0)
        else ("BEARISH EMA/MACD" if (curr_ema12 < curr_ema26 and current_macd <= 0) else "ASSORBIMENTO / NO-FLIP")
    )

    return {
        "action": action,
        "certainty_pct": certainty_val,
        "certainty_level": certainty_level,
        "certainty_text": f"{certainty_val}% ({certainty_level})",
        "filter_reason": filter_reason,
        "trigger_condition": trigger_condition,
        "trend_state": trend_state,
        "bingx_symbol": bx_symbol,
        "ai_score": f"{score}%",
        "sentiment": sent,
        "sentiment_color": col,
        "inversion": inv,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "roi_pct": roi_pct,
        "target_rialzista": target_rialzista,
        "target_ribassista": target_ribassista,
        "duration": duration,
        "candle_tf_label": candle_tf_label,
        "tp_candles_est": candles_needed,
        "tp_eta_utc": tp_eta_utc,
        "tp_eta_text": tp_eta_text,
        "rrr": rrr_val,
        "rsi": round(rsi, 1),
        "vol_ratio": vol_ratio,
        "confluence": confluence_label,
        "vol_regime": f"{vol_regime} ({vol_pct}%)",
        "size": pos_size,
        "leverage": f"Isolata {lev_int}x",
        "margin": margin,
        "decimals": decimals,
        "sl_dist": round(sl_dist, decimals + 2),
        "tp_dist": round(tp_dist, decimals + 2),
        "roundtrip_fee": roundtrip_fee,
        "expected_net": expected_net_profit,
        "false_breakout": block_by_fakeout,
        "fakeout_label": anti_fake["fakeout_label"],
        "fakeout_reason": anti_fake["fakeout_reason"],
        "reversal_to": anti_fake["reversal_to"],
        "reversal_prob_pct": anti_fake["reversal_prob_pct"],
        "reversal_trigger": anti_fake["reversal_trigger"],
        "reversal_summary": anti_fake["reversal_summary"],
        "reversal_reason": anti_fake["reversal_reason"],
        "p_win_pct": math_model["p_win_pct"],
        "net_win_usd": math_model["net_win_usd"],
        "total_loss_usd": math_model["total_loss_usd"],
        "ev_net_usd": math_model["ev_net_usd"],
        "kelly_pct": math_model["kelly_pct"],
        "math_approved": math_model["math_approved"],
        "math_badge": math_model["math_badge"],
        "advisor_status": f"SETUP ATTIVO ({certainty_val}%)" if action in ["BUY", "SELL"] else "FILTRATO / IN ATTESA",
        "advisor_color": "#00ff66" if action in ["BUY", "SELL"] else "#8a99ad",
        "advisor_msg": f"BingX Live: {bx_symbol} | Certezza {certainty_val}% ({certainty_level}) | EV ${math_model['ev_net_usd']}",
        "applied_params": cfg,
        "market_regime": regime_info,
        "recommended_params": regime_info["recommended_params"],
    }


def build_performance_payload():
    sync_bingx_real_closed_trades()
    now_utc = datetime.now(timezone.utc)
    today_key = now_utc.strftime("%Y-%m-%d")
    month_key = now_utc.strftime("%Y-%m")

    with STATE_LOCK:
        closed = list(STATE.get("closed_trades", []))

    def agg(predicate):
        items = [t for t in closed if predicate(t)]
        wins = [t for t in items if safe_float(t.get("realized_pnl")) > 0]
        losses = [t for t in items if safe_float(t.get("realized_pnl")) < 0]
        total = len(items)
        w_cnt = len(wins)
        l_cnt = len(losses)
        win_pct = round((w_cnt / total) * 100.0, 0) if total > 0 else 0.0
        loss_pct = round((l_cnt / total) * 100.0, 0) if total > 0 else 0.0
        won_amt = round(sum(safe_float(t.get("realized_pnl")) for t in wins), 2)
        lost_amt = round(sum(abs(safe_float(t.get("realized_pnl"))) for t in losses), 2)
        net_pnl = round(sum(safe_float(t.get("realized_pnl")) for t in items), 2)
        return {
            "total_trades": total,
            "wins": w_cnt,
            "losses": l_cnt,
            "win_pct": win_pct,
            "loss_pct": loss_pct,
            "won_amount": won_amt,
            "lost_amount": lost_amt,
            "net_pnl": net_pnl,
        }

    daily = agg(lambda t: str(t.get("closed_date", "")) == today_key)
    monthly = agg(lambda t: str(t.get("closed_month", "")) == month_key)
    daily["date"] = today_key
    monthly["month"] = month_key
    return {
        "daily": daily,
        "monthly": monthly,
        "api_configured": bool(BINGX_API_KEY and BINGX_API_SECRET),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/live-ticker", methods=["GET"])
def api_live_ticker():
    sym = canonicalize_symbol_key(request.args.get("symbol", "BTCUSDT"))
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0]})
    live_p = fetch_bingx_ticker(meta["bx"])
    if not live_p or live_p <= 0:
        return jsonify({"error": f"Prezzo live BingX non disponibile per {meta['bx']}"}), 503
    return jsonify({
        "symbol": sym,
        "bingx_symbol": meta["bx"],
        "price": live_p,
        "live": True,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


@app.route("/api/signals", methods=["GET"])
def get_signals():
    profile = request.args.get("profile", "balanced")
    low_vol_mode = str(request.args.get("low_vol_mode", "0")).lower() in ("1", "true", "yes", "on")
    if profile.lower() == "low_vol":
        low_vol_mode = True
    timeframe = request.args.get("timeframe", "15m")
    if timeframe not in ("5m", "15m", "1h"):
        timeframe = "15m"
    balance = max(1.0, safe_float(request.args.get("balance"), 2.0))
    risk_pct = min(20.0, max(0.25, safe_float(request.args.get("risk"), 1.0)))
    min_score = min(99.0, max(50.0, safe_float(request.args.get("min_score"), 80.0)))
    market_filter = request.args.get("market", "ALL")
    symbol_filter = request.args.get("symbol", "ALL")

    cfg = build_profile_config(profile, timeframe, min_score, low_vol_mode=low_vol_mode)
    sessions = get_session_statuses()

    signals_out = []
    monitoring_rows = []
    market_diagnostics = {}

    for category, assets in MARKET_CATALOG.items():
        sess = sessions.get(category, {"status": "APERTO (Live)", "is_open": True, "hours": "24/7"})
        is_open = bool(sess.get("is_open", True))
        cat_signals = 0
        cat_scanned = 0
        cat_filtered = 0
        cat_feed_errors = 0
        top_reasons = []

        for meta in assets:
            sym = meta["symbol"]
            if market_filter != "ALL" and category != market_filter:
                continue
            if symbol_filter != "ALL" and sym != symbol_filter:
                continue

            cat_scanned += 1
            df, curr_price = get_real_market_dataframe(meta, timeframe)
            if df is None or curr_price <= 0:
                cat_feed_errors += 1
                err_reason = f"Dato live BingX non disponibile per {meta['bx']} ({timeframe})."
                if len(top_reasons) < 3:
                    top_reasons.append(f"{sym}: {err_reason}")
                monitoring_rows.append({
                    "category": category,
                    "symbol": sym,
                    "bingx_symbol": meta["bx"],
                    "name": meta["name"],
                    "session_status": sess["status"],
                    "is_open": is_open,
                    "price": None,
                    "entry": None,
                    "sl": None,
                    "tp1": None,
                    "action": "NEUTRAL",
                    "taken": False,
                    "ai_score": "0.0%",
                    "certainty_pct": 0.0,
                    "certainty_level": "BASSA",
                    "certainty_text": "0.0% (BASSA)",
                    "filter_reason": err_reason,
                    "why_not_taken": err_reason,
                    "when_taken": "Richiede feed live BingX attivo",
                    "params": {"profile": cfg["profile"], "low_vol_mode": low_vol_mode, "timeframe": timeframe, "balance": balance, "risk": risk_pct, "min_score": min_score},
                })
                continue

            res = calculate_setup(df, curr_price, sym, balance, risk_pct, cfg, category, is_open)
            taken = res["action"] in ("BUY", "SELL") and is_open

            row_payload = {
                "category": category,
                "symbol": sym,
                "bingx_symbol": res["bingx_symbol"],
                "name": meta["name"],
                "session_status": sess["status"],
                "is_open": is_open,
                "price": curr_price,
                "entry": res["entry"],
                "sl": res["sl"],
                "tp1": res["tp1"],
                "target_rialzista": res["target_rialzista"],
                "target_ribassista": res["target_ribassista"],
                "rrr": res["rrr"],
                "roi_pct": res["roi_pct"],
                "duration": res["duration"],
                "candle_tf_label": res["candle_tf_label"],
                "tp_candles_est": res["tp_candles_est"],
                "tp_eta_utc": res["tp_eta_utc"],
                "tp_eta_text": res["tp_eta_text"],
                "confluence": res["confluence"],
                "size": res["size"],
                "leverage": res["leverage"],
                "margin": res["margin"],
                "roundtrip_fee": res["roundtrip_fee"],
                "expected_net": res["expected_net"],
                "action": res["action"] if is_open else "NEUTRAL",
                "taken": taken,
                "ai_score": res["ai_score"],
                "certainty_pct": res["certainty_pct"],
                "certainty_level": res["certainty_level"],
                "certainty_text": res["certainty_text"],
                "rsi": res["rsi"],
                "vol_ratio": res["vol_ratio"],
                "vol_regime": res["vol_regime"],
                "trend_state": res["trend_state"],
                "false_breakout": res["false_breakout"],
                "fakeout_label": res["fakeout_label"],
                "fakeout_reason": res["fakeout_reason"],
                "reversal_to": res["reversal_to"],
                "reversal_prob_pct": res["reversal_prob_pct"],
                "reversal_trigger": res["reversal_trigger"],
                "reversal_summary": res["reversal_summary"],
                "reversal_reason": res["reversal_reason"],
                "p_win_pct": res["p_win_pct"],
                "net_win_usd": res["net_win_usd"],
                "total_loss_usd": res["total_loss_usd"],
                "ev_net_usd": res["ev_net_usd"],
                "kelly_pct": res["kelly_pct"],
                "math_approved": res["math_approved"],
                "math_badge": res["math_badge"],
                "filter_reason": res["filter_reason"],
                "why_not_taken": res["filter_reason"],
                "when_taken": res["trigger_condition"],
                "params": {"profile": cfg["profile"], "low_vol_mode": low_vol_mode, "timeframe": timeframe, "balance": balance, "risk": risk_pct, "min_score": min_score},
            }
            monitoring_rows.append(row_payload)

            if taken:
                cat_signals += 1
                signals_out.append({
                    **row_payload,
                    "id": f"{sym}_{res['action']}_{timeframe}",
                    "sentiment": res["sentiment"],
                    "sentiment_color": res["sentiment_color"],
                    "applied_params": cfg,
                })
            else:
                cat_filtered += 1
                if len(top_reasons) < 3:
                    top_reasons.append(f"{sym}: {res['filter_reason']}")

        if not is_open:
            diag_reason = f"Mercato {category} attualmente chiuso ({sess['hours']})."
        elif cat_scanned == 0:
            diag_reason = f"Nessun asset selezionato per {category} con il filtro corrente."
        elif cat_signals == 0:
            details_str = " | ".join(top_reasons)
            diag_reason = (
                f"Analizzati {cat_scanned} asset reali su {category} ({cfg['profile']}): 0 segnali attivi, "
                f"{cat_filtered} filtrati (feed non disponibile: {cat_feed_errors}). {details_str}"
            )
        else:
            diag_reason = f"{cat_signals} segnali reali attivi su {category} ({cat_filtered} filtrati, profilo {cfg['profile']})."

        market_diagnostics[category] = {
            "category": category,
            "session_status": sess["status"],
            "is_open": is_open,
            "scanned": cat_scanned,
            "signals_count": cat_signals,
            "filtered_count": cat_filtered,
            "feed_errors": cat_feed_errors,
            "filter_reason": diag_reason,
            "reason": diag_reason,
            "sample_reasons": top_reasons,
        }

    live_positions, _ = fetch_bingx_real_positions()

    with STATE_LOCK:
        STATE["scans_count"] += 1
        scans_val = STATE["scans_count"]
        session_pnl_val = STATE["session_pnl"]
        STATE["telemetry_logs"] = ([{
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "status": f"LIVE SCAN {timeframe} ({cfg['profile']})",
            "signals_found": len(signals_out),
            "scanned": len(monitoring_rows),
        }] + STATE["telemetry_logs"])[:40]
        logs_copy = list(STATE["telemetry_logs"])

    return jsonify({
        "sessions": sessions,
        "signals": signals_out,
        "active_params": cfg,
        "market_diagnostics": market_diagnostics,
        "market_monitoring_table": monitoring_rows,
        "performance": build_performance_payload(),
        "telemetry": {
            "scans_count": scans_val,
            "active_orders_count": len(live_positions),
            "session_pnl": session_pnl_val,
            "telemetry_logs": logs_copy,
        },
    })


@app.route("/api/analytics/performance", methods=["GET"])
def api_analytics_performance():
    return jsonify(build_performance_payload())


@app.route("/api/autotrade/execute-manual", methods=["POST"])
def api_execute_manual():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return jsonify({
            "success": False,
            "error": "Modalità reale attiva: imposta BINGX_API_KEY e BINGX_API_SECRET per inviare ordini reali su BingX.",
        }), 400

    body = request.get_json(silent=True) or {}
    sym = canonicalize_symbol_key(body.get("symbol", "BTCUSDT"))
    bx_symbol, _ = resolve_bingx_symbol(sym)
    action = (body.get("action") or "BUY").upper()
    if action not in ("BUY", "SELL"):
        action = "BUY"

    qty = max(0.0001, safe_float(body.get("size"), 0.001))
    side = "BID" if action == "BUY" else "ASK"
    pos_side = "LONG" if action == "BUY" else "SHORT"

    order_data, err = bingx_signed_request(
        "POST",
        "/openApi/swap/v2/trade/order",
        {
            "symbol": bx_symbol,
            "side": side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": qty,
        },
    )
    if err:
        return jsonify({"success": False, "error": f"Errore BingX ordine reale: {err}"}), 400

    return jsonify({"success": True, "mode": "BINGX REAL LIVE", "order": order_data})


@app.route("/api/autotrade/status", methods=["GET"])
def api_autotrade_status():
    positions, pos_err = fetch_bingx_real_positions()
    perf = build_performance_payload()
    with STATE_LOCK:
        closed_copy = list(STATE["closed_trades"])
    return jsonify({
        "auto_trade": STATE["auto_trade"],
        "mode": "BINGX REAL LIVE",
        "api_configured": bool(BINGX_API_KEY and BINGX_API_SECRET),
        "positions": positions,
        "positions_error": pos_err,
        "history": closed_copy,
        "performance": perf,
    })


@app.route("/api/telemetry", methods=["GET"])
def api_telemetry():
    sync_bingx_real_closed_trades()
    positions, _ = fetch_bingx_real_positions()
    with STATE_LOCK:
        return jsonify({
            "scans_count": STATE["scans_count"],
            "active_orders_count": len(positions),
            "session_pnl": STATE["session_pnl"],
            "telemetry_logs": list(STATE["telemetry_logs"]),
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
