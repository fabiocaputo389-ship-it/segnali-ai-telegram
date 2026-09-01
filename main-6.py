"""
Segnali AI - Bitget -> Telegram + TRADING AUTOMATICO + MINI APP
------------------------------------------------------------------
Bot che legge i prezzi pubblici da Bitget (perpetui USDT-FUTURES), calcola indicatori
tecnici, pubblica i segnali su un canale Telegram privato, APRE AUTOMATICAMENTE le
posizioni su Bitget (Demo Trading di default) ed espone una Telegram Mini App (dashboard
web che si apre dentro Telegram) per monitorare tutto e controllare il bot.

VARIABILI D'AMBIENTE RICHIESTE (da impostare su Railway, mai nel codice):
    BOT_TOKEN            -> token del bot Telegram (da @BotFather)
    CHANNEL_ID           -> id numerico del canale privato (es. -1001234567890)
    BITGET_API_KEY       -> API key Bitget (creata sulla pagina Demo Trading per iniziare in demo)
    BITGET_API_SECRET    -> API secret Bitget
    BITGET_API_PASSPHRASE-> passphrase impostata alla creazione della API key

VARIABILI OPZIONALI:
    ADMIN_CHAT_ID         -> chat privata Telegram di Fabio, per le notifiche di errore
    BITGET_DEMO           -> "true" (default) = si parte in Demo Trading. "false" = si parte
                              in LIVE. Da qui in poi la modalita' e' comunque cambiabile in
                              qualsiasi momento dalla Mini App (vedi sotto), quindi questa
                              variabile conta solo per il primissimo avvio senza stato salvato.
    BITGET_API_KEY_LIVE / BITGET_API_SECRET_LIVE / BITGET_API_PASSPHRASE_LIVE
                           -> credenziali del conto LIVE (soldi reali), SEPARATE da quelle
                              demo sopra. Senza queste, la Mini App rifiuta di passare in
                              modalita' live anche se richiesto (interruttore di sicurezza).
    TRADING_ABILITATO     -> "true" (default) = apre davvero le posizioni su Bitget.
    AUTHORIZED_USER_ID    -> id utente Telegram di Fabio (numero). Se impostato, la Mini
                              App accetta comandi SOLO da questo utente, anche se qualcun
                              altro dovesse ottenere il link. Fortemente consigliato.
    PORT                  -> porta HTTP per la Mini App (Railway la imposta da sola).

MINI APP:
    Il bot serve una dashboard web (dashboard.html, da caricare insieme a main.py) su
    HTTPS grazie al dominio pubblico che Railway assegna al servizio. Si apre da dentro
    Telegram con il comando /dashboard. L'autenticazione avviene tramite initData firmato
    da Telegram (HMAC-SHA256 col bot token) - nessuna password da gestire, ma solo chi
    apre il bottone dal proprio Telegram puo' usarla.

Dipendenze (vedi requirements.txt):
    pip install python-telegram-bot pandas numpy requests Pillow aiohttp
"""

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import math
import os
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from urllib.parse import parse_qsl

import numpy as np
import pandas as pd
import requests
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # chat privata Telegram di Fabio, per le notifiche di errore

# --- Trading automatico su Bitget ---
BITGET_API_KEY = os.environ.get("BITGET_API_KEY", "")
BITGET_API_SECRET = os.environ.get("BITGET_API_SECRET", "")
BITGET_API_PASSPHRASE = os.environ.get("BITGET_API_PASSPHRASE", "")
# Credenziali del conto LIVE (soldi reali) - opzionali, SEPARATE da quelle demo sopra.
# Bitget richiede una API key dedicata per il Demo Trading, quindi le due coppie di
# chiavi sono normalmente diverse. Finche' queste non sono impostate, il bot rifiuta
# di passare in modalita' live anche se richiesto dalla dashboard (interruttore di sicurezza).
BITGET_API_KEY_LIVE = os.environ.get("BITGET_API_KEY_LIVE", "")
BITGET_API_SECRET_LIVE = os.environ.get("BITGET_API_SECRET_LIVE", "")
BITGET_API_PASSPHRASE_LIVE = os.environ.get("BITGET_API_PASSPHRASE_LIVE", "")
BITGET_DEMO_DEFAULT = os.environ.get("BITGET_DEMO", "true").strip().lower() != "false"
TRADING_ABILITATO = os.environ.get("TRADING_ABILITATO", "true").strip().lower() != "false"
BITGET_BASE_URL = "https://api.bitget.com"

# --- Mini App (dashboard web dentro Telegram) ---
AUTHORIZED_USER_ID = os.environ.get("AUTHORIZED_USER_ID", "").strip()
WEB_PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")

# Configurazione RUNTIME (mutabile dalla Mini App, a differenza delle costanti sopra) -
# persistita in stato_bot.json cosi' sopravvive ai redeploy.
CONFIG = {
    "rischio_percento": 3.0,   # sovrascrive RISCHIO_PER_TRADE_PERCENTO se diverso da default
    "pausa_manuale": False,    # pausa decisa da Fabio dalla dashboard, indipendente dal circuit breaker
    "modalita": "demo" if BITGET_DEMO_DEFAULT else "live",  # "demo" o "live" - cambiabile dalla dashboard
}


def bitget_demo_attivo() -> bool:
    return CONFIG["modalita"] == "demo"


def credenziali_bitget_correnti() -> tuple:
    """Ritorna (api_key, api_secret, passphrase) per la modalita' attualmente attiva
    (demo o live). Le due modalita' usano credenziali SEPARATE per costruzione."""
    if bitget_demo_attivo():
        return BITGET_API_KEY, BITGET_API_SECRET, BITGET_API_PASSPHRASE
    return BITGET_API_KEY_LIVE, BITGET_API_SECRET_LIVE, BITGET_API_PASSPHRASE_LIVE

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
# pair_kraken_a_bitget() sia per l'analisi che per il monitoraggio SL/TP e il trading.
WATCHLIST = [
    # Ridotta su richiesta di Fabio (01/09/2026) alle sole major cap, per maggiore
    # tranquillita' - coppie piu' liquide e storicamente meno erratiche delle altcoin
    # minori rimosse. XBTUSD = Bitcoin (convenzione di naming Kraken usata internamente).
    "XBTUSD", "ETHUSD", "SOLUSD", "BNBUSD",
]

# Il Demo Trading USDT-M Futures di Bitget supporta SOLO queste 4 coppie (limite della
# piattaforma, non nostro - confermato dalla pagina di supporto ufficiale Bitget). Tutte
# le altre coppie della WATCHLIST restano valide per l'analisi e per il LIVE, ma in Demo
# Trading il bot non tentera' di aprirle (fallirebbero sempre con "Parameter ... does not
# exist") - il segnale viene comunque pubblicato su Telegram come informativo.
COPPIE_TRADABILI_IN_DEMO = {"XBTUSD", "ETHUSD", "EOSUSD", "XRPUSD"}

SCAN_INTERVAL_SECONDS = 600       # ogni 10 minuti
SCORE_MINIMO_PUBBLICAZIONE = 65   # su 100 - confermato dal backtest: EV +0.043R/trade, ~15 segnali/giorno
COOLDOWN_ORE = 4                  # non ripetere segnale stesso asset entro N ore
STATISTICHE_INTERVALLO_ORE = 24    # ogni quanto pubblicare le statistiche reali sul canale
MAX_POSIZIONI_STESSA_DIREZIONE = 5 # evita di accumulare troppo rischio correlato (tutte crypto si muovono insieme)
MAX_PERDITE_CONSECUTIVE = 5        # dopo N stop loss di fila, pausa automatica (circuit breaker)
PAUSA_ORE = 12                     # durata della pausa prima di riprendere da soli
REPORT_SETTIMANALE_INTERVALLO_GIORNI = 7

# --- Parametri per il calcolo della leva e del capitale per trade ---
# La leva NON e' un moltiplicatore di guadagno: serve solo a mantenere costante
# la % di capitale REALE a rischio, dato quanto e' "stretto" o "largo" lo Stop Loss.
# Il margine allocato su ogni trade = saldo disponibile su Bitget * questa percentuale.
RISCHIO_PER_TRADE_PERCENTO_DEFAULT = 3.0  # valore iniziale di CONFIG["rischio_percento"]
LEVA_MASSIMA = 10                  # tetto di sicurezza, mai superato indipendentemente dal calcolo
LEVA_MINIMA = 1
SOGLIA_CHIUSURA_RESIDUA = 0.0  # sotto questa quantita' (dopo arrotondamento) la posizione e' considerata chiusa

# Numero di decimali di riserva se Bitget non risponde alla richiesta di precisione live
# (vedi get_decimali_bitget). Valori approssimati sui vecchi tick-size Kraken (agosto 2026),
# tenuti come seconda rete di sicurezza prima della stima generica dal prezzo.
DECIMALI_PREZZO = {
    "XBTUSD": 1, "ETHUSD": 2, "SOLUSD": 2, "XRPUSD": 4, "ADAUSD": 4,
    "DOGEUSD": 5, "AVAXUSD": 3, "DOTUSD": 3, "LINKUSD": 3, "MATICUSD": 4,
    "LTCUSD": 2, "BCHUSD": 1, "ATOMUSD": 3, "UNIUSD": 3, "ARBUSD": 4,
    "TRXUSD": 5, "NEARUSD": 3, "APTUSD": 3, "FILUSD": 3, "ICPUSD": 3,
    "OPUSD": 4, "SUIUSD": 4, "INJUSD": 3, "RENDERUSD": 3, "TIAUSD": 3,
    "SEIUSD": 4, "AAVEUSD": 2, "MKRUSD": 1, "SNXUSD": 4, "GRTUSD": 5,
    "SANDUSD": 4, "MANAUSD": 4, "AXSUSD": 3, "FTMUSD": 5, "ALGOUSD": 4,
    "EGLDUSD": 2, "FLOWUSD": 4, "CHZUSD": 5, "KSMUSD": 2, "XLMUSD": 5,
}


