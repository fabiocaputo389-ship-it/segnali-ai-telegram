"""
Backtest per Segnali AI - stima realistica di win rate/EV usando la STESSA logica di
score, categorie, soglie e moltiplicatori ATR usate DAVVERO da main.py in questo
momento (non una versione semplificata scritta a parte, per evitare che backtest e
bot live divergano senza accorgersene).

LIMITI ONESTI - leggili prima di fidarti dei numeri:
- Usa solo prezzi storici Bitget (max ~1000 candele per chiamata: circa 41 giorni su
  1h, 166 giorni su 4h). Finestra corta = risultati piu' rumorosi, presta attenzione
  al numero di trade simulati prima di trarre conclusioni.
- NON include notizie storiche: non simula ne' il controllo IA leggero (Demo) ne'
  l'analisi approfondita LIVE ne' lo studio generale di mercato per categoria - quei
  filtri esistono solo nel bot live e qui non possono essere replicati (le notizie
  del passato non sono "ricercabili" allo stesso modo).
- NON applica il filtro orario di Wall Street per azioni/ETF - i risultati storici su
  quelle due categorie vanno quindi presi con piu' cautela rispetto alle crypto.
- NON simula slippage ne' il fallimento di un ordine sotto il minimo Bitget - assume
  esecuzione perfetta esattamente ai prezzi di SL/TP calcolati.
- Le commissioni sono stimate con un valore fisso approssimativo (COMMISSIONE_PERCENTO
  sotto) - sostituiscilo con la tua commissione reale se la conosci.
- Un buon risultato storico NON garantisce risultati futuri - il mercato cambia, e
  questo resta uno strumento per confrontare parametri fra loro, non una previsione.

USO: carica questo file nella STESSA cartella di main.py su GitHub (Upload files),
poi aprilo da Railway -> Console del servizio e lancia:
    python3 backtest.py
Non servono BOT_TOKEN ne' credenziali Bitget: usa solo l'API pubblica dei prezzi.
Impiega qualche minuto (scarica dati storici per ogni coppia attiva).
"""
import sys
import time
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from main import (  # noqa: E402
    CATEGORIE_WATCHLIST, CONFIG, Direzione, categoria_di, ema, rsi, macd, atr,
    get_ohlc, soglie_di, DURATA_MASSIMA_POSIZIONE_ORE, COOLDOWN_ORE,
)

# --- Parametri del backtest, separati da CONFIG cosi' puoi cambiarli senza toccare main.py ---
SOGLIE_DA_TESTARE = [55, 60, 65, 70, 75, 80]
CANDELE_1H = 1000
CANDELE_4H = 1000
COMMISSIONE_PERCENTO = 0.06  # taker+taker stimato - sostituisci con la tua commissione reale se la conosci
FINESTRA_RSI_CANDELE = CONFIG.get("finestra_rsi_candele", 3)
FINESTRA_MACD_CANDELE = CONFIG.get("finestra_macd_candele", 2)
MULT_SL = CONFIG.get("atr_moltiplicatore_sl", 1.8)
MULT_TP1 = CONFIG.get("atr_moltiplicatore_tp1", 1.5)
MULT_TP2 = CONFIG.get("atr_moltiplicatore_tp2", 3.0)
MULT_TP3 = CONFIG.get("atr_moltiplicatore_tp3", 5.0)


def coppie_da_testare() -> list:
    """Tutte le coppie di tutte le categorie definite in main.py - stesso universo che
    il bot analizzerebbe se tutte le categorie fossero attive."""
    viste, elenco = set(), []
    for categoria in CATEGORIE_WATCHLIST.values():
        for pair in categoria["simboli"]:
            if pair not in viste:
                viste.add(pair)
                elenco.append(pair)
    return elenco


def prepara_serie(pair: str):
    """Scarica e prepara le serie 4h/1h con tutti gli indicatori pre-calcolati.
    Ritorna None se i dati non sono disponibili o insufficienti (coppia non listata,
    troppo giovane per avere 200 candele 4h di storico, ecc.)."""
    try:
        df_4h = get_ohlc(pair, interval=240, count=CANDELE_4H)
        df_1h = get_ohlc(pair, interval=60, count=CANDELE_1H)
    except Exception as e:
        print(f"  [salto] {pair}: dati non disponibili ({e})")
        return None
    if len(df_4h) < 200 or len(df_1h) < 60:
        print(f"  [salto] {pair}: storico insufficiente (4h={len(df_4h)}, 1h={len(df_1h)})")
        return None

    df_4h = df_4h.copy()
    df_4h["ema50"] = ema(df_4h["close"], 50)
    df_4h["ema200"] = ema(df_4h["close"], 200)

    df_1h = df_1h.copy()
    df_1h["rsi"] = rsi(df_1h["close"])
    df_1h["macd"], df_1h["macd_signal"] = macd(df_1h["close"])
    df_1h["atr"] = atr(df_1h)
    df_1h["volume_media"] = df_1h["volume"].rolling(20).mean()

    # Per ogni candela 1h, allinea l'ULTIMA candela 4h gia' chiusa a quel momento -
    # stessa logica del bot live, che ad ogni ciclo usa l'ultimo 4h disponibile.
    riferimento_4h = df_4h[["time", "close", "ema50", "ema200"]].rename(
        columns={"close": "close_4h", "ema50": "ema50_4h", "ema200": "ema200_4h"}
    )
    df_1h = pd.merge_asof(
        df_1h.sort_values("time"), riferimento_4h.sort_values("time"),
        on="time", direction="backward",
    )
    return df_1h


