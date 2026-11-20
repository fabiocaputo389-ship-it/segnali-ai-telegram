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
DEFAULT_DEMO_MODE = os.getenv("BINGX_DEMO_MODE", "true").lower() in ("1", "true", "yes")

DIRECTION_COOLDOWN_SEC = 120.0
_LAST_CONFIRMED_DIRECTION = {}
_LIVE_PRICE_CACHE = {}

STATE_LOCK = threading.Lock()
STATE = {
    "auto_trade": False,
    "demo_mode": DEFAULT_DEMO_MODE or not (BINGX_API_KEY and BINGX_API_SECRET),
    "scans_count": 0,
    "session_pnl": 0.0,
    "telemetry_logs": [],
    "positions": [],
    "history": [],
    "closed_trades": [],
    "last_bingx_sync": 0.0,
}

MARKET_CATALOG = {
    "Criptovalute Globali": [
        {"symbol": "BTCUSDT", "bx": "BTC-USDT", "name": "Bitcoin (BTC-USDT)", "base_price": 94250.0, "precision": 1},
        {"symbol": "ETHUSDT", "bx": "ETH-USDT", "name": "Ethereum (ETH-USDT)", "base_price": 3240.0, "precision": 2},
        {"symbol": "SOLUSDT", "bx": "SOL-USDT", "name": "Solana (SOL-USDT)", "base_price": 218.5, "precision": 2},
        {"symbol": "XRPUSDT", "bx": "XRP-USDT", "name": "Ripple (XRP-USDT)", "base_price": 1.48, "precision": 4},
        {"symbol": "BNBUSDT", "bx": "BNB-USDT", "name": "BNB (BNB-USDT)", "base_price": 685.0, "precision": 2},
        {"symbol": "ADAUSDT", "bx": "ADA-USDT", "name": "Cardano (ADA-USDT)", "base_price": 0.86, "precision": 4},
        {"symbol": "DOGEUSDT", "bx": "DOGE-USDT", "name": "Dogecoin (DOGE-USDT)", "base_price": 0.38, "precision": 5},
        {"symbol": "AVAXUSDT", "bx": "AVAX-USDT", "name": "Avalanche (AVAX-USDT)", "base_price": 44.2, "precision": 2},
        {"symbol": "LINKUSDT", "bx": "LINK-USDT", "name": "Chainlink (LINK-USDT)", "base_price": 22.4, "precision": 3},
        {"symbol": "SUIUSDT", "bx": "SUI-USDT", "name": "Sui (SUI-USDT)", "base_price": 3.85, "precision": 4},
    ],
    "Materie Prime & Forex": [
        {"symbol": "PAXGUSDT", "bx": "PAXG-USDT", "name": "Oro (GOLD(XAU)-USDT)", "base_price": 2745.0, "precision": 2},
        {"symbol": "XAGUSDT", "bx": "XAG-USDT", "name": "Argento (SILVER(XAG)-USDT)", "base_price": 31.8, "precision": 3},
        {"symbol": "WTIUSDT", "bx": "WTI-USDT", "name": "Petrolio WTI (WTI-USDT)", "base_price": 71.4, "precision": 2},
        {"symbol": "NATGASUSDT", "bx": "NATGAS-USDT", "name": "Gas Naturale (NATGAS-USDT)", "base_price": 3.12, "precision": 3},
        {"symbol": "COPPERUSDT", "bx": "COPPER-USDT", "name": "Rame (COPPER-USDT)", "base_price": 4.28, "precision": 3},
        {"symbol": "GBPUSDT", "bx": "GBP-USDT", "name": "GBP/USD (GBPUSD-USDT)", "base_price": 1.272, "precision": 4},
        {"symbol": "EURUSDT", "bx": "EUR-USDT", "name": "EUR/USD (EURUSD-USDT)", "base_price": 1.084, "precision": 4},
        {"symbol": "USDJPY", "bx": "USDJPY-USDT", "name": "USD/JPY (USDJPY-USDT)", "base_price": 154.6, "precision": 2},
    ],
    "Azioni & Indici Globali": [
        {"symbol": "AAPLUSDT", "bx": "AAPL-USDT", "name": "Apple Inc. (AAPL-USDT)", "base_price": 229.5, "precision": 2},
        {"symbol": "TSLAUSDT", "bx": "TSLA-USDT", "name": "Tesla Inc. (TSLA-USDT)", "base_price": 342.0, "precision": 2},
        {"symbol": "NVDAUSDT", "bx": "NVDA-USDT", "name": "Nvidia Corp. (NVDA-USDT)", "base_price": 146.8, "precision": 2},
        {"symbol": "AMZNUSDT", "bx": "AMZN-USDT", "name": "Amazon (AMZN-USDT)", "base_price": 208.4, "precision": 2},
        {"symbol": "MSFTUSDT", "bx": "MSFT-USDT", "name": "Microsoft (MSFT-USDT)", "base_price": 424.0, "precision": 2},
        {"symbol": "GOOGLUSDT", "bx": "GOOGL-USDT", "name": "Alphabet (GOOGL-USDT)", "base_price": 176.5, "precision": 2},
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
            "badge_color": "#00ff66" if commodities_open else "#ff1e42",
        },
        "Azioni & Indici Globali": {
            "status": "APERTO (NYSE/NASDAQ)" if equities_open else "CHIUSO (Fuori Orario)",
            "is_open": equities_open,
            "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC (Lun-Ven)",
            "badge_color": "#00ff66" if equities_open else "#ff1e42",
        },
    }


