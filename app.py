import hashlib
import hmac
import json
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

MIN_BINGX_CAPITAL_USD = 2.0
BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_SPOT_BASE = "https://api.binance.com"


def _clean_env_secret(val: str) -> str:
    s = (val or "").strip()
    if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        s = s[1:-1].strip()
    return s


def get_bingx_base_url() -> str:
    raw = _clean_env_secret(os.getenv("BINGX_BASE_URL") or "https://open-api.bingx.com")
    return (raw or "https://open-api.bingx.com").rstrip("/")


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


BINGX_BASE_URL = get_bingx_base_url()
BINGX_API_KEY, BINGX_API_SECRET = get_bingx_credentials()

DIRECTION_COOLDOWN_SEC = 240.0
_LAST_CONFIRMED_DIRECTION = {}
_LIVE_PRICE_CACHE = {}
_KLINES_CACHE = {}
_AUTOTRADE_LAST_EXEC = {}
_CONTRACT_SPECS_CACHE = {"specs": {}, "ts": 0.0}

STATE_LOCK = threading.Lock()
STATE = {
    "auto_trade": False,
    "only_a_plus": False,
    "max_open_positions": 1,
    "daily_loss_cap_usd": 0.50,
    "scans_count": 0,
    "session_pnl": 0.0,
    "telemetry_logs": [],
    "closed_trades": [],
    "last_bingx_sync": 0.0,
    "last_bingx_balance": 0.0,
    "last_bingx_equity": 0.0,
    "last_bingx_available": 0.0,
    "last_bingx_unrealized": 0.0,
    "last_bingx_balance_error": None,
    "bingx_rate_limit_until": 0.0,
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
        {"symbol": "PAXGUSDT", "bx": "PAXG-USDT", "name": "Oro / Gold (PAXG-USDT)", "precision": 2},
        {"symbol": "XAGUSDT", "bx": "XAG-USDT", "name": "Argento / Silver (XAG-USDT)", "precision": 3},
        {"symbol": "XPTUSDT", "bx": "XPT-USDT", "name": "Platino / Platinum (XPT-USDT)", "precision": 2},
        {"symbol": "XPDUSDT", "bx": "XPD-USDT", "name": "Palladio / Palladium (XPD-USDT)", "precision": 2},
        {"symbol": "COPPERUSDT", "bx": "COPPER-USDT", "name": "Rame / Copper (COPPER-USDT)", "precision": 4},
        {"symbol": "ALUMINIUMUSDT", "bx": "ALUMINIUM-USDT", "name": "Alluminio / Aluminium (ALUMINIUM-USDT)", "precision": 2},
        {"symbol": "WTIUSDT", "bx": "WTI-USDT", "name": "Petrolio WTI Crude (WTI-USDT)", "precision": 2},
        {"symbol": "BRENTUSDT", "bx": "BRENT-USDT", "name": "Petrolio Brent Crude (BRENT-USDT)", "precision": 2},
        {"symbol": "NATGASUSDT", "bx": "NATGAS-USDT", "name": "Gas Naturale (NATGAS-USDT)", "precision": 3},
        {"symbol": "WHEATUSDT", "bx": "WHEAT-USDT", "name": "Grano / Wheat (WHEAT-USDT)", "precision": 2},
        {"symbol": "CORNUSDT", "bx": "CORN-USDT", "name": "Mais / Corn (CORN-USDT)", "precision": 2},
        {"symbol": "SOYBEANUSDT", "bx": "SOYBEAN-USDT", "name": "Soia / Soybeans (SOYBEAN-USDT)", "precision": 2},
        {"symbol": "COFFEEUSDT", "bx": "COFFEE-USDT", "name": "Caffè / Coffee (COFFEE-USDT)", "precision": 2},
        {"symbol": "SUGARUSDT", "bx": "SUGAR-USDT", "name": "Zucchero / Sugar (SUGAR-USDT)", "precision": 3},
        {"symbol": "COCOAUSDT", "bx": "COCOA-USDT", "name": "Cacao / Cocoa (COCOA-USDT)", "precision": 1},
        {"symbol": "EURUSDT", "bx": "EUR-USDT", "name": "EUR/USD (EUR-USDT)", "precision": 4},
        {"symbol": "GBPUSDT", "bx": "GBP-USDT", "name": "GBP/USD (GBP-USDT)", "precision": 4},
        {"symbol": "USDJPY", "bx": "USDJPY-USDT", "name": "USD/JPY (USDJPY-USDT)", "precision": 2},
        {"symbol": "AUDUSDT", "bx": "AUD-USDT", "name": "AUD/USD (AUD-USDT)", "precision": 4},
        {"symbol": "USDCHF", "bx": "USDCHF-USDT", "name": "USD/CHF (USDCHF-USDT)", "precision": 4},
        {"symbol": "USDCAD", "bx": "USDCAD-USDT", "name": "USD/CAD (USDCAD-USDT)", "precision": 4},
        {"symbol": "NZDUSDT", "bx": "NZD-USDT", "name": "NZD/USD (NZD-USDT)", "precision": 4},
        {"symbol": "EURGBP", "bx": "EURGBP-USDT", "name": "EUR/GBP (EURGBP-USDT)", "precision": 4},
        {"symbol": "EURJPY", "bx": "EURJPY-USDT", "name": "EUR/JPY (EURJPY-USDT)", "precision": 2},
        {"symbol": "GBPJPY", "bx": "GBPJPY-USDT", "name": "GBP/JPY (GBPJPY-USDT)", "precision": 2},
        {"symbol": "AUDJPY", "bx": "AUDJPY-USDT", "name": "AUD/JPY (AUDJPY-USDT)", "precision": 2},
        {"symbol": "CHFJPY", "bx": "CHFJPY-USDT", "name": "CHF/JPY (CHFJPY-USDT)", "precision": 2},
    ],
    "Azioni & Indici Globali": [
        {"symbol": "AAPLUSDT", "bx": "AAPL-USDT", "name": "Apple Inc. (AAPL-USDT)", "precision": 2},
        {"symbol": "TSLAUSDT", "bx": "TSLA-USDT", "name": "Tesla Inc. (TSLA-USDT)", "precision": 2},
        {"symbol": "NVDAUSDT", "bx": "NVDA-USDT", "name": "Nvidia Corp. (NVDA-USDT)", "precision": 2},
        {"symbol": "AMZNUSDT", "bx": "AMZN-USDT", "name": "Amazon.com (AMZN-USDT)", "precision": 2},
        {"symbol": "MSFTUSDT", "bx": "MSFT-USDT", "name": "Microsoft Corp. (MSFT-USDT)", "precision": 2},
        {"symbol": "GOOGLUSDT", "bx": "GOOGL-USDT", "name": "Alphabet Inc. (GOOGL-USDT)", "precision": 2},
        {"symbol": "METAUSDT", "bx": "META-USDT", "name": "Meta Platforms (META-USDT)", "precision": 2},
        {"symbol": "NFLXUSDT", "bx": "NFLX-USDT", "name": "Netflix Inc. (NFLX-USDT)", "precision": 2},
        {"symbol": "AMDUSDT", "bx": "AMD-USDT", "name": "AMD (AMD-USDT)", "precision": 2},
        {"symbol": "INTCUSDT", "bx": "INTC-USDT", "name": "Intel Corp. (INTC-USDT)", "precision": 2},
        {"symbol": "AVGOUSDT", "bx": "AVGO-USDT", "name": "Broadcom Inc. (AVGO-USDT)", "precision": 2},
        {"symbol": "COINUSDT", "bx": "COIN-USDT", "name": "Coinbase Global (COIN-USDT)", "precision": 2},
        {"symbol": "MSTRUSDT", "bx": "MSTR-USDT", "name": "Strategy / MSTR (MSTR-USDT)", "precision": 2},
        {"symbol": "JPMUSDT", "bx": "JPM-USDT", "name": "JPMorgan Chase (JPM-USDT)", "precision": 2},
        {"symbol": "VUSDT", "bx": "V-USDT", "name": "Visa Inc. (V-USDT)", "precision": 2},
        {"symbol": "MAUSDT", "bx": "MA-USDT", "name": "Mastercard Inc. (MA-USDT)", "precision": 2},
        {"symbol": "SP500USDT", "bx": "SP500-USDT", "name": "S&P 500 Index (SP500-USDT)", "precision": 2},
        {"symbol": "NASDAQ100USDT", "bx": "NASDAQ100-USDT", "name": "NASDAQ 100 Index (NASDAQ100-USDT)", "precision": 2},
        {"symbol": "DOWJONESUSDT", "bx": "DOWJONES-USDT", "name": "Dow Jones Index (DOWJONES-USDT)", "precision": 2},
        {"symbol": "NIKKEI225USDT", "bx": "NIKKEI225-USDT", "name": "Nikkei 225 Index (NIKKEI225-USDT)", "precision": 1},
        {"symbol": "SPYUSDT", "bx": "SPY-USDT", "name": "SPDR S&P 500 ETF (SPY-USDT)", "precision": 2},
        {"symbol": "QQQUSDT", "bx": "QQQ-USDT", "name": "Invesco QQQ ETF (QQQ-USDT)", "precision": 2},
        {"symbol": "DIAUSDT", "bx": "DIA-USDT", "name": "SPDR Dow Jones ETF (DIA-USDT)", "precision": 2},
        {"symbol": "IWMUSDT", "bx": "IWM-USDT", "name": "iShares Russell 2000 ETF (IWM-USDT)", "precision": 2},
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
        if isinstance(val, str):
            val = val.replace(",", ".").strip()
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


def _decimals_from_step(step_val: float, fallback: int = 2) -> int:
    s = safe_float(step_val, 0.0)
    if s <= 0:
        return max(0, int(fallback))
    if s >= 1.0:
        return 0
    txt = f"{s:.10f}".rstrip("0").rstrip(".")
    if "." in txt:
        return len(txt.split(".")[1])
    return max(0, int(fallback))


def get_bingx_contract_spec(bx_symbol: str, default_price_dec: int = 2):
    now_ts = time.time()
    if (now_ts - _CONTRACT_SPECS_CACHE["ts"]) > 300.0:
        try:
            resp = requests.get(f"{get_bingx_base_url()}/openApi/swap/v2/quote/contracts", timeout=2.5)
            if resp.status_code == 200:
                rows = (resp.json() or {}).get("data") or []
                parsed = {}
                for item in rows:
                    if isinstance(item, dict) and item.get("symbol"):
                        sym_key = str(item["symbol"]).upper().strip()
                        p_step = safe_float(item.get("pricePrecision") or item.get("tickSize"), 0.0)
                        q_step = safe_float(item.get("quantityPrecision") or item.get("stepSize"), 0.0)
                        min_q = safe_float(item.get("minQty") or item.get("tradeMinQuantity"), 0.0)
                        parsed[sym_key] = {
                            "price_dec": int(p_step) if 0 < p_step <= 8 else _decimals_from_step(p_step, default_price_dec),
                            "qty_step": q_step if q_step > 0 else None,
                            "min_qty": min_q if min_q > 0 else None,
                        }
                if parsed:
                    _CONTRACT_SPECS_CACHE["specs"] = parsed
                    _CONTRACT_SPECS_CACHE["ts"] = now_ts
        except Exception:
            pass

    spec = _CONTRACT_SPECS_CACHE["specs"].get(str(bx_symbol or "").upper().strip())
    if spec:
        return spec
    return {"price_dec": max(0, int(default_price_dec)), "qty_step": None, "min_qty": None}


def quantize_to_tick(value: float, decimals: int) -> float:
    return round(float(value), max(0, int(decimals)))


def get_lot_step(bx_symbol: str, price: float) -> float:
    spec = get_bingx_contract_spec(bx_symbol, 2)
    if spec.get("qty_step"):
        q_val = safe_float(spec["qty_step"])
        if 0 < q_val <= 100:
            return q_val if q_val < 1 else float(int(q_val))
    if "BTC" in bx_symbol:
        return 0.0001
    if "ETH" in bx_symbol or "PAXG" in bx_symbol:
        return 0.001
    if price >= 50:
        return 0.01
    if price >= 1:
        return 0.1
    return 1.0


def compute_dynamic_leverage(symbol: str, category: str, balance: float, price: float = 0.0, profile: str = "pro_sniper") -> int:
    cap = max(MIN_BINGX_CAPITAL_USD, safe_float(balance, MIN_BINGX_CAPITAL_USD))
    bx_symbol, _ = resolve_bingx_symbol(symbol)
    step = get_lot_step(bx_symbol, price) if price > 0 else 0.01
    min_notional = max(2.0, step * max(price, 0.0)) if price > 0 else 2.0

    if cap <= 2.50 or (min_notional > cap * 10.0):
        base_lev = 20
    elif cap <= 5.0 and profile == "momentum_desk":
        base_lev = 20
    elif symbol in ("BTCUSDT", "ETHUSDT") or category == "Criptovalute Globali":
        base_lev = 10
    else:
        base_lev = 5

    needed_lev = int(math.ceil((min_notional * 1.05) / max(cap, MIN_BINGX_CAPITAL_USD)))
    return max(base_lev, min(25, needed_lev))


def compute_order_quantity(bx_symbol: str, price: float, raw_qty: float, balance: float, leverage: int) -> float:
    if price <= 0:
        return 0.0
    eff_balance = max(MIN_BINGX_CAPITAL_USD, safe_float(balance, MIN_BINGX_CAPITAL_USD))
    eff_lev = max(1, int(leverage or 1))
    step = get_lot_step(bx_symbol, price)

    max_notional = eff_balance * eff_lev * 0.92
    desired_notional = max(raw_qty * price, min(eff_balance * eff_lev * 0.50, max_notional))
    capped_notional = min(desired_notional, max_notional)
    capped_qty = max(step, capped_notional / price)

    qty = math.floor(capped_qty / step) * step
    if qty < step:
        qty = step
    dec = len(str(step).split(".")[1]) if "." in str(step) else 0
    return round(qty, dec)


def get_timeframe_candle_timing(timeframe: str, now_ts: float = None):
    tf = (timeframe or "15m").strip()
    tf_map = {
        "5m": (300, "5 minuti (300 secondi)"),
        "15m": (900, "15 minuti (900 secondi)"),
        "1h": (3600, "1 ora (3600 secondi)"),
    }
    duration_sec, label = tf_map.get(tf, (900, "15 minuti (900 secondi)"))
    ts = float(now_ts if now_ts is not None else time.time())
    bucket_start = int(ts // duration_sec) * duration_sec
    bucket_end = bucket_start + duration_sec
    remaining_sec = max(0, int(bucket_end - ts))

    open_dt = datetime.fromtimestamp(bucket_start, tz=timezone.utc)
    close_dt = datetime.fromtimestamp(bucket_end, tz=timezone.utc)
    signal_dt = datetime.fromtimestamp(ts, tz=timezone.utc)

    return {
        "timeframe": tf,
        "candle_duration_sec": duration_sec,
        "candle_duration_label": label,
        "candle_open_ts": bucket_start,
        "candle_close_ts": bucket_end,
        "candle_remaining_sec": remaining_sec,
        "candle_open_utc": open_dt.strftime("%H:%M:%S UTC"),
        "candle_close_utc": close_dt.strftime("%H:%M:%S UTC"),
        "signal_opened_at_utc": signal_dt.strftime("%H:%M:%S UTC"),
        "candle_window_text": f"Candela {label} • Apertura {open_dt.strftime('%H:%M:%S')} → Chiusura {close_dt.strftime('%H:%M:%S UTC')}",
    }


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


def sign_bingx_query(params: dict, api_secret: str) -> str:
    query_str = urlencode(sorted(params.items()))
    sig = hmac.new(api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_str}&signature={sig}"


def bingx_signed_request(method: str, path: str, params: dict):
    global BINGX_BASE_URL, BINGX_API_KEY, BINGX_API_SECRET
    base_url = get_bingx_base_url()
    api_key, api_secret = get_bingx_credentials()
    BINGX_BASE_URL, BINGX_API_KEY, BINGX_API_SECRET = base_url, api_key, api_secret
    if not (api_key and api_secret):
        return None, "Configura BINGX_API_KEY e BINGX_SECRET_KEY su Render"

    now_sec = time.time()
    with STATE_LOCK:
        rl_until = safe_float(STATE.get("bingx_rate_limit_until"), 0.0)
        if now_sec < rl_until:
            rem = int(max(1, rl_until - now_sec))
            return None, f"Rate limit BingX attivo: riprovo tra {rem} secondi"

    req_params = dict(params or {})
    req_params["timestamp"] = int(now_sec * 1000)
    signed_qs = sign_bingx_query(req_params, api_secret)
    url = f"{base_url}{path}?{signed_qs}"
    headers = {"X-BX-APIKEY": api_key}
    try:
        if method.upper() == "POST":
            resp = requests.post(url, headers=headers, timeout=3.5)
        else:
            resp = requests.get(url, headers=headers, timeout=3.5)
        data = resp.json()
        err_str = str(data)
        if "100410" in err_str:
            with STATE_LOCK:
                STATE["bingx_rate_limit_until"] = time.time() + 35.0
            return None, "code:100410: Limite frequenza BingX raggiunto (pausa 35 secondi)"
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {data}"
        if isinstance(data, dict) and data.get("code") not in (0, "0", None):
            return None, str(data.get("msg") or data)
        return data, None
    except Exception as exc:
        return None, str(exc)


def set_bingx_leverage(bx_symbol: str, pos_side: str, leverage: int):
    lev_val = max(1, min(25, int(leverage or 20)))
    return bingx_signed_request(
        "POST",
        "/openApi/swap/v2/trade/leverage",
        {
            "symbol": bx_symbol,
            "side": pos_side,
            "leverage": lev_val,
        },
    )


def place_bingx_order_with_tpsl(
    symbol: str,
    action: str,
    qty: float,
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    leverage: int = 20,
):
    bx_symbol, bx_prec = resolve_bingx_symbol(symbol)
    spec = get_bingx_contract_spec(bx_symbol, bx_prec if bx_prec is not None else 2)
    dec = int(spec.get("price_dec", bx_prec if bx_prec is not None else 2))

    is_buy = str(action or "BUY").upper() == "BUY"
    side = "BUY" if is_buy else "SELL"
    close_side = "SELL" if is_buy else "BUY"
    pos_side = "LONG" if is_buy else "SHORT"

    lev_data, lev_err = set_bingx_leverage(bx_symbol, pos_side, leverage)

    sl_q = quantize_to_tick(safe_float(sl_price, 0.0), dec) if safe_float(sl_price, 0.0) > 0 else 0.0
    tp_q = quantize_to_tick(safe_float(tp_price, 0.0), dec) if safe_float(tp_price, 0.0) > 0 else 0.0

    entry_params = {
        "symbol": bx_symbol,
        "side": side,
        "positionSide": pos_side,
        "type": "MARKET",
        "quantity": qty,
    }
    if sl_q > 0:
        entry_params["stopLoss"] = json.dumps({
            "type": "STOP_MARKET",
            "stopPrice": sl_q,
            "workingType": "MARK_PRICE",
        })
    if tp_q > 0:
        entry_params["takeProfit"] = json.dumps({
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp_q,
            "workingType": "MARK_PRICE",
        })

    entry_data, entry_err = bingx_signed_request("POST", "/openApi/swap/v2/trade/order", entry_params)

    if entry_err and ("stopLoss" in entry_params or "takeProfit" in entry_params):
        entry_data, entry_err = bingx_signed_request(
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
    if entry_err:
        return None, entry_err

    sl_data, sl_err = None, None
    if sl_q > 0:
        for sl_type in ("STOP_MARKET", "STOP"):
            sl_data, sl_err = bingx_signed_request(
                "POST",
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": bx_symbol,
                    "side": close_side,
                    "positionSide": pos_side,
                    "type": sl_type,
                    "stopPrice": sl_q,
                    "workingType": "MARK_PRICE",
                    "quantity": qty,
                },
            )
            if not sl_err:
                break

    tp_data, tp_err = None, None
    if tp_q > 0:
        for tp_type in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
            tp_data, tp_err = bingx_signed_request(
                "POST",
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": bx_symbol,
                    "side": close_side,
                    "positionSide": pos_side,
                    "type": tp_type,
                    "stopPrice": tp_q,
                    "workingType": "MARK_PRICE",
                    "quantity": qty,
                },
            )
            if not tp_err:
                break

    return {
        "leverage_set": lev_data,
        "leverage_error": lev_err,
        "entry_order": entry_data,
        "sl_price": sl_q,
        "sl_order": sl_data,
        "sl_error": sl_err,
        "tp_price": tp_q,
        "tp_order": tp_data,
        "tp_error": tp_err,
    }, None


def close_bingx_position(symbol: str, action: str = "", size: float = 0.0):
    positions, pos_err = fetch_bingx_real_positions()
    if pos_err:
        return None, pos_err

    target_sym = canonicalize_symbol_key(symbol)
    target_act = (action or "").upper().strip()
    matches = []
    for p in positions:
        if canonicalize_symbol_key(p.get("symbol")) != target_sym:
            continue
        if target_act in ("BUY", "SELL") and p.get("action") != target_act:
            continue
        matches.append(p)

    if not matches:
        return None, f"Nessuna posizione aperta trovata per {target_sym}"

    results = []
    for pos in matches:
        qty_to_close = safe_float(size, 0.0) if safe_float(size, 0.0) > 0 else safe_float(pos.get("size"), 0.0)
        if qty_to_close <= 0:
            continue
        is_long = pos.get("action") == "BUY"
        close_side = "SELL" if is_long else "BUY"
        pos_side = "LONG" if is_long else "SHORT"
        order_data, order_err = bingx_signed_request(
            "POST",
            "/openApi/swap/v2/trade/order",
            {
                "symbol": pos.get("bx_symbol") or resolve_bingx_symbol(target_sym)[0],
                "side": close_side,
                "positionSide": pos_side,
                "type": "MARKET",
                "quantity": qty_to_close,
                "reduceOnly": "true",
            },
        )
        if order_err:
            return None, order_err
        results.append({"symbol": pos.get("bx_symbol"), "side": close_side, "qty": qty_to_close, "order": order_data})

    with STATE_LOCK:
        STATE["last_bingx_sync"] = 0.0
    sync_bingx_real_closed_trades()
    return results, None


def fetch_bingx_real_balance():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return {
            "balance": 0.0,
            "equity": 0.0,
            "available_margin": 0.0,
            "unrealized_pnl": 0.0,
            "asset": "USDT",
        }, "Configura BINGX_API_KEY e BINGX_SECRET_KEY su Render"

    payload, err = bingx_signed_request("GET", "/openApi/swap/v2/user/balance", {})
    if err or not isinstance(payload, dict):
        with STATE_LOCK:
            STATE["last_bingx_balance_error"] = err or "Errore lettura saldo BingX"
        return {
            "balance": STATE.get("last_bingx_balance", 0.0),
            "equity": STATE.get("last_bingx_equity", 0.0),
            "available_margin": STATE.get("last_bingx_available", 0.0),
            "unrealized_pnl": STATE.get("last_bingx_unrealized", 0.0),
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
        STATE["last_bingx_unrealized"] = info["unrealized_pnl"]
        STATE["last_bingx_balance_error"] = None
    return info, None


def sync_bingx_real_closed_trades():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return
    with STATE_LOCK:
        if time.time() - STATE.get("last_bingx_sync", 0.0) < 8.0:
            return
        if time.time() < safe_float(STATE.get("bingx_rate_limit_until"), 0.0):
            return
        STATE["last_bingx_sync"] = time.time()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (31 * 86400 * 1000)
    payload, err = bingx_signed_request(
        "GET",
        "/openApi/swap/v2/user/income",
        {"startTime": start_ms, "endTime": now_ms, "limit": 500},
    )
    if err or not isinstance(payload, dict):
        return

    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return

    parsed_map = {}
    for idx, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        inc_type = str(item.get("incomeType") or "").upper()
        if inc_type not in ("REALIZED_PNL", "PROFIT", "TRADE_PNL", "COMMISSION", "FUNDING_FEE"):
            continue
        amt_val = safe_float(item.get("income"), 0.0)
        if amt_val == 0.0:
            continue
        t_ms = int(safe_float(item.get("time"), now_ms))
        dt_utc = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
        raw_id = item.get("tranId") or item.get("orderId")
        trade_id = f"{raw_id}_{inc_type}" if raw_id else f"bx_{inc_type}_{t_ms}_{idx}_{round(amt_val, 6)}"
        is_fee = inc_type in ("COMMISSION", "FUNDING_FEE")
        parsed_map[trade_id] = {
            "id": trade_id,
            "symbol": canonicalize_symbol_key(item.get("symbol") or "BTCUSDT"),
            "income_type": inc_type,
            "is_fee": is_fee,
            "realized_pnl": round(amt_val, 4),
            "closed_date": dt_utc.strftime("%Y-%m-%d"),
            "closed_month": dt_utc.strftime("%Y-%m"),
            "closed_at": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "ts_ms": t_ms,
        }

    ordered_trades = sorted(parsed_map.values(), key=lambda x: x["ts_ms"], reverse=True)
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_net = round(sum(safe_float(t.get("realized_pnl")) for t in ordered_trades if t["closed_date"] == today_key), 4)

    with STATE_LOCK:
        STATE["closed_trades"] = ordered_trades
        STATE["session_pnl"] = today_net


def fetch_bingx_real_positions():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return [], "Configura BINGX_API_KEY e BINGX_SECRET_KEY su Render"
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
            "mark_price": safe_float(p.get("markPrice"), 0.0),
            "size": amt,
            "leverage": f"{int(safe_float(p.get('leverage'), 1))}x",
            "unrealized_pnl": round(safe_float(p.get("unrealizedProfit"), 0.0), 4),
        })
    return active, None