def valuta_candela(df_1h: pd.DataFrame, i: int, pair: str) -> tuple:
    """Rirpoduce la logica di score di analizza_coppia() per la candela 1h all'indice i.
    Ritorna (direzione, score) oppure (None, score) se non valido."""
    riga = df_1h.iloc[i]
    if pd.isna(riga.get("ema50_4h")) or pd.isna(riga.get("ema200_4h")):
        return None, -1
    if riga["ema50_4h"] > riga["ema200_4h"]:
        bias = Direzione.LONG
    else:
        bias = Direzione.SHORT

    prezzo_conferma = (
        (bias == Direzione.LONG and riga["close_4h"] > riga["ema50_4h"]) or
        (bias == Direzione.SHORT and riga["close_4h"] < riga["ema50_4h"])
    )
    distanza_ema_percento = abs(riga["ema50_4h"] - riga["ema200_4h"]) / riga["ema200_4h"] * 100 if riga["ema200_4h"] else 0
    soglie = soglie_di(pair)
    if not prezzo_conferma or distanza_ema_percento < soglie["ema_min"]:
        return bias, 0

    if pd.isna(riga["rsi"]) or pd.isna(riga["atr"]):
        return bias, -1

    score = 30  # trend confermato

    finestra_rsi = df_1h["rsi"].iloc[max(0, i - FINESTRA_RSI_CANDELE):i]
    if bias == Direzione.LONG:
        rsi_ok = bool((finestra_rsi < 33).any() and 35 <= riga["rsi"] <= 55)
    else:
        rsi_ok = bool((finestra_rsi > 67).any() and 45 <= riga["rsi"] <= 65)
    if rsi_ok:
        score += 25

    macd_ok = False
    for k in range(1, FINESTRA_MACD_CANDELE + 1):
        if i - k - 1 < 0:
            break
        prec, cur = df_1h.iloc[i - k - 1], df_1h.iloc[i - k]
        incrocio_su = prec["macd"] < prec["macd_signal"] and cur["macd"] >= cur["macd_signal"]
        incrocio_giu = prec["macd"] > prec["macd_signal"] and cur["macd"] <= cur["macd_signal"]
        if bias == Direzione.LONG and incrocio_su and riga["macd"] >= riga["macd_signal"]:
            macd_ok = True
            break
        if bias == Direzione.SHORT and incrocio_giu and riga["macd"] <= riga["macd_signal"]:
            macd_ok = True
            break
    if macd_ok:
        score += 25

    volume_ok = pd.notna(riga["volume_media"]) and riga["volume"] > riga["volume_media"] * 1.3
    if volume_ok:
        score += 20

    atr_percento = (riga["atr"] / riga["close"]) * 100 if riga["close"] else 0
    if atr_percento < soglie["atr_min"]:
        return bias, 0  # mercato piatto, stesso scarto del bot live

    return bias, score


def simula_trade(df_1h: pd.DataFrame, i_ingresso: int, bias: Direzione, atr_val: float, entry: float) -> float:
    """Simula l'esito del trade candela per candela, con TP1/TP2/breakeven come nel bot
    live. Ritorna il risultato in multipli di R (rischio iniziale = 1R), fee incluse."""
    segno = 1 if bias == Direzione.LONG else -1
    stop_loss = entry - segno * MULT_SL * atr_val
    tp1 = entry + segno * MULT_TP1 * atr_val
    tp2 = entry + segno * MULT_TP2 * atr_val
    tp3 = entry + segno * MULT_TP3 * atr_val
    rischio = abs(entry - stop_loss)
    if rischio <= 0:
        return 0.0

    sl_attuale = stop_loss
    tp1_raggiunto = tp2_raggiunto = False
    peso_restante = 1.0  # frazione di posizione ancora aperta
    r_totale = 0.0
    limite = min(len(df_1h), i_ingresso + 1 + DURATA_MASSIMA_POSIZIONE_ORE)

    for j in range(i_ingresso + 1, limite):
        low, high = df_1h.iloc[j]["low"], df_1h.iloc[j]["high"]

        tocca_sl = (low <= sl_attuale) if bias == Direzione.LONG else (high >= sl_attuale)
        if tocca_sl:
            r_uscita = (sl_attuale - entry) / rischio * segno
            r_totale += r_uscita * peso_restante
            return r_totale - COMMISSIONE_PERCENTO / 100 * 2

        if not tp1_raggiunto:
            tocca_tp1 = (high >= tp1) if bias == Direzione.LONG else (low <= tp1)
            if tocca_tp1:
                tp1_raggiunto = True
                r_totale += ((tp1 - entry) / rischio * segno) * (1 / 3)
                peso_restante -= 1 / 3
                sl_attuale = entry  # breakeven, come nel bot live
                continue

        if tp1_raggiunto and not tp2_raggiunto:
            tocca_tp2 = (high >= tp2) if bias == Direzione.LONG else (low <= tp2)
            if tocca_tp2:
                tp2_raggiunto = True
                r_totale += ((tp2 - entry) / rischio * segno) * (1 / 3)
                peso_restante -= 1 / 3
                sl_attuale = tp1
                continue

        if tp2_raggiunto:
            tocca_tp3 = (high >= tp3) if bias == Direzione.LONG else (low <= tp3)
            if tocca_tp3:
                r_totale += ((tp3 - entry) / rischio * segno) * peso_restante
                return r_totale - COMMISSIONE_PERCENTO / 100 * 2

    # Timeout (DURATA_MASSIMA_POSIZIONE_ORE) senza chiusura completa: chiude al prezzo
    # dell'ultima candela disponibile, come fa il bot live.
    prezzo_finale = df_1h.iloc[min(limite, len(df_1h)) - 1]["close"]
    r_totale += ((prezzo_finale - entry) / rischio * segno) * peso_restante
    return r_totale - COMMISSIONE_PERCENTO / 100 * 2