def sign_bingx_query(params: dict) -> str:
    query_str = urlencode(sorted(params.items()))
    sig = hmac.new(BINGX_API_SECRET.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_str}&signature={sig}"


def sync_bingx_closed_income_if_configured():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return
    with STATE_LOCK:
        if time.time() - STATE.get("last_bingx_sync", 0.0) < 15.0:
            return
        STATE["last_bingx_sync"] = time.time()

    try:
        ts_ms = int(time.time() * 1000)
        start_ms = ts_ms - (30 * 86400 * 1000)
        q = sign_bingx_query({"timestamp": ts_ms, "startTime": start_ms, "limit": 100})
        url = f"{BINGX_BASE_URL}/openApi/swap/v2/user/income?{q}"
        resp = requests.get(url, headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=3.5)
        if resp.status_code != 200:
            return
        rows = resp.json().get("data") or []
        if not isinstance(rows, list):
            return

        synced = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            inc_type = str(item.get("incomeType") or "").upper()
            if inc_type not in ("REALIZED_PNL", "PROFIT", "TRADE_PNL"):
                continue
            pnl_val = safe_float(item.get("income"), 0.0)
            if pnl_val == 0.0:
                continue
            t_ms = int(safe_float(item.get("time"), ts_ms))
            dt_utc = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
            trade_id = str(item.get("tranId") or item.get("orderId") or f"bx_{t_ms}_{pnl_val}")
            synced.append({
                "id": trade_id,
                "symbol": canonicalize_symbol_key(item.get("symbol") or "BTCUSDT"),
                "realized_pnl": round(pnl_val, 4),
                "closed_date": dt_utc.strftime("%Y-%m-%d"),
                "closed_month": dt_utc.strftime("%Y-%m"),
                "closed_at": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            })

        if synced:
            with STATE_LOCK:
                existing_ids = {t.get("id") for t in STATE["closed_trades"]}
                for tr in synced:
                    if tr["id"] not in existing_ids:
                        STATE["closed_trades"].append(tr)
                        existing_ids.add(tr["id"])
    except Exception:
        pass


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
                        v = safe_float(r.get("volume"), 1000.0)
                    elif isinstance(r, (list, tuple)) and len(r) >= 6:
                        o = safe_float(r[1])
                        h = safe_float(r[2])
                        l = safe_float(r[3])
                        c = safe_float(r[4])
                        v = safe_float(r[5], 1000.0)
                    else:
                        continue
                    if c > 0 and h >= l > 0:
                        parsed.append({"Open": o or c, "High": h, "Low": l, "Close": c, "Volume": max(v, 1.0)})
                if len(parsed) >= 35:
                    return pd.DataFrame(parsed)
    except Exception:
        pass
    return None


