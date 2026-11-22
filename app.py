import hashlib
import hmac
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")


def _clean_env_secret(val: str) -> str:
    s = (val or "").strip()
    if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        s = s[1:-1].strip()
    return s


def get_bingx_credentials():
    api_key = _clean_env_secret(
        os.getenv("BINGX_API_KEY")
        or os.getenv("BX_API_KEY")
        or os.getenv("BINGX_KEY")
        or ""
    )
    api_secret = _clean_env_secret(
        os.getenv("BINGX_API_SECRET")
        or os.getenv("BINGX_SECRET_KEY")
        or os.getenv("BX_API_SECRET")
        or os.getenv("BINGX_SECRET")
        or ""
    )
    return api_key, api_secret


BINGX_API_KEY, BINGX_API_SECRET = get_bingx_credentials()

DIRECTION_COOLDOWN_SEC = 180.0
_LAST_CONFIRMED_DIRECTION = {}
_LIVE_PRICE_CACHE = {}

STATE_LOCK = threading.Lock()
STATE = {
    "auto_trade": False,
    "real_mode": True,
    "scans_count": 0,
    "session_pnl": 0.0,
    "daily_loss_guard_usd": 1.0,
    "telemetry_logs": [],
    "closed_trades": [],
    "last_bingx_sync": 0.0,
    "last_bingx_balance": 0.0,
    "last_bingx_equity": 0.0,
    "last_bingx_available": 0.0,
    "last_bingx_balance_error": None,
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
            "badge_color": "#00ff66" if commodities_open else "#ff1e42",
        },
        "Azioni & Indici Globali": {
            "status": "APERTO (NYSE/NASDAQ)" if equities_open else "CHIUSO (Fuori Orario)",
            "is_open": equities_open,
            "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC (Lun-Ven)",
            "badge_color": "#00ff66" if equities_open else "#ff1e42",
        },
    }


def sign_bingx_query(params: dict, api_secret: str) -> str:
    query_str = urlencode(sorted(params.items()))
    sig = hmac.new(api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_str}&signature={sig}"


def bingx_signed_request(method: str, path: str, params: dict):
    global BINGX_API_KEY, BINGX_API_SECRET
    api_key, api_secret = get_bingx_credentials()
    BINGX_API_KEY, BINGX_API_SECRET = api_key, api_secret
    if not (api_key and api_secret):
        return None, "API Key / Secret BingX non configurate su Render"
    req_params = dict(params or {})
    req_params["timestamp"] = int(time.time() * 1000)
    signed_qs = sign_bingx_query(req_params, api_secret)
    url = f"{BINGX_BASE_URL}{path}?{signed_qs}"
    headers = {"X-BX-APIKEY": api_key}
    try:
        if method.upper() == "POST":
            resp = requests.post(url, headers=headers, timeout=3.0)
        else:
            resp = requests.get(url, headers=headers, timeout=3.0)
        data = resp.json()
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {data}"
        if isinstance(data, dict) and data.get("code") not in (0, "0", None):
            return None, str(data.get("msg") or data)
        return data, None
    except Exception as exc:
        return None, str(exc)


