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
import time
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

# Watchlist: le coppie Kraken piu' scambiate (puoi ampliarla)
WATCHLIST = [
    "XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOGEUSD", "AVAXUSD", "DOTUSD", "LINKUSD", "MATICUSD",
    "LTCUSD", "BCHUSD", "ATOMUSD", "UNIUSD", "ARBUSD",
]

SCAN_INTERVAL_SECONDS = 600       # ogni 10 minuti
SCORE_MINIMO_PUBBLICAZIONE = 65   # su 100
COOLDOWN_ORE = 4                  # non ripetere segnale stesso asset entro N ore

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("segnali_ai")


class Direzione(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Segnale:
    coppia: str
    direzione: Direzione
    entry: float
    stop_loss: float
    take_profit: float
    timeframe: str
    score: int
    motivazione: str

    @property
    def risk_reward(self) -> float:
        rischio = abs(self.entry - self.stop_loss)
        rendimento = abs(self.take_profit - self.entry)
        return round(rendimento / rischio, 2) if rischio else 0.0


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

def analizza_coppia(pair: str) -> Segnale | None:
    try:
        df_4h = get_ohlc(pair, interval=240, count=250)
        df_1h = get_ohlc(pair, interval=60, count=250)
    except Exception as e:
        logger.warning(f"Dati non disponibili per {pair}: {e}")
        return None

    if len(df_4h) < 200 or len(df_1h) < 50:
        return None

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
        return None

    entry = ultimo["close"]
    atr_val = ultimo["atr"] if pd.notna(ultimo["atr"]) else entry * 0.01

    if bias == Direzione.LONG:
        stop_loss = entry - (1.5 * atr_val)
        take_profit = entry + (3 * atr_val)
    else:
        stop_loss = entry + (1.5 * atr_val)
        take_profit = entry - (3 * atr_val)

    return Segnale(
        coppia=pair,
        direzione=bias,
        entry=round(entry, 6),
        stop_loss=round(stop_loss, 6),
        take_profit=round(take_profit, 6),
        timeframe="1h (trend 4h)",
        score=score,
        motivazione="; ".join(motivi),
    )


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def formatta_messaggio(s: Segnale) -> str:
    emoji_direzione = "🟢" if s.direzione == Direzione.LONG else "🔴"
    freccia = "📈" if s.direzione == Direzione.LONG else "📉"
    piene = round(s.score / 10)
    barra = "█" * piene + "░" * (10 - piene)

    return (
        f"{emoji_direzione} *SEGNALE {s.direzione.value}* {freccia}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"*Coppia:* `{s.coppia}`\n"
        f"*Timeframe:* {s.timeframe}\n\n"
        f"*Entry:* `{s.entry}`\n"
        f"*Stop Loss:* `{s.stop_loss}`\n"
        f"*Take Profit:* `{s.take_profit}`\n"
        f"*Risk/Reward:* 1:{s.risk_reward}\n\n"
        f"*Confidenza:* {barra} {s.score}/100\n\n"
        f"_{s.motivazione}_\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"⚠️ _Segnale generato automaticamente da indicatori tecnici. Non è consulenza finanziaria. DYOR._"
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


# ---------------------------------------------------------------------------
# LOOP PRINCIPALE
# ---------------------------------------------------------------------------

async def ciclo_scansione(bot: Bot, ultimo_segnale: dict):
    ora = datetime.now()

    for pair in WATCHLIST:
        ultima_pubblicazione = ultimo_segnale.get(pair)
        if ultima_pubblicazione and (ora - ultima_pubblicazione) < timedelta(hours=COOLDOWN_ORE):
            continue

        segnale = analizza_coppia(pair)
        if segnale:
            inviato = await invia_segnale(bot, segnale)
            if inviato:
                ultimo_segnale[pair] = ora

        time.sleep(1)  # rispetto rate limit Kraken


async def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("BOT_TOKEN o CHANNEL_ID mancanti. Impostali come variabili d'ambiente.")
        return

    bot = Bot(token=BOT_TOKEN)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text="✅ Segnali AI avviato. Scansione mercato in corso...",
    )

    ultimo_segnale: dict = {}

    logger.info("Bot avviato. Scansione ogni %d secondi.", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            await ciclo_scansione(bot, ultimo_segnale)
        except Exception as e:
            logger.error(f"Errore nel ciclo di scansione: {e}")

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