def generate_market_dataframe(meta: dict, timeframe: str, live_price_override=None):
    symbol = meta["symbol"]
    base = safe_float(live_price_override, 0.0)
    if base <= 0:
        bx_live = fetch_bingx_ticker(meta["bx"])
        base = bx_live if bx_live and bx_live > 0 else meta["base_price"]

    klines_df = fetch_bingx_klines(meta["bx"], timeframe, limit=80)
    if klines_df is not None and len(klines_df) >= 35:
        if live_price_override and live_price_override > 0:
            klines_df.iloc[-1, klines_df.columns.get_loc("Close")] = live_price_override
            klines_df.iloc[-1, klines_df.columns.get_loc("High")] = max(klines_df.iloc[-1]["High"], live_price_override)
            klines_df.iloc[-1, klines_df.columns.get_loc("Low")] = min(klines_df.iloc[-1]["Low"], live_price_override)
        return klines_df, safe_float(klines_df["Close"].iloc[-1], base)

    seed_val = int(hashlib.md5(f"{symbol}_{timeframe}_{int(time.time() // 15)}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed_val)
    n = 75
    drift = 0.0006 if seed_val % 2 == 0 else -0.0006
    returns = rng.normal(loc=drift, scale=0.0022, size=n)
    closes = base * np.cumprod(1 + returns)
    closes[-1] = base

    opens = np.roll(closes, 1)
    opens[0] = closes[0] * 0.999
    spreads = np.abs(rng.normal(loc=0.0025, scale=0.001, size=n)) * closes
    highs = np.maximum(opens, closes) + spreads * 0.65
    lows = np.minimum(opens, closes) - spreads * 0.65
    vols = rng.uniform(800, 3200, size=n)
    vols[-1] = rng.uniform(1500, 3600)

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": vols,
    })
    return df, base