def decimali_per_coppia(pair: str, prezzo: float) -> int:
    """Decimali da usare per SL/TP. Priorita': precisione reale di Bitget (dove si opera
    davvero) -> tabella Kraken calibrata a mano -> stima prudente dal prezzo."""
    decimali_bitget = get_decimali_bitget(pair)
    if decimali_bitget is not None:
        return decimali_bitget
    if pair in DECIMALI_PREZZO:
        return DECIMALI_PREZZO[pair]
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
    (CONFIG["rischio_percento"]) rispetto al capitale, dato quanto e' distante lo SL.
    """
    distanza_percento = abs(entry - stop_loss) / entry * 100
    if distanza_percento <= 0:
        return LEVA_MINIMA
    leva = CONFIG["rischio_percento"] / distanza_percento
    leva_arrotondata = round(leva)
    return max(LEVA_MINIMA, min(LEVA_MASSIMA, leva_arrotondata))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("segnali_ai")

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
                 ultimo_invio_statistiche: datetime, ultimo_invio_report: datetime,
                 storia_segnali: list = None):
    try:
        dati = {
            "ultimo_segnale": {pair: ts.isoformat() for pair, ts in ultimo_segnale.items()},
            "posizioni_aperte": {
                pair: {
                    "segnale": _segnale_to_dict(p["segnale"]),
                    "tp1_raggiunto": p["tp1_raggiunto"],
                    "tp2_raggiunto": p["tp2_raggiunto"],
                    "aperto_il": p["aperto_il"].isoformat(),
                    "quantita_totale": p.get("quantita_totale", 0),
                    "quantita_rimanente": p.get("quantita_rimanente", 0),
                    "quantita_tp1": p.get("quantita_tp1", 0),
                    "quantita_tp2": p.get("quantita_tp2", 0),
                    "quantita_tp3": p.get("quantita_tp3", 0),
                    "sl_order_id": p.get("sl_order_id"),
                    "tp_order_ids": p.get("tp_order_ids", []),
                }
                for pair, p in posizioni_aperte.items()
            },
            "statistiche": statistiche,
            "ultimo_invio_statistiche": ultimo_invio_statistiche.isoformat(),
            "ultimo_invio_report": ultimo_invio_report.isoformat(),
            "config": CONFIG,
            "storia_segnali": storia_segnali or [],
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
        "vinti": 0, "persi": 0, "pareggi": 0, "totali": 0,
        "perdite_consecutive": 0, "pausa_fino": None, "per_coppia": {},
        "pnl_totale_usd": 0.0,
    }
    default = ({}, {}, statistiche_default, datetime.now(), datetime.now(), [])
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
                "quantita_totale": p.get("quantita_totale", 0),
                "quantita_rimanente": p.get("quantita_rimanente", 0),
                "quantita_tp1": p.get("quantita_tp1", 0),
                "quantita_tp2": p.get("quantita_tp2", 0),
                "quantita_tp3": p.get("quantita_tp3", 0),
                "sl_order_id": p.get("sl_order_id"),
                "tp_order_ids": p.get("tp_order_ids", []),
            }
            for pair, p in dati.get("posizioni_aperte", {}).items()
        }
        statistiche = dati.get("statistiche", statistiche_default)
        statistiche.setdefault("perdite_consecutive", 0)
        statistiche.setdefault("pausa_fino", None)
        statistiche.setdefault("per_coppia", {})
        statistiche.setdefault("pareggi", 0)
        statistiche.setdefault("pnl_totale_usd", 0.0)

        config_salvato = dati.get("config") or {}
        CONFIG["rischio_percento"] = config_salvato.get("rischio_percento", RISCHIO_PER_TRADE_PERCENTO_DEFAULT)
        CONFIG["pausa_manuale"] = config_salvato.get("pausa_manuale", False)
        CONFIG["modalita"] = config_salvato.get("modalita", CONFIG["modalita"])
        storia_segnali = dati.get("storia_segnali", [])

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
        return ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche, ultimo_invio_report, storia_segnali
    except Exception as e:
        logger.warning(f"Impossibile leggere lo stato salvato ({e}), parto da zero.")
        return default


# ---------------------------------------------------------------------------
# DATI DI MERCATO (Bitget API pubblica)
# ---------------------------------------------------------------------------

GRANULARITA_BITGET = {1: "1m", 15: "15m", 60: "1H", 240: "4H"}


def get_ohlc(pair: str, interval: int, count: int = 250) -> pd.DataFrame:
    simbolo = pair_kraken_a_bitget(pair)
    granularita = GRANULARITA_BITGET.get(interval)
    if granularita is None:
        raise ValueError(f"Intervallo {interval} non mappato su una granularita' Bitget")

    resp = requests.get(
        f"{BITGET_BASE_URL}/api/v2/mix/market/candles",
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

    df = pd.DataFrame(righe).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="ms")
    df["vwap"] = df["close"]
    df["count"] = 0

    df = df.sort_values("time").reset_index(drop=True)
    return df.tail(count).reset_index(drop=True)


def get_ultimo_prezzo(pair: str) -> float:
    df = get_ohlc(pair, interval=1, count=1)
    return float(df.iloc[-1]["close"])


MAPPA_TICKER_BITGET = {"XBT": "BTC"}


def pair_kraken_a_bitget(pair: str) -> str:
    """Converte una coppia Kraken (es. XBTUSD) nel simbolo perpetuo Bitget (es. BTCUSDT)."""
    base = pair[:-3] if pair.endswith("USD") else pair
    base = MAPPA_TICKER_BITGET.get(base, base)
    return f"{base}USDT"


def get_prezzo_bitget(pair: str) -> float:
    simbolo = pair_kraken_a_bitget(pair)
    resp = requests.get(
        f"{BITGET_BASE_URL}/api/v2/mix/market/ticker",
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
    try:
        return get_prezzo_bitget(pair)
    except Exception as e:
        logger.info(f"{pair}: ticker Bitget non disponibile ({e}), uso l'ultima candela 1m come riferimento.")
        return get_ultimo_prezzo(pair)


_cache_decimali_bitget = {}
_cache_contratto = {}  # simbolo Bitget -> {"volume_place":.., "min_trade_num":..}


def get_decimali_bitget(pair: str) -> int:
    """Interroga l'API pubblica Bitget per sapere quanti decimali di PREZZO accetta
    per quella coppia. Ritorna None se non disponibile."""
    info = _get_contratto_raw(pair)
    if info is None:
        return None
    try:
        return int(info.get("pricePlace", info.get("priceScale")))
    except (TypeError, ValueError):
        return None


def get_info_contratto(pair: str) -> dict:
    """Decimali di QUANTITA' (volumePlace) e size minima ordinabile (minTradeNum) per la
    coppia, necessari per calcolare correttamente la size di un ordine reale. Se Bitget
    non risponde, ritorna una stima prudente (4 decimali, size minima 0.001)."""
    info = _get_contratto_raw(pair)
    if info is None:
        return {"volume_place": 4, "min_trade_num": 0.001}
    try:
        return {
            "volume_place": int(info.get("volumePlace", 4)),
            "min_trade_num": float(info.get("minTradeNum", 0.001)),
        }
    except (TypeError, ValueError):
        return {"volume_place": 4, "min_trade_num": 0.001}


def _get_contratto_raw(pair: str) -> dict | None:
    simbolo = pair_kraken_a_bitget(pair)
    if simbolo in _cache_contratto:
        return _cache_contratto[simbolo]
    try:
        resp = requests.get(
            f"{BITGET_BASE_URL}/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES", "symbol": simbolo},
            timeout=10,
        )
        resp.raise_for_status()
        dati = resp.json()
        righe = dati.get("data") or []
        if not righe:
            raise ValueError("nessun contratto trovato")
        riga = righe[0]
        _cache_contratto[simbolo] = riga
        return riga
    except Exception as e:
        logger.info(f"{pair}: info contratto Bitget non disponibile ({e}), uso una stima.")
        return None


# ---------------------------------------------------------------------------
# TRADING AUTOMATICO SU BITGET (API privata, autenticata)
# ---------------------------------------------------------------------------
# Tutte le funzioni qui sotto chiamano endpoint privati (richiedono API key con
# permesso di trading). In Demo Trading (BITGET_DEMO=true, default) operano su
# saldo virtuale grazie all'header PAPTRADING:1 - nessun fondo reale e' coinvolto.

def _bitget_timestamp() -> str:
    return str(int(time.time() * 1000))


def _bitget_sign(timestamp: str, method: str, request_path: str, query_string: str, body: str, secret: str) -> str:
    prehash = timestamp + method.upper() + request_path
    if query_string:
        prehash += "?" + query_string
    prehash += body or ""
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


# Bitget ha due schemi diversi e non documentati in modo coerente per il Demo Trading:
#   A) header PAPTRADING:1 + nomi normali (USDT-FUTURES, marginCoin USDT)
#   B) nessun header speciale, ma productType/marginCoin con prefisso "S" (SUSDT-FUTURES, SUSDT)
# Quale dei due sia attivo dipende da come l'account demo e' stato provisionato lato Bitget,
# e non e' verificabile in anticipo. _SCHEMA_DEMO memorizza quale funziona DAVVERO per
# questo account, una volta scoperto al primo tentativo riuscito, cosi' le chiamate
# successive vanno dirette senza sprecare un round-trip a vuoto ogni volta.
_SCHEMA_DEMO = {"alternativo": None}  # None = non ancora scoperto, True/False = noto


def _traduci_per_schema_alternativo(d: dict) -> dict:
    if not d:
        return d
    d2 = dict(d)
    if d2.get("productType") == "USDT-FUTURES":
        d2["productType"] = "SUSDT-FUTURES"
    if d2.get("marginCoin") == "USDT":
        d2["marginCoin"] = "SUSDT"
    simbolo = d2.get("symbol")
    if simbolo and simbolo.endswith("USDT") and not simbolo.startswith("S"):
        base = simbolo[:-4]
        d2["symbol"] = f"S{base}SUSDT"
    return d2


def _esegui_chiamata_bitget(method: str, path: str, params: dict, body: dict,
                             api_key: str, api_secret: str, passphrase: str, con_header_demo: bool):
    timestamp = _bitget_timestamp()
    query_string = ""
    url = BITGET_BASE_URL + path
    if params:
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + query_string
    body_str = json.dumps(body) if body else ""

    sign = _bitget_sign(timestamp, method, path, query_string, body_str, api_secret)
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "locale": "it-IT",
    }
    if con_header_demo:
        headers["PAPTRADING"] = "1"

    resp = requests.request(method, url, headers=headers, data=body_str if body else None, timeout=15)

    # Leggiamo SEMPRE il corpo della risposta prima di sollevare eccezioni: se lo status
    # HTTP e' un errore (es. 400), resp.raise_for_status() da solo perderebbe il messaggio
    # specifico che Bitget scrive nel JSON (es. "parametro non valido", codice 40099, ecc.)
    # mostrando solo un generico "400 Bad Request".
    try:
        dati_grezzi = resp.json()
    except ValueError:
        dati_grezzi = None

    return resp, dati_grezzi


def bitget_request(method: str, path: str, params: dict = None, body: dict = None):
    """Chiamata autenticata generica verso l'API privata Bitget. Usa le credenziali della
    modalita' correntemente attiva (demo o live, vedi CONFIG['modalita']). In demo, se lo
    schema 'A' (header PAPTRADING + nomi normali) viene rifiutato con l'errore 40099
    ('exchange environment is incorrect'), riprova automaticamente con lo schema 'B'
    (nomi con prefisso S, nessun header) - vedi nota sopra _SCHEMA_DEMO. Solleva
    un'eccezione se mancano le credenziali o se entrambi gli schemi falliscono."""
    api_key, api_secret, passphrase = credenziali_bitget_correnti()
    if not (api_key and api_secret and passphrase):
        modalita = CONFIG["modalita"]
        raise ValueError(f"Credenziali Bitget per la modalita' '{modalita}' non configurate")

    def _valida(resp, dati_grezzi):
        if not resp.ok:
            if dati_grezzi:
                raise ValueError(
                    f"Errore Bitget API ({path}): HTTP {resp.status_code} - "
                    f"codice={dati_grezzi.get('code')} msg={dati_grezzi.get('msg')}"
                )
            resp.raise_for_status()
        if dati_grezzi and dati_grezzi.get("code") != "00000":
            raise ValueError(f"Errore Bitget API ({path}): codice={dati_grezzi.get('code')} msg={dati_grezzi.get('msg')}")
        return (dati_grezzi or {}).get("data")

    if not bitget_demo_attivo():
        resp, dati_grezzi = _esegui_chiamata_bitget(method, path, params, body, api_key, api_secret, passphrase, con_header_demo=False)
        return _valida(resp, dati_grezzi)

    # --- Modalita' demo: proviamo lo schema gia' noto per primo, se lo abbiamo scoperto ---
    if _SCHEMA_DEMO["alternativo"] is True:
        resp, dati_grezzi = _esegui_chiamata_bitget(
            method, path, _traduci_per_schema_alternativo(params), _traduci_per_schema_alternativo(body),
            api_key, api_secret, passphrase, con_header_demo=False,
        )
        return _valida(resp, dati_grezzi)

    resp, dati_grezzi = _esegui_chiamata_bitget(method, path, params, body, api_key, api_secret, passphrase, con_header_demo=True)
    e_40099 = dati_grezzi and str(dati_grezzi.get("code")) == "40099"
    if resp.ok and not e_40099:
        _SCHEMA_DEMO["alternativo"] = False
        return _valida(resp, dati_grezzi)
    if not resp.ok and not e_40099:
        return _valida(resp, dati_grezzi)  # errore diverso, non ha senso ritentare con l'altro schema

    logger.info(f"{path}: schema demo A rifiutato (40099), provo lo schema alternativo (nomi con prefisso S)")
    resp2, dati_grezzi2 = _esegui_chiamata_bitget(
        method, path, _traduci_per_schema_alternativo(params), _traduci_per_schema_alternativo(body),
        api_key, api_secret, passphrase, con_header_demo=False,
    )
    if resp2.ok and not (dati_grezzi2 and str(dati_grezzi2.get("code")) == "40099"):
        _SCHEMA_DEMO["alternativo"] = True
        logger.info(f"{path}: schema demo alternativo funzionante - lo uso da ora in poi per questo account")
    return _valida(resp2, dati_grezzi2)