def fetch_bingx_real_balance():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return {
            "balance": 0.0,
            "equity": 0.0,
            "available_margin": 0.0,
            "unrealized_pnl": 0.0,
            "asset": "USDT",
        }, "API Key / Secret BingX non configurate su Render"

    payload, err = bingx_signed_request("GET", "/openApi/swap/v2/user/balance", {})
    if err or not isinstance(payload, dict):
        with STATE_LOCK:
            STATE["last_bingx_balance_error"] = err or "Errore lettura saldo BingX"
        return {
            "balance": STATE.get("last_bingx_balance", 0.0),
            "equity": STATE.get("last_bingx_equity", 0.0),
            "available_margin": STATE.get("last_bingx_available", 0.0),
            "unrealized_pnl": 0.0,
            "asset": "USDT",
        }, err or "Errore lettura saldo BingX"

    raw_data = payload.get("data")
    bal_obj = None
    if isinstance(raw_data, dict):
        if isinstance(raw_data.get("balance"), dict):
            bal_obj = raw_data["balance"]
        elif isinstance(raw_data.get("balances"), list) and raw_data["balances"]:
            for item in raw_data["balances"]:
                if isinstance(item, dict) and str(item.get("asset") or "USDT").upper() == "USDT":
                    bal_obj = item
                    break
            if bal_obj is None and isinstance(raw_data["balances"][0], dict):
                bal_obj = raw_data["balances"][0]
        else:
            bal_obj = raw_data
    elif isinstance(raw_data, list) and raw_data:
        for item in raw_data:
            if isinstance(item, dict) and str(item.get("asset") or "USDT").upper() == "USDT":
                bal_obj = item
                break
        if bal_obj is None and isinstance(raw_data[0], dict):
            bal_obj = raw_data[0]

    bal_obj = bal_obj or {}
    wallet_bal = safe_float(bal_obj.get("balance") or bal_obj.get("walletBalance"), 0.0)
    unrealized = safe_float(bal_obj.get("unrealizedProfit"), 0.0)
    equity_val = safe_float(bal_obj.get("equity"), wallet_bal + unrealized)
    avail_val = safe_float(
        bal_obj.get("availableMargin") or bal_obj.get("availableBalance"),
        max(0.0, equity_val),
    )
    asset_code = str(bal_obj.get("asset") or "USDT").upper()

    info = {
        "balance": round(wallet_bal, 4),
        "equity": round(equity_val, 4),
        "available_margin": round(avail_val, 4),
        "unrealized_pnl": round(unrealized, 4),
        "asset": asset_code,
    }
    with STATE_LOCK:
        STATE["last_bingx_balance"] = info["balance"]
        STATE["last_bingx_equity"] = info["equity"]
        STATE["last_bingx_available"] = info["available_margin"]
        STATE["last_bingx_balance_error"] = None
    return info, None


def sync_bingx_real_closed_trades():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return
    with STATE_LOCK:
        if time.time() - STATE.get("last_bingx_sync", 0.0) < 2.5:
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
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return [], "API Key / Secret BingX non configurate su Render"
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
    if cached and (time.time() - cached["ts"]) < 1.0:
        return cached["price"]
    try:
        url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker"
        resp = requests.get(url, params={"symbol": bx_symbol}, timeout=2.0)
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
        resp = requests.get(url, params={"symbol": bx_symbol, "interval": bx_int, "limit": limit}, timeout=2.2)
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


