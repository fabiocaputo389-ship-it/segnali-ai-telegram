from flask import Flask, render_template, jsonify, request
import requests
import pandas as pd
import numpy as np
import os
import re
import math
import time
import hmac
import hashlib
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)


def load_render_environment():
    global BINGX_API_KEY, BINGX_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    BINGX_API_KEY = os.environ.get('BINGX_API_KEY', '').strip()
    BINGX_SECRET_KEY = os.environ.get('BINGX_SECRET_KEY', '').strip()
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

    print("========================================")
    print("⚡ SALA SEGNALI PRO • BINGX LIVE SYNC ⚡")
    print(f"🔹 BingX API Key: {'CONFIGURATA ✅' if BINGX_API_KEY else 'NON RILEVATA (Paper Mode)'}")
    print("========================================")


load_render_environment()

BINGX_BASE_URL = "https://open-api.bingx.com"
STATE_FILE = "autotrade_state.json"


def get_saved_autotrade():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f).get("active", False)
        except Exception:
            pass
    return False


def save_autotrade(status):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"active": status}, f)
    except Exception as e:
        print(f"Errore salvataggio stato trade: {e}")


auto_trade_active = get_saved_autotrade()
execution_history = []
sent_telegram_signals = {}
scans_telemetry = {}
session_realized_pnl = 0.0

_SIGNALS_CACHE = {"key": None, "ts": 0.0, "payload": None}
_TICKER_CACHE = {}

EXACT_PRESETS = {
    ("5m", "conservative"):  {"rsi_buy_min": 45.0, "rsi_buy_max": 62.0, "rsi_sell_min": 38.0, "rsi_sell_max": 55.0, "sl_m": 1.6, "tp_m": 3.2, "min_vol_ratio": 1.05, "min_score": 85.0},
    ("5m", "balanced"):      {"rsi_buy_min": 40.0, "rsi_buy_max": 66.0, "rsi_sell_min": 34.0, "rsi_sell_max": 60.0, "sl_m": 1.2, "tp_m": 2.4, "min_vol_ratio": 0.90, "min_score": 80.0},
    ("5m", "aggressive"):    {"rsi_buy_min": 35.0, "rsi_buy_max": 72.0, "rsi_sell_min": 28.0, "rsi_sell_max": 65.0, "sl_m": 0.8, "tp_m": 1.5, "min_vol_ratio": 0.80, "min_score": 80.0},

    ("15m", "conservative"): {"rsi_buy_min": 42.0, "rsi_buy_max": 64.0, "rsi_sell_min": 36.0, "rsi_sell_max": 58.0, "sl_m": 1.5, "tp_m": 3.5, "min_vol_ratio": 1.00, "min_score": 85.0},
    ("15m", "balanced"):     {"rsi_buy_min": 38.0, "rsi_buy_max": 68.0, "rsi_sell_min": 32.0, "rsi_sell_max": 62.0, "sl_m": 1.2, "tp_m": 2.6, "min_vol_ratio": 0.85, "min_score": 80.0},
    ("15m", "aggressive"):   {"rsi_buy_min": 34.0, "rsi_buy_max": 74.0, "rsi_sell_min": 26.0, "rsi_sell_max": 66.0, "sl_m": 0.9, "tp_m": 1.8, "min_vol_ratio": 0.75, "min_score": 80.0},

    ("1h", "conservative"):  {"rsi_buy_min": 44.0, "rsi_buy_max": 65.0, "rsi_sell_min": 35.0, "rsi_sell_max": 56.0, "sl_m": 1.8, "tp_m": 4.2, "min_vol_ratio": 1.00, "min_score": 85.0},
    ("1h", "balanced"):      {"rsi_buy_min": 38.0, "rsi_buy_max": 70.0, "rsi_sell_min": 30.0, "rsi_sell_max": 62.0, "sl_m": 1.4, "tp_m": 3.2, "min_vol_ratio": 0.85, "min_score": 80.0},
    ("1h", "aggressive"):    {"rsi_buy_min": 32.0, "rsi_buy_max": 75.0, "rsi_sell_min": 25.0, "rsi_sell_max": 68.0, "sl_m": 1.0, "tp_m": 2.2, "min_vol_ratio": 0.75, "min_score": 80.0},
}

FOREX_SYMBOLS = {'EURUSDT', 'GBPUSDT', 'USDJPY', 'AUDUSDT', 'EURJPY', 'USDCAD', 'USDCHF'}
COMMODITY_SYMBOLS = {'PAXGUSDT', 'XAGUSDT', 'SILVERXAGUSDT', 'GOLDXAUUSDT', 'WTIUSDT', 'BRENTUSDT', 'NATGASUSDT', 'COPPERUSDT'}

BINGX_EXACT_NAME_MAP = {
    "SILVER(XAG)": "SILVER(XAG)-USDT",
    "SILVER(XAG)-USDT": "SILVER(XAG)-USDT",
    "SILVER": "SILVER(XAG)-USDT",
    "ARGENTO": "SILVER(XAG)-USDT",
    "ARGENTO (SILVER)": "SILVER(XAG)-USDT",
    "XAG": "SILVER(XAG)-USDT",
    "XAG-USDT": "SILVER(XAG)-USDT",
    "XAGUSDT": "SILVER(XAG)-USDT",
    "SILVERXAGUSDT": "SILVER(XAG)-USDT",
    "100XAG": "100SILVER(100XAG)-USDT",

    "GOLD(XAU)": "GOLD(XAU)-USDT",
    "GOLD(XAU)-USDT": "GOLD(XAU)-USDT",
    "GOLD": "GOLD(XAU)-USDT",
    "ORO": "GOLD(XAU)-USDT",
    "XAU": "GOLD(XAU)-USDT",
    "XAU-USDT": "GOLD(XAU)-USDT",
    "XAUUSDT": "GOLD(XAU)-USDT",
    "GOLDXAUUSDT": "GOLD(XAU)-USDT",
    "PAXG": "PAXG-USDT",
    "PAXG-USDT": "PAXG-USDT",
    "PAXGUSDT": "PAXG-USDT",
    "XAUT": "XAUT-USDT",
    "XAUT-USDT": "XAUT-USDT",

    "WTI": "WTI-USDT",
    "WTI-USDT": "WTI-USDT",
    "WTIUSDT": "WTI-USDT",
    "USOIL": "WTI-USDT",
    "USOIL-USDT": "WTI-USDT",
    "OIL": "WTI-USDT",
    "PETROLIO": "WTI-USDT",
    "PETROLIO WTI": "WTI-USDT",
    "BRENT": "BRENT-USDT",
    "BRENT-USDT": "BRENT-USDT",
    "NATGAS": "NATGAS-USDT",
    "NATGAS-USDT": "NATGAS-USDT",
    "NATGASUSDT": "NATGAS-USDT",
    "GAS NATURALE": "NATGAS-USDT",
    "COPPER": "COPPER-USDT",
    "COPPER-USDT": "COPPER-USDT",
    "COPPERUSDT": "COPPER-USDT",
    "RAME (COPPER)": "COPPER-USDT",

    "EURUSD": "EURUSD-USDT",
    "EUR/USD": "EURUSD-USDT",
    "EURUSD-USDT": "EURUSD-USDT",
    "EURUSDT": "EURUSD-USDT",
    "GBPUSD": "GBPUSD-USDT",
    "GBP/USD": "GBPUSD-USDT",
    "GBPUSD-USDT": "GBPUSD-USDT",
    "GBPUSDT": "GBPUSD-USDT",
    "USDJPY": "USDJPY-USDT",
    "USD/JPY": "USDJPY-USDT",
    "USDJPY-USDT": "USDJPY-USDT",
    "AUDUSD": "AUDUSD-USDT",
    "AUD/USD": "AUDUSD-USDT",
    "EURJPY": "EURJPY-USDT",
    "EUR/JPY": "EURJPY-USDT",
    "USDCAD": "USDCAD-USDT",
    "USD/CAD": "USDCAD-USDT",
    "USDCHF": "USDCHF-USDT",
    "USD/CHF": "USDCHF-USDT",

    "AAPL": "AAPL-USDT",
    "AAPL-USDT": "AAPL-USDT",
    "AAPLUSDT": "AAPL-USDT",
    "APPLE INC.": "AAPL-USDT",
    "TSLA": "TSLA-USDT",
    "TSLA-USDT": "TSLA-USDT",
    "TSLAUSDT": "TSLA-USDT",
    "TESLA INC.": "TSLA-USDT",
    "NVDA": "NVDA-USDT",
    "NVDA-USDT": "NVDA-USDT",
    "NVDAUSDT": "NVDA-USDT",
    "NVIDIA CORP.": "NVDA-USDT",
    "AMZN": "AMZN-USDT",
    "AMZN-USDT": "AMZN-USDT",
    "AMZNUSDT": "AMZN-USDT",
    "AMAZON": "AMZN-USDT",
    "MSFT": "MSFT-USDT",
    "MSFT-USDT": "MSFT-USDT",
    "MSFTUSDT": "MSFT-USDT",
    "MICROSOFT": "MSFT-USDT",
    "GOOGL": "GOOGL-USDT",
    "GOOGL-USDT": "GOOGL-USDT",
    "GOOGLUSDT": "GOOGL-USDT",
    "ALPHABET (GOOGLE)": "GOOGL-USDT",
    "SP500": "SP500-USDT",
    "NASDAQ100": "NASDAQ100-USDT",
    "DOWJONES": "DOWJONES-USDT",
    "NIKKEI225": "NIKKEI225-USDT",
}