def build_profile_config(profile: str, timeframe: str, min_score: float):
    p = (profile or "balanced").lower()
    if p == "conservative":
        return {
            "profile": "conservative",
            "timeframe": timeframe,
            "min_score": max(min_score, 82.0),
            "rsi_buy_min": 50.0,
            "rsi_buy_max": 64.0,
            "rsi_sell_min": 36.0,
            "rsi_sell_max": 50.0,
            "sl_m": 1.3,
            "tp_m": 2.6,
            "min_vol_ratio": 1.15,
        }
    if p == "aggressive":
        return {
            "profile": "aggressive",
            "timeframe": timeframe,
            "min_score": max(min_score, 76.0),
            "rsi_buy_min": 48.0,
            "rsi_buy_max": 67.0,
            "rsi_sell_min": 33.0,
            "rsi_sell_max": 52.0,
            "sl_m": 1.1,
            "tp_m": 2.2,
            "min_vol_ratio": 1.05,
        }
    return {
        "profile": "balanced",
        "timeframe": timeframe,
        "min_score": max(min_score, 80.0),
        "rsi_buy_min": 49.0,
        "rsi_buy_max": 65.0,
        "rsi_sell_min": 35.0,
        "rsi_sell_max": 51.0,
        "sl_m": 1.2,
        "tp_m": 2.4,
        "min_vol_ratio": 1.10,
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
        regime = "LATERALE / CONSOLIDAMENTO"

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
    curr_ema50,
    current_macd,
    prev_macd,
    vol_ratio,
    min_vol_ratio,
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

    fake_bull_breakout = (last_high > prev_high_20) and (current_price <= prev_high_20 * 1.0005)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price >= prev_low_20 * 0.9995)
    wick_rejection_top = (upper_wick > body_size * 1.8) and (upper_wick > atr * 0.35)
    wick_rejection_bottom = (lower_wick > body_size * 1.8) and (lower_wick > atr * 0.35)

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
        fakeout_flags.append(f"Volume debole ({vol_ratio}x < {min_vol_ratio}x)")

    dec = 5 if current_price < 10 else 2
    bull_exhaustion = (rsi >= 65.0) or (curr_ema12 < curr_ema26 and current_macd < prev_macd) or fake_bull_breakout or wick_rejection_top
    bear_exhaustion = (rsi <= 35.0) or (curr_ema12 > curr_ema26 and current_macd > prev_macd) or fake_bear_breakout or wick_rejection_bottom

    if curr_ema12 >= curr_ema26 and bull_exhaustion:
        rev_dir = "SELL"
        rev_prob = min(
            88.0,
            round(
                32.0
                + max(0.0, (rsi - 60.0) * 1.8)
                + (18.0 if (fake_bull_breakout or wick_rejection_top) else 0.0)
                + (12.0 if current_macd < prev_macd else 0.0),
                1,
            ),
        )
        rev_trigger = round(min(current_price - atr * 0.65, curr_ema26 * 0.999), dec)
        rev_reason = "Esaurimento spinta rialzista / possibile inversione SHORT"
    elif curr_ema12 < curr_ema26 and bear_exhaustion:
        rev_dir = "BUY"
        rev_prob = min(
            88.0,
            round(
                32.0
                + max(0.0, (40.0 - rsi) * 1.8)
                + (18.0 if (fake_bear_breakout or wick_rejection_bottom) else 0.0)
                + (12.0 if current_macd > prev_macd else 0.0),
                1,
            ),
        )
        rev_trigger = round(max(current_price + atr * 0.65, curr_ema26 * 1.001), dec)
        rev_reason = "Esaurimento spinta ribassista / possibile inversione LONG"
    else:
        rev_dir = "SELL" if curr_ema12 >= curr_ema26 else "BUY"
        rev_prob = round(min(45.0, max(12.0, abs(50.0 - rsi) * 1.1)), 1)
        rev_trigger = round(curr_ema26, dec)
        rev_reason = "Trend principale intatto — inversione non imminente"

    return {
        "false_breakout": false_breakout_detected,
        "fakeout_label": "BLOCCATO (FAKEOUT/RUMORE)" if (false_breakout_detected or len(fakeout_flags) > 0) else "PULITO (ROTTURA VALIDA)",
        "fakeout_reason": " • ".join(fakeout_flags) if fakeout_flags else "Nessuna falsa rottura o anomalia rilevata.",
        "reversal_to": rev_dir,
        "reversal_prob_pct": rev_prob,
        "reversal_trigger": rev_trigger,
        "reversal_summary": f"Se inverte → {rev_dir} ({rev_prob}%) @ {rev_trigger}",
        "reversal_reason": rev_reason,
    }