def _fetch_binance_ticker_fallback(symbol_key: str):
    clean = canonicalize_symbol_key(symbol_key)
    for base in (BINANCE_FAPI_BASE, BINANCE_SPOT_BASE):
        path = "/fapi/v1/ticker/price" if "fapi" in base else "/api/v3/ticker/price"
        try:
            resp = requests.get(f"{base}{path}", params={"symbol": clean}, timeout=1.2)
            if resp.status_code == 200:
                p = safe_float((resp.json() or {}).get("price"), 0.0)
                if p > 0:
                    return p
        except Exception:
            continue
    return None


def _fetch_binance_klines_fallback(symbol_key: str, interval: str, limit: int = 100):
    clean = canonicalize_symbol_key(symbol_key)
    tf_map = {"5m": "5m", "15m": "15m", "1h": "1h"}
    b_int = tf_map.get(interval, "15m")
    for base in (BINANCE_FAPI_BASE, BINANCE_SPOT_BASE):
        path = "/fapi/v1/klines" if "fapi" in base else "/api/v3/klines"
        try:
            resp = requests.get(
                f"{base}{path}",
                params={"symbol": clean, "interval": b_int, "limit": limit},
                timeout=1.4,
            )
            if resp.status_code == 200:
                rows = resp.json() or []
                if isinstance(rows, list) and len(rows) >= 40:
                    parsed = []
                    for r in rows[-limit:]:
                        if isinstance(r, (list, tuple)) and len(r) >= 6:
                            o = safe_float(r[1])
                            h = safe_float(r[2])
                            l = safe_float(r[3])
                            c = safe_float(r[4])
                            v = safe_float(r[5], 0.0)
                            if c > 0 and h >= l > 0:
                                parsed.append({"Open": o or c, "High": h, "Low": l, "Close": c, "Volume": max(v, 0.0)})
                    if len(parsed) >= 40:
                        return pd.DataFrame(parsed)
        except Exception:
            continue
    return None


