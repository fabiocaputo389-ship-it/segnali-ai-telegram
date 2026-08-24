"""
Segnali AI - Bitget -> Telegram
---------------------------------
Bot che legge i prezzi pubblici da Bitget (perpetui USDT-FUTURES), calcola indicatori
tecnici e pubblica automaticamente segnali BUY/SELL su un canale Telegram privato.

Nessuna API key di Bitget necessaria: usiamo solo endpoint pubblici (lettura prezzi).
Nessun trade viene eseguito: e' un bot di sola segnalazione.

VARIABILI D'AMBIENTE RICHIESTE (da impostare su Railway, mai nel codice):
    BOT_TOKEN     -> token del bot Telegram (da @BotFather)
    CHANNEL_ID    -> id numerico del canale privato (es. -1001234567890)

Dipendenze (vedi requirements.txt):
    pip install python-telegram-bot pandas numpy requests
"""

import asyncio
import io
import json
import logging
import os
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum

import numpy as np
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # chat privata Telegram di Fabio, per le notifiche di errore

# Cartella per salvare statistiche/posizioni su disco, cosi' sopravvivono ai redeploy.
# Su Railway va collegata a un Volume (disco persistente) montato su questo path -
# senza Volume, la variabile non esiste e si usa la cartella corrente (si azzera comunque
# ai redeploy, ma il bot funziona lo stesso).
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
STATO_FILE = os.path.join(DATA_DIR, "stato_bot.json")
# Logo e font per le card grafiche: stessa cartella di main.py, cosi' basta un solo
# "Upload files" su GitHub senza dover creare sottocartelle.
ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))

# Watchlist: nomi in stile Kraken usati come chiave interna (es. XBTUSD), tradotti
# automaticamente nel simbolo perpetuo Bitget corrispondente (es. BTCUSDT) da
# pair_kraken_a_bitget() sia per l'analisi che per il monitoraggio SL/TP.
# Nota: MATICUSD, MKRUSD, FTMUSD rimosse -> non esiste un perpetuo Bitget corrispondente
# (probabile rebranding ticker, es. MATIC -> POL). Rivedere se serve reintrodurle con nome corretto.
WATCHLIST = [
    # Top cap
    "XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOGEUSD", "AVAXUSD", "DOTUSD", "LINKUSD",
    "LTCUSD", "BCHUSD", "ATOMUSD", "UNIUSD", "ARBUSD",
    # Aggiunte: altre coppie liquide
    "TRXUSD", "NEARUSD", "APTUSD", "FILUSD", "ICPUSD",
    "OPUSD", "SUIUSD", "INJUSD", "RENDERUSD", "TIAUSD",
    "SEIUSD", "AAVEUSD", "SNXUSD", "GRTUSD",
    "SANDUSD", "MANAUSD", "AXSUSD", "ALGOUSD",
    "EGLDUSD", "FLOWUSD", "CHZUSD", "KSMUSD", "XLMUSD",
    # Seconda ondata (2026-08-22): watchlist ampliata su richiesta di Fabio.
    # NOTA: alcuni ticker potrebbero non avere un perpetuo Bitget corrispondente (come
    # successo con MATIC/MKR/FTM) - controllare i log dopo il redeploy e
    # rimuovere quelli falliti.
    "ETCUSD", "XMRUSD", "ZECUSD", "XTZUSD", "EOSUSD",
    "THETAUSD", "APEUSD", "GALAUSD", "IMXUSD", "RUNEUSD",
    "KAVAUSD", "MINAUSD", "OCEANUSD", "ENJUSD", "BATUSD",
    "COMPUSD", "YFIUSD", "CRVUSD", "SUSHIUSD", "ZRXUSD",
    "STORJUSD", "ANKRUSD", "LRCUSD", "QNTUSD", "FETUSD",
    "PEPEUSD", "SHIBUSD", "JUPUSD", "STXUSD", "ONDOUSD",
]

SCAN_INTERVAL_SECONDS = 600       # ogni 10 minuti
SCORE_MINIMO_PUBBLICAZIONE = 65   # su 100 - confermato dal backtest: EV +0.043R/trade, ~15 segnali/giorno
# NOTA: lo score è discreto (30/50/55/75/80/100 - somma di blocchi), quindi 65/70/75
# producono risultati identici. 80 è stato testato e dà EV negativo: NON alzare oltre 75.
COOLDOWN_ORE = 4                  # non ripetere segnale stesso asset entro N ore
STATISTICHE_INTERVALLO_ORE = 24    # ogni quanto pubblicare le statistiche reali sul canale
MAX_POSIZIONI_STESSA_DIREZIONE = 5 # evita di accumulare troppo rischio correlato (tutte crypto si muovono insieme)
MAX_PERDITE_CONSECUTIVE = 5        # dopo N stop loss di fila, pausa automatica (circuit breaker)
PAUSA_ORE = 12                     # durata della pausa prima di riprendere da soli
REPORT_SETTIMANALE_INTERVALLO_GIORNI = 7

# --- Parametri per il calcolo della leva suggerita ---
# La leva NON è un moltiplicatore di guadagno: serve solo a mantenere costante
# la % di capitale a rischio, dato quanto è "stretto" o "largo" lo Stop Loss.
RISCHIO_PER_TRADE_PERCENTO = 3.0   # % di capitale che si è disposti a perdere se lo SL viene colpito
# (alzato da 1.5 a 3.0 su richiesta: con SL tipici 2-7%, a 1.5% la leva risultava
# quasi sempre 1x. A 3.0% varia di più mantenendo comunque il calcolo reale sul prezzo.)
LEVA_MASSIMA = 10                  # tetto di sicurezza, mai superato indipendentemente dal calcolo
LEVA_MINIMA = 1

# Numero di decimali di riserva se Bitget non risponde alla richiesta di precisione live
# (vedi get_decimali_bitget). Valori approssimati sui vecchi tick-size Kraken (agosto 2026),
# tenuti come seconda rete di sicurezza prima della stima generica dal prezzo.
# Se una coppia non è in questa lista, si usa un default calcolato dal prezzo stesso.
DECIMALI_PREZZO = {
    "XBTUSD": 1,
    "ETHUSD": 2,
    "SOLUSD": 2,
    "XRPUSD": 4,
    "ADAUSD": 4,
    "DOGEUSD": 5,
    "AVAXUSD": 3,
    "DOTUSD": 3,
    "LINKUSD": 3,
    "MATICUSD": 4,
    "LTCUSD": 2,
    "BCHUSD": 1,
    "ATOMUSD": 3,
    "UNIUSD": 3,
    "ARBUSD": 4,
    "TRXUSD": 5,
    "NEARUSD": 3,
    "APTUSD": 3,
    "FILUSD": 3,
    "ICPUSD": 3,
    "OPUSD": 4,
    "SUIUSD": 4,
    "INJUSD": 3,
    "RENDERUSD": 3,
    "TIAUSD": 3,
    "SEIUSD": 4,
    "AAVEUSD": 2,
    "MKRUSD": 1,
    "SNXUSD": 4,
    "GRTUSD": 5,
    "SANDUSD": 4,
    "MANAUSD": 4,
    "AXSUSD": 3,
    "FTMUSD": 5,
    "ALGOUSD": 4,
    "EGLDUSD": 2,
    "FLOWUSD": 4,
    "CHZUSD": 5,
    "KSMUSD": 2,
    "XLMUSD": 5,
}


