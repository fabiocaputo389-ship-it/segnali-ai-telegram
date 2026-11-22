from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import hmac
import io
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "").strip()
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "").strip()
ALLOW_WEEKEND_TRADFI = os.getenv("ALLOW_WEEKEND_TRADFI", "false").strip().lower() in ("1", "true", "yes")
STATE_FILE_PATH = os.getenv("TERMINAL_STATE_FILE", "terminal_state.json")
MAX_SLIPPAGE_PCT = float(os.getenv("MAX_SLIPPAGE_PCT", "0.35"))
LOSS_COOLDOWN_SEC = float(os.getenv("LOSS_COOLDOWN_MINUTES", "20")) * 60.0

BINGX_CANDIDATE_BASES = []
for _u in [
    BINGX_BASE_URL,
    "https://open-api.bingx.com",
    "https://open-api-swap.bingx.com",
]:
    if _u and _u not in BINGX_CANDIDATE_BASES:
        BINGX_CANDIDATE_BASES.append(_u)

DIRECTION_COOLDOWN_SEC = 90.0
_LAST_CONFIRMED_DIRECTION = {}
_LIVE_PRICE_CACHE = {}
_KLINES_CACHE = {}
_CONTRACT_SPECS_CACHE = {"ts": 0.0, "by_symbol": {}}
_SIGNALS_RESPONSE_CACHE = {}
_CACHE_LOCK = threading.Lock()
_LAST_GOOD_SIGNALS_PAYLOAD = None