def fetch_bingx_ticker(bx_symbol: str):
    cached = _LIVE_PRICE_CACHE.get(bx_symbol)
    if cached and (time.time() - cached["ts"]) < 2.2:
        return cached["price"]
    base_url = get_bingx_base_url()
    try:
        url = f"{base_url}/openApi/swap/v2/quote/ticker"
        resp = requests.get(url, params={"symbol": bx_symbol}, timeout=1.2)
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

    fb_price = _fetch_binance_ticker_fallback(bx_symbol)
    if fb_price and fb_price > 0:
        _LIVE_PRICE_CACHE[bx_symbol] = {"price": fb_price, "ts": time.time()}
        return fb_price
    return cached["price"] if cached else None


def fetch_bingx_klines(bx_symbol: str, interval: str, limit: int = 100):
    cache_key = f"{bx_symbol}_{interval}_{limit}"
    cached = _KLINES_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < 8.0:
        return cached["df"].copy()

    base_url = get_bingx_base_url()
    tf_map = {"5m": "5m", "15m": "15m", "1h": "1h"}
    bx_int = tf_map.get(interval, "15m")
    try:
        url = f"{base_url}/openApi/swap/v2/quote/klines"
        resp = requests.get(url, params={"symbol": bx_symbol, "interval": bx_int, "limit": limit}, timeout=1.4)
        if resp.status_code == 200:
            rows = resp.json().get("data") or []
            if isinstance(rows, list) and len(rows) >= 40:
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
                if len(parsed) >= 40:
                    df_out = pd.DataFrame(parsed)
                    _KLINES_CACHE[cache_key] = {"df": df_out.copy(), "ts": time.time()}
                    return df_out
    except Exception:
        pass

    fb_df = _fetch_binance_klines_fallback(bx_symbol, interval, limit=limit)
    if fb_df is not None and len(fb_df) >= 40:
        _KLINES_CACHE[cache_key] = {"df": fb_df.copy(), "ts": time.time()}
        return fb_df
    return cached["df"].copy() if cached else None