def decimali_per_coppia(pair: str, prezzo: float) -> int:
    """Decimali da usare per SL/TP. Priorita': precisione reale di Bitget (dove si opera
    davvero) -> tabella Kraken calibrata a mano -> stima prudente dal prezzo."""
    decimali_bitget = get_decimali_bitget(pair)
    if decimali_bitget is not None:
        return decimali_bitget
    if pair in DECIMALI_PREZZO:
        return DECIMALI_PREZZO[pair]
    # Stima prudente se la coppia non e' mappata e Bitget non ha risposto: meglio pochi
    # decimali in piu' (valore ancora valido su qualunque exchange) che troppi (rischio
    # che l'exchange rifiuti il prezzo per eccesso di precisione).
    if prezzo >= 1000:
        return 1
    if prezzo >= 100:
        return 2
    if prezzo >= 1:
        return 3
    if prezzo >= 0.1:
        return 4
    if prezzo >= 0.01:
        return 5
    return 6


def arrotonda_prezzo(pair: str, prezzo: float) -> float:
    decimali = decimali_per_coppia(pair, prezzo)
    return round(prezzo, decimali)


def calcola_leva(entry: float, stop_loss: float) -> int:
    """
    Leva suggerita = quanta leva puoi usare mantenendo costante il rischio
    (RISCHIO_PER_TRADE_PERCENTO) rispetto al capitale, dato quanto è distante lo SL.
    Non è un moltiplicatore "a caso": più lo SL è stretto, più leva è
    matematicamente coerente con lo stesso rischio in euro/dollari.
    """
    distanza_percento = abs(entry - stop_loss) / entry * 100
    if distanza_percento <= 0:
        return LEVA_MINIMA
    leva = RISCHIO_PER_TRADE_PERCENTO / distanza_percento
    leva_arrotondata = round(leva)
    return max(LEVA_MINIMA, min(LEVA_MASSIMA, leva_arrotondata))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("segnali_ai")

# La libreria httpx (usata internamente da python-telegram-bot per le chiamate HTTP)
# logga di default l'URL completo di ogni richiesta, token del bot incluso.
# Alziamo il suo livello a WARNING cosi' non finisce piu' nei log di Railway.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class Direzione(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Segnale:
    coppia: str
    direzione: Direzione
    entry: float
    stop_loss: float
    take_profit: float      # TP3, il finale (4.5x ATR) - mantenuto per compatibilità risk_reward
    tp1: float               # 1.5x ATR (1:1)
    tp2: float                # 3x ATR (1:2)
    timeframe: str
    score: int
    motivazione: str
    leva_suggerita: int = 1

    @property
    def risk_reward(self) -> float:
        rischio = abs(self.entry - self.stop_loss)
        rendimento = abs(self.take_profit - self.entry)
        return round(rendimento / rischio, 2) if rischio else 0.0

    @property
    def sl_percento(self) -> float:
        return round(abs(self.entry - self.stop_loss) / self.entry * 100, 2)

    def tp_percento(self, tp_value: float) -> float:
        return round(abs(tp_value - self.entry) / self.entry * 100, 2)


# ---------------------------------------------------------------------------
# PERSISTENZA STATO (statistiche e posizioni aperte sopravvivono ai redeploy)
# ---------------------------------------------------------------------------

def _segnale_to_dict(s: Segnale) -> dict:
    d = asdict(s)
    d["direzione"] = s.direzione.value
    return d


def _segnale_from_dict(d: dict) -> Segnale:
    d = dict(d)
    d["direzione"] = Direzione(d["direzione"])
    return Segnale(**d)


def salva_stato(ultimo_segnale: dict, posizioni_aperte: dict, statistiche: dict,
                 ultimo_invio_statistiche: datetime, ultimo_invio_report: datetime):
    try:
        dati = {
            "ultimo_segnale": {pair: ts.isoformat() for pair, ts in ultimo_segnale.items()},
            "posizioni_aperte": {
                pair: {
                    "segnale": _segnale_to_dict(p["segnale"]),
                    "tp1_raggiunto": p["tp1_raggiunto"],
                    "tp2_raggiunto": p["tp2_raggiunto"],
                    "aperto_il": p["aperto_il"].isoformat(),
                }
                for pair, p in posizioni_aperte.items()
            },
            "statistiche": statistiche,
            "ultimo_invio_statistiche": ultimo_invio_statistiche.isoformat(),
            "ultimo_invio_report": ultimo_invio_report.isoformat(),
        }
        with open(STATO_FILE, "w") as f:
            json.dump(dati, f)
    except Exception as e:
        logger.warning(f"Impossibile salvare lo stato su disco: {e}")


def carica_stato():
    """Ritorna (ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche,
    ultimo_invio_report). Se il file non esiste (primo avvio, o nessun Volume collegato
    su Railway), parte da zero."""
    statistiche_default = {
        "vinti": 0, "persi": 0, "totali": 0,
        "perdite_consecutive": 0, "pausa_fino": None, "per_coppia": {},
    }
    default = ({}, {}, statistiche_default, datetime.now(), datetime.now())
    if not os.path.exists(STATO_FILE):
        logger.info("Nessuno stato salvato trovato, parto da zero.")
        return default

    try:
        with open(STATO_FILE) as f:
            dati = json.load(f)

        ultimo_segnale = {pair: datetime.fromisoformat(ts) for pair, ts in dati.get("ultimo_segnale", {}).items()}
        posizioni_aperte = {
            pair: {
                "segnale": _segnale_from_dict(p["segnale"]),
                "tp1_raggiunto": p["tp1_raggiunto"],
                "tp2_raggiunto": p["tp2_raggiunto"],
                "aperto_il": datetime.fromisoformat(p["aperto_il"]),
            }
            for pair, p in dati.get("posizioni_aperte", {}).items()
        }
        statistiche = dati.get("statistiche", statistiche_default)
        # Retrocompatibilita': se il file e' stato salvato prima di aggiungere questi campi.
        statistiche.setdefault("perdite_consecutive", 0)
        statistiche.setdefault("pausa_fino", None)
        statistiche.setdefault("per_coppia", {})

        ultimo_invio_statistiche = datetime.fromisoformat(
            dati.get("ultimo_invio_statistiche", datetime.now().isoformat())
        )
        ultimo_invio_report = datetime.fromisoformat(
            dati.get("ultimo_invio_report", datetime.now().isoformat())
        )
        logger.info(
            f"Stato ricaricato da disco: {len(posizioni_aperte)} posizioni aperte, "
            f"statistiche {statistiche['totali']} chiuse finora."
        )
        return ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche, ultimo_invio_report
    except Exception as e:
        logger.warning(f"Impossibile leggere lo stato salvato ({e}), parto da zero.")
        return default


