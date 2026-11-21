"""
Aurum Signal Room - backend
Dati reali da BingX Perpetual Swap (API pubbliche, nessuna chiave), motore di segnali,
tracciamento TP/SL, notifiche Telegram e dashboard servita su "/".

Variabili d'ambiente (tutte opzionali):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> invio segnali su Telegram
  DASHBOARD_PASSWORD                    -> protegge la dashboard con password
  PAIRS        (default BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,UNI)
  MIN_CONF     (default 72)   confidenza minima per emettere un segnale
  MAX_ACTIVE   (default 8)    segnali attivi contemporanei
  COOLDOWN_MIN (default 180)  minuti di pausa per coppia dopo un segnale
  ENGINE_SEC   (default 60)   frequenza di analisi
  DB_PATH      (default signals.db)
  BINGX_BASE_URL (default https://open-api.bingx.com)

Avvio: gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT
(usa UN solo worker: il motore dei segnali gira dentro il processo).
"""
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, Response, jsonify, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aurum")

BASE = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")
PAIRS = [
    p.strip().upper().replace("-USDT", "").replace("USDT", "")
    for p in os.getenv("PAIRS", "BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,UNI").split(",")
    if p.strip()
]
MIN_CONF = int(os.getenv("MIN_CONF", "72"))
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE", "8"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "180"))
ENGINE_SEC = int(os.getenv("ENGINE_SEC", "60"))
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DB_PATH = os.getenv("DB_PATH", "signals.db")
GRAN = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
SESSION = requests.Session()
POOL = ThreadPoolExecutor(max_workers=6)


def bx_sym(sym: str) -> str:
    clean = sym.strip().upper().replace("-USDT", "").replace("USDT", "")
    return f"{clean}-USDT"


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
    j = http_json(BASE + path, params)
    code = j.get("code") if isinstance(j, dict) else None
    if code not in (0, "0", None):
        raise RuntimeError("BingX: %s" % (j.get("msg") if isinstance(j, dict) else j))
    if not isinstance(j, dict) or "data" not in j:
        raise RuntimeError("BingX: risposta priva di 'data'")
    return j["data"]


# ------------------------------------------------------------------ prezzi
PRICES = {}
FUNDING = {"avg": 0.0}
PLOCK = threading.Lock()


def _fetch_single_ticker(sym: str):
    data = bingx("/openApi/swap/v2/quote/ticker", {"symbol": bx_sym(sym)})
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("Formato ticker non valido per %s" % sym)
    p = float(data["lastPrice"])
    if p <= 0:
        raise RuntimeError("Prezzo non valido per %s" % sym)
    open_p = float(data.get("openPrice") or 0)
    if open_p <= 0:
        pct_raw = str(data.get("priceChangePercent") or "0").replace("%", "").strip()
        pct = float(pct_raw or 0) / 100.0
        open_p = p / (1.0 + pct) if pct > -0.99 else p
    fr_raw = data.get("lastFundingRate") if data.get("lastFundingRate") is not None else data.get("fundingRate")
    fr = float(fr_raw) * 100.0 if fr_raw not in (None, "") else None
    return sym, {"p": p, "o": open_p}, fr


def refresh_prices():
    results = list(POOL.map(lambda s: _safe(_fetch_single_ticker, s), PAIRS))
    out, fund = {}, []
    for item in results:
        if not item:
            continue
        sym, pdata, fr = item
        out[sym] = pdata
        if fr is not None:
            fund.append(fr)
    if not out:
        raise RuntimeError("Nessun ticker BingX aggiornato")
    with PLOCK:
        PRICES.update(out)
        if fund:
            FUNDING["avg"] = sum(fund) / len(fund)


# ------------------------------------------------------------------ candele
CC = {}
CLOCK = threading.Lock()


def _parse_candle_row(r):
    if isinstance(r, dict):
        return [
            int(r["time"]),
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            float(r.get("volume", 0.0)),
        ]
    return [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]