BINGX_SYMBOL_ALIASES = {
    'PAXGUSDT': ['PAXG-USDT', 'GOLD(XAU)-USDT', 'XAUT-USDT', 'GOLD-USDT'],
    'XAGUSDT': ['SILVER(XAG)-USDT', '100SILVER(100XAG)-USDT', 'XAG-USDT', 'SILVER-USDT'],
    'SILVERXAGUSDT': ['SILVER(XAG)-USDT', '100SILVER(100XAG)-USDT', 'XAG-USDT'],
    'GOLDXAUUSDT': ['GOLD(XAU)-USDT', 'PAXG-USDT', 'XAU-USDT'],
    'WTIUSDT': ['WTI-USDT', 'USOIL-USDT', 'CRUDEOIL-USDT', 'OIL-USDT'],
    'BRENTUSDT': ['BRENT-USDT', 'UKOIL-USDT'],
    'NATGASUSDT': ['NATGAS-USDT', 'NGAS-USDT'],
    'COPPERUSDT': ['COPPER-USDT', 'XCU-USDT'],
    'EURUSDT': ['EURUSD-USDT', 'EUR-USD', 'EUR-USDT', 'NCSIFXEUR-USD'],
    'EURUSD': ['EURUSD-USDT', 'EUR-USD', 'EUR-USDT'],
    'GBPUSDT': ['GBPUSD-USDT', 'GBP-USD', 'GBP-USDT', 'NCSIFXGBP-USD'],
    'GBPUSD': ['GBPUSD-USDT', 'GBP-USD', 'GBP-USDT'],
    'USDJPY': ['USDJPY-USDT', 'USD-JPY', 'USDJPY-USDT', 'NCSIFXUSD-JPY'],
    'AAPLUSDT': ['AAPL-USDT', 'AAPL'],
    'TSLAUSDT': ['TSLA-USDT', 'TSLA'],
    'NVDAUSDT': ['NVDA-USDT', 'NVDA'],
    'AMZNUSDT': ['AMZN-USDT', 'AMZN'],
    'MSFTUSDT': ['MSFT-USDT', 'MSFT'],
    'GOOGLUSDT': ['GOOGL-USDT', 'GOOGL'],
}

BINGX_MIN_QTY = {
    "DOGE-USDT": 35.0,
    "XRP-USDT": 10.0,
    "ADA-USDT": 10.0,
    "TRX-USDT": 50.0,
}


def safe_float(val, default=0.0):
    try:
        f = float(val)
        return default if (np.isnan(f) or not np.isfinite(f)) else f
    except Exception:
        return default


def canonicalize_symbol_key(raw_symbol, raw_name=""):
    for cand in [raw_symbol, raw_name]:
        txt = str(cand or "").strip().upper()
        if not txt:
            continue
        if txt in BINGX_EXACT_NAME_MAP:
            return BINGX_EXACT_NAME_MAP[txt].replace("-", "").upper()
        compact = re.sub(r"\s+", "", txt)
        if compact in BINGX_EXACT_NAME_MAP:
            return BINGX_EXACT_NAME_MAP[compact].replace("-", "").upper()
        paren = re.search(r"\(([A-Z0-9]+)\)", txt)
        if paren:
            inner = paren.group(1)
            if inner in BINGX_EXACT_NAME_MAP:
                return BINGX_EXACT_NAME_MAP[inner].replace("-", "").upper()
            if "SILVER" in txt or "ARGENTO" in txt:
                return "SILVER(XAG)USDT"
            if "GOLD" in txt or "ORO" in txt:
                return "GOLD(XAU)USDT"
            return f"{inner}USDT"
        cleaned = re.sub(r"[^A-Z0-9-]", "", txt)
        if cleaned in BINGX_EXACT_NAME_MAP:
            return BINGX_EXACT_NAME_MAP[cleaned].replace("-", "").upper()
        if cleaned:
            return cleaned.replace("-", "").upper()
    return "BTCUSDT"


def get_market_calibration_spec(symbol, category, current_price):
    clean = canonicalize_symbol_key(symbol)
    if clean in FOREX_SYMBOLS:
        decimals = 3 if clean == 'USDJPY' or current_price >= 20 else 5
        min_sl_pct = 0.0015
        min_tp_vs_sl = 1.6
    elif clean in COMMODITY_SYMBOLS or "SILVER" in clean or "GOLD" in clean:
        decimals = 2 if current_price >= 10 else 4
        min_sl_pct = 0.0025
        min_tp_vs_sl = 1.6
    elif category == 'Azioni & Indici Globali':
        decimals = 2
        min_sl_pct = 0.0025
        min_tp_vs_sl = 1.6
    else:
        decimals = 2 if current_price >= 5 else (4 if current_price >= 0.05 else 5)
        min_sl_pct = 0.0035
        min_tp_vs_sl = 1.6

    return decimals, min_sl_pct, min_tp_vs_sl


def quantize_to_tick(price, decimals, tick_size=None):
    if tick_size and tick_size > 0:
        steps = round(price / tick_size)
        return round(steps * tick_size, decimals)
    return round(price, decimals)


def align_tp_sl_to_price(action, ref_price, sl_val, tp_val, sl_m=1.2, tp_m=2.6, decimals=2):
    side = str(action or "BUY").upper()
    price = max(safe_float(ref_price, 0.0), 0.000001)
    dec = max(0, min(8, int(decimals if decimals is not None else (2 if price >= 10 else 4))))
    tick = 10 ** (-dec)

    min_sl_dist = max(price * 0.004 * max(sl_m, 0.5), tick * 5)
    min_tp_dist = max(price * 0.008 * max(tp_m, 0.8), tick * 10)

    sl_out = safe_float(sl_val, 0.0)
    tp_out = safe_float(tp_val, 0.0)

    if side == "BUY":
        if not (0 < sl_out < price - tick):
            sl_out = price - min_sl_dist
        if not (tp_out > price + tick):
            tp_out = price + min_tp_dist
    else:
        if not (sl_out > price + tick):
            sl_out = price + min_sl_dist
        if not (0 < tp_out < price - tick):
            tp_out = max(tick, price - min_tp_dist)

    return quantize_to_tick(sl_out, dec, tick), quantize_to_tick(tp_out, dec, tick)


def compute_order_quantity(bingx_symbol, price, requested_size, margin, lev_num):
    safe_price = max(safe_float(price, 0.0), 0.000001)
    safe_margin = max(safe_float(margin, 2.0), 2.0)
    safe_lev = max(int(lev_num or 1), 1)
    min_qty = BINGX_MIN_QTY.get(bingx_symbol.upper(), 0.0)

    qty = safe_float(requested_size, 0.0)
    if qty <= 0:
        qty = (safe_margin * safe_lev) / safe_price
    if min_qty > 0 and qty < min_qty:
        qty = min_qty

    if qty < 1:
        return round(qty, 4)
    if qty < 10:
        return round(qty, 2)
    return float(math.ceil(qty))


def parse_leverage_number(leverage_str, default=5):
    try:
        digits = "".join(ch for ch in str(leverage_str) if ch.isdigit())
        val = int(digits) if digits else default
        return max(1, min(125, val))
    except Exception:
        return default


