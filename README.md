# Super Scanner

Scanner di trading per cripto (Bitget futures), azioni, indici e materie prime, con dashboard web.
Ogni segnale mostra **time frame esatto** (15m / 1h / 4h), **tempo utile di ingresso** con conto alla rovescia,
entrata, SL, TP1, TP2, leva consigliata e trigger (Breakout & Retest, Pullback su EMA20, ...).
Il track record è misurato sui segnali reali, non stimato.

Solo libreria standard di Python (3.8+): nessuna dipendenza da installare.

## Avvio locale / Termux
    python app.py --demo          # dati simulati
    python app.py --netcheck      # verifica quali fonti dati sono raggiungibili
    python app.py                 # modalità live, apri http://127.0.0.1:8000

## Render
1. New > Blueprint, scegli questo repository (usa `render.yaml`).
2. Imposta `DASH_PASSWORD` (utente: `admin`). Opzionali: `ANTHROPIC_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
3. Nei log, dopo il primo ciclo, controlla la riga `Fonti dopo il primo ciclo`.

Piano gratuito: il servizio si sospende dopo 15 minuti senza visite e lo storico si azzera. Per 24/7 e disco persistente serve un piano a pagamento.

Non scrivere mai chiavi o password nel codice: solo nelle variabili d'ambiente di Render.

Strumento di analisi, non consulenza finanziaria. Il trading con leva può far perdere l'intero capitale.