def candles(sym, tf, ttl=30):
    """Righe: [ts, open, high, low, close, volume], ordinate per tempo."""
    k = (sym, tf)
    now = time.time()
    with CLOCK:
        prev = CC.get(k)
        if prev and now - prev[0] < ttl:
            return prev[1]
    try:
        rows = bingx(
            "/openApi/swap/v2/quote/klines",
            {"symbol": bx_sym(sym), "interval": GRAN[tf], "limit": 150},
        )
        c = sorted((_parse_candle_row(r) for r in rows), key=lambda x: x[0])
        if len(c) < 20:
            raise RuntimeError("Candele insufficienti per %s %s" % (sym, tf))
    except Exception:
        if prev:
            return prev[1]
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
        info[tf] = {
            "trend": 1 if ema(cl, 20)[-1] > ema(cl, 50)[-1] else -1,
            "rsi": rsi(cl),
            "hist": macd_hist(cl),
            "atr": atr(c),
            "c": c,
        }
    return info


def score(info, price):
    """Restituisce un dict con direzione, confidenza, livelli e motivazioni, oppure None."""
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
    return {
        "id": r["id"],
        "sym": r["sym"],
        "dir": r["dir"],
        "tf": r["tf"],
        "entry": r["entry"],
        "sl": r["sl"],
        "tp": [r["tp1"], r["tp2"], r["tp3"]],
        "conf": r["conf"],
        "t": r["t"],
        "valid": r["valid"],
        "hits": r["hits"],
        "closed": bool(r["closed"]),
        "res": r["res"],
        "ct": r["ct"],
        "why": json.loads(r["why"] or "[]"),
        "R": r["R"],
        "log": json.loads(r["log"] or "[]"),
    }


def fmt(p):
    return ("%.2f" if p >= 10 else "%.3f" if p >= 1 else "%.5f") % p


# ------------------------------------------------------------------ Telegram
def telegram(text):
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        r = SESSION.post(
            "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
            json={"chat_id": TG_CHAT, "text": text},
            timeout=8,
        )
        return r.ok
    except Exception:
        log.exception("telegram")
        return False


def sig_text(g):
    icon = "🟢" if g["dir"] == "long" else "🔴"
    return (
        "%s %s/USDT %s · %s\nEntry: %s\nSL: %s\nTP1: %s\nTP2: %s\nTP3: %s\n"
        "Confidenza: %d%%\nRischio consigliato: 1%%\nDati: BingX"
        % (
            icon,
            g["sym"],
            g["dir"].upper(),
            g["tf"],
            fmt(g["entry"]),
            fmt(g["sl"]),
            fmt(g["tp"][0]),
            fmt(g["tp"][1]),
            fmt(g["tp"][2]),
            g["conf"],
        )
    )


# ------------------------------------------------------------------ motore
def create_signal(sym, sc, price):
    s = 1 if sc["dir"] == "long" else -1
    R = sc["R"]
    now = int(time.time() * 1000)
    tp = [price + s * R * m for m in (1, 2, 3)]
    sl = price - s * R
    lg = [{"t": now, "m": "Segnale emesso · %s @ %s" % (sc["dir"].upper(), fmt(price))}]
    with DLOCK:
        cur = DB.execute(
            "INSERT INTO signals(sym,dir,tf,entry,sl,tp1,tp2,tp3,conf,t,valid,why,R,log) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sym,
                sc["dir"],
                "1h",
                price,
                sl,
                tp[0],
                tp[1],
                tp[2],
                sc["conf"],
                now,
                now + 15 * 60000,
                json.dumps(sc["why"], ensure_ascii=False),
                R,
                json.dumps(lg),
            ),
        )
        DB.commit()
        row = DB.execute("SELECT * FROM signals WHERE id=?", (cur.lastrowid,)).fetchone()
    g = to_sig(row)
    telegram(sig_text(g))
    log.info("Nuovo segnale %s %s conf %d", sym, sc["dir"], sc["conf"])