def get_real_market_dataframe(meta: dict, timeframe: str):
    bx_sym = meta["bx"]
    live_price = fetch_bingx_ticker(bx_sym)
    klines_df = fetch_bingx_klines(bx_sym, timeframe, limit=100)

    if klines_df is not None and len(klines_df) >= 40:
        price_out = live_price if (live_price and live_price > 0) else safe_float(klines_df["Close"].iloc[-1], 0.0)
        if price_out > 0:
            klines_df.iloc[-1, klines_df.columns.get_loc("Close")] = price_out
            klines_df.iloc[-1, klines_df.columns.get_loc("High")] = max(klines_df.iloc[-1]["High"], price_out)
            klines_df.iloc[-1, klines_df.columns.get_loc("Low")] = min(klines_df.iloc[-1]["Low"], price_out)
        return klines_df, price_out

    return None, (live_price or 0.0)


def get_higher_tf_bias(bx_symbol: str, timeframe: str) -> str:
    htf = "1h" if timeframe != "1h" else "4h"
    df_htf = fetch_bingx_klines(bx_symbol, "1h" if htf == "4h" else htf, limit=60)
    if df_htf is None or len(df_htf) < 30:
        return "NEUTRAL"
    c = df_htf["Close"]
    ema20 = safe_float(c.ewm(span=20, adjust=False).mean().iloc[-1], 0.0)
    ema50 = safe_float(c.ewm(span=50, adjust=False).mean().iloc[-1], 0.0)
    last_c = safe_float(c.iloc[-1], 0.0)
    if ema20 > ema50 and last_c >= ema20 * 0.998:
        return "BULLISH"
    if ema20 < ema50 and last_c <= ema20 * 1.002:
        return "BEARISH"
    return "NEUTRAL"


def _required_factors_for_score(min_score: float, default_factors: int) -> int:
    if min_score < 65.0:
        return 2
    if min_score < 75.0:
        return 3
    if min_score < 85.0:
        return min(4, default_factors)
    return default_factors


