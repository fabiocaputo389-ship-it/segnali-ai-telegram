"""
Aurum Signal Room - backend per Render, collegato a BingX (futures perpetui USDT-M).

Cosa fa
  - dati reali BingX: prezzi, candele 5m/15m/1h/4h, order book, funding
  - motore di segnali (trend multi-timeframe, MACD, RSI, breakout, volume)
  - tracciamento TP1/TP2/TP3/SL con statistiche salvate in SQLite
  - notifiche Telegram
  - conto reale BingX in sola lettura (saldo e posizioni) se imposti le chiavi API
  - esecuzione automatica degli ordini: DISATTIVATA di default (AUTO_TRADE=false)
  - dashboard servita su "/"

Variabili d'ambiente
  EXCHANGE            bingx (default) oppure bitget
  BINGX_API_KEY, BINGX_API_SECRET   chiavi API BingX (conto e ordini)
  BINGX_BASE          https://open-api.bingx.com (reale) oppure
                      https://open-api-vst.bingx.com (conto DEMO VST, consigliato per i test)
  AUTO_TRADE          true per aprire davvero le posizioni (default false)
  RISK_PCT            rischio per trade in % del capitale (default 1)
  LEVERAGE            leva impostata prima di ogni ordine (default 5)
  POSITION_MODE       hedge (default) oppure oneway: deve combaciare con il conto BingX
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  DASHBOARD_PASSWORD  protegge dashboard e API con password
  PAIRS (crypto), MIN_CONF, MAX_ACTIVE, COOLDOWN_MIN, ENGINE_SEC, DB_PATH
  TRADFI              true (default): aggiunge materie prime, azioni, forex e indici trovati su BingX
  EXTRA_MAP           forza i simboli, es. "XAU=NCCOGOLD2USD-USDT,SPX=..." (vedi /api/symbols)
  LEVERAGE_TRADFI     leva per strumenti non crypto (default 2)

Avvio: gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT
(un solo worker: il motore dei segnali vive dentro il processo).
"""
import hashlib
import hmac
import json
import logging
import math
import os
import sqlite3
import threading
import time
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, Response, jsonify, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aurum")


def env_bool(k, d=False):
    return os.getenv(k, str(d)).strip().lower() in ("1", "true", "yes", "on")