def build_profile_config(profile: str, timeframe: str, min_score: float):
    p = (profile or "balanced").lower()
    if p == "certain_profit":
        return {
            "profile": "certain_profit",
            "timeframe": timeframe,
            "min_score": max(min_score, 88.0),
            "required_factors": 6,
            "min_rrr": 2.0,
            "max_risk_pct": 0.5,
            "rsi_buy_min": 51.0,
            "rsi_buy_max": 62.0,
            "rsi_sell_min": 38.0,
            "rsi_sell_max": 49.0,
            "sl_m": 1.4,
            "tp_m": 3.0,
            "min_vol_ratio": 1.25,
        }
    if p == "conservative":
        return {
            "profile": "conservative",
            "timeframe": timeframe,
            "min_score": max(min_score, 82.0),
            "required_factors": 5,
            "min_rrr": 1.5,
            "max_risk_pct": 1.5,
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
            "required_factors": 5,
            "min_rrr": 1.5,
            "max_risk_pct": 3.0,
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
        "required_factors": 5,
        "min_rrr": 1.5,
        "max_risk_pct": 2.0,
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


def compute_math_profit_model(certainty_pct, rrr_val, pos_size, tp_dist, sl_dist, roundtrip_fee, risk_pct):
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


def compute_exact_market_score(
    bull_hits: int,
    bear_hits: int,
    rsi: float,
    vol_ratio: float,
    min_vol_ratio: float,
    curr_ema12: float,
    curr_ema26: float,
    current_macd: float,
    prev_macd: float,
    false_breakout: bool,
):
    ema_spread_ratio = abs(curr_ema12 - curr_ema26) / max(abs(curr_ema26), 1e-9)
    ema_pts = min(28.0, ema_spread_ratio * 2200.0)

    macd_accel = abs(current_macd - prev_macd) / max(abs(current_macd) + abs(prev_macd), 1e-9)
    macd_pts = min(22.0, (12.0 if (current_macd * prev_macd > 0 or current_macd != 0) else 4.0) + macd_accel * 10.0)

    rsi_dist_from_50 = min(20.0, abs(rsi - 50.0) * 0.95)
    rsi_pts = rsi_dist_from_50

    vol_pts = min(15.0, (vol_ratio / max(min_vol_ratio, 0.5)) * 11.0)
    factor_pts = (max(bull_hits, bear_hits) / 6.0) * 15.0

    raw_score = ema_pts + macd_pts + rsi_pts + vol_pts + factor_pts
    if false_breakout:
        raw_score *= 0.68

    exact_score = round(max(5.0, min(99.0, raw_score)), 1)
    dominant_side = "BULLISH" if bull_hits >= bear_hits else "BEARISH"
    return exact_score, dominant_side


def build_tp_candle_cycle_info(now_ts: float, tf_min: int, candles_needed: int, tp_dist: float, recent_movement: float, atr: float):
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    sec_into_day = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second
    tf_sec = max(60, tf_min * 60)

    current_candle_idx = (sec_into_day // tf_sec) + 1
    total_day_candles = (24 * 3600) // tf_sec
    sec_left_in_candle = tf_sec - (sec_into_day % tf_sec)
    min_left_in_candle = max(0.1, round(sec_left_in_candle / 60.0, 1))

    target_candle_idx = current_candle_idx + max(1, candles_needed)
    est_min_exact = max(tf_min, int(round((candles_needed * tf_sec + sec_left_in_candle) / 60.0)))
    tp_eta_ts = now_ts + (est_min_exact * 60)
    tp_eta_utc = datetime.fromtimestamp(tp_eta_ts, tz=timezone.utc).strftime("%H:%M UTC")

    velocity_per_candle = round(max(recent_movement, atr * 0.15), 6)
    cycle_summary = (
        f"Ciclo Candele {tf_min}m: Candela #{current_candle_idx}/{total_day_candles} "
        f"(chiude tra {min_left_in_candle}m) → Target Candela #{target_candle_idx} "
        f"(+{candles_needed} candele, ~{est_min_exact}m, ETA {tp_eta_utc})"
    )

    return {
        "current_candle_idx": current_candle_idx,
        "total_day_candles": total_day_candles,
        "target_candle_idx": target_candle_idx,
        "min_left_in_candle": min_left_in_candle,
        "est_min_exact": est_min_exact,
        "velocity_per_candle": velocity_per_candle,
        "tp_eta_utc": tp_eta_utc,
        "tp_cycle_exact": cycle_summary,
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
    req_factors = int(cfg.get("required_factors", 5))
    min_rrr_req = safe_float(cfg.get("min_rrr"), 1.5)
    effective_risk_pct = min(risk_pct, safe_float(cfg.get("max_risk_pct"), risk_pct))
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
    is_chop = (vol_avg20 > 0 and vol_curr < vol_avg20 * 0.70) or (last_range < atr * 0.12)
    confirmed_breakout_vol = (vol_avg20 > 0) and (vol_curr >= vol_avg20 * min_vol_ratio)

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
    )

    exact_market_score, dominant_side = compute_exact_market_score(
        bull_hits=bull_hits,
        bear_hits=bear_hits,
        rsi=rsi,
        vol_ratio=vol_ratio,
        min_vol_ratio=min_vol_ratio,
        curr_ema12=curr_ema12,
        curr_ema26=curr_ema26,
        current_macd=current_macd,
        prev_macd=prev_macd,
        false_breakout=anti_fake["false_breakout"],
    )

    bull_strict_ok = (
        not is_chop
        and not fake_bull_breakout
        and confirmed_breakout_vol
        and (curr_ema12 > curr_ema26)
        and (current_price >= curr_ema50 * 0.992)
        and (rsi_buy_min <= rsi <= rsi_buy_max)
        and bull_hits >= req_factors
        and exact_market_score >= min_score
    )

    bear_strict_ok = (
        not is_chop
        and not fake_bear_breakout
        and confirmed_breakout_vol
        and (curr_ema12 < curr_ema26)
        and (current_price <= curr_ema50 * 1.008)
        and (rsi_sell_min <= rsi <= rsi_sell_max)
        and bear_hits >= req_factors
        and exact_market_score >= min_score
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
        score = exact_market_score
        action, sent, col = "BUY", "LONG EV+ CONFERMATO", "#00ff66"
        inv = f"🧠 Cervello Matematico LONG (Score {score}%)"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "BUY", "ts": now_ts}
    elif candidate_action == "SELL":
        certainty_val = bear_certainty
        confluence_hits = bear_hits
        score = exact_market_score
        action, sent, col = "SELL", "SHORT EV+ CONFERMATO", "#ff1e42"
        inv = f"🧠 Cervello Matematico SHORT (Score {score}%)"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "SELL", "ts": now_ts}
    else:
        certainty_val = max(bull_certainty, bear_certainty)
        confluence_hits = max(bull_hits, bear_hits)
        score = exact_market_score
        action, sent, col = "NEUTRAL", "FILTRATO / NO-TRADE", "#ffffff"
        inv = "🧠 Cervello Matematico: Filtro EV+ & Anti-Flip-Flop"

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
    cycle_info = build_tp_candle_cycle_info(now_ts, tf_min, candles_needed, tp_dist, recent_movement, atr)
    est_min = cycle_info["est_min_exact"]
    duration = (
        f"~{est_min} min ({candles_needed} candele)"
        if est_min < 60
        else f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    )
    tp_eta_utc = cycle_info["tp_eta_utc"]
    tp_eta_text = f"{duration} • {candle_tf_label} • ETA ~{tp_eta_utc}"

    if balance <= 2.0 and profile == "aggressive":
        lev_int = 20
    elif symbol in ["BTCUSDT", "ETHUSDT"]:
        lev_int = 8
    elif category == "Criptovalute Globali":
        lev_int = 10
    else:
        lev_int = 5

    max_risk = balance * (effective_risk_pct / 100.0)
    risk_unit = max(abs(entry - sl), tick_size)
    pos_size = compute_order_quantity(bx_symbol, entry, max_risk / risk_unit, max(balance, 2.0), lev_int)
    margin = round((pos_size * entry) / lev_int, 2)

    roi_raw = round((tp_dist / entry) * lev_int * 100, 1) if entry > 0 else 0.0
    roi_pct = f"+{roi_raw}%" if action == "BUY" else f"-{roi_raw}%" if action == "SELL" else "0.0%"

    notional = pos_size * entry
    roundtrip_fee = round(notional * 0.001, 4)
    expected_gross_profit = round(pos_size * tp_dist, 4)
    expected_net_profit = round(expected_gross_profit - roundtrip_fee, 4)

    math_model = compute_math_profit_model(
        certainty_pct=certainty_val,
        rrr_val=rrr_val,
        pos_size=pos_size,
        tp_dist=tp_dist,
        sl_dist=sl_dist,
        roundtrip_fee=roundtrip_fee,
        risk_pct=effective_risk_pct,
    )

    with STATE_LOCK:
        daily_guard_limit = -abs(safe_float(STATE.get("daily_loss_guard_usd"), 1.0))
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_net = sum(
            safe_float(t.get("realized_pnl"))
            for t in STATE.get("closed_trades", [])
            if str(t.get("closed_date", "")) == today_str
        )

    guard_tripped = (profile == "certain_profit") and (today_net <= daily_guard_limit)
    math_ok = math_model["math_approved"] and (rrr_val >= min_rrr_req) and (confluence_hits >= req_factors)

    if action in ["BUY", "SELL"] and (anti_fake["false_breakout"] or not math_ok or guard_tripped):
        action = "NEUTRAL"
        sent = "GUARDIA ATTIVA / NO-TRADE"
        col = "#ffd166"
        inv = "🛡️ Cervello Matematico: Filtro EV+ / Guardia"
        if guard_tripped:
            filter_reason = f"Guardia giornaliera attivata (Netto oggi ${today_net:.2f} <= ${daily_guard_limit:.2f}): trade bloccati."
        else:
            filter_reason = (
                f"Score {exact_market_score}% • Filtro EV/Rischio: fattori {confluence_hits}/{req_factors}, "
                f"R:R 1:{rrr_val} (min 1:{min_rrr_req}), EV netto ${math_model['ev_net_usd']}."
            )
        trigger_condition = f"Richiede {req_factors}/6 fattori, R:R >= {min_rrr_req}, EV netto > 0 e guardia non attiva."
    elif not is_open:
        filter_reason = f"Score Mercato {exact_market_score}% • Borsa {category} chiusa."
        trigger_condition = f"Attivazione alla riapertura con Score >= {min_score}% e EV netto > 0."
    elif (
        prev_dir_info
        and (now_ts - safe_float(prev_dir_info.get("ts"), 0.0)) < DIRECTION_COOLDOWN_SEC
        and prev_dir_info.get("action") in ["BUY", "SELL"]
        and action == "NEUTRAL"
    ):
        rem_cd = int(max(1, DIRECTION_COOLDOWN_SEC - (now_ts - safe_float(prev_dir_info.get("ts"), 0.0))))
        filter_reason = (
            f"Score Mercato {exact_market_score}% • Protezione anti-inversione ({prev_dir_info.get('action')}): "
            f"attesa {rem_cd}s."
        )
        trigger_condition = f"Cambio direzione dopo {rem_cd}s con conferma piena ({req_factors}/6 fattori)."
    elif action == "NEUTRAL":
        filter_reason = (
            f"Score Mercato {exact_market_score}% ({dominant_side}) • Certezza {certainty_val}% ({confluence_hits}/6) "
            f"• EV ${math_model['ev_net_usd']}."
        )
        trigger_condition = (
            f"Score >= {min_score}%, >= {req_factors}/6 fattori, R:R >= {min_rrr_req} e EV netto > 0."
        )
    else:
        filter_reason = (
            f"Score Mercato {exact_market_score}% • Setup {action} EV+ approvato (+${math_model['ev_net_usd']}) "
            f"• {cycle_info['tp_cycle_exact']}."
        )
        trigger_condition = f"Segnale {action} EV+ confermato ({exact_market_score}%) con SL {sl_m}x ATR e TP1 {tp_m}x ATR."

    vol_pct = round((atr / entry) * 100, 2) if entry > 0 else 0.0
    vol_regime = "ALTA VOLATILITÀ" if vol_pct >= 1.2 else ("MODERATA" if vol_pct >= 0.35 else "CONTENUTA")
    trend_state = (
        "BULLISH EMA/MACD"
        if (curr_ema12 > curr_ema26 and current_macd > 0)
        else ("BEARISH EMA/MACD" if (curr_ema12 < curr_ema26 and current_macd < 0) else "MISTO / NO-FLIP")
    )

    return {
        "action": action,
        "exact_market_score": exact_market_score,
        "dominant_side": dominant_side,
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
        "tp_cycle_exact": cycle_info["tp_cycle_exact"],
        "current_candle_idx": cycle_info["current_candle_idx"],
        "target_candle_idx": cycle_info["target_candle_idx"],
        "min_left_in_candle": cycle_info["min_left_in_candle"],
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
        "advisor_status": f"CERVELLO EV+ ({exact_market_score}%)" if action in ["BUY", "SELL"] else f"SCORE MERCATO {exact_market_score}%",
        "advisor_color": "#00ff66" if action in ["BUY", "SELL"] else "#ffd166",
        "advisor_msg": f"BingX Live: {bx_symbol} | Score {exact_market_score}% ({dominant_side}) | EV ${math_model['ev_net_usd']}",
        "applied_params": cfg,
        "market_regime": regime_info,
        "recommended_params": regime_info["recommended_params"],
    }


def build_performance_payload():
    sync_bingx_real_closed_trades()
    bal_info, bal_err = fetch_bingx_real_balance()
    api_key, api_secret = get_bingx_credentials()
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
        "account": bal_info,
        "account_error": bal_err,
        "api_configured": bool(api_key and api_secret),
    }


