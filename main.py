"""
Segnali AI - Kraken -> Telegram
---------------------------------
Bot che legge i prezzi pubblici da Kraken, calcola indicatori tecnici
e pubblica automaticamente segnali BUY/SELL su un canale Telegram privato.

Nessuna API key di Kraken necessaria: usiamo solo endpoint pubblici (lettura prezzi).
Nessun trade viene eseguito: e' un bot di sola segnalazione.

VARIABILI D'AMBIENTE RICHIESTE (da impostare su Railway, mai nel codice):
    BOT_TOKEN     -> token del bot Telegram (da @BotFather)
    CHANNEL_ID    -> id numerico del canale privato (es. -1001234567890)

Dipendenze (vedi requirements.txt):
    pip install python-telegram-bot pandas numpy requests
"""

import asyncio
import logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import numpy as np
import pandas as pd
import requests
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

# Watchlist: le coppie Kraken piu' scambiate (ampliata per piu' opportunita' di segnale)
# Nota: MATICUSD, MKRUSD, FTMUSD rimosse -> Kraken le rifiuta come "Invalid asset pair"
# (probabile rebranding ticker, es. MATIC -> POL). Rivedere se serve reintrodurle con nome corretto.
WATCHLIST = [
    # Top cap
    "XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOGEUSD", "AVAXUSD", "DOTUSD", "LINKUSD",
    "LTCUSD", "BCHUSD", "ATOMUSD", "UNIUSD", "ARBUSD",
    # Aggiunte: altre coppie liquide su Kraken
    "TRXUSD", "NEARUSD", "APTUSD", "FILUSD", "ICPUSD",
    "OPUSD", "SUIUSD", "INJUSD", "RENDERUSD", "TIAUSD",
    "SEIUSD", "AAVEUSD", "SNXUSD", "GRTUSD",
    "SANDUSD", "MANAUSD", "AXSUSD", "ALGOUSD",
    "EGLDUSD", "FLOWUSD", "CHZUSD", "KSMUSD", "XLMUSD",
    # Seconda ondata (2026-08-22): watchlist ampliata su richiesta di Fabio.
    # NOTA: alcuni ticker potrebbero dare "Invalid asset pair" su Kraken (come
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

# --- Parametri per il calcolo della leva suggerita ---
# La leva NON è un moltiplicatore di guadagno: serve solo a mantenere costante
# la % di capitale a rischio, dato quanto è "stretto" o "largo" lo Stop Loss.
RISCHIO_PER_TRADE_PERCENTO = 3.0   # % di capitale che si è disposti a perdere se lo SL viene colpito
# (alzato da 1.5 a 3.0 su richiesta: con SL tipici 2-7%, a 1.5% la leva risultava
# quasi sempre 1x. A 3.0% varia di più mantenendo comunque il calcolo reale sul prezzo.)
LEVA_MASSIMA = 10                  # tetto di sicurezza, mai superato indipendentemente dal calcolo
LEVA_MINIMA = 1

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Numero di decimali accettati da Kraken per il PREZZO di ciascuna coppia.
# Valori approssimati sui tick-size reali di Kraken (agosto 2026).
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
    """Restituisce i decimali corretti per una coppia; se sconosciuta, li stima dal prezzo."""
    if pair in DECIMALI_PREZZO:
        return DECIMALI_PREZZO[pair]
    # Stima ragionevole se la coppia non è mappata esplicitamente
    if prezzo >= 1000:
        return 1
    if prezzo >= 100:
        return 2
    if prezzo >= 1:
        return 3
    if prezzo >= 0.1:
        return 4
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
# DATI DI MERCATO (Kraken API pubblica)
# ---------------------------------------------------------------------------

def get_ohlc(pair: str, interval: int, count: int = 250) -> pd.DataFrame:
    """
    Scarica candele OHLC da Kraken.
    interval in minuti: 60 = 1h, 240 = 4h
    """
    params = {"pair": pair, "interval": interval}
    resp = requests.get(KRAKEN_OHLC_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise ValueError(f"Errore Kraken per {pair}: {data['error']}")

    result_key = [k for k in data["result"].keys() if k != "last"][0]
    raw = data["result"][result_key]

    df = pd.DataFrame(
        raw,
        columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    for col in ["open", "high", "low", "close", "vwap", "volume"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.tail(count).reset_index(drop=True)


def get_ultimo_prezzo(pair: str) -> float:
    """Prezzo corrente approssimato con l'ultima candela a 1 minuto. Usato solo
    per controllare se una posizione aperta ha toccato SL/TP, non per generare segnali."""
    df = get_ohlc(pair, interval=1, count=1)
    return float(df.iloc[-1]["close"])


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
        f"⚠️ _Segnale generato automaticamente da indicatori tecnici reali (Kraken). "
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
            prezzo = get_ultimo_prezzo(pair)
        except Exception as e:
            logger.warning(f"Impossibile leggere prezzo corrente per {pair}: {e}")
            continue

        long = s.direzione == Direzione.LONG

        # --- Stop Loss: chiude la posizione, conta come persa ---
        sl_colpito = (prezzo <= s.stop_loss) if long else (prezzo >= s.stop_loss)
        if sl_colpito:
            await invia_aggiornamento(
                bot, s, "🛑 *STOP LOSS COLPITO*",
                f"Stop Loss raggiunto su `{pair}`. Perdita: -{s.sl_percento}% dall'entry. Posizione chiusa."
            )
            statistiche["persi"] += 1
            statistiche["totali"] += 1
            chiuse.append(pair)
            await asyncio.sleep(1)
            continue

        # --- TP3: target finale, chiude la posizione, conta come vinta ---
        tp3_colpito = (prezzo >= s.take_profit) if long else (prezzo <= s.take_profit)
        if tp3_colpito:
            await invia_aggiornamento(
                bot, s, "✅ *TARGET FINALE RAGGIUNTO (TP3)*",
                f"Target finale centrato su `{pair}`. Guadagno: +{s.tp_percento(s.take_profit)}% dall'entry. Posizione chiusa."
            )
            statistiche["vinti"] += 1
            statistiche["totali"] += 1
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
    """Pubblica sul canale le statistiche reali (non teoriche) raccolte dal bot
    dall'ultimo riavvio: quante posizioni chiuse, vinte, perse, win rate."""
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
            "_Dati reali raccolti dall'ultimo riavvio del bot (non è uno storico permanente). "
            "\"Vinta\" = target finale TP3 raggiunto; \"Persa\" = Stop Loss colpito. "
            "Non è consulenza finanziaria._\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
        logger.info("Statistiche pubblicate sul canale")
    except TelegramError as e:
        logger.error(f"Errore invio statistiche: {e}")


MESSAGGIO_BENVENUTO = (
    "🏛 *SALA SEGNALI VIP*\n"
    "━━━━━━━━━━━━━━━\n"
    f"Bot automatico che analizza {len(WATCHLIST)} coppie su Kraken (EMA, RSI, MACD, ATR, Volume) "
    f"e pubblica solo i setup con punteggio di confidenza ≥{SCORE_MINIMO_PUBBLICAZIONE}/100.\n\n"
    "📍 Ogni segnale include Entry, Stop Loss, 3 Take Profit scalari e leva calcolata "
    "sul rischio reale — mai un numero fisso.\n"
    "🎯 Aggiornamenti automatici quando SL o un TP viene raggiunto.\n"
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

async def ciclo_scansione(bot: Bot, ultimo_segnale: dict, posizioni_aperte: dict):
    ora = datetime.now()
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

        await asyncio.sleep(1)  # rispetto rate limit Kraken (non bloccante)

    logger.info("--- Fine scansione ---")
    if not almeno_un_segnale:
        await invia_riepilogo(bot)


async def invia_riepilogo(bot: Bot):
    """Messaggio generico di attività quando nessun segnale supera la soglia.
    Niente punteggi grezzi in un canale pubblico: comunica selettività, non debolezza."""
    testo = (
        "🔍 *Riepilogo scansione*\n"
        "Nessun setup ha raggiunto la soglia di confidenza minima in questo ciclo. "
        "Continuiamo a monitorare il mercato — pubblichiamo solo segnali di qualità, mai per riempire.\n\n"
        f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=testo, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        logger.error(f"Errore invio riepilogo: {e}")


async def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN o CHANNEL_ID mancanti. Impostali come variabili d'ambiente.")
        return

    bot = Bot(token=BOT_TOKEN)

    # Il messaggio di benvenuto/pin non deve MAI poter bloccare l'avvio del bot:
    # se fallisce per un motivo imprevisto, logghiamo l'errore completo e andiamo avanti comunque.
    try:
        await invia_e_fissa_benvenuto(bot)
    except Exception:
        logger.error("Errore critico durante l'invio del messaggio di benvenuto (il bot continua comunque):")
        logger.error(traceback.format_exc())

    ultimo_segnale: dict = {}
    posizioni_aperte: dict = {}
    statistiche = {"vinti": 0, "persi": 0, "totali": 0}
    ultimo_invio_statistiche = datetime.now()

    logger.info("Bot avviato. Scansione ogni %d secondi.", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            await ciclo_scansione(bot, ultimo_segnale, posizioni_aperte)
            await controlla_posizioni_aperte(bot, posizioni_aperte, statistiche)

            if datetime.now() - ultimo_invio_statistiche >= timedelta(hours=STATISTICHE_INTERVALLO_ORE):
                await invia_statistiche(bot, statistiche)
                ultimo_invio_statistiche = datetime.now()
        except Exception:
            # Logghiamo il traceback COMPLETO (non solo il messaggio) cosi', se il bot
            # dovesse mai fermarsi di nuovo, l'errore vero resta scritto nei log Railway.
            logger.error("Errore nel ciclo di scansione:")
            logger.error(traceback.format_exc())

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.error("Errore FATALE non gestito, il processo sta per terminare:")
        logger.error(traceback.format_exc())
        raise