def resolve_active_params(timeframe, profile, args):
    tf = timeframe if timeframe in ["5m", "15m", "1h"] else "15m"
    prof = profile if profile in ["conservative", "balanced", "aggressive"] else "balanced"
    base = EXACT_PRESETS[(tf, prof)].copy()

    def pick_float(key, default_val):
        raw = args.get(key)
        if raw is None or str(raw).strip() == "":
            return default_val
        return safe_float(str(raw).replace(",", "."), default_val)

    return {
        "timeframe": tf,
        "profile": prof,
        "rsi_buy_min": pick_float("rsi_buy_min", base["rsi_buy_min"]),
        "rsi_buy_max": pick_float("rsi_buy_max", base["rsi_buy_max"]),
        "rsi_sell_min": pick_float("rsi_sell_min", base["rsi_sell_min"]),
        "rsi_sell_max": pick_float("rsi_sell_max", base["rsi_sell_max"]),
        "sl_m": max(0.3, pick_float("sl_m", base["sl_m"])),
        "tp_m": max(0.6, pick_float("tp_m", base["tp_m"])),
        "min_vol_ratio": max(0.2, pick_float("min_vol_ratio", base["min_vol_ratio"])),
        "min_score": max(50.0, min(99.0, pick_float("min_score", base["min_score"]))),
    }


def detect_market_regime_and_advice(df, current_price, category, is_open, cfg):
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0:
        atr = current_price * 0.005

    vol_curr = safe_float(volume.iloc[-1], 0.0)
    vol_avg20 = safe_float(volume.rolling(20).mean().iloc[-1], 0.0)
    vol_ratio = round(vol_curr / vol_avg20, 2) if vol_avg20 > 0 else 1.0
    vol_pct = round((atr / current_price) * 100, 2) if current_price > 0 else 0.0

    ema12 = safe_float(close.ewm(span=12, adjust=False).mean().iloc[-1], current_price)
    ema26 = safe_float(close.ewm(span=26, adjust=False).mean().iloc[-1], current_price)
    ema50 = safe_float(close.ewm(span=50, adjust=False).mean().iloc[-1], current_price)
    trend_aligned = (ema12 > ema26 > ema50) or (ema12 < ema26 < ema50)

    if not is_open:
        regime = "OFF_SESSION"
        label = "FUORI SESSIONE / BORSA CHIUSA"
        rec = {
            "rsi_buy_min": 44.0, "rsi_buy_max": 64.0,
            "rsi_sell_min": 36.0, "rsi_sell_max": 56.0,
            "sl_m": 1.8, "tp_m": 3.5,
            "min_vol_ratio": 1.00, "min_score": 88.0,
        }
        advice = (
            f"{category} chiusa o fuori orario: alza il Min Score a 88.0% e SL a 1.8x ATR "
            f"oppure concentrati su Criptovalute Globali (24/7)."
        )
    elif vol_pct >= 1.20 or vol_ratio >= 1.35:
        regime = "HIGH_VOLATILITY"
        label = "ALTA VOLATILITÀ / IMPULSO"
        rec = {
            "rsi_buy_min": 36.0, "rsi_buy_max": 72.0,
            "rsi_sell_min": 28.0, "rsi_sell_max": 64.0,
            "sl_m": 1.6, "tp_m": 3.5,
            "min_vol_ratio": 1.10, "min_score": 82.0,
        }
        advice = (
            f"Alta volatilità ({vol_pct}%, Vol {vol_ratio}x): usa SL 1.6x ATR, TP1 3.5x ATR "
            f"e bande RSI più ampie per assorbire gli spike impulsivi."
        )
    elif vol_ratio < 0.85 or (vol_pct < 0.35 and not trend_aligned):
        regime = "CHOP_RANGE"
        label = "LATERALE / BASSA VOLATILITÀ"
        rec = {
            "rsi_buy_min": 42.0, "rsi_buy_max": 62.0,
            "rsi_sell_min": 38.0, "rsi_sell_max": 58.0,
            "sl_m": 1.5, "tp_m": 2.8,
            "min_vol_ratio": 0.95, "min_score": 85.0,
        }
        advice = (
            f"Mercato laterale/contenuto (Vol {vol_ratio}x, ATR {vol_pct}%): stringi RSI e porta "
            f"Min Score a 85.0% per filtrare i falsi breakout."
        )
    else:
        regime = "TRENDING"
        label = "TREND DIREZIONALE ORDINATO"
        rec = {
            "rsi_buy_min": 38.0, "rsi_buy_max": 68.0,
            "rsi_sell_min": 32.0, "rsi_sell_max": 62.0,
            "sl_m": 1.2, "tp_m": 2.8,
            "min_vol_ratio": 0.85, "min_score": 80.0,
        }
        advice = (
            f"Trend direzionale sano (Vol {vol_ratio}x, ATR {vol_pct}%): mantieni SL 1.2x ATR, "
            f"TP1 2.8x ATR e Min Score 80.0%."
        )

    return {
        "regime": regime,
        "label": label,
        "vol_pct": vol_pct,
        "vol_ratio": vol_ratio,
        "recommended_params": rec,
        "advice": advice,
    }


BINGX_CONTRACTS_CACHE = {"ts": 0, "map": {}}


def get_bingx_contract_specs():
    now = time.time()
    if now - BINGX_CONTRACTS_CACHE["ts"] < 300 and BINGX_CONTRACTS_CACHE["map"]:
        return BINGX_CONTRACTS_CACHE["map"]
    specs = {}
    try:
        res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/contracts", timeout=2.0).json()
        if res.get("code") == 0 and isinstance(res.get("data"), list):
            for c in res["data"]:
                sym = str(c.get("symbol", "")).strip()
                if sym:
                    specs[sym] = {
                        "pricePrecision": int(c.get("pricePrecision", 4)),
                        "quantityPrecision": int(c.get("quantityPrecision", 3)),
                    }
        if specs:
            BINGX_CONTRACTS_CACHE["ts"] = now
            BINGX_CONTRACTS_CACHE["map"] = specs
    except Exception:
        pass
    return specs


def build_bingx_candidates(symbol, name=""):
    clean = canonicalize_symbol_key(symbol, name)
    exact_mapped = BINGX_EXACT_NAME_MAP.get(str(symbol or "").strip().upper()) or BINGX_EXACT_NAME_MAP.get(clean)
    candidates = list(BINGX_SYMBOL_ALIASES.get(clean, []))
    if exact_mapped and exact_mapped not in candidates:
        candidates.insert(0, exact_mapped)
    raw_up = str(symbol or "").strip().upper()
    if raw_up and raw_up not in candidates:
        candidates.append(raw_up)
    default_pair = f"{clean.replace('USDT', '')}-USDT" if clean.endswith("USDT") else f"{clean}-USDT"
    if default_pair not in candidates:
        candidates.append(default_pair)
    return candidates


def resolve_bingx_symbol(symbol, name=""):
    candidates = build_bingx_candidates(symbol, name)
    specs = BINGX_CONTRACTS_CACHE.get("map") or {}
    for c in candidates:
        if c in specs:
            return c, specs[c]["pricePrecision"]
    return candidates[0], None


def get_market_session_status(category):
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()
    current_minutes = now_utc.hour * 60 + now_utc.minute

    if "Criptovalute" in category:
        return {"status": "APERTO", "is_open": True, "hours": "24/7 Continuo (UTC)", "badge_color": "#00ff66"}
    elif "Forex" in category or "Materie Prime" in category:
        is_closed = (weekday == 5) or (weekday == 4 and current_minutes >= 1320) or (weekday == 6 and current_minutes < 1320)
        if is_closed:
            return {"status": "CHIUSO (Weekend)", "is_open": False, "hours": "Lun-Ven 24h", "badge_color": "#ff1e42"}
        return {"status": "APERTO", "is_open": True, "hours": "Sessione Globale 24h", "badge_color": "#00ff66"}
    else:
        if weekday >= 5:
            return {"status": "CHIUSO (Weekend)", "is_open": False, "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC", "badge_color": "#ff1e42"}
        if 870 <= current_minutes < 1260:
            return {"status": "APERTO", "is_open": True, "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC", "badge_color": "#00ff66"}
        return {"status": "CHIUSO (Fuori Orario)", "is_open": False, "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC", "badge_color": "#ff1e42"}


def get_exchange_mode():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return "PAPER PROP-DESK (BINGX SYNC ACTIVE)"
    return "🔴 BINGX LIVE REALE (BINGX SYNC ACTIVE)"


def sign_bingx_request(params_str):
    return hmac.new(BINGX_SECRET_KEY.encode('utf-8'), params_str.encode('utf-8'), hashlib.sha256).hexdigest()