def _scan_single_asset(category, sess, is_open, meta, timeframe, profile, balance, risk_pct, min_score, cfg):
    sym = meta["symbol"]
    df, curr_price = get_real_market_dataframe(meta, timeframe)
    if df is None or curr_price <= 0:
        return {
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
            "exact_market_score": 0.0,
            "dominant_side": "NEUTRAL",
            "certainty_pct": 0.0,
            "certainty_level": "BASSA",
            "certainty_text": "0.0% (BASSA)",
            "why_not_taken": f"Dato live BingX non disponibile per {meta['bx']}",
            "when_taken": "Richiede feed live BingX attivo",
            "params": {"profile": profile, "timeframe": timeframe, "balance": balance, "risk": risk_pct, "min_score": min_score},
        }, None

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
        "tp_cycle_exact": res["tp_cycle_exact"],
        "current_candle_idx": res["current_candle_idx"],
        "target_candle_idx": res["target_candle_idx"],
        "min_left_in_candle": res["min_left_in_candle"],
        "confluence": res["confluence"],
        "size": res["size"],
        "leverage": res["leverage"],
        "margin": res["margin"],
        "roundtrip_fee": res["roundtrip_fee"],
        "expected_net": res["expected_net"],
        "action": res["action"] if is_open else "NEUTRAL",
        "taken": taken,
        "ai_score": res["ai_score"],
        "exact_market_score": res["exact_market_score"],
        "dominant_side": res["dominant_side"],
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

    sig_payload = None
    if taken:
        sig_payload = {
            **row_payload,
            "id": f"{sym}_{res['action']}_{timeframe}",
            "sentiment": res["sentiment"],
            "sentiment_color": res["sentiment_color"],
            "applied_params": cfg,
        }
    return row_payload, sig_payload


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/bingx/live-account", methods=["GET"])
def api_bingx_live_account():
    api_key, api_secret = get_bingx_credentials()
    bal_info, bal_err = fetch_bingx_real_balance()
    positions, pos_err = fetch_bingx_real_positions()
    sync_bingx_real_closed_trades()
    with STATE_LOCK:
        session_pnl = STATE["session_pnl"]
    return jsonify({
        "success": bool(api_key and api_secret) and not (bal_err and pos_err),
        "mode": "BINGX REAL LIVE",
        "api_configured": bool(api_key and api_secret),
        "account": bal_info,
        "balance_error": bal_err,
        "positions": positions,
        "positions_error": pos_err,
        "session_pnl": session_pnl,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


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


@app.route("/api/candles", methods=["GET"])
def api_candles():
    sym = canonicalize_symbol_key(request.args.get("symbol", "BTCUSDT"))
    tf = (request.args.get("tf") or request.args.get("timeframe") or "5m").strip()
    if tf not in ("5m", "15m", "1h"):
        tf = "5m"
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0], "precision": 2})
    df, curr_price = get_real_market_dataframe(meta, tf)
    if df is None or df.empty:
        return jsonify({
            "symbol": sym,
            "bingx_symbol": meta["bx"],
            "timeframe": tf,
            "price": curr_price or 0.0,
            "candles": [],
        }), 200

    tail_df = df.tail(40).copy()
    candles = []
    for _, row in tail_df.iterrows():
        candles.append({
            "open": safe_float(row.get("Open")),
            "high": safe_float(row.get("High")),
            "low": safe_float(row.get("Low")),
            "close": safe_float(row.get("Close")),
            "volume": safe_float(row.get("Volume")),
        })

    return jsonify({
        "symbol": sym,
        "bingx_symbol": meta["bx"],
        "timeframe": tf,
        "price": curr_price,
        "candles": candles,
    }), 200


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

    tasks = []
    for category, assets in MARKET_CATALOG.items():
        sess = sessions.get(category, {"status": "APERTO (Live)", "is_open": True, "hours": "24/7"})
        is_open = bool(sess.get("is_open", True))
        for meta in assets:
            if market_filter != "ALL" and category != market_filter:
                continue
            if symbol_filter != "ALL" and meta["symbol"] != symbol_filter:
                continue
            tasks.append((category, sess, is_open, meta))

    rows_by_cat = {cat: [] for cat in MARKET_CATALOG.keys()}
    signals_out = []
    monitoring_rows = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {
            pool.submit(
                _scan_single_asset,
                cat,
                sess,
                is_open,
                meta,
                timeframe,
                profile,
                balance,
                risk_pct,
                min_score,
                cfg,
            ): (cat, meta["symbol"])
            for (cat, sess, is_open, meta) in tasks
        }
        for fut in as_completed(future_map, timeout=6.5):
            cat, _ = future_map[fut]
            try:
                row_p, sig_p = fut.result(timeout=1.0)
                rows_by_cat[cat].append(row_p)
                monitoring_rows.append(row_p)
                if sig_p:
                    signals_out.append(sig_p)
            except Exception:
                pass

    market_diagnostics = {}
    for category, assets in MARKET_CATALOG.items():
        sess = sessions.get(category, {"status": "APERTO (Live)", "is_open": True, "hours": "24/7"})
        is_open = bool(sess.get("is_open", True))
        cat_rows = rows_by_cat.get(category, [])
        cat_scanned = len(cat_rows)
        cat_signals = sum(1 for r in cat_rows if r.get("taken"))
        valid_scores = [safe_float(r.get("exact_market_score"), 0.0) for r in cat_rows if safe_float(r.get("exact_market_score"), 0.0) > 0]
        avg_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 0.0
        top_row = max(cat_rows, key=lambda r: safe_float(r.get("exact_market_score"), 0.0), default=None)
        top_txt = f" • Top: {top_row['symbol']} ({top_row['exact_market_score']}%)" if top_row else ""

        if not is_open:
            diag_reason = f"Score Mercato {avg_score}% • Borsa {category} chiusa ({sess['hours']})."
        elif cat_signals == 0:
            diag_reason = (
                f"Score Mercato {avg_score}%{top_txt} • Analizzati {cat_scanned} asset: nessun setup ha superato "
                f"i filtri matematici EV+, anti-fakeout e Score >= {min_score}%."
            )
        else:
            diag_reason = f"Score Mercato {avg_score}%{top_txt} • {cat_signals} segnali EV+ confermati su {category}."

        market_diagnostics[category] = {
            "scanned": cat_scanned,
            "signals_count": cat_signals,
            "market_score": avg_score,
            "reason": diag_reason,
        }

    live_positions, _ = fetch_bingx_real_positions()

    with STATE_LOCK:
        STATE["scans_count"] += 1
        scans_val = STATE["scans_count"]
        session_pnl_val = STATE["session_pnl"]
        STATE["telemetry_logs"] = ([{
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "status": f"LIVE SCAN {timeframe} ({profile})",
            "signals_found": len(signals_out),
            "scanned": len(monitoring_rows),
        }] + STATE["telemetry_logs"])[:40]
        logs_copy = list(STATE["telemetry_logs"])

    perf_payload = build_performance_payload()
    return jsonify({
        "sessions": sessions,
        "signals": signals_out,
        "active_params": cfg,
        "market_diagnostics": market_diagnostics,
        "market_monitoring_table": monitoring_rows,
        "performance": perf_payload,
        "telemetry": {
            "scans_count": scans_val,
            "active_orders_count": len(live_positions),
            "session_pnl": session_pnl_val,
            "performance": perf_payload,
            "telemetry_logs": logs_copy,
        },
    })