def build_pro_profile_config(profile: str, timeframe: str, min_score: float, min_rrr_input: float):
    p = (profile or "pro_sniper").lower()
    clean_min_score = min(98.0, max(20.0, safe_float(min_score, 70.0)))
    clean_rrr = min(10.0, max(0.5, safe_float(min_rrr_input, 1.5)))
    if p == "pro_sniper":
        return {
            "profile": "pro_sniper",
            "profile_label": "PRO SNIPER (SCORE DINAMICO)",
            "timeframe": timeframe,
            "min_score": clean_min_score,
            "required_factors": _required_factors_for_score(clean_min_score, 5),
            "min_rrr": clean_rrr,
            "max_risk_pct": 1.0,
            "sl_atr": 1.25,
            "tp1_atr": 2.60,
            "tp2_atr": 4.00,
            "tp3_atr": 5.80,
            "min_vol_ratio": 0.90,
        }
    if p == "institutional_swing":
        return {
            "profile": "institutional_swing",
            "profile_label": "STRUTTURA & LIQUIDITÀ",
            "timeframe": timeframe,
            "min_score": clean_min_score,
            "required_factors": _required_factors_for_score(clean_min_score, 5),
            "min_rrr": clean_rrr,
            "max_risk_pct": 1.5,
            "sl_atr": 1.40,
            "tp1_atr": 2.80,
            "tp2_atr": 4.40,
            "tp3_atr": 6.40,
            "min_vol_ratio": 0.85,
        }
    return {
        "profile": "momentum_desk",
        "profile_label": "MOMENTUM DESK",
        "timeframe": timeframe,
        "min_score": clean_min_score,
        "required_factors": _required_factors_for_score(clean_min_score, 4),
        "min_rrr": clean_rrr,
        "max_risk_pct": 2.0,
        "sl_atr": 1.15,
        "tp1_atr": 2.20,
        "tp2_atr": 3.50,
        "tp3_atr": 5.20,
        "min_vol_ratio": 0.80,
    }