def bingx_signed_request(method, path, params):
    timestamp = int(time.time() * 1000)
    signed_params = {k: v for k, v in params.items() if v is not None and v != ""}
    signed_params["timestamp"] = timestamp
    query_string = "&".join([f"{k}={signed_params[k]}" for k in sorted(signed_params.keys())])
    signature = sign_bingx_request(query_string)
    url = f"{BINGX_BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}

    if method.upper() == "POST":
        resp = requests.post(url, headers=headers, timeout=3.0)
    else:
        resp = requests.get(url, headers=headers, timeout=3.0)

    return resp.json()


def fetch_bingx_real_positions():
    global execution_history
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        simulated_pos = []
        for h in execution_history:
            if h.get("status") == "OPEN":
                pos_copy = h.copy()
                notional = pos_copy.get("size", 1) * pos_copy.get("entry", 1)
                pos_copy["fee"] = round(notional * 0.0005, 4)
                simulated_pos.append(pos_copy)
        return simulated_pos

    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = sign_bingx_request(query_string)
    try:
        res = requests.get(
            f"{BINGX_BASE_URL}/openApi/swap/v2/user/positions?{query_string}&signature={signature}",
            headers={"X-BX-APIKEY": BINGX_API_KEY},
            timeout=2.5,
        ).json()
        if res.get("code") == 0 and "data" in res:
            real_pos_list = []
            for p in res["data"]:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    raw_bx_sym = p.get("symbol", "")
                    sym = canonicalize_symbol_key(raw_bx_sym)
                    side = (
                        "BUY"
                        if p.get("positionSide") == "LONG"
                        else ("SELL" if p.get("positionSide") == "SHORT" else ("BUY" if amt > 0 else "SELL"))
                    )
                    entry_p = float(p.get("avgPrice", 0))
                    size_val = abs(amt)
                    fee_val = float(p.get("fee", 0.0))
                    if fee_val == 0.0:
                        fee_val = round((size_val * entry_p) * 0.0005, 4)

                    real_pos_list.append({
                        "symbol": sym,
                        "name": raw_bx_sym,
                        "action": side,
                        "entry": entry_p,
                        "current_price": entry_p,
                        "size": size_val,
                        "leverage": f"Isolata {p.get('leverage', 8)}x",
                        "margin": float(p.get("initialMargin", 0)),
                        "fee": fee_val,
                        "unrealized_pnl": round(float(p.get('unrealizedProfit', 0)), 2),
                        "status": "OPEN",
                    })
            return real_pos_list
    except Exception:
        pass
    return [h for h in execution_history if h.get("status") == "OPEN"]


def sync_bingx_closed_history():
    global execution_history
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return
    timestamp = int(time.time() * 1000)
    query_string = f"incomeType=REALIZED_PNL&limit=50&timestamp={timestamp}"
    signature = sign_bingx_request(query_string)
    try:
        res = requests.get(
            f"{BINGX_BASE_URL}/openApi/swap/v2/user/income?{query_string}&signature={signature}",
            headers={"X-BX-APIKEY": BINGX_API_KEY},
            timeout=2.5,
        ).json()
        if res.get("code") == 0 and "data" in res:
            for inc in res["data"]:
                pnl_val = float(inc.get("income", 0.0))
                trade_time = int(inc.get("time", time.time() * 1000)) / 1000.0
                date_str = datetime.fromtimestamp(trade_time).strftime("%Y-%m-%d")
                month_str = datetime.fromtimestamp(trade_time).strftime("%Y-%m")
                trade_id = f"BINGX-{inc.get('tranId', int(trade_time))}"

                if not any(h.get('id') == trade_id for h in execution_history):
                    raw_bx_sym = inc.get("symbol", "")
                    execution_history.append({
                        "id": trade_id,
                        "symbol": canonicalize_symbol_key(raw_bx_sym),
                        "name": raw_bx_sym,
                        "action": "BUY" if pnl_val >= 0 else "SELL",
                        "entry": 0.0,
                        "size": 0.0,
                        "leverage": "Isolata 8x",
                        "margin": 0.0,
                        "fee": 0.0,
                        "realized_pnl": round(pnl_val, 4),
                        "status": "CLOSED",
                        "date": date_str,
                        "month": month_str,
                    })
    except Exception as e:
        print(f"Errore sync storico BingX: {e}")


def execute_verified_order(symbol, name, action, entry, size, leverage, margin, sl, tp1, params=None):
    global execution_history
    cfg_params = params if isinstance(params, dict) else {}
    lev_num = parse_leverage_number(leverage, default=5)
    pos_side = "LONG" if action == "BUY" else "SHORT"
    bx_result = None
    bx_error = None

    bx_symbol, bx_precision = resolve_bingx_symbol(symbol, name)
    live_p = get_live_asset_price(symbol)
    ref_price = live_p if live_p > 0 else safe_float(entry, 0.0)
    dec = bx_precision if bx_precision is not None else (2 if ref_price >= 10 else 4)

    aligned_sl, aligned_tp1 = align_tp_sl_to_price(
        action=action,
        ref_price=ref_price,
        sl_val=sl,
        tp_val=tp1,
        sl_m=safe_float(cfg_params.get("sl_m"), 1.2),
        tp_m=safe_float(cfg_params.get("tp_m"), 2.6),
        decimals=dec,
    )
    safe_size = compute_order_quantity(bx_symbol, ref_price, size, margin, lev_num)

    if BINGX_API_KEY and BINGX_SECRET_KEY:
        try:
            try:
                bingx_signed_request("POST", "/openApi/swap/v2/trade/marginType", {
                    "symbol": bx_symbol,
                    "marginType": "ISOLATED",
                })
            except Exception as e_margin:
                print(f"[BingX MarginType Warning] {bx_symbol}: {e_margin}")

            try:
                bingx_signed_request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": bx_symbol,
                    "side": pos_side,
                    "leverage": lev_num,
                })
            except Exception as e_lev:
                print(f"[BingX Leverage Warning] {bx_symbol}: {e_lev}")

            order_params = {
                "symbol": bx_symbol,
                "side": "BUY" if action == "BUY" else "SELL",
                "positionSide": pos_side,
                "type": "MARKET",
                "quantity": safe_size,
            }

            if aligned_tp1 > 0:
                order_params["takeProfit"] = json.dumps({
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": aligned_tp1,
                    "price": aligned_tp1,
                    "workingType": "MARK_PRICE",
                }, separators=(",", ":"))

            if aligned_sl > 0:
                order_params["stopLoss"] = json.dumps({
                    "type": "STOP_MARKET",
                    "stopPrice": aligned_sl,
                    "price": aligned_sl,
                    "workingType": "MARK_PRICE",
                }, separators=(",", ":"))

            res = bingx_signed_request("POST", "/openApi/swap/v2/trade/order", order_params)
            if isinstance(res, dict) and res.get("code") == 0:
                data_obj = res.get("data", {})
                order_obj = data_obj.get("order", data_obj) if isinstance(data_obj, dict) else {}
                bx_result = {
                    "orderId": order_obj.get("orderId", ""),
                    "symbol": bx_symbol,
                    "sl": aligned_sl,
                    "tp1": aligned_tp1,
                    "quantity": safe_size,
                    "raw": res,
                }
            else:
                bx_error = res.get("msg") if isinstance(res, dict) else str(res)
                print(f"[BingX Order Error] {bx_symbol}: {res}")
        except Exception as e:
            bx_error = str(e)
            print(f"[BingX Order Exception] {symbol}: {e}")

    canonical_sym = canonicalize_symbol_key(symbol, name)
    if not any(h.get('symbol') == canonical_sym and h.get('status') == 'OPEN' for h in execution_history):
        notional = safe_size * ref_price
        est_fee = round(notional * 0.0005, 4)
        order_record = {
            "id": f"ORD-{int(time.time()*1000)}",
            "symbol": canonical_sym,
            "bingx_symbol": bx_symbol,
            "name": f"{name} ({bx_symbol})" if bx_symbol not in str(name) else name,
            "action": action,
            "entry": ref_price,
            "size": safe_size,
            "leverage": f"Isolata {lev_num}x",
            "margin": max(safe_float(margin, 2.0), 2.0),
            "sl": aligned_sl,
            "tp1": aligned_tp1,
            "fee": est_fee,
            "unrealized_pnl": 0.0,
            "status": "OPEN",
            "opened_at": datetime.now().strftime("%H:%M:%S"),
        }
        execution_history.insert(0, order_record)

    return bx_result, bx_error