def get_saldo_disponibile_usdt() -> float:
    """Saldo USDT disponibile (non impegnato in margine) sul conto futures - reale se
    BITGET_DEMO=false, virtuale (Demo Trading) altrimenti. Se questo account demo usa lo
    schema alternativo di Bitget, il saldo virtuale torna con marginCoin 'SUSDT' invece
    di 'USDT' - riconosciamo entrambi."""
    dati = bitget_request("GET", "/api/v2/mix/account/accounts", params={"productType": "USDT-FUTURES"})
    for conto in dati or []:
        if conto.get("marginCoin") in ("USDT", "SUSDT"):
            logger.info(f"Dati account Bitget grezzi: {conto}")
            return float(conto.get("available", 0))
    raise ValueError("Conto USDT non trovato tra gli account futures")


def imposta_leva(pair: str, leva: int, direzione: "Direzione"):
    """Imposta la leva per il simbolo. Due dettagli non ovvi dell'API Bitget che hanno
    causato bug reali in produzione:
    1. La leva e' tenuta separata per lato long/short (holdSide) - senza specificarlo,
       la chiamata puo' aggiornare il lato sbagliato.
    2. La leva e' tenuta separata ANCHE per modalita' margine (cross vs isolated) - un
       account puo' avere leve diverse impostate per le due modalita' sullo stesso
       simbolo. I nostri ordini usano sempre marginMode='isolated', quindi impostiamo
       ESPLICITAMENTE la modalita' isolata prima di impostare la leva, cosi' siamo
       sicuri di toccare la leva che verra' davvero usata dall'ordine.
    """
    symbol = pair_kraken_a_bitget(pair)
    hold_side = "long" if direzione == Direzione.LONG else "short"

    try:
        bitget_request("POST", "/api/v2/mix/account/set-margin-mode", body={
            "symbol": symbol, "productType": "USDT-FUTURES", "marginCoin": "USDT",
            "marginMode": "isolated",
        })
    except Exception as e:
        # Se il simbolo ha gia' una posizione aperta, Bitget puo' rifiutare il cambio di
        # modalita' margine - logghiamo ma non blocchiamo, il set-leverage sotto potrebbe
        # comunque funzionare se la modalita' era gia' isolata.
        logger.info(f"{pair}: set-margin-mode non applicato ({e}) - probabile modalita' gia' corretta")

    risposta = bitget_request("POST", "/api/v2/mix/account/set-leverage", body={
        "symbol": symbol, "productType": "USDT-FUTURES", "marginCoin": "USDT",
        "leverage": str(leva), "holdSide": hold_side,
    })
    logger.info(f"{pair}: risposta set-leverage (holdSide={hold_side}, richiesta={leva}x): {risposta}")


VALORE_MINIMO_ORDINE_USDT = 5.0  # Bitget richiede un valore nozionale minimo per ordine
# (osservato: errore 45110 "less than the minimum amount 5 USDT"), indipendentemente
# dalla quantita' minima ordinabile (min_trade_num). Aggiungiamo un piccolo margine
# per non finire esattamente al limite a causa di micro-oscillazioni di prezzo tra il
# calcolo e l'invio dell'ordine.


def calcola_size_ordine(pair: str, notional_usdt: float, prezzo: float) -> float:
    """Converte un valore nozionale in USDT nella quantita' dell'asset da ordinare,
    rispettando i decimali, la size minima ordinabile e il valore minimo in USDT
    richiesti da Bitget per quella coppia."""
    info = get_info_contratto(pair)
    qty = notional_usdt / prezzo
    qty = round(qty, info["volume_place"])
    if qty < info["min_trade_num"]:
        qty = info["min_trade_num"]

    valore_ordine = qty * prezzo
    if valore_ordine < VALORE_MINIMO_ORDINE_USDT:
        qty_minima_usdt = (VALORE_MINIMO_ORDINE_USDT * 1.05) / prezzo  # 5% di margine di sicurezza
        decimali = info["volume_place"]
        fattore = 10 ** decimali
        qty = math.ceil(qty_minima_usdt * fattore) / fattore  # arrotonda per eccesso, mai per difetto
    return qty


def apri_posizione_bitget(segnale: Segnale, quantita: float) -> dict:
    """Apre la posizione a mercato su Bitget. Ritorna i dati dell'ordine (incl. orderId).
    NOTA: 'tradeSide' NON va incluso in modalita' one-way (posMode one_way_mode) - la
    documentazione Bitget dice esplicitamente 'tradeSide: ignore' per questa modalita', e
    includerlo comunque causa l'errore 40774 'The order type for unilateral position must
    also be the unilateral position type'. In one-way mode basta 'side' (buy/sell)."""
    symbol = pair_kraken_a_bitget(segnale.coppia)
    side = "buy" if segnale.direzione == Direzione.LONG else "sell"
    body = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "marginMode": "isolated",
        "marginCoin": "USDT",
        "size": str(quantita),
        "side": side,
        "orderType": "market",
        "clientOid": f"segnaliai-{uuid.uuid4().hex[:16]}",
    }
    return bitget_request("POST", "/api/v2/mix/order/place-order", body=body)


def piazza_ordine_piano(pair: str, direzione: Direzione, trigger_price: float, size: float) -> dict:
    """Piazza un ordine 'piano' (trigger) reduce-only che chiude (parte del)la posizione
    al tocco di trigger_price - usato sia per lo Stop Loss (size piena) sia per i tre
    Take Profit parziali (size frazionata). NOTA: niente 'tradeSide' qui per lo stesso
    motivo di apri_posizione_bitget - in one-way mode l'intento di chiusura e' espresso
    solo tramite 'reduceOnly': 'YES', non tramite tradeSide."""
    symbol = pair_kraken_a_bitget(pair)
    side_chiusura = "sell" if direzione == Direzione.LONG else "buy"
    body = {
        "planType": "normal_plan",
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "marginMode": "isolated",
        "marginCoin": "USDT",
        "size": str(size),
        "side": side_chiusura,
        "orderType": "market",
        "triggerPrice": str(trigger_price),
        "triggerType": "mark_price",
        "reduceOnly": "YES",
        "clientOid": f"segnaliai-{uuid.uuid4().hex[:16]}",
    }
    return bitget_request("POST", "/api/v2/mix/order/place-plan-order", body=body)


def cancella_ordine_piano(pair: str, order_id: str):
    if not order_id:
        return
    symbol = pair_kraken_a_bitget(pair)
    try:
        bitget_request("POST", "/api/v2/mix/order/cancel-plan-order", body={
            "symbol": symbol, "productType": "USDT-FUTURES", "marginCoin": "USDT",
            "orderId": order_id,
        })
    except Exception as e:
        # Puo' fallire semplicemente perche' l'ordine e' gia' stato eseguito o cancellato -
        # non deve mai bloccare il resto della gestione della posizione.
        logger.info(f"{pair}: impossibile cancellare l'ordine piano {order_id} (probabilmente gia' concluso): {e}")


def get_size_posizione_aperta(pair: str) -> float:
    """Quantita' attualmente aperta su Bitget per questa coppia (0 se la posizione e'
    completamente chiusa)."""
    symbol = pair_kraken_a_bitget(pair)
    try:
        dati = bitget_request("GET", "/api/v2/mix/position/single-position", params={
            "symbol": symbol, "productType": "USDT-FUTURES", "marginCoin": "USDT",
        })
    except Exception as e:
        logger.warning(f"{pair}: impossibile leggere la posizione aperta su Bitget: {e}")
        raise
    if not dati:
        return 0.0
    riga = dati[0] if isinstance(dati, list) else dati
    valore = riga.get("total") or riga.get("available") or 0
    return float(valore)


def spiega_errore_bitget(e: Exception) -> str:
    """Traduce un'eccezione di bitget_request in un messaggio comprensibile. In
    particolare riconosce il codice 40099 ('exchange environment is incorrect'), un
    bug documentato lato Bitget che colpisce alcuni endpoint di sola lettura (incl.
    la lista posizioni) quando si usa l'header di Demo Trading - non e' causato dal
    nostro codice e non risulta impattare gli endpoint di trading vero e proprio
    (apertura posizioni, ordini SL/TP), solo la lettura per mostrare i dati."""
    testo = str(e)
    if "40099" in testo:
        return (
            "Bitget ha un bug noto e documentato (codice 40099, \"exchange environment "
            "is incorrect\") su alcuni endpoint di sola lettura in Demo Trading - non è "
            "causato dal nostro codice. Non risulta impattare l'apertura delle posizioni "
            "né la gestione di SL/TP, solo la loro visualizzazione qui. In LIVE questo "
            "endpoint funziona normalmente."
        )
    return f"`{testo}`"


def get_tutte_posizioni_aperte() -> list:
    """Tutte le posizioni realmente aperte su Bitget in questo momento (USDT-FUTURES),
    lette direttamente dall'exchange - fonte di verita' per la riconciliazione, non la
    memoria interna del bot. NOTA: marginCoin NON va passato qui - Bitget risponde 400
    Bad Request su questo endpoint specifico se incluso (a differenza di altri endpoint
    dove e' richiesto), a differenza di quanto suggerito da alcune SDK di terze parti."""
    dati = bitget_request("GET", "/api/v2/mix/position/all-position", params={
        "productType": "USDT-FUTURES",
    })
    return dati or []


def get_ordini_piano_pendenti(pair: str = None) -> list:
    """Ordini piano (Stop Loss / Take Profit) ancora in attesa di trigger su Bitget.
    Se pair e' specificata, filtra su quella coppia; altrimenti li ritorna tutti."""
    params = {"productType": "USDT-FUTURES", "planType": "normal_plan"}
    if pair:
        params["symbol"] = pair_kraken_a_bitget(pair)
    dati = bitget_request("GET", "/api/v2/mix/order/orders-plan-pending", params=params)
    if isinstance(dati, dict):
        return dati.get("entrustedList") or []
    return dati or []


async def verifica_coerenza_posizioni(bot: Bot, posizioni_aperte: dict):
    """Confronta cio' che il bot pensa di avere aperto (posizioni_aperte, tracciato
    internamente) con cio' che Bitget mostra DAVVERO in questo momento. Se una posizione
    tracciata dal bot non esiste sull'exchange (o viceversa), manda un avviso privato
    immediato invece di lasciare che la discrepanza passi inosservata."""
    if not TRADING_ABILITATO or not posizioni_aperte:
        return
    try:
        posizioni_reali = get_tutte_posizioni_aperte()
    except Exception as e:
        logger.warning(f"Impossibile verificare la coerenza delle posizioni con Bitget: {e}")
        return

    simboli_reali = {}
    for p in posizioni_reali:
        symbol = p.get("symbol")
        size = float(p.get("total") or p.get("available") or 0)
        if symbol and size > 0:
            simboli_reali[symbol] = size

    for pair, pos in posizioni_aperte.items():
        symbol = pair_kraken_a_bitget(pair)
        size_attesa = pos.get("quantita_rimanente", pos.get("quantita_totale", 0))
        size_reale = simboli_reali.get(symbol, 0)

        if size_reale <= 0:
            await notifica_admin(
                bot,
                f"🚨 *Incoerenza rilevata* — il bot traccia una posizione aperta su `{pair}` "
                f"(size attesa ~{size_attesa}) ma Bitget non mostra nessuna posizione aperta "
                f"su questa coppia. Controllare manualmente: potrebbe essere gia' stata chiusa "
                f"senza che il bot se ne accorgesse, o l'apertura potrebbe non essere mai andata "
                f"a buon fine."
            )
        elif abs(size_reale - size_attesa) > size_attesa * 0.1 and size_attesa > 0:
            await notifica_admin(
                bot,
                f"⚠️ *Scostamento rilevato* su `{pair}` — il bot si aspetta una size residua di "
                f"~{size_attesa} ma su Bitget la posizione reale e' {size_reale}. Controllare "
                f"manualmente se serve un riallineamento."
            )


