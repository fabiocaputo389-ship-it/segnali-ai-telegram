import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BINGX_BASE_URL = "https://open-api.bingx.com"
DEFAULT_SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "XRP-USDT",
    "BNB-USDT",
    "DOGE-USDT",
]

STATE_LOCK = threading.Lock()
STATE = {
    "closed_trades": [],
    "session_pnl": 0.0,
    "last_bingx_sync": 0.0,
    "bingx_rate_limit_until": 0.0,
    "auto_trade_enabled": False,
    "max_open_positions": 1,
    "daily_stop_loss_usd": 0.5,
    "close_on_candle_end": True,
    "auto_trade_filter": "ALL_VALID",
    "active_tab": "positions",
}


def safe_float(val, default=0.0):
    if val is None or val == "":
        return float(default)
    try:
        if isinstance(val, str):
            val = val.replace(",", ".").strip()
        f = float(val)
        if f != f or f == float("inf") or f == float("-inf"):
            return float(default)
        return f
    except (ValueError, TypeError):
        return float(default)


def canonicalize_symbol_key(sym):
    s = str(sym or "BTC-USDT").upper().strip().replace("_", "/").replace("-", "/")
    if "/" not in s and s.endswith("USDT") and len(s) > 4:
        s = f"{s[:-4]}/USDT"
    return s


def to_bingx_symbol(sym):
    c = canonicalize_symbol_key(sym)
    return c.replace("/", "-")


def compute_dynamic_leverage(capital_usd):
    cap = safe_float(capital_usd, 0.0)
    if cap <= 2.0:
        return 20
    if cap <= 10.0:
        return 15
    if cap <= 50.0:
        return 10
    return 5


def get_bingx_credentials():
    api_key = (
        os.environ.get("BINGX_API_KEY")
        or os.environ.get("BINGX_KEY")
        or ""
    ).strip()
    api_secret = (
        os.environ.get("BINGX_API_SECRET")
        or os.environ.get("BINGX_SECRET")
        or ""
    ).strip()
    return api_key, api_secret


def bingx_signed_request(method, path, params=None):
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return None, "Credenziali API BingX non configurate"

    q_params = dict(params or {})
    q_params["timestamp"] = int(time.time() * 1000)
    q_params.setdefault("recvWindow", 5000)

    query_string = urlencode(sorted((str(k), str(v)) for k, v in q_params.items()))
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = f"{BINGX_BASE_URL}{path}?{query_string}&signature={signature}"
    req = Request(
        url,
        method=method.upper(),
        headers={
            "X-BX-APIKEY": api_key,
            "Content-Type": "application/json",
            "User-Agent": "BingX-HFT-Terminal/2.0",
        },
    )

    try:
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as exc:
        msg = str(exc)
        if "429" in msg or "rate" in msg.lower():
            with STATE_LOCK:
                STATE["bingx_rate_limit_until"] = time.time() + 20.0
        return None, f"Errore rete BingX: {msg}"

    if isinstance(data, dict):
        code = data.get("code")
        if code not in (None, 0, "0"):
            msg = str(data.get("msg") or data.get("message") or f"Codice {code}")
            if str(code) in ("100410", "100429", "429"):
                with STATE_LOCK:
                    STATE["bingx_rate_limit_until"] = time.time() + 20.0
            return None, f"BingX API ({code}): {msg}"
    return data, None


def bingx_public_request(path, params=None):
    q = urlencode(params or {})
    url = f"{BINGX_BASE_URL}{path}" + (f"?{q}" if q else "")
    req = Request(url, headers={"User-Agent": "BingX-HFT-Terminal/2.0"})
    try:
        with urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as exc:
        return None, str(exc)