# ---------------------------------------------------------------------------
# DATI DI MERCATO (Bitget API pubblica)
# ---------------------------------------------------------------------------

GRANULARITA_BITGET = {1: "1m", 15: "15m", 60: "1H", 240: "4H"}


def get_ohlc(pair: str, interval: int, count: int = 250) -> pd.DataFrame:
    """
    Scarica candele OHLC dal perpetuo Bitget corrispondente alla coppia (stesso exchange
    su cui si opera davvero, dall'analisi tecnica al monitoraggio SL/TP).
    interval in minuti: 1, 15, 60 = 1h, 240 = 4h
    """
    simbolo = pair_kraken_a_bitget(pair)
    granularita = GRANULARITA_BITGET.get(interval)
    if granularita is None:
        raise ValueError(f"Intervallo {interval} non mappato su una granularita' Bitget")

    resp = requests.get(
        "https://api.bitget.com/api/v2/mix/market/candles",
        params={
            "symbol": simbolo,
            "productType": "USDT-FUTURES",
            "granularity": granularita,
            "limit": min(count, 1000),
        },
        timeout=10,
    )
    resp.raise_for_status()
    dati = resp.json()
    if dati.get("code") != "00000":
        raise ValueError(f"Errore Bitget per {simbolo}: {dati}")

    righe = dati.get("data") or []
    if not righe:
        raise ValueError(f"Nessuna candela restituita da Bitget per {simbolo}")

    # Le prime 6 colonne sono sempre timestamp/open/high/low/close/volume base;
    # eventuali colonne extra (es. volume in quote currency) vengono ignorate.
    df = pd.DataFrame(righe).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="ms")
    df["vwap"] = df["close"]  # Bitget non fornisce il vwap per candela, approssimato col close
    df["count"] = 0  # non fornito da Bitget, non usato nella logica di scoring

    # Bitget non garantisce sempre l'ordine cronologico crescente: lo forziamo noi.
    df = df.sort_values("time").reset_index(drop=True)
    return df.tail(count).reset_index(drop=True)


def get_ultimo_prezzo(pair: str) -> float:
    """Prezzo corrente approssimato con l'ultima candela a 1 minuto. Usato solo
    per controllare se una posizione aperta ha toccato SL/TP, non per generare segnali."""
    df = get_ohlc(pair, interval=1, count=1)
    return float(df.iloc[-1]["close"])


# Mappa i casi in cui il ticker Kraken non coincide con quello Bitget (es. XBT vs BTC).
MAPPA_TICKER_BITGET = {"XBT": "BTC"}


def pair_kraken_a_bitget(pair: str) -> str:
    """Converte una coppia Kraken (es. XBTUSD) nel simbolo perpetuo Bitget (es. BTCUSDT)."""
    base = pair[:-3] if pair.endswith("USD") else pair
    base = MAPPA_TICKER_BITGET.get(base, base)
    return f"{base}USDT"


def get_prezzo_bitget(pair: str) -> float:
    """Prezzo corrente del perpetuo Bitget corrispondente (API pubblica, nessuna key richiesta).
    Usato per allineare i controlli SL/TP all'exchange su cui operi davvero. Se Bitget non
    risponde o il simbolo non esiste li', il chiamante deve gestire l'eccezione e ripiegare
    su Kraken (fail-open: non deve mai bloccare il monitoraggio)."""
    simbolo = pair_kraken_a_bitget(pair)
    resp = requests.get(
        "https://api.bitget.com/api/v2/mix/market/ticker",
        params={"symbol": simbolo, "productType": "USDT-FUTURES"},
        timeout=10,
    )
    resp.raise_for_status()
    dati = resp.json()
    if dati.get("code") != "00000" or not dati.get("data"):
        raise ValueError(f"Risposta Bitget inattesa per {simbolo}: {dati}")
    riga = dati["data"][0] if isinstance(dati["data"], list) else dati["data"]
    prezzo = riga.get("lastPr") or riga.get("last") or riga.get("close")
    if prezzo is None:
        raise ValueError(f"Prezzo non trovato nella risposta Bitget per {simbolo}: {riga}")
    return float(prezzo)


def get_prezzo_per_monitoraggio(pair: str) -> float:
    """Prezzo da usare per controllare SL/TP: prova prima il ticker Bitget in tempo reale,
    e se non risponde ripiega sull'ultima candela 1m (comunque dati Bitget). Non blocca mai il monitoraggio."""
    try:
        return get_prezzo_bitget(pair)
    except Exception as e:
        logger.info(f"{pair}: ticker Bitget non disponibile ({e}), uso l'ultima candela 1m come riferimento.")
        return get_ultimo_prezzo(pair)


_cache_decimali_bitget = {}  # simbolo Bitget -> numero di decimali accettati (evita richieste ripetute)


def get_decimali_bitget(pair: str) -> int:
    """Interroga l'API pubblica Bitget per sapere quanti decimali accetta DAVVERO
    per quella coppia (campo 'pricePlace' del contratto perpetuo), cosi' gli SL/TP
    che pubblichiamo sono sempre inseribili su Bitget senza errori di precisione.
    Ritorna None se Bitget non risponde o il simbolo non esiste li' - in quel caso
    il chiamante deve ripiegare sulla stima basata sul prezzo."""
    simbolo = pair_kraken_a_bitget(pair)
    if simbolo in _cache_decimali_bitget:
        return _cache_decimali_bitget[simbolo]
    try:
        resp = requests.get(
            "https://api.bitget.com/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES", "symbol": simbolo},
            timeout=10,
        )
        resp.raise_for_status()
        dati = resp.json()
        righe = dati.get("data") or []
        if not righe:
            raise ValueError("nessun contratto trovato")
        riga = righe[0]
        decimali = int(riga.get("pricePlace", riga.get("priceScale")))
        _cache_decimali_bitget[simbolo] = decimali
        return decimali
    except Exception as e:
        logger.info(f"{pair}: precisione prezzo Bitget non disponibile ({e}), uso una stima.")
        return None


