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
  (il backend usa SOLO il conto reale https://open-api.bingx.com: il conto demo VST non e supportato)
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
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import sqlite3
import threading
import time
import zlib
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
BX_BASE = os.getenv("AURUM_TEST_BASE", "https://open-api.bingx.com").rstrip("/")  # solo conto reale (AURUM_TEST_BASE serve solo ai test locali)
if "vst" in os.getenv("BINGX_BASE", "").lower():
    log.warning("BINGX_BASE punta al conto demo: ignorato, il backend lavora solo sul conto reale")
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
if AUTO_TRADE and not ACCOUNT_ON:
    log.warning("AUTO_TRADE attivo ma chiavi BingX mancanti: nessun ordine verra inviato")

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
    # versione servita dal backend: solo dati reali, mai la simulazione
    html = None
    for p in (os.path.join(HERE, "index.html"), "index.html", os.path.join("templates", "index.html")):
        try:
            with open(p, encoding="utf-8") as fh:
                html = fh.read()
            break
        except OSError:
            continue
    if html is None:
        html = zlib.decompress(base64.b64decode(DASH_B64)).decode("utf-8")
    head = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>body{margin:0;font-size:14px}</style><script>window.REAL_ONLY=true</script>')
    return Response(head + html, mimetype="text/html")


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


# <<EMBEDDED_HTML>>
# Dashboard incorporata: se index.html manca nel repository, il backend usa questa copia.
DASH_B64 = """
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
"""
# <<END_EMBEDDED_HTML>>

start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
