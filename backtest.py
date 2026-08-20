"""
Backtest - Segnali AI
---------------------------------
Simula la stessa identica logica di main.py su dati storici Kraken,
per calcolare il win rate reale della strategia prima di fidarsi "alla cieca".

NON invia nulla su Telegram. Stampa solo un report a schermo.

Uso:
    python3 backtest.py

Richiede connessione internet (va eseguito dalla Console di Railway,
non dal sandbox locale se questo non ha accesso alla rete).
"""

import time
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# CONFIG (stessa logica di main.py)
# ---------------------------------------------------------------------------

WATCHLIST = [
    "XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOGEUSD", "AVAXUSD", "DOTUSD", "LINKUSD", "MATICUSD",
    "LTCUSD", "BCHUSD", "ATOMUSD", "UNIUSD", "ARBUSD",
    "TRXUSD", "NEARUSD", "APTUSD", "FILUSD", "ICPUSD",
    "OPUSD", "SUIUSD", "INJUSD", "RENDERUSD", "TIAUSD",
    "SEIUSD", "AAVEUSD", "MKRUSD", "SNXUSD", "GRTUSD",
    "SANDUSD", "MANAUSD", "AXSUSD", "FTMUSD", "ALGOUSD",
    "EGLDUSD", "FLOWUSD", "CHZUSD", "KSMUSD", "XLMUSD",
]

SCORE_MINIMO = 65
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Kraken restituisce al massimo ~720 candele per chiamata.
# Con interval=60 (1h) -> coprono circa 30 giorni.
CANDELE_DA_SCARICARE = 720


# ---------------------------------------------------------------------------
# DATI STORICI
# ---------------------------------------------------------------------------

def get_ohlc_storico(pair: str, interval: int) -> pd.DataFrame:
    params = {"pair": pair, "interval": interval}
    resp = requests.get(KRAKEN_OHLC_URL, params=params, timeout=15)
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
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# INDICATORI (identici a main.py)
# ---------------------------------------------------------------------------

def ema(series, periodo):
    return series.ewm(span=periodo, adjust=False).mean()


def rsi(series, periodo=14):
    delta = series.diff()
    guadagno = delta.clip(lower=0)
    perdita = -delta.clip(upper=0)
    media_guadagno = guadagno.rolling(periodo).mean()
    media_perdita = perdita.rolling(periodo).mean()
    rs = media_guadagno / media_perdita.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series):
    ema12 = ema(series, 12)
    ema26 = ema(series, 26)
    linea_macd = ema12 - ema26
    linea_segnale = ema(linea_macd, 9)
    return linea_macd, linea_segnale