EXCHANGE = os.getenv("EXCHANGE", "bingx").strip().lower()
XNAME = "BingX" if EXCHANGE == "bingx" else "Bitget"
BX_BASE = os.getenv("BINGX_BASE", "https://open-api.bingx.com").rstrip("/")
BX_KEY = os.getenv("BINGX_API_KEY", "")
BX_SECRET = os.getenv("BINGX_API_SECRET", "")
BG_BASE = "https://api.bitget.com"
MIN_CONF = int(os.getenv("MIN_CONF", "72"))
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE", "8"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "180"))
ENGINE_SEC = int(os.getenv("ENGINE_SEC", "60"))
AUTO_TRADE = env_bool("AUTO_TRADE", False)
RISK_PCT = float(os.getenv("RISK_PCT", "1"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
LEVERAGE_TRADFI = int(os.getenv("LEVERAGE_TRADFI", "2"))
HEDGE = os.getenv("POSITION_MODE", "hedge").strip().lower() != "oneway"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DB_PATH = os.getenv("DB_PATH", "signals.db")
HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNT_ON = EXCHANGE == "bingx" and bool(BX_KEY and BX_SECRET)

# ---- catalogo multi-asset -------------------------------------------------------
# chiave: (classe, nome, [parole chiave cercate nei contratti BingX])
CRYPTO = [p.strip().upper() for p in os.getenv("PAIRS", "BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,UNI").split(",") if p.strip()]
CATALOG = {
    "XAU": ("comm", "Oro", ["GOLD", "XAU"]), "XAG": ("comm", "Argento", ["SILVER", "XAG"]),
    "WTI": ("comm", "Petrolio WTI", ["WTI", "CRUDEOIL", "USOIL", "OIL"]), "BRENT": ("comm", "Petrolio Brent", ["BRENT", "UKOIL"]),
    "NGAS": ("comm", "Gas naturale", ["NATGAS", "NGAS", "NATURALGAS"]), "COPPER": ("comm", "Rame", ["COPPER"]),
    "AAPL": ("stock", "Apple", ["AAPL"]), "TSLA": ("stock", "Tesla", ["TSLA"]), "NVDA": ("stock", "Nvidia", ["NVDA"]),
    "MSFT": ("stock", "Microsoft", ["MSFT"]), "AMZN": ("stock", "Amazon", ["AMZN"]),
    "GOOGL": ("stock", "Alphabet", ["GOOGL", "GOOG"]), "META": ("stock", "Meta", ["META"]),
    "EURUSD": ("forex", "EUR/USD", ["EURUSD", "EUR2USD"]), "GBPUSD": ("forex", "GBP/USD", ["GBPUSD", "GBP2USD"]),
    "USDJPY": ("forex", "USD/JPY", ["USDJPY", "USD2JPY"]), "AUDUSD": ("forex", "AUD/USD", ["AUDUSD", "AUD2USD"]),
    "USDCAD": ("forex", "USD/CAD", ["USDCAD", "USD2CAD"]), "USDCHF": ("forex", "USD/CHF", ["USDCHF", "USD2CHF"]),
    "SPX": ("index", "S&P 500", ["SP500", "S&P500", "SPX", "US500"]), "NDX": ("index", "Nasdaq 100", ["NAS100", "NASDAQ100", "NDX", "US100"]),
    "DJI": ("index", "Dow Jones", ["DJ30", "DOWJONES", "US30", "DJI"]), "DAX": ("index", "DAX 40", ["GER40", "DAX", "DE40"]),
    "NIKKEI": ("index", "Nikkei 225", ["NIKKEI", "JP225"]), "FTSE": ("index", "FTSE 100", ["FTSE", "UK100"]),
}
TRADFI = env_bool("TRADFI", True)
CLASS_OF = {p: "crypto" for p in CRYPTO}
LABEL = {}
SYMMAP = {}                # chiave -> simbolo BingX (solo strumenti non crypto)
MISSING = []               # strumenti del catalogo non trovati su BingX
PAIRS = list(CRYPTO)


def discover_symbols():
    """Cerca nei contratti pubblici BingX gli strumenti del catalogo. Override: EXTRA_MAP="XAU=NCCOGOLD2USD-USDT,..."."""
    found = {}
    for part in os.getenv("EXTRA_MAP", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            found[k.strip().upper()] = v.strip()
    if TRADFI and EXCHANGE == "bingx":
        try:
            contracts = [c["symbol"] for c in (bingx("/openApi/swap/v2/quote/contracts") or []) if c.get("symbol", "").endswith("-USDT")]
        except Exception:
            contracts = []
            log.warning("elenco contratti BingX non disponibile: strumenti non crypto non attivi")
        for key, (cls, name, kws) in CATALOG.items():
            if key in found or key in CRYPTO:
                continue
            cand = [c for c in contracts if c[:-5] not in CRYPTO and any(k in c.upper() for k in kws)]
            nc = [c for c in cand if c.upper().startswith("NC")]
            # senza prefisso NC accetto solo corrispondenze esatte e mai azioni (evita token crypto omonimi)
            exact = [c for c in cand if c[:-5].upper() in kws] if cls != "stock" else []
            cand = nc or exact
            if cand:
                found[key] = sorted(cand, key=len)[0]
    for key, sym in found.items():
        cls, name, _ = CATALOG.get(key, ("comm", key, []))
        SYMMAP[key] = sym
        CLASS_OF[key] = cls
        LABEL[key] = name
        if key not in PAIRS:
            PAIRS.append(key)
    MISSING[:] = [k for k in CATALOG if k not in CLASS_OF] if TRADFI else []
    log.info("Strumenti attivi: %d (non crypto trovati: %d, non trovati: %s)", len(PAIRS), len(SYMMAP), ",".join(MISSING) or "nessuno")


app = Flask(__name__)
SESSION = requests.Session()
POOL = ThreadPoolExecutor(max_workers=6)


# ------------------------------------------------------------------ HTTP
def http_json(url, params=None, retries=2):
    for i in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, timeout=8)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries:
                raise
            time.sleep(0.5)


def bingx(path, params=None):
    j = http_json(BX_BASE + path, params)
    if j.get("code") not in (0, "0", None):
        raise RuntimeError("BingX: %s" % j.get("msg"))
    return j.get("data")


def bx_signed(method, path, params=None):
    """Richiesta firmata (HMAC SHA256 sulla query string), header X-BX-APIKEY."""
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    qs = "&".join("%s=%s" % (k, quote(str(p[k]), safe="")) for k in sorted(p))  # valori codificati, firma sulla stringa inviata
    sig = hmac.new(BX_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = SESSION.request(method, "%s%s?%s&signature=%s" % (BX_BASE, path, qs, sig),
                        headers={"X-BX-APIKEY": BX_KEY}, timeout=10)
    j = r.json()
    if j.get("code") not in (0, "0", None):
        raise RuntimeError("BingX: %s" % j.get("msg"))
    return j.get("data")


def bitget(path, params=None):
    j = http_json(BG_BASE + path, params)
    if str(j.get("code")) != "00000":
        raise RuntimeError("Bitget: %s" % j.get("msg"))
    return j["data"]


# ------------------------------------------------------------------ adapter exchange
def bx_sym(s):
    return SYMMAP.get(s, s + "-USDT")


def fetch_tickers():
    """-> {BASE: {'p': ultimo, 'o': prezzo 24h fa}}, funding medio in %"""
    out, fund = {}, []
    if EXCHANGE == "bingx":
        rev = {bx_sym(k): k for k in PAIRS}
        for t in bingx("/openApi/swap/v2/quote/ticker") or []:
            key = rev.get(t.get("symbol", ""))
            if not key:
                continue
            p = float(t["lastPrice"])
            o = float(t.get("openPrice") or 0)
            if o <= 0:
                pct = float(t.get("priceChangePercent") or 0)
                o = p / (1 + pct / 100) if pct > -99 else p
            out[key] = {"p": p, "o": o}
        try:
            d = bingx("/openApi/swap/v2/quote/premiumIndex")
            d = d if isinstance(d, list) else [d]
            fund = [float(x["lastFundingRate"]) * 100 for x in d
                    if x.get("symbol", "")[:-5] in CRYPTO and x.get("lastFundingRate") not in (None, "")]
        except Exception:
            pass
    else:
        for t in bitget("/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"}):
            sym = t.get("symbol", "")
            if sym.endswith("USDT") and sym[:-4] in CRYPTO:
                p = float(t["lastPr"])
                ch = float(t.get("change24h") or 0)
                out[sym[:-4]] = {"p": p, "o": p / (1 + ch) if ch > -0.99 else p}
                if t.get("fundingRate") not in (None, ""):
                    fund.append(float(t["fundingRate"]) * 100)
    return out, (sum(fund) / len(fund) if fund else None)


def fetch_candle_rows(sym, tf):
    if EXCHANGE != "bingx" and sym not in CRYPTO:
        raise RuntimeError("strumento non disponibile su Bitget")
    """-> righe [ts, open, high, low, close, volume] ordinate per tempo."""
    if EXCHANGE == "bingx":
        try:
            d = bingx("/openApi/swap/v3/quote/klines", {"symbol": bx_sym(sym), "interval": tf, "limit": 150})
        except Exception:
            d = bingx("/openApi/swap/v2/quote/klines", {"symbol": bx_sym(sym), "interval": tf, "limit": 150})
        rows = [[int(k["time"]), float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"]),
                 float(k.get("volume") or 0)] for k in d]
    else:
        g = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}[tf]
        d = bitget("/api/v2/mix/market/candles",
                   {"symbol": sym + "USDT", "productType": "USDT-FUTURES", "granularity": g, "limit": "150"})
        rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in d]
    return sorted(rows, key=lambda x: x[0])