STATE_LOCK = threading.Lock()
STATE = {
    "auto_trade": False,
    "real_mode": True,
    "max_daily_loss_usd": 5.0,
    "max_open_positions": 3,
    "max_slippage_pct": MAX_SLIPPAGE_PCT,
    "loss_cooldown_sec": LOSS_COOLDOWN_SEC,
    "auto_executed_keys": [],
    "trailing_updated_keys": [],
    "scans_count": 0,
    "session_pnl": 0.0,
    "telemetry_logs": [],
    "closed_trades": [],
    "signal_alerts": [],
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
    "Forex & Valute": [
        {"symbol": "EURUSDT", "bx": "EUR-USDT", "name": "EUR/USD (EUR-USDT)", "precision": 4},
        {"symbol": "GBPUSDT", "bx": "GBP-USDT", "name": "GBP/USD (GBP-USDT)", "precision": 4},
        {"symbol": "USDJPY", "bx": "USDJPY-USDT", "name": "USD/JPY (USDJPY-USDT)", "precision": 2},
    ],
    "Materie Prime": [
        {"symbol": "PAXGUSDT", "bx": "PAXG-USDT", "name": "Oro (GOLD(XAU)-USDT)", "precision": 2},
        {"symbol": "XAGUSDT", "bx": "XAG-USDT", "name": "Argento (SILVER(XAG)-USDT)", "precision": 3},
        {"symbol": "WTIUSDT", "bx": "WTI-USDT", "name": "Petrolio WTI (WTI-USDT)", "precision": 2},
        {"symbol": "NATGASUSDT", "bx": "NATGAS-USDT", "name": "Gas Naturale (NATGAS-USDT)", "precision": 3},
        {"symbol": "COPPERUSDT", "bx": "COPPER-USDT", "name": "Rame (COPPER-USDT)", "precision": 3},
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
    if isinstance(val, str):
        val = val.replace(",", ".").strip()
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return float(default)
        out = float(val)
        return float(default) if (math.isnan(out) or math.isinf(out)) else out
    except Exception:
        return float(default)


def persist_state_locked():
    try:
        snapshot = {
            "auto_trade": bool(STATE.get("auto_trade", False)),
            "max_daily_loss_usd": safe_float(STATE.get("max_daily_loss_usd"), 5.0),
            "max_open_positions": int(STATE.get("max_open_positions", 3)),
            "max_slippage_pct": safe_float(STATE.get("max_slippage_pct"), MAX_SLIPPAGE_PCT),
            "loss_cooldown_sec": safe_float(STATE.get("loss_cooldown_sec"), LOSS_COOLDOWN_SEC),
            "auto_executed_keys": list(STATE.get("auto_executed_keys", []))[-300:],
            "trailing_updated_keys": list(STATE.get("trailing_updated_keys", []))[-300:],
            "closed_trades": list(STATE.get("closed_trades", []))[-500:],
        }
        tmp_path = f"{STATE_FILE_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_FILE_PATH)
    except Exception:
        pass


def load_persisted_state():
    if not os.path.exists(STATE_FILE_PATH):
        return
    try:
        with open(STATE_FILE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return
        with STATE_LOCK:
            if "auto_trade" in data:
                STATE["auto_trade"] = bool(data["auto_trade"])
            if "max_daily_loss_usd" in data:
                STATE["max_daily_loss_usd"] = max(0.5, safe_float(data["max_daily_loss_usd"], 5.0))
            if "max_open_positions" in data:
                STATE["max_open_positions"] = max(1, min(15, int(safe_float(data["max_open_positions"], 3))))
            if "max_slippage_pct" in data:
                STATE["max_slippage_pct"] = max(0.05, min(5.0, safe_float(data["max_slippage_pct"], MAX_SLIPPAGE_PCT)))
            if "loss_cooldown_sec" in data:
                STATE["loss_cooldown_sec"] = max(0.0, safe_float(data["loss_cooldown_sec"], LOSS_COOLDOWN_SEC))
            if isinstance(data.get("auto_executed_keys"), list):
                STATE["auto_executed_keys"] = data["auto_executed_keys"][-300:]
            if isinstance(data.get("trailing_updated_keys"), list):
                STATE["trailing_updated_keys"] = data["trailing_updated_keys"][-300:]
            if isinstance(data.get("closed_trades"), list):
                STATE["closed_trades"] = data["closed_trades"][-500:]
                STATE["session_pnl"] = round(
                    sum(safe_float(t.get("realized_pnl")) for t in STATE["closed_trades"]), 4
                )
    except Exception:
        pass


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


def get_bingx_symbol_candidates(meta: dict) -> list:
    bx_main = meta.get("bx") or resolve_bingx_symbol(meta.get("symbol", "BTCUSDT"))[0]
    base = bx_main.replace("-USDT", "").replace("USDT", "")
    cands = [bx_main]
    alt_map = {
        "PAXG": ["PAXG-USDT", "XAU-USDT"],
        "XAG": ["XAG-USDT", "SILVER-USDT"],
        "WTI": ["WTI-USDT", "USOIL-USDT"],
        "NATGAS": ["NATGAS-USDT", "NG-USDT"],
        "COPPER": ["COPPER-USDT", "HG-USDT"],
        "EUR": ["EUR-USDT"],
        "GBP": ["GBP-USDT"],
        "USDJPY": ["USDJPY-USDT"],
        "AAPL": ["AAPL-USDT"],
        "TSLA": ["TSLA-USDT"],
        "NVDA": ["NVDA-USDT"],
        "AMZN": ["AMZN-USDT"],
        "MSFT": ["MSFT-USDT"],
        "GOOGL": ["GOOGL-USDT"],
    }
    for extra in alt_map.get(base, [f"{base}-USDT"]):
        if extra not in cands:
            cands.append(extra)
    return cands[:2]


def get_bingx_contract_spec(bx_symbol: str):
    now = time.time()
    with _CACHE_LOCK:
        if now - _CONTRACT_SPECS_CACHE.get("ts", 0.0) > 600.0:
            for base in BINGX_CANDIDATE_BASES[:2]:
                try:
                    resp = requests.get(f"{base}/openApi/swap/v2/quote/contracts", timeout=2.0)
                    if resp.status_code == 200:
                        rows = resp.json().get("data") or []
                        if isinstance(rows, list) and rows:
                            parsed = {}
                            for item in rows:
                                if not isinstance(item, dict):
                                    continue
                                s_key = str(item.get("symbol") or "").upper()
                                if not s_key:
                                    continue
                                step_val = safe_float(item.get("size"), 0.0)
                                qty_prec = int(safe_float(item.get("quantityPrecision"), 0))
                                price_prec = int(safe_float(item.get("pricePrecision"), 2))
                                if step_val <= 0 and qty_prec >= 0:
                                    step_val = 10 ** (-qty_prec) if qty_prec > 0 else 1.0
                                min_notional = max(
                                    safe_float(item.get("tradeMinUSDT"), 0.0),
                                    safe_float(item.get("minNotional"), 0.0),
                                )
                                parsed[s_key] = {
                                    "pricePrecision": max(0, min(8, price_prec)),
                                    "quantityPrecision": max(0, min(6, qty_prec)),
                                    "stepSize": step_val if step_val > 0 else 0.001,
                                    "tradeMinUSDT": min_notional if min_notional > 0 else 1.0,
                                }
                            if parsed:
                                _CONTRACT_SPECS_CACHE["by_symbol"] = parsed
                                _CONTRACT_SPECS_CACHE["ts"] = now
                                break
                except Exception:
                    continue
        return _CONTRACT_SPECS_CACHE.get("by_symbol", {}).get(str(bx_symbol or "").upper())


def get_symbol_loss_cooldown_remaining(symbol: str):
    clean_sym = canonicalize_symbol_key(symbol)
    with STATE_LOCK:
        cd_sec = max(0.0, safe_float(STATE.get("loss_cooldown_sec"), LOSS_COOLDOWN_SEC))
        trades = list(STATE.get("closed_trades", []))
    if cd_sec <= 0.0:
        return 0
    now_ts = time.time()
    latest_loss_ts = 0.0
    for tr in trades:
        if canonicalize_symbol_key(tr.get("symbol") or "") != clean_sym:
            continue
        if safe_float(tr.get("realized_pnl"), 0.0) >= 0.0:
            continue
        t_ts = safe_float(tr.get("closed_ts"), 0.0)
        if t_ts <= 0.0 and tr.get("closed_at"):
            try:
                dt_obj = datetime.strptime(str(tr["closed_at"]).replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                t_ts = dt_obj.timestamp()
            except Exception:
                t_ts = 0.0
        if t_ts > latest_loss_ts:
            latest_loss_ts = t_ts
    if latest_loss_ts <= 0.0:
        return 0
    rem = int(math.ceil(cd_sec - (now_ts - latest_loss_ts)))
    return rem if rem > 0 else 0


def validate_order_execution_guards(bx_symbol: str, price: float, qty: float, expected_entry: float = 0.0):
    c_spec = get_bingx_contract_spec(bx_symbol) or {}
    min_usdt = safe_float(c_spec.get("tradeMinUSDT"), 1.0)
    if price <= 0 or qty <= 0:
        return False, f"Quantità o prezzo non valido per {bx_symbol}."
    notional = qty * price
    if notional < min_usdt:
        return False, f"Nozionale ordine (${notional:.2f}) inferiore al minimo BingX (${min_usdt:.2f}) per {bx_symbol}."

    with STATE_LOCK:
        max_slip = max(0.05, safe_float(STATE.get("max_slippage_pct"), MAX_SLIPPAGE_PCT))

    ref_entry = safe_float(expected_entry, 0.0)
    if ref_entry > 0:
        slip_pct = abs(price - ref_entry) / ref_entry * 100.0
        if slip_pct > max_slip:
            return False, f"Slippage {slip_pct:.2f}% superiore al limite {max_slip:.2f}% (Entry {ref_entry} vs Live {price})."

    return True, None


def get_market_calibration_spec(symbol: str, category: str, price: float, bx_symbol: str = None):
    key = canonicalize_symbol_key(symbol)
    meta = ASSET_LOOKUP.get(key)
    bx_name = bx_symbol or (meta["bx"] if meta else resolve_bingx_symbol(symbol)[0])
    c_spec = get_bingx_contract_spec(bx_name)
    if c_spec and "pricePrecision" in c_spec:
        decimals = int(c_spec["pricePrecision"])
    elif meta:
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
        return decimals, 0.0040, 1.65
    if category in ("Forex & Valute", "Materie Prime"):
        return decimals, 0.0030, 1.65
    return decimals, 0.0035, 1.65


def quantize_to_tick(value: float, decimals: int, tick_size: float) -> float:
    if tick_size <= 0:
        return round(value, decimals)
    quantized = round(value / tick_size) * tick_size
    return round(quantized, decimals)


def align_tp_sl_to_price(action: str, entry: float, raw_sl: float, raw_tp: float, decimals: int):
    tick = 10 ** (-decimals)
    min_step = max(tick * 2, entry * 0.0012)
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
    max_notional = max(balance, 1.0) * max(leverage, 1) * 0.92
    capped_qty = min(max(raw_qty, 0.0), max_notional / price)

    c_spec = get_bingx_contract_spec(bx_symbol)
    if c_spec and safe_float(c_spec.get("stepSize"), 0.0) > 0:
        step = safe_float(c_spec["stepSize"])
        dec = int(safe_float(c_spec.get("quantityPrecision"), 0))
    else:
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
        dec = len(str(step).split(".")[1]) if "." in str(step) else 0

    qty = max(step, math.floor(capped_qty / step) * step)
    return round(qty, dec)


def get_session_statuses():
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()
    hour_float = now_utc.hour + now_utc.minute / 60.0

    crypto_open = True
    forex_open = True
    commodities_open = True
    equities_open = ALLOW_WEEKEND_TRADFI or ((weekday < 5) and (14.5 <= hour_float <= 21.0))

    return {
        "Criptovalute Globali": {
            "status": "APERTO LIVE (24/7)",
            "is_open": crypto_open,
            "days_label": "Lun - Dom (Sempre Aperto)",
            "hours": "24/7 Continuo (UTC)",
            "badge_color": "#15803d",
        },
        "Forex & Valute": {
            "status": "APERTO LIVE",
            "is_open": forex_open,
            "days_label": "Aperto Live • FX Lun-Ven",
            "hours": "Operativo Live 24/7 (UTC)",
            "badge_color": "#15803d",
        },
        "Materie Prime": {
            "status": "APERTO LIVE",
            "is_open": commodities_open,
            "days_label": "Aperto Live • Metalli/WTI/Gas",
            "hours": "Operativo Live 24/7 (UTC)",
            "badge_color": "#15803d",
        },
        "Azioni & Indici Globali": {
            "status": "APERTO LIVE" if equities_open else "CHIUSO (Sab-Dom / Fuori Orario)",
            "is_open": equities_open,
            "days_label": "Lun - Ven (Chiuso Sabato & Domenica)",
            "hours": "NYSE/NASDAQ: Lun-Ven 14:30 - 21:00 UTC",
            "badge_color": "#15803d" if equities_open else "#dc2626",
        },
    }


def get_macro_windows(now_utc: datetime):
    h = now_utc.hour + now_utc.minute / 60.0
    wd = now_utc.weekday()
    return [
        {
            "window": "Lun - Ven • 08:00 - 11:30 UTC",
            "title": "Sessione Londra (FX & Oro)",
            "risk": "MEDIA-ALTA",
            "active": (wd < 5) and (8.0 <= h <= 11.5),
        },
        {
            "window": "Lun - Ven • 13:30 - 21:00 UTC",
            "title": "Sessione USA & Borse NYSE/NASDAQ (14:30-21:00 UTC)",
            "risk": "ALTA VOLATILITÀ",
            "active": (wd < 5) and (13.5 <= h <= 21.0),
        },
    ]


def sign_bingx_query(params: dict) -> str:
    query_str = urlencode(sorted(params.items()))
    sig = hmac.new(BINGX_API_SECRET.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query_str}&signature={sig}"


def bingx_signed_request(method: str, path: str, params: dict):
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return None, "API Key / Secret BingX non configurate"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    last_err = "Errore connessione BingX"
    for base in BINGX_CANDIDATE_BASES[:2]:
        req_params = dict(params or {})
        req_params["timestamp"] = int(time.time() * 1000)
        signed_qs = sign_bingx_query(req_params)
        url = f"{base}{path}?{signed_qs}"
        try:
            if method.upper() == "POST":
                resp = requests.post(url, headers=headers, timeout=2.5)
            else:
                resp = requests.get(url, headers=headers, timeout=2.5)
            data = resp.json()
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {data}"
                continue
            if isinstance(data, dict) and data.get("code") not in (0, "0", None):
                last_err = str(data.get("msg") or data)
                continue
            return data, None
        except Exception as exc:
            last_err = str(exc)
    return None, last_err


def sync_bingx_leverage_and_margin(bx_symbol: str, lev_int: int, pos_side: str):
    bingx_signed_request(
        "POST",
        "/openApi/swap/v2/trade/marginType",
        {"symbol": bx_symbol, "marginType": "ISOLATED"},
    )
    bingx_signed_request(
        "POST",
        "/openApi/swap/v2/trade/leverage",
        {"symbol": bx_symbol, "side": pos_side, "leverage": int(lev_int)},
    )


def fetch_bingx_account_balance():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return None, "API Key / Secret BingX non configurate"
    payload, err = bingx_signed_request("GET", "/openApi/swap/v2/user/balance", {})
    if err or not isinstance(payload, dict):
        return None, err or "Errore lettura saldo BingX"
    bal_obj = payload.get("data", {}).get("balance", payload.get("data", {}))
    if isinstance(bal_obj, list) and bal_obj:
        bal_obj = bal_obj[0]
    if not isinstance(bal_obj, dict):
        return None, "Formato saldo BingX non valido"
    return {
        "equity": round(safe_float(bal_obj.get("equity"), safe_float(bal_obj.get("balance"), 0.0)), 4),
        "available_margin": round(safe_float(bal_obj.get("availableMargin"), safe_float(bal_obj.get("free"), 0.0)), 4),
        "unrealized_pnl": round(safe_float(bal_obj.get("unrealizedProfit"), 0.0), 4),
    }, None


def sync_bingx_real_closed_trades():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return
    with STATE_LOCK:
        if time.time() - STATE.get("last_bingx_sync", 0.0) < 25.0:
            return
        STATE["last_bingx_sync"] = time.time()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (31 * 86400 * 1000)
    payload, err = bingx_signed_request(
        "GET",
        "/openApi/swap/v2/user/income",
        {"startTime": start_ms, "endTime": now_ms, "limit": 100},
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
            "closed_ts": round(t_ms / 1000.0, 3),
            "closed_date": dt_utc.strftime("%Y-%m-%d"),
            "closed_month": dt_utc.strftime("%Y-%m"),
            "closed_at": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

    with STATE_LOCK:
        existing_ids = {t.get("id") for t in STATE["closed_trades"]}
        added = False
        for tr in parsed_trades:
            if tr["id"] not in existing_ids:
                STATE["closed_trades"].append(tr)
                existing_ids.add(tr["id"])
                added = True
        STATE["session_pnl"] = round(sum(safe_float(t.get("realized_pnl")) for t in STATE["closed_trades"]), 4)
        if added:
            persist_state_locked()


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


def run_break_even_trailing_on_positions(positions, signals_list):
    if not (BINGX_API_KEY and BINGX_API_SECRET) or not positions:
        return []
    sig_by_sym = {s.get("symbol"): s for s in (signals_list or [])}
    updates = []

    for pos in positions:
        sym = pos.get("symbol")
        bx_sym = pos.get("bx_symbol") or resolve_bingx_symbol(sym)[0]
        action = pos.get("action")
        entry = safe_float(pos.get("entry"), 0.0)
        sig = sig_by_sym.get(sym)
        if not sig or entry <= 0:
            continue
        curr_p = safe_float(sig.get("price"), 0.0)
        tp1 = safe_float(sig.get("tp1"), 0.0)
        if curr_p <= 0 or tp1 <= 0:
            continue

        be_key = f"BE_{sym}_{action}_{round(entry, 5)}"
        with STATE_LOCK:
            if be_key in STATE["trailing_updated_keys"]:
                continue

        reached_tp1 = (action == "BUY" and curr_p >= tp1) or (action == "SELL" and curr_p <= tp1)
        if reached_tp1:
            be_price = round(entry * (1.0012 if action == "BUY" else 0.9988), 6)
            _, err = bingx_signed_request(
                "POST",
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": bx_sym,
                    "side": "ASK" if action == "BUY" else "BID",
                    "positionSide": "LONG" if action == "BUY" else "SHORT",
                    "type": "STOP_MARKET",
                    "stopPrice": be_price,
                    "quantity": pos.get("size"),
                    "reduceOnly": "true",
                },
            )
            if not err:
                with STATE_LOCK:
                    STATE["trailing_updated_keys"].append(be_key)
                    persist_state_locked()
                updates.append(f"{bx_sym} -> Break-Even {be_price}")
    return updates


def fetch_bingx_ticker(bx_symbol: str):
    with _CACHE_LOCK:
        cached = _LIVE_PRICE_CACHE.get(bx_symbol)
        if cached and (time.time() - cached["ts"]) < 4.0:
            return cached["price"]

    for base in BINGX_CANDIDATE_BASES[:2]:
        try:
            url = f"{base}/openApi/swap/v2/quote/ticker"
            resp = requests.get(url, params={"symbol": bx_symbol}, timeout=1.6)
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data")
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    price = safe_float(data.get("lastPrice"), 0.0)
                    if price > 0:
                        with _CACHE_LOCK:
                            _LIVE_PRICE_CACHE[bx_symbol] = {"price": price, "ts": time.time()}
                        return price
        except Exception:
            continue

    with _CACHE_LOCK:
        if cached:
            return cached["price"]
    return None


def fetch_bingx_klines(bx_symbol: str, interval: str, limit: int = 25):
    tf_map = {"5m": "5m", "15m": "15m", "1h": "1h"}
    bx_int = tf_map.get(interval, "15m")
    cache_key = f"{bx_symbol}_{bx_int}_{limit}"
    with _CACHE_LOCK:
        cached = _KLINES_CACHE.get(cache_key)
        if cached and (time.time() - cached["ts"]) < 15.0:
            return cached["df"].copy()

    for base in BINGX_CANDIDATE_BASES[:2]:
        try:
            url = f"{base}/openApi/swap/v2/quote/klines"
            resp = requests.get(url, params={"symbol": bx_symbol, "interval": bx_int, "limit": limit}, timeout=1.8)
            if resp.status_code == 200:
                rows = resp.json().get("data") or []
                if isinstance(rows, list) and len(rows) >= 15:
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
                    if len(parsed) >= 15:
                        df_out = pd.DataFrame(parsed)
                        with _CACHE_LOCK:
                            _KLINES_CACHE[cache_key] = {"df": df_out, "ts": time.time()}
                        return df_out.copy()
        except Exception:
            continue

    with _CACHE_LOCK:
        if cached:
            return cached["df"].copy()
    return None


def fetch_bingx_ticker_multi(candidates: list):
    for bx_symbol in candidates:
        p = fetch_bingx_ticker(bx_symbol)
        if p and p > 0:
            return p, bx_symbol
    return None, candidates[0]


def fetch_bingx_klines_multi(candidates: list, interval: str, limit: int = 25):
    for bx_symbol in candidates:
        df = fetch_bingx_klines(bx_symbol, interval, limit=limit)
        if df is not None and len(df) >= 15:
            return df, bx_symbol
    return None, candidates[0]


def get_real_market_dataframe(meta: dict, timeframe: str):
    candidates = get_bingx_symbol_candidates(meta)
    klines_df, matched_kline_sym = fetch_bingx_klines_multi(candidates, timeframe, limit=25)
    if klines_df is not None and len(klines_df) >= 15:
        live_price = fetch_bingx_ticker(matched_kline_sym)
        price_out = live_price if (live_price and live_price > 0) else safe_float(klines_df["Close"].iloc[-1], 0.0)
        if price_out > 0:
            klines_df.iloc[-1, klines_df.columns.get_loc("Close")] = price_out
            klines_df.iloc[-1, klines_df.columns.get_loc("High")] = max(klines_df.iloc[-1]["High"], price_out)
            klines_df.iloc[-1, klines_df.columns.get_loc("Low")] = min(klines_df.iloc[-1]["Low"], price_out)
        return klines_df, price_out, matched_kline_sym

    live_price, matched_ticker_sym = fetch_bingx_ticker_multi(candidates)
    return None, (live_price or 0.0), matched_ticker_sym


def build_profile_config(profile: str, timeframe: str, min_score: float, risk_pct: float = 1.0):
    p = (profile or "pro_room").lower()
    risk_scale = max(0.5, min(2.0, risk_pct / 1.0))
    if p == "conservative":
        return {
            "profile": "conservative",
            "timeframe": timeframe,
            "min_score": max(min_score, 75.0),
            "min_certainty": 83.3,
            "min_profit_cert": 68.0,
            "min_hits": 5,
            "min_rrr": 1.35,
            "rsi_buy_min": 48.0,
            "rsi_buy_max": 68.0,
            "rsi_sell_min": 32.0,
            "rsi_sell_max": 52.0,
            "sl_m": round(1.25 / max(0.85, min(1.2, risk_scale)), 2),
            "tp_m": 2.2,
            "min_vol_ratio": 0.9,
            "cooldown_sec": DIRECTION_COOLDOWN_SEC,
            "flip_min_cert": 80.0,
            "strict_ev_gate": True,
        }
    if p == "aggressive":
        return {
            "profile": "aggressive",
            "timeframe": timeframe,
            "min_score": min_score,
            "min_certainty": 66.7,
            "min_profit_cert": 55.0,
            "min_hits": 4,
            "min_rrr": 1.15,
            "rsi_buy_min": 40.0,
            "rsi_buy_max": 75.0,
            "rsi_sell_min": 25.0,
            "rsi_sell_max": 60.0,
            "sl_m": round(1.10 / max(0.85, min(1.2, risk_scale)), 2),
            "tp_m": 1.9,
            "min_vol_ratio": 0.5,
            "cooldown_sec": 45.0,
            "flip_min_cert": 70.0,
            "strict_ev_gate": False,
        }
    if p == "pro_room":
        return {
            "profile": "pro_room",
            "timeframe": timeframe,
            "min_score": min_score,
            "min_certainty": 50.0,
            "min_profit_cert": 50.0,
            "min_hits": 3,
            "min_rrr": 1.10,
            "rsi_buy_min": 35.0,
            "rsi_buy_max": 78.0,
            "rsi_sell_min": 22.0,
            "rsi_sell_max": 65.0,
            "sl_m": round(1.10 / max(0.85, min(1.2, risk_scale)), 2),
            "tp_m": 1.85,
            "min_vol_ratio": 0.25,
            "cooldown_sec": 30.0,
            "flip_min_cert": 66.7,
            "strict_ev_gate": False,
        }
    return {
        "profile": "balanced",
        "timeframe": timeframe,
        "min_score": min_score,
        "min_certainty": 66.7,
        "min_profit_cert": 60.0,
        "min_hits": 4,
        "min_rrr": 1.25,
        "rsi_buy_min": 42.0,
        "rsi_buy_max": 72.0,
        "rsi_sell_min": 28.0,
        "rsi_sell_max": 58.0,
        "sl_m": round(1.20 / max(0.85, min(1.2, risk_scale)), 2),
        "tp_m": 2.0,
        "min_vol_ratio": 0.65,
        "cooldown_sec": DIRECTION_COOLDOWN_SEC,
        "flip_min_cert": 75.0,
        "strict_ev_gate": True,
    }


def detect_market_regime_and_advice(df, current_price, is_open, cfg):
    close = df["Close"]
    ema12 = safe_float(close.ewm(span=12, adjust=False).mean().iloc[-1], current_price)
    ema26 = safe_float(close.ewm(span=26, adjust=False).mean().iloc[-1], current_price)
    diff_pct = ((ema12 - ema26) / ema26 * 100.0) if ema26 > 0 else 0.0

    if not is_open:
        regime = "MERCATO CHIUSO"
    elif abs(diff_pct) >= 0.35:
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
    strict_fakeout=True,
):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr_series = tr.rolling(10).mean()
    atr = safe_float(atr_series.iloc[-1], current_price * 0.005)
    atr_prev = safe_float(atr_series.iloc[-5], atr) if len(atr_series) >= 5 else atr
    if atr <= 0:
        atr = current_price * 0.005

    atr_spike = (atr_prev > 0) and (atr > atr_prev * 2.15)

    last_open = safe_float(df["Open"].iloc[-1], current_price)
    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    body_size = abs(current_price - last_open)
    upper_wick = max(0.0, last_high - max(current_price, last_open))
    lower_wick = max(0.0, min(current_price, last_open) - last_low)

    prev_high_20 = safe_float(high.iloc[-15:-1].max(), last_high) if len(df) >= 16 else last_high
    prev_low_20 = safe_float(low.iloc[-15:-1].min(), last_low) if len(df) >= 16 else last_low

    fake_bull_breakout = (last_high > prev_high_20) and (current_price < prev_high_20 * 0.999)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price > prev_low_20 * 1.001)
    wick_rejection_top = (upper_wick > max(body_size * 2.4, atr * 0.45)) and (current_price < last_open)
    wick_rejection_bottom = (lower_wick > max(body_size * 2.4, atr * 0.45)) and (current_price > last_open)

    hard_fakeout = fake_bull_breakout or fake_bear_breakout or wick_rejection_top or wick_rejection_bottom or atr_spike

    fakeout_flags = []
    if fake_bull_breakout:
        fakeout_flags.append("Falsa rottura massimo recente")
    if fake_bear_breakout:
        fakeout_flags.append("Falsa rottura minimo recente")
    if wick_rejection_top:
        fakeout_flags.append("Rejection ombra superiore")
    if wick_rejection_bottom:
        fakeout_flags.append("Rejection ombra inferiore")
    if atr_spike:
        fakeout_flags.append("Spike anomalo ATR")
    if vol_ratio < min_vol_ratio:
        fakeout_flags.append(f"Volume {vol_ratio}x < {min_vol_ratio}x")

    dec = 5 if current_price < 10 else 2
    bull_exhaustion = (rsi >= 68.0) or (curr_ema12 < curr_ema26 and current_macd < prev_macd) or fake_bull_breakout or wick_rejection_top
    bear_exhaustion = (rsi <= 32.0) or (curr_ema12 > curr_ema26 and current_macd > prev_macd) or fake_bear_breakout or wick_rejection_bottom

    if curr_ema12 >= curr_ema26 and bull_exhaustion:
        rev_dir = "SELL"
        rev_prob = min(88.0, round(32.0 + max(0.0, (rsi - 60.0) * 1.8) + (18.0 if (fake_bull_breakout or wick_rejection_top) else 0.0), 1))
        rev_trigger = round(min(current_price - atr * 0.65, curr_ema26 * 0.999), dec)
        rev_reason = "Esaurimento spinta rialzista / possibile inversione SHORT"
    elif curr_ema12 < curr_ema26 and bear_exhaustion:
        rev_dir = "BUY"
        rev_prob = min(88.0, round(32.0 + max(0.0, (40.0 - rsi) * 1.8) + (18.0 if (fake_bear_breakout or wick_rejection_bottom) else 0.0), 1))
        rev_trigger = round(max(current_price + atr * 0.65, curr_ema26 * 1.001), dec)
        rev_reason = "Esaurimento spinta ribassista / possibile inversione LONG"
    else:
        rev_dir = "SELL" if curr_ema12 >= curr_ema26 else "BUY"
        rev_prob = round(min(45.0, max(12.0, abs(50.0 - rsi) * 1.1)), 1)
        rev_trigger = round(curr_ema26, dec)
        rev_reason = "Trend principale intatto — inversione non imminente"

    blocked_flag = hard_fakeout if strict_fakeout else (fake_bull_breakout or fake_bear_breakout or atr_spike)

    return {
        "false_breakout": blocked_flag,
        "fakeout_label": "BLOCCATO (FAKEOUT/RUMORE)" if blocked_flag else ("ATTENZIONE VOLUME" if fakeout_flags else "PULITO (ROTTURA VALIDA)"),
        "fakeout_reason": " • ".join(fakeout_flags) if fakeout_flags else "Nessuna falsa rottura o anomalia rilevata.",
        "reversal_to": rev_dir,
        "reversal_prob_pct": rev_prob,
        "reversal_trigger": rev_trigger,
        "reversal_summary": f"Se inverte → {rev_dir} ({rev_prob}%) @ {rev_trigger}",
        "reversal_reason": rev_reason,
    }


def compute_math_profit_model(
    certainty_pct,
    rrr_val,
    pos_size,
    tp_dist,
    sl_dist,
    roundtrip_fee,
    risk_pct,
    min_rrr=1.25,
    strict_ev_gate=True,
    has_fakeout=False,
    htf_aligned=True,
):
    c_norm = max(0.0, min(1.0, certainty_pct / 100.0))
    rr_adj = 0.04 if rrr_val >= 1.8 else (-0.05 if rrr_val < min_rrr else 0.0)
    htf_adj = 0.02 if htf_aligned else -0.03
    fake_pen = 0.08 if has_fakeout else 0.0

    p_win = max(0.35, min(0.85, 0.42 + 0.38 * c_norm + rr_adj + htf_adj - fake_pen))
    p_win = round(p_win, 3)
    p_loss = round(1.0 - p_win, 3)

    gross_win = pos_size * tp_dist
    gross_loss = pos_size * sl_dist
    net_win = round(gross_win - roundtrip_fee, 4)
    total_loss = round(gross_loss + roundtrip_fee, 4)
    ev_net = round((p_win * net_win) - (p_loss * total_loss), 4)

    b_ratio = (net_win / total_loss) if total_loss > 0 else 0.0
    kelly_raw = ((b_ratio * p_win) - p_loss) / b_ratio if b_ratio > 0 else 0.0
    safe_kelly_pct = round(max(0.0, min(kelly_raw * 0.5, risk_pct / 100.0)) * 100.0, 2)

    denom_be = net_win + total_loss
    break_even_wr = round((total_loss / denom_be) * 100.0, 1) if denom_be > 0 else 50.0
    edge_over_be = p_win - (break_even_wr / 100.0)

    sig_edge = 1.0 / (1.0 + math.exp(-6.0 * edge_over_be))
    risk_ref = max(total_loss, 0.1)
    ev_norm = math.tanh(ev_net / risk_ref)

    profit_certainty_raw = (0.55 * p_win) + (0.30 * sig_edge) + (0.15 * max(-1.0, ev_norm))
    if has_fakeout:
        profit_certainty_raw *= 0.72
    profit_certainty_pct = round(max(0.0, min(99.0, profit_certainty_raw * 100.0)), 1)

    math_approved = True if not strict_ev_gate else ((ev_net > 0.0) and (rrr_val >= min_rrr) and (net_win > 0.0))

    return {
        "p_win_pct": round(p_win * 100.0, 1),
        "p_loss_pct": round(p_loss * 100.0, 1),
        "net_win_usd": net_win,
        "total_loss_usd": total_loss,
        "ev_net_usd": ev_net,
        "kelly_pct": safe_kelly_pct,
        "break_even_wr_pct": break_even_wr,
        "profit_certainty_pct": profit_certainty_pct,
        "math_approved": math_approved,
        "math_badge": "EV+ APPROVATO" if math_approved else "EV- / R:R BASSO",
    }


def compute_tp_progress_pct(action: str, entry: float, current_price: float, tp_val: float) -> float:
    if not entry or not tp_val or tp_val == entry:
        return 0.0
    if action == "SELL":
        denom = entry - tp_val
        if denom <= 0:
            return 0.0
        pct = ((entry - current_price) / denom) * 100.0
    else:
        denom = tp_val - entry
        if denom <= 0:
            return 0.0
        pct = ((current_price - entry) / denom) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def calculate_setup(df, current_price, symbol, balance, risk_pct, cfg, category, is_open, htf_df=None, bx_override=None):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    regime_info = detect_market_regime_and_advice(df, current_price, is_open, cfg)

    rsi_buy_min = cfg["rsi_buy_min"]
    rsi_buy_max = cfg["rsi_buy_max"]
    rsi_sell_min = cfg["rsi_sell_min"]
    rsi_sell_max = cfg["rsi_sell_max"]
    sl_m = cfg["sl_m"]
    tp_m = cfg["tp_m"]
    min_vol_ratio = cfg["min_vol_ratio"]
    min_score = cfg["min_score"]
    min_certainty = safe_float(cfg.get("min_certainty", 50.0), 50.0)
    min_profit_cert = safe_float(cfg.get("min_profit_cert", 50.0), 50.0)
    min_hits = int(cfg.get("min_hits", 3))
    min_rrr = safe_float(cfg.get("min_rrr", 1.10), 1.10)
    cooldown_sec = safe_float(cfg.get("cooldown_sec", DIRECTION_COOLDOWN_SEC), DIRECTION_COOLDOWN_SEC)
    flip_min_cert = safe_float(cfg.get("flip_min_cert", 66.7), 66.7)
    strict_ev_gate = bool(cfg.get("strict_ev_gate", False))
    timeframe = cfg["timeframe"]
    profile = cfg["profile"]

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=10).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=10).mean()
    rsi_series = 100 - (100 / (1 + (gain / loss).replace(0, np.nan)))
    rsi = safe_float(rsi_series.iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    current_macd = safe_float(macd_hist.iloc[-1], 0.0)
    prev_macd = safe_float(macd_hist.iloc[-2], 0.0)
    curr_ema12 = safe_float(ema12.iloc[-1], current_price)
    curr_ema26 = safe_float(ema26.iloc[-1], current_price)

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(10).mean().iloc[-1], current_price * 0.005)
    if atr <= 0:
        atr = current_price * 0.005

    vol_curr = safe_float(volume.iloc[-1], 0.0)
    vol_avg20 = safe_float(volume.rolling(15).mean().iloc[-1], 0.0)
    vol_ratio = round(vol_curr / vol_avg20, 2) if vol_avg20 > 0 else 1.0

    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    prev_high_20 = safe_float(high.iloc[-15:-1].max(), last_high) if len(df) >= 16 else last_high
    prev_low_20 = safe_float(low.iloc[-15:-1].min(), last_low) if len(df) >= 16 else last_low

    fake_bull_breakout = (last_high > prev_high_20) and (current_price < prev_high_20 * 0.999)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price > prev_low_20 * 1.001)
    last_range = abs(last_high - last_low)
    is_chop = (vol_avg20 > 0 and vol_curr < vol_avg20 * 0.30) and (last_range < atr * 0.08)
    vol_ok = (vol_avg20 <= 0) or (vol_ratio >= min_vol_ratio)

    htf_bias = "NEUTRAL"
    if htf_df is not None and len(htf_df) >= 15:
        h_close = htf_df["Close"]
        h_ema12 = safe_float(h_close.ewm(span=12, adjust=False).mean().iloc[-1], current_price)
        h_ema26 = safe_float(h_close.ewm(span=26, adjust=False).mean().iloc[-1], current_price)
        if h_ema12 > h_ema26 * 1.0002:
            htf_bias = "BULL"
        elif h_ema12 < h_ema26 * 0.9998:
            htf_bias = "BEAR"

    bull_factors = [
        curr_ema12 > curr_ema26,
        curr_ema26 >= current_price * 0.985,
        current_price >= curr_ema26 * 0.985,
        (current_macd > 0) or (current_macd > prev_macd),
        rsi_buy_min <= rsi <= rsi_buy_max,
        vol_ratio >= min_vol_ratio,
    ]
    bear_factors = [
        curr_ema12 < curr_ema26,
        curr_ema26 <= current_price * 1.015,
        current_price <= curr_ema26 * 1.015,
        (current_macd < 0) or (current_macd < prev_macd),
        rsi_sell_min <= rsi <= rsi_sell_max,
        vol_ratio >= min_vol_ratio,
    ]

    bull_hits = sum(1 for f in bull_factors if f)
    bear_hits = sum(1 for f in bear_factors if f)

    bull_certainty = round((bull_hits / 6.0) * 100.0, 1)
    bear_certainty = round((bear_hits / 6.0) * 100.0, 1)

    htf_bull_ok = True if profile in ("pro_room", "aggressive") else (htf_bias != "BEAR")
    htf_bear_ok = True if profile in ("pro_room", "aggressive") else (htf_bias != "BULL")

    bull_score_est = min(99.0, round(50.0 + bull_hits * 8.0, 1))
    bear_score_est = min(99.0, round(50.0 + bear_hits * 8.0, 1))

    bull_strict_ok = (
        not is_chop
        and not fake_bull_breakout
        and vol_ok
        and htf_bull_ok
        and (curr_ema12 > curr_ema26 or profile == "pro_room")
        and bull_hits >= min_hits
        and bull_certainty >= min_certainty
        and bull_score_est >= min_score
    )

    bear_strict_ok = (
        not is_chop
        and not fake_bear_breakout
        and vol_ok
        and htf_bear_ok
        and (curr_ema12 < curr_ema26 or profile == "pro_room")
        and bear_hits >= min_hits
        and bear_certainty >= min_certainty
        and bear_score_est >= min_score
    )

    clean_sym = canonicalize_symbol_key(symbol)
    now_ts = time.time()
    prev_dir_info = _LAST_CONFIRMED_DIRECTION.get(clean_sym)

    candidate_action = "NEUTRAL"
    if bull_strict_ok and bear_strict_ok:
        candidate_action = "BUY" if bull_certainty >= bear_certainty else "SELL"
    elif bull_strict_ok:
        candidate_action = "BUY"
    elif bear_strict_ok:
        candidate_action = "SELL"

    if candidate_action in ["BUY", "SELL"] and prev_dir_info:
        last_side = prev_dir_info.get("action")
        last_ts = safe_float(prev_dir_info.get("ts"), 0.0)
        if last_side in ["BUY", "SELL"] and last_side != candidate_action:
            elapsed = now_ts - last_ts
            opp_cert = bull_certainty if candidate_action == "BUY" else bear_certainty
            if elapsed < cooldown_sec and opp_cert < flip_min_cert:
                candidate_action = "NEUTRAL"

    loss_cd_rem = get_symbol_loss_cooldown_remaining(clean_sym)
    if candidate_action in ["BUY", "SELL"] and loss_cd_rem > 0:
        candidate_action = "NEUTRAL"

    if candidate_action == "BUY":
        certainty_val = bull_certainty
        confluence_hits = bull_hits
        score = bull_score_est
        action, sent, col = "BUY", "LONG CERTO CONFERMATO", "#15803d"
        inv = f"Segnale Certo LONG ({certainty_val}%)"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "BUY", "ts": now_ts}
    elif candidate_action == "SELL":
        certainty_val = bear_certainty
        confluence_hits = bear_hits
        score = bear_score_est
        action, sent, col = "SELL", "SHORT CERTO CONFERMATO", "#dc2626"
        inv = f"Segnale Certo SHORT ({certainty_val}%)"
        _LAST_CONFIRMED_DIRECTION[clean_sym] = {"action": "SELL", "ts": now_ts}
    else:
        certainty_val = max(bull_certainty, bear_certainty)
        confluence_hits = max(bull_hits, bear_hits)
        score = round(min(99.0, max(50.0, 50.0 + confluence_hits * 8.0)), 1)
        action, sent, col = "NEUTRAL", "FILTRATO ANTI-FAKEOUT / NO-TRADE", "#0f172a"
        inv = "Solo Segnali Certi • Anti-Falsa Rottura Attivo"

    certainty_level = "ALTA" if certainty_val >= 80.0 else ("MEDIA" if certainty_val >= 50.0 else "BASSA")
    confluence_label = f"{confluence_hits}/6 Fattori"

    bx_symbol, bx_precision = resolve_bingx_symbol(symbol)
    if bx_override:
        bx_symbol = bx_override
    mkt_decimals, min_sl_pct, min_tp_vs_sl = get_market_calibration_spec(symbol, category, current_price, bx_symbol=bx_symbol)
    c_spec = get_bingx_contract_spec(bx_symbol)
    decimals = int(c_spec["pricePrecision"]) if (c_spec and "pricePrecision" in c_spec) else (bx_precision if bx_precision is not None else mkt_decimals)
    tick_size = 10 ** (-decimals)

    raw_sl_dist = max(atr * sl_m, current_price * min_sl_pct)
    raw_tp_dist = max(atr * tp_m, raw_sl_dist * max(min_tp_vs_sl, min_rrr))
    entry = quantize_to_tick(current_price, decimals, tick_size)

    if action == "BUY":
        sl, tp1 = align_tp_sl_to_price("BUY", entry, entry - raw_sl_dist, entry + raw_tp_dist, decimals)
        tp2 = quantize_to_tick(entry + abs(tp1 - entry) * 1.45, decimals, tick_size)
        tp3 = quantize_to_tick(entry + abs(tp1 - entry) * 2.0, decimals, tick_size)
    elif action == "SELL":
        sl, tp1 = align_tp_sl_to_price("SELL", entry, entry + raw_sl_dist, entry - raw_tp_dist, decimals)
        tp2 = quantize_to_tick(entry - abs(entry - tp1) * 1.45, decimals, tick_size)
        tp3 = quantize_to_tick(entry - abs(entry - tp1) * 2.0, decimals, tick_size)
    else:
        sl = quantize_to_tick(entry - raw_sl_dist, decimals, tick_size)
        tp1 = quantize_to_tick(entry + raw_tp_dist, decimals, tick_size)
        tp2 = quantize_to_tick(entry + raw_tp_dist * 1.45, decimals, tick_size)
        tp3 = quantize_to_tick(entry + raw_tp_dist * 2.0, decimals, tick_size)

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp1 - entry)
    rrr_val = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    prog_action = action if action in ("BUY", "SELL") else ("SELL" if curr_ema12 < curr_ema26 else "BUY")
    tp1_progress_pct = compute_tp_progress_pct(prog_action, entry, current_price, tp1)
    tp2_progress_pct = compute_tp_progress_pct(prog_action, entry, current_price, tp2)
    tp3_progress_pct = compute_tp_progress_pct(prog_action, entry, current_price, tp3)

    tf_min = 5 if timeframe == "5m" else (15 if timeframe == "15m" else 60)
    candle_tf_label = f"Candela {timeframe}"
    recent_movement = max(abs(close.diff().tail(5)).mean(), atr * 0.18, current_price * 0.0001)
    candles_needed = max(1, int(np.ceil(tp_dist / recent_movement)))
    est_min = candles_needed * tf_min
    duration = f"~{est_min} min ({candles_needed} candele)" if est_min < 60 else f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    tp_eta_utc = datetime.fromtimestamp(now_ts + (est_min * 60), tz=timezone.utc).strftime("%H:%M UTC")
    tp_eta_text = f"{duration} • {candle_tf_label} • ETA ~{tp_eta_utc}"

    if balance <= 2.0 and profile in ("aggressive", "pro_room"):
        lev_int = 20
    elif symbol in ["BTCUSDT", "ETHUSDT"]:
        lev_int = 8
    elif category == "Criptovalute Globali":
        lev_int = 10
    else:
        lev_int = 5

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = max(abs(entry - sl), tick_size)
    pos_size = compute_order_quantity(bx_symbol, entry, max_risk / risk_unit, max(balance, 1.0), lev_int)
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
        strict_fakeout=(profile == "conservative"),
    )

    htf_aligned = (action == "BUY" and htf_bias != "BEAR") or (action == "SELL" and htf_bias != "BULL") or (action == "NEUTRAL")

    math_model = compute_math_profit_model(
        certainty_pct=certainty_val,
        rrr_val=rrr_val,
        pos_size=pos_size,
        tp_dist=tp_dist,
        sl_dist=sl_dist,
        roundtrip_fee=roundtrip_fee,
        risk_pct=risk_pct,
        min_rrr=min_rrr,
        strict_ev_gate=strict_ev_gate,
        has_fakeout=anti_fake["false_breakout"],
        htf_aligned=htf_aligned,
    )

    profit_cert_val = math_model["profit_certainty_pct"]
    profit_cert_ok = True if profile == "pro_room" else (profit_cert_val >= min_profit_cert)

    if action in ["BUY", "SELL"] and (anti_fake["false_breakout"] or not math_model["math_approved"] or not profit_cert_ok):
        action = "NEUTRAL"
        score = 55.0
        sent = "BLOCCATO FILTRO PROFITTO / EV"
        col = "#dc2626"
        inv = "Blocco Certezza Profitto & EV"
        filter_reason = (
            f"Filtrato da modello profitto: Certezza Profitto {profit_cert_val}% (min {min_profit_cert}%), "
            f"EV netto ${math_model['ev_net_usd']} (R:R 1:{rrr_val}) | {anti_fake['fakeout_reason']}."
        )
        trigger_condition = f"Attesa Certezza Profitto >={min_profit_cert}%, EV netto > 0 e struttura senza fakeout."
    elif loss_cd_rem > 0 and action == "NEUTRAL":
        filter_reason = f"Cooldown post-perdita attivo su {clean_sym}: pausa residua {loss_cd_rem}s."
        trigger_condition = f"Ripresa scansione su {clean_sym} al termine del cooldown ({loss_cd_rem}s)."
    elif not is_open:
        filter_reason = f"Borsa {category} chiusa nei giorni/orari correnti — in attesa riapertura."
        trigger_condition = f"Attivazione alla riapertura con certezza >={min_certainty}% e Score >={min_score}%."
    elif action == "NEUTRAL":
        filter_reason = (
            f"Setup in monitoraggio: Certezza {certainty_val}% (Profitto {profit_cert_val}%), {confluence_hits}/{min_hits} fattori, "
            f"Score {score}% (min {min_score}%), RSI {round(rsi, 1)}, Vol {vol_ratio}x."
        )
        trigger_condition = f"Segnali attivi con certezza >={min_certainty}%, Score >={min_score}% e R:R >= 1:{min_rrr}."
    else:
        filter_reason = (
            f"Segnale certo preso in carico: {action} con Certezza Profitto {profit_cert_val}% ({certainty_label}, {confluence_label}) "
            f"e EV netto +${math_model['ev_net_usd']}."
        )
        trigger_condition = f"Segnale certo {action} (Profitto {profit_cert_val}%) con SL {sl_m}x ATR e TP1 {tp_m}x ATR."

    closes_tail = [round(safe_float(x, current_price), decimals) for x in close.tail(20).tolist()]

    return {
        "action": action,
        "certainty_pct": certainty_val,
        "certainty_level": certainty_level,
        "certainty_text": f"{certainty_val}% ({certainty_level})",
        "profit_certainty_pct": profit_cert_val,
        "loss_cooldown_rem": loss_cd_rem,
        "filter_reason": filter_reason,
        "trigger_condition": trigger_condition,
        "htf_bias": htf_bias,
        "bingx_symbol": bx_symbol,
        "ai_score": f"{score}%",
        "sentiment": sent,
        "sentiment_color": col,
        "inversion": inv,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp1_progress_pct": tp1_progress_pct,
        "tp2_progress_pct": tp2_progress_pct,
        "tp3_progress_pct": tp3_progress_pct,
        "series": closes_tail,
        "roi_pct": roi_pct,
        "duration": duration,
        "candle_tf_label": candle_tf_label,
        "tp_eta_text": tp_eta_text,
        "rrr": rrr_val,
        "rsi": round(rsi, 1),
        "vol_ratio": vol_ratio,
        "confluence": confluence_label,
        "size": pos_size,
        "leverage": f"Isolata {lev_int}x",
        "leverage_num": lev_int,
        "margin": margin,
        "roundtrip_fee": roundtrip_fee,
        "p_win_pct": math_model["p_win_pct"],
        "ev_net_usd": math_model["ev_net_usd"],
        "kelly_pct": math_model["kelly_pct"],
        "math_approved": math_model["math_approved"],
        "applied_params": cfg,
        "market_regime": regime_info,
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


def maybe_run_auto_trade_loop(signals_out, live_positions, kill_switch_hit, account_balance=None):
    with STATE_LOCK:
        is_enabled = bool(STATE.get("auto_trade"))
        max_open_pos = int(STATE.get("max_open_positions", 3))
    if not is_enabled or kill_switch_hit or not (BINGX_API_KEY and BINGX_API_SECRET):
        return []
    if len(live_positions) >= max_open_pos:
        return []

    executed = []
    open_syms = {p.get("symbol") for p in live_positions}
    bal_val = safe_float(account_balance, 0.0)

    for sig in signals_out:
        if len(live_positions) + len(executed) >= max_open_pos:
            break
        sym = sig.get("symbol")
        if not sym or sym in open_syms:
            continue
        if get_symbol_loss_cooldown_remaining(sym) > 0:
            continue
        exec_key = f"{sym}_{sig.get('action')}_{sig.get('entry')}_{sig.get('tp1')}"
        with STATE_LOCK:
            if exec_key in STATE["auto_executed_keys"]:
                continue

        bx_symbol = sig.get("bingx_symbol") or resolve_bingx_symbol(sym)[0]
        action = sig.get("action")
        profile_name = (sig.get("params") or {}).get("profile", "pro_room")
        lev_int = max(1, min(20, int(safe_float(sig.get("leverage_num"), 5))))
        if bal_val > 0 and bal_val <= 2.0 and profile_name in ("aggressive", "pro_room"):
            lev_int = 20

        live_p = fetch_bingx_ticker(bx_symbol) or safe_float(sig.get("price"), safe_float(sig.get("entry"), 0.0))
        qty = compute_order_quantity(
            bx_symbol,
            live_p if live_p > 0 else 1.0,
            safe_float(sig.get("size"), 0.001),
            max(bal_val, 1.0),
            lev_int,
        )
        ok_guard, _guard_err = validate_order_execution_guards(bx_symbol, live_p, qty, expected_entry=safe_float(sig.get("entry"), 0.0))
        if not ok_guard:
            continue

        side = "BID" if action == "BUY" else "ASK"
        pos_side = "LONG" if action == "BUY" else "SHORT"

        sync_bingx_leverage_and_margin(bx_symbol, lev_int, pos_side)
        order_params = {
            "symbol": bx_symbol,
            "side": side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": qty,
        }
        if safe_float(sig.get("sl")) > 0:
            order_params["stopLoss"] = round(safe_float(sig.get("sl")), 6)
        if safe_float(sig.get("tp1")) > 0:
            order_params["takeProfit"] = round(safe_float(sig.get("tp1")), 6)

        _, err = bingx_signed_request("POST", "/openApi/swap/v2/trade/order", order_params)
        if not err:
            with STATE_LOCK:
                STATE["auto_executed_keys"].append(exec_key)
                persist_state_locked()
            executed.append(f"{bx_symbol} {action} {lev_int}x qty={qty}")
    return executed


def _evaluate_single_asset(category, sess, meta, timeframe, htf_tf, balance, risk_pct, cfg):
    is_open = bool(sess.get("is_open", True))
    sym = meta["symbol"]
    df, curr_price, resolved_bx = get_real_market_dataframe(meta, timeframe)
    if df is None or curr_price <= 0:
        err_reason = f"Dato live BingX non disponibile per {resolved_bx} ({timeframe})."
        return category, {
            "category": category,
            "symbol": sym,
            "bingx_symbol": resolved_bx,
            "name": meta["name"],
            "session_status": sess["status"],
            "days_label": sess.get("days_label", ""),
            "is_open": is_open,
            "price": None,
            "entry": None,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "tp1_progress_pct": 0.0,
            "series": [],
            "action": "NEUTRAL",
            "taken": False,
            "ai_score": "0.0%",
            "certainty_pct": 0.0,
            "profit_certainty_pct": 0.0,
            "certainty_level": "BASSA",
            "certainty_text": "0.0% (BASSA)",
            "filter_reason": err_reason,
            "params": {
                "profile": cfg["profile"],
                "timeframe": timeframe,
                "balance": balance,
                "risk": risk_pct,
                "min_score": cfg["min_score"],
            },
        }, None, True, err_reason

    htf_df = df if htf_tf == timeframe else fetch_bingx_klines(resolved_bx, htf_tf, limit=25)
    res = calculate_setup(df, curr_price, sym, balance, risk_pct, cfg, category, is_open, htf_df=htf_df, bx_override=resolved_bx)
    taken = res["action"] in ("BUY", "SELL") and is_open

    row_payload = {
        "category": category,
        "symbol": sym,
        "bingx_symbol": res["bingx_symbol"],
        "name": meta["name"],
        "session_status": sess["status"],
        "days_label": sess.get("days_label", ""),
        "is_open": is_open,
        "price": curr_price,
        "entry": res["entry"],
        "sl": res["sl"],
        "tp1": res["tp1"],
        "tp2": res["tp2"],
        "tp3": res["tp3"],
        "tp1_progress_pct": res["tp1_progress_pct"],
        "series": res["series"],
        "rrr": res["rrr"],
        "roi_pct": res["roi_pct"],
        "duration": res["duration"],
        "candle_tf_label": res["candle_tf_label"],
        "tp_eta_text": res["tp_eta_text"],
        "confluence": res["confluence"],
        "htf_bias": res["htf_bias"],
        "size": res["size"],
        "leverage": res["leverage"],
        "leverage_num": res["leverage_num"],
        "margin": res["margin"],
        "roundtrip_fee": res["roundtrip_fee"],
        "action": res["action"] if is_open else "NEUTRAL",
        "taken": taken,
        "ai_score": res["ai_score"],
        "certainty_pct": res["certainty_pct"],
        "profit_certainty_pct": res["profit_certainty_pct"],
        "loss_cooldown_rem": res["loss_cooldown_rem"],
        "certainty_level": res["certainty_level"],
        "certainty_text": res["certainty_text"],
        "rsi": res["rsi"],
        "vol_ratio": res["vol_ratio"],
        "p_win_pct": res["p_win_pct"],
        "ev_net_usd": res["ev_net_usd"],
        "kelly_pct": res["kelly_pct"],
        "filter_reason": res["filter_reason"],
        "params": {
            "profile": cfg["profile"],
            "timeframe": timeframe,
            "balance": balance,
            "risk": risk_pct,
            "min_score": cfg["min_score"],
        },
    }

    signal_item = None
    if taken:
        signal_item = {
            **row_payload,
            "id": f"{sym}_{res['action']}_{timeframe}",
            "sentiment": res["sentiment"],
            "sentiment_color": res["sentiment_color"],
            "applied_params": cfg,
        }
    return category, row_payload, signal_item, False, f"{sym}: {res['filter_reason']}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/live-ticker", methods=["GET"])