def evaluate_pro_setup(df, current_price, symbol, category, is_open, balance, risk_pct, cfg):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    bx_symbol, bx_precision = resolve_bingx_symbol(symbol)
    spec = get_bingx_contract_spec(bx_symbol, bx_precision if bx_precision is not None else (4 if current_price < 5 else 2))
    decimals = int(spec.get("price_dec", bx_precision if bx_precision is not None else (4 if current_price < 5 else 2)))
    tick = 10 ** (-decimals)

    ema20_s = close.ewm(span=20, adjust=False).mean()
    ema50_s = close.ewm(span=50, adjust=False).mean()
    ema200_s = close.ewm(span=min(100, len(close)), adjust=False).mean()

    ema20 = safe_float(ema20_s.iloc[-1], current_price)
    ema50 = safe_float(ema50_s.iloc[-1], current_price)
    ema200 = safe_float(ema200_s.iloc[-1], current_price)

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = safe_float((100.0 - (100.0 / (1.0 + rs))).iloc[-1], 50.0)

    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    macd_curr = safe_float(hist.iloc[-1], 0.0)
    macd_prev = safe_float(hist.iloc[-2], 0.0)

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.004)
    if atr <= 0:
        atr = current_price * 0.004

    vol_curr = safe_float(volume.iloc[-1], 0.0)
    vol_avg20 = safe_float(volume.rolling(20).mean().iloc[-1], 0.0)
    vol_ratio = round(vol_curr / vol_avg20, 2) if vol_avg20 > 0 else 1.0

    swing_high_20 = safe_float(high.iloc[-21:-1].max(), current_price) if len(df) >= 22 else safe_float(high.max(), current_price)
    swing_low_20 = safe_float(low.iloc[-21:-1].min(), current_price) if len(df) >= 22 else safe_float(low.min(), current_price)

    last_open = safe_float(df["Open"].iloc[-1], current_price)
    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    body = abs(current_price - last_open)
    upper_wick = max(0.0, last_high - max(current_price, last_open))
    lower_wick = max(0.0, min(current_price, last_open) - last_low)

    sweep_high = (last_high > swing_high_20) and (current_price < swing_high_20)
    sweep_low = (last_low < swing_low_20) and (current_price > swing_low_20)
    fakeout = sweep_high or sweep_low or (upper_wick > body * 2.5 and upper_wick > atr * 0.5) or (lower_wick > body * 2.5 and lower_wick > atr * 0.5)

    htf_bias = get_higher_tf_bias(bx_symbol, cfg["timeframe"])

    bull_checks = [
        ema20 > ema50,
        current_price >= ema20 * 0.995,
        current_price >= ema200 * 0.985,
        macd_curr > 0 or macd_curr >= macd_prev,
        45.0 <= rsi <= 72.0,
        vol_ratio >= cfg["min_vol_ratio"],
        htf_bias != "BEARISH",
    ]
    bear_checks = [
        ema20 < ema50,
        current_price <= ema20 * 1.005,
        current_price <= ema200 * 1.015,
        macd_curr < 0 or macd_curr <= macd_prev,
        28.0 <= rsi <= 55.0,
        vol_ratio >= cfg["min_vol_ratio"],
        htf_bias != "BULLISH",
    ]

    bull_hits = sum(1 for x in bull_checks if x)
    bear_hits = sum(1 for x in bear_checks if x)

    trend_strength = min(30.0, (abs(ema20 - ema50) / max(ema50, 1e-9)) * 2600.0)
    mom_strength = min(22.0, abs(rsi - 50.0) * 1.15)
    vol_strength = min(18.0, (vol_ratio / max(cfg["min_vol_ratio"], 0.5)) * 13.0)
    factor_strength = (max(bull_hits, bear_hits) / 7.0) * 30.0
    raw_score = trend_strength + mom_strength + vol_strength + factor_strength
    if fakeout:
        raw_score *= 0.80
    score = round(max(10.0, min(99.0, raw_score)), 1)

    side_bias = "LONG" if bull_hits >= bear_hits else "SHORT"
    entry = quantize_to_tick(current_price, decimals)
    sl_dist = max(atr * cfg["sl_atr"], entry * 0.0025)
    tp1_dist = max(atr * cfg["tp1_atr"], sl_dist * cfg["min_rrr"])
    tp2_dist = max(atr * cfg["tp2_atr"], tp1_dist * 1.5)
    tp3_dist = max(atr * cfg["tp3_atr"], tp2_dist * 1.35)

    if side_bias == "LONG":
        sl = quantize_to_tick(max(entry - sl_dist, swing_low_20 - tick * 2) if swing_low_20 < entry and (entry - swing_low_20) <= sl_dist * 1.35 else entry - sl_dist, decimals)
        sl_dist = max(abs(entry - sl), tick * 2)
        tp1 = quantize_to_tick(entry + max(tp1_dist, sl_dist * cfg["min_rrr"]), decimals)
        tp2 = quantize_to_tick(entry + max(tp2_dist, sl_dist * (cfg["min_rrr"] * 1.5)), decimals)
        tp3 = quantize_to_tick(entry + max(tp3_dist, sl_dist * (cfg["min_rrr"] * 2.2)), decimals)
    else:
        sl = quantize_to_tick(min(entry + sl_dist, swing_high_20 + tick * 2) if swing_high_20 > entry and (swing_high_20 - entry) <= sl_dist * 1.35 else entry + sl_dist, decimals)
        sl_dist = max(abs(sl - entry), tick * 2)
        tp1 = quantize_to_tick(entry - max(tp1_dist, sl_dist * cfg["min_rrr"]), decimals)
        tp2 = quantize_to_tick(entry - max(tp2_dist, sl_dist * (cfg["min_rrr"] * 1.5)), decimals)
        tp3 = quantize_to_tick(entry - max(tp3_dist, sl_dist * (cfg["min_rrr"] * 2.2)), decimals)

    tp1_dist = abs(tp1 - entry)
    rrr = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    eff_balance = max(MIN_BINGX_CAPITAL_USD, safe_float(balance, MIN_BINGX_CAPITAL_USD))
    lev = compute_dynamic_leverage(symbol, category, eff_balance, entry, cfg["profile"])

    eff_risk_pct = min(risk_pct, cfg["max_risk_pct"])
    risk_usd = max(eff_balance * (eff_risk_pct / 100.0), eff_balance * 0.10)
    qty = compute_order_quantity(bx_symbol, entry, risk_usd / sl_dist, eff_balance, lev)
    margin_usd = round((qty * entry) / max(lev, 1), 2)
    fee_usd = round((qty * entry) * 0.001, 4)

    certainty_pct = round(min(96.0, max(35.0, (max(bull_hits, bear_hits) / 7.0) * 100.0)), 1)
    win_prob = min(0.76, max(0.38, (score / 100.0) * 0.72))
    loss_prob = 1.0 - win_prob
    net_win = round((qty * tp1_dist) - fee_usd, 4)
    net_loss = round((qty * sl_dist) + fee_usd, 4)
    ev_usd = round((win_prob * net_win) - (loss_prob * net_loss), 4)
    kelly_pct = round(max(0.0, min(15.0, ((win_prob * max(rrr, 0.5)) - loss_prob) / max(rrr, 0.5) * 100.0)), 1)

    tf_min = 5 if cfg["timeframe"] == "5m" else (60 if cfg["timeframe"] == "1h" else 15)
    candle_move = max(abs(close.diff().tail(6)).mean(), atr * 0.2, entry * 0.0002)
    candles_est = max(1, int(math.ceil(tp1_dist / candle_move)))
    eta_min = candles_est * tf_min
    eta_utc = datetime.fromtimestamp(time.time() + eta_min * 60, tz=timezone.utc).strftime("%H:%M UTC")

    clean_sym = canonicalize_symbol_key(symbol)
    now_ts = time.time()
    prev_dir = _LAST_CONFIRMED_DIRECTION.get(clean_sym)

    raw_action = "NEUTRAL"
    if bull_hits >= cfg["required_factors"] and bull_hits > bear_hits and score >= cfg["min_score"] and rrr >= cfg["min_rrr"] and is_open:
        raw_action = "BUY"
    elif bear_hits >= cfg["required_factors"] and bear_hits > bull_hits and score >= cfg["min_score"] and rrr >= cfg["min_rrr"] and is_open:
        raw_action = "SELL"

    action = raw_action
    reason = f"Setup {action} validato (Score {score}% >= {cfg['min_score']}%) • Leva {lev}x • R:R 1:{rrr}"
    grade = "A+" if (score >= max(85.0, cfg["min_score"]) and rrr >= 2.0 and not fakeout) else ("CERTO" if (action in ("BUY", "SELL") and certainty_pct >= 70.0) else ("VALIDATO" if action in ("BUY", "SELL") else "NO-TRADE"))

    if action in ("BUY", "SELL") and prev_dir:
        if prev_dir.get("action") != action and (now_ts - safe_float(prev_dir.get("ts"), 0.0)) < DIRECTION_COOLDOWN_SEC:
            rem = int(DIRECTION_COOLDOWN_SEC - (now_ts - safe_float(prev_dir.get("ts"), 0.0)))
            action = "NEUTRAL"
            reason = f"Anti-flip-flop attivo ({rem} secondi residui dopo {prev_dir.get('action')})"
    elif action == "NEUTRAL":
        if not is_open:
            reason = f"Mercato {category} chiuso"
        elif rrr < cfg["min_rrr"]:
            reason = f"R:R 1:{rrr} inferiore al minimo 1:{cfg['min_rrr']}"
        elif score < cfg["min_score"]:
            reason = f"Score {score}% sotto soglia {cfg['min_score']}% ({max(bull_hits, bear_hits)}/7 fattori)"
        else:
            reason = f"In attesa di confluenza direzionale netta ({bull_hits}L / {bear_hits}S)"

    if action in ("BUY", "SELL"):
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": action, "ts": now_ts}

    candle_timing = get_timeframe_candle_timing(cfg["timeframe"], now_ts)

    return {
        "category": category,
        "symbol": clean_sym,
        "bingx_symbol": bx_symbol,
        "name": ASSET_LOOKUP.get(clean_sym, {}).get("name", f"{clean_sym} ({bx_symbol})"),
        "is_open": is_open,
        "price": entry,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rrr": rrr,
        "score": score,
        "certainty_pct": certainty_pct,
        "kelly_pct": kelly_pct,
        "grade": grade,
        "action": action,
        "taken": action in ("BUY", "SELL"),
        "side_bias": side_bias,
        "htf_bias": htf_bias,
        "rsi": round(rsi, 1),
        "vol_ratio": vol_ratio,
        "confluence": f"{max(bull_hits, bear_hits)}/7",
        "fakeout": fakeout,
        "fakeout_label": "FAKEOUT / SWEEP" if fakeout else "STRUTTURA PULITA",
        "size": qty,
        "leverage": f"{lev}x",
        "leverage_num": lev,
        "margin": margin_usd,
        "ev_net_usd": ev_usd,
        "win_prob_pct": round(win_prob * 100, 1),
        "tp_eta_text": f"circa {eta_min} minuti ({candles_est} candele da {cfg['timeframe']}, arrivo stimato {eta_utc})",
        "reason": reason,
        "swing_high": quantize_to_tick(swing_high_20, decimals),
        "swing_low": quantize_to_tick(swing_low_20, decimals),
        "ema20": quantize_to_tick(ema20, decimals),
        "ema50": quantize_to_tick(ema50, decimals),
        **candle_timing,
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
        trade_items = [t for t in items if not t.get("is_fee")]
        wins = [t for t in trade_items if safe_float(t.get("realized_pnl")) > 0]
        losses = [t for t in trade_items if safe_float(t.get("realized_pnl")) < 0]
        total = len(trade_items)
        w_cnt = len(wins)
        l_cnt = len(losses)
        win_pct = round((w_cnt / total) * 100.0, 0) if total > 0 else 0.0
        loss_pct = round((l_cnt / total) * 100.0, 0) if total > 0 else 0.0
        won_amt = round(sum(safe_float(t.get("realized_pnl")) for t in wins), 4)
        lost_amt = round(sum(abs(safe_float(t.get("realized_pnl"))) for t in losses), 4)
        fees_amt = round(sum(safe_float(t.get("realized_pnl")) for t in items if t.get("is_fee")), 4)
        gross_pnl = round(sum(safe_float(t.get("realized_pnl")) for t in trade_items), 4)
        net_pnl = round(sum(safe_float(t.get("realized_pnl")) for t in items), 4)
        return {
            "total_trades": total,
            "wins": w_cnt,
            "losses": l_cnt,
            "win_pct": win_pct,
            "loss_pct": loss_pct,
            "won_amount": won_amt,
            "lost_amount": lost_amt,
            "fees_pnl": fees_amt,
            "gross_pnl": gross_pnl,
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


def maybe_execute_pro_autotrade(signals_out, live_positions, daily_net_pnl):
    with STATE_LOCK:
        enabled = bool(STATE.get("auto_trade"))
        only_a_plus = bool(STATE.get("only_a_plus"))
        max_pos = int(STATE.get("max_open_positions", 1))
        daily_cap = -abs(safe_float(STATE.get("daily_loss_cap_usd"), 0.50))
        rl_until = safe_float(STATE.get("bingx_rate_limit_until"), 0.0)
        avail_bal = safe_float(STATE.get("last_bingx_available") or STATE.get("last_bingx_balance"), 0.0)

    if not enabled or not signals_out or time.time() < rl_until:
        return []
    if daily_net_pnl <= daily_cap:
        return []
    if len(live_positions) >= max_pos:
        return []

    active_syms = {canonicalize_symbol_key(p.get("symbol")) for p in live_positions}
    now_ts = time.time()
    executed = []

    for sig in signals_out:
        if sig.get("action") not in ("BUY", "SELL"):
            continue
        if only_a_plus and sig.get("grade") not in ("A+", "CERTO"):
            continue
        sym = sig["symbol"]
        if sym in active_syms:
            continue
        if (now_ts - _AUTOTRADE_LAST_EXEC.get(sym, 0.0)) < 120.0:
            continue

        entry_p = safe_float(sig.get("price") or sig.get("entry"), 0.0)
        lev_num = int(safe_float(sig.get("leverage_num"), 20))
        eff_cap = max(MIN_BINGX_CAPITAL_USD, avail_bal if avail_bal >= MIN_BINGX_CAPITAL_USD else safe_float(sig.get("margin"), MIN_BINGX_CAPITAL_USD) * max(1, lev_num))
        qty = compute_order_quantity(
            sig.get("bingx_symbol") or resolve_bingx_symbol(sym)[0],
            entry_p,
            safe_float(sig.get("size"), 0.0),
            eff_cap,
            lev_num,
        )
        if qty <= 0:
            continue

        exec_res, err = place_bingx_order_with_tpsl(
            symbol=sym,
            action=sig["action"],
            qty=qty,
            sl_price=safe_float(sig.get("sl"), 0.0),
            tp_price=safe_float(sig.get("tp1"), 0.0),
            leverage=lev_num,
        )
        _AUTOTRADE_LAST_EXEC[sym] = now_ts
        if not err:
            executed.append({
                "symbol": sig["bingx_symbol"],
                "action": sig["action"],
                "qty": qty,
                "leverage": f"{lev_num}x",
                "sl": (exec_res or {}).get("sl_price") or sig.get("sl"),
                "tp1": (exec_res or {}).get("tp_price") or sig.get("tp1"),
                "sl_error": (exec_res or {}).get("sl_error"),
                "tp_error": (exec_res or {}).get("tp_error"),
                "candle_duration_sec": sig.get("candle_duration_sec"),
                "candle_open_utc": sig.get("candle_open_utc"),
                "candle_close_utc": sig.get("candle_close_utc"),
                "order": exec_res,
            })
            break
        with STATE_LOCK:
            STATE["last_bingx_balance_error"] = err
    return executed


@app.route("/")
def index():
    return render_template("index.html", market_catalog=MARKET_CATALOG)


@app.route("/api/bingx/live-account", methods=["GET"])
def api_bingx_live_account():
    api_key, api_secret = get_bingx_credentials()
    bal_info, bal_err = fetch_bingx_real_balance()
    positions, pos_err = fetch_bingx_real_positions()
    sync_bingx_real_closed_trades()
    with STATE_LOCK:
        session_pnl = STATE["session_pnl"]
        auto_trade = bool(STATE["auto_trade"])
        risk_cfg = {
            "only_a_plus": bool(STATE["only_a_plus"]),
            "max_open_positions": int(STATE["max_open_positions"]),
            "daily_loss_cap_usd": float(STATE["daily_loss_cap_usd"]),
        }
    return jsonify({
        "success": bool(api_key and api_secret) and not (bal_err and pos_err),
        "mode": "BINGX PRO DESK",
        "api_configured": bool(api_key and api_secret),
        "auto_trade": auto_trade,
        "risk_config": risk_cfg,
        "account": bal_info,
        "balance_error": bal_err,
        "positions": positions,
        "positions_error": pos_err,
        "session_pnl": session_pnl,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


@app.route("/api/bingx/close-position", methods=["POST"])
def api_bingx_close_position():
    body = request.get_json(silent=True) or {}
    sym = body.get("symbol", "")
    act = body.get("action", "")
    sz = safe_float(body.get("size"), 0.0)
    res, err = close_bingx_position(sym, act, sz)
    if err:
        return jsonify({"success": False, "error": err}), 400
    return jsonify({"success": True, "closed": res})


@app.route("/api/bingx/close-all", methods=["POST"])
def api_bingx_close_all():
    positions, pos_err = fetch_bingx_real_positions()
    if pos_err:
        return jsonify({"success": False, "error": pos_err}), 400
    closed_all = []
    for p in positions:
        res, err = close_bingx_position(p.get("symbol"), p.get("action"), p.get("size"))
        if err:
            return jsonify({"success": False, "error": err, "closed": closed_all}), 400
        if res:
            closed_all.extend(res)
    return jsonify({"success": True, "closed_count": len(closed_all), "closed": closed_all})


@app.route("/api/live-ticker", methods=["GET"])
def api_live_ticker():
    sym = canonicalize_symbol_key(request.args.get("symbol", "BTCUSDT"))
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0]})
    live_p = fetch_bingx_ticker(meta["bx"])
    if not live_p or live_p <= 0:
        return jsonify({"error": f"Prezzo live non disponibile per {meta['bx']}"}), 503
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
    tf = (request.args.get("tf") or request.args.get("timeframe") or "15m").strip()
    if tf not in ("5m", "15m", "1h"):
        tf = "15m"
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0], "precision": 2})
    df, curr_price = get_real_market_dataframe(meta, tf)
    timing = get_timeframe_candle_timing(tf, time.time())
    if df is None or df.empty:
        return jsonify({
            "symbol": sym,
            "bingx_symbol": meta["bx"],
            "timeframe": tf,
            "price": curr_price or 0.0,
            "candles": [],
            **timing,
        }), 200

    ema20_s = df["Close"].ewm(span=20, adjust=False).mean()
    ema50_s = df["Close"].ewm(span=50, adjust=False).mean()
    tail_df = df.tail(45).copy()
    tail_ema20 = ema20_s.tail(45)
    tail_ema50 = ema50_s.tail(45)

    candles = [
        {
            "open": safe_float(row.get("Open")),
            "high": safe_float(row.get("High")),
            "low": safe_float(row.get("Low")),
            "close": safe_float(row.get("Close")),
            "volume": safe_float(row.get("Volume")),
            "ema20": safe_float(tail_ema20.loc[idx]),
            "ema50": safe_float(tail_ema50.loc[idx]),
        }
        for idx, row in tail_df.iterrows()
    ]
    return jsonify({
        "symbol": sym,
        "bingx_symbol": meta["bx"],
        "timeframe": tf,
        "price": curr_price,
        "candles": candles,
        **timing,
    }), 200


