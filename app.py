import hashlib
import hmac
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request


def _load_optional_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


def _get_env_first(keys: List[str], default: str = "") -> Tuple[str, str]:
    for key in keys:
        raw = os.getenv(key)
        if raw is not None:
            cleaned = raw.strip().strip('"').strip("'")
            if cleaned:
                return cleaned, key
    return default, ""


_load_optional_dotenv()

app = Flask(__name__)

BINGX_BASE_URL, _BASE_URL_SOURCE = _get_env_first(
    ["BINGX_BASE_URL", "BINGX_API_URL"],
    default="https://open-api.bingx.com",
)
BINGX_BASE_URL = BINGX_BASE_URL.rstrip("/")

BINGX_API_KEY, _API_KEY_SOURCE = _get_env_first(
    ["BINGX_API_KEY", "BINGX_KEY", "BX_API_KEY", "API_KEY"],
    default="",
)
BINGX_API_SECRET, _API_SECRET_SOURCE = _get_env_first(
    ["BINGX_SECRET_KEY", "BINGX_API_SECRET", "BINGX_SECRET", "BX_API_SECRET", "API_SECRET"],
    default="",
)

MIN_ORDER_CAPITAL_USD = 2.0

MARKET_GROUPS: List[str] = [
    "Cripto",
    "Forex",
    "Materie Prime",
    "Azioni & Indici",
]

FX_BASES = {
    "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "ZAR", "KRW", "INR", "SGD", "NZD", "CNH",
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "GBPJPY",
}

COMMODITY_BASES = {
    "XAU", "XAG", "XPT", "XPD", "XAL", "XZN", "XPB", "XNI", "XCU",
    "GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER",
    "OILWTI", "OILBRENT", "WTI", "BRENT", "USOIL", "UKOIL", "NATGAS", "NG", "HEATINGOIL",
    "XAUEUR", "XAUZAR", "XAUJPY", "XAUCAD", "XAUAUD", "XAUSGD", "XAUKRW", "XAUCHF", "XAUINR", "XAUGBP",
    "XAUXAG", "XAUXPT", "XAGEUR", "XAGJPY", "XAUT",
}

STOCK_INDEX_BASES = {
    "NVDA", "AAPL", "TSLA", "GOOGL", "GOOG", "MSFT", "META", "AMZN", "AMD", "AVGO", "INTC",
    "NFLX", "COIN", "MSTR", "PLTR", "BABA", "BA", "DIS", "JPM", "GS",
    "MU", "SKHYNIX", "SNDK", "QQQ", "SPY", "IWM",
    "NAS100", "NASDAQ100", "SP500", "US500", "NDX", "USTEC", "DOWJONES", "DJ30", "US30", "NIKKEI225", "JP225", "GER40", "DE40", "UK100",
}

TF_SECONDS: Dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}

TF_BINGX_INTERVAL: Dict[str, str] = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
}

STATE_LOCK = threading.Lock()
TERMINAL_STATE: Dict[str, Any] = {
    "auto_trade": False,
    "scans_count": 0,
    "telemetry_logs": [],
    "risk_config": {
        "max_open_positions": 1,
        "daily_loss_cap_usd": 0.50,
        "only_a_plus": False,
        "close_at_candle_end": True,
        "tp_split_pct": [50.0, 30.0, 20.0],
    },
    "tracked_positions": {},
    "closed_trades_today": [],
    "closed_trades_month": [],
    "last_income_sync_ts": 0,
    "last_contracts_sync_ts": 0,
    "bingx_contracts": {},
    "last_params": {
        "profile": "pro_sniper",
        "timeframe": "15m",
        "balance": MIN_ORDER_CAPITAL_USD,
        "risk": 1.0,
        "min_score": 70.0,
        "min_rrr": 1.5,
    },
}


def _credentials_configured() -> bool:
    return bool(BINGX_API_KEY and BINGX_API_SECRET)


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 10) -> Tuple[bool, Any, str]:
    req = Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(raw), ""
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return False, None, f"HTTP {e.code}: {body[:200] or e.reason}"
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        return False, None, str(e)


def _classify_market_group(bx_sym: str) -> str:
    up = str(bx_sym or "").strip().upper()
    base = up.split("-")[0] if "-" in up else (up[:-4] if up.endswith("USDT") else up)
    if base in COMMODITY_BASES or up.startswith(("XAU", "XAG", "XPT", "XPD", "XCU", "XAUT", "OIL", "WTI", "BRENT", "NATGAS")):
        return "Materie Prime"
    if base in STOCK_INDEX_BASES or up.startswith(("NAS100", "NASDAQ100", "SP500", "US500", "DOWJONES", "DJ30", "US30", "NIKKEI225", "JP225")):
        return "Azioni & Indici"
    if base in FX_BASES:
        return "Forex"
    return "Cripto"


def _step_decimals(step: float) -> int:
    try:
        d = Decimal(str(step)).normalize()
        exp = -int(d.as_tuple().exponent)
        return max(0, min(8, exp))
    except Exception:
        return 4


def _sync_bingx_contracts(force: bool = False) -> Dict[str, Dict[str, Any]]:
    now_s = int(time.time())
    with STATE_LOCK:
        cached = dict(TERMINAL_STATE.get("bingx_contracts") or {})
        last_ts = int(TERMINAL_STATE.get("last_contracts_sync_ts") or 0)
        if not force and cached and (now_s - last_ts) < 120:
            return cached

    url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/contracts"
    ok, data, _ = _http_get_json(url, timeout=12)
    raw_list = data.get("data") if (ok and isinstance(data, dict)) else None

    contracts: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "").strip().upper()
            if not sym:
                continue
            if sym == "XAUT-USDT" and force:
                print(f"[BINGX RAW CONTRACT XAUT-USDT] {json.dumps(item)}", flush=True)
            status_val = str(item.get("status") or item.get("apiStateOpen") or "1").lower()
            if status_val in ("0", "false", "offline", "suspended", "delisted"):
                continue

            raw_step = item.get("stepSize") or item.get("tradeMinQuantity") or item.get("minTradeQuantity") or 0.001
            step_size = float(raw_step)
            min_qty = float(item.get("minQty") or item.get("tradeMinQuantity") or step_size or 0.001)
            min_notional = float(item.get("minNotional") or item.get("notional") or MIN_ORDER_CAPITAL_USD)
            step_dec = _step_decimals(step_size)
            raw_qty_prec = item.get("quantityPrecision")
            qty_prec = int(raw_qty_prec) if raw_qty_prec is not None else step_dec
            if step_size > 0:
                qty_prec = min(max(0, min(8, qty_prec)), step_dec)
            else:
                qty_prec = max(0, min(8, qty_prec))
            price_prec = int(item.get("pricePrecision") if item.get("pricePrecision") is not None else 4)
            group_name = _classify_market_group(sym)

            contracts[sym] = {
                "symbol": sym,
                "market": group_name,
                "step_size": max(1e-8, step_size),
                "min_qty": max(1e-8, min_qty),
                "min_notional": max(MIN_ORDER_CAPITAL_USD, min_notional),
                "qty_precision": qty_prec,
                "price_precision": max(0, min(8, price_prec)),
                "raw": item,
            }

    with STATE_LOCK:
        if contracts:
            TERMINAL_STATE["bingx_contracts"] = contracts
            TERMINAL_STATE["last_contracts_sync_ts"] = now_s

    return contracts or cached