def api_live_ticker():
    sym = canonicalize_symbol_key(request.args.get("symbol", "BTCUSDT"))
    meta = ASSET_LOOKUP.get(sym, {"symbol": sym, "bx": resolve_bingx_symbol(sym)[0]})
    live_p, bx_used = fetch_bingx_ticker_multi(get_bingx_symbol_candidates(meta))
    if not live_p or live_p <= 0:
        return jsonify({"error": f"Prezzo live BingX non disponibile per {bx_used}"}), 503
    return jsonify({
        "symbol": sym,
        "bingx_symbol": bx_used,
        "price": live_p,
        "live": True,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


@app.route("/api/account/summary", methods=["GET"])
def api_account_summary():
    bal, err = fetch_bingx_account_balance()
    positions, pos_err = fetch_bingx_real_positions()
    with STATE_LOCK:
        auto_trade = STATE["auto_trade"]
        real_mode = STATE["real_mode"]
        max_daily_loss = STATE["max_daily_loss_usd"]
        max_open_pos = STATE["max_open_positions"]
    return jsonify({
        "api_configured": bool(BINGX_API_KEY and BINGX_API_SECRET),
        "real_mode": real_mode,
        "balance": bal,
        "balance_error": err,
        "positions": positions,
        "positions_error": pos_err,
        "auto_trade": auto_trade,
        "max_daily_loss_usd": max_daily_loss,
        "max_open_positions": max_open_pos,
    })


@app.route("/api/autotrade/toggle", methods=["POST"])
@app.route("/api/autotrade/config", methods=["POST"])
def api_autotrade_toggle():
    body = request.get_json(silent=True) or {}
    with STATE_LOCK:
        if "enabled" in body:
            STATE["auto_trade"] = bool(body["enabled"])
        elif "auto_trade" in body:
            STATE["auto_trade"] = bool(body["auto_trade"])
        elif request.path.endswith("/toggle"):
            STATE["auto_trade"] = not bool(STATE["auto_trade"])

        if "max_daily_loss_usd" in body:
            STATE["max_daily_loss_usd"] = max(0.5, safe_float(body["max_daily_loss_usd"], STATE["max_daily_loss_usd"]))
        if "max_open_positions" in body:
            STATE["max_open_positions"] = max(1, min(15, int(safe_float(body["max_open_positions"], STATE["max_open_positions"]))))
        if "max_slippage_pct" in body:
            STATE["max_slippage_pct"] = max(0.05, min(5.0, safe_float(body["max_slippage_pct"], STATE["max_slippage_pct"])))

        persist_state_locked()
        return jsonify({
            "success": True,
            "auto_trade": STATE["auto_trade"],
            "real_mode": STATE["real_mode"],
            "max_daily_loss_usd": STATE["max_daily_loss_usd"],
            "max_open_positions": STATE["max_open_positions"],
            "max_slippage_pct": STATE["max_slippage_pct"],
        })


@app.route("/api/autotrade/close-position", methods=["POST"])
def api_close_position():
    if not (BINGX_API_KEY and BINGX_API_SECRET):
        return jsonify({
            "success": False,
            "error": "Modalità reale attiva: imposta BINGX_API_KEY e BINGX_API_SECRET per chiudere posizioni su BingX.",
        }), 400
    body = request.get_json(silent=True) or {}
    sym = canonicalize_symbol_key(body.get("symbol", "BTCUSDT"))
    bx_symbol, _ = resolve_bingx_symbol(sym)
    action = (body.get("action") or "BUY").upper()
    qty = max(0.0001, safe_float(body.get("size"), 0.001))
    close_side = "ASK" if action == "BUY" else "BID"
    pos_side = "LONG" if action == "BUY" else "SHORT"
    order_data, err = bingx_signed_request(
        "POST",
        "/openApi/swap/v2/trade/order",
        {
            "symbol": bx_symbol,
            "side": close_side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": qty,
            "reduceOnly": "true",
        },
    )
    if err:
        return jsonify({"success": False, "error": f"Errore chiusura posizione BingX: {err}"}), 400
    return jsonify({"success": True, "order": order_data})


@app.route("/api/export/report", methods=["GET"])
def api_export_report():
    fmt = (request.args.get("format") or "json").lower()
    perf = build_performance_payload()
    with STATE_LOCK:
        closed_copy = list(STATE["closed_trades"])
        logs_copy = list(STATE["telemetry_logs"])

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "symbol", "realized_pnl", "closed_date", "closed_month", "closed_at"])
        for t in closed_copy:
            writer.writerow([
                t.get("id", ""),
                t.get("symbol", ""),
                t.get("realized_pnl", 0.0),
                t.get("closed_date", ""),
                t.get("closed_month", ""),
                t.get("closed_at", ""),
            ])
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=bingx_desk_report.csv"},
        )

    return jsonify({
        "performance": perf,
        "closed_trades": closed_copy,
        "telemetry_logs": logs_copy,
    })