@app.route("/api/signals", methods=["GET"])
def get_signals():
    profile = request.args.get("profile", "pro_sniper")
    timeframe = request.args.get("timeframe", "15m")
    if timeframe not in ("5m", "15m", "1h"):
        timeframe = "15m"
    balance = max(MIN_BINGX_CAPITAL_USD, safe_float(request.args.get("balance"), MIN_BINGX_CAPITAL_USD))
    risk_pct = min(10.0, max(0.25, safe_float(request.args.get("risk"), 1.0)))
    min_score = min(98.0, max(20.0, safe_float(request.args.get("min_score"), 70.0)))
    min_rrr = min(10.0, max(0.5, safe_float(request.args.get("min_rrr"), 1.5)))
    market_filter = request.args.get("market", "ALL")
    symbol_filter = request.args.get("symbol", "ALL")

    cfg = build_pro_profile_config(profile, timeframe, min_score, min_rrr)
    sessions = get_session_statuses()
    candle_timing = get_timeframe_candle_timing(timeframe, time.time())

    tasks = []
    for category, assets in MARKET_CATALOG.items():
        sess = sessions.get(category, {"status": "APERTO (Live)", "is_open": True, "hours": "24/7"})
        is_open = bool(sess.get("is_open", True))
        for meta in assets:
            if market_filter != "ALL" and category != market_filter:
                continue
            if symbol_filter != "ALL" and meta["symbol"] != symbol_filter:
                continue
            tasks.append((category, is_open, meta))

    monitoring_rows = []
    signals_out = []

    def _worker(cat, open_flag, m_item):
        df_m, p_m = get_real_market_dataframe(m_item, timeframe)
        if df_m is None or p_m <= 0:
            return None
        return evaluate_pro_setup(df_m, p_m, m_item["symbol"], cat, open_flag, balance, risk_pct, cfg)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(_worker, c, o, m) for (c, o, m) in tasks]
        for fut in as_completed(futs, timeout=12.0):
            try:
                ev = fut.result(timeout=2.0)
                if ev:
                    monitoring_rows.append(ev)
                    if ev["taken"]:
                        signals_out.append(ev)
            except Exception:
                pass

    monitoring_rows.sort(key=lambda r: (1 if r.get("taken") else 0, safe_float(r.get("score"))), reverse=True)
    signals_out.sort(key=lambda s: safe_float(s.get("score")), reverse=True)

    live_positions, _ = fetch_bingx_real_positions()
    perf_payload = build_performance_payload()
    daily_net = safe_float((perf_payload.get("daily") or {}).get("net_pnl"), 0.0)
    autotrade_executed = maybe_execute_pro_autotrade(signals_out, live_positions, daily_net)

    market_diagnostics = {}
    for category in MARKET_CATALOG.keys():
        sess = sessions.get(category, {"status": "APERTO (Live)", "is_open": True, "hours": "24/7"})
        cat_rows = [r for r in monitoring_rows if r.get("category") == category]
        cat_sigs = [r for r in cat_rows if r.get("taken")]
        certain_cnt = sum(1 for r in cat_rows if r.get("grade") in ("CERTO", "A+"))
        avg_sc = round(sum(safe_float(r.get("score")) for r in cat_rows) / len(cat_rows), 1) if cat_rows else 0.0
        top_r = cat_rows[0] if cat_rows else None
        top_txt = f" • Top: {top_r['symbol']} ({top_r['score']}% {top_r['grade']})" if top_r else ""
        if not sess.get("is_open", True):
            reason = f"Mercato chiuso ({sess.get('hours')})."
        elif cat_sigs:
            reason = f"{len(cat_sigs)} setup validati (score >= {cfg['min_score']}%) su {category}{top_txt}."
        else:
            reason = f"Nessun setup con score >= {cfg['min_score']}% e R:R 1:{cfg['min_rrr']}{top_txt}."
        market_diagnostics[category] = {
            "is_open": bool(sess.get("is_open", True)),
            "status": sess.get("status", "APERTO"),
            "scanned": len(cat_rows),
            "signals_count": len(cat_sigs),
            "certain_count": certain_cnt,
            "market_score": avg_sc,
            "reason": reason,
        }

    with STATE_LOCK:
        STATE["scans_count"] += 1
        scans_val = STATE["scans_count"]
        auto_trade_val = bool(STATE["auto_trade"])
        STATE["telemetry_logs"] = ([{
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "status": f"PRO DESK {timeframe} ({cfg['profile_label']})",
            "signals_found": len(signals_out),
            "scanned": len(monitoring_rows),
        }] + STATE["telemetry_logs"])[:40]
        logs_copy = list(STATE["telemetry_logs"])

    return jsonify({
        "sessions": sessions,
        "signals": signals_out,
        "active_params": cfg,
        "candle_timing": candle_timing,
        "auto_trade": auto_trade_val,
        "autotrade_executed": autotrade_executed,
        "market_diagnostics": market_diagnostics,
        "market_monitoring_table": monitoring_rows,
        "performance": perf_payload,
        "telemetry": {
            "scans_count": scans_val,
            "active_orders_count": len(live_positions),
            "session_pnl": STATE["session_pnl"],
            "performance": perf_payload,
            "telemetry_logs": logs_copy,
        },
    })


