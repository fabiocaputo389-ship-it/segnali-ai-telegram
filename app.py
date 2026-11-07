<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro • Real Binance Live Engine</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] },
          colors: {
            obsidian: { 950: '#000000', 900: '#0a0a0a', 850: '#121212', 800: '#1a1a1a' },
            emeraldNeon: '#00FF66',
            rubyNeon: '#FF334B'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #ffffff; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    .terminal-card { background: linear-gradient(145deg, rgba(18, 18, 18, 0.96) 0%, rgba(10, 10, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.15); }
    .master-enter-section { background: radial-gradient(circle at top right, rgba(0, 255, 102, 0.08), rgba(10, 10, 10, 0.98) 70%); border: 1.5px solid rgba(255, 255, 255, 0.35); }
    .action-btn { background: #ffffff; color: #000000; font-weight: 800; }
    .action-btn:hover { background: #00FF66; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- HEADER -->
  <header class="sticky top-0 z-40 bg-[#000000]/95 backdrop-blur-md border-b border-white/20 px-3 sm:px-5 py-3">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-white flex items-center justify-center font-extrabold text-black">⚡</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold text-white">ApexScan <span class="text-[#00FF66]">Live Binance Engine</span></h1>
            <span id="apiStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00FF66]/15 text-[#00FF66] border border-[#00FF66]/40">
              <span class="w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping"></span> API DIRETTE ATTIVE
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-zinc-400 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>•</span>
            <span id="lastUpdateText">Ultimo aggiornamento: in corso...</span>
          </div>
        </div>
      </div>

      <!-- TIMEFRAME -->
      <div class="flex items-center bg-[#0a0a0a] p-1 rounded-xl border border-white/20" id="tfSelector">
        <span class="text-[10px] font-mono text-zinc-400 px-2">TF:</span>
        <button data-tf="15m" class="tf-btn px-2.5 py-1 rounded-lg text-xs font-mono text-zinc-400 hover:text-white">15m</button>
        <button data-tf="1h" class="tf-btn active px-2.5 py-1 rounded-lg text-xs font-mono font-bold bg-white/20 text-white border border-white/40">1H</button>
        <button data-tf="4h" class="tf-btn px-2.5 py-1 rounded-lg text-xs font-mono text-zinc-400 hover:text-white">4H</button>
      </div>

      <div class="flex items-center gap-2">
        <button onclick="fetchAllRealData(true)" class="action-btn px-3 py-1.5 rounded-xl text-xs transition-colors">↻ Sincronizza Ora</button>
      </div>
    </div>
  </header>

  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- SEZIONE SEGNALI REALI IN PRIMO PIANO -->
    <section class="master-enter-section rounded-2xl p-4 sm:p-5">
      <div class="flex flex-wrap items-center justify-between gap-2 pb-3 mb-4 border-b border-white/20">
        <div>
          <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">⚡ ENTRA ADESSO — SETUP CALCOLATI SU PREZZI REALI</h2>
          <p class="text-[11px] text-zinc-300 mt-0.5">Livelli di Entry, Stop Loss e Take Profit derivati direttamente dall'ATR(14) e dal trend in tempo reale dei book.</p>
        </div>
        <span id="activeSetupsCount" class="px-2.5 py-1 rounded-md text-xs font-mono font-bold bg-white/10 text-white border border-white/30">Caricamento...</span>
      </div>
      <div id="masterCardsGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4"></div>
    </section>

    <!-- SCREENER COMPLETO -->
    <section class="terminal-card rounded-2xl p-4">
      <div class="flex flex-wrap items-center justify-between gap-2 pb-3 mb-3 border-b border-white/20">
        <h2 class="text-sm font-bold text-white">Screener Live Multi-Asset</h2>
        <input type="text" id="searchInput" placeholder="Cerca ticker (es. BTC, ETH, SOL)..." class="bg-black border border-white/25 rounded-xl px-3 py-1.5 text-xs text-white outline-none focus:border-white w-full sm:w-64"/>
      </div>
      <div id="scannerGrid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3"></div>
    </section>

  </main>

  <!-- MODALE GRAFICO TRADINGVIEW + PARAMETRI -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-md p-2 sm:p-4 overflow-y-auto">
    <div class="bg-[#0a0a0a] border border-white/30 rounded-2xl w-full max-w-[1400px] shadow-2xl overflow-hidden my-auto">
      <div class="px-4 py-3 border-b border-white/20 flex items-center justify-between bg-black">
        <div class="flex items-center gap-2.5">
          <h2 id="modalSymbol" class="text-base sm:text-lg font-extrabold font-mono text-white">BTCUSDT</h2>
          <span id="modalDirectionBadge" class="px-2 py-0.5 rounded text-xs font-mono font-bold">LONG</span>
          <span id="modalPrice" class="text-base font-mono font-bold text-white ml-2">$0.00</span>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-[#121212] text-zinc-400 hover:text-white">✕</button>
      </div>

      <div class="p-4 grid grid-cols-1 lg:grid-cols-12 gap-4">
        <!-- WIDGET TRADINGVIEW UFFICIALE -->
        <div class="lg:col-span-8 bg-black rounded-xl border border-white/20 overflow-hidden h-[420px]" id="tvChartContainer"></div>

        <!-- DETTAGLIO LIVELLI REALI -->
        <div class="lg:col-span-4 flex flex-col justify-between gap-3">
          <div class="bg-black border border-white/25 rounded-xl p-3.5">
            <span class="text-[10px] font-bold uppercase text-zinc-400 block">Stato di Mercato Reale</span>
            <p id="modalAdviceText" class="text-xs font-bold text-white mt-1">--</p>
          </div>

          <div class="bg-black border border-white/25 rounded-xl p-3.5 space-y-2 font-mono text-xs">
            <div class="text-white font-sans font-bold text-xs pb-1 border-b border-white/20">Livelli Calcolati su ATR(14) Reale</div>
            <div class="flex justify-between p-2 rounded bg-white/10">
              <span class="text-zinc-300">Entry Reale:</span>
              <strong id="mEntry" class="text-white">--</strong>
            </div>
            <div class="flex justify-between p-2 rounded bg-[#FF334B]/15">
              <span class="text-[#FF334B]">Stop Loss (<span id="mSlPct">--</span>):</span>
              <strong id="mSl" class="text-[#FF334B]">--</strong>
            </div>
            <div class="flex justify-between p-2 rounded bg-[#00FF66]/15">
              <span class="text-[#00FF66]">Take Profit 1 (<span id="mTp1Pct">--</span>):</span>
              <strong id="mTp1" class="text-[#00FF66]">--</strong>
            </div>
            <div class="flex justify-between p-2 rounded bg-[#00FF66]/15">
              <span class="text-[#00FF66]">Take Profit 2 (<span id="mTp2Pct">--</span>):</span>
              <strong id="mTp2" class="text-[#00FF66]">--</strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const SYMBOLS = [
      { binance: 'BTCUSDT', tv: 'BINANCE:BTCUSDT', name: 'Bitcoin', decimals: 2 },
      { binance: 'ETHUSDT', tv: 'BINANCE:ETHUSDT', name: 'Ethereum', decimals: 2 },
      { binance: 'SOLUSDT', tv: 'BINANCE:SOLUSDT', name: 'Solana', decimals: 2 },
      { binance: 'XRPUSDT', tv: 'BINANCE:XRPUSDT', name: 'Ripple', decimals: 4 },
      { binance: 'BNBUSDT', tv: 'BINANCE:BNBUSDT', name: 'BNB', decimals: 2 },
      { binance: 'AVAXUSDT', tv: 'BINANCE:AVAXUSDT', name: 'Avalanche', decimals: 2 },
      { binance: 'LINKUSDT', tv: 'BINANCE:LINKUSDT', name: 'Chainlink', decimals: 3 },
      { binance: 'ADAUSDT', tv: 'BINANCE:ADAUSDT', name: 'Cardano', decimals: 4 }
    ];

    let state = {
      timeframe: '1h',
      assetsData: [],
      selectedAsset: null
    };

    function calcRSI(closes, period = 14) {
      if (closes.length <= period) return 50;
      let gains = 0, losses = 0;
      for (let i = 1; i <= period; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff >= 0) gains += diff; else losses -= diff;
      }
      let avgGain = gains / period, avgLoss = losses / period;
      for (let i = period + 1; i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        avgGain = (avgGain * (period - 1) + (diff >= 0 ? diff : 0)) / period;
        avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
      }
      if (avgLoss === 0) return 100;
      return 100 - (100 / (1 + (avgGain / avgLoss)));
    }

    function calcATR(candles, period = 14) {
      if (candles.length <= period) return candles[candles.length - 1].close * 0.01;
      let trs = [];
      for (let i = 1; i < candles.length; i++) {
        const h = candles[i].high, l = candles[i].low, prevC = candles[i - 1].close;
        trs.push(Math.max(h - l, Math.abs(h - prevC), Math.abs(l - prevC)));
      }
      return trs.slice(-period).reduce((a, b) => a + b, 0) / period;
    }

    async function fetchAllRealData(manual = false) {
      try {
        const promises = SYMBOLS.map(async (sym) => {
          const [tickerRes, klinesRes] = await Promise.all([
            fetch(`https://api.binance.com/api/v3/ticker/24hr?symbol=${sym.binance}`),
            fetch(`https://api.binance.com/api/v3/klines?symbol=${sym.binance}&interval=${state.timeframe}&limit=40`)
          ]);
          const ticker = await tickerRes.json();
          const klines = await klinesRes.json();

          const currentPrice = parseFloat(ticker.lastPrice);
          const change24h = parseFloat(ticker.priceChangePercent);
          const candles = klines.map(k => ({ open: parseFloat(k[1]), high: parseFloat(k[2]), low: parseFloat(k[3]), close: parseFloat(k[4]) }));
          const closes = candles.map(c => c.close);
          const rsi = calcRSI(closes, 14);
          const atr = calcATR(candles, 14);

          const ema20 = closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
          const lastC = candles[candles.length - 1];
          const prevC = candles[candles.length - 2];

          // Direzione reale guidata dal trend di mercato effettivo
          const isLong = currentPrice > ema20 && lastC.close >= prevC.close;

          const slDist = atr * 1.3;
          const entry = currentPrice;
          const sl = isLong ? entry - slDist : entry + slDist;
          const tp1 = isLong ? entry + (slDist * 1.5) : entry - (slDist * 1.5);
          const tp2 = isLong ? entry + (slDist * 2.8) : entry - (slDist * 2.8);

          const slPct = ((Math.abs(entry - sl) / entry) * 100).toFixed(2);
          const tp1Pct = ((Math.abs(tp1 - entry) / entry) * 100).toFixed(2);
          const tp2Pct = ((Math.abs(tp2 - entry) / entry) * 100).toFixed(2);

          const adviceText = isLong 
            ? `Mercato in momentum rialzista sopra EMA20 ($${ema20.toFixed(sym.decimals)}). RSI a ${rsi.toFixed(1)}.`
            : `Pressione ribassista sotto EMA20 ($${ema20.toFixed(sym.decimals)}). RSI a ${rsi.toFixed(1)}.`;

          return { ...sym, currentPrice, change24h, rsi, atr, isLong, entry, sl, tp1, tp2, slPct, tp1Pct, tp2Pct, adviceText };
        });

        state.assetsData = await Promise.all(promises);
        document.getElementById('lastUpdateText').textContent = `Sincronizzato: ${new Date().toLocaleTimeString('it-IT')}`;
        renderUI();
      } catch (err) {
        console.error('Errore connessione API Binance:', err);
        document.getElementById('apiStatusBadge').textContent = '⚠ ERRORE RETE';
      }
    }

    function renderUI() {
      const search = document.getElementById('searchInput').value.toLowerCase();
      const filtered = state.assetsData.filter(a => a.binance.toLowerCase().includes(search) || a.name.toLowerCase().includes(search));

      document.getElementById('activeSetupsCount').textContent = `${state.assetsData.length} ASSET LIVE`;

      document.getElementById('masterCardsGrid').innerHTML = state.assetsData.slice(0, 3).map(a => `
        <div class="bg-black border border-white/30 rounded-2xl p-4 flex flex-col justify-between relative overflow-hidden">
          <div class="absolute top-0 left-0 w-1.5 h-full ${a.isLong ? 'bg-[#00FF66]' : 'bg-[#FF334B]'}"></div>
          <div>
            <div class="flex items-center justify-between pb-2 border-b border-white/20">
              <div class="flex items-center gap-2">
                <span class="font-mono font-extrabold text-white text-base">${a.binance}</span>
                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold ${a.isLong ? 'bg-[#00FF66]/15 text-[#00FF66]' : 'bg-[#FF334B]/15 text-[#FF334B]'}">${a.isLong ? 'LONG' : 'SHORT'}</span>
              </div>
              <span class="font-mono font-extrabold text-sm text-white">$${a.currentPrice.toFixed(a.decimals)}</span>
            </div>

            <div class="grid grid-cols-3 gap-2 font-mono text-center my-3 text-xs">
              <div class="bg-white/10 p-2 rounded-xl border border-white/20">
                <span class="text-[9px] text-zinc-300 block">Entry Reale</span>
                <strong class="text-white">$${a.entry.toFixed(a.decimals)}</strong>
              </div>
              <div class="bg-[#FF334B]/10 p-2 rounded-xl border border-[#FF334B]/30">
                <span class="text-[9px] text-[#FF334B] block">SL (-${a.slPct}%)</span>
                <strong class="text-[#FF334B]">$${a.sl.toFixed(a.decimals)}</strong>
              </div>
              <div class="bg-[#00FF66]/10 p-2 rounded-xl border border-[#00FF66]/30">
                <span class="text-[9px] text-[#00FF66] block">TP1 (+${a.tp1Pct}%)</span>
                <strong class="text-[#00FF66]">$${a.tp1.toFixed(a.decimals)}</strong>
              </div>
            </div>
          </div>
          <button onclick="openAssetModal('${a.binance}')" class="action-btn w-full mt-2 py-2 rounded-xl text-xs transition-colors">Apri Grafico Reale ↗</button>
        </div>
      `).join('');

      document.getElementById('scannerGrid').innerHTML = filtered.map(a => `
        <div onclick="openAssetModal('${a.binance}')" class="bg-black border border-white/20 hover:border-white rounded-xl p-3 flex flex-col justify-between gap-2.5 cursor-pointer transition-all">
          <div class="flex items-center justify-between border-b border-white/15 pb-2">
            <div>
              <div class="flex items-center gap-1.5">
                <span class="font-mono font-bold text-white text-sm">${a.binance}</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold ${a.isLong ? 'bg-[#00FF66]/15 text-[#00FF66]' : 'bg-[#FF334B]/15 text-[#FF334B]'}">${a.isLong ? 'LONG' : 'SHORT'}</span>
              </div>
              <span class="text-[10px] text-zinc-400">RSI: ${a.rsi.toFixed(1)}</span>
            </div>
            <div class="text-right font-mono">
              <div class="font-bold text-white text-xs">$${a.currentPrice.toFixed(a.decimals)}</div>
              <div class="text-[10px] ${a.change24h >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${a.change24h >= 0 ? '+' : ''}${a.change24h.toFixed(2)}%</div>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-1.5 font-mono text-[11px]">
            <div class="bg-[#FF334B]/10 p-1.5 rounded border border-[#FF334B]/25">
              <span class="text-[9px] text-[#FF334B] block">SL</span>
              <strong class="text-[#FF334B]">$${a.sl.toFixed(a.decimals)}</strong>
            </div>
            <div class="bg-[#00FF66]/10 p-1.5 rounded border border-[#00FF66]/25">
              <span class="text-[9px] text-[#00FF66] block">TP1</span>
              <strong class="text-[#00FF66]">$${a.tp1.toFixed(a.decimals)}</strong>
            </div>
          </div>
        </div>
      `).join('');
    }

    function openAssetModal(symbol) {
      const asset = state.assetsData.find(a => a.binance === symbol);
      if (!asset) return;
      state.selectedAsset = asset;

      document.getElementById('modalSymbol').textContent = asset.binance;
      document.getElementById('modalPrice').textContent = `$${asset.currentPrice.toFixed(asset.decimals)}`;
      const dirBadge = document.getElementById('modalDirectionBadge');
      dirBadge.textContent = asset.isLong ? 'LONG' : 'SHORT';
      dirBadge.className = `px-2 py-0.5 rounded text-xs font-mono font-bold ${asset.isLong ? 'bg-[#00FF66]/15 text-[#00FF66]' : 'bg-[#FF334B]/15 text-[#FF334B]'}`;

      document.getElementById('modalAdviceText').textContent = asset.adviceText;
      document.getElementById('mEntry').textContent = `$${asset.entry.toFixed(asset.decimals)}`;
      document.getElementById('mSlPct').textContent = `${asset.isLong ? '-' : '+'}${asset.slPct}%`;
      document.getElementById('mSl').textContent = `$${asset.sl.toFixed(asset.decimals)}`;
      document.getElementById('mTp1Pct').textContent = `${asset.isLong ? '+' : '-'}${asset.tp1Pct}%`;
      document.getElementById('mTp1').textContent = `$${asset.tp1.toFixed(asset.decimals)}`;
      document.getElementById('mTp2').textContent = `$${asset.tp2.toFixed(asset.decimals)}`;

      const tvInterval = state.timeframe === '15m' ? '15' : state.timeframe === '4h' ? '240' : '60';
      document.getElementById('tvChartContainer').innerHTML = `
        <iframe src="https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=${asset.tv}&interval=${tvInterval}&hidesidetoolbar=1&symboledit=0&saveimage=0&toolbarbg=000000&studies=RSI%40tv-basicstudies&theme=dark&style=1&timezone=Europe%2FRome"
          style="width: 100%; height: 100%; border: none;" allowtransparency="true" scrolling="no"></iframe>
      `;

      const modal = document.getElementById('assetModal');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }

    function closeModal() {
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
    }

    window.addEventListener('DOMContentLoaded', () => {
      fetchAllRealData(false);
      setInterval(() => fetchAllRealData(false), 10000); // Aggiornamento automatico ogni 10 secondi dai server reali

      setInterval(() => {
        document.getElementById('liveCetClock').textContent = new Date().toLocaleTimeString('it-IT', { timeZone: 'Europe/Rome' }) + ' CET';
      }, 1000);

      document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          document.querySelectorAll('.tf-btn').forEach(b => b.className = 'tf-btn px-2.5 py-1 rounded-lg text-xs font-mono text-zinc-400 hover:text-white');
          e.target.className = 'tf-btn active px-2.5 py-1 rounded-lg text-xs font-mono font-bold bg-white/20 text-white border border-white/40';
          state.timeframe = e.target.getAttribute('data-tf');
          fetchAllRealData(true);
        });
      });

      document.getElementById('searchInput').addEventListener('input', renderUI);
    });
  </script>
</body>
</html>
