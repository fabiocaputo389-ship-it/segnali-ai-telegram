
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "").strip()
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "").strip()

# Mappa simboli TradFi (Forex, Materie Prime, Indici, Azioni) verso coppie perpetue BingX
TRADFI_SYMBOLS = {
    "XAU-USDT": {"label": "XAU / USD", "category": "Commodities", "decimals": 2, "pip_size": 0.10, "contract_size": 100},
    "XAG-USDT": {"label": "XAG / USD", "category": "Commodities", "decimals": 3, "pip_size": 0.01, "contract_size": 5000},
    "WTI-USDT": {"label": "WTI Crude Oil", "category": "Commodities", "decimals": 2, "pip_size": 0.05, "contract_size": 1000},
    "EUR-USDT": {"label": "EUR / USD", "category": "Forex", "decimals": 5, "pip_size": 0.0001, "contract_size": 100000},
    "GBP-USDT": {"label": "GBP / USD", "category": "Forex", "decimals": 5, "pip_size": 0.0001, "contract_size": 100000},
    "JPY-USDT": {"label": "USD / JPY", "category": "Forex", "decimals": 3, "pip_size": 0.01, "contract_size": 100000},
    "NDX-USDT": {"label": "NASDAQ 100", "category": "Indices", "decimals": 2, "pip_size": 1.0, "contract_size": 1},
    "SPX-USDT": {"label": "S&P 500", "category": "Indices", "decimals": 2, "pip_size": 0.5, "contract_size": 1},
    "GER40-USDT": {"label": "DAX 40", "category": "Indices", "decimals": 2, "pip_size": 1.0, "contract_size": 1},
    "NVDA-USDT": {"label": "NVIDIA Corp", "category": "Equities", "decimals": 2, "pip_size": 0.10, "contract_size": 1},
    "AAPL-USDT": {"label": "Apple Inc", "category": "Equities", "decimals": 2, "pip_size": 0.10, "contract_size": 1},
}


def compute_dynamic_leverage(capital: float) -> int:
    """Regola dinamica della leva in base al capitale allocato."""
    if capital <= 2.00:
        return 20
    if capital <= 25.00:
        return 15
    if capital <= 100.00:
        return 10
    return 5


def build_signed_query(params: dict | None = None) -> tuple[str, str]:
    """Costruisce query ordinata con timestamp e firma HMAC-SHA256 per BingX."""
    query_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    query_params["timestamp"] = int(time.time() * 1000)
    query_params.setdefault("recvWindow", 5000)

    ordered = sorted(query_params.items(), key=lambda item: item[0])
    encoded_query = urlencode(ordered)
    signature = hmac.new(
        BINGX_SECRET_KEY.encode("utf-8"),
        encoded_query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return encoded_query, signature


def bingx_public_request(path: str, params: dict | None = None) -> dict:
    """Esegue una chiamata REST pubblica verso BingX Swap v2."""
    url = f"{BINGX_BASE_URL}{path}"
    resp = requests.get(url, params=params or {}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def bingx_signed_request(method: str, path: str, params: dict | None = None) -> dict:
    """Esegue una chiamata REST autenticata verso BingX Swap v2."""
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return {
            "code": -1,
            "msg": "Chiavi BINGX_API_KEY o BINGX_SECRET_KEY non configurate su Render.",
            "data": None,
        }

    encoded_query, signature = build_signed_query(params)
    url = f"{BINGX_BASE_URL}{path}?{encoded_query}&signature={signature}"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}

    method_upper = method.upper()
    if method_upper == "GET":
        resp = requests.get(url, headers=headers, timeout=10)
    elif method_upper == "POST":
        resp = requests.post(url, headers=headers, timeout=10)
    elif method_upper == "DELETE":
        resp = requests.delete(url, headers=headers, timeout=10)
    else:
        raise ValueError(f"Metodo HTTP non supportato: {method}")

    resp.raise_for_status()
    return resp.json()


@app.route("/")
def index():
    return render_template("index.html", symbols=TRADFI_SYMBOLS)


@app.route("/api/live-ticker")
def api_live_ticker():
    """Legge il ticker live da BingX per i simboli TradFi richiesti."""
    symbols_param = request.args.get("symbols", ",".join(TRADFI_SYMBOLS.keys()))
    requested = [s.strip().upper() for s in symbols_param.split(",") if s.strip()]
    results = []

    for sym in requested:
        meta = TRADFI_SYMBOLS.get(
            sym,
            {"label": sym, "category": "TradFi", "decimals": 2, "pip_size": 0.1, "contract_size": 1},
        )
        try:
            payload = bingx_public_request("/openApi/swap/v2/quote/ticker", {"symbol": sym})
            data = payload.get("data") or {}
            last_price = float(data.get("lastPrice") or data.get("price") or 0.0)
            change_pct = float(data.get("priceChangePercent") or 0.0)
            high_24h = float(data.get("highPrice") or last_price)
            low_24h = float(data.get("lowPrice") or last_price)

            results.append(
                {
                    "symbol": sym,
                    "label": meta["label"],
                    "category": meta["category"],
                    "decimals": meta["decimals"],
                    "lastPrice": last_price,
                    "priceChangePercent": change_pct,
                    "highPrice": high_24h,
                    "lowPrice": low_24h,
                    "source": "bingx_live",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "symbol": sym,
                    "label": meta["label"],
                    "category": meta["category"],
                    "decimals": meta["decimals"],
                    "lastPrice": None,
                    "priceChangePercent": 0.0,
                    "highPrice": None,
                    "lowPrice": None,
                    "source": "error",
                    "error": str(exc),
                }
            )

    return jsonify({"code": 0, "timestamp": int(time.time() * 1000), "tickers": results})


@app.route("/api/klines")
def api_klines():
    """Recupera le candele storiche da BingX per popolare il grafico."""
    symbol = request.args.get("symbol", "XAU-USDT").upper()
    interval = request.args.get("interval", "15m")
    limit = int(request.args.get("limit", 100))

    try:
        payload = bingx_public_request(
            "/openApi/swap/v2/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        raw_candles = payload.get("data") or []
        candles = []
        for item in raw_candles:
            if isinstance(item, dict):
                candles.append(
                    {
                        "time": int(item.get("time", 0)),
                        "open": float(item.get("open", 0)),
                        "high": float(item.get("high", 0)),
                        "low": float(item.get("low", 0)),
                        "close": float(item.get("close", 0)),
                        "volume": float(item.get("volume", 0)),
                    }
                )
            elif isinstance(item, list) and len(item) >= 5:
                candles.append(
                    {
                        "time": int(item[0]),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]) if len(item) > 5 else 0.0,
                    }
                )

        return jsonify({"code": 0, "symbol": symbol, "interval": interval, "candles": candles})
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "candles": []}), 500