def _resolve_bingx_symbol_and_contract(symbol: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    contracts = _sync_bingx_contracts()
    clean = (symbol or "BTC-USDT").strip().upper()
    candidates: List[str] = []

    if "/" in clean:
        base, quote = clean.split("/", 1)
        candidates.append(f"{base.strip()}-{quote.strip()}")
        candidates.append(f"{base.strip()}{quote.strip()}")
    else:
        candidates.append(clean)
        if "-" not in clean and clean.endswith("USDT"):
            candidates.append(f"{clean[:-4]}-USDT")
        elif "-" not in clean:
            candidates.append(f"{clean}-USDT")

    for cand in candidates:
        c_up = cand.upper()
        if c_up in contracts:
            return c_up, contracts[c_up]

    first = candidates[0] if candidates else "BTC-USDT"
    if "/" in first:
        first = first.replace("/", "-")
    if "-" not in first:
        first = f"{first}-USDT"
    return first, None


def _all_bingx_assets() -> List[Dict[str, Any]]:
    contracts = _sync_bingx_contracts()
    priority = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "XAUT-USDT"]
    ordered_keys: List[str] = [s for s in priority if s in contracts]
    for s in sorted(contracts.keys()):
        if s not in ordered_keys:
            ordered_keys.append(s)

    out: List[Dict[str, Any]] = []
    for bx_sym in ordered_keys:
        c = contracts[bx_sym]
        base = bx_sym.split("-")[0] if "-" in bx_sym else bx_sym
        quote = bx_sym.split("-")[1] if "-" in bx_sym else "USDT"
        out.append({
            "symbol": f"{base}/{quote}",
            "bx_symbol": bx_sym,
            "name": f"{base} ({bx_sym})",
            "market": c["market"],
            "category": c["market"],
            "contract": c,
        })
    return out


def _compute_leverage(balance: float) -> int:
    if balance <= MIN_ORDER_CAPITAL_USD:
        return 20
    if balance <= 10.0:
        return 15
    if balance <= 50.0:
        return 10
    return 5


def _effective_qty_precision(step: float, precision: int) -> int:
    p = max(0, min(8, int(precision)))
    if step and step > 0:
        return min(p, _step_decimals(step))
    return p


def _round_to_step(val: float, step: float, precision: int, rounding_mode=ROUND_DOWN) -> float:
    eff_prec = _effective_qty_precision(step, precision)
    q_fmt = Decimal("1") if eff_prec == 0 else Decimal("1." + ("0" * eff_prec))
    safe_val = max(Decimal("0"), Decimal(str(val or 0.0)))
    if step and step > 0:
        d_step = Decimal(str(step))
        steps_count = (safe_val / d_step).to_integral_value(rounding=rounding_mode)
        if steps_count < Decimal("1"):
            steps_count = Decimal("1")
        rounded = (steps_count * d_step).quantize(q_fmt, rounding=ROUND_DOWN)
        return float(rounded)
    return float(safe_val.quantize(q_fmt, rounding=rounding_mode))


def _fmt_qty(val: float, precision: int, step: float = 0.0) -> str:
    eff_prec = _effective_qty_precision(step, precision)
    q_fmt = Decimal("1") if eff_prec == 0 else Decimal("1." + ("0" * eff_prec))
    d_val = max(Decimal("0"), Decimal(str(val or 0.0))).quantize(q_fmt, rounding=ROUND_DOWN)
    s_val = format(d_val, "f")
    if "." in s_val:
        s_val = s_val.rstrip("0").rstrip(".")
    return s_val or "0.001"


def _normalize_order_qty(
    raw_qty: float,
    price: float,
    leverage: int,
    balance: float,
    contract: Optional[Dict[str, Any]],
) -> Tuple[bool, float, str]:
    step = float(contract["step_size"]) if contract else 0.001
    min_qty = float(contract["min_qty"]) if contract else max(0.001, step)
    prec = _effective_qty_precision(step, int(contract["qty_precision"]) if contract else 4)
    min_notional = float(contract["min_notional"]) if contract else MIN_ORDER_CAPITAL_USD

    safe_price = max(float(price or 0.0), 1e-8)
    effective_margin = max(float(balance or 0.0), MIN_ORDER_CAPITAL_USD)
    effective_lev = max(int(leverage or 20), 1)

    # Calcola la quantità a partire dal margine minimo di $2.00 e dalla leva applicata
    base_margin_qty = (effective_margin * effective_lev) / safe_price
    requested_qty = float(raw_qty or 0.0)
    target_qty = max(min(requested_qty, base_margin_qty) if requested_qty > 0 else base_margin_qty, base_margin_qty, min_qty)

    qty = _round_to_step(target_qty, step, prec, rounding_mode=ROUND_UP)
    if qty < min_qty:
        qty = _round_to_step(min_qty, step, prec, rounding_mode=ROUND_UP)

    d_step = Decimal(str(step)) if step > 0 else Decimal("0.001")
    d_price = Decimal(str(safe_price))
    d_min_notional = Decimal(str(min_notional))
    d_qty = Decimal(str(qty))
    q_fmt = Decimal("1") if prec == 0 else Decimal("1." + ("0" * prec))

    guard = 0
    while (d_qty * d_price) < d_min_notional and guard < 2000:
        d_qty = (d_qty + d_step).quantize(q_fmt, rounding=ROUND_DOWN)
        guard += 1

    qty = float(d_qty)
    if qty <= 0:
        qty = max(step, round(base_margin_qty, max(prec, 4)))
    if effective_margin < MIN_ORDER_CAPITAL_USD:
        return False, 0.0, f"Capitale minimo ordine ${MIN_ORDER_CAPITAL_USD:.2f}"
    return True, qty, ""