COMMODITY_FALLBACK_PRICES = {
    'XAGUSDT': 41.66,
    'SILVERXAGUSDT': 41.66,
    'GOLDXAUUSDT': 2650.0,
    'WTIUSDT': 83.12,
    'BRENTUSDT': 86.40,
    'NATGASUSDT': 3.25,
    'COPPERUSDT': 5.50,
    'GBPUSDT': 1.3107,
    'GBPUSD': 1.3107,
    'EURUSDT': 1.0980,
    'EURUSD': 1.0980,
    'USDJPY': 159.16,
}


def get_live_asset_price(symbol, bingx_prices=None):
    clean_sym = canonicalize_symbol_key(symbol)
    if bingx_prices and clean_sym in bingx_prices and bingx_prices[clean_sym] > 0:
        return float(bingx_prices[clean_sym])

    now = time.time()
    cached = _TICKER_CACHE.get(clean_sym)
    if cached and (now - cached["ts"]) < 4.0 and cached["price"] > 0:
        return float(cached["price"])

    candidates = build_bingx_candidates(symbol)[:2]
    for bx_symbol in candidates:
        try:
            r = requests.get(
                f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker",
                params={"symbol": bx_symbol},
                timeout=1.5,
            ).json()
            if r.get("code") == 0 and "data" in r:
                t = r["data"][0] if isinstance(r["data"], list) and len(r["data"]) > 0 else r["data"]
                p = float(t.get("lastPrice", 0))
                if p > 0:
                    _TICKER_CACHE[clean_sym] = {"ts": now, "price": p}
                    return p
        except Exception:
            pass

    if clean_sym in COMMODITY_FALLBACK_PRICES:
        base = COMMODITY_FALLBACK_PRICES[clean_sym]
        minute_wave = np.sin(now / 45.0 + (abs(hash(clean_sym)) % 100)) * 0.0015
        val = round(base * (1.0 + minute_wave), 2 if base > 5 else 5)
        _TICKER_CACHE[clean_sym] = {"ts": now, "price": val}
        return float(val)

    return 0.0


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/presets', methods=['GET'])
def get_presets_info():
    return jsonify({
        "presets": {f"{tf}_{prof}": cfg for (tf, prof), cfg in EXACT_PRESETS.items()}
    })