def simula_su_soglia(df_1h: pd.DataFrame, pair: str, soglia_score: int) -> list:
    """Ritorna la lista degli esiti (in R) di tutti i trade simulati per questa coppia
    a questa soglia di score, sui dati 1h gia' preparati da prepara_serie()."""
    esiti = []
    ultimo_ingresso = None
    i = 60
    while i < len(df_1h) - 1:
        if ultimo_ingresso is not None:
            ore_da_ultimo = (df_1h.iloc[i]["time"] - df_1h.iloc[ultimo_ingresso]["time"]).total_seconds() / 3600
            if ore_da_ultimo < COOLDOWN_ORE:
                i += 1
                continue

        bias, score = valuta_candela(df_1h, i, pair)
        if bias is not None and score >= soglia_score:
            entry = df_1h.iloc[i]["close"]
            atr_val = df_1h.iloc[i]["atr"]
            if pd.notna(atr_val) and atr_val > 0:
                r = simula_trade(df_1h, i, bias, atr_val, entry)
                esiti.append(r)
                ultimo_ingresso = i
        i += 1

    return esiti


def main():
    coppie = coppie_da_testare()
    print(f"Backtest su {len(coppie)} coppie, {len(SOGLIE_DA_TESTARE)} soglie di score.")
    print(f"Parametri usati (presi da CONFIG attuale): SL={MULT_SL}x TP1={MULT_TP1}x "
          f"TP2={MULT_TP2}x TP3={MULT_TP3}x, finestra RSI={FINESTRA_RSI_CANDELE}, "
          f"finestra MACD={FINESTRA_MACD_CANDELE}\n")

    dati_per_coppia = {}
    for idx, pair in enumerate(coppie, 1):
        print(f"[{idx}/{len(coppie)}] Scarico {pair}...")
        df = prepara_serie(pair)
        dati_per_coppia[pair] = df
        time.sleep(0.3)  # non martellare l'API pubblica di Bitget

    giorni_coperti = None
    for df in dati_per_coppia.values():
        if df is not None:
            giorni_coperti = (df.iloc[-1]["time"] - df.iloc[0]["time"]).total_seconds() / 86400
            break

    righe_report = []
    for soglia in SOGLIE_DA_TESTARE:
        tutti_esiti = []
        for pair, df in dati_per_coppia.items():
            if df is None:
                continue
            tutti_esiti.extend(simula_su_soglia(df, pair, soglia))

        n = len(tutti_esiti)
        if n == 0:
            righe_report.append((soglia, 0, 0.0, 0.0, 0.0))
            continue
        vinti = sum(1 for r in tutti_esiti if r > 0)
        win_rate = vinti / n * 100
        ev_medio = float(np.mean(tutti_esiti))
        segnali_giorno = n / giorni_coperti if giorni_coperti else 0
        righe_report.append((soglia, n, win_rate, ev_medio, segnali_giorno))

    print("\n" + "=" * 70)
    print(f"RISULTATI ({giorni_coperti:.0f} giorni di storico coperti)" if giorni_coperti else "RISULTATI")
    print("=" * 70)
    print(f"{'Soglia':<8}{'Trade':<8}{'Win rate':<12}{'EV/trade (R)':<15}{'Segnali/giorno':<15}")
    for soglia, n, wr, ev, sg in righe_report:
        marcatore = " <-- attuale" if soglia == CONFIG.get("score_minimo") else ""
        print(f"{soglia:<8}{n:<8}{wr:<11.1f}%{ev:<+14.3f} {sg:<14.2f}{marcatore}")
    print("\nEV/trade (R) = risultato medio per trade, in multipli del rischio iniziale.")
    print("Positivo = il sistema avrebbe avuto un vantaggio statistico su questo storico.")
    print("Ricorda i LIMITI elencati in cima a questo file prima di trarre conclusioni definitive.")


if __name__ == "__main__":
    main()