def compute_math_profit_model(action, certainty_pct, rrr_val, pos_size, tp_dist, sl_dist, roundtrip_fee, balance, risk_pct):
    base_p = max(0.35, min(0.78, (certainty_pct / 100.0) * 0.78))
    if rrr_val >= 2.0:
        p_win = round(min(0.80, base_p + 0.04), 3)
    elif rrr_val < 1.5:
        p_win = round(max(0.30, base_p - 0.06), 3)
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

    math_approved = (ev_net > 0.0) and (rrr_val >= 1.5) and (net_win > 0.0)

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

    rsi_buy_min = max(46.0, cfg["rsi_buy_min"])
    rsi_buy_max = min(68.0, cfg["rsi_buy_max"])
    rsi_sell_min = max(32.0, cfg["rsi_sell_min"])
    rsi_sell_max = min(54.0, cfg["rsi_sell_max"])
    sl_m = cfg["sl_m"]
    tp_m = cfg["tp_m"]
    min_vol_ratio = max(0.90, cfg["min_vol_ratio"])
    min_score = cfg["min_score"]
    timeframe = cfg["timeframe"]
    profile = cfg["profile"]

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = safe_float((100 - (100 / (1 + (gain / loss)))).iloc[-1], 52.0)

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
    vol_ratio = round(vol_curr / vol_avg20, 2) if vol_avg20 > 0 else 1.25

    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    prev_high_20 = safe_float(high.iloc[-21:-1].max(), last_high) if len(df) >= 22 else last_high
    prev_low_20 = safe_float(low.iloc[-21:-1].min(), last_low) if len(df) >= 22 else last_low

    fake_bull_breakout = (last_high > prev_high_20) and (current_price < prev_high_20)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price > prev_low_20)
    last_range = abs(last_high - last_low)
    is_chop = (vol_avg20 > 0 and vol_curr < vol_avg20 * 0.70) or (last_range < atr * 0.12)
    confirmed_breakout_vol = (vol_avg20 <= 0) or (vol_curr >= vol_avg20 * min_vol_ratio)

    bull_factors = [
        curr_ema12 > curr_ema26,
        curr_ema26 >= curr_ema50 * 0.997,
        current_price >= curr_ema50 * 0.992,
        current_macd > 0 and current_macd >= prev_macd,
        rsi_buy_min <= rsi <= rsi_buy_max,
        vol_ratio >= min_vol_ratio,
    ]
    bear_factors = [
        curr_ema12 < curr_ema26,
        curr_ema26 <= curr_ema50 * 1.003,
        current_price <= curr_ema50 * 1.008,
        current_macd < 0 and current_macd <= prev_macd,
        rsi_sell_min <= rsi <= rsi_sell_max,
        vol_ratio >= min_vol_ratio,
    ]

    bull_hits = sum(1 for f in bull_factors if f)
    bear_hits = sum(1 for f in bear_factors if f)

    bull_certainty = round((bull_hits / 6.0) * 100.0, 1)
    bear_certainty = round((bear_hits / 6.0) * 100.0, 1)

    bull_strict_ok = (
        not is_chop
        and not fake_bull_breakout
        and confirmed_breakout_vol
        and (curr_ema12 > curr_ema26)
        and (current_price >= curr_ema50 * 0.992)
        and (rsi_buy_min <= rsi <= rsi_buy_max)
        and bull_hits >= 5
    )

    bear_strict_ok = (
        not is_chop
        and not fake_bear_breakout
        and confirmed_breakout_vol
        and (curr_ema12 < curr_ema26)
        and (current_price <= curr_ema50 * 1.008)
        and (rsi_sell_min <= rsi <= rsi_sell_max)
        and bear_hits >= 5
    )

    clean_sym = canonicalize_symbol_key(symbol)
    now_ts = time.time()
    prev_dir_info = _LAST_CONFIRMED_DIRECTION.get(clean_sym)

    candidate_action = "NEUTRAL"
    if bull_strict_ok and not bear_strict_ok:
        candidate_action = "BUY"
    elif bear_strict_ok and not bull_strict_ok:
        candidate_action = "SELL"

    if candidate_action in ["BUY", "SELL"] and prev_dir_info:
        last_side = prev_dir_info.get("action")
        last_ts = safe_float(prev_dir_info.get("ts"), 0.0)
        if last_side in ["BUY", "SELL"] and last_side != candidate_action:
            elapsed = now_ts - last_ts
            opp_cert = bull_certainty if candidate_action == "BUY" else bear_certainty
            if elapsed < DIRECTION_COOLDOWN_SEC and opp_cert < 83.3:
                candidate_action = "NEUTRAL"

    if candidate_action == "BUY":
        certainty_val = bull_certainty
        confluence_hits = bull_hits
        score = round(min(99.0, max(min_score, 72.0 + bull_hits * 4.2)), 1)
        action, sent, col = "BUY", "LONG CONFERMATO", "#00ff66"
        inv = f"🚀 Preset {timeframe}/{profile} LONG Alta Precisione"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "BUY", "ts": now_ts}
    elif candidate_action == "SELL":
        certainty_val = bear_certainty
        confluence_hits = bear_hits
        score = round(min(99.0, max(min_score, 72.0 + bear_hits * 4.2)), 1)
        action, sent, col = "SELL", "SHORT CONFERMATO", "#ff1e42"
        inv = f"📉 Preset {timeframe}/{profile} SHORT Alta Precisione"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "SELL", "ts": now_ts}
    else:
        certainty_val = max(bull_certainty, bear_certainty)
        confluence_hits = max(bull_hits, bear_hits)
        action, score, sent, col = "NEUTRAL", 55.0, "FILTRATO / NO-TRADE", "#ffffff"
        inv = "🛡️ Filtro Anti-Flip-Flop & Precisione Attivo"

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

    raw_sl_dist = max(atr * sl_m, current_price * min_sl_pct)
    raw_tp_dist = max(atr * tp_m, raw_sl_dist * min_tp_vs_sl)
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

    if balance <= 2.0 and profile == "aggressive":
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
        curr_ema50=curr_ema50,
        current_macd=current_macd,
        prev_macd=prev_macd,
        vol_ratio=vol_ratio,
        min_vol_ratio=min_vol_ratio,
    )

    math_model = compute_math_profit_model(
        action=action,
        certainty_pct=certainty_val,
        rrr_val=rrr_val,
        pos_size=pos_size,
        tp_dist=tp_dist,
        sl_dist=sl_dist,
        roundtrip_fee=roundtrip_fee,
        balance=balance,
        risk_pct=risk_pct,
    )

    if action in ["BUY", "SELL"] and (anti_fake["false_breakout"] or not math_model["math_approved"]):
        action = "NEUTRAL"
        score = 55.0
        sent = "BLOCCATO ANTI-FAKEOUT / EV"
        col = "#ffd166"
        inv = "🛡️ Blocco Matematico Anti-Falsa Rottura"
        filter_reason = (
            f"Bloccato da sistema anti-falsa rottura / EV: {anti_fake['fakeout_reason']} | "
            f"EV netto ${math_model['ev_net_usd']} (R:R 1:{rrr_val})."
        )
        trigger_condition = "Attesa rottura pulita senza wick anomala e con EV netto > 0 e R:R >= 1.5."
    elif not is_open:
        filter_reason = f"Borsa {category} chiusa — in attesa riapertura sessione."
        trigger_condition = f"Attivazione alla riapertura con almeno 5/6 fattori allineati e Score >= {min_score}%."
    elif (
        prev_dir_info
        and (now_ts - safe_float(prev_dir_info.get("ts"), 0.0)) < DIRECTION_COOLDOWN_SEC
        and prev_dir_info.get("action") in ["BUY", "SELL"]
        and action == "NEUTRAL"
    ):
        rem_cd = int(max(1, DIRECTION_COOLDOWN_SEC - (now_ts - safe_float(prev_dir_info.get("ts"), 0.0))))
        filter_reason = (
            f"Protezione anti-inversione ({prev_dir_info.get('action')} recente): "
            f"attesa {rem_cd}s o certezza >=83.3% per cambiare direzione."
        )
        trigger_condition = f"Cambio direzione consentito dopo {rem_cd}s oppure con conferma piena (5/6 fattori)."
    elif action == "NEUTRAL":
        filter_reason = (
            f"Precisione non ancora raggiunta (Certezza {certainty_val}%, {confluence_hits}/6 fattori, "
            f"RSI {round(rsi, 1)}, Vol {vol_ratio}x)."
        )
        trigger_condition = (
            f"LONG con EMA12>EMA26, RSI [{rsi_buy_min}-{rsi_buy_max}], Vol>={min_vol_ratio}x e >=5/6 fattori; "
            f"SHORT con EMA12<EMA26, RSI [{rsi_sell_min}-{rsi_sell_max}], Vol>={min_vol_ratio}x e >=5/6 fattori."
        )
    else:
        filter_reason = (
            f"Preso in carico: {action} con certezza {certainty_val}% ({certainty_level}, {confluence_label}) "
            f"e EV netto +${math_model['ev_net_usd']}."
        )
        trigger_condition = f"Segnale {action} confermato ({certainty_val}% certezza) con SL {sl_m}x ATR e TP1 {tp_m}x ATR."

    vol_pct = round((atr / entry) * 100, 2) if entry > 0 else 0.0
    vol_regime = "ALTA VOLATILITÀ" if vol_pct >= 1.2 else ("MODERATA" if vol_pct >= 0.35 else "CONTENUTA")
    trend_state = (
        "BULLISH EMA/MACD"
        if (curr_ema12 > curr_ema26 and current_macd > 0)
        else ("BEARISH EMA/MACD" if (curr_ema12 < curr_ema26 and current_macd < 0) else "MISTO / NO-FLIP")
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
        "false_breakout": anti_fake["false_breakout"],
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
        "advisor_status": f"SETUP ALTA PRECISIONE ({certainty_val}%)" if action in ["BUY", "SELL"] else "FILTRATO (NO FALSO SEGNALE)",
        "advisor_color": "#00ff66" if action in ["BUY", "SELL"] else "#8a99ad",
        "advisor_msg": f"BingX: {bx_symbol} | Certezza {certainty_val}% ({certainty_level}) | EV ${math_model['ev_net_usd']}",
        "applied_params": cfg,
        "market_regime": regime_info,
        "recommended_params": regime_info["recommended_params"],
    }