@app.route("/api/account-sync")
def api_account_sync():
    """Sincronizza saldo, posizioni aperte e cronologia chiusure (income) da BingX."""
    try:
        balance_res = bingx_signed_request("GET", "/openApi/swap/v2/user/balance")
        positions_res = bingx_signed_request("GET", "/openApi/swap/v2/user/positions")
        income_res = bingx_signed_request(
            "GET",
            "/openApi/swap/v2/user/income",
            {"incomeType": "REALIZED_PNL", "limit": 50},
        )

        closed_trades = income_res.get("data") or []
        realized_pnl_total = 0.0
        wins = 0
        losses = 0

        if isinstance(closed_trades, list):
            for row in closed_trades:
                pnl = float(row.get("income", 0.0))
                realized_pnl_total += pnl
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1

        total_closed = wins + losses
        win_rate = round((wins / total_closed) * 100, 1) if total_closed > 0 else 0.0

        return jsonify(
            {
                "code": 0,
                "configured": bool(BINGX_API_KEY and BINGX_SECRET_KEY),
                "balance": balance_res,
                "positions": positions_res,
                "income_history": income_res,
                "analytics": {
                    "realized_pnl_total": round(realized_pnl_total, 2),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                },
            }
        )
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc)}), 500


@app.route("/api/execute-signal", methods=["POST"])
def api_execute_signal():
    """Invia un ordine su BingX con leva dinamica e separazione esplicita TP/SL."""
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol", "XAU-USDT")).upper()
    direction = str(body.get("direction", "")).upper()
    capital = float(body.get("capital", 0.0))
    quantity = float(body.get("quantity", 0.0))
    tp_price = body.get("tp_price")
    sl_price = body.get("sl_price")

    # Separazione direzionale rigorosa: BUY o SELL obbligatorio
    if direction not in ("BUY", "SELL"):
        return jsonify({"code": 400, "msg": "Direzione non valida: usare solo BUY o SELL."}), 400

    if quantity <= 0:
        return jsonify({"code": 400, "msg": "La quantità (quantity) deve essere maggiore di zero."}), 400

    leverage = int(body.get("leverage") or compute_dynamic_leverage(capital))

    try:
        # 1. Imposta leva sul simbolo
        lev_res = bingx_signed_request(
            "POST",
            "/openApi/swap/v2/trade/leverage",
            {"symbol": symbol, "side": "BOTH", "leverage": leverage},
        )

        # 2. Prepara ordine di mercato con TP e SL separati
        order_params = {
            "symbol": symbol,
            "side": direction,
            "positionSide": "LONG" if direction == "BUY" else "SHORT",
            "type": "MARKET",
            "quantity": quantity,
        }
        if tp_price is not None and tp_price != "":
            order_params["takeProfit"] = json_tp_sl_payload("TAKE_PROFIT_MARKET", float(tp_price))
        if sl_price is not None and sl_price != "":
            order_params["stopLoss"] = json_tp_sl_payload("STOP_MARKET", float(sl_price))

        order_res = bingx_signed_request("POST", "/openApi/swap/v2/trade/order", order_params)

        return jsonify(
            {
                "code": 0,
                "symbol": symbol,
                "direction": direction,
                "leverage_applied": leverage,
                "leverage_response": lev_res,
                "order_response": order_res,
            }
        )
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc)}), 500


def json_tp_sl_payload(order_type: str, stop_price: float) -> str:
    import json

    return json.dumps(
        {
            "type": order_type,
            "stopPrice": stop_price,
            "workingType": "MARK_PRICE",
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