@app.route("/api/analytics/performance", methods=["GET"])
def api_analytics_performance():
    return jsonify(build_performance_payload())


@app.route("/api/autotrade/toggle", methods=["GET", "POST"])
def api_autotrade_toggle():
    body = request.get_json(silent=True) or {}
    with STATE_LOCK:
        if "enabled" in body:
            STATE["auto_trade"] = bool(body["enabled"])
        elif request.args.get("enabled") is not None:
            STATE["auto_trade"] = str(request.args.get("enabled")).lower() in ("1", "true", "on", "yes")
        else:
            STATE["auto_trade"] = not bool(STATE.get("auto_trade"))
        curr = bool(STATE["auto_trade"])
    return jsonify({"success": True, "auto_trade": curr})


@app.route("/api/autotrade/config", methods=["POST"])
def api_autotrade_config():
    body = request.get_json(silent=True) or {}
    with STATE_LOCK:
        if "only_a_plus" in body:
            STATE["only_a_plus"] = bool(body["only_a_plus"])
        if "max_open_positions" in body:
            STATE["max_open_positions"] = max(1, min(10, int(safe_float(body["max_open_positions"], 1))))
        if "daily_loss_cap_usd" in body:
            STATE["daily_loss_cap_usd"] = max(0.05, min(500.0, safe_float(body["daily_loss_cap_usd"], 0.50)))
        cfg_out = {
            "only_a_plus": bool(STATE["only_a_plus"]),
            "max_open_positions": int(STATE["max_open_positions"]),
            "daily_loss_cap_usd": float(STATE["daily_loss_cap_usd"]),
        }
    return jsonify({"success": True, "risk_config": cfg_out})


@app.route("/api/autotrade/execute-manual", methods=["POST"])
def api_execute_manual():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return jsonify({
            "success": False,
            "error": "Imposta BINGX_API_KEY e BINGX_SECRET_KEY nelle variabili d'ambiente su Render.",
        }), 400

    body = request.get_json(silent=True) or {}
    sym = canonicalize_symbol_key(body.get("symbol", "BTCUSDT"))
    bx_symbol, _ = resolve_bingx_symbol(sym)
    action = (body.get("action") or "BUY").upper()
    if action not in ("BUY", "SELL"):
        action = "BUY"

    curr_p = fetch_bingx_ticker(bx_symbol) or safe_float(body.get("entry"), 0.0)
    with STATE_LOCK:
        avail_bal = safe_float(STATE.get("last_bingx_available") or STATE.get("last_bingx_balance"), MIN_BINGX_CAPITAL_USD)
    req_bal = max(MIN_BINGX_CAPITAL_USD, safe_float(body.get("balance"), avail_bal))
    lev_num = int(safe_float(body.get("leverage"), compute_dynamic_leverage(sym, "Criptovalute Globali", req_bal, curr_p)))

    raw_size = safe_float(body.get("size"), 0.0)
    qty = compute_order_quantity(bx_symbol, curr_p, raw_size, req_bal, lev_num)
    if qty <= 0:
        return jsonify({"success": False, "error": "Quantità ordine non valida per il saldo/prezzo corrente."}), 400

    sl_p = safe_float(body.get("sl"), 0.0)
    tp_p = safe_float(body.get("tp1"), 0.0)
    tf = (body.get("timeframe") or "15m").strip()
    timing = get_timeframe_candle_timing(tf, time.time())

    exec_res, err = place_bingx_order_with_tpsl(
        symbol=sym,
        action=action,
        qty=qty,
        sl_price=sl_p,
        tp_price=tp_p,
        leverage=lev_num,
    )
    if err:
        return jsonify({"success": False, "error": f"Errore BingX ordine reale: {err}"}), 400

    return jsonify({
        "success": True,
        "mode": "BINGX PRO DESK",
        "symbol": bx_symbol,
        "qty": qty,
        "leverage": f"{lev_num}x",
        "sl": (exec_res or {}).get("sl_price"),
        "tp1": (exec_res or {}).get("tp_price"),
        "sl_error": (exec_res or {}).get("sl_error"),
        "tp_error": (exec_res or {}).get("tp_error"),
        "candle_timing": timing,
        "order": exec_res,
    })


@app.route("/api/autotrade/status", methods=["GET"])
def api_autotrade_status():
    api_key, api_secret = get_bingx_credentials()
    positions, pos_err = fetch_bingx_real_positions()
    perf = build_performance_payload()
    with STATE_LOCK:
        closed_copy = list(STATE["closed_trades"])
        auto_trade_val = bool(STATE["auto_trade"])
    return jsonify({
        "auto_trade": auto_trade_val,
        "mode": "BINGX PRO DESK",
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
            "auto_trade": bool(STATE["auto_trade"]),
            "account": bal_info,
            "performance": perf,
            "telemetry_logs": list(STATE["telemetry_logs"]),
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