async def esegui_apertura_trade(bot: Bot, segnale: Segnale) -> dict | None:
    """Esegue l'intera sequenza di apertura reale su Bitget: calcola la size dal saldo
    reale, imposta la leva, apre a mercato, piazza lo Stop Loss e i 3 Take Profit
    parziali come ordini piano sull'exchange. Ritorna i dettagli della posizione aperta
    da salvare in posizioni_aperte, oppure None se l'esecuzione fallisce (in tal caso
    NON si deve pubblicare il segnale come "aperto" - solo come informativo)."""
    try:
        saldo = get_saldo_disponibile_usdt()
    except Exception as e:
        logger.error(f"{segnale.coppia}: impossibile leggere il saldo Bitget, trade NON eseguito: {e}")
        await notifica_admin(bot, f"⚠️ Trade su `{segnale.coppia}` saltato: impossibile leggere il saldo Bitget.\n`{e}`")
        return None

    margine_usdt = saldo * (CONFIG["rischio_percento"] / 100)
    notional_usdt = margine_usdt * segnale.leva_suggerita
    quantita_totale = calcola_size_ordine(segnale.coppia, notional_usdt, segnale.entry)
    info_contratto = get_info_contratto(segnale.coppia)

    valore_reale_ordine = quantita_totale * segnale.entry
    if valore_reale_ordine > notional_usdt * 1.15:  # forzato oltre il 15% in piu' del previsto
        logger.warning(
            f"{segnale.coppia}: size forzata al minimo ordinabile su Bitget "
            f"({valore_reale_ordine:.2f} USDT) - supera il nozionale prevsto dal rischio "
            f"{CONFIG['rischio_percento']}% ({notional_usdt:.2f} USDT). Il rischio reale su "
            f"questo trade e' quindi piu' alto di quanto impostato."
        )
        await notifica_admin(
            bot,
            f"ℹ️ Su `{segnale.coppia}` la size e' stata forzata al minimo richiesto da Bitget "
            f"({valore_reale_ordine:.2f} USDT invece di {notional_usdt:.2f} USDT previsti dal "
            f"{CONFIG['rischio_percento']}% di rischio) - con questo saldo, il rischio reale su "
            f"questo trade e' piu' alto del solito."
        )

    logger.info(
        f"{segnale.coppia}: diagnostica apertura - saldo={saldo:.4f} USDT, "
        f"margine={margine_usdt:.4f} USDT, leva={segnale.leva_suggerita}x, "
        f"notional={notional_usdt:.4f} USDT, entry={segnale.entry}, "
        f"quantita_calcolata={quantita_totale}, min_trade_num={info_contratto['min_trade_num']}, "
        f"volume_place={info_contratto['volume_place']}"
    )

    # Controllo finale: il margine REALMENTE richiesto dalla quantita' finale (dopo
    # l'eventuale forzatura al minimo Bitget) deve stare dentro al saldo LIBERO attuale -
    # non basta che ci fosse margine sufficiente al momento del calcolo iniziale, perche'
    # altre posizioni gia' aperte possono aver bloccato margine nel frattempo. Senza
    # questo controllo, Bitget rifiuta l'ordine con "exceeds the balance" (codice 40762)
    # dopo aver gia' impostato la leva - scartiamo PRIMA di arrivare a quel punto.
    margine_reale_richiesto = (quantita_totale * segnale.entry) / segnale.leva_suggerita
    if margine_reale_richiesto > saldo * 0.95:  # 5% di margine di sicurezza sul saldo libero
        logger.warning(
            f"{segnale.coppia}: margine reale richiesto ({margine_reale_richiesto:.4f} USDT) "
            f"troppo vicino o superiore al saldo libero ({saldo:.4f} USDT) - probabilmente "
            f"altre posizioni aperte stanno gia' impegnando parte del capitale. Trade saltato."
        )
        await notifica_admin(
            bot,
            f"⚠️ Trade su `{segnale.coppia}` saltato: margine richiesto "
            f"{margine_reale_richiesto:.2f} USDT ma saldo libero solo {saldo:.2f} USDT "
            f"(probabilmente altre posizioni aperte occupano gia' il capitale)."
        )
        return None

    if quantita_totale <= 0 or margine_usdt <= 0:
        logger.warning(
            f"{segnale.coppia}: saldo insufficiente per aprire una posizione minima, trade saltato. "
            f"(saldo={saldo:.4f}, margine={margine_usdt:.4f}, quantita={quantita_totale}, "
            f"min_trade_num={info_contratto['min_trade_num']})"
        )
        await notifica_admin(
            bot,
            f"⚠️ Trade su `{segnale.coppia}` saltato: saldo insufficiente (disponibile {saldo:.2f} USDT).\n"
            f"Dettaglio: margine {margine_usdt:.4f} USDT, quantita' calcolata {quantita_totale}, "
            f"minimo ordinabile {info_contratto['min_trade_num']}."
        )
        return None

    try:
        imposta_leva(segnale.coppia, segnale.leva_suggerita, segnale.direzione)
        apri_posizione_bitget(segnale, quantita_totale)
    except Exception as e:
        logger.error(f"{segnale.coppia}: apertura posizione su Bitget fallita: {e}")
        await notifica_admin(bot, f"⚠️ Apertura posizione `{segnale.coppia}` fallita su Bitget:\n`{e}`")
        return None

    # Verifica di sicurezza: rileggiamo la leva REALMENTE applicata sulla posizione appena
    # aperta e avvisiamo se non corrisponde a quella richiesta - una discrepanza qui
    # significa rischio reale diverso da quello mostrato nel segnale.
    try:
        symbol_verifica = pair_kraken_a_bitget(segnale.coppia)
        dati_posizione = bitget_request("GET", "/api/v2/mix/position/single-position", params={
            "symbol": symbol_verifica, "productType": "USDT-FUTURES", "marginCoin": "USDT",
        })
        riga_posizione = (dati_posizione[0] if isinstance(dati_posizione, list) else dati_posizione) if dati_posizione else {}
        leva_reale = riga_posizione.get("leverage")
        if leva_reale is not None and int(float(leva_reale)) != segnale.leva_suggerita:
            logger.warning(
                f"{segnale.coppia}: leva richiesta {segnale.leva_suggerita}x ma su Bitget "
                f"risulta {leva_reale}x - il rischio reale su questo trade e' diverso da "
                f"quello comunicato nel segnale."
            )
            await notifica_admin(
                bot,
                f"🚨 *Leva non corrispondente* su `{segnale.coppia}` — richiesta "
                f"{segnale.leva_suggerita}x, su Bitget risulta {leva_reale}x. Il rischio "
                f"reale su questo trade e' diverso da quello comunicato. Controllare "
                f"manualmente su Bitget."
            )
    except Exception as e:
        logger.info(f"{segnale.coppia}: impossibile verificare la leva reale dopo l'apertura: {e}")

    # Frazionamento della size sui Take Profit. IMPORTANTE: con capitale piccolo, dividere
    # sempre in 3 parti uguali puo' lasciare ogni singola porzione SOTTO il valore minimo
    # ordine di Bitget (5 USDT) - in quel caso l'ordine TP fallirebbe silenziosamente e il
    # prezzo potrebbe superare quel livello senza che nulla scatti (successo osservato in
    # produzione). Calcoliamo quindi quanti livelli di TP sono realmente sostenibili con
    # la size disponibile, e usiamo meno livelli invece di tentare ordini destinati a fallire.
    info = get_info_contratto(segnale.coppia)
    volume_place = info["volume_place"]

    valore_totale_tp = quantita_totale * segnale.entry
    if valore_totale_tp / 3 >= VALORE_MINIMO_ORDINE_USDT * 1.05:
        quantita_tp1 = round(quantita_totale / 3, volume_place)
        quantita_tp2 = round(quantita_totale / 3, volume_place)
        quantita_tp3 = round(quantita_totale - quantita_tp1 - quantita_tp2, volume_place)
    elif valore_totale_tp / 2 >= VALORE_MINIMO_ORDINE_USDT * 1.05:
        quantita_tp1 = 0.0
        quantita_tp2 = round(quantita_totale / 2, volume_place)
        quantita_tp3 = round(quantita_totale - quantita_tp2, volume_place)
        logger.info(f"{segnale.coppia}: capitale insufficiente per 3 livelli di TP, uso solo TP2+TP3 (salto TP1)")
        await notifica_admin(bot, f"ℹ️ Su `{segnale.coppia}` uso solo 2 livelli di TP (non 3) - capitale troppo piccolo per dividere in 3 parti sopra il minimo Bitget.")
    else:
        quantita_tp1 = 0.0
        quantita_tp2 = 0.0
        quantita_tp3 = quantita_totale
        logger.info(f"{segnale.coppia}: capitale insufficiente per TP parziali, uso solo TP3 (target unico)")
        await notifica_admin(bot, f"ℹ️ Su `{segnale.coppia}` uso un solo Take Profit finale (non 3 livelli) - capitale troppo piccolo per dividere.")

    # Controllo post-arrotondamento: su asset trattati solo in numeri interi (volume_place=0,
    # es. LINK), meta' di una quantita' piccola puo' arrotondarsi esattamente a ZERO anche se
    # il controllo sul valore in USDT sopra sembrava viable - la quantita' andrebbe persa in
    # silenzio invece di confluire nel livello successivo. La fondiamo qui in cascata.
    if quantita_tp1 > 0 and round(quantita_tp1, volume_place) <= 0:
        quantita_tp2 += quantita_tp1
        quantita_tp1 = 0.0
    if quantita_tp2 > 0 and round(quantita_tp2, volume_place) <= 0:
        quantita_tp3 += quantita_tp2
        quantita_tp2 = 0.0
    quantita_tp1 = round(quantita_tp1, volume_place)
    quantita_tp2 = round(quantita_tp2, volume_place)
    quantita_tp3 = round(quantita_totale - quantita_tp1 - quantita_tp2, volume_place)

    sl_order_id = None
    tp_order_ids = []
    try:
        risposta_sl = piazza_ordine_piano(segnale.coppia, segnale.direzione, segnale.stop_loss, quantita_totale)
        sl_order_id = (risposta_sl or {}).get("orderId")
    except Exception as e:
        logger.error(f"{segnale.coppia}: posizione APERTA ma piazzamento Stop Loss fallito: {e}")
        await notifica_admin(
            bot,
            f"🚨 *ATTENZIONE* — posizione `{segnale.coppia}` aperta su Bitget MA lo Stop Loss "
            f"non e' stato piazzato correttamente. Controllare manualmente su Bitget.\n`{e}`"
        )

    for nome_tp, tp_price, tp_size in (("TP1", segnale.tp1, quantita_tp1), ("TP2", segnale.tp2, quantita_tp2), ("TP3", segnale.take_profit, quantita_tp3)):
        if tp_size <= 0:
            continue
        try:
            risposta_tp = piazza_ordine_piano(segnale.coppia, segnale.direzione, tp_price, tp_size)
            tp_order_ids.append((risposta_tp or {}).get("orderId"))
        except Exception as e:
            logger.warning(f"{segnale.coppia}: piazzamento {nome_tp} fallito ({tp_price}): {e}")
            await notifica_admin(
                bot,
                f"🚨 *ATTENZIONE* — posizione `{segnale.coppia}` aperta su Bitget MA {nome_tp} "
                f"non e' stato piazzato correttamente. Se il prezzo raggiunge quel livello, "
                f"NON si chiudera' nulla li'. Controllare manualmente su Bitget.\n`{e}`"
            )

    logger.info(
        f"{segnale.coppia}: trade eseguito su Bitget - size {quantita_totale}, leva {segnale.leva_suggerita}x, "
        f"margine ~{margine_usdt:.2f} USDT (demo={bitget_demo_attivo()})"
    )

    return {
        "quantita_totale": quantita_totale,
        "quantita_rimanente": quantita_totale,
        "quantita_tp1": quantita_tp1,
        "quantita_tp2": quantita_tp2,
        "quantita_tp3": quantita_tp3,
        "sl_order_id": sl_order_id,
        "tp_order_ids": tp_order_ids,
    }


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
    try:
        df_4h = get_ohlc(pair, interval=240, count=250)
        df_1h = get_ohlc(pair, interval=60, count=250)
    except Exception as e:
        logger.warning(f"Dati non disponibili per {pair}: {e}")
        return None, -1, ""

    if len(df_4h) < 200 or len(df_1h) < 50:
        return None, -1, ""

    df_4h["ema50"] = ema(df_4h["close"], 50)
    df_4h["ema200"] = ema(df_4h["close"], 200)
    ultimo_4h = df_4h.iloc[-1]

    if ultimo_4h["ema50"] > ultimo_4h["ema200"]:
        bias = Direzione.LONG
    else:
        bias = Direzione.SHORT

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

    score += 30
    motivi.append(f"Trend {bias.value.lower()} su 4h (EMA50/EMA200)")

    if bias == Direzione.LONG and precedente["rsi"] < 35 and ultimo["rsi"] >= 35:
        score += 25
        motivi.append("RSI in uscita da ipervenduto")
    elif bias == Direzione.SHORT and precedente["rsi"] > 65 and ultimo["rsi"] <= 65:
        score += 25
        motivi.append("RSI in uscita da ipercomprato")

    cross_up = precedente["macd"] < precedente["macd_signal"] and ultimo["macd"] >= ultimo["macd_signal"]
    cross_down = precedente["macd"] > precedente["macd_signal"] and ultimo["macd"] <= ultimo["macd_signal"]
    if bias == Direzione.LONG and cross_up:
        score += 25
        motivi.append("MACD cross rialzista")
    elif bias == Direzione.SHORT and cross_down:
        score += 25
        motivi.append("MACD cross ribassista")

    if pd.notna(ultimo["volume_media"]) and ultimo["volume"] > ultimo["volume_media"] * 1.3:
        score += 20
        motivi.append("Volume sopra media (spike)")

    if score < SCORE_MINIMO_PUBBLICAZIONE:
        logger.info(f"{pair}: score {score}/100 ({bias.value}) - sotto soglia, scartato")
        return None, score, bias.value

    logger.info(f"{pair}: score {score}/100 ({bias.value}) - SEGNALE VALIDO")

    try:
        df_15m = get_ohlc(pair, interval=15, count=50)
        df_15m["ema9"] = ema(df_15m["close"], 9)
        df_15m["ema21"] = ema(df_15m["close"], 21)
        ultimo_15m = df_15m.iloc[-1]
        if pd.isna(ultimo_15m["ema9"]) or pd.isna(ultimo_15m["ema21"]):
            conferma_15m = True
        else:
            conferma_15m = (
                (bias == Direzione.LONG and ultimo_15m["ema9"] > ultimo_15m["ema21"]) or
                (bias == Direzione.SHORT and ultimo_15m["ema9"] < ultimo_15m["ema21"])
            )
    except Exception as e:
        logger.warning(f"{pair}: impossibile leggere 15m per conferma multi-timeframe ({e}) - procedo comunque")
        conferma_15m = True

    if not conferma_15m:
        logger.info(f"{pair}: score {score}/100 ma momentum 15m non allineato - segnale scartato")
        return None, score, bias.value

    motivi.append("Momentum 15m allineato (EMA9/EMA21)")

    entry = ultimo["close"]
    atr_val = ultimo["atr"] if pd.notna(ultimo["atr"]) else entry * 0.01

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