def run_engine():
    with DLOCK:
        active = [r["sym"] for r in DB.execute("SELECT sym FROM signals WHERE closed=0")]
    if len(active) >= MAX_ACTIVE:
        return
    jobs = [(s, tf) for s in PAIRS for tf in ("15m", "1h", "4h")]
    list(POOL.map(lambda a: _safe(candles, *a), jobs))
    now = int(time.time() * 1000)
    for sym in PAIRS:
        if sym in active or sym not in PRICES:
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


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def resolve_all():
    now = int(time.time() * 1000)
    with DLOCK:
        rows = DB.execute("SELECT * FROM signals WHERE closed=0").fetchall()
        for r in rows:
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
            DB.execute(
                "UPDATE signals SET hits=?,closed=?,res=?,ct=?,log=? WHERE id=?",
                (hits, int(closed), res, ct, json.dumps(lg), g["id"]),
            )
        DB.commit()


# ------------------------------------------------------------------ sentiment e news
EXTRA = {"fg": None, "news": []}


def refresh_extra():
    try:
        j = http_json("https://api.alternative.me/fng/", {"limit": 1})
        EXTRA["fg"] = float(j["data"][0]["value"])
    except Exception:
        log.warning("fear&greed non disponibile")
    try:
        j = http_json("https://min-api.cryptocompare.com/data/v2/news/", {"lang": "EN"})
        EXTRA["news"] = [
            {
                "t": int(n["published_on"]) * 1000,
                "h": n["title"],
                "sn": 0,
                "src": (n.get("source_info") or {}).get("name") or n.get("source", ""),
            }
            for n in j["Data"][:10]
        ]
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
    for fn, sec, name in (
        (lambda: (refresh_prices(), resolve_all()), 3, "prezzi"),
        (run_engine, ENGINE_SEC, "motore"),
        (refresh_extra, 300, "extra"),
    ):
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
    return jsonify(
        ok=True,
        exchange="BingX",
        pairs=PAIRS,
        telegram=bool(TG_TOKEN and TG_CHAT),
        min_conf=MIN_CONF,
    )


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
        prices = {k: dict(v) for k, v in PRICES.items()}
        fund = FUNDING["avg"]
    return jsonify(
        exchange="BingX",
        prices=prices,
        signals=[to_sig(r) for r in rows],
        sentiment={"fg": EXTRA["fg"] if EXTRA["fg"] is not None else 50, "funding": fund},
        news=EXTRA["news"],
    )


@app.get("/api/candles")
def api_candles():
    tf = request.args.get("tf", "1h")
    if tf not in GRAN:
        return jsonify(error="tf non valido"), 400
    res = list(POOL.map(lambda s: (s, _safe(candles, s, tf)), PAIRS))
    return jsonify({s: [[x[1], x[2], x[3], x[4]] for x in c[-120:]] for s, c in res if c})


@app.get("/api/orderbook")
def orderbook():
    sym = request.args.get("sym", "").upper().replace("-USDT", "").replace("USDT", "")
    if sym not in PAIRS:
        return jsonify(error="coppia non valida"), 400
    try:
        d = bingx("/openApi/swap/v2/quote/depth", {"symbol": bx_sym(sym), "limit": 20})
        asks = [[float(x[0]), float(x[1])] for x in (d.get("asks") or [])[:10]]
        bids = [[float(x[0]), float(x[1])] for x in (d.get("bids") or [])[:10]]
        return jsonify(asks=asks, bids=bids)
    except Exception:
        return jsonify(error="order book non disponibile"), 502


@app.post("/api/telegram/test")
def tg_test():
    ok = telegram("Aurum Signal Room (BingX): test di collegamento Telegram riuscito.")
    return jsonify(ok=ok, configured=bool(TG_TOKEN and TG_CHAT))


start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