@app.route("/api/analytics/performance", methods=["GET"])
def api_analytics_performance():
    return jsonify(build_performance_payload())


@app.route("/api/autotrade/execute-manual", methods=["POST"])
def api_execute_manual():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return jsonify({
            "success": False,
            "error": "Modalità reale attiva: imposta BINGX_API_KEY e BINGX_API_SECRET nelle Environment Variables di Render per inviare ordini reali su BingX.",
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
    api_key, api_secret = get_bingx_credentials()
    positions, pos_err = fetch_bingx_real_positions()
    perf = build_performance_payload()
    with STATE_LOCK:
        closed_copy = list(STATE["closed_trades"])
    return jsonify({
        "auto_trade": STATE["auto_trade"],
        "mode": "BINGX REAL LIVE",
        "api_configured": bool(api_key and api_secret),
        "positions": positions,
        "positions_error": pos_err,
        "history": closed_copy,
        "performance": perf,
    })


@app.route("/api/telemetry", methods=["GET"])
def api_telemetry():
    sync_bingx_real_closed_trades()
    positions, _ = fetch_bingx_real_positions()
    bal_info, _ = fetch_bingx_real_balance()
    perf = build_performance_payload()
    with STATE_LOCK:
        return jsonify({
            "scans_count": STATE["scans_count"],
            "active_orders_count": len(positions),
            "session_pnl": STATE["session_pnl"],
            "account": bal_info,
            "performance": perf,
            "telemetry_logs": list(STATE["telemetry_logs"]),
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