def fetch_depth(sym):
    """-> (asks crescenti, bids decrescenti) come liste [prezzo, quantita]."""
    if EXCHANGE == "bingx":
        d = bingx("/openApi/swap/v2/quote/depth", {"symbol": bx_sym(sym), "limit": 20})
        asks, bids = d["asks"], d["bids"]
    else:
        d = bitget("/api/v2/mix/market/merge-depth", {"symbol": sym + "USDT", "productType": "USDT-FUTURES", "limit": "15"})
        asks, bids = d["asks"], d["bids"]
    asks = sorted(([float(a[0]), float(a[1])] for a in asks), key=lambda x: x[0])
    bids = sorted(([float(b[0]), float(b[1])] for b in bids), key=lambda x: -x[0])
    return asks, bids


# ------------------------------------------------------------------ prezzi e candele
PRICES = {}
FUNDING = {"avg": 0.0}
PLOCK = threading.Lock()


def is_open(sym):
    if CLASS_OF.get(sym, "crypto") == "crypto":
        return True
    e = CC.get((sym, "15m"))
    return bool(e and e[1] and time.time() * 1000 - e[1][-1][0] < 45 * 60000)


def refresh_prices():
    out, fund = fetch_tickers()
    with PLOCK:
        PRICES.update(out)
        if fund is not None:
            FUNDING["avg"] = fund