# ---------------------------------------------------------------------------
# INDICATORI TECNICI
# ---------------------------------------------------------------------------

def ema(series: pd.Series, periodo: int) -> pd.Series:
    return series.ewm(span=periodo, adjust=False).mean()


def rsi(series: pd.Series, periodo: int = 14) -> pd.Series:
    delta = series.diff()
    guadagno = delta.clip(lower=0)
    perdita = -delta.clip(upper=0)
    media_guadagno = guadagno.rolling(periodo).mean()
    media_perdita = perdita.rolling(periodo).mean()
    rs = media_guadagno / media_perdita.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series):
    ema12 = ema(series, 12)
    ema26 = ema(series, 26)
    linea_macd = ema12 - ema26
    linea_segnale = ema(linea_macd, 9)
    return linea_macd, linea_segnale


def atr(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()


# ---------------------------------------------------------------------------
# LOGICA DI GENERAZIONE SEGNALE
# ---------------------------------------------------------------------------

def analizza_coppia(pair: str) -> tuple[Segnale | None, int, str]:
    """Restituisce (segnale o None, score calcolato, direzione) per poter sempre tracciare lo score,
    anche quando il segnale non supera la soglia minima."""
    try:
        df_4h = get_ohlc(pair, interval=240, count=250)
        df_1h = get_ohlc(pair, interval=60, count=250)
    except Exception as e:
        logger.warning(f"Dati non disponibili per {pair}: {e}")
        return None, -1, ""

    if len(df_4h) < 200 or len(df_1h) < 50:
        return None, -1, ""

    # --- Trend su 4h ---
    df_4h["ema50"] = ema(df_4h["close"], 50)
    df_4h["ema200"] = ema(df_4h["close"], 200)
    ultimo_4h = df_4h.iloc[-1]

    if ultimo_4h["ema50"] > ultimo_4h["ema200"]:
        bias = Direzione.LONG
    else:
        bias = Direzione.SHORT

    # --- Trigger su 1h ---
    df_1h["rsi"] = rsi(df_1h["close"])
    df_1h["macd"], df_1h["macd_signal"] = macd(df_1h["close"])
    df_1h["atr"] = atr(df_1h)
    df_1h["volume_media"] = df_1h["volume"].rolling(20).mean()

    ultimo = df_1h.iloc[-1]
    precedente = df_1h.iloc[-2]

    if pd.isna(ultimo["rsi"]) or pd.isna(precedente["rsi"]) or pd.isna(ultimo["atr"]):
        return None, -1, ""

    score = 0
    motivi = []

    # Trend (30 punti)
    score += 30
    motivi.append(f"Trend {bias.value.lower()} su 4h (EMA50/EMA200)")

    # RSI (25 punti)
    if bias == Direzione.LONG and precedente["rsi"] < 35 and ultimo["rsi"] >= 35:
        score += 25
        motivi.append("RSI in uscita da ipervenduto")
    elif bias == Direzione.SHORT and precedente["rsi"] > 65 and ultimo["rsi"] <= 65:
        score += 25
        motivi.append("RSI in uscita da ipercomprato")

    # MACD (25 punti)
    cross_up = precedente["macd"] < precedente["macd_signal"] and ultimo["macd"] >= ultimo["macd_signal"]
    cross_down = precedente["macd"] > precedente["macd_signal"] and ultimo["macd"] <= ultimo["macd_signal"]
    if bias == Direzione.LONG and cross_up:
        score += 25
        motivi.append("MACD cross rialzista")
    elif bias == Direzione.SHORT and cross_down:
        score += 25
        motivi.append("MACD cross ribassista")

    # Volume (20 punti)
    if pd.notna(ultimo["volume_media"]) and ultimo["volume"] > ultimo["volume_media"] * 1.3:
        score += 20
        motivi.append("Volume sopra media (spike)")

    if score < SCORE_MINIMO_PUBBLICAZIONE:
        logger.info(f"{pair}: score {score}/100 ({bias.value}) - sotto soglia, scartato")
        return None, score, bias.value

    logger.info(f"{pair}: score {score}/100 ({bias.value}) - SEGNALE VALIDO")

    # --- Conferma multi-timeframe su 15m ---
    # Il 4h da' il trend, l'1h da' il trigger: qui controlliamo che anche il momentum
    # a brevissimo termine (15m) sia allineato, per scartare segnali dove il prezzo
    # si sta gia' muovendo contro nell'immediato (riduce falsi segnali).
    try:
        df_15m = get_ohlc(pair, interval=15, count=50)
        df_15m["ema9"] = ema(df_15m["close"], 9)
        df_15m["ema21"] = ema(df_15m["close"], 21)
        ultimo_15m = df_15m.iloc[-1]
        if pd.isna(ultimo_15m["ema9"]) or pd.isna(ultimo_15m["ema21"]):
            conferma_15m = True  # dati insufficienti, non blocchiamo per questo
        else:
            conferma_15m = (
                (bias == Direzione.LONG and ultimo_15m["ema9"] > ultimo_15m["ema21"]) or
                (bias == Direzione.SHORT and ultimo_15m["ema9"] < ultimo_15m["ema21"])
            )
    except Exception as e:
        logger.warning(f"{pair}: impossibile leggere 15m per conferma multi-timeframe ({e}) - procedo comunque")
        conferma_15m = True  # fail-open: un problema di rete sul 15m non deve bloccare il segnale

    if not conferma_15m:
        logger.info(f"{pair}: score {score}/100 ma momentum 15m non allineato - segnale scartato")
        return None, score, bias.value

    motivi.append("Momentum 15m allineato (EMA9/EMA21)")

    entry = ultimo["close"]
    atr_val = ultimo["atr"] if pd.notna(ultimo["atr"]) else entry * 0.01

    # Tre livelli di Take Profit scalati sull'ATR, per uscita parziale della posizione:
    # TP1 = 1.5x ATR (1:1)  -> chiudi una parte, sposta SL a breakeven
    # TP2 = 3x ATR   (1:2)  -> chiudi un'altra parte
    # TP3 = 4.5x ATR (1:3)  -> target finale, resto della posizione
    if bias == Direzione.LONG:
        stop_loss = entry - (1.5 * atr_val)
        tp1 = entry + (1.5 * atr_val)
        tp2 = entry + (3.0 * atr_val)
        tp3 = entry + (4.5 * atr_val)
    else:
        stop_loss = entry + (1.5 * atr_val)
        tp1 = entry - (1.5 * atr_val)
        tp2 = entry - (3.0 * atr_val)
        tp3 = entry - (4.5 * atr_val)

    entry_r = arrotonda_prezzo(pair, entry)
    stop_loss_r = arrotonda_prezzo(pair, stop_loss)
    tp1_r = arrotonda_prezzo(pair, tp1)
    tp2_r = arrotonda_prezzo(pair, tp2)
    tp3_r = arrotonda_prezzo(pair, tp3)
    leva = calcola_leva(entry_r, stop_loss_r)

    segnale = Segnale(
        coppia=pair,
        direzione=bias,
        entry=entry_r,
        stop_loss=stop_loss_r,
        take_profit=tp3_r,
        tp1=tp1_r,
        tp2=tp2_r,
        timeframe="1h (trend 4h)",
        score=score,
        motivazione="; ".join(motivi),
        leva_suggerita=leva,
    )
    return segnale, score, bias.value


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def formatta_messaggio(s: Segnale) -> str:
    emoji_testata = "🚀" if s.score >= 80 else "📡"
    emoji_direzione = "🟢" if s.direzione == Direzione.LONG else "🔴"
    label_direzione = "LONG (Buy)" if s.direzione == Direzione.LONG else "SHORT (Sell)"
    piene = round(s.score / 10)
    barra = "█" * piene + "░" * (10 - piene)
    segno_sl = "-" if s.direzione == Direzione.LONG else "+"
    segno_tp = "+" if s.direzione == Direzione.LONG else "-"

    return (
        f"{emoji_testata} *SALA SEGNALI VIP* {emoji_testata}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{emoji_direzione} *Asset:* `{s.coppia}`\n"
        f"*Direzione:* {label_direzione}\n"
        f"*Timeframe:* {s.timeframe}\n"
        f"*Leva suggerita:* {s.leva_suggerita}x _(calcolata: rischio {RISCHIO_PER_TRADE_PERCENTO}% ÷ distanza SL {s.sl_percento}%)_\n\n"
        f"📍 *Entry Zone:* `{s.entry}`\n\n"
        f"🎯 *TP 1:* `{s.tp1}` ({segno_tp}{s.tp_percento(s.tp1)}%)\n"
        f"🎯 *TP 2:* `{s.tp2}` ({segno_tp}{s.tp_percento(s.tp2)}%)\n"
        f"🎯 *TP 3:* `{s.take_profit}` ({segno_tp}{s.tp_percento(s.take_profit)}%)\n"
        f"🛑 *Stop Loss:* `{s.stop_loss}` ({segno_sl}{s.sl_percento}%)\n\n"
        f"*Risk/Reward:* 1:{s.risk_reward} _(su TP3)_\n"
        f"*Confidenza:* {barra} {s.score}/100\n\n"
        f"📊 *Analisi:* _{s.motivazione}_\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"⚠️ _Segnale generato automaticamente da indicatori tecnici reali (Bitget). "
        f"Tutti i valori sopra sono calcolati sul prezzo, non fissi. Non è consulenza finanziaria. DYOR._"
    )


async def invia_segnale(bot: Bot, segnale: Segnale) -> bool:
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatta_messaggio(segnale),
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Pubblicato: {segnale.coppia} {segnale.direzione.value} score={segnale.score}")
        return True
    except TelegramError as e:
        logger.error(f"Errore invio Telegram: {e}")
        return False


def _font(nome: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(ASSETS_DIR, nome), size)


def genera_card_risultato(s: Segnale, esito_positivo: bool, percentuale: float, prezzo_chiusura: float) -> io.BytesIO:
    """Card grafica brandizzata (logo Sala Segnali VIP) per un segnale chiuso in
    vittoria (TP3) o perdita (SL). Ritorna un buffer PNG pronto da inviare su Telegram."""
    W, H = 1100, 750
    img = Image.new("RGB", (W, H), (7, 12, 24))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(int(7 + 7 * t), int(12 + 8 * t), int(24 + 16 * t)))

    try:
        logo = Image.open(os.path.join(ASSETS_DIR, "logo.png")).convert("RGBA")
        logo_h = 130
        logo_w = int(logo.width * (logo_h / logo.height))
        logo = logo.resize((logo_w, logo_h))
        img.paste(logo, (50, 40), logo)
        titolo_x = 50 + logo_w + 25
    except Exception as e:
        logger.warning(f"Impossibile caricare il logo per la card ({e}), procedo senza.")
        titolo_x = 50

    draw.text((titolo_x, 60), "SALA SEGNALI VIP", font=_font("font_bold.ttf", 44), fill=(230, 190, 90))
    draw.text((titolo_x, 115), datetime.now().strftime("%d/%m/%Y %H:%M"),
               font=_font("font_regular.ttf", 26), fill=(140, 150, 170))

    y = 220
    colore_dir = (60, 210, 130) if s.direzione == Direzione.LONG else (230, 80, 90)
    draw.text((50, y), s.coppia, font=_font("font_bold.ttf", 56), fill=(255, 255, 255))
    draw.text((50, y + 70), f"Bitget Perpetuo   |   {s.direzione.value}   |   {s.leva_suggerita}x",
               font=_font("font_regular.ttf", 30), fill=colore_dir)

    colore_pct = (60, 210, 130) if esito_positivo else (230, 80, 90)
    segno = "+" if esito_positivo else "-"
    draw.text((50, y + 150), f"{segno}{abs(percentuale)}%", font=_font("font_bold_big.ttf", 130), fill=colore_pct)

    y2 = y + 340
    draw.text((50, y2), "Prezzo d'ingresso", font=_font("font_regular.ttf", 26), fill=(140, 150, 170))
    draw.text((50, y2 + 40), f"{s.entry}", font=_font("font_bold.ttf", 38), fill=(255, 255, 255))
    draw.text((420, y2), "Prezzo di chiusura", font=_font("font_regular.ttf", 26), fill=(140, 150, 170))
    draw.text((420, y2 + 40), f"{prezzo_chiusura}", font=_font("font_bold.ttf", 38), fill=(255, 255, 255))

    draw.line([(50, H - 90), (W - 50, H - 90)], fill=(40, 48, 65), width=2)
    draw.text(
        (50, H - 65),
        "Segnale generato da analisi tecnica automatica — non è consulenza finanziaria. DYOR.",
        font=_font("font_regular.ttf", 20), fill=(110, 120, 140),
    )

    buffer = io.BytesIO()
    buffer.name = "risultato.png"
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