def fetch_bingx_real_balance():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return {
            "wallet_balance": 0.0,
            "unrealized_pnl": 0.0,
            "available_margin": 0.0,
            "equity": 0.0,
            "dynamic_leverage": compute_dynamic_leverage(0.0),
        }, "API non configurata"

    payload, err = bingx_signed_request("GET", "/openApi/swap/v2/user/balance")
    if err or not isinstance(payload, dict):
        return {
            "wallet_balance": 0.0,
            "unrealized_pnl": 0.0,
            "available_margin": 0.0,
            "equity": 0.0,
            "dynamic_leverage": compute_dynamic_leverage(0.0),
        }, err or "Risposta saldo non valida"

    raw_data = payload.get("data")
    entries = []
    if isinstance(raw_data, dict):
        if isinstance(raw_data.get("balance"), dict):
            entries = [raw_data["balance"]]
        elif isinstance(raw_data.get("balances"), list):
            entries = raw_data["balances"]
        else:
            entries = [raw_data]
    elif isinstance(raw_data, list):
        entries = raw_data

    usdt_item = None
    for item in entries:
        if isinstance(item, dict) and str(item.get("asset") or "USDT").upper() == "USDT":
            usdt_item = item
            break
    if usdt_item is None and entries and isinstance(entries[0], dict):
        usdt_item = entries[0]
    usdt_item = usdt_item or {}

    wallet_bal = safe_float(
        usdt_item.get("balance")
        if usdt_item.get("balance") is not None
        else usdt_item.get("walletBalance"),
        0.0,
    )
    upnl = safe_float(
        usdt_item.get("unrealizedProfit")
        if usdt_item.get("unrealizedProfit") is not None
        else usdt_item.get("crossUnrealizedPnl"),
        0.0,
    )
    avail = safe_float(
        usdt_item.get("availableMargin")
        if usdt_item.get("availableMargin") is not None
        else usdt_item.get("free"),
        wallet_bal,
    )
    equity = safe_float(
        usdt_item.get("equity"),
        wallet_bal + upnl,
    )

    return {
        "wallet_balance": round(wallet_bal, 4),
        "unrealized_pnl": round(upnl, 4),
        "available_margin": round(avail, 4),
        "equity": round(equity, 4),
        "dynamic_leverage": compute_dynamic_leverage(wallet_bal),
    }, None


def fetch_bingx_open_positions():
    api_key, api_secret = get_bingx_credentials()
    if not (api_key and api_secret):
        return []
    payload, err = bingx_signed_request("GET", "/openApi/swap/v2/user/positions")
    if err or not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []

    positions = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        amt = safe_float(p.get("positionAmt"), 0.0)
        if abs(amt) <= 0.0:
            continue
        sym = canonicalize_symbol_key(p.get("symbol") or "BTC-USDT")
        side = str(p.get("positionSide") or ("LONG" if amt > 0 else "SHORT")).upper()
        entry_px = safe_float(p.get("avgPrice") or p.get("entryPrice"), 0.0)
        mark_px = safe_float(p.get("markPrice"), entry_px)
        upnl = safe_float(p.get("unrealizedProfit"), 0.0)
        lev = int(safe_float(p.get("leverage"), 1))
        positions.append({
            "symbol": sym,
            "side": side,
            "size": abs(amt),
            "entry_price": entry_px,
            "mark_price": mark_px,
            "unrealized_pnl": round(upnl, 4),
            "leverage": lev,
        })
    return positions


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

    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    month_start_utc = datetime(now_utc.year, now_utc.month, 1, 0, 0, 0, tzinfo=timezone.utc)
    start_ms = int(month_start_utc.timestamp() * 1000)
    current_month_key = now_utc.strftime("%Y-%m")
    today_key = now_utc.strftime("%Y-%m-%d")

    allowed_types = ("REALIZED_PNL", "PROFIT", "TRADE_PNL", "COMMISSION", "FUNDING_FEE")
    parsed_map = {}
    cursor_end = now_ms

    for _ in range(10):
        payload, err = bingx_signed_request(
            "GET",
            "/openApi/swap/v2/user/income",
            {"startTime": start_ms, "endTime": cursor_end, "limit": 1000},
        )
        if err or not isinstance(payload, dict):
            break
        rows = payload.get("data") or []
        if not isinstance(rows, list) or not rows:
            break

        oldest_ts = None
        for idx, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            inc_type = str(item.get("incomeType") or "").upper()
            if inc_type not in allowed_types:
                continue
            amt_val = safe_float(item.get("income"), 0.0)
            if amt_val == 0.0:
                continue
            t_ms = int(safe_float(item.get("time"), now_ms))
            if t_ms < start_ms or t_ms > now_ms:
                continue
            if oldest_ts is None or t_ms < oldest_ts:
                oldest_ts = t_ms

            dt_utc = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
            m_key = dt_utc.strftime("%Y-%m")
            if m_key != current_month_key:
                continue

            sym_clean = canonicalize_symbol_key(item.get("symbol") or "BTCUSDT")
            raw_id = str(
                item.get("tranId") or item.get("orderId") or item.get("id") or ""
            ).strip()
            if raw_id:
                trade_id = f"{raw_id}_{inc_type}_{sym_clean}_{t_ms}"
            else:
                trade_id = f"bx_{inc_type}_{sym_clean}_{t_ms}_{idx}_{round(amt_val, 6)}"

            parsed_map[trade_id] = {
                "id": trade_id,
                "symbol": sym_clean,
                "income_type": inc_type,
                "is_fee": inc_type in ("COMMISSION", "FUNDING_FEE"),
                "realized_pnl": round(amt_val, 4),
                "closed_date": dt_utc.strftime("%Y-%m-%d"),
                "closed_month": m_key,
                "closed_at": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "ts_ms": t_ms,
            }

        if len(rows) < 1000 or oldest_ts is None or oldest_ts <= start_ms:
            break
        cursor_end = oldest_ts - 1

    ordered_trades = sorted(parsed_map.values(), key=lambda x: x["ts_ms"], reverse=True)
    today_net = round(
        sum(safe_float(t.get("realized_pnl")) for t in ordered_trades if t["closed_date"] == today_key),
        4,
    )

    with STATE_LOCK:
        STATE["closed_trades"] = ordered_trades
        STATE["session_pnl"] = today_net


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