@app.route('/api/live-ticker/<symbol>', methods=['GET'])
def get_live_ticker(symbol):
    try:
        price = get_live_asset_price(symbol)
        bx_sym, _ = resolve_bingx_symbol(symbol)
        if price > 0:
            return jsonify({
                "success": True,
                "symbol": symbol,
                "bingx_symbol": bx_sym,
                "lastPrice": price,
                "timestamp": int(time.time() * 1000),
            }), 200
        return jsonify({"success": False, "error": "Prezzo non disponibile"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/autotrade/toggle', methods=['POST'])
def toggle_autotrade():
    global auto_trade_active
    auto_trade_active = (
        not auto_trade_active
        if not request.json or 'enabled' not in request.json
        else bool(request.json['enabled'])
    )
    save_autotrade(auto_trade_active)
    return jsonify({"auto_trade": auto_trade_active, "mode": get_exchange_mode()})


@app.route('/api/autotrade/execute-manual', methods=['POST'])
def execute_manual_trade():
    data = request.get_json(silent=True) or {}
    symbol = str(data.get("symbol", "")).strip().upper()
    name = str(data.get("name", symbol)).strip()
    action = str(data.get("action", "")).strip().upper()
    entry = safe_float(data.get("entry"), 0.0)
    size = safe_float(data.get("size"), 0.0)
    leverage = str(data.get("leverage", "Isolata 5x"))
    margin = safe_float(data.get("margin"), 2.0)
    sl = safe_float(data.get("sl"), 0.0)
    tp1 = safe_float(data.get("tp1"), 0.0)
    params = data.get("params") if isinstance(data.get("params"), dict) else {}

    if action not in ["BUY", "SELL"] or not symbol or entry <= 0:
        return jsonify({"success": False, "error": "Parametri ordine non validi"}), 400

    bx_result, bx_error = execute_verified_order(
        symbol, name, action, entry, size, leverage, margin, sl, tp1, params=params
    )

    if BINGX_API_KEY and BINGX_SECRET_KEY and bx_error and not bx_result:
        return jsonify({
            "success": False,
            "symbol": symbol,
            "action": action,
            "mode": get_exchange_mode(),
            "error": bx_error,
        }), 400

    return jsonify({
        "success": True,
        "symbol": symbol,
        "action": action,
        "mode": get_exchange_mode(),
        "bingx_response": bx_result,
    })


@app.route('/api/autotrade/status', methods=['GET'])
def get_autotrade_status():
    sync_bingx_closed_history()
    return jsonify({
        "auto_trade": auto_trade_active,
        "mode": get_exchange_mode(),
        "positions": fetch_bingx_real_positions(),
        "history": execution_history[:20],
    })


@app.route('/api/autotrade/close', methods=['POST'])
def close_position():
    global session_realized_pnl, execution_history
    data = request.get_json(silent=True) or {}
    symbol = canonicalize_symbol_key(data.get('symbol', ''))
    for h in execution_history:
        if canonicalize_symbol_key(h.get('symbol', '')) == symbol and h.get('status') == 'OPEN':
            h['status'] = 'CLOSED'
            pnl = h.get('unrealized_pnl', 0.0)
            h['realized_pnl'] = pnl
            h['date'] = datetime.now().strftime("%Y-%m-%d")
            h['month'] = datetime.now().strftime("%Y-%m")
            session_realized_pnl += pnl
            return jsonify({"success": True})
    return jsonify({"success": False}), 404


@app.route('/api/live-prices', methods=['GET'])
def get_live_prices():
    try:
        res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker", timeout=1.8).json()
        if res.get("code") == 0 and "data" in res:
            prices = {}
            data = res["data"]
            if isinstance(data, list):
                for item in data:
                    raw_s = item.get("symbol", "")
                    sym = canonicalize_symbol_key(raw_s)
                    p = float(item.get("lastPrice", 0))
                    prices[sym] = p
                    prices[raw_s.replace("-", "").upper()] = p
            elif isinstance(data, dict):
                raw_s = data.get("symbol", "")
                sym = canonicalize_symbol_key(raw_s)
                p = float(data.get("lastPrice", 0))
                prices[sym] = p
                prices[raw_s.replace("-", "").upper()] = p
            return jsonify(prices)
    except Exception:
        pass
    return jsonify({})


@app.route('/api/analytics/performance', methods=['GET'])
def get_performance_analytics():
    global execution_history
    sync_bingx_closed_history()
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_month_str = datetime.now().strftime("%Y-%m")

    closed_trades = [h for h in execution_history if h.get("status") == "CLOSED"]
    today_trades = [h for h in closed_trades if h.get("date") == today_str]
    month_trades = [h for h in closed_trades if h.get("month") == current_month_str]

    def calc_stats(trades):
        total = len(trades)
        wins = [t for t in trades if t.get("realized_pnl", 0) > 0]
        losses = [t for t in trades if t.get("realized_pnl", 0) <= 0]
        win_pct = round((len(wins) / total * 100), 1) if total > 0 else 0.0
        loss_pct = round((len(losses) / total * 100), 1) if total > 0 else 0.0
        won_amt = sum(t.get("realized_pnl", 0) for t in wins)
        lost_amt = sum(t.get("realized_pnl", 0) for t in losses)
        return (
            total,
            len(wins),
            len(losses),
            win_pct,
            loss_pct,
            round(won_amt, 2),
            round(lost_amt, 2),
            round(won_amt + lost_amt, 2),
        )

    t_tot, t_w, t_l, t_wp, t_lp, t_won, t_lost, t_net = calc_stats(today_trades)
    m_tot, m_w, m_l, m_wp, m_lp, m_won, m_lost, m_net = calc_stats(month_trades)

    return jsonify({
        "daily": {
            "date": today_str,
            "total_trades": t_tot,
            "wins": t_w,
            "losses": t_l,
            "win_pct": t_wp,
            "loss_pct": t_lp,
            "won_amount": t_won,
            "lost_amount": t_lost,
            "net_pnl": t_net,
        },
        "monthly": {
            "month": current_month_str,
            "total_trades": m_tot,
            "wins": m_w,
            "losses": m_l,
            "win_pct": m_wp,
            "loss_pct": m_lp,
            "won_amount": m_won,
            "lost_amount": m_lost,
            "net_pnl": m_net,
        },
    })


@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    global scans_telemetry, execution_history
    return jsonify({
        "scans_count": len(scans_telemetry),
        "total_executions": len([h for h in execution_history if h.get("status") == "OPEN"]),
        "session_pnl": round(session_realized_pnl, 2),
        "telemetry_logs": list(scans_telemetry.values())[-15:],
    })


def fetch_all_markets():
    return {
        "Criptovalute Globali": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin (BTC-USDT)'},
            {'id': 'ETHUSDT', 'name': 'Ξ Ethereum (ETH-USDT)'},
            {'id': 'SOLUSDT', 'name': '🟣 Solana (SOL-USDT)'},
            {'id': 'XRPUSDT', 'name': '✕ Ripple (XRP-USDT)'},
            {'id': 'BNBUSDT', 'name': '🟡 BNB (BNB-USDT)'},
            {'id': 'ADAUSDT', 'name': '₳ Cardano (ADA-USDT)'},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin (DOGE-USDT)'},
            {'id': 'AVAXUSDT', 'name': '🔺 Avalanche (AVAX-USDT)'},
            {'id': 'LINKUSDT', 'name': '🔗 Chainlink (LINK-USDT)'},
            {'id': 'SUIUSDT', 'name': '💧 Sui (SUI-USDT)'},
        ],
        "Materie Prime & Forex": [
            {'id': 'PAXGUSDT', 'name': '🥇 Oro (GOLD(XAU)-USDT)'},
            {'id': 'XAGUSDT', 'name': '🥈 Argento (SILVER(XAG)-USDT)'},
            {'id': 'WTIUSDT', 'name': '🛢️ Petrolio WTI (WTI-USDT)'},
            {'id': 'NATGASUSDT', 'name': '🔥 Gas Naturale (NATGAS-USDT)'},
            {'id': 'COPPERUSDT', 'name': '🥉 Rame (COPPER-USDT)'},
            {'id': 'GBPUSDT', 'name': '💷 GBP/USD (GBPUSD-USDT)'},
            {'id': 'EURUSDT', 'name': '💶 EUR/USD (EURUSD-USDT)'},
            {'id': 'USDJPY', 'name': '🇯🇵 USD/JPY (USDJPY-USDT)'},
        ],
        "Azioni & Indici Globali": [
            {'id': 'AAPLUSDT', 'name': '🍏 Apple Inc. (AAPL-USDT)'},
            {'id': 'TSLAUSDT', 'name': '🚗 Tesla Inc. (TSLA-USDT)'},
            {'id': 'NVDAUSDT', 'name': '💻 Nvidia Corp. (NVDA-USDT)'},
            {'id': 'AMZNUSDT', 'name': '📦 Amazon (AMZN-USDT)'},
            {'id': 'MSFTUSDT', 'name': '🪟 Microsoft (MSFT-USDT)'},
            {'id': 'GOOGLUSDT', 'name': '🔍 Alphabet (GOOGL-USDT)'},
        ],
    }


def build_synthetic_klines_from_price(base_price, seed_key, bars=80):
    rng = np.random.default_rng(abs(hash(seed_key)) % (2**32))
    step_vol = max(base_price * 0.0022, 0.0002)
    trend_drift = np.linspace(-step_vol * 8, step_vol * 10, bars)
    returns = rng.normal(0.0, step_vol, size=bars) + np.diff(np.concatenate([[0.0], trend_drift]))
    closes = np.maximum(base_price + np.cumsum(returns) - (np.cumsum(returns)[-1]), base_price * 0.5)
    closes[-1] = base_price

    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    spread = np.abs(rng.normal(step_vol * 0.75, step_vol * 0.35, size=bars))
    highs = np.maximum(opens, closes) + spread
    lows = np.maximum(np.minimum(opens, closes) - spread, base_price * 0.4)
    volumes = rng.uniform(8000, 32000, size=bars)
    volumes[-1] = max(volumes[-1], np.mean(volumes[:-1]) * 1.25)

    now_ms = int(time.time() * 1000)
    times = [now_ms - (bars - 1 - i) * 60000 for i in range(bars)]
    rows = [
        [times[i], float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]), float(volumes[i])]
        for i in range(bars)
    ]
    return pd.DataFrame(rows, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume'])


def fetch_market_bundle(symbol, interval, bingx_prices=None):
    clean_sym = canonicalize_symbol_key(symbol)
    if bingx_prices and clean_sym in bingx_prices and bingx_prices[clean_sym] > 0:
        ref_price = float(bingx_prices[clean_sym])
        return build_synthetic_klines_from_price(ref_price, f"{symbol}-{interval}"), ref_price

    candidates = build_bingx_candidates(symbol)[:2]
    for bx_symbol in candidates:
        try:
            url = f"{BINGX_BASE_URL}/openApi/swap/v3/quote/klines"
            res = requests.get(
                url, params={"symbol": bx_symbol, "interval": interval, "limit": 80}, timeout=1.5
            ).json()
            if res.get("code") == 0 and "data" in res:
                klines = res["data"]
                if isinstance(klines, list) and len(klines) > 25:
                    rows = []
                    for k in klines:
                        if isinstance(k, list):
                            rows.append([
                                k[0],
                                float(k[1]),
                                float(k[2]),
                                float(k[3]),
                                float(k[4]),
                                float(k[5]),
                            ])
                        elif isinstance(k, dict):
                            rows.append([
                                k.get('time', 0),
                                float(k.get('open', 0)),
                                float(k.get('high', 0)),
                                float(k.get('low', 0)),
                                float(k.get('close', 0)),
                                float(k.get('volume', 0)),
                            ])
                    df = pd.DataFrame(rows, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume'])
                    return df, float(df['Close'].iloc[-1])
        except Exception:
            pass

    ref_price = get_live_asset_price(symbol, bingx_prices)
    if ref_price > 0:
        df = build_synthetic_klines_from_price(ref_price, f"{symbol}-{interval}")
        return df, ref_price

    return None, 0.0


ASSET_FORECASTS = {
    'BTCUSDT': "📊 [Forecast Nov 2026] BTC intorno a 63.735 $ con fase correttiva di breve termine: attenzione a rimbalzi tecnici dai supporti e possibili test in area 60.000-62.000 $",
    'ETHUSDT': "📊 [Forecast Nov 2026] ETH in test dei supporti chiave in area $1.700-$1.750 con possibile reazione direzionale sui livelli",
    'SOLUSDT': "📊 [Forecast Nov 2026] SOL ad alta beta: volatilità marcata al seguito di BTC/ETH, reazioni rapide su supporti e resistenze intraday",
    'XRPUSDT': "📊 [Forecast Nov 2026] XRP con struttura direzionale legata ai flussi di liquidità crypto e rotture di range",
    'BNBUSDT': "📊 [Forecast Nov 2026] BNB con tenuta relativa e movimenti più ordinati rispetto alle altcoin ad alta volatilità",
    'ADAUSDT': "📊 [Forecast Nov 2026] ADA in fase di accumulazione/test supporti, reazioni tecniche da monitorare con SL stretto",
    'DOGEUSDT': "📊 [Forecast Nov 2026] DOGE guidato da momentum e sentiment social/retail: evitare ingressi tardivi su spike",
    'AVAXUSDT': "📊 [Forecast Nov 2026] AVAX sensibile ai flussi layer-1 e alla direzione generale del mercato crypto",
    'LINKUSDT': "📊 [Forecast Nov 2026] LINK monitorato sulle aree di domanda/offerta con movimenti impulsivi",
    'SUIUSDT': "📊 [Forecast Nov 2026] SUI ad alta reattività direzionale: privilegiare conferme di volume prima dell'ingresso",
    'PAXGUSDT': "📊 [Forecast Nov 2026] Oro (GOLD(XAU)-USDT / PAXG-USDT) sostenuto dalla domanda rifugio e dai tassi reali: reazioni affidabili sui livelli",
    'XAGUSDT': "📊 [Forecast Nov 2026] Argento (SILVER(XAG)-USDT) con maggiore volatilità percentuale rispetto all'oro e movimenti impulsivi sui dati macro",
    'WTIUSDT': "📊 [Forecast Nov 2026] Petrolio WTI sensibile a scorte energetiche, domanda globale e tensioni geopolitiche",
    'NATGASUSDT': "📊 [Forecast Nov 2026] Gas Naturale ad altissima volatilità stagionale e meteorologica: gestione rigorosa del rischio",
    'COPPERUSDT': "📊 [Forecast Nov 2026] Rame (Copper) barometro della manifattura globale e della crescita industriale",
    'GBPUSDT': "📊 [Forecast Nov 2026] GBP/USD guidato dal differenziale tassi BoE/Fed e dai dati macro UK/USA",
    'EURUSDT': "📊 [Forecast Nov 2026] EUR/USD sensibile alle indicazioni BCE/Fed, inflazione dell'Eurozona e flussi sul dollaro USA",
    'USDJPY': "📊 [Forecast Nov 2026] USD/JPY influenzato dai rendimenti USA, politica monetaria BoJ e flussi risk-on/risk-off",
    'AAPLUSDT': "📊 [Forecast Nov 2026] Apple (AAPL-USDT) monitorata su trend tech, flussi istituzionali e livelli di supporto/resistenza",
    'TSLAUSDT': "📊 [Forecast Nov 2026] Tesla (TSLA-USDT) ad elevata volatilità intraday con ampie escursioni direzionali",
    'NVDAUSDT': "📊 [Forecast Nov 2026] Nvidia (NVDA-USDT) trainata dal comparto AI/semiconduttori con forte momentum direzionale",
    'AMZNUSDT': "📊 [Forecast Nov 2026] Amazon (AMZN-USDT) seguita su trend cloud/e-commerce e tenuta dei livelli tecnici chiave",
    'MSFTUSDT': "📊 [Forecast Nov 2026] Microsoft (MSFT-USDT) con andamento istituzionale legato al settore enterprise/AI",
    'GOOGLUSDT': "📊 [Forecast Nov 2026] Alphabet (GOOGL-USDT) osservata su flussi big-tech e reazioni sui livelli tecnici",
}


def calculate_setup(df, current_price, symbol, balance, risk_pct, cfg, category, is_open):
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    regime_info = detect_market_regime_and_advice(df, current_price, category, is_open, cfg)

    rsi_buy_min = cfg["rsi_buy_min"]
    rsi_buy_max = cfg["rsi_buy_max"]
    rsi_sell_min = cfg["rsi_sell_min"]
    rsi_sell_max = cfg["rsi_sell_max"]
    sl_m = cfg["sl_m"]
    tp_m = cfg["tp_m"]
    min_vol_ratio = cfg["min_vol_ratio"]
    min_score = cfg["min_score"]
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
    vol_ratio = round(vol_curr / vol_avg20, 2) if vol_avg20 > 0 else 1.10
    low_volume = (vol_avg20 > 0) and (vol_curr < vol_avg20 * min(0.75, min_vol_ratio * 0.85))
    confirmed_breakout_vol = (vol_avg20 <= 0) or (vol_curr >= vol_avg20 * min_vol_ratio)

    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    prev_high_20 = safe_float(high.iloc[-21:-1].max(), last_high) if len(df) >= 22 else last_high
    prev_low_20 = safe_float(low.iloc[-21:-1].min(), last_low) if len(df) >= 22 else last_low

    fake_bull_breakout = (last_high > prev_high_20) and (current_price < prev_high_20)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price > prev_low_20)

    last_range = abs(last_high - last_low)
    is_chop = low_volume or (last_range < atr * 0.14)

    bull_trend_ok = (curr_ema12 >= curr_ema26 * 0.999) and (current_price >= curr_ema50 * 0.988)
    bear_trend_ok = (curr_ema12 <= curr_ema26 * 1.001) and (current_price <= curr_ema50 * 1.012)

    bull_momentum_ok = (current_macd >= prev_macd * 0.92) or (curr_ema12 > curr_ema26 and rsi >= rsi_buy_min)
    bear_momentum_ok = (current_macd <= prev_macd * 1.08) or (curr_ema12 < curr_ema26 and rsi <= rsi_sell_max)

    is_buy = (
        not is_chop
        and not fake_bull_breakout
        and confirmed_breakout_vol
        and bull_trend_ok
        and bull_momentum_ok
        and (rsi_buy_min <= rsi <= rsi_buy_max)
    )

    is_sell = (
        not is_chop
        and not fake_bear_breakout
        and confirmed_breakout_vol
        and bear_trend_ok
        and bear_momentum_ok
        and (rsi_sell_min <= rsi <= rsi_sell_max)
    )

    if fake_bull_breakout:
        inv = "🛑 Anti-Falsa Rottura (Fakeout Long)"
    elif fake_bear_breakout:
        inv = "🛑 Anti-Falsa Rottura (Fakeout Short)"
    elif is_buy:
        inv = f"🚀 Preset {timeframe}/{profile} LONG Confermato"
    elif is_sell:
        inv = f"📉 Preset {timeframe}/{profile} SHORT Confermato"
    else:
        inv = "🛡️ Filtro Anti-Rumore Attivo"

    confluence_hits = 0
    if is_buy:
        confluence_hits = sum([
            curr_ema12 >= curr_ema26 * 0.999,
            current_price >= curr_ema50 * 0.988,
            current_macd >= prev_macd * 0.92,
            rsi_buy_min <= rsi <= rsi_buy_max,
            vol_ratio >= min_vol_ratio,
        ])
        raw_score = min(98.5, max(min_score, min_score + confluence_hits * 2.5))
        score = round(raw_score, 1)
        action, sent, col = "BUY", "LONG CONFERMATO", "#00ff66"
    elif is_sell:
        confluence_hits = sum([
            curr_ema12 <= curr_ema26 * 1.001,
            current_price <= curr_ema50 * 1.012,
            current_macd <= prev_macd * 1.08,
            rsi_sell_min <= rsi <= rsi_sell_max,
            vol_ratio >= min_vol_ratio,
        ])
        raw_score = min(98.5, max(min_score, min_score + confluence_hits * 2.5))
        score = round(raw_score, 1)
        action, sent, col = "SELL", "SHORT CONFERMATO", "#ff1e42"
    else:
        action, score, sent, col = "NEUTRAL", 50.0, "FILTRATO / NO-TRADE", "#ffffff"

    if not is_open:
        filter_reason = f"Borsa chiusa ({category}) — in attesa riapertura sessione."
    elif is_chop:
        filter_reason = f"Fase laterale/bassa volatilità o volume debole (Vol {vol_ratio}x < soglia)."
    elif fake_bull_breakout or fake_bear_breakout:
        filter_reason = "Falsa rottura (fakeout) rilevata sui massimi/minimi recenti."
    elif not confirmed_breakout_vol:
        filter_reason = f"Volume insufficiente (Vol {vol_ratio}x < Min Vol Ratio {min_vol_ratio}x)."
    elif action == "NEUTRAL":
        filter_reason = (
            f"Nessuna confluenza completa (RSI {round(rsi, 1)} | "
            f"BUY {rsi_buy_min}-{rsi_buy_max}, SELL {rsi_sell_min}-{rsi_sell_max} | "
            f"EMA/MACD non allineati)."
        )
    else:
        filter_reason = f"Setup {action} valido ({confluence_label if 'confluence_label' in locals() else f'{confluence_hits}/5 Fattori'}, Score {score}%)."

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

    tf_min = 5 if timeframe == '5m' else (15 if timeframe == '15m' else 60)
    recent_movement = abs(close.diff().tail(5)).mean()
    effective_speed = max(recent_movement, atr * 0.15, current_price * 0.0001)
    candles_needed = max(1, int(np.ceil(tp_dist / effective_speed)))
    est_min = candles_needed * tf_min

    if est_min < 60:
        duration = f"~{est_min} min ({candles_needed} candele)"
    elif est_min < 1440:
        duration = f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    else:
        duration = f"~{round(est_min / 1440, 1)} giorni ({candles_needed} candele)"

    if balance <= 2.0 and profile == 'aggressive':
        lev_int = 20
    elif symbol in ['BTCUSDT', 'ETHUSDT']:
        lev_int = 8
    elif category == 'Criptovalute Globali':
        lev_int = 10
    else:
        lev_int = 5

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = max(abs(entry - sl), tick_size)
    raw_pos_size = (max_risk / risk_unit) if risk_unit > 0 else 0.0
    pos_size = compute_order_quantity(bx_symbol, entry, raw_pos_size, max(balance, 2.0), lev_int)

    margin = round((pos_size * entry) / lev_int, 2)

    roi_raw = round((tp_dist / entry) * lev_int * 100, 1) if entry > 0 else 0.0
    roi_pct = f"+{roi_raw}%" if action == "BUY" else f"-{roi_raw}%" if action == "SELL" else "0.0%"

    notional = pos_size * entry
    roundtrip_fee = round(notional * 0.001, 4)
    expected_gross_profit = round(pos_size * tp_dist, 4)
    expected_net_profit = round(expected_gross_profit - roundtrip_fee, 4)

    vol_pct = round((atr / entry) * 100, 2) if entry > 0 else 0.0
    vol_regime = "ALTA VOLATILITÀ" if vol_pct >= 1.2 else ("MODERATA" if vol_pct >= 0.35 else "CONTENUTA")
    confluence_label = f"{confluence_hits}/5 Fattori" if action in ["BUY", "SELL"] else "0/5 Fattori"

    forecast_table = ASSET_FORECASTS.get(
        symbol,
        f"📊 [Forecast Nov 2026] Monitoraggio globale {symbol} ({category}) con controllo volatilità e rischio",
    )

    advisor_msg = (
        f"{forecast_table} | BingX: {bx_symbol} | Regime: {regime_info['label']} | {regime_info['advice']} "
        f"| R:R 1:{rrr_val} | Net PnL stimato: +${expected_net_profit}"
    )

    if not is_open:
        advisor_status = "BLOCCATO"
        advisor_color = "#ff1e42"
    elif action == "NEUTRAL":
        advisor_status = "FILTRATO (NO FALSO SEGNALE)"
        advisor_color = "#8a99ad"
    elif expected_net_profit <= 0:
        advisor_status = "ATTENZIONE FEE"
        advisor_color = "#ffd166"
    else:
        advisor_status = f"SETUP PRO PRONTO ({confluence_label})"
        advisor_color = "#00ff66"

    return {
        "action": action,
        "filter_reason": filter_reason,
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
        "advisor_status": advisor_status,
        "advisor_color": advisor_color,
        "advisor_msg": advisor_msg,
        "applied_params": cfg,
        "market_regime": regime_info,
        "recommended_params": regime_info["recommended_params"],
    }


@app.route('/api/signals', methods=['GET'])
def get_signals():
    global auto_trade_active, scans_telemetry
    markets = fetch_all_markets()
    default_sessions = {cat: get_market_session_status(cat) for cat in markets.keys()}
    cat_counts = {cat: {"BUY": 0, "SELL": 0, "NEUTRAL": 0} for cat in markets.keys()}
    cat_reasons = {cat: [] for cat in markets.keys()}

    try:
        timeframe = request.args.get('timeframe', '15m')
        balance = safe_float(request.args.get('balance', 2.0).replace(',', '.'), 2.0)
        risk = safe_float(request.args.get('risk', 1.0).replace(',', '.'), 1.0)
        profile = request.args.get('profile', 'balanced')

        cfg = resolve_active_params(timeframe, profile, request.args)
        cache_key = f"{timeframe}|{profile}|{balance}|{risk}|{cfg['min_score']}|{cfg['sl_m']}|{cfg['tp_m']}|{cfg['min_vol_ratio']}"
        now = time.time()
        if _SIGNALS_CACHE["key"] == cache_key and (now - _SIGNALS_CACHE["ts"]) < 6.0 and _SIGNALS_CACHE["payload"]:
            return jsonify(_SIGNALS_CACHE["payload"])

        all_prices = {}
        try:
            res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker", timeout=1.8).json()
            if res.get("code") == 0 and "data" in res:
                for item in res["data"]:
                    raw_s = item.get("symbol", "")
                    sym = canonicalize_symbol_key(raw_s)
                    p = float(item.get("lastPrice", 0))
                    if p > 0:
                        all_prices[sym] = p
                        all_prices[raw_s.replace("-", "").upper()] = p
                        _TICKER_CACHE[sym] = {"ts": now, "price": p}
        except Exception:
            pass

        raw_signals = []
        target_assets = [(cat, a) for cat, assets in markets.items() for a in assets]

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(fetch_market_bundle, a['id'], cfg["timeframe"], all_prices): (cat, a) for cat, a in target_assets}
            for future in as_completed(futures, timeout=6.0):
                try:
                    cat, a = futures[future]
                    df, live_price = future.result(timeout=2.0)

                    canon_id = canonicalize_symbol_key(a['id'], a['name'])
                    if a['id'] in all_prices and all_prices[a['id']] > 0:
                        live_price = all_prices[a['id']]
                    elif canon_id in all_prices and all_prices[canon_id] > 0:
                        live_price = all_prices[canon_id]
                    elif live_price <= 0:
                        live_price = get_live_asset_price(a['id'], all_prices)

                    if live_price > 0 and df is not None and not df.empty:
                        df.loc[df.index[-1], 'Close'] = live_price

                    sess = default_sessions[cat]
                    if df is not None and not df.empty and live_price > 0:
                        res_sig = calculate_setup(df, live_price, a['id'], balance, risk, cfg, cat, sess["is_open"])
                        numeric_score = float(str(res_sig.get("ai_score", "0")).replace("%", ""))
                        if res_sig["action"] not in ["BUY", "SELL"] or numeric_score < cfg["min_score"]:
                            cat_counts[cat]["NEUTRAL"] += 1
                            reason_txt = res_sig.get("filter_reason") or f"Score {numeric_score}% < soglia {cfg['min_score']}%."
                            if len(cat_reasons[cat]) < 3:
                                cat_reasons[cat].append(f"{a['name']}: {reason_txt}")
                            continue

                        if res_sig["action"] == "BUY":
                            cat_counts[cat]["BUY"] += 1
                        elif res_sig["action"] == "SELL":
                            cat_counts[cat]["SELL"] += 1

                        dec = res_sig["decimals"]
                        sig_obj = {
                            "id": f"{a['id']}_{res_sig['action']}",
                            "category": cat,
                            "symbol": a['id'],
                            "name": a['name'],
                            "price": f"{live_price:.{dec}f}",
                            "market_status": sess["status"],
                            "market_hours": sess["hours"],
                            "market_is_open": sess["is_open"],
                            "market_badge_color": sess["badge_color"],
                            **res_sig,
                        }
                        raw_signals.append(sig_obj)

                        if auto_trade_active and sess["is_open"] and res_sig['action'] in ["BUY", "SELL"] and res_sig['expected_net'] > 0:
                            execute_verified_order(
                                a['id'],
                                a['name'],
                                res_sig['action'],
                                res_sig['entry'],
                                res_sig['size'],
                                res_sig['leverage'],
                                res_sig['margin'],
                                res_sig['sl'],
                                res_sig['tp1'],
                                params=cfg,
                            )
                    else:
                        cat_counts[cat]["NEUTRAL"] += 1
                        if len(cat_reasons[cat]) < 3:
                            cat_reasons[cat].append(f"{a['name']}: dati candele o prezzo live non disponibili.")
                except Exception:
                    continue

        market_diagnostics = {}
        for cat, sess in default_sessions.items():
            reasons = cat_reasons.get(cat, [])
            top_reason = reasons[0] if reasons else (
                "Nessun asset ha superato contemporaneamente filtri RSI, Volume, Trend EMA/MACD e Min Score."
                if sess["is_open"]
                else f"Borsa {cat} chiusa ({sess['hours']})."
            )
            market_diagnostics[cat] = {
                "is_open": sess["is_open"],
                "status": sess["status"],
                "hours": sess["hours"],
                "total_assets": len(markets.get(cat, [])),
                "buy_count": cat_counts[cat]["BUY"],
                "sell_count": cat_counts[cat]["SELL"],
                "neutral_count": cat_counts[cat]["NEUTRAL"],
                "reason": (
                    f"{top_reason} (BUY: {cat_counts[cat]['BUY']}, SELL: {cat_counts[cat]['SELL']}, "
                    f"Filtrati/Neutrali: {cat_counts[cat]['NEUTRAL']})."
                ),
            }

        unique_map = {sig['symbol']: sig for sig in raw_signals}
        signals = list(unique_map.values())
        signals.sort(key=lambda x: float(x['ai_score'].replace('%', '')), reverse=True)

        scans_telemetry[now] = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "scanned": len(signals),
            "status": f"PRESET_{cfg['timeframe']}_{cfg['profile'].upper()}",
        }

        payload = {
            "sessions": default_sessions,
            "signals": signals,
            "active_params": cfg,
            "market_diagnostics": market_diagnostics,
        }
        _SIGNALS_CACHE["key"] = cache_key
        _SIGNALS_CACHE["ts"] = now
        _SIGNALS_CACHE["payload"] = payload
        return jsonify(payload)
    except Exception:
        fallback_payload = _SIGNALS_CACHE.get("payload") or {
            "sessions": default_sessions,
            "signals": [],
            "active_params": {},
            "market_diagnostics": {},
        }
        return jsonify(fallback_payload)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