async def invia_card_risultato(bot: Bot, s: Segnale, esito_positivo: bool, percentuale: float, prezzo_chiusura: float):
    """Manda la card grafica sul canale. Se la generazione o l'invio falliscono per
    qualunque motivo, non deve MAI bloccare il resto (il messaggio di testo che segue
    resta comunque la fonte di verita')."""
    try:
        buffer = genera_card_risultato(s, esito_positivo, percentuale, prezzo_chiusura)
        await bot.send_photo(chat_id=CHANNEL_ID, photo=buffer)
    except Exception as e:
        logger.warning(f"Impossibile generare/inviare la card grafica per {s.coppia}: {e}")


def formatta_messaggio_aggiornamento(s: Segnale, titolo: str, corpo: str) -> str:
    emoji_direzione = "🟢" if s.direzione == Direzione.LONG else "🔴"
    return (
        f"{titolo}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{emoji_direzione} *Asset:* `{s.coppia}` ({s.direzione.value})\n"
        f"{corpo}\n"
        f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )


async def invia_aggiornamento(bot: Bot, s: Segnale, titolo: str, corpo: str):
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatta_messaggio_aggiornamento(s, titolo, corpo),
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Aggiornamento inviato: {s.coppia} - {titolo}")
    except TelegramError as e:
        logger.error(f"Errore invio aggiornamento: {e}")


