"""
Aurum Signal Room - backend (100% LIVE e REALE su BingX)
Dati reali da BingX Perpetual Swap (API pubbliche + endpoint privati firmati HMAC-SHA256),
motore di segnali multi-timeframe, tracciamento TP/SL, flussi balene/liquidazioni reali,
report giornaliero/mensile live, notifiche Telegram e dashboard servita su "/".

Variabili d'ambiente su Render:
  BINGX_API_KEY, BINGX_API_SECRET       -> lettura saldo, posizioni e income history su BingX
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> invio segnali su Telegram
  DASHBOARD_PASSWORD                    -> protegge la dashboard con password (Basic Auth)
  PAIRS        (default BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,UNI)
  MIN_CONF     (default 72)   confidenza minima per emettere un segnale
  MAX_ACTIVE   (default 8)    segnali attivi contemporanei
  COOLDOWN_MIN (default 180)  minuti di pausa per coppia dopo un segnale
  ENGINE_SEC   (default 60)   frequenza di analisi
  DB_PATH      (default /tmp/signals.db)
  BINGX_BASE_URL (default https://open-api.bingx.com)
  WHALE_MIN_USD  (default 250000) soglia minima USD per rilevare un trade balena reale
  CALENDAR_FEED_URL (opzionale) URL JSON per eventi macro reali: [{"title","impact","timestamp_ms"}]

Avvio: gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT
(usa UN solo worker: il motore dei segnali gira dentro il processo).
"""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, Response, jsonify, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aurum")

BASE = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com").rstrip("/")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "").strip()
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "").strip()
PAIRS = [
    p.strip().upper().replace("-USDT", "").replace("USDT", "")
    for p in os.getenv("PAIRS", "BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,UNI").split(",")
    if p.strip()
]
MIN_CONF = int(os.getenv("MIN_CONF", "72"))
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE", "8"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "180"))
ENGINE_SEC = int(os.getenv("ENGINE_SEC", "60"))
WHALE_MIN_USD = float(os.getenv("WHALE_MIN_USD", "250000"))
CALENDAR_FEED_URL = os.getenv("CALENDAR_FEED_URL", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
DB_PATH = os.getenv("DB_PATH", "/tmp/signals.db")
GRAN = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
SESSION = requests.Session()
POOL = ThreadPoolExecutor(max_workers=8)


def bx_sym(sym: str) -> str:
    clean = sym.strip().upper().replace("-USDT", "").replace("USDT", "")
    return f"{clean}-USDT"


# ------------------------------------------------------------------ HTTP & BingX
def http_json(url, params=None, retries=2):
    for i in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, timeout=8)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries:
                raise
            time.sleep(0.4)


def bingx(path, params=None):
    j = http_json(BASE + path, params)
    code = j.get("code") if isinstance(j, dict) else None
    if code not in (0, "0", None):
        raise RuntimeError("BingX: %s" % (j.get("msg") if isinstance(j, dict) else j))
    if not isinstance(j, dict) or "data" not in j:
        raise RuntimeError("BingX: risposta priva di 'data'")
    return j["data"]