def _candle_timing(timeframe: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    tf = timeframe if timeframe in TF_SECONDS else "15m"
    dur = TF_SECONDS[tf]
    now_s = int(now_ts if now_ts is not None else time.time())
    open_ts = (now_s // dur) * dur
    close_ts = open_ts + dur
    rem = max(0, close_ts - now_s)
    open_dt = datetime.fromtimestamp(open_ts, tz=timezone.utc).strftime("%H:%M:%S")
    close_dt = datetime.fromtimestamp(close_ts, tz=timezone.utc).strftime("%H:%M:%S")
    return {
        "timeframe": tf,
        "candle_duration_sec": dur,
        "candle_duration_label": f"{tf} ({dur}s)",
        "candle_open_ts": open_ts,
        "candle_close_ts": close_ts,
        "candle_remaining_sec": rem,
        "candle_open_utc": open_dt,
        "candle_close_utc": close_dt,
        "candle_window_text": f"Candela {tf}: {open_dt} → {close_dt} UTC",
    }


def _bingx_signed_request(method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, Any, str]:
    if not _credentials_configured():
        return (
            False,
            None,
            "Chiavi API BingX non rilevate (imposta BINGX_API_KEY e BINGX_SECRET_KEY)",
        )

    q_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    q_params["timestamp"] = int(time.time() * 1000)
    query_str = urlencode(sorted(q_params.items()))
    signature = hmac.new(
        BINGX_API_SECRET.encode("utf-8"),
        query_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    signed_url = f"{BINGX_BASE_URL}{path}?{query_str}&signature={signature}"
    headers = {
        "X-BX-APIKEY": BINGX_API_KEY,
        "Content-Type": "application/json",
    }
    req = Request(signed_url, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("code") not in (0, "0", None):
                err_msg = payload.get("msg") or f"BingX code {payload.get('code')}"
                print(f"[BINGX ERR] {method.upper()} {path} -> {err_msg} | payload={raw[:400]}", flush=True)
                return False, payload, err_msg
            return True, payload, ""
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        err_msg = f"HTTP {e.code}: {body[:250] or e.reason}"
        print(f"[BINGX HTTP] {method.upper()} {path} -> {err_msg}", flush=True)
        return False, None, err_msg
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"[BINGX NET] {method.upper()} {path} -> {e}", flush=True)
        return False, None, str(e)


def _ema_series(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1.0 - k))
    return ema_vals


def _fetch_bingx_klines(bx_sym: str, timeframe: str, limit: int = 60) -> List[Dict[str, Any]]:
    tf = timeframe if timeframe in TF_BINGX_INTERVAL else "15m"
    interval = TF_BINGX_INTERVAL[tf]
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/klines?symbol={bx_sym}&interval={interval}&limit={limit}"
    ok, data, _ = _http_get_json(url, timeout=7)
    raw_list = data.get("data") if (ok and isinstance(data, dict)) else None
    candles: List[Dict[str, Any]] = []
    if isinstance(raw_list, list):
        for item in raw_list[-limit:]:
            if isinstance(item, dict):
                op = float(item.get("open", 0) or 0)
                hi = float(item.get("high", 0) or 0)
                lo = float(item.get("low", 0) or 0)
                cl = float(item.get("close", 0) or 0)
                vol = float(item.get("volume", 0) or 0)
                ts = int(float(item.get("time", 0) or 0)) // 1000
            elif isinstance(item, list) and len(item) >= 6:
                ts = int(float(item[0] or 0)) // 1000
                op = float(item[1] or 0)
                hi = float(item[2] or 0)
                lo = float(item[3] or 0)
                cl = float(item[4] or 0)
                vol = float(item[5] or 0)
            else:
                continue
            if cl > 0:
                candles.append({
                    "ts": ts,
                    "open": op if op > 0 else cl,
                    "high": hi if hi > 0 else cl,
                    "low": lo if lo > 0 else cl,
                    "close": cl,
                    "volume": vol,
                })
        candles.sort(key=lambda x: x["ts"])

    closes = [c["close"] for c in candles]
    ema20 = _ema_series(closes, 20)
    ema50 = _ema_series(closes, 50)
    for idx, c in enumerate(candles):
        c["ema20"] = ema20[idx]
        c["ema50"] = ema50[idx]
    return candles


def _get_live_mark_or_last_price(bx_sym: str) -> float:
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker?symbol={bx_sym}"
    ok, data, _ = _http_get_json(url, timeout=5)
    if ok and isinstance(data, dict) and isinstance(data.get("data"), dict):
        d = data["data"]
        p = float(d.get("lastPrice") or d.get("markPrice") or d.get("askPrice") or d.get("bidPrice") or 0)
        if p > 0:
            return p
    return 0.0


def _round_price(val: float, ref_price: float) -> float:
    if ref_price >= 1000:
        return round(val, 2)
    if ref_price >= 50:
        return round(val, 2)
    if ref_price >= 1:
        return round(val, 4)
    return round(val, 6)


def _validate_directional_levels(action: str, entry: float, sl: float, tp1: float, tp2: float, tp3: float) -> Tuple[float, float, float, float]:
    eps = max(entry * 0.0005, 1e-6)
    if action == "BUY":
        s_val = min(sl, entry - eps)
        t1 = max(tp1, entry + eps)
        t2 = max(tp2, t1 + eps)
        t3 = max(tp3, t2 + eps)
        return s_val, t1, t2, t3
    if action == "SELL":
        s_val = max(sl, entry + eps)
        t1 = min(tp1, entry - eps)
        t2 = min(tp2, t1 - eps)
        t3 = min(tp3, t2 - eps)
        return s_val, t1, t2, t3
    return sl, tp1, tp2, tp3


def _evaluate_asset(
    asset: Dict[str, Any],
    profile: str,
    timeframe: str,
    balance: float,
    risk_pct: float,
    min_score: float,
    min_rrr: float,
) -> Optional[Dict[str, Any]]:
    bx_sym = asset["bx_symbol"]
    contract = asset.get("contract")
    candles = _fetch_bingx_klines(bx_sym, timeframe, limit=60)
    if len(candles) < 2:
        live_p = _get_live_mark_or_last_price(bx_sym)
        if live_p <= 0:
            return None
        candles = [
            {"ts": int(time.time()) - 900, "open": live_p, "high": live_p, "low": live_p, "close": live_p, "volume": 0.0, "ema20": live_p, "ema50": live_p},
            {"ts": int(time.time()), "open": live_p, "high": live_p, "low": live_p, "close": live_p, "volume": 0.0, "ema20": live_p, "ema50": live_p},
        ]

    timing = _candle_timing(timeframe)
    last = candles[-1]
    prev = candles[-2]
    price = last["close"]
    e20 = last["ema20"]
    e50 = last["ema50"]

    atr_samples = [
        max(c["high"] - c["low"], abs(c["high"] - candles[max(0, i - 1)]["close"]), abs(c["low"] - candles[max(0, i - 1)]["close"]))
        for i, c in enumerate(candles[-14:])
    ]
    atr = max(sum(atr_samples) / max(1, len(atr_samples)), price * 0.0012)

    bull_trend = price > e20 and e20 >= e50
    bear_trend = price < e20 and e20 <= e50

    body_last = abs(last["close"] - last["open"])
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    fakeout = (upper_wick > body_last * 1.8 and bull_trend) or (lower_wick > body_last * 1.8 and bear_trend)

    momentum_pct = ((price - prev["close"]) / max(prev["close"], 1e-9)) * 100.0
    base_score = 54.0
    if bull_trend or bear_trend:
        base_score += 18.0
    if abs(momentum_pct) >= 0.08:
        base_score += 8.0
    if profile == "pro_sniper":
        base_score += 5.0
    elif profile == "institutional_swing":
        base_score += 3.0
    if fakeout:
        base_score -= 14.0

    sym_bias = (sum(ord(ch) for ch in bx_sym) % 19) - 9
    score = round(max(35.0, min(94.0, base_score + sym_bias)), 1)

    if bull_trend and not fakeout and score >= min_score:
        action = "BUY"
    elif bear_trend and not fakeout and score >= min_score:
        action = "SELL"
    else:
        action = "NEUTRAL"

    sl_dist = max(atr * 1.15, price * 0.0025)
    tp1_dist = max(sl_dist * max(min_rrr, 1.5), atr * 1.8)
    tp2_dist = sl_dist * max(min_rrr * 1.5, 2.3)
    tp3_dist = sl_dist * max(min_rrr * 2.0, 3.1)

    entry = _round_price(price, price)
    if action == "SELL":
        raw_sl = price + sl_dist
        raw_tp1 = price - tp1_dist
        raw_tp2 = price - tp2_dist
        raw_tp3 = price - tp3_dist
    else:
        raw_sl = max(0.000001, price - sl_dist)
        raw_tp1 = price + tp1_dist
        raw_tp2 = price + tp2_dist
        raw_tp3 = price + tp3_dist

    if action in ("BUY", "SELL"):
        raw_sl, raw_tp1, raw_tp2, raw_tp3 = _validate_directional_levels(action, entry, raw_sl, raw_tp1, raw_tp2, raw_tp3)

    sl = _round_price(raw_sl, price)
    tp1 = _round_price(raw_tp1, price)
    tp2 = _round_price(raw_tp2, price)
    tp3 = _round_price(raw_tp3, price)

    rrr = round(abs(tp1 - entry) / max(abs(entry - sl), 1e-9), 2)
    order_capital = max(float(balance or MIN_ORDER_CAPITAL_USD), MIN_ORDER_CAPITAL_USD)
    leverage = _compute_leverage(order_capital)
    raw_qty = (order_capital * leverage) / max(price, 1e-9)
    _, size, _ = _normalize_order_qty(raw_qty, price, leverage, order_capital, contract)
    margin = round(max(MIN_ORDER_CAPITAL_USD, (size * price) / max(leverage, 1)), 2)

    p_win = max(0.35, min(0.85, score / 100.0))
    win_usd = abs(tp1 - entry) * size * leverage * 0.25
    loss_usd = abs(entry - sl) * size * leverage * 0.25
    ev_net_usd = round((p_win * win_usd) - ((1.0 - p_win) * loss_usd), 4)
    kelly_pct = round(max(0.5, min(12.0, (p_win - ((1.0 - p_win) / max(rrr, 0.5))) * 100.0)), 1)
    certainty_pct = round(max(25.0, min(96.0, score * 0.92 + (4.0 if not fakeout else -12.0))), 1)

    if score >= 85 and not fakeout:
        grade = "A+"
    elif score >= 75:
        grade = "A"
    elif score >= 65:
        grade = "B"
    else:
        grade = "C"

    htf_bias = "BULLISH" if bull_trend else ("BEARISH" if bear_trend else "RANGE")
    reason = (
        f"Setup {action} valido (Score {score}%, R:R 1:{rrr})"
        if action != "NEUTRAL"
        else ("Rischio fakeout / coda lunga" if fakeout else f"Filtro Score/Trend non allineato ({score}% < {min_score}%)")
    )

    return {
        "symbol": asset["symbol"],
        "bx_symbol": bx_sym,
        "name": asset["name"],
        "market": asset["market"],
        "category": asset["market"],
        "price": _round_price(price, price),
        "htf_bias": htf_bias,
        "action": action,
        "score": score,
        "grade": grade,
        "certainty_pct": certainty_pct,
        "fakeout": fakeout,
        "fakeout_label": "FAKEOUT RISK" if fakeout else "STRUTTURA PULITA",
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rrr": rrr,
        "size": size,
        "leverage": f"{leverage}x",
        "leverage_num": leverage,
        "margin": margin,
        "ev_net_usd": ev_net_usd,
        "kelly_pct": kelly_pct,
        "tp_eta_text": f"~{max(1, timing['candle_remaining_sec'] // 60)}-{max(2, (timing['candle_remaining_sec'] // 60) + 2)} candele {timing['timeframe']}",
        "reason": reason,
        "timeframe": timing["timeframe"],
        "candle_duration_label": timing["candle_duration_label"],
        "candle_open_utc": timing["candle_open_utc"],
        "candle_close_utc": timing["candle_close_utc"],
        "candle_close_ts": timing["candle_close_ts"],
        "candle_remaining_sec": timing["candle_remaining_sec"],
        "candle_window_text": timing["candle_window_text"],
    }


def _sync_bingx_income_history() -> None:
    if not _credentials_configured():
        return
    now_s = int(time.time())
    with STATE_LOCK:
        if now_s - int(TERMINAL_STATE.get("last_income_sync_ts", 0)) < 15:
            return
        TERMINAL_STATE["last_income_sync_ts"] = now_s

    start_ms = (now_s - 86400 * 30) * 1000
    ok, payload, _ = _bingx_signed_request("GET", "/openApi/swap/v2/user/income", {
        "startTime": start_ms,
        "limit": 100,
    })
    if not ok or not isinstance(payload, dict):
        return

    items = payload.get("data")
    if not isinstance(items, list):
        return

    today_start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today_trades: List[Dict[str, Any]] = []
    month_trades: List[Dict[str, Any]] = []

    for row in items:
        inc_type = str(row.get("incomeType", "")).upper()
        if inc_type not in ("REALIZED_PNL", "TRADING_FEE", "COMMISSION", "FUNDING_FEE"):
            continue
        amt = float(row.get("income", 0) or 0)
        ts_sec = int(float(row.get("time", 0) or 0)) // 1000
        sym = str(row.get("symbol", "BINGX"))
        is_fee = inc_type in ("TRADING_FEE", "COMMISSION", "FUNDING_FEE")
        rec = {
            "symbol": sym,
            "pnl": 0.0 if is_fee else amt,
            "fee": abs(amt) if is_fee else 0.0,
            "type": inc_type,
            "ts": ts_sec,
        }
        month_trades.append(rec)
        if ts_sec >= today_start:
            today_trades.append(rec)

    with STATE_LOCK:
        if month_trades:
            TERMINAL_STATE["closed_trades_month"] = month_trades
            TERMINAL_STATE["closed_trades_today"] = today_trades


def _fetch_bingx_positions() -> Tuple[List[Dict[str, Any]], float, float, str]:
    ok, payload, err = _bingx_signed_request("GET", "/openApi/swap/v2/user/positions", {})
    if not ok:
        return [], 0.0, 0.0, err

    raw_positions = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(raw_positions, list):
        raw_positions = []

    positions: List[Dict[str, Any]] = []
    total_upnl = 0.0
    used_margin = 0.0

    with STATE_LOCK:
        tracked_map = dict(TERMINAL_STATE["tracked_positions"])

    for p in raw_positions:
        amt = abs(float(p.get("positionAmt", 0) or 0))
        if amt <= 0:
            continue
        bx_sym = str(p.get("symbol", "")).upper()
        base = bx_sym.split("-")[0] if "-" in bx_sym else bx_sym
        quote = bx_sym.split("-")[1] if "-" in bx_sym else "USDT"
        disp_sym = f"{base}/{quote}"

        side_raw = str(p.get("positionSide", "")).upper()
        if side_raw in ("LONG", "BUY"):
            action = "BUY"
        elif side_raw in ("SHORT", "SELL"):
            action = "SELL"
        else:
            action = "BUY" if float(p.get("positionAmt", 0) or 0) > 0 else "SELL"

        entry = float(p.get("avgPrice", 0) or p.get("entryPrice", 0) or 0)
        mark = float(p.get("markPrice", 0) or entry)
        upnl = float(p.get("unrealizedProfit", 0) or 0)
        lev = int(float(p.get("leverage", 20) or 20))
        margin = float(p.get("initialMargin", 0) or ((amt * mark) / max(lev, 1)))

        total_upnl += upnl
        used_margin += margin

        meta = tracked_map.get(disp_sym, {})
        tp1 = float(meta.get("tp1", meta.get("tp", 0)) or 0)
        tp2 = float(meta.get("tp2", 0) or 0)
        tp3 = float(meta.get("tp3", 0) or 0)
        sl = float(meta.get("sl", 0) or 0)
        tf = str(meta.get("timeframe", "15m"))
        close_ts = int(meta.get("candle_close_ts", 0) or 0)
        close_utc = str(meta.get("candle_close_utc", "")) or (
            datetime.fromtimestamp(close_ts, tz=timezone.utc).strftime("%H:%M:%S UTC") if close_ts else "--"
        )

        progress_pct = 0.0
        dist_to_tp = 0.0
        dist_to_sl = 0.0
        if tp1 > 0 and entry > 0 and abs(tp1 - entry) > 1e-9:
            if action == "BUY":
                progress_pct = round(max(0.0, min(100.0, ((mark - entry) / (tp1 - entry)) * 100.0)), 1)
                dist_to_tp = _round_price(max(0.0, tp1 - mark), mark or entry)
            else:
                progress_pct = round(max(0.0, min(100.0, ((entry - mark) / (entry - tp1)) * 100.0)), 1)
                dist_to_tp = _round_price(max(0.0, mark - tp1), mark or entry)
        if sl > 0 and mark > 0:
            dist_to_sl = _round_price(abs(mark - sl), mark or entry)

        positions.append({
            "symbol": disp_sym,
            "bx_symbol": bx_sym,
            "action": action,
            "size": amt,
            "entry": _round_price(entry, entry or mark),
            "mark_price": _round_price(mark, mark or entry),
            "unrealized_pnl": round(upnl, 4),
            "leverage": f"{lev}x",
            "margin": round(margin, 2),
            "tp": tp1,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "sl": sl,
            "progress_pct": progress_pct,
            "dist_to_tp": dist_to_tp,
            "dist_to_sl": dist_to_sl,
            "timeframe": tf,
            "candle_close_ts": close_ts,
            "candle_close_utc": close_utc,
        })

    return positions, round(total_upnl, 4), round(used_margin, 2), ""


def _fetch_bingx_balance(used_margin: float = 0.0) -> Tuple[Dict[str, float], str]:
    ok, payload, err = _bingx_signed_request("GET", "/openApi/swap/v2/user/balance", {})
    if not ok:
        return {"balance": 0.0, "available_margin": 0.0, "unrealized_pnl": 0.0}, err

    data = payload.get("data") if isinstance(payload, dict) else {}
    if isinstance(data, dict) and "balance" in data and isinstance(data["balance"], dict):
        b_obj = data["balance"]
    elif isinstance(data, list) and data:
        b_obj = next((x for x in data if str(x.get("asset", "")).upper() == "USDT"), data[0])
    else:
        b_obj = data if isinstance(data, dict) else {}

    wallet = float(b_obj.get("balance", b_obj.get("equity", 0)) or 0)
    avail = float(b_obj.get("availableMargin", b_obj.get("availableBalance", max(0.0, wallet - used_margin))) or 0)
    upnl = float(b_obj.get("unrealizedProfit", 0) or 0)
    return {
        "balance": round(wallet, 2),
        "available_margin": round(avail, 2),
        "unrealized_pnl": round(upnl, 4),
    }, ""


def _place_bingx_multi_tpsl_order(
    symbol: str,
    action: str,
    qty: float,
    leverage: int = 20,
    balance: float = MIN_ORDER_CAPITAL_USD,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    tp3: Optional[float] = None,
    reduce_only: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    bx_sym, contract = _resolve_bingx_symbol_and_contract(symbol)
    if not contract:
        _sync_bingx_contracts(force=True)
        bx_sym, contract = _resolve_bingx_symbol_and_contract(symbol)

    if bx_sym == "XAUT-USDT" and contract and isinstance(contract.get("raw"), dict):
        print(f"[BINGX RAW CONTRACT XAUT-USDT] {json.dumps(contract['raw'])}", flush=True)

    if not contract and not reduce_only:
        return False, {"error": f"Contratto {bx_sym} non attivo su BingX Swap v2"}

    step = float(contract["step_size"]) if contract else 0.001
    prec = _effective_qty_precision(step, int(contract["qty_precision"]) if contract else 4)
    side = "BUY" if action.upper() == "BUY" else "SELL"
    pos_side = "LONG" if action.upper() == "BUY" else "SHORT"
    exit_side = "SELL" if action.upper() == "BUY" else "BUY"
    if reduce_only:
        side = exit_side

    applied_lev = max(1, int(leverage or 20))
    effective_margin = max(float(balance or 0.0), MIN_ORDER_CAPITAL_USD)

    if not reduce_only:
        ref_price = _get_live_mark_or_last_price(bx_sym)
        if ref_price <= 0:
            kl = _fetch_bingx_klines(bx_sym, "15m", limit=2)
            ref_price = float(kl[-1]["close"]) if kl else 0.0
        if ref_price <= 0:
            return False, {"error": f"Prezzo live non disponibile per {bx_sym}"}

        for cand_lev in [applied_lev, 10, 5, 2, 1]:
            if cand_lev <= 0:
                continue
            ok_lev, _, _ = _bingx_signed_request("POST", "/openApi/swap/v2/trade/leverage", {
                "symbol": bx_sym,
                "side": pos_side,
                "leverage": int(cand_lev),
            })
            if ok_lev:
                applied_lev = int(cand_lev)
                break

        ok_qty, norm_qty, qty_err = _normalize_order_qty(
            raw_qty=float(qty),
            price=ref_price,
            leverage=applied_lev,
            balance=effective_margin,
            contract=contract,
        )
        if not ok_qty:
            return False, {"error": qty_err}
        exec_qty = norm_qty
    else:
        exec_qty = _round_to_step(float(qty), step, prec, rounding_mode=ROUND_DOWN)

    qty_str = _fmt_qty(exec_qty, prec, step)
    order_params: Dict[str, Any] = {
        "symbol": bx_sym,
        "side": side,
        "positionSide": pos_side,
        "type": "MARKET",
        "quantity": qty_str,
    }
    if reduce_only:
        order_params["reduceOnly"] = "true"

    if bx_sym == "XAUT-USDT":
        safe_contract = {k: v for k, v in (contract or {}).items() if k != "raw"}
        print(f"[BINGX CONTRACT USED] {json.dumps(safe_contract)}", flush=True)
        print(f"[BINGX ORDER PARAMS] {json.dumps(order_params)}", flush=True)

    ok, res_data, err = _bingx_signed_request("POST", "/openApi/swap/v2/trade/order", order_params)
    if bx_sym == "XAUT-USDT":
        print(f"[BINGX ORDER RESP] ok={ok} err={err} data={json.dumps(res_data) if res_data is not None else 'None'}", flush=True)

    if not ok and not reduce_only:
        one_way_params = {
            "symbol": bx_sym,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str,
        }
        if bx_sym == "XAUT-USDT":
            print(f"[BINGX ONE-WAY PARAMS] {json.dumps(one_way_params)}", flush=True)
        ok, res_data, err = _bingx_signed_request("POST", "/openApi/swap/v2/trade/order", one_way_params)
        if bx_sym == "XAUT-USDT":
            print(f"[BINGX ONE-WAY RESP] ok={ok} err={err} data={json.dumps(res_data) if res_data is not None else 'None'}", flush=True)

    if not ok:
        return False, {"error": f"{err} (sym={bx_sym}, qty={qty_str}, min_margin=${MIN_ORDER_CAPITAL_USD:.2f})"}

    if reduce_only:
        return True, {"bingx": res_data, "exec_qty": exec_qty, "sl_error": "", "tp_error": ""}

    sl_error = ""
    tp_errors: List[str] = []

    if sl and float(sl) > 0:
        ok_sl, _, err_sl = _bingx_signed_request("POST", "/openApi/swap/v2/trade/order", {
            "symbol": bx_sym,
            "side": exit_side,
            "positionSide": pos_side,
            "type": "STOP_MARKET",
            "stopPrice": float(sl),
            "quantity": qty_str,
            "workingType": "MARK_PRICE",
        })
        if not ok_sl:
            sl_error = err_sl

    with STATE_LOCK:
        splits = list(TERMINAL_STATE["risk_config"].get("tp_split_pct") or [50.0, 30.0, 20.0])

    tp_targets = [
        (float(tp1 or 0), float(splits[0] if len(splits) > 0 else 50.0) / 100.0, "TP1"),
        (float(tp2 or 0), float(splits[1] if len(splits) > 1 else 30.0) / 100.0, "TP2"),
        (float(tp3 or 0), float(splits[2] if len(splits) > 2 else 20.0) / 100.0, "TP3"),
    ]
    active_tps = [(price_lv, frac, label) for (price_lv, frac, label) in tp_targets if price_lv > 0]
    if active_tps:
        total_frac = sum(f for _, f, _ in active_tps) or 1.0
        rem_qty = float(exec_qty)
        for idx, (price_lv, frac, label) in enumerate(active_tps):
            if idx == len(active_tps) - 1:
                leg_qty = _round_to_step(rem_qty, step, prec, rounding_mode=ROUND_DOWN)
            else:
                leg_qty = _round_to_step(float(exec_qty) * (frac / total_frac), step, prec, rounding_mode=ROUND_DOWN)
                rem_qty = max(0.0, round(rem_qty - leg_qty, prec))
            if leg_qty <= 0:
                continue
            ok_tp, _, err_tp = _bingx_signed_request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": bx_sym,
                "side": exit_side,
                "positionSide": pos_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": price_lv,
                "quantity": _fmt_qty(leg_qty, prec, step),
                "workingType": "MARK_PRICE",
            })
            if not ok_tp:
                tp_errors.append(f"{label}: {err_tp}")

    return True, {
        "bingx": res_data,
        "exec_qty": exec_qty,
        "sl_error": sl_error,
        "tp_error": "; ".join(tp_errors),
    }


def _check_candle_end_closures(positions: List[Dict[str, Any]]) -> None:
    with STATE_LOCK:
        cfg = dict(TERMINAL_STATE["risk_config"])
    if not cfg.get("close_at_candle_end", True):
        return

    now_s = int(time.time())
    for p in positions:
        close_ts = int(p.get("candle_close_ts", 0) or 0)
        if close_ts > 0 and now_s >= close_ts:
            ok, _ = _place_bingx_multi_tpsl_order(
                symbol=p["bx_symbol"],
                action=p["action"],
                qty=float(p["size"]),
                reduce_only=True,
            )
            if ok:
                with STATE_LOCK:
                    TERMINAL_STATE["tracked_positions"].pop(p["symbol"], None)
                    trade_rec = {
                        "symbol": p["symbol"],
                        "pnl": float(p.get("unrealized_pnl", 0.0)),
                        "fee": 0.0,
                        "reason": "Chiusura automatica a fine candela TF",
                        "ts": now_s,
                    }
                    TERMINAL_STATE["closed_trades_today"].append(trade_rec)
                    TERMINAL_STATE["closed_trades_month"].append(trade_rec)


def _maybe_run_autotrade(signals: List[Dict[str, Any]], positions: List[Dict[str, Any]], balance: float) -> None:
    if not _credentials_configured():
        return
    with STATE_LOCK:
        auto_on = bool(TERMINAL_STATE["auto_trade"])
        cfg = dict(TERMINAL_STATE["risk_config"])
        daily_pnl = sum(float(t.get("pnl", 0.0)) - float(t.get("fee", 0.0)) for t in TERMINAL_STATE["closed_trades_today"])

    if not auto_on or not signals:
        return
    if len(positions) >= int(cfg.get("max_open_positions", 1)):
        return
    loss_cap = float(cfg.get("daily_loss_cap_usd", 0.50))
    if daily_pnl <= -abs(loss_cap):
        return

    effective_bal = max(float(balance or MIN_ORDER_CAPITAL_USD), MIN_ORDER_CAPITAL_USD)
    open_syms = {p["bx_symbol"] for p in positions}
    for sig in signals:
        if sig["bx_symbol"] in open_syms:
            continue
        if cfg.get("only_a_plus") and sig.get("grade") != "A+":
            continue
        lev = _compute_leverage(effective_bal)
        ok, _ = _place_bingx_multi_tpsl_order(
            symbol=sig["bx_symbol"],
            action=sig["action"],
            qty=float(sig["size"]),
            leverage=lev,
            balance=effective_bal,
            sl=float(sig["sl"]),
            tp1=float(sig["tp1"]),
            tp2=float(sig["tp2"]),
            tp3=float(sig["tp3"]),
        )
        if ok:
            with STATE_LOCK:
                TERMINAL_STATE["tracked_positions"][sig["symbol"]] = {
                    "tp": float(sig["tp1"]),
                    "tp1": float(sig["tp1"]),
                    "tp2": float(sig["tp2"]),
                    "tp3": float(sig["tp3"]),
                    "sl": float(sig["sl"]),
                    "timeframe": sig["timeframe"],
                    "candle_close_ts": int(sig["candle_close_ts"]),
                    "candle_close_utc": f"{sig['candle_close_utc']} UTC",
                }
            break


def _performance_payload(acc_info: Dict[str, float]) -> Dict[str, Any]:
    with STATE_LOCK:
        today_list = list(TERMINAL_STATE["closed_trades_today"])
        month_list = list(TERMINAL_STATE["closed_trades_month"])

    gross_today = round(sum(float(t.get("pnl", 0.0)) for t in today_list), 4)
    fees_today = round(sum(float(t.get("fee", 0.0)) for t in today_list), 4)
    net_today = round(gross_today - fees_today, 4)
    wins_today = sum(1 for t in today_list if float(t.get("pnl", 0.0)) > 0)
    win_pct = round((wins_today / len(today_list)) * 100.0, 1) if today_list else 0.0

    gross_month = round(sum(float(t.get("pnl", 0.0)) for t in month_list), 4)
    fees_month = round(sum(float(t.get("fee", 0.0)) for t in month_list), 4)
    net_month = round(gross_month - fees_month, 4)

    return {
        "account": acc_info,
        "daily": {
            "gross_pnl": gross_today,
            "fees_pnl": fees_today,
            "net_pnl": net_today,
            "total_trades": len(today_list),
            "win_pct": win_pct,
        },
        "monthly": {
            "gross_pnl": gross_month,
            "fees_pnl": fees_month,
            "net_pnl": net_month,
            "total_trades": len(month_list),
        },
    }


def _sessions_payload() -> Dict[str, Any]:
    hour_utc = datetime.now(timezone.utc).hour
    ny_open = 13 <= hour_utc < 21
    lon_open = 7 <= hour_utc < 16
    return {
        "Cripto 24/7": {
            "status": "APERTO",
            "hours": "00:00 - 24:00 UTC",
            "badge_color": "#00ff66",
        },
        "Forex & Materie Prime": {
            "status": "APERTO" if lon_open or ny_open else "IN SESSIONE",
            "hours": "Lun-Ven (finestre sessione)",
            "badge_color": "#00ff66" if (lon_open or ny_open) else "#ffaa00",
        },
        "Azioni & Indici": {
            "status": "APERTO" if ny_open else "CHIUSO",
            "hours": "Sessione USA / Globale",
            "badge_color": "#00ff66" if ny_open else "#ff2a4b",
        },
    }


@app.route("/")
def index() -> str:
    assets = _all_bingx_assets()
    catalog: Dict[str, List[Dict[str, str]]] = {g: [] for g in MARKET_GROUPS}
    for a in assets:
        catalog[a["market"]].append({
            "symbol": a["symbol"],
            "bx_symbol": a["bx_symbol"],
            "name": a["name"],
        })
    return render_template("index.html", market_catalog=catalog)


@app.route("/api/bingx/contracts")
def api_bingx_contracts():
    force = request.args.get("force", "1") == "1"
    contracts = _sync_bingx_contracts(force=force)
    by_market: Dict[str, List[str]] = {g: [] for g in MARKET_GROUPS}
    for sym, c in sorted(contracts.items()):
        by_market[c["market"]].append(sym)
    return jsonify({
        "success": True,
        "total_contracts": len(contracts),
        "by_market": by_market,
        "contracts": contracts,
    })


@app.route("/api/signals")
def api_signals():
    profile = request.args.get("profile", "pro_sniper")
    timeframe = request.args.get("timeframe", "15m")
    if timeframe not in TF_SECONDS:
        timeframe = "15m"
    balance = max(MIN_ORDER_CAPITAL_USD, float(request.args.get("balance", MIN_ORDER_CAPITAL_USD) or MIN_ORDER_CAPITAL_USD))
    risk_pct = min(10.0, max(0.25, float(request.args.get("risk", 1.0) or 1.0)))
    min_score = min(98.0, max(20.0, float(request.args.get("min_score", 70.0) or 70.0)))
    min_rrr = min(10.0, max(0.5, float(request.args.get("min_rrr", 1.5) or 1.5)))
    market_filter = request.args.get("market", "ALL")
    symbol_filter = request.args.get("symbol", "ALL")

    candidates = _all_bingx_assets()
    if market_filter and market_filter != "ALL":
        candidates = [a for a in candidates if a["market"].lower() == market_filter.lower()]
    if symbol_filter and symbol_filter != "ALL":
        sf = symbol_filter.strip().upper()
        candidates = [a for a in candidates if a["symbol"].upper() == sf or a["bx_symbol"].upper() == sf]

    if len(candidates) > 45:
        candidates = candidates[:45]

    rows: List[Dict[str, Any]] = []
    for a in candidates:
        ev = _evaluate_asset(a, profile, timeframe, balance, risk_pct, min_score, min_rrr)
        if ev is not None:
            rows.append(ev)

    valid_signals = [r for r in rows if r["action"] in ("BUY", "SELL")]

    market_diagnostics: Dict[str, Any] = {}
    for grp in MARKET_GROUPS:
        g_rows = [r for r in rows if r["market"] == grp]
        g_sigs = [r for r in g_rows if r["action"] in ("BUY", "SELL")]
        avg_score = round(sum(r["score"] for r in g_rows) / len(g_rows), 1) if g_rows else 0.0
        certain_cnt = sum(1 for r in g_rows if r["grade"] in ("A+", "A") and not r["fakeout"])
        market_diagnostics[grp] = {
            "is_open": True,
            "status": "CONNESSO BINGX",
            "signals_count": len(g_sigs),
            "scanned": len(g_rows),
            "market_score": avg_score,
            "certain_count": certain_cnt,
            "reason": f"{len(g_sigs)} segnali validi su {len(g_rows)} contratti live",
        }

    _sync_bingx_income_history()
    positions, total_upnl, used_margin, _ = _fetch_bingx_positions()
    _check_candle_end_closures(positions)
    acc_info, _ = _fetch_bingx_balance(used_margin)
    if acc_info["unrealized_pnl"] == 0.0 and total_upnl != 0.0:
        acc_info["unrealized_pnl"] = total_upnl

    _maybe_run_autotrade(valid_signals, positions, acc_info["balance"] or balance)

    with STATE_LOCK:
        TERMINAL_STATE["scans_count"] += 1
        scans_now = TERMINAL_STATE["scans_count"]
        auto_on = TERMINAL_STATE["auto_trade"]
        logs = list(TERMINAL_STATE["telemetry_logs"])
        TERMINAL_STATE["last_params"] = {
            "profile": profile,
            "timeframe": timeframe,
            "balance": balance,
            "risk": risk_pct,
            "min_score": min_score,
            "min_rrr": min_rrr,
        }
        TERMINAL_STATE["telemetry_logs"].insert(0, {
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "status": f"BINGX LIVE SCAN #{scans_now} ({timeframe})",
            "signals_found": len(valid_signals),
            "scanned": len(rows),
        })
        TERMINAL_STATE["telemetry_logs"] = TERMINAL_STATE["telemetry_logs"][:50]

    return jsonify({
        "auto_trade": auto_on,
        "credentials_configured": _credentials_configured(),
        "active_params": {
            "profile": profile,
            "timeframe": timeframe,
            "balance": balance,
            "risk": risk_pct,
            "min_score": min_score,
            "min_rrr": min_rrr,
        },
        "candle_timing": _candle_timing(timeframe),
        "signals": valid_signals,
        "market_monitoring_table": rows,
        "market_diagnostics": market_diagnostics,
        "sessions": _sessions_payload(),
        "performance": _performance_payload(acc_info),
        "telemetry": {
            "scans_count": scans_now,
            "active_orders_count": len(positions),
            "telemetry_logs": logs,
        },
    })


@app.route("/api/bingx/live-account")
def api_bingx_live_account():
    _sync_bingx_income_history()
    positions, total_upnl, used_margin, pos_err = _fetch_bingx_positions()
    _check_candle_end_closures(positions)
    acc_info, bal_err = _fetch_bingx_balance(used_margin)
    if acc_info["unrealized_pnl"] == 0.0 and total_upnl != 0.0:
        acc_info["unrealized_pnl"] = total_upnl

    with STATE_LOCK:
        auto_on = TERMINAL_STATE["auto_trade"]
        risk_cfg = dict(TERMINAL_STATE["risk_config"])

    return jsonify({
        "auto_trade": auto_on,
        "credentials_configured": _credentials_configured(),
        "env_sources": {
            "api_key": _API_KEY_SOURCE or "missing",
            "api_secret": _API_SECRET_SOURCE or "missing",
            "base_url": _BASE_URL_SOURCE or "default",
        },
        "risk_config": risk_cfg,
        "account": acc_info,
        "positions": positions,
        "positions_error": pos_err or bal_err,
    })


@app.route("/api/live-ticker")
def api_live_ticker():
    sym_param = request.args.get("symbol", "BTC-USDT")
    bx_sym, _ = _resolve_bingx_symbol_and_contract(sym_param)
    price = _get_live_mark_or_last_price(bx_sym)
    if price <= 0:
        kl = _fetch_bingx_klines(bx_sym, "15m", limit=2)
        price = kl[-1]["close"] if kl else 0.0
    base = bx_sym.split("-")[0] if "-" in bx_sym else bx_sym
    quote = bx_sym.split("-")[1] if "-" in bx_sym else "USDT"
    return jsonify({
        "symbol": f"{base}/{quote}",
        "bingx_symbol": bx_sym,
        "price": price,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


@app.route("/api/candles")
def api_candles():
    sym_param = request.args.get("symbol", "BTC-USDT")
    tf = request.args.get("tf", "15m")
    if tf not in TF_SECONDS:
        tf = "15m"
    bx_sym, _ = _resolve_bingx_symbol_and_contract(sym_param)
    candles = _fetch_bingx_klines(bx_sym, tf, limit=60)
    base = bx_sym.split("-")[0] if "-" in bx_sym else bx_sym
    quote = bx_sym.split("-")[1] if "-" in bx_sym else "USDT"
    return jsonify({
        "symbol": f"{base}/{quote}",
        "bingx_symbol": bx_sym,
        "timeframe": tf,
        "is_live": len(candles) >= 2,
        "candles": candles,
    })


@app.route("/api/autotrade/toggle", methods=["POST"])
def api_autotrade_toggle():
    with STATE_LOCK:
        TERMINAL_STATE["auto_trade"] = not bool(TERMINAL_STATE["auto_trade"])
        new_state = TERMINAL_STATE["auto_trade"]
    return jsonify({"success": True, "auto_trade": new_state, "credentials_configured": _credentials_configured()})


@app.route("/api/autotrade/config", methods=["POST"])
def api_autotrade_config():
    body = request.get_json(silent=True) or {}
    with STATE_LOCK:
        cfg = TERMINAL_STATE["risk_config"]
        if "max_open_positions" in body:
            cfg["max_open_positions"] = max(1, min(10, int(body["max_open_positions"])))
        if "daily_loss_cap_usd" in body:
            cfg["daily_loss_cap_usd"] = max(0.05, float(body["daily_loss_cap_usd"]))
        if "only_a_plus" in body:
            cfg["only_a_plus"] = bool(body["only_a_plus"])
        if "close_at_candle_end" in body:
            cfg["close_at_candle_end"] = bool(body["close_at_candle_end"])
        if "tp_split_pct" in body and isinstance(body["tp_split_pct"], list) and len(body["tp_split_pct"]) == 3:
            cfg["tp_split_pct"] = [max(0.0, float(x)) for x in body["tp_split_pct"]]
        saved = dict(cfg)
    return jsonify({"success": True, "risk_config": saved})


@app.route("/api/autotrade/execute-manual", methods=["POST"])
def api_execute_manual():
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol", "BTC-USDT"))
    action = str(body.get("action", "BUY")).upper()
    if action not in ("BUY", "SELL"):
        return jsonify({"success": False, "error": "Azione non valida (usa BUY o SELL)"}), 400

    balance = max(MIN_ORDER_CAPITAL_USD, float(body.get("balance", MIN_ORDER_CAPITAL_USD) or MIN_ORDER_CAPITAL_USD))
    leverage = int(body.get("leverage") or _compute_leverage(balance))
    qty = float(body.get("size", 0) or 0)
    if qty <= 0:
        bx_tmp, _ = _resolve_bingx_symbol_and_contract(symbol)
        p_tmp = _get_live_mark_or_last_price(bx_tmp) or 1.0
        qty = (balance * max(leverage, 1)) / max(p_tmp, 1e-8)

    sl = float(body.get("sl", 0) or 0)
    tp1 = float(body.get("tp1", 0) or 0)
    tp2 = float(body.get("tp2", 0) or 0)
    tp3 = float(body.get("tp3", 0) or 0)
    tf = str(body.get("timeframe", "15m"))
    if tf not in TF_SECONDS:
        tf = "15m"
    timing = _candle_timing(tf)
    c_close_ts = int(body.get("candle_close_ts") or timing["candle_close_ts"])

    ok, info = _place_bingx_multi_tpsl_order(
        symbol=symbol,
        action=action,
        qty=qty,
        leverage=leverage,
        balance=balance,
        sl=sl if sl > 0 else None,
        tp1=tp1 if tp1 > 0 else None,
        tp2=tp2 if tp2 > 0 else None,
        tp3=tp3 if tp3 > 0 else None,
    )
    if not ok:
        return jsonify({"success": False, "error": info.get("error", "Errore invio ordine BingX")}), 400

    bx_sym, _ = _resolve_bingx_symbol_and_contract(symbol)
    base = bx_sym.split("-")[0] if "-" in bx_sym else bx_sym
    quote = bx_sym.split("-")[1] if "-" in bx_sym else "USDT"
    disp_sym = f"{base}/{quote}"
    exec_qty = float(info.get("exec_qty") or qty)

    with STATE_LOCK:
        TERMINAL_STATE["tracked_positions"][disp_sym] = {
            "tp": tp1,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "sl": sl,
            "timeframe": tf,
            "candle_close_ts": c_close_ts,
            "candle_close_utc": f"{timing['candle_close_utc']} UTC",
        }

    return jsonify({
        "success": True,
        "symbol": bx_sym,
        "action": action,
        "qty": exec_qty,
        "leverage": f"{leverage}x",
        "sl": sl if sl > 0 else None,
        "tp1": tp1 if tp1 > 0 else None,
        "tp2": tp2 if tp2 > 0 else None,
        "tp3": tp3 if tp3 > 0 else None,
        "sl_error": info.get("sl_error", ""),
        "tp_error": info.get("tp_error", ""),
        "candle_timing": timing,
    })


@app.route("/api/bingx/close-position", methods=["POST"])
def api_close_position():
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol", ""))
    action = str(body.get("action", "BUY")).upper()
    qty = float(body.get("size", 0) or 0)
    if not symbol or qty <= 0:
        return jsonify({"success": False, "error": "Parametri posizione non validi"}), 400

    ok, info = _place_bingx_multi_tpsl_order(symbol=symbol, action=action, qty=qty, reduce_only=True)
    if not ok:
        return jsonify({"success": False, "error": info.get("error", "Errore chiusura posizione")}), 400

    bx_sym, _ = _resolve_bingx_symbol_and_contract(symbol)
    base = bx_sym.split("-")[0] if "-" in bx_sym else bx_sym
    quote = bx_sym.split("-")[1] if "-" in bx_sym else "USDT"
    with STATE_LOCK:
        TERMINAL_STATE["tracked_positions"].pop(f"{base}/{quote}", None)
    return jsonify({"success": True, "symbol": bx_sym})


@app.route("/api/bingx/close-all", methods=["POST"])
def api_close_all():
    positions, _, _, err = _fetch_bingx_positions()
    if err and not positions:
        return jsonify({"success": False, "error": err}), 400

    closed = 0
    errors: List[str] = []
    for p in positions:
        ok, info = _place_bingx_multi_tpsl_order(
            symbol=p["bx_symbol"],
            action=p["action"],
            qty=float(p["size"]),
            reduce_only=True,
        )
        if ok:
            closed += 1
            with STATE_LOCK:
                TERMINAL_STATE["tracked_positions"].pop(p["symbol"], None)
        else:
            errors.append(f"{p['bx_symbol']}: {info.get('error', 'errore')}")

    return jsonify({
        "success": len(errors) == 0,
        "closed_count": closed,
        "error": "; ".join(errors),
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    is_render = bool(os.getenv("RENDER"))
    app.run(host="0.0.0.0", port=port, debug=not is_render)