def fetch_market_rows(symbols=None):
    target_syms = symbols or DEFAULT_SYMBOLS
    rows = []
    for raw_sym in target_syms:
        canon = canonicalize_symbol_key(raw_sym)
        bx_sym = to_bingx_symbol(canon)
        payload, err = bingx_public_request("/openApi/swap/v2/quote/ticker", {"symbol": bx_sym})
        data = payload.get("data") if isinstance(payload, dict) else {}
        if isinstance(data, list) and data:
            data = data[0]
        data = data or {}

        price = safe_float(data.get("lastPrice"), 0.0)
        chg_pct = safe_float(data.get("priceChangePercent"), 0.0)
        high_24h = safe_float(data.get("highPrice"), price)
        low_24h = safe_float(data.get("lowPrice"), price)
        vol_24h = safe_float(data.get("volume"), 0.0)

        direction = "BUY" if chg_pct > 0.25 else ("SELL" if chg_pct < -0.25 else "NEUTRAL")
        rows.append({
            "symbol": canon,
            "bingx_symbol": bx_sym,
            "price": price,
            "change_24h_pct": round(chg_pct, 2),
            "high_24h": high_24h,
            "low_24h": low_24h,
            "volume_24h": round(vol_24h, 2),
            "signal": direction,
            "error": err,
        })
    return rows


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def api_health():
    api_key, api_secret = get_bingx_credentials()
    return jsonify({
        "status": "ok",
        "utc_now": datetime.now(timezone.utc).isoformat(),
        "api_configured": bool(api_key and api_secret),
    })


@app.route("/api/performance")
def api_performance():
    return jsonify(build_performance_payload())


@app.route("/api/positions")
def api_positions():
    perf = build_performance_payload()
    open_pos = fetch_bingx_open_positions()
    with STATE_LOCK:
        cfg = {
            "auto_trade_enabled": STATE["auto_trade_enabled"],
            "max_open_positions": STATE["max_open_positions"],
            "daily_stop_loss_usd": STATE["daily_stop_loss_usd"],
            "close_on_candle_end": STATE["close_on_candle_end"],
            "auto_trade_filter": STATE["auto_trade_filter"],
            "active_tab": STATE["active_tab"],
        }
    return jsonify({
        "open_positions": open_pos,
        "performance": perf,
        "config": cfg,
    })


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        with STATE_LOCK:
            if "auto_trade_enabled" in body:
                STATE["auto_trade_enabled"] = bool(body["auto_trade_enabled"])
            if "max_open_positions" in body:
                STATE["max_open_positions"] = max(1, int(safe_float(body["max_open_positions"], 1)))
            if "daily_stop_loss_usd" in body:
                STATE["daily_stop_loss_usd"] = max(0.0, safe_float(body["daily_stop_loss_usd"], 0.5))
            if "close_on_candle_end" in body:
                STATE["close_on_candle_end"] = bool(body["close_on_candle_end"])
            if "auto_trade_filter" in body and body["auto_trade_filter"]:
                STATE["auto_trade_filter"] = str(body["auto_trade_filter"])
            if "active_tab" in body and body["active_tab"]:
                STATE["active_tab"] = str(body["active_tab"])
    with STATE_LOCK:
        return jsonify({
            "auto_trade_enabled": STATE["auto_trade_enabled"],
            "max_open_positions": STATE["max_open_positions"],
            "daily_stop_loss_usd": STATE["daily_stop_loss_usd"],
            "close_on_candle_end": STATE["close_on_candle_end"],
            "auto_trade_filter": STATE["auto_trade_filter"],
            "active_tab": STATE["active_tab"],
        })


@app.route("/api/live-ticker")
def api_live_ticker():
    raw_syms = request.args.get("symbols", "")
    syms = [s.strip() for s in raw_syms.split(",") if s.strip()] if raw_syms else DEFAULT_SYMBOLS
    return jsonify({"rows": fetch_market_rows(syms), "timestamp_ms": int(time.time() * 1000)})


@app.route("/api/reset-sync", methods=["POST"])
def api_reset_sync():
    with STATE_LOCK:
        STATE["last_bingx_sync"] = 0.0
        STATE["bingx_rate_limit_until"] = 0.0
        STATE["closed_trades"] = []
    perf = build_performance_payload()
    return jsonify({"status": "synced", "performance": perf})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