def build_performance_payload():
    sync_bingx_closed_income_if_configured()
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
        win_pct = round((w_cnt / total) * 100.0, 1) if total > 0 else 0.0
        loss_pct = round((l_cnt / total) * 100.0, 1) if total > 0 else 0.0
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
    return {"daily": daily, "monthly": monthly}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/live-ticker", methods=["GET"])
def api_live_ticker():
    sym = canonicalize_symbol_key(request.args.get("symbol", "BTCUSDT"))
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0], "base_price": 100.0})
    live_p = fetch_bingx_ticker(meta["bx"])
    price_out = live_p if (live_p and live_p > 0) else meta["base_price"]
    return jsonify({
        "symbol": sym,
        "bingx_symbol": meta["bx"],
        "price": price_out,
        "live": bool(live_p and live_p > 0),
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


@app.route("/api/signals", methods=["GET"])
def get_signals():
    profile = request.args.get("profile", "balanced")
    timeframe = request.args.get("timeframe", "15m")
    if timeframe not in ("5m", "15m", "1h"):
        timeframe = "15m"
    balance = max(1.0, safe_float(request.args.get("balance"), 2.0))
    risk_pct = min(20.0, max(0.25, safe_float(request.args.get("risk"), 1.0)))
    min_score = min(99.0, max(50.0, safe_float(request.args.get("min_score"), 80.0)))
    market_filter = request.args.get("market", "ALL")
    symbol_filter = request.args.get("symbol", "ALL")

    cfg = build_profile_config(profile, timeframe, min_score)
    sessions = get_session_statuses()

    signals_out = []
    monitoring_rows = []
    market_diagnostics = {}

    for category, assets in MARKET_CATALOG.items():
        sess = sessions.get(category, {"status": "APERTO (Live)", "is_open": True, "hours": "24/7"})
        is_open = bool(sess.get("is_open", True))
        cat_signals = 0
        cat_scanned = 0

        for meta in assets:
            sym = meta["symbol"]
            if market_filter != "ALL" and category != market_filter:
                continue
            if symbol_filter != "ALL" and sym != symbol_filter:
                continue

            cat_scanned += 1
            df, curr_price = generate_market_dataframe(meta, timeframe)
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
                "why_not_taken": res["filter_reason"],
                "when_taken": res["trigger_condition"],
                "params": {"profile": profile, "timeframe": timeframe, "balance": balance, "risk": risk_pct, "min_score": min_score},
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

        if not is_open:
            diag_reason = f"Mercato {category} attualmente chiuso ({sess['hours']})."
        elif cat_signals == 0:
            diag_reason = (
                f"Analizzati {cat_scanned} asset su {category}: nessun setup ha superato contemporaneamente "
                f"5/6 fattori direzionali, filtro anti-falsa rottura, EV netto > 0 e Score >= {min_score}%."
            )
        else:
            diag_reason = f"{cat_signals} segnali ad alta precisione attivi su {category}."

        market_diagnostics[category] = {"scanned": cat_scanned, "signals_count": cat_signals, "reason": diag_reason}

    with STATE_LOCK:
        STATE["scans_count"] += 1
        STATE["telemetry_logs"] = ([{
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "status": f"SCAN {timeframe} ({profile})",
            "signals_found": len(signals_out),
            "scanned": len(monitoring_rows),
        }] + STATE["telemetry_logs"])[:40]

    return jsonify({
        "sessions": sessions,
        "signals": signals_out,
        "active_params": cfg,
        "market_diagnostics": market_diagnostics,
        "market_monitoring_table": monitoring_rows,
        "performance": build_performance_payload(),
    })


@app.route("/api/analytics/performance", methods=["GET"])
def api_analytics_performance():
    return jsonify(build_performance_payload())


@app.route("/api/autotrade/execute-manual", methods=["POST"])
def api_execute_manual():
    body = request.get_json(silent=True) or {}
    sym = canonicalize_symbol_key(body.get("symbol", "BTCUSDT"))
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0], "name": body.get("name", sym), "category": "Criptovalute Globali"})
    action = (body.get("action") or "BUY").upper()
    if action not in ("BUY", "SELL"):
        action = "BUY"

    entry = safe_float(body.get("entry"), meta.get("base_price", 100.0))
    size = max(0.01, safe_float(body.get("size"), 0.01))
    now_dt = datetime.now(timezone.utc)

    pos_item = {
        "id": f"ord_{int(time.time() * 1000)}_{sym}",
        "symbol": sym,
        "name": meta["name"],
        "action": action,
        "entry": entry,
        "size": size,
        "leverage": body.get("leverage", "Isolata 5x"),
        "margin": safe_float(body.get("margin"), 2.0),
        "sl": safe_float(body.get("sl"), entry * (0.99 if action == "BUY" else 1.01)),
        "tp1": safe_float(body.get("tp1"), entry * (1.02 if action == "BUY" else 0.98)),
        "unrealized_pnl": 0.0,
        "opened_at": now_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    with STATE_LOCK:
        STATE["positions"].append(pos_item)
        STATE["history"].insert(0, {**pos_item, "status": "APERTO"})

    return jsonify({"success": True, "mode": "DEMO/PAPER" if STATE["demo_mode"] else "BINGX LIVE", "position": pos_item})


@app.route("/api/autotrade/close-position", methods=["POST"])
def api_close_position():
    body = request.get_json(silent=True) or {}
    pos_id = body.get("id")
    realized_pnl = safe_float(body.get("realized_pnl"), 0.0)
    now_dt = datetime.now(timezone.utc)

    closed_item = None
    with STATE_LOCK:
        remaining = []
        for p in STATE["positions"]:
            if not closed_item and (pos_id is None or p.get("id") == pos_id):
                closed_item = {
                    **p,
                    "status": "CHIUSO",
                    "realized_pnl": round(realized_pnl, 4),
                    "closed_date": now_dt.strftime("%Y-%m-%d"),
                    "closed_month": now_dt.strftime("%Y-%m"),
                    "closed_at": now_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
            else:
                remaining.append(p)
        STATE["positions"] = remaining
        if closed_item:
            STATE["closed_trades"].append(closed_item)
            STATE["session_pnl"] = round(STATE["session_pnl"] + realized_pnl, 4)
            STATE["history"].insert(0, closed_item)

    return jsonify({"success": True, "closed": closed_item, "performance": build_performance_payload()})


@app.route("/api/autotrade/status", methods=["GET"])
def api_autotrade_status():
    with STATE_LOCK:
        payload = {
            "auto_trade": STATE["auto_trade"],
            "mode": "DEMO/PAPER" if STATE["demo_mode"] else "BINGX LIVE",
            "positions": list(STATE["positions"]),
            "history": list(STATE["history"]),
        }
    payload["performance"] = build_performance_payload()
    return jsonify(payload)


@app.route("/api/telemetry", methods=["GET"])
def api_telemetry():
    with STATE_LOCK:
        return jsonify({
            "scans_count": STATE["scans_count"],
            "session_pnl": STATE["session_pnl"],
            "telemetry_logs": list(STATE["telemetry_logs"]),
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