async def controlla_posizioni_aperte(bot: Bot, posizioni_aperte: dict, statistiche: dict):
    """Controlla ogni posizione aperta rispetto al prezzo corrente e manda un
    aggiornamento reale su Telegram quando SL o un TP viene toccato.
    Aggiorna anche il conteggio vinti/persi per le statistiche del canale."""
    chiuse = []

    for pair, pos in list(posizioni_aperte.items()):
        s: Segnale = pos["segnale"]
        try:
            prezzo = get_prezzo_per_monitoraggio(pair)
        except Exception as e:
            logger.warning(f"Impossibile leggere prezzo corrente per {pair}: {e}")
            continue

        long = s.direzione == Direzione.LONG

        # --- Stop Loss: chiude la posizione, conta come persa ---
        sl_colpito = (prezzo <= s.stop_loss) if long else (prezzo >= s.stop_loss)
        if sl_colpito:
            await invia_card_risultato(bot, s, esito_positivo=False, percentuale=s.sl_percento, prezzo_chiusura=prezzo)
            await invia_aggiornamento(
                bot, s, "🛑 *STOP LOSS COLPITO*",
                f"Stop Loss raggiunto su `{pair}`. Perdita: -{s.sl_percento}% dall'entry. Posizione chiusa."
            )
            statistiche["persi"] += 1
            statistiche["totali"] += 1
            statistiche["perdite_consecutive"] = statistiche.get("perdite_consecutive", 0) + 1
            per_coppia = statistiche.setdefault("per_coppia", {})
            per_coppia.setdefault(pair, {"vinti": 0, "persi": 0})["persi"] += 1
            chiuse.append(pair)

            # --- Circuit breaker: troppe perdite di fila -> pausa automatica ---
            if statistiche["perdite_consecutive"] >= MAX_PERDITE_CONSECUTIVE and not statistiche.get("pausa_fino"):
                pausa_fino = datetime.now() + timedelta(hours=PAUSA_ORE)
                statistiche["pausa_fino"] = pausa_fino.isoformat()
                try:
                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=(
                            f"⏸ *Pausa automatica attivata*\n"
                            f"{MAX_PERDITE_CONSECUTIVE} Stop Loss consecutivi raggiunti. "
                            f"Pubblicazione nuovi segnali sospesa fino alle "
                            f"{pausa_fino.strftime('%d/%m %H:%M')} per proteggere il capitale.\n"
                            f"Le posizioni gia' aperte restano monitorate normalmente."
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except TelegramError as e:
                    logger.error(f"Errore invio messaggio di pausa: {e}")
                await notifica_admin(
                    bot,
                    f"⏸ *Pausa automatica attivata*\n{MAX_PERDITE_CONSECUTIVE} SL consecutivi. "
                    f"Ripresa prevista: {pausa_fino.strftime('%d/%m %H:%M')}"
                )

            await asyncio.sleep(1)
            continue

        # --- TP3: target finale, chiude la posizione, conta come vinta ---
        tp3_colpito = (prezzo >= s.take_profit) if long else (prezzo <= s.take_profit)
        if tp3_colpito:
            await invia_card_risultato(
                bot, s, esito_positivo=True, percentuale=s.tp_percento(s.take_profit), prezzo_chiusura=prezzo
            )
            await invia_aggiornamento(
                bot, s, "✅ *TARGET FINALE RAGGIUNTO (TP3)*",
                f"Target finale centrato su `{pair}`. Guadagno: +{s.tp_percento(s.take_profit)}% dall'entry. Posizione chiusa."
            )
            statistiche["vinti"] += 1
            statistiche["totali"] += 1
            statistiche["perdite_consecutive"] = 0
            per_coppia = statistiche.setdefault("per_coppia", {})
            per_coppia.setdefault(pair, {"vinti": 0, "persi": 0})["vinti"] += 1
            chiuse.append(pair)
            await asyncio.sleep(1)
            continue

        # --- TP2: uscita parziale, non chiude ancora la posizione ---
        if not pos["tp2_raggiunto"]:
            tp2_colpito = (prezzo >= s.tp2) if long else (prezzo <= s.tp2)
            if tp2_colpito:
                pos["tp2_raggiunto"] = True
                await invia_aggiornamento(
                    bot, s, "🎯 *TP2 RAGGIUNTO*",
                    f"Secondo target raggiunto su `{pair}` (+{s.tp_percento(s.tp2)}%). "
                    f"Consigliato chiudere un'altra parte della posizione."
                )

        # --- TP1: prima uscita parziale ---
        if not pos["tp1_raggiunto"]:
            tp1_colpito = (prezzo >= s.tp1) if long else (prezzo <= s.tp1)
            if tp1_colpito:
                pos["tp1_raggiunto"] = True
                await invia_aggiornamento(
                    bot, s, "🎯 *TP1 RAGGIUNTO*",
                    f"Primo target raggiunto su `{pair}` (+{s.tp_percento(s.tp1)}%). "
                    f"Consigliato chiudere una parte e spostare lo Stop Loss a breakeven."
                )

        await asyncio.sleep(1)

    for pair in chiuse:
        posizioni_aperte.pop(pair, None)


async def invia_statistiche(bot: Bot, statistiche: dict):
    """Pubblica sul canale le statistiche reali (non teoriche) raccolte dal bot,
    quante posizioni chiuse, vinte, perse, win rate. Persistono tra i redeploy se e'
    configurato il Volume su Railway (altrimenti si azzerano ad ogni riavvio)."""
    totali = statistiche["totali"]

    if totali == 0:
        testo = (
            "📊 *Statistiche Sala Segnali*\n"
            "Nessuna posizione ancora chiusa (SL o TP3) da quando il bot è attivo.\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
    else:
        vinti = statistiche["vinti"]
        persi = statistiche["persi"]
        win_rate = round(vinti / totali * 100, 1)
        testo = (
            "📊 *Statistiche Sala Segnali*\n"
            "━━━━━━━━━━━━━━━\n"
            f"Posizioni chiuse: {totali}\n"
            f"Vinte (TP3 raggiunto): {vinti}  |  Perse (SL colpito): {persi}\n"
            f"Win rate: {win_rate}%\n\n"
            "_Dati reali raccolti dal bot. \"Vinta\" = target finale TP3 raggiunto; "
            "\"Persa\" = Stop Loss colpito. Non è consulenza finanziaria._\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
        logger.info("Statistiche pubblicate sul canale")
    except TelegramError as e:
        logger.error(f"Errore invio statistiche: {e}")


def _testo_statistiche(statistiche: dict) -> str:
    """Testo condiviso tra il messaggio periodico sul canale e il comando /stats privato."""
    totali = statistiche.get("totali", 0)
    if totali == 0:
        return "Nessuna posizione ancora chiusa (SL o TP3) da quando il bot è attivo."
    vinti = statistiche.get("vinti", 0)
    persi = statistiche.get("persi", 0)
    win_rate = round(vinti / totali * 100, 1)
    testo = (
        f"Posizioni chiuse: {totali}\n"
        f"Vinte (TP3): {vinti}  |  Perse (SL): {persi}\n"
        f"Win rate: {win_rate}%"
    )
    pausa_fino = statistiche.get("pausa_fino")
    if pausa_fino:
        try:
            pf = datetime.fromisoformat(pausa_fino)
            if datetime.now() < pf:
                testo += f"\n\n⏸ Pausa automatica attiva fino alle {pf.strftime('%d/%m %H:%M')}"
        except ValueError:
            pass
    return testo


async def invia_report_settimanale(bot: Bot, statistiche: dict):
    """Report settimanale con le coppie migliori/peggiori, in aggiunta alle
    statistiche generali gia' pubblicate ogni 24h."""
    per_coppia = statistiche.get("per_coppia", {})
    if not per_coppia:
        testo = (
            "📅 *Report settimanale*\n"
            "Ancora nessun dato sufficiente per un report per coppia.\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
    else:
        classifica = []
        for pair, esiti in per_coppia.items():
            v, p = esiti.get("vinti", 0), esiti.get("persi", 0)
            tot = v + p
            if tot == 0:
                continue
            classifica.append((pair, v, p, tot, v - p))

        classifica_migliori = sorted(classifica, key=lambda x: x[4], reverse=True)[:3]
        classifica_peggiori = sorted(classifica, key=lambda x: x[4])[:3]

        righe_migliori = "\n".join(f"  `{p}` — {v}V/{pe}P" for p, v, pe, t, n in classifica_migliori) or "  (dati insufficienti)"
        righe_peggiori = "\n".join(f"  `{p}` — {v}V/{pe}P" for p, v, pe, t, n in classifica_peggiori) or "  (dati insufficienti)"

        testo = (
            "📅 *Report settimanale*\n"
            "━━━━━━━━━━━━━━━\n"
            f"{_testo_statistiche(statistiche)}\n\n"
            f"🏆 *Coppie migliori (vinti-persi):*\n{righe_migliori}\n\n"
            f"⚠️ *Coppie peggiori:*\n{righe_peggiori}\n\n"
            "_Dati cumulativi reali raccolti dal bot._\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
        logger.info("Report settimanale pubblicato sul canale")
    except TelegramError as e:
        logger.error(f"Errore invio report settimanale: {e}")


MESSAGGIO_BENVENUTO = (
    "🏛 *SALA SEGNALI VIP*\n"
    "━━━━━━━━━━━━━━━\n"
    f"Bot automatico che analizza {len(WATCHLIST)} coppie (dati Bitget: EMA, RSI, MACD, ATR, Volume) "
    f"e pubblica solo i setup con punteggio di confidenza ≥{SCORE_MINIMO_PUBBLICAZIONE}/100.\n\n"
    "📍 Ogni segnale include Entry, Stop Loss, 3 Take Profit scalari e leva calcolata "
    "sul rischio reale — mai un numero fisso.\n"
    "🎯 Analisi e monitoraggio SL/TP interamente su dati Bitget, lo stesso exchange su cui si opera.\n"
    "📊 Statistiche reali pubblicate periodicamente (non promesse, dati veri).\n\n"
    "⚠️ *Importante:* nessun segnale è garantito. Questo è un sistema di analisi tecnica "
    "automatica, non consulenza finanziaria. Fai sempre le tue verifiche (DYOR) e non "
    "rischiare mai più di quanto puoi permetterti di perdere."
)


async def invia_e_fissa_benvenuto(bot: Bot):
    try:
        msg = await bot.send_message(chat_id=CHANNEL_ID, text=MESSAGGIO_BENVENUTO, parse_mode=ParseMode.MARKDOWN)
        try:
            await bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=msg.message_id, disable_notification=True)
            logger.info("Messaggio di benvenuto pubblicato e fissato in cima al canale")
        except TelegramError as e:
            logger.warning(
                f"Messaggio pubblicato ma non fissato (il bot deve essere admin con permesso "
                f"'Pin messages' sul canale): {e}"
            )
    except TelegramError as e:
        logger.error(f"Errore invio messaggio di benvenuto: {e}")


# ---------------------------------------------------------------------------
# LOOP PRINCIPALE
# ---------------------------------------------------------------------------

async def ciclo_scansione(bot: Bot, ultimo_segnale: dict, posizioni_aperte: dict, statistiche: dict):
    ora = datetime.now()

    # --- Circuit breaker: se siamo in pausa, non pubblichiamo nuovi segnali ---
    pausa_fino_str = statistiche.get("pausa_fino")
    if pausa_fino_str:
        try:
            pausa_fino = datetime.fromisoformat(pausa_fino_str)
        except ValueError:
            pausa_fino = None
        if pausa_fino and ora < pausa_fino:
            logger.info(
                f"Pausa automatica attiva fino a {pausa_fino.strftime('%d/%m %H:%M')} - "
                f"scansione saltata (nessun nuovo segnale pubblicato)."
            )
            return
        else:
            statistiche["pausa_fino"] = None
            statistiche["perdite_consecutive"] = 0
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text="▶️ *Pausa terminata* — riprendiamo la pubblicazione dei segnali.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError as e:
                logger.error(f"Errore invio messaggio di ripresa: {e}")
            logger.info("Pausa automatica terminata, riprendo la pubblicazione dei segnali.")

    logger.info(f"--- Inizio scansione: {len(WATCHLIST)} coppie ---")

    risultati = []  # (pair, score, direzione) - solo per log interni, non più pubblicati
    almeno_un_segnale = False

    for pair in WATCHLIST:
        ultima_pubblicazione = ultimo_segnale.get(pair)
        if ultima_pubblicazione and (ora - ultima_pubblicazione) < timedelta(hours=COOLDOWN_ORE):
            logger.info(f"{pair}: in cooldown, salto")
            continue

        segnale, score, direzione = analizza_coppia(pair)
        if score >= 0:
            risultati.append((pair, score, direzione))

        if segnale:
            aperte_stessa_direzione = sum(
                1 for p in posizioni_aperte.values() if p["segnale"].direzione == segnale.direzione
            )
            if aperte_stessa_direzione >= MAX_POSIZIONI_STESSA_DIREZIONE:
                logger.info(
                    f"{pair}: segnale valido (score {score}) ma gia' {aperte_stessa_direzione} posizioni "
                    f"{segnale.direzione.value} aperte (max {MAX_POSIZIONI_STESSA_DIREZIONE}) - scartato "
                    f"per anti-correlazione"
                )
            else:
                inviato = await invia_segnale(bot, segnale)
                if inviato:
                    ultimo_segnale[pair] = ora
                    posizioni_aperte[pair] = {
                        "segnale": segnale,
                        "tp1_raggiunto": False,
                        "tp2_raggiunto": False,
                        "aperto_il": ora,
                    }
                    almeno_un_segnale = True

        await asyncio.sleep(1)  # rispetto rate limit Bitget (non bloccante)

    logger.info("--- Fine scansione ---")
    if not almeno_un_segnale:
        await invia_riepilogo(bot)


async def invia_riepilogo(bot: Bot):
    """Nessun setup ha superato la soglia in questo ciclo. Non pubblichiamo piu' nulla
    sul canale per questo (una sala segnali VIP resta silenziosa finche' non ha un
    segnale vero da dare) - restiamo solo nei log Railway per il monitoraggio interno."""
    logger.info("Nessun segnale sopra soglia in questo ciclo - nessun messaggio inviato al canale.")


async def notifica_admin(bot: Bot, testo: str):
    """Manda un avviso privato a Fabio (non al canale pubblico) quando succede
    qualcosa che merita attenzione, es. un errore nel ciclo di scansione.
    Se ADMIN_CHAT_ID non e' configurato, si limita a loggare un avviso una tantum."""
    if not ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        # Non deve MAI poter interrompere il bot: se anche la notifica fallisce, solo un log.
        logger.warning(f"Impossibile inviare la notifica privata all'admin: {e}")


# ---------------------------------------------------------------------------
# COMANDI BOT (funzionano solo in chat privata con il bot, non nel canale)
# ---------------------------------------------------------------------------

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛 Ciao! Sono il bot della Sala Segnali VIP.\n\n"
        "Comandi disponibili:\n"
        "/stats — statistiche reali aggiornate\n"
        "/regole — come funziona la sala segnali\n"
        "/help — questo messaggio"
    )


async def cmd_help(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandi disponibili:\n"
        "/stats — statistiche reali aggiornate (vinti/persi/win rate)\n"
        "/regole — come funziona la sala segnali e i suoi limiti\n"
        "/help — questo messaggio"
    )


async def cmd_regole(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MESSAGGIO_BENVENUTO, parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update, context: ContextTypes.DEFAULT_TYPE):
    statistiche = context.bot_data.get("statistiche", {"vinti": 0, "persi": 0, "totali": 0})
    testo = "📊 *Statistiche Sala Segnali*\n━━━━━━━━━━━━━━━\n" + _testo_statistiche(statistiche)
    await update.message.reply_text(testo, parse_mode=ParseMode.MARKDOWN)


async def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN o CHANNEL_ID mancanti. Impostali come variabili d'ambiente.")
        return
    if not ADMIN_CHAT_ID:
        logger.warning(
            "ADMIN_CHAT_ID non impostato: le notifiche private di errore sono disattivate. "
            "Imposta questa variabile con il tuo chat_id Telegram personale per riceverle."
        )

    # Application invece del semplice Bot: serve per ricevere ed elaborare i comandi
    # /start /help /regole /stats che gli iscritti possono mandare in chat privata al bot.
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("regole", cmd_regole))
    application.add_handler(CommandHandler("stats", cmd_stats))
    bot = application.bot  # stesso identico oggetto Bot usato finora per inviare messaggi

    ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche, ultimo_invio_report = carica_stato()
    # bot_data e' condiviso con i comandi (es. /stats) - passiamo lo STESSO dict, non una copia,
    # cosi' ogni aggiornamento fatto nel ciclo di scansione e' visibile subito anche ai comandi.
    application.bot_data["statistiche"] = statistiche

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Comandi bot (/start /help /regole /stats) attivi in chat privata.")

    # Il messaggio di benvenuto/pin non deve MAI poter bloccare l'avvio del bot:
    # se fallisce per un motivo imprevisto, logghiamo l'errore completo e andiamo avanti comunque.
    try:
        await invia_e_fissa_benvenuto(bot)
    except Exception:
        logger.error("Errore critico durante l'invio del messaggio di benvenuto (il bot continua comunque):")
        logger.error(traceback.format_exc())

    logger.info("Bot avviato. Scansione ogni %d secondi.", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            await ciclo_scansione(bot, ultimo_segnale, posizioni_aperte, statistiche)
            await controlla_posizioni_aperte(bot, posizioni_aperte, statistiche)

            if datetime.now() - ultimo_invio_statistiche >= timedelta(hours=STATISTICHE_INTERVALLO_ORE):
                await invia_statistiche(bot, statistiche)
                ultimo_invio_statistiche = datetime.now()

            if datetime.now() - ultimo_invio_report >= timedelta(days=REPORT_SETTIMANALE_INTERVALLO_GIORNI):
                await invia_report_settimanale(bot, statistiche)
                ultimo_invio_report = datetime.now()

            salva_stato(ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche, ultimo_invio_report)
        except Exception:
            # Logghiamo il traceback COMPLETO (non solo il messaggio) cosi', se il bot
            # dovesse mai fermarsi di nuovo, l'errore vero resta scritto nei log Railway.
            tb = traceback.format_exc()
            logger.error("Errore nel ciclo di scansione:")
            logger.error(tb)
            ultima_riga_errore = tb.strip().splitlines()[-1] if tb.strip() else "Errore sconosciuto"
            await notifica_admin(
                bot,
                f"⚠️ *Errore nel bot Segnali AI*\n`{ultima_riga_errore}`\n\n"
                f"Il bot sta continuando a girare, ma controlla i log Railway per i dettagli."
            )

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.error("Errore FATALE non gestito, il processo sta per terminare:")
        logger.error(traceback.format_exc())
        raise