@app.route("/api/signals", methods=["GET"])
def get_signals():
    global _LAST_GOOD_SIGNALS_PAYLOAD
    profile = request.args.get("profile", "pro_room")
    timeframe = request.args.get("timeframe", "15m")
    if timeframe not in ("5m", "15m", "1h"):
        timeframe = "15m"
    requested_balance = max(1.0, safe_float(request.args.get("balance"), 2.0))
    risk_pct = min(20.0, max(0.25, safe_float(request.args.get("risk"), 1.0)))
    min_score = min(99.0, max(1.0, safe_float(request.args.get("min_score"), 70.0)))
    market_filter = request.args.get("market", "ALL")
    symbol_filter = request.args.get("symbol", "ALL")

    cache_key = f"{profile}_{timeframe}_{market_filter}_{symbol_filter}_{round(requested_balance, 2)}_{round(risk_pct, 2)}_{round(min_score, 1)}"
    with _CACHE_LOCK:
        cached_resp = _SIGNALS_RESPONSE_CACHE.get(cache_key)
        if cached_resp and (time.time() - cached_resp["ts"]) < 12.0:
            return jsonify(cached_resp["payload"])

    account_bal, _ = fetch_bingx_account_balance()
    live_equity = safe_float(account_bal.get("equity"), 0.0) if isinstance(account_bal, dict) else 0.0
    effective_balance = live_equity if live_equity > 0 else requested_balance

    cfg = build_profile_config(profile, timeframe, min_score, risk_pct=risk_pct)
    sessions = get_session_statuses()
    macro_windows = get_macro_windows(datetime.now(timezone.utc))
    htf_tf = "1h" if (timeframe != "1h" and market_filter != "ALL") else timeframe

    tasks = []
    for category, assets in MARKET_CATALOG.items():
        sess = sessions.get(category, {"status": "APERTO LIVE", "is_open": True, "hours": "24/7"})
        for meta in assets:
            sym = meta["symbol"]
            if market_filter != "ALL" and category != market_filter:
                continue
            if symbol_filter != "ALL" and sym != symbol_filter:
                continue
            tasks.append((category, sess, meta))

    signals_out = []
    monitoring_rows = []

    if tasks:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [
                pool.submit(_evaluate_single_asset, cat, sess, meta, timeframe, htf_tf, effective_balance, risk_pct, cfg)
                for cat, sess, meta in tasks
            ]
            for fut in as_completed(futs):
                try:
                    _cat, row_payload, sig_item, _is_err, _reason_txt = fut.result(timeout=10.0)
                except Exception:
                    continue
                monitoring_rows.append(row_payload)
                if sig_item is not None:
                    signals_out.append(sig_item)

    signals_out.sort(key=lambda x: safe_float(x.get("profit_certainty_pct"), 0.0), reverse=True)

    perf_payload = build_performance_payload()
    live_positions, _ = fetch_bingx_real_positions()

    with STATE_LOCK:
        daily_net = safe_float(perf_payload.get("daily", {}).get("net_pnl"), 0.0)
        max_loss_val = abs(safe_float(STATE.get("max_daily_loss_usd"), 5.0))
        max_open_pos_val = int(STATE.get("max_open_positions", 3))
        kill_switch_hit = daily_net <= -max_loss_val
        if kill_switch_hit and STATE.get("auto_trade"):
            STATE["auto_trade"] = False
            persist_state_locked()

    auto_executed = maybe_run_auto_trade_loop(signals_out, live_positions, kill_switch_hit, account_balance=effective_balance)
    be_updates = run_break_even_trailing_on_positions(live_positions, monitoring_rows)

    now_utc_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    with STATE_LOCK:
        STATE["scans_count"] += 1
        scans_val = STATE["scans_count"]
        session_pnl_val = STATE["session_pnl"]
        extra_info = ""
        if auto_executed:
            extra_info += f" | AUTO: {', '.join(auto_executed)}"
        if be_updates:
            extra_info += f" | BE: {', '.join(be_updates)}"
        STATE["telemetry_logs"] = ([{
            "timestamp": now_utc_str,
            "status": f"LIVE SCAN {timeframe} ({profile} • Cap ${effective_balance:.2f} • Risk {risk_pct}% • MinScore {min_score}%){' [KILL-SWITCH]' if kill_switch_hit else ''}{extra_info}",
            "signals_found": len(signals_out),
            "scanned": len(monitoring_rows),
        }] + STATE["telemetry_logs"])[:30]
        logs_copy = list(STATE["telemetry_logs"])

    payload_out = {
        "sessions": sessions,
        "macro_windows": macro_windows,
        "signals": signals_out,
        "active_params": cfg,
        "market_monitoring_table": monitoring_rows,
        "performance": perf_payload,
        "kill_switch_hit": kill_switch_hit,
        "max_daily_loss_usd": max_loss_val,
        "max_open_positions": max_open_pos_val,
        "auto_executed": auto_executed,
        "be_updates": be_updates,
        "account_balance": account_bal,
        "telemetry": {
            "scans_count": scans_val,
            "active_orders_count": len(live_positions),
            "session_pnl": session_pnl_val,
            "telemetry_logs": logs_copy,
        },
    }

    with _CACHE_LOCK:
        if monitoring_rows:
            _LAST_GOOD_SIGNALS_PAYLOAD = payload_out
            _SIGNALS_RESPONSE_CACHE[cache_key] = {"ts": time.time(), "payload": payload_out}
        elif _LAST_GOOD_SIGNALS_PAYLOAD is not None:
            fallback = dict(_LAST_GOOD_SIGNALS_PAYLOAD)
            fallback["telemetry"] = payload_out["telemetry"]
            return jsonify(fallback)

    return jsonify(payload_out)


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

    perf = build_performance_payload()
    daily_net = safe_float(perf.get("daily", {}).get("net_pnl"), 0.0)
    with STATE_LOCK:
        max_loss = abs(safe_float(STATE.get("max_daily_loss_usd"), 5.0))
        max_open_pos = int(STATE.get("max_open_positions", 3))

    if daily_net <= -max_loss:
        return jsonify({
            "success": False,
            "error": f"Kill-switch giornaliero attivo: perdita netta oggi (${daily_net:.2f}) <= -${max_loss:.2f}.",
        }), 400

    positions, _ = fetch_bingx_real_positions()
    if len(positions) >= max_open_pos:
        return jsonify({
            "success": False,
            "error": f"Limite posizioni simultanee raggiunto ({len(positions)}/{max_open_pos}).",
        }), 400

    body = request.get_json(silent=True) or {}
    sym = canonicalize_symbol_key(body.get("symbol", "BTCUSDT"))
    loss_cd_rem = get_symbol_loss_cooldown_remaining(sym)
    if loss_cd_rem > 0:
        return jsonify({
            "success": False,
            "error": f"Cooldown post-perdita attivo su {sym}: attendi ancora {loss_cd_rem}s prima di riaprire.",
        }), 400

    bx_symbol, _ = resolve_bingx_symbol(sym)
    action = (body.get("action") or "BUY").upper()
    if action not in ("BUY", "SELL"):
        action = "BUY"

    lev_int = max(1, min(20, int(safe_float(body.get("leverage_num"), 5))))
    account_bal, _ = fetch_bingx_account_balance()
    bal_val = safe_float(account_bal.get("equity"), 2.0) if isinstance(account_bal, dict) else 2.0
    curr_p = fetch_bingx_ticker(bx_symbol) or safe_float(body.get("entry"), 0.0)
    qty = compute_order_quantity(
        bx_symbol,
        curr_p if curr_p > 0 else 1.0,
        safe_float(body.get("size"), 0.001),
        max(bal_val, 1.0),
        lev_int,
    )

    ok_guard, guard_err = validate_order_execution_guards(
        bx_symbol,
        curr_p,
        qty,
        expected_entry=safe_float(body.get("entry"), 0.0),
    )
    if not ok_guard:
        return jsonify({"success": False, "error": guard_err}), 400

    side = "BID" if action == "BUY" else "ASK"
    pos_side = "LONG" if action == "BUY" else "SHORT"

    sync_bingx_leverage_and_margin(bx_symbol, lev_int, pos_side)

    order_params = {
        "symbol": bx_symbol,
        "side": side,
        "positionSide": pos_side,
        "type": "MARKET",
        "quantity": qty,
    }
    if safe_float(body.get("sl")) > 0:
        order_params["stopLoss"] = round(safe_float(body.get("sl")), 6)
    if safe_float(body.get("tp1")) > 0:
        order_params["takeProfit"] = round(safe_float(body.get("tp1")), 6)

    order_data, err = bingx_signed_request("POST", "/openApi/swap/v2/trade/order", order_params)
    if err:
        return jsonify({"success": False, "error": f"Errore BingX ordine reale: {err}"}), 400

    return jsonify({"success": True, "mode": "BINGX REAL LIVE", "order": order_data})


@app.route("/api/autotrade/status", methods=["GET"])
def api_autotrade_status():
    positions, pos_err = fetch_bingx_real_positions()
    perf = build_performance_payload()
    with STATE_LOCK:
        closed_copy = list(STATE["closed_trades"])
        auto_trade = STATE["auto_trade"]
        max_loss = STATE["max_daily_loss_usd"]
        max_open_pos = STATE["max_open_positions"]
    return jsonify({
        "auto_trade": auto_trade,
        "max_daily_loss_usd": max_loss,
        "max_open_positions": max_open_pos,
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


load_persisted_state()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