def formatta_messaggio(s: Segnale, eseguito: bool, non_supportata_in_demo: bool = False) -> str:
    emoji_testata = "🚀" if s.score >= 80 else "📡"
    emoji_direzione = "🟢" if s.direzione == Direzione.LONG else "🔴"
    label_direzione = "LONG (Buy)" if s.direzione == Direzione.LONG else "SHORT (Sell)"
    piene = round(s.score / 10)
    barra = "█" * piene + "░" * (10 - piene)
    segno_sl = "-" if s.direzione == Direzione.LONG else "+"
    segno_tp = "+" if s.direzione == Direzione.LONG else "-"
    if eseguito:
        riga_esecuzione = f"✅ *Posizione aperta automaticamente su Bitget* {'(Demo Trading)' if bitget_demo_attivo() else '(LIVE)'}\n\n"
    elif non_supportata_in_demo:
        riga_esecuzione = (
            "ℹ️ *Segnale informativo, non eseguibile in Demo Trading* — il Demo Trading di "
            "Bitget supporta solo BTC/ETH/EOS/XRP, questa coppia sarà tradabile in LIVE\n\n"
        )
    else:
        riga_esecuzione = "⚠️ *Segnale pubblicato ma NON eseguito automaticamente* (controllare i log)\n\n"

    return (
        f"{emoji_testata} *SALA SEGNALI VIP* {emoji_testata}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{riga_esecuzione}"
        f"{emoji_direzione} *Asset:* `{s.coppia}`\n"
        f"*Direzione:* {label_direzione}\n"
        f"*Timeframe:* {s.timeframe}\n"
        f"*Leva:* {s.leva_suggerita}x _(calcolata: rischio {CONFIG['rischio_percento']}% ÷ distanza SL {s.sl_percento}%)_\n\n"
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
        f"⚠️ _Segnale generato automaticamente da indicatori tecnici reali (Bitget). Non è consulenza finanziaria. DYOR._"
    )


async def invia_segnale(bot: Bot, segnale: Segnale, eseguito: bool, non_supportata_in_demo: bool = False) -> bool:
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatta_messaggio(segnale, eseguito, non_supportata_in_demo),
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Pubblicato: {segnale.coppia} {segnale.direzione.value} score={segnale.score} eseguito={eseguito}")
        return True
    except TelegramError as e:
        logger.error(f"Errore invio Telegram: {e}")
        return False


def _font(nome: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(ASSETS_DIR, nome), size)


def _stella(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, colore):
    """Disegna una stella a 5 punte piena, centrata in (cx, cy) con raggio esterno r."""
    punti = []
    r_interno = r * 0.382
    import math
    for i in range(10):
        angolo = math.radians(-90 + i * 36)
        raggio = r if i % 2 == 0 else r_interno
        punti.append((cx + raggio * math.cos(angolo), cy + raggio * math.sin(angolo)))
    draw.polygon(punti, fill=colore)


def _stemma(draw: ImageDraw.ImageDraw, cx: float, top_y: float, larghezza: float,
            oro: tuple, oro_chiaro: tuple, navy: tuple):
    """Stemma originale disegnato interamente a codice (nessuna immagine esterna):
    corona a tre punte sopra uno scudo con fulmine intagliato. Stile araldico coerente
    col brand 'Sala Segnali VIP', proporzionato su larghezza in pixel."""
    w = larghezza
    h_corona = w * 0.42
    h_scudo = w * 1.05

    # --- Corona: contorno a zig-zag (base + tre punte, quella centrale piu' alta) ---
    def mappa(nx, ny, x0, y0, w_box, h_box):
        return (x0 + nx * w_box, y0 + ny * h_box)

    x0 = cx - w / 2
    norm_corona = [
        (0.00, 0.65), (0.15, 0.15), (0.33, 0.45), (0.50, 0.00),
        (0.67, 0.45), (0.85, 0.15), (1.00, 0.65), (1.00, 1.00), (0.00, 1.00),
    ]
    punti_corona = [mappa(nx, ny, x0, top_y, w, h_corona) for nx, ny in norm_corona]
    draw.polygon(punti_corona, fill=oro)
    draw.line(punti_corona[:7] + [punti_corona[0]], fill=oro_chiaro, width=3, joint="curve")
    # gemme sulle punte
    for nx, ny in [(0.15, 0.15), (0.50, 0.00), (0.85, 0.15)]:
        px, py = mappa(nx, ny, x0, top_y, w, h_corona)
        r = w * 0.028
        draw.ellipse([px - r, py - r, px + r, py + r], fill=navy, outline=oro_chiaro, width=2)
    # fascia di base della corona
    fascia_y = top_y + h_corona * 0.82
    draw.line([(x0 + w * 0.05, fascia_y), (x0 + w * 0.95, fascia_y)], fill=navy, width=3)

    # --- Scudo ---
    top_scudo = top_y + h_corona - 4
    norm_scudo = [
        (0.06, 0.00), (0.94, 0.00), (1.00, 0.12), (0.94, 0.55),
        (0.50, 1.00), (0.06, 0.55), (0.00, 0.12),
    ]
    punti_scudo = [mappa(nx, ny, x0, top_scudo, w, h_scudo) for nx, ny in norm_scudo]
    draw.polygon(punti_scudo, fill=oro)
    draw.line(punti_scudo + [punti_scudo[0]], fill=oro_chiaro, width=3, joint="curve")
    # bordo interno sottile per profondita'
    inset = 10
    norm_scudo_interno = [(nx, ny) for nx, ny in norm_scudo]
    x0i, top_i, wi, hi = x0 + inset, top_scudo + inset, w - inset * 2, h_scudo - inset * 2
    punti_scudo_interni = [mappa(nx, ny, x0i, top_i, wi, hi) for nx, ny in norm_scudo_interno]
    draw.line(punti_scudo_interni + [punti_scudo_interni[0]], fill=(255, 245, 220), width=1)

    # --- Fulmine intagliato (colore navy sopra l'oro, per un effetto a due toni) ---
    norm_fulmine = [
        (0.56, 0.08), (0.28, 0.52), (0.46, 0.52),
        (0.30, 0.94), (0.72, 0.42), (0.50, 0.42),
    ]
    punti_fulmine = [mappa(nx, ny, x0, top_scudo, w, h_scudo) for nx, ny in norm_fulmine]
    draw.polygon(punti_fulmine, fill=navy)
    draw.line(punti_fulmine + [punti_fulmine[0]], fill=oro_chiaro, width=2, joint="curve")


