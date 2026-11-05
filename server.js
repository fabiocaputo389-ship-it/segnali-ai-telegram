```javascript
/* 
===================================================================
  RACE CRYPTO SCANNER - WEB SERVICE (Node.js + Express)
  Backend per Scansione Crypto e Generazione Segnali
  Eseguibile su Render & GitHub
===================================================================
*/

const express = require('express');
const cors = require('cors');
const axios = require('axios');

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json());

// Lista Coppie Crypto da Scansionare
const SYMBOLS = [
    { symbol: 'BTCUSDT', name: 'BTC (Bitcoin)', isBig: true, top: true },
    { symbol: 'ETHUSDT', name: 'ETH (Ethereum)', isBig: true, top: true },
    { symbol: 'SOLUSDT', name: 'SOL (Solana)', isBig: true, top: true },
    { symbol: 'BNBUSDT', name: 'BNB (Binance Coin)', isBig: true, top: false },
    { symbol: 'XRPUSDT', name: 'XRP (Ripple)', isBig: true, top: false },
    { symbol: 'ADAUSDT', name: 'ADA (Cardano)', isBig: false, top: false },
    { symbol: 'DOGEUSDT', name: 'DOGE (Dogecoin)', isBig: true, top: true },
    { symbol: 'AVAXUSDT', name: 'AVAX (Avalanche)', isBig: false, top: false },
    { symbol: 'LINKUSDT', name: 'LINK (Chainlink)', isBig: false, top: false },
    { symbol: 'NEARUSDT', name: 'NEAR (Near Protocol)', isBig: false, top: false }
];

// Helper per Calcolo Indicatori Tecnici (RSI, EMA, ATR)
function calculateRSI(prices, period = 14) {
    let gains = 0, losses = 0;
    for (let i = 1; i <= period; i++) {
        const diff = prices[prices.length - i] - prices[prices.length - i - 1];
        if (diff >= 0) gains += diff;
        else losses -= diff;
    }
    let avgGain = gains / period;
    let avgLoss = losses / period;
    if (avgLoss === 0) return 100;
    const rs = avgGain / avgLoss;
    return Math.round(100 - (100 / (1 + rs)));
}

function calculateEMA(prices, period) {
    const k = 2 / (period + 1);
    let ema = prices[0];
    for (let i = 1; i < prices.length; i++) {
        ema = (prices[i] * k) + (ema * (1 - k));
    }
    return ema;
}

function calculateATR(highs, lows, closes, period = 14) {
    let trSum = 0;
    for (let i = closes.length - period; i < closes.length; i++) {
        const tr = Math.max(
            highs[i] - lows[i],
            Math.abs(highs[i] - closes[i - 1]),
            Math.abs(lows[i] - closes[i - 1])
        );
        trSum += tr;
    }
    return trSum / period;
}

function getDecimals(price) {
    if (price >= 1000) return 2;
    if (price >= 1) return 4;
    return 6;
}

// Rotta Principale API Segnali
app.get('/api/signals', async (req, res) => {
    try {
        const signalPromises = SYMBOLS.map(async (item) => {
            const url = `https://api.binance.com/api/v3/klines?symbol=${item.symbol}&interval=1h&limit=50`;
            const response = await axios.get(url);
            const data = response.data;

            const closes = data.map(d => parseFloat(d[4]));
            const highs = data.map(d => parseFloat(d[2]));
            const lows = data.map(d => parseFloat(d[3]));

            const currentPrice = closes[closes.length - 1];
            const prevPrice = closes[0];
            const change24h = (((currentPrice - prevPrice) / prevPrice) * 100).toFixed(2);

            const rsi = calculateRSI(closes, 14);
            const ema20 = calculateEMA(closes, 20);
            const ema50 = calculateEMA(closes, 50);
            const atr = calculateATR(highs, lows, closes, 14);

            let signalType = 'LONG';
            let score = 50;

            // Algoritmo di Confluenza
            if (currentPrice > ema20) score += 15;
            if (ema20 > ema50) score += 15;
            if (rsi > 45 && rsi < 68) score += 10;
            if (rsi < 30) score += 20;

            if (currentPrice < ema20) score -= 15;
            if (ema20 < ema50) score -= 15;
            if (rsi < 55 && rsi > 32) score -= 10;
            if (rsi > 70) score -= 20;

            if (score >= 50) {
                signalType = 'LONG';
            } else {
                signalType = 'SHORT';
                score = 100 - score;
            }

            score = Math.min(Math.max(Math.round(score), 65), 96);
            const isGoldDiamond = score >= 85;

            const atrMultiplier = atr > 0 ? atr : currentPrice * 0.015;
            let sl, tp1, tp2;

            if (signalType === 'LONG') {
                sl = currentPrice - (atrMultiplier * 1.5);
                tp1 = currentPrice + (atrMultiplier * 2.0);
                tp2 = currentPrice + (atrMultiplier * 4.0);
            } else {
                sl = currentPrice + (atrMultiplier * 1.5);
                tp1 = currentPrice - (atrMultiplier * 2.0);
                tp2 = currentPrice - (atrMultiplier * 4.0);
            }

            const decimals = getDecimals(currentPrice);

            return {
                ...item,
                price: currentPrice,
                change24h: parseFloat(change24h),
                signalType,
                score,
                isGoldDiamond,
                leverage: score > 88 ? '5x' : (score > 80 ? '3x' : '2x'),
                sl: sl.toFixed(decimals),
                tp1: tp1.toFixed(decimals),
                tp2: tp2.toFixed(decimals),
                slPercent: Math.abs((((sl - currentPrice) / currentPrice) * 100)).toFixed(2),
                tp1Percent: Math.abs((((tp1 - currentPrice) / currentPrice) * 100)).toFixed(2),
                tp2Percent: Math.abs((((tp2 - currentPrice) / currentPrice) * 100)).toFixed(2),
                timestamp: new Date().toISOString()
            };
        });

        const results = await Promise.all(signalPromises);
        res.json({
            status: 'success',
            count: results.length,
            data: results
        });
    } catch (error) {
        console.error("Errore server:", error.message);
        res.status(500).json({ status: 'error', message: 'Errore nel recupero dati di mercato' });
    }
});

// Rotta per verificare se il servizio è online (Health Check)
app.get('/health', (req, res) => {
    res.json({ status: 'OK', uptime: process.uptime() });
});

// Pagina di Benvenuto
app.get('/', (req, res) => {
    res.send('<h1>Crypto Race Scanner Web Service Active 🚀</h1><p>Usa l endpoint <code>/api/signals</code> per i dati live.</p>');
});

app.listen(PORT, () => {
    console.log(`Web Service avviato sulla porta ${PORT}`);
});
```