CC = {}
CLOCK = threading.Lock()


def candles(sym, tf, ttl=30):
    k = (sym, tf)
    now = time.time()
    with CLOCK:
        e = CC.get(k)
        if e and now - e[0] < ttl:
            return e[1]
    try:
        c = fetch_candle_rows(sym, tf)
    except Exception:
        if e:
            return e[1]
        raise
    with CLOCK:
        CC[k] = (now, c)
    return c


# ------------------------------------------------------------------ indicatori
def ema(a, n):
    k, e, o = 2 / (n + 1), a[0], []
    for v in a:
        e = v * k + e * (1 - k)
        o.append(e)
    return o


def rsi(a, n=14):
    if len(a) < n + 2:
        return 50.0
    g = l = 0.0
    for i in range(1, n + 1):
        d = a[i] - a[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g /= n
    l /= n
    for i in range(n + 1, len(a)):
        d = a[i] - a[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
    return 100 - 100 / (1 + g / (l or 1e-9))


def macd_hist(a):
    m = [x - y for x, y in zip(ema(a, 12), ema(a, 26))]
    s = ema(m, 9)
    return m[-1] - s[-1]


def atr(c, n=14):
    return sum(x[2] - x[3] for x in c[-n:]) / n


def analyze(sym):
    info = {}
    for tf in ("15m", "1h", "4h"):
        c = candles(sym, tf)
        cl = [x[4] for x in c]
        info[tf] = {"trend": 1 if ema(cl, 20)[-1] > ema(cl, 50)[-1] else -1, "rsi": rsi(cl),
                    "hist": macd_hist(cl), "atr": atr(c), "c": c}
    return info


def score(info, price):
    tr = [info[k]["trend"] for k in ("15m", "1h", "4h")]
    al = sum(tr)
    d = 1 if al > 0 else -1
    if abs(al) < 2 or info["1h"]["trend"] != d:
        return None
    h = info["1h"]
    c = h["c"]
    conf, why = 60, []
    name = "rialzista" if d > 0 else "ribassista"
    if abs(al) == 3:
        conf += 8
        why.append("Trend %s concorde su 15m, 1h e 4h (EMA20/EMA50)" % name)
    else:
        why.append("Trend %s su 1h e almeno un altro timeframe" % name)
    if h["hist"] * d > 0:
        conf += 5
        why.append("Istogramma MACD %s su 1h" % ("positivo" if d > 0 else "negativo"))
    r = h["rsi"]
    if (d > 0 and 40 <= r <= 65) or (d < 0 and 35 <= r <= 60):
        conf += 5
        why.append("RSI(14) a %.0f: spazio di movimento nella direzione" % r)
    elif (d > 0 and r > 72) or (d < 0 and r < 28):
        conf -= 8
        why.append("RSI(14) a %.0f: movimento esteso, rischio di ritracciamento" % r)
    prev = c[-21:-1]
    if d > 0 and c[-1][4] > max(x[2] for x in prev):
        conf += 6
        why.append("Rottura del massimo delle ultime 20 candele 1h")
    elif d < 0 and c[-1][4] < min(x[3] for x in prev):
        conf += 6
        why.append("Rottura del minimo delle ultime 20 candele 1h")
    vols = [x[5] for x in c[-21:-1]]
    if vols and c[-2][5] > 1.3 * (sum(vols) / len(vols)):
        conf += 5
        why.append("Volume sopra la media delle ultime 20 candele")
    conf = int(max(50, min(97, conf)))
    R = min(max(h["atr"] * 1.5, price * 0.004), price * 0.02)
    why.append("Stop a 1,5×ATR (%.2f%%), target a 1R, 2R e 3R" % (R / price * 100))
    return {"dir": "long" if d > 0 else "short", "conf": conf, "R": R, "why": why}


# ------------------------------------------------------------------ database
DLOCK = threading.RLock()
DB = None


def init_db():
    global DB
    DB = sqlite3.connect(DB_PATH, check_same_thread=False)
    DB.row_factory = sqlite3.Row
    DB.execute("""CREATE TABLE IF NOT EXISTS signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, sym TEXT, dir TEXT, tf TEXT,
        entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL, conf INTEGER,
        t INTEGER, valid INTEGER, hits INTEGER DEFAULT 0, closed INTEGER DEFAULT 0,
        res REAL, ct INTEGER, why TEXT, R REAL, log TEXT)""")
    DB.commit()


def to_sig(r):
    return {"cls": CLASS_OF.get(r["sym"], "crypto"), "id": r["id"], "sym": r["sym"], "dir": r["dir"], "tf": r["tf"], "entry": r["entry"], "sl": r["sl"],
            "tp": [r["tp1"], r["tp2"], r["tp3"]], "conf": r["conf"], "t": r["t"], "valid": r["valid"],
            "hits": r["hits"], "closed": bool(r["closed"]), "res": r["res"], "ct": r["ct"],
            "why": json.loads(r["why"] or "[]"), "R": r["R"], "log": json.loads(r["log"] or "[]")}


def add_log(sid, msg):
    with DLOCK:
        r = DB.execute("SELECT log FROM signals WHERE id=?", (sid,)).fetchone()
        lg = json.loads(r["log"] or "[]")
        lg.append({"t": int(time.time() * 1000), "m": msg})
        DB.execute("UPDATE signals SET log=? WHERE id=?", (json.dumps(lg), sid))
        DB.commit()


def fmt(p):
    return ("%.2f" if p >= 10 else "%.3f" if p >= 1 else "%.5f") % p


# ------------------------------------------------------------------ Telegram
def telegram(text):
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        r = SESSION.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                         json={"chat_id": TG_CHAT, "text": text}, timeout=8)
        return r.ok
    except Exception:
        log.exception("telegram")
        return False


def sig_text(g):
    icon = "🟢" if g["dir"] == "long" else "🔴"
    return "%s %s/USDT %s · %s\nEntry: %s\nSL: %s\nTP1: %s\nTP2: %s\nTP3: %s\nConfidenza: %d%%\nRischio consigliato: 1%%\nDati: %s" % (
        icon, g["sym"], g["dir"].upper(), g["tf"], fmt(g["entry"]), fmt(g["sl"]),
        fmt(g["tp"][0]), fmt(g["tp"][1]), fmt(g["tp"][2]), g["conf"], XNAME)


# ------------------------------------------------------------------ conto BingX (sola lettura)
ACC = {"t": 0, "v": None}


def account_info(ttl=5):
    if not ACCOUNT_ON:
        return None
    if time.time() - ACC["t"] < ttl and ACC["v"]:
        return ACC["v"]
    b = bx_signed("GET", "/openApi/swap/v2/user/balance")
    b = b["balance"] if isinstance(b, dict) and "balance" in b else b
    if isinstance(b, list):
        b = b[0]
    pos = bx_signed("GET", "/openApi/swap/v2/user/positions") or []
    v = {"enabled": True, "exchange": XNAME,
         "equity": float(b.get("equity") or b.get("balance") or 0),
         "available": float(b.get("availableMargin") or 0),
         "upnl": float(b.get("unrealizedProfit") or 0),
         "positions": [{"sym": p["symbol"].replace("-USDT", ""), "side": p.get("positionSide", ""),
                        "qty": float(p.get("positionAmt") or 0), "entry": float(p.get("avgPrice") or 0),
                        "upnl": float(p.get("unrealizedProfit") or 0), "lev": p.get("leverage")}
                       for p in pos if float(p.get("positionAmt") or 0) != 0]}
    ACC.update(t=time.time(), v=v)
    return v


# ------------------------------------------------------------------ esecuzione ordini (opzionale)
PREC = {}


def qty_precision(sym):
    if not PREC:
        for c in bx_signed("GET", "/openApi/swap/v2/quote/contracts") or []:
            PREC[c["symbol"]] = int(c.get("quantityPrecision", 3))
    return PREC.get(bx_sym(sym), 3)


def place_trade(g):
    """Apre la posizione con SL e TP2 attaccati. Rischio = RISK_PCT del capitale."""
    acc = account_info(ttl=0)
    if not acc or acc["equity"] <= 0:
        raise RuntimeError("capitale non disponibile")
    dist = abs(g["entry"] - g["sl"])
    qty = acc["equity"] * RISK_PCT / 100 / dist
    lev_eff = LEVERAGE if CLASS_OF.get(g["sym"], "crypto") == "crypto" else LEVERAGE_TRADFI
    qty = min(qty, acc["available"] * 0.9 * lev_eff / g["entry"])  # margine disponibile
    prec = qty_precision(g["sym"])
    qty = math.floor(qty * 10 ** prec) / 10 ** prec
    if qty <= 0:
        raise RuntimeError("quantita troppo piccola")
    long_ = g["dir"] == "long"
    ps = ("LONG" if long_ else "SHORT") if HEDGE else "BOTH"
    lev = LEVERAGE if CLASS_OF.get(g["sym"], "crypto") == "crypto" else LEVERAGE_TRADFI
    bx_signed("POST", "/openApi/swap/v2/trade/leverage",
              {"symbol": bx_sym(g["sym"]), "side": ps, "leverage": lev})
    params = {"symbol": bx_sym(g["sym"]), "side": "BUY" if long_ else "SELL", "positionSide": ps,
              "type": "MARKET", "quantity": qty,
              "stopLoss": json.dumps({"type": "STOP_MARKET", "stopPrice": round(g["sl"], 8), "workingType": "MARK_PRICE"}, separators=(",", ":")),
              "takeProfit": json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": round(g["tp"][1], 8), "workingType": "MARK_PRICE"}, separators=(",", ":"))}
    bx_signed("POST", "/openApi/swap/v2/trade/order", params)
    return qty


# ------------------------------------------------------------------ motore segnali
def create_signal(sym, sc, price):
    s = 1 if sc["dir"] == "long" else -1
    R = sc["R"]
    now = int(time.time() * 1000)
    tp = [price + s * R * m for m in (1, 2, 3)]
    lg = [{"t": now, "m": "Segnale emesso · %s @ %s" % (sc["dir"].upper(), fmt(price))}]
    with DLOCK:
        cur = DB.execute(
            "INSERT INTO signals(sym,dir,tf,entry,sl,tp1,tp2,tp3,conf,t,valid,why,R,log) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, sc["dir"], "1h", price, price - s * R, tp[0], tp[1], tp[2], sc["conf"], now, now + 15 * 60000,
             json.dumps(sc["why"], ensure_ascii=False), R, json.dumps(lg)))
        DB.commit()
        row = DB.execute("SELECT * FROM signals WHERE id=?", (cur.lastrowid,)).fetchone()
    g = to_sig(row)
    telegram(sig_text(g))
    log.info("Nuovo segnale %s %s conf %d", sym, sc["dir"], sc["conf"])
    if AUTO_TRADE and ACCOUNT_ON:
        try:
            q = place_trade(g)
            msg = "Ordine eseguito su %s: quantita %s, SL e TP2 attaccati" % (XNAME, q)
        except Exception as e:
            msg = "Ordine NON eseguito: %s" % e
            log.warning(msg)
        add_log(g["id"], msg)
        telegram("%s %s: %s" % (g["sym"], g["dir"].upper(), msg))


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def run_engine():
    with DLOCK:
        active = [r["sym"] for r in DB.execute("SELECT sym FROM signals WHERE closed=0")]
    if len(active) >= MAX_ACTIVE:
        return
    list(POOL.map(lambda a: _safe(candles, *a), [(s, tf) for s in PAIRS for tf in ("15m", "1h", "4h")]))
    now = int(time.time() * 1000)
    for sym in PAIRS:
        if sym in active or sym not in PRICES or not is_open(sym):
            continue
        with DLOCK:
            last = DB.execute("SELECT MAX(t) m FROM signals WHERE sym=?", (sym,)).fetchone()["m"] or 0
        if now - last < COOLDOWN_MIN * 60000:
            continue
        try:
            sc = score(analyze(sym), PRICES[sym]["p"])
        except Exception:
            log.warning("analisi %s non riuscita", sym)
            continue
        if sc and sc["conf"] >= MIN_CONF:
            create_signal(sym, sc, PRICES[sym]["p"])
            active.append(sym)
            if len(active) >= MAX_ACTIVE:
                break


def resolve_all():
    now = int(time.time() * 1000)
    with DLOCK:
        for r in DB.execute("SELECT * FROM signals WHERE closed=0").fetchall():
            g = to_sig(r)
            pr = PRICES.get(g["sym"])
            if not pr:
                continue
            p, s, hits = pr["p"], (1 if g["dir"] == "long" else -1), g["hits"]
            lg, closed, res, ct, msgs = g["log"], False, None, None, []
            sl = g["entry"] if hits >= 1 else g["sl"]
            if s * (p - sl) <= 0:
                closed, ct = True, now
                res = -1.0 if hits == 0 else (0.33 if hits == 1 else 1.0)
                msgs.append("Stop loss colpito · -1R" if hits == 0 else "Chiuso a break-even · %+.2fR" % res)
            else:
                while hits < 3 and s * (p - g["tp"][hits]) >= 0:
                    hits += 1
                    msgs.append("TP%d raggiunto%s" % (hits, " · SL spostato a break-even" if hits == 1 else ""))
                if hits == 3:
                    closed, ct, res = True, now, 2.0
                    msgs.append("Chiuso a TP3 · +2R")
            if not msgs:
                continue
            for m in msgs:
                lg.append({"t": now, "m": m})
                telegram("%s %s: %s" % (g["sym"], g["dir"].upper(), m))
            DB.execute("UPDATE signals SET hits=?,closed=?,res=?,ct=?,log=? WHERE id=?",
                       (hits, int(closed), res, ct, json.dumps(lg), g["id"]))
        DB.commit()


# ------------------------------------------------------------------ sentiment e news
EXTRA = {"fg": None, "news": []}


def refresh_extra():
    try:
        EXTRA["fg"] = float(http_json("https://api.alternative.me/fng/", {"limit": 1})["data"][0]["value"])
    except Exception:
        log.warning("fear&greed non disponibile")
    try:
        j = http_json("https://min-api.cryptocompare.com/data/v2/news/", {"lang": "EN"})
        EXTRA["news"] = [{"t": int(n["published_on"]) * 1000, "h": n["title"], "sn": 0,
                          "src": (n.get("source_info") or {}).get("name") or n.get("source", "")}
                         for n in j["Data"][:10]]
    except Exception:
        log.warning("news non disponibili")


def loop(fn, sec, name):
    while True:
        try:
            fn()
        except Exception:
            log.warning("%s: errore temporaneo", name)
        time.sleep(sec)


_started = False


def start():
    global _started
    if _started:
        return
    _started = True
    init_db()
    discover_symbols()
    log.info("Exchange %s · auto-trade %s · conto %s", XNAME, "ON" if AUTO_TRADE else "off",
             "collegato" if ACCOUNT_ON else "non collegato")
    for fn, sec, name in ((lambda: (refresh_prices(), resolve_all()), 3, "prezzi"),
                          (run_engine, ENGINE_SEC, "motore"),
                          (refresh_extra, 300, "extra")):
        threading.Thread(target=loop, args=(fn, sec, name), daemon=True).start()


# ------------------------------------------------------------------ web
@app.before_request
def auth():
    if not PASSWORD:
        return None
    a = request.authorization
    if not a or not hmac.compare_digest(a.password or "", PASSWORD):
        return Response("Accesso riservato", 401, {"WWW-Authenticate": 'Basic realm="Aurum"'})
    return None


@app.get("/")
def index():
    return send_file(os.path.join(HERE, "index.html"))


@app.get("/api/health")
def health():
    return jsonify(ok=True, pairs=PAIRS, classes=CLASS_OF, labels=LABEL, exchange=XNAME, account=ACCOUNT_ON, autotrade=AUTO_TRADE and ACCOUNT_ON,
                   telegram=bool(TG_TOKEN and TG_CHAT), min_conf=MIN_CONF)


@app.get("/api/state")
def state():
    if not PRICES:
        try:
            refresh_prices()
        except Exception:
            pass
    with DLOCK:
        rows = DB.execute("SELECT * FROM signals ORDER BY t DESC LIMIT 300").fetchall()
    with PLOCK:
        prices = {k: dict(v, cls=CLASS_OF.get(k, "crypto"), open=is_open(k)) for k, v in PRICES.items()}
        fund = FUNDING["avg"]
    return jsonify(prices=prices, signals=[to_sig(r) for r in rows],
                   sentiment={"fg": EXTRA["fg"] if EXTRA["fg"] is not None else 50, "funding": fund},
                   news=EXTRA["news"])


@app.get("/api/candles")
def api_candles():
    tf = request.args.get("tf", "1h")
    if tf not in ("5m", "15m", "1h", "4h"):
        return jsonify(error="tf non valido"), 400
    res = list(POOL.map(lambda s: (s, _safe(candles, s, tf)), PAIRS))
    return jsonify({s: [[x[1], x[2], x[3], x[4]] for x in c[-120:]] for s, c in res if c})


@app.get("/api/orderbook")
def orderbook():
    sym = request.args.get("sym", "").upper()
    if sym not in PAIRS:
        return jsonify(error="coppia non valida"), 400
    try:
        asks, bids = fetch_depth(sym)
        return jsonify(asks=asks[:10], bids=bids[:10])
    except Exception:
        return jsonify(error="order book non disponibile"), 502


@app.get("/api/account")
def api_account():
    if not ACCOUNT_ON:
        return jsonify(enabled=False)
    try:
        return jsonify(account_info())
    except Exception as e:
        log.warning("conto: %s", e)
        return jsonify(enabled=False, error="conto non raggiungibile"), 502


@app.get("/api/symbols")
def api_symbols():
    return jsonify(found={k: SYMMAP[k] for k in SYMMAP}, missing=MISSING, crypto=CRYPTO, tradfi=TRADFI)


@app.post("/api/telegram/test")
def tg_test():
    ok = telegram("Aurum Signal Room: test di collegamento Telegram riuscito.")
    return jsonify(ok=ok, configured=bool(TG_TOKEN and TG_CHAT))


start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