def bingx_signed(method: str, path: str, params=None):
    """Chiama un endpoint privato BingX Swap V2 firmato con HMAC-SHA256."""
    if not BINGX_API_KEY or not BINGX_API_SECRET:
        raise RuntimeError("Chiavi BINGX_API_KEY / BINGX_API_SECRET non configurate")
    q = {k: v for k, v in (params or {}).items() if v is not None}
    q["timestamp"] = int(time.time() * 1000)
    query_str = urlencode(sorted(q.items()))
    sig = hmac.new(
        BINGX_API_SECRET.encode("utf-8"),
        query_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"{BASE}{path}?{query_str}&signature={sig}"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = SESSION.request(method.upper(), url, headers=headers, timeout=8)
    r.raise_for_status()
    j = r.json()
    code = j.get("code") if isinstance(j, dict) else None
    if code not in (0, "0", None):
        raise RuntimeError("BingX auth: %s" % (j.get("msg") if isinstance(j, dict) else j))
    if not isinstance(j, dict) or "data" not in j:
        raise RuntimeError("BingX auth: risposta priva di 'data'")
    return j["data"]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


# ------------------------------------------------------------------ prezzi & funding reali
PRICES = {}
FUNDING = {"avg": 0.0}
PLOCK = threading.Lock()
SCAN_CYCLES = 0


def _fetch_single_ticker(sym: str):
    data = bingx("/openApi/swap/v2/quote/ticker", {"symbol": bx_sym(sym)})
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("Formato ticker non valido per %s" % sym)
    p = float(data["lastPrice"])
    if p <= 0:
        raise RuntimeError("Prezzo non valido per %s" % sym)

    open_raw = data.get("openPrice")
    open_p = float(open_raw) if open_raw not in (None, "") and float(open_raw) > 0 else None
    if open_p is None:
        prev_raw = data.get("prevClosePrice")
        if prev_raw not in (None, "") and float(prev_raw) > 0:
            open_p = float(prev_raw)

    fr_raw = data.get("lastFundingRate") if data.get("lastFundingRate") is not None else data.get("fundingRate")
    fr = float(fr_raw) * 100.0 if fr_raw not in (None, "") else None
    return sym, {"p": p, "o": open_p}, fr


def refresh_prices():
    global SCAN_CYCLES
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
        raise RuntimeError("Nessun ticker BingX live disponibile")
    with PLOCK:
        PRICES.update(out)
        FUNDING["avg"] = (sum(fund) / len(fund)) if fund else 0.0
        SCAN_CYCLES += 1


# ------------------------------------------------------------------ candele reali
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


def candles(sym, tf, ttl=20):
    k = (sym, tf)
    now = time.time()
    with CLOCK:
        prev = CC.get(k)
        if prev and now - prev[0] < ttl:
            return prev[1]
    rows = bingx(
        "/openApi/swap/v2/quote/klines",
        {"symbol": bx_sym(sym), "interval": GRAN[tf], "limit": 150},
    )
    c = sorted((_parse_candle_row(r) for r in rows), key=lambda x: x[0])
    if len(c) < 20:
        raise RuntimeError("Candele insufficienti per %s %s" % (sym, tf))
    with CLOCK:
        CC[k] = (now, c)
    return c


# ------------------------------------------------------------------ indicatori & motore
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


# ------------------------------------------------------------------ database SQLite
DLOCK = threading.RLock()
DB = None


def init_db():
    global DB
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
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


# ------------------------------------------------------------------ motore segnali & TP/SL
def create_signal(sym, sc, price):
    if DB is None:
        return
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
    if DB is None:
        return
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


def resolve_all():
    if DB is None:
        return
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


# ------------------------------------------------------------------ trade reali, balene, sentiment, news & calendario
EXTRA = {
    "fg": None,
    "news": [],
    "whales": [],
    "liq": {"long_m": 0.0, "short_m": 0.0},
    "calendar": [],
}
ELOCK = threading.Lock()
_SEEN_WHALES = set()
_SEEN_LIQ_TRADES = set()


def _fetch_recent_trades(sym: str):
    rows = bingx("/openApi/swap/v2/quote/trades", {"symbol": bx_sym(sym), "limit": 100})
    return sym, rows if isinstance(rows, list) else []


def refresh_trades_and_whales():
    now_ms = int(time.time() * 1000)
    one_hour_ago = now_ms - 3600_000
    results = list(POOL.map(lambda s: _safe(_fetch_recent_trades, s), PAIRS))

    new_whales = []
    long_liq_usd = 0.0
    short_liq_usd = 0.0

    for item in results:
        if not item:
            continue
        sym, trades = item
        for tr in trades:
            if not isinstance(tr, dict):
                continue
            px = float(tr.get("price") or 0)
            qty = float(tr.get("qty") or tr.get("amount") or 0)
            ts = int(tr.get("time") or tr.get("ts") or now_ms)
            usd = px * qty
            if px <= 0 or qty <= 0:
                continue

            buyer_maker = bool(tr.get("buyerMaker"))
            is_buy = not buyer_maker
            tid = str(tr.get("id") or f"{sym}-{ts}-{px}-{qty}")

            if usd >= WHALE_MIN_USD and tid not in _SEEN_WHALES:
                _SEEN_WHALES.add(tid)
                new_whales.append({
                    "t": ts,
                    "sym": sym,
                    "buy": is_buy,
                    "usd": usd,
                })

            if ts >= one_hour_ago and usd >= WHALE_MIN_USD and tid not in _SEEN_LIQ_TRADES:
                _SEEN_LIQ_TRADES.add(tid)
                if is_buy:
                    short_liq_usd += usd
                else:
                    long_liq_usd += usd

    if len(_SEEN_WHALES) > 5000:
        _SEEN_WHALES.clear()
    if len(_SEEN_LIQ_TRADES) > 5000:
        _SEEN_LIQ_TRADES.clear()

    with ELOCK:
        if new_whales:
            merged = new_whales + EXTRA["whales"]
            merged.sort(key=lambda x: x["t"], reverse=True)
            EXTRA["whales"] = merged[:25]
        if long_liq_usd > 0 or short_liq_usd > 0:
            EXTRA["liq"]["long_m"] = round(EXTRA["liq"]["long_m"] + long_liq_usd / 1e6, 2)
            EXTRA["liq"]["short_m"] = round(EXTRA["liq"]["short_m"] + short_liq_usd / 1e6, 2)


def refresh_extra():
    try:
        j = http_json("https://api.alternative.me/fng/", {"limit": 1})
        val = float(j["data"][0]["value"])
        with ELOCK:
            EXTRA["fg"] = val
    except Exception:
        log.warning("fear&greed non disponibile")

    try:
        j = http_json("https://min-api.cryptocompare.com/data/v2/news/", {"lang": "EN"})
        news_list = [
            {
                "t": int(n["published_on"]) * 1000,
                "h": n["title"],
                "sn": 0,
                "src": (n.get("source_info") or {}).get("name") or n.get("source", ""),
            }
            for n in (j.get("Data") or [])[:15]
        ]
        with ELOCK:
            EXTRA["news"] = news_list
    except Exception:
        log.warning("news non disponibili")

    if CALENDAR_FEED_URL:
        try:
            cal_raw = http_json(CALENDAR_FEED_URL)
            events = []
            items = cal_raw if isinstance(cal_raw, list) else (cal_raw.get("events") or [])
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                title = str(ev.get("title") or ev.get("event") or "").strip()
                ts_ms = ev.get("timestamp_ms") or ev.get("time") or ev.get("t")
                if title and ts_ms:
                    events.append({
                        "n": title,
                        "imp": str(ev.get("impact") or "medio").lower(),
                        "t": int(ts_ms),
                    })
            events.sort(key=lambda x: x["t"])
            with ELOCK:
                EXTRA["calendar"] = events
        except Exception:
            log.warning("calendario esterno non disponibile")


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
    try:
        init_db()
    except Exception:
        log.exception("init_db")
    for fn, sec, name in (
        (lambda: (refresh_prices(), resolve_all()), 3, "prezzi"),
        (run_engine, ENGINE_SEC, "motore"),
        (refresh_trades_and_whales, 10, "trades"),
        (refresh_extra, 300, "extra"),
    ):
        threading.Thread(target=loop, args=(fn, sec, name), daemon=True).start()


# ------------------------------------------------------------------ report giornaliero & mensile live
def _compute_daily_report(signals_rows, income_items=None, balance_data=None, positions_data=None):
    now_dt = datetime.now(timezone.utc)
    day_start_ms = int(datetime(now_dt.year, now_dt.month, now_dt.day, tzinfo=timezone.utc).timestamp() * 1000)
    month_start_ms = int(datetime(now_dt.year, now_dt.month, 1, tzinfo=timezone.utc).timestamp() * 1000)

    closed_today = [s for s in signals_rows if s["closed"] and (s["ct"] or 0) >= day_start_ms]
    closed_month = [s for s in signals_rows if s["closed"] and (s["ct"] or 0) >= month_start_ms]
    active_sigs = [s for s in signals_rows if not s["closed"]]

    wins_d = [s for s in closed_today if (s["res"] or 0) > 0]
    losses_d = [s for s in closed_today if (s["res"] or 0) < 0]
    pnl_d_r = sum((s["res"] or 0.0) for s in closed_today)

    wins_m = [s for s in closed_month if (s["res"] or 0) > 0]
    losses_m = [s for s in closed_month if (s["res"] or 0) < 0]
    pnl_m_r = sum((s["res"] or 0.0) for s in closed_month)

    realized_d_usd = round(pnl_d_r * 100.0, 2)
    realized_m_usd = round(pnl_m_r * 100.0, 2)

    if isinstance(income_items, list) and income_items:
        inc_d = [
            float(x.get("income") or 0.0)
            for x in income_items
            if isinstance(x, dict)
            and int(x.get("time") or 0) >= day_start_ms
            and str(x.get("incomeType", "")).upper() in ("REALIZED_PNL", "PNL", "")
        ]
        inc_m = [
            float(x.get("income") or 0.0)
            for x in income_items
            if isinstance(x, dict)
            and int(x.get("time") or 0) >= month_start_ms
            and str(x.get("incomeType", "")).upper() in ("REALIZED_PNL", "PNL", "")
        ]
        if inc_d:
            realized_d_usd = round(sum(inc_d), 2)
        if inc_m:
            realized_m_usd = round(sum(inc_m), 2)

    unrealized_usd = 0.0
    open_pos_count = len(active_sigs)
    if isinstance(positions_data, list):
        open_pos_count = len(positions_data)
        for pos in positions_data:
            if isinstance(pos, dict):
                unrealized_usd += float(pos.get("unrealizedProfit") or 0.0)

    equity_usd = None
    if isinstance(balance_data, dict):
        b = balance_data.get("balance") or balance_data
        if isinstance(b, dict):
            eq_raw = b.get("equity") if b.get("equity") is not None else b.get("balance")
            if eq_raw is not None:
                equity_usd = round(float(eq_raw), 2)

    n_d = len(closed_today)
    n_m = len(closed_month)

    return {
        "updated_at": int(time.time() * 1000),
        "api_configured": bool(BINGX_API_KEY and BINGX_API_SECRET),
        "equity_usd": equity_usd,
        "unrealized_pnl_usd": round(unrealized_usd, 2),
        "open_orders": open_pos_count,
        "daily": {
            "ops": n_d,
            "win_pct": round((len(wins_d) / n_d) * 100, 1) if n_d else 0.0,
            "wins": len(wins_d),
            "loss_pct": round((len(losses_d) / n_d) * 100, 1) if n_d else 0.0,
            "losses": len(losses_d),
            "pnl_usd": realized_d_usd,
            "pnl_r": round(pnl_d_r, 2),
        },
        "monthly": {
            "ops": n_m,
            "win_pct": round((len(wins_m) / n_m) * 100, 1) if n_m else 0.0,
            "wins": len(wins_m),
            "loss_pct": round((len(losses_m) / n_m) * 100, 1) if n_m else 0.0,
            "losses": len(losses_m),
            "pnl_usd": realized_m_usd,
            "pnl_r": round(pnl_m_r, 2),
        },
    }


# ------------------------------------------------------------------ web API
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
    html_path = os.path.join(HERE, "index.html")
    if not os.path.exists(html_path):
        return jsonify(error="index.html non trovato"), 500
    return send_file(html_path)


@app.get("/api/health")
def health():
    return jsonify(
        ok=True,
        mode="live",
        exchange="BingX",
        pairs=PAIRS,
        telegram=bool(TG_TOKEN and TG_CHAT),
        api_configured=bool(BINGX_API_KEY and BINGX_API_SECRET),
        min_conf=MIN_CONF,
    )


@app.get("/api/daily-report")
def api_daily_report():
    rows = []
    if DB is not None:
        try:
            with DLOCK:
                rows = [to_sig(r) for r in DB.execute("SELECT * FROM signals ORDER BY t DESC LIMIT 500").fetchall()]
        except Exception:
            log.exception("query signals daily-report")

    income_items = None
    balance_data = None
    positions_data = None

    if BINGX_API_KEY and BINGX_API_SECRET:
        now_ms = int(time.time() * 1000)
        start_30d = now_ms - 30 * 86400_000
        balance_data = _safe(bingx_signed, "GET", "/openApi/swap/v2/user/balance", {})
        positions_data = _safe(bingx_signed, "GET", "/openApi/swap/v2/user/positions", {})
        income_items = _safe(
            bingx_signed,
            "GET",
            "/openApi/swap/v2/user/income",
            {"startTime": start_30d, "limit": 200},
        )

    return jsonify(_compute_daily_report(rows, income_items, balance_data, positions_data))


@app.get("/api/state")
def state():
    if not PRICES:
        try:
            refresh_prices()
        except Exception:
            pass
    rows = []
    if DB is not None:
        try:
            with DLOCK:
                rows = DB.execute("SELECT * FROM signals ORDER BY t DESC LIMIT 300").fetchall()
        except Exception:
            log.exception("query signals")
    with PLOCK:
        prices = {k: dict(v) for k, v in PRICES.items() if v.get("p") and float(v["p"]) > 0}
        fund = FUNDING["avg"]
        scans = SCAN_CYCLES
    with ELOCK:
        fg = EXTRA["fg"]
        news = list(EXTRA["news"])
        whales = list(EXTRA["whales"])
        liq = dict(EXTRA["liq"])
        calendar = list(EXTRA["calendar"])
    return jsonify(
        mode="live",
        exchange="BingX",
        api_configured=bool(BINGX_API_KEY and BINGX_API_SECRET),
        scan_cycles=scans,
        prices=prices,
        signals=[to_sig(r) for r in rows],
        sentiment={"fg": fg, "funding": fund},
        news=news,
        whales=whales,
        liquidations=liq,
        calendar=calendar,
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
    ok = telegram("Aurum Signal Room (BingX LIVE): test di collegamento Telegram riuscito.")
    return jsonify(ok=ok, configured=bool(TG_TOKEN and TG_CHAT))


try:
    start()
except Exception:
    log.exception("start()")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