def _cornice_doppia(draw: ImageDraw.ImageDraw, box, colore, raggio=14):
    """Cornice ornamentale a doppia linea (bordo esterno + interno sottile) per
    incorniciare il riquadro dei risultati, nello stile 'certificato' del brand."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=raggio, outline=colore, width=2)
    draw.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y1 - 6], radius=raggio - 4, outline=colore, width=1)


def genera_card_risultato(s: Segnale, esito_positivo: bool, percentuale: float, prezzo_chiusura: float) -> io.BytesIO:
    W, H = 1100, 820
    NAVY_SCURO = (6, 11, 23)
    NAVY = (11, 19, 38)
    ORO = (196, 155, 74)
    ORO_CHIARO = (240, 205, 130)
    BIANCO = (238, 240, 245)

    img = Image.new("RGB", (W, H), NAVY_SCURO)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(
            int(NAVY_SCURO[0] + (NAVY[0] - NAVY_SCURO[0]) * t),
            int(NAVY_SCURO[1] + (NAVY[1] - NAVY_SCURO[1]) * t),
            int(NAVY_SCURO[2] + (NAVY[2] - NAVY_SCURO[2]) * t),
        ))

    # Bagliore dorato soffuso dietro lo stemma (piu' livelli semi-trasparenti sovrapposti)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    gx, gy = W / 2, 175
    for r, alpha in [(230, 10), (170, 16), (110, 22), (60, 28)]:
        glow_draw.ellipse([gx - r, gy - r, gx + r, gy + r], fill=(ORO_CHIARO[0], ORO_CHIARO[1], ORO_CHIARO[2], alpha))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Logo reale (caricato da file, con fallback allo stemma disegnato se il file manca)
    try:
        logo = Image.open(os.path.join(ASSETS_DIR, "logo.png")).convert("RGBA")
        logo_h = 220
        logo_w = int(logo.width * (logo_h / logo.height))
        logo_ridim = logo.resize((logo_w, logo_h), Image.LANCZOS)
        img.paste(logo_ridim, (int(W / 2 - logo_w / 2), 40), logo_ridim)
    except Exception as e:
        logger.warning(f"Impossibile caricare logo.png per la card ({e}), uso lo stemma disegnato.")
        _stemma(draw, cx=W / 2, top_y=50, larghezza=140, oro=ORO, oro_chiaro=ORO_CHIARO, navy=NAVY_SCURO)

    # Titolo
    titolo = "SALA SEGNALI VIP"
    font_titolo = _font("font_bold.ttf", 40)
    tw = draw.textlength(titolo, font=font_titolo)
    draw.text((W / 2 - tw / 2, 275), titolo, font=font_titolo, fill=ORO_CHIARO)
    sottotitolo = ("POSIZIONE CHIUSA IN PROFITTO" if esito_positivo else "STOP LOSS COLPITO")
    font_sotto = _font("font_regular.ttf", 21)
    sw = draw.textlength(sottotitolo, font=font_sotto)
    draw.text((W / 2 - sw / 2, 325), sottotitolo, font=font_sotto, fill=(150, 160, 180))

    # Riquadro con cornice doppia: asset/direzione/leva a sinistra, esito a destra
    box = (70, 375, W - 70, 495)
    _cornice_doppia(draw, box, ORO)
    colore_dir = (60, 210, 130) if s.direzione == Direzione.LONG else (230, 80, 90)
    draw.text((100, 397), s.coppia, font=_font("font_bold.ttf", 34), fill=BIANCO)
    draw.text((100, 440), f"{s.direzione.value}  {s.leva_suggerita}x  ·  Bitget Perpetuo",
               font=_font("font_regular.ttf", 20), fill=colore_dir)

    colore_pct = (60, 210, 130) if esito_positivo else (230, 80, 90)
    segno = "+" if esito_positivo else "-"
    testo_pct = f"{segno}{abs(percentuale)}%"
    font_pct = _font("font_bold_big.ttf", 62)
    pw = draw.textlength(testo_pct, font=font_pct)
    draw.text((W - 100 - pw, 410), testo_pct, font=font_pct, fill=colore_pct)

    # Prezzi
    y2 = 530
    draw.text((70, y2), "Prezzo d'ingresso", font=_font("font_regular.ttf", 22), fill=(140, 150, 170))
    draw.text((70, y2 + 34), f"{s.entry}", font=_font("font_bold.ttf", 32), fill=BIANCO)
    draw.text((440, y2), "Prezzo di chiusura", font=_font("font_regular.ttf", 22), fill=(140, 150, 170))
    draw.text((440, y2 + 34), f"{prezzo_chiusura}", font=_font("font_bold.ttf", 32), fill=BIANCO)

    # Stelle decorative
    for i in range(5):
        _stella(draw, W / 2 - 100 + i * 50, 640, 15, ORO_CHIARO)

    # Piede pagina - dicitura corretta, nessun claim di certificazione non verificabile
    draw.line([(70, H - 90), (W - 70, H - 90)], fill=(40, 48, 65), width=2)
    riga1 = "Dati reali · Verificati dal bot"
    font_r1 = _font("font_bold.ttf", 22)
    r1w = draw.textlength(riga1, font=font_r1)
    draw.text((W / 2 - r1w / 2, H - 68), riga1, font=font_r1, fill=ORO)
    riga2 = "Segnale generato da analisi tecnica automatica — non è consulenza finanziaria. DYOR."
    font_r2 = _font("font_regular.ttf", 17)
    r2w = draw.textlength(riga2, font=font_r2)
    draw.text((W / 2 - r2w / 2, H - 38), riga2, font=font_r2, fill=(110, 120, 140))

    buffer = io.BytesIO()
    buffer.name = "risultato.png"
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


async def invia_card_risultato(bot: Bot, s: Segnale, esito_positivo: bool, percentuale: float, prezzo_chiusura: float):
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


def calcola_pnl_usd(pos: dict, prezzo_chiusura_finale: float) -> float:
    """Calcola il guadagno/perdita realizzato in USDT per una posizione, usando i prezzi
    REALI a cui ogni porzione e' stata chiusa (TP1/TP2 se raggiunti, il resto al prezzo
    di chiusura finale) pesati per le quantita' realmente ordinate su Bitget - non una
    stima sulla sola percentuale."""
    s: Segnale = pos["segnale"]
    segno = 1 if s.direzione == Direzione.LONG else -1
    q_tp1 = pos.get("quantita_tp1", 0) or 0
    q_tp2 = pos.get("quantita_tp2", 0) or 0
    q_tp3 = pos.get("quantita_tp3", 0) or 0

    pnl = 0.0
    quota_restante = q_tp1 + q_tp2 + q_tp3
    if pos.get("tp1_raggiunto"):
        pnl += (s.tp1 - s.entry) * segno * q_tp1
        quota_restante -= q_tp1
    if pos.get("tp2_raggiunto"):
        pnl += (s.tp2 - s.entry) * segno * q_tp2
        quota_restante -= q_tp2
    pnl += (prezzo_chiusura_finale - s.entry) * segno * quota_restante
    return pnl


async def controlla_posizioni_aperte(bot: Bot, posizioni_aperte: dict, statistiche: dict):
    """Controlla ogni posizione aperta leggendo la size REALE rimasta su Bitget (non un
    prezzo simulato): se e' scesa rispetto a quanto tracciato localmente, un ordine piano
    (TP1, TP2 o SL) e' stato eseguito sull'exchange. Quando TP1/TP2 scattano, lo Stop Loss
    viene cancellato e ripiazzato piu' in alto (long) / piu' in basso (short) per bloccare
    il profitto parziale. Quando la size arriva a zero, la posizione e' chiusa: l'esito
    (vinto/pareggio/vinto_parziale/perso) si deduce da quali TP erano gia' scattati."""
    if not TRADING_ABILITATO:
        return  # nessuna posizione reale da monitorare in modalita' solo-segnalazione

    chiuse = []

    for pair, pos in list(posizioni_aperte.items()):
        s: Segnale = pos["segnale"]
        try:
            size_attuale = get_size_posizione_aperta(pair)
        except Exception:
            continue  # errore gia' loggato in get_size_posizione_aperta, riproviamo al prossimo ciclo

        quantita_rimanente_attesa = pos.get("quantita_rimanente", pos.get("quantita_totale", 0))
        tolleranza = max(pos.get("quantita_tp1", 0), pos.get("quantita_tp2", 0)) * 0.3 or 1e-9

        # --- Posizione completamente chiusa ---
        if size_attuale <= SOGLIA_CHIUSURA_RESIDUA or size_attuale < get_info_contratto(pair)["min_trade_num"] * 0.5:
            # Ripuliamo eventuali ordini piano rimasti pendenti (SL o TP non scattati).
            cancella_ordine_piano(pair, pos.get("sl_order_id"))
            for oid in pos.get("tp_order_ids", []):
                cancella_ordine_piano(pair, oid)

            if pos["tp2_raggiunto"]:
                esito, titolo, corpo = (
                    "vinto_parziale", "✅ *POSIZIONE CHIUSA IN PROFITTO (SL a TP1)*",
                    f"Posizione su `{pair}` chiusa dopo TP1 e TP2 raggiunti sull'exchange."
                )
            elif pos["tp1_raggiunto"]:
                esito, titolo, corpo = (
                    "pareggio", "➖ *POSIZIONE CHIUSA IN PAREGGIO (SL a breakeven)*",
                    f"Posizione su `{pair}` chiusa a breakeven dopo TP1. Nessuna perdita netta."
                )
            else:
                esito, titolo, corpo = (
                    "perso", "🛑 *STOP LOSS COLPITO*",
                    f"Stop Loss originale colpito su `{pair}`. Posizione chiusa in perdita."
                )

            # Prezzo di chiusura REALE: usiamo il livello dell'ordine che ha effettivamente
            # chiuso la posizione (SL o TP), non il prezzo di mercato letto ORA - possono
            # essere passati fino a 10 minuti dalla chiusura vera, e nel frattempo il prezzo
            # puo' essersi mosso parecchio, falsando sia la card che il calcolo del P&L
            # (visto in produzione: uno Stop Loss mostrato come "vinto" perche' il prezzo
            # era rimbalzato sopra l'entry nel tempo tra la chiusura vera e il nostro poll).
            if esito == "perso":
                prezzo_chiusura_reale = s.stop_loss  # SL originale, mai spostato in questo caso
            elif esito == "pareggio":
                prezzo_chiusura_reale = s.stop_loss  # SL a breakeven (gia' spostato = entry)
            else:
                # "vinto_parziale": la size restante puo' essersi chiusa sul TP3 (target
                # finale) o sullo SL spostato a TP1 (se il prezzo e' tornato indietro dopo
                # TP2) - senza consultare i fill reali non possiamo saperlo con certezza,
                # quindi qui manteniamo il prezzo di mercato attuale come approssimazione.
                try:
                    prezzo_chiusura_reale = get_prezzo_per_monitoraggio(pair)
                except Exception:
                    prezzo_chiusura_reale = s.entry
            prezzo_chiusura = prezzo_chiusura_reale
            pnl_usd = calcola_pnl_usd(pos, prezzo_chiusura)
            await invia_card_risultato(bot, s, esito_positivo=(esito != "perso"), percentuale=s.sl_percento, prezzo_chiusura=prezzo_chiusura)
            corpo += f"\nRisultato: {'+' if pnl_usd >= 0 else ''}{pnl_usd:.2f} USDT"
            await invia_aggiornamento(bot, s, titolo, corpo)

            statistiche["totali"] += 1
            statistiche["pnl_totale_usd"] = statistiche.get("pnl_totale_usd", 0.0) + pnl_usd
            voce_storia = pos.get("storia_ref")
            if voce_storia is not None:
                voce_storia["chiuso"] = True
                voce_storia["esito"] = esito
                voce_storia["pnl_usd"] = pnl_usd
            per_coppia = statistiche.setdefault("per_coppia", {})
            riga_coppia = per_coppia.setdefault(pair, {"vinti": 0, "persi": 0, "pareggi": 0})
            if esito == "perso":
                statistiche["persi"] += 1
                statistiche["perdite_consecutive"] = statistiche.get("perdite_consecutive", 0) + 1
                riga_coppia["persi"] += 1
            else:
                statistiche["perdite_consecutive"] = 0
                if esito == "pareggio":
                    statistiche["pareggi"] = statistiche.get("pareggi", 0) + 1
                    riga_coppia["pareggi"] += 1
                else:
                    statistiche["vinti"] += 1
                    riga_coppia["vinti"] += 1

            chiuse.append(pair)

            if statistiche["perdite_consecutive"] >= MAX_PERDITE_CONSECUTIVE and not statistiche.get("pausa_fino"):
                pausa_fino = datetime.now() + timedelta(hours=PAUSA_ORE)
                statistiche["pausa_fino"] = pausa_fino.isoformat()
                try:
                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=(
                            f"⏸ *Pausa automatica attivata*\n"
                            f"{MAX_PERDITE_CONSECUTIVE} Stop Loss consecutivi raggiunti. "
                            f"Pubblicazione E apertura di nuovi trade sospese fino alle "
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

        # --- Riduzione parziale della size: un TP e' scattato sull'exchange ---
        diff = quantita_rimanente_attesa - size_attuale
        if diff > tolleranza:
            if not pos["tp1_raggiunto"]:
                pos["tp1_raggiunto"] = True
                pos["quantita_rimanente"] = size_attuale
                nuovo_sl = s.entry
                cancella_ordine_piano(pair, pos.get("sl_order_id"))
                try:
                    risposta = piazza_ordine_piano(pair, s.direzione, nuovo_sl, size_attuale)
                    pos["sl_order_id"] = (risposta or {}).get("orderId")
                    s.stop_loss = nuovo_sl
                except Exception as e:
                    logger.error(f"{pair}: impossibile ripiazzare lo SL a breakeven dopo TP1: {e}")
                    await notifica_admin(bot, f"🚨 SL non ripiazzato dopo TP1 su `{pair}` - controllare manualmente su Bitget.\n`{e}`")
                await invia_aggiornamento(
                    bot, s, "🎯 *TP1 RAGGIUNTO*",
                    f"Primo target eseguito su Bitget per `{pair}` (+{s.tp_percento(s.tp1)}%). "
                    f"Stop Loss spostato a breakeven (`{nuovo_sl}`)."
                )
            elif not pos["tp2_raggiunto"]:
                pos["tp2_raggiunto"] = True
                pos["quantita_rimanente"] = size_attuale
                nuovo_sl = s.tp1
                cancella_ordine_piano(pair, pos.get("sl_order_id"))
                try:
                    risposta = piazza_ordine_piano(pair, s.direzione, nuovo_sl, size_attuale)
                    pos["sl_order_id"] = (risposta or {}).get("orderId")
                    s.stop_loss = nuovo_sl
                except Exception as e:
                    logger.error(f"{pair}: impossibile ripiazzare lo SL a TP1 dopo TP2: {e}")
                    await notifica_admin(bot, f"🚨 SL non ripiazzato dopo TP2 su `{pair}` - controllare manualmente su Bitget.\n`{e}`")
                await invia_aggiornamento(
                    bot, s, "🎯 *TP2 RAGGIUNTO*",
                    f"Secondo target eseguito su Bitget per `{pair}` (+{s.tp_percento(s.tp2)}%). "
                    f"Stop Loss spostato a `{nuovo_sl}` (livello TP1, profitto bloccato)."
                )

        await asyncio.sleep(1)

    for pair in chiuse:
        posizioni_aperte.pop(pair, None)


async def invia_statistiche(bot: Bot, statistiche: dict):
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
        pareggi = statistiche.get("pareggi", 0)
        win_rate = round(vinti / totali * 100, 1)
        pnl = statistiche.get("pnl_totale_usd", 0.0)
        testo = (
            "📊 *Statistiche Sala Segnali*\n"
            "━━━━━━━━━━━━━━━\n"
            f"Posizioni chiuse: {totali}\n"
            f"Vinte: {vinti}  |  Pareggi: {pareggi}  |  Perse: {persi}\n"
            f"Win rate: {win_rate}%\n"
            f"Risultato totale: {'+' if pnl >= 0 else ''}{pnl:.2f} USDT\n\n"
            "_Dati reali raccolti dal bot direttamente dagli ordini eseguiti su Bitget "
            f"({'Demo Trading' if bitget_demo_attivo() else 'conto LIVE'}). Non è consulenza finanziaria._\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
        logger.info("Statistiche pubblicate sul canale")
    except TelegramError as e:
        logger.error(f"Errore invio statistiche: {e}")


def _testo_statistiche(statistiche: dict) -> str:
    totali = statistiche.get("totali", 0)
    if totali == 0:
        return "Nessuna posizione ancora chiusa (SL o TP3) da quando il bot è attivo."
    vinti = statistiche.get("vinti", 0)
    persi = statistiche.get("persi", 0)
    pareggi = statistiche.get("pareggi", 0)
    win_rate = round(vinti / totali * 100, 1)
    pnl = statistiche.get("pnl_totale_usd", 0.0)
    testo = (
        f"Posizioni chiuse: {totali}\n"
        f"Vinte: {vinti}  |  Pareggi: {pareggi}  |  Perse: {persi}\n"
        f"Win rate: {win_rate}%\n"
        f"Risultato totale: {'+' if pnl >= 0 else ''}{pnl:.2f} USDT"
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
            v, p, pa = esiti.get("vinti", 0), esiti.get("persi", 0), esiti.get("pareggi", 0)
            tot = v + p + pa
            if tot == 0:
                continue
            classifica.append((pair, v, p, pa, tot, v - p))

        classifica_migliori = sorted(classifica, key=lambda x: x[5], reverse=True)[:3]
        classifica_peggiori = sorted(classifica, key=lambda x: x[5])[:3]

        righe_migliori = "\n".join(f"  `{p}` — {v}V/{pa}Pareggio/{pe}P" for p, v, pe, pa, t, n in classifica_migliori) or "  (dati insufficienti)"
        righe_peggiori = "\n".join(f"  `{p}` — {v}V/{pa}Pareggio/{pe}P" for p, v, pe, pa, t, n in classifica_peggiori) or "  (dati insufficienti)"

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


def _messaggio_benvenuto() -> str:
    riga_trading = (
        f"🤖 *Trading automatico ATTIVO* su Bitget ({'Demo Trading — soldi virtuali' if bitget_demo_attivo() else 'conto LIVE — soldi reali'}). "
        "Ogni segnale valido apre in automatico la posizione, con Stop Loss e 3 Take Profit "
        "gestiti come ordini reali sull'exchange.\n"
        if TRADING_ABILITATO else
        "📡 *Modalita' solo segnalazione*: i segnali vengono pubblicati ma nessun ordine viene aperto automaticamente.\n"
    )
    riga_limite_demo = (
        "\nℹ️ _Nota: il Demo Trading di Bitget supporta l'apertura automatica solo su BTC/ETH/EOS/XRP "
        "(limite della piattaforma). I segnali sulle altre coppie restano pubblicati come informativi "
        "e diventeranno eseguibili passando al conto LIVE._\n"
        if TRADING_ABILITATO and bitget_demo_attivo() else ""
    )
    return (
        "🏛 *SALA SEGNALI VIP*\n"
        "━━━━━━━━━━━━━━━\n"
        f"Bot automatico che analizza {len(WATCHLIST)} coppie (dati Bitget: EMA, RSI, MACD, ATR, Volume) "
        f"e pubblica solo i setup con punteggio di confidenza ≥{SCORE_MINIMO_PUBBLICAZIONE}/100.\n\n"
        f"{riga_trading}"
        f"{riga_limite_demo}\n"
        "🔒 Lo Stop Loss si sposta automaticamente a breakeven dopo TP1 e a TP1 dopo TP2, "
        "per proteggere i profitti parziali - direttamente sugli ordini piazzati su Bitget.\n"
        "📊 Statistiche reali pubblicate periodicamente (non promesse, dati veri).\n\n"
        "⚠️ *Importante:* nessun segnale è garantito. Fai sempre le tue verifiche (DYOR) e non "
        "rischiare mai più di quanto puoi permetterti di perdere."
    )


async def invia_e_fissa_benvenuto(bot: Bot) -> int | None:
    try:
        msg = await bot.send_message(chat_id=CHANNEL_ID, text=_messaggio_benvenuto(), parse_mode=ParseMode.MARKDOWN)
        try:
            await bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=msg.message_id, disable_notification=True)
            logger.info("Messaggio di benvenuto pubblicato e fissato in cima al canale")
        except TelegramError as e:
            logger.warning(
                f"Messaggio pubblicato ma non fissato (il bot deve essere admin con permesso "
                f"'Pin messages' sul canale): {e}"
            )
        return msg.message_id
    except TelegramError as e:
        logger.error(f"Errore invio messaggio di benvenuto: {e}")
        return None


async def aggiorna_messaggio_benvenuto(bot, message_id: int | None):
    """Riscrive il messaggio fissato con il testo aggiornato (es. dopo un cambio
    demo/live dalla Mini App), cosi' non resta congelato a quello che diceva all'avvio."""
    if not bot or not message_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=CHANNEL_ID, message_id=message_id,
            text=_messaggio_benvenuto(), parse_mode=ParseMode.MARKDOWN,
        )
        logger.info("Messaggio fissato aggiornato dopo un cambio di configurazione.")
    except TelegramError as e:
        logger.warning(f"Impossibile aggiornare il messaggio fissato: {e}")


# ---------------------------------------------------------------------------
# LOOP PRINCIPALE
# ---------------------------------------------------------------------------

async def ciclo_scansione(bot: Bot, ultimo_segnale: dict, posizioni_aperte: dict, statistiche: dict, storia_segnali: list):
    ora = datetime.now()

    if CONFIG.get("pausa_manuale"):
        logger.info("Pausa MANUALE attiva (impostata dalla Mini App) - scansione saltata.")
        return

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
                    text="▶️ *Pausa terminata* — riprendiamo la pubblicazione e l'esecuzione dei segnali.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError as e:
                logger.error(f"Errore invio messaggio di ripresa: {e}")
            logger.info("Pausa automatica terminata, riprendo la pubblicazione dei segnali.")

    logger.info(f"--- Inizio scansione: {len(WATCHLIST)} coppie ---")

    risultati = []
    almeno_un_segnale = False

    for pair in WATCHLIST:
        ultima_pubblicazione = ultimo_segnale.get(pair)
        if ultima_pubblicazione and (ora - ultima_pubblicazione) < timedelta(hours=COOLDOWN_ORE):
            logger.info(f"{pair}: in cooldown, salto")
            continue

        # Non apriamo una seconda posizione sulla stessa coppia se ce n'e' gia' una
        # attiva (tracciata localmente con i suoi ordini piano su Bitget).
        if pair in posizioni_aperte:
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
                non_supportata_in_demo = bitget_demo_attivo() and pair not in COPPIE_TRADABILI_IN_DEMO
                dettagli_trade = None
                if TRADING_ABILITATO and not non_supportata_in_demo:
                    dettagli_trade = await esegui_apertura_trade(bot, segnale)
                elif non_supportata_in_demo:
                    logger.info(
                        f"{pair}: segnale valido (score {score}) ma questa coppia non e' tra le 4 "
                        f"supportate dal Demo Trading Bitget (BTC/ETH/EOS/XRP) - pubblicato solo come "
                        f"informativo, nessun tentativo di apertura."
                    )

                eseguito = dettagli_trade is not None
                inviato = await invia_segnale(bot, segnale, eseguito, non_supportata_in_demo)
                if inviato:
                    ultimo_segnale[pair] = ora
                    voce_storia = {
                        "coppia": segnale.coppia,
                        "direzione": segnale.direzione.value,
                        "score": segnale.score,
                        "entry": segnale.entry,
                        "stop_loss": segnale.stop_loss,
                        "tp1": segnale.tp1, "tp2": segnale.tp2, "tp3": segnale.take_profit,
                        "leva": segnale.leva_suggerita,
                        "eseguito": eseguito,
                        "timestamp": ora.isoformat(),
                        "chiuso": False,
                    }
                    storia_segnali.append(voce_storia)
                    del storia_segnali[:-30]  # teniamo solo gli ultimi 30 per non far crescere il file all'infinito
                    voce_posizione = {
                        "segnale": segnale,
                        "tp1_raggiunto": False,
                        "tp2_raggiunto": False,
                        "aperto_il": ora,
                    }
                    if dettagli_trade:
                        voce_posizione.update(dettagli_trade)
                    # Tracciamo la posizione (per il monitoraggio SL/TP) solo se un trade
                    # e' stato davvero eseguito, oppure se il trading automatico e'
                    # disattivato (allora si torna al monitoraggio simulato via prezzo).
                    if eseguito or not TRADING_ABILITATO:
                        voce_posizione["storia_ref"] = voce_storia  # solo in-memory, non persistito
                        posizioni_aperte[pair] = voce_posizione
                    almeno_un_segnale = True

        await asyncio.sleep(1)

    logger.info("--- Fine scansione ---")
    if not almeno_un_segnale:
        await invia_riepilogo(bot)


async def invia_riepilogo(bot: Bot):
    logger.info("Nessun segnale sopra soglia in questo ciclo - nessun messaggio inviato al canale.")


async def notifica_admin(bot: Bot, testo: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Impossibile inviare la notifica privata all'admin: {e}")


# ---------------------------------------------------------------------------
# MINI APP (dashboard web dentro Telegram)
# ---------------------------------------------------------------------------

def valida_init_data(init_data: str) -> dict | None:
    """Verifica che initData sia stato firmato davvero da Telegram (HMAC-SHA256 col
    bot token, secondo lo standard ufficiale delle Mini App) e non sia scaduto (5 minuti).
    Se AUTHORIZED_USER_ID e' impostato, verifica anche che l'utente sia proprio Fabio.
    Ritorna il dizionario dei dati (incl. 'user') se valido, altrimenti None."""
    if not init_data:
        return None
    try:
        coppie = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    hash_ricevuto = coppie.pop("hash", None)
    if not hash_ricevuto:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(coppie.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    hash_calcolato = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(hash_calcolato, hash_ricevuto):
        return None

    try:
        auth_date = int(coppie.get("auth_date", "0"))
        if time.time() - auth_date > 300:
            return None
    except ValueError:
        return None

    if AUTHORIZED_USER_ID:
        try:
            utente = json.loads(coppie.get("user", "{}"))
            if str(utente.get("id")) != AUTHORIZED_USER_ID:
                return None
        except (json.JSONDecodeError, TypeError):
            return None

    return coppie


async def handle_index(request):
    try:
        with open(os.path.join(ASSETS_DIR, "dashboard.html"), "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="dashboard.html non trovato accanto a main.py.", status=500)


async def handle_api_data(request):
    init_data = request.query.get("initData", "")
    if not valida_init_data(init_data):
        return web.json_response({"errore": "Autenticazione Telegram non valida"}, status=401)

    stato = request.app["stato_condiviso"]
    posizioni_aperte = stato["posizioni_aperte"]
    statistiche = stato["statistiche"]
    storia_segnali = stato["storia_segnali"]

    posizioni_reali = []
    avviso_posizioni_reali = None
    if TRADING_ABILITATO:
        try:
            grezze = get_tutte_posizioni_aperte()
            for p in grezze:
                posizioni_reali.append({
                    "symbol": p.get("symbol"),
                    "lato": p.get("holdSide"),
                    "size": p.get("total") or p.get("available"),
                    "entry": p.get("openPriceAvg"),
                    "pnl": p.get("unrealizedPL"),
                    "leva": p.get("leverage"),
                })
        except Exception as e:
            logger.warning(f"Mini App: impossibile leggere le posizioni reali da Bitget: {e}")
            avviso_posizioni_reali = spiega_errore_bitget(e)

    payload = {
        "status": {
            "trading_abilitato": TRADING_ABILITATO,
            "demo": bitget_demo_attivo(),
            "modalita": CONFIG["modalita"],
            "live_configurato": bool(BITGET_API_KEY_LIVE and BITGET_API_SECRET_LIVE and BITGET_API_PASSPHRASE_LIVE),
            "pausa_manuale": CONFIG["pausa_manuale"],
            "pausa_automatica_fino": statistiche.get("pausa_fino"),
            "rischio_percento": CONFIG["rischio_percento"],
        },
        "posizioni_reali": posizioni_reali,
        "posizioni_tracciate": [
            {
                "coppia": pair,
                "direzione": pos["segnale"].direzione.value,
                "entry": pos["segnale"].entry,
                "stop_loss": pos["segnale"].stop_loss,
                "tp1": pos["segnale"].tp1, "tp2": pos["segnale"].tp2, "tp3": pos["segnale"].take_profit,
                "tp1_raggiunto": pos["tp1_raggiunto"], "tp2_raggiunto": pos["tp2_raggiunto"],
            }
            for pair, pos in posizioni_aperte.items()
        ],
        "statistiche": {
            "vinti": statistiche.get("vinti", 0),
            "persi": statistiche.get("persi", 0),
            "pareggi": statistiche.get("pareggi", 0),
            "totali": statistiche.get("totali", 0),
            "pnl_totale_usd": statistiche.get("pnl_totale_usd", 0.0),
        },
        "storia_segnali": list(reversed(storia_segnali[-15:])),
        "avviso_posizioni_reali": avviso_posizioni_reali,
    }
    return web.json_response(payload)


async def handle_api_action(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"errore": "Corpo JSON non valido"}, status=400)

    if not valida_init_data(body.get("initData", "")):
        return web.json_response({"errore": "Autenticazione Telegram non valida"}, status=401)

    azione = body.get("azione")
    if azione == "pausa":
        CONFIG["pausa_manuale"] = True
    elif azione == "riprendi":
        CONFIG["pausa_manuale"] = False
    elif azione == "rischio":
        try:
            valore = float(body.get("valore"))
        except (TypeError, ValueError):
            return web.json_response({"errore": "Valore rischio non valido"}, status=400)
        if not (0.5 <= valore <= 10):
            return web.json_response({"errore": "Il rischio per trade deve essere tra 0.5% e 10%"}, status=400)
        CONFIG["rischio_percento"] = valore
    elif azione == "modalita":
        nuova = body.get("valore")
        if nuova not in ("demo", "live"):
            return web.json_response({"errore": "Modalita' non valida"}, status=400)
        if nuova == "live" and not (BITGET_API_KEY_LIVE and BITGET_API_SECRET_LIVE and BITGET_API_PASSPHRASE_LIVE):
            return web.json_response({
                "errore": "Credenziali del conto LIVE non configurate su Railway "
                           "(BITGET_API_KEY_LIVE / BITGET_API_SECRET_LIVE / BITGET_API_PASSPHRASE_LIVE). "
                           "Aggiungile prima di passare a Live."
            }, status=400)
        CONFIG["modalita"] = nuova
        logger.info(f"Modalita' cambiata a '{nuova}' dalla Mini App.")
        stato = request.app["stato_condiviso"]
        await aggiorna_messaggio_benvenuto(stato.get("bot"), stato.get("id_benvenuto"))
    else:
        return web.json_response({"errore": "Azione sconosciuta"}, status=400)

    return web.json_response({"ok": True, "config": CONFIG})


async def avvia_web_server(stato_condiviso: dict):
    """Avvia il server HTTP della Mini App sulla porta assegnata da Railway, in
    parallelo al loop di scansione del bot (stessa event loop, nessun processo separato)."""
    app = web.Application()
    app["stato_condiviso"] = stato_condiviso
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/data", handle_api_data)
    app.router.add_post("/api/action", handle_api_action)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    logger.info(f"Mini App in ascolto su 0.0.0.0:{WEB_PORT}")


# ---------------------------------------------------------------------------
# COMANDI BOT (funzionano solo in chat privata con il bot, non nel canale)
# ---------------------------------------------------------------------------

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛 Ciao! Sono il bot della Sala Segnali VIP.\n\n"
        "Comandi disponibili:\n"
        "/stats — statistiche reali aggiornate\n"
        "/posizioni — stato reale su Bitget in questo momento\n"
        "/dashboard — apri la Mini App con grafica completa\n"
        "/regole — come funziona la sala segnali\n"
        "/help — questo messaggio"
    )


async def cmd_help(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandi disponibili:\n"
        "/stats — statistiche reali aggiornate (vinti/pareggi/persi/win rate)\n"
        "/posizioni — posizioni e ordini SL/TP letti in diretta da Bitget (non dalla memoria del bot)\n"
        "/dashboard — apri la Mini App: dashboard web con grafica completa e controlli\n"
        "/regole — come funziona la sala segnali e i suoi limiti\n"
        "/help — questo messaggio"
    )


async def cmd_regole(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_messaggio_benvenuto(), parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update, context: ContextTypes.DEFAULT_TYPE):
    statistiche = context.bot_data.get("statistiche", {"vinti": 0, "persi": 0, "pareggi": 0, "totali": 0})
    testo = "📊 *Statistiche Sala Segnali*\n━━━━━━━━━━━━━━━\n" + _testo_statistiche(statistiche)
    await update.message.reply_text(testo, parse_mode=ParseMode.MARKDOWN)


async def cmd_dashboard(update, context: ContextTypes.DEFAULT_TYPE):
    if not RAILWAY_PUBLIC_DOMAIN:
        await update.message.reply_text(
            "⚠️ Dominio pubblico non rilevato. Su Railway, nel servizio, vai in "
            "Settings → Networking e genera un dominio pubblico, poi riprova."
        )
        return
    url = f"https://{RAILWAY_PUBLIC_DOMAIN}/"
    tastiera = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Apri Dashboard", web_app=WebAppInfo(url=url))]])
    await update.message.reply_text("Tocca per aprire la dashboard:", reply_markup=tastiera)


async def cmd_posizioni(update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra lo stato REALE su Bitget in questo momento (non la memoria interna del
    bot) - posizioni aperte e ordini SL/TP ancora pendenti. Utile per verificare a colpo
    d'occhio che tutto combaci prima di fidarsi del bot in modalita' reale."""
    if not TRADING_ABILITATO:
        await update.message.reply_text("Trading automatico disattivato (TRADING_ABILITATO=false) - nessuna posizione da controllare su Bitget.")
        return
    try:
        posizioni = get_tutte_posizioni_aperte()
    except Exception as e:
        await update.message.reply_text(f"⚠️ Impossibile leggere le posizioni da Bitget:\n{spiega_errore_bitget(e)}", parse_mode=ParseMode.MARKDOWN)
        return

    if not posizioni:
        testo = "📭 Nessuna posizione aperta su Bitget in questo momento."
    else:
        righe = [f"📋 *Posizioni reali su Bitget* {'(Demo Trading)' if bitget_demo_attivo() else '(LIVE)'}", "━━━━━━━━━━━━━━━"]
        for p in posizioni:
            symbol = p.get("symbol", "?")
            lato = p.get("holdSide", "?")
            size = p.get("total") or p.get("available") or "0"
            entry = p.get("openPriceAvg", "?")
            pnl = p.get("unrealizedPL", "?")
            try:
                ordini_piano = get_ordini_piano_pendenti()
                n_ordini = sum(1 for o in ordini_piano if o.get("symbol") == symbol)
            except Exception:
                n_ordini = "?"
            righe.append(
                f"`{symbol}` ({lato}) — size {size}, entry {entry}, PnL {pnl} USDT, "
                f"{n_ordini} ordini SL/TP pendenti"
            )
        testo = "\n".join(righe)

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
    if TRADING_ABILITATO and not (BITGET_API_KEY and BITGET_API_SECRET and BITGET_API_PASSPHRASE):
        logger.error(
            "TRADING_ABILITATO e' true ma mancano le credenziali Bitget demo "
            "(BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE). Il bot si ferma."
        )
        return
    logger.info(
        f"Trading automatico: {'ATTIVO' if TRADING_ABILITATO else 'DISATTIVATO (solo segnalazione)'} - "
        f"ambiente: {'DEMO TRADING (soldi virtuali)' if bitget_demo_attivo() else 'LIVE (soldi reali!)'}"
    )

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("regole", cmd_regole))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("posizioni", cmd_posizioni))
    application.add_handler(CommandHandler("dashboard", cmd_dashboard))
    bot = application.bot

    ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche, ultimo_invio_report, storia_segnali = carica_stato()
    application.bot_data["statistiche"] = statistiche

    if CONFIG["modalita"] == "live" and not (BITGET_API_KEY_LIVE and BITGET_API_SECRET_LIVE and BITGET_API_PASSPHRASE_LIVE):
        logger.error(
            "La modalita' era impostata su LIVE ma mancano le credenziali live "
            "(BITGET_API_KEY_LIVE / BITGET_API_SECRET_LIVE / BITGET_API_PASSPHRASE_LIVE). "
            "Torno in modalita' DEMO per sicurezza."
        )
        CONFIG["modalita"] = "demo"

    stato_condiviso = {
        "posizioni_aperte": posizioni_aperte, "statistiche": statistiche, "storia_segnali": storia_segnali,
        "bot": bot, "id_benvenuto": None,
    }
    asyncio.create_task(avvia_web_server(stato_condiviso))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Comandi bot (/start /help /regole /stats /posizioni /dashboard) attivi in chat privata.")

    try:
        stato_condiviso["id_benvenuto"] = await invia_e_fissa_benvenuto(bot)
    except Exception:
        logger.error("Errore critico durante l'invio del messaggio di benvenuto (il bot continua comunque):")
        logger.error(traceback.format_exc())

    logger.info("Bot avviato. Scansione ogni %d secondi.", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            await ciclo_scansione(bot, ultimo_segnale, posizioni_aperte, statistiche, storia_segnali)
        except Exception:
            tb = traceback.format_exc()
            logger.error("Errore nella scansione di nuovi segnali (il controllo delle posizioni aperte prosegue comunque):")
            logger.error(tb)
            ultima_riga_errore = tb.strip().splitlines()[-1] if tb.strip() else "Errore sconosciuto"
            await notifica_admin(
                bot,
                f"⚠️ *Errore nella scansione nuovi segnali*\n`{ultima_riga_errore}`\n\n"
                f"Il controllo di TP/SL sulle posizioni gia' aperte NON e' stato bloccato da questo errore."
            )

        # Separato dal blocco sopra apposta: un errore nella ricerca di nuovi segnali non
        # deve MAI impedire il controllo di TP/SL sulle posizioni gia' aperte, altrimenti
        # una posizione reale resterebbe senza monitoraggio finche' l'errore non si risolve.
        try:
            await controlla_posizioni_aperte(bot, posizioni_aperte, statistiche)
            await verifica_coerenza_posizioni(bot, posizioni_aperte)
        except Exception:
            tb = traceback.format_exc()
            logger.error("Errore nel controllo delle posizioni aperte (TP/SL):")
            logger.error(tb)
            ultima_riga_errore = tb.strip().splitlines()[-1] if tb.strip() else "Errore sconosciuto"
            await notifica_admin(
                bot,
                f"🚨 *Errore nel controllo TP/SL delle posizioni aperte*\n`{ultima_riga_errore}`\n\n"
                f"Controllare manualmente le posizioni aperte su Bitget finche' l'errore non e' risolto."
            )

        try:
            if datetime.now() - ultimo_invio_statistiche >= timedelta(hours=STATISTICHE_INTERVALLO_ORE):
                await invia_statistiche(bot, statistiche)
                ultimo_invio_statistiche = datetime.now()

            if datetime.now() - ultimo_invio_report >= timedelta(days=REPORT_SETTIMANALE_INTERVALLO_GIORNI):
                await invia_report_settimanale(bot, statistiche)
                ultimo_invio_report = datetime.now()

            salva_stato(ultimo_segnale, posizioni_aperte, statistiche, ultimo_invio_statistiche, ultimo_invio_report, storia_segnali)
        except Exception:
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