def atr(df, periodo=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()


class Direzione(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class TradeSimulato:
    coppia: str
    direzione: Direzione
    entry_time: pd.Timestamp
    entry: float
    stop_loss: float
    take_profit: float
    score: int
    esito: str = "APERTO"   # WIN / LOSS / APERTO (mai chiuso nel periodo)


# ---------------------------------------------------------------------------
# BACKTEST: cammina candela per candela, come farebbe il bot in tempo reale
# ---------------------------------------------------------------------------

def genera_segnali_storici(pair: str, df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> list[TradeSimulato]:
    df_4h = df_4h.copy()
    df_1h = df_1h.copy()

    df_4h["ema50"] = ema(df_4h["close"], 50)
    df_4h["ema200"] = ema(df_4h["close"], 200)

    df_1h["rsi"] = rsi(df_1h["close"])
    df_1h["macd"], df_1h["macd_signal"] = macd(df_1h["close"])
    df_1h["atr"] = atr(df_1h)
    df_1h["volume_media"] = df_1h["volume"].rolling(20).mean()

    trade_generati = []

    inizio = max(210, 60)

    for i in range(inizio, len(df_1h) - 1):
        candela_time = df_1h["time"].iloc[i]

        riga_4h = df_4h[df_4h["time"] <= candela_time]
        if len(riga_4h) < 200:
            continue
        ultimo_4h = riga_4h.iloc[-1]

        if pd.isna(ultimo_4h["ema50"]) or pd.isna(ultimo_4h["ema200"]):
            continue

        bias = Direzione.LONG if ultimo_4h["ema50"] > ultimo_4h["ema200"] else Direzione.SHORT

        ultimo = df_1h.iloc[i]
        precedente = df_1h.iloc[i - 1]

        if pd.isna(ultimo["rsi"]) or pd.isna(precedente["rsi"]) or pd.isna(ultimo["atr"]):
            continue

        score = 30
        if bias == Direzione.LONG and precedente["rsi"] < 35 <= ultimo["rsi"]:
            score += 25
        elif bias == Direzione.SHORT and precedente["rsi"] > 65 >= ultimo["rsi"]:
            score += 25

        cross_up = precedente["macd"] < precedente["macd_signal"] and ultimo["macd"] >= ultimo["macd_signal"]
        cross_down = precedente["macd"] > precedente["macd_signal"] and ultimo["macd"] <= ultimo["macd_signal"]
        if bias == Direzione.LONG and cross_up:
            score += 25
        elif bias == Direzione.SHORT and cross_down:
            score += 25

        if pd.notna(ultimo["volume_media"]) and ultimo["volume"] > ultimo["volume_media"] * 1.3:
            score += 20

        if score < SCORE_MINIMO:
            continue

        entry = ultimo["close"]
        atr_val = ultimo["atr"]

        if bias == Direzione.LONG:
            stop_loss = entry - (1.5 * atr_val)
            take_profit = entry + (3 * atr_val)
        else:
            stop_loss = entry + (1.5 * atr_val)
            take_profit = entry - (3 * atr_val)

        trade = TradeSimulato(
            coppia=pair,
            direzione=bias,
            entry_time=candela_time,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            score=score,
        )

        for j in range(i + 1, min(i + 200, len(df_1h))):
            futura = df_1h.iloc[j]
            if bias == Direzione.LONG:
                if futura["low"] <= stop_loss:
                    trade.esito = "LOSS"
                    break
                if futura["high"] >= take_profit:
                    trade.esito = "WIN"
                    break
            else:
                if futura["high"] >= stop_loss:
                    trade.esito = "LOSS"
                    break
                if futura["low"] <= take_profit:
                    trade.esito = "WIN"
                    break

        trade_generati.append(trade)

    return trade_generati


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    tutti_i_trade = []

    print(f"Backtest su {len(WATCHLIST)} coppie, ultimi ~30 giorni (720 candele 1h)...\n")

    for pair in WATCHLIST:
        try:
            df_1h = get_ohlc_storico(pair, interval=60)
            df_4h = get_ohlc_storico(pair, interval=240)
        except Exception as e:
            print(f"  [SKIP] {pair}: {e}")
            continue

        if len(df_1h) < 250 or len(df_4h) < 210:
            print(f"  [SKIP] {pair}: dati storici insufficienti")
            continue

        trades = genera_segnali_storici(pair, df_1h, df_4h)
        tutti_i_trade.extend(trades)
        print(f"  {pair}: {len(trades)} segnali generati nel periodo")

        time.sleep(1)

    chiusi = [t for t in tutti_i_trade if t.esito in ("WIN", "LOSS")]
    aperti = [t for t in tutti_i_trade if t.esito == "APERTO"]
    vinti = [t for t in chiusi if t.esito == "WIN"]
    persi = [t for t in chiusi if t.esito == "LOSS"]

    print("\n" + "=" * 50)
    print("REPORT BACKTEST")
    print("=" * 50)
    print(f"Segnali totali generati:     {len(tutti_i_trade)}")
    print(f"Trade chiusi (SL o TP colpito): {len(chiusi)}")
    print(f"Trade ancora aperti a fine periodo: {len(aperti)}")
    print(f"  - Vinti (TP colpito):  {len(vinti)}")
    print(f"  - Persi (SL colpito):  {len(persi)}")

    if chiusi:
        win_rate = len(vinti) / len(chiusi) * 100
        print(f"\nWIN RATE: {win_rate:.1f}%")

        risultato_netto = len(vinti) * 2 - len(persi) * 1
        print(f"Risultato netto (in unità di rischio, R:R 1:2): {risultato_netto:+.1f}R")
        print("(Se rischi l'1% di capitale per trade, questo equivale a circa "
              f"{risultato_netto:+.1f}% di variazione del capitale nel periodo, "
              "escludendo commissioni/slippage)")
    else:
        print("\nNessun trade chiuso nel periodo analizzato.")

    print("=" * 50)


if __name__ == "__main__":
    main()