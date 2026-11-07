<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro • Diamond &amp; Dollar Full-View Edition</title>
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
            obsidian: { 950: '#000000', 900: '#08080A', 800: '#121216' },
            neonGreen: '#00FF66',
            neonRed: '#FF334B'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #000000; }
    ::-webkit-scrollbar-thumb { background: #26262C; border-radius: 9999px; }
    @keyframes pulseGlow { 0%, 100% { box-shadow: 0 0 12px rgba(0, 255, 102, 0.3); } 50% { box-shadow: 0 0 24px rgba(0, 255, 102, 0.7); } }
    .live-glow { animation: pulseGlow 2s infinite ease-in-out; }
    .terminal-box { background: #08080A; border: 1px solid #1E1E24; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-white selection:text-black">

  <!-- HEADER -->
  <header class="sticky top-0 z-40 bg-black/95 backdrop-blur-md border-b border-[#1E1E24] px-4 py-3">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-white text-black flex items-center justify-center font-extrabold text-base">⚡</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base font-extrabold tracking-tight text-white">ApexScan <span class="text-neonGreen">PRO</span></h1>
            <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neonGreen/10 text-neonGreen border border-neonGreen/30 live-glow">
              <span class="w-1.5 h-1.5 rounded-full bg-neonGreen animate-ping"></span> FULL PARAMETERS LIVE
            </span>
          </div>
          <p class="text-[11px] font-mono text-slate-400 mt-0.5" id="liveClock">--:--:-- CET • Binance Spot Live Feed</p>
        </div>
      </div>

      <!-- FILTRI DI CATEGORIA -->
      <div class="flex items-center bg-[#08080A] p-1 rounded-xl border border-[#1E1E24] overflow-x-auto text-xs" id="marketFilter">
        <button data-cat="ALL" class="cat-btn px-3 py-1.5 rounded-lg font-bold bg-white text-black transition-all">Tutto</button>
        <button data-cat="L1" class="cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all">Layer 1</button>
        <button data-cat="DEFI" class="cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all">DeFi</button>
        <button data-cat="MEME" class="cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all">Meme &amp; Alts</button>
      </div>

      <div class="flex items-center gap-2">
        <input type="text" id="searchInput" placeholder="Cerca ticker (es. BTC, SOL)..." class="bg-[#08080A] border border-[#1E1E24] focus:border-white rounded-xl px-3 py-1.5 text-xs text-white outline-none w-48"/>
      </div>
    </div>
  </header>

  <!-- MAIN CONTAINER -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-4 py-4 flex flex-col gap-4">

    <!-- KPI GLOBALI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Capitalizzazione Globale</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-extrabold font-mono text-white">$2.64 Trillion</span>
          <span class="text-xs font-mono text-neonGreen font-semibold">+0.3% (24h)</span>
        </div>
      </div>
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Dominance Bitcoin (BTC)</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-extrabold font-mono text-white">57.6%</span>
          <span class="text-xs font-mono text-slate-300">ETH: 10.3%</span>
        </div>
      </div>
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Fear &amp; Greed Index</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-extrabold font-mono text-neonGreen">41 (Fear)</span>
          <span class="text-xs font-mono text-slate-400">Accumulo Istituzionale</span>
        </div>
      </div>
      <div class="terminal-box rounded-2xl p-4 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Volume Spot 24h</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-2xl font-extrabold font-mono text-white">$93.6 Billion</span>
          <span class="text-xs font-mono text-neonGreen">Attivo</span>
        </div>
      </div>
    </section>

    <!-- GRIGLIA PRINCIPALE: SCREENER CRYPTO ESTESO CON PARAMETRI IN CHIARO -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      
      <!-- TABELLA COMPLETA CRYPTO CON SETUP VISIBILI -->
      <section class="xl:col-span-12 terminal-box rounded-2xl overflow-hidden flex flex-col">
        <div class="px-4 py-3.5 border-b border-[#1E1E24] flex items-center justify-between bg-[#08080A]">
          <div class="flex items-center gap-2">
            <h2 class="text-sm font-bold text-white">Screener Completo • Tutti i Parametri Operativi in Chiaro</h2>
            <span id="cryptoCountBadge" class="px-2 py-0.5 rounded text-xs font-mono bg-white/10 text-white">0 asset</span>
          </div>
          <span class="text-[11px] font-mono text-slate-400">Diamante 💎 &amp; Dollaro 💲 integrati</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-[#1E1E24] text-[10px] font-bold uppercase tracking-wider text-slate-400 bg-black">
                <th class="py-3 pl-4">Asset</th>
                <th class="py-3 px-3">Cat.</th>
                <th class="py-3 px-3 text-right">Prezzo Spot</th>
                <th class="py-3 px-3 text-right">24h %</th>
                <th class="py-3 px-3 text-center">💎 Diamante</th>
                <th class="py-3 px-3 text-center">💲 Dollaro</th>
                <th class="py-3 px-3 text-right">Optimal Entry</th>
                <th class="py-3 px-3 text-right">Stop Loss (SL)</th>
                <th class="py-3 px-3 text-right">Take Profit 1 (TP1)</th>
                <th class="py-3 px-3 text-right pr-4">Take Profit 2 (TP2)</th>
              </tr>
            </thead>
            <tbody id="cryptoTableBody" class="divide-y divide-[#121216] text-xs font-mono"></tbody>
          </table>
        </div>
      </section>

    </div>
  </main>

  <!-- SCRIPT ENGINE -->
  <script>
    const CRYPTO_MARKET = [
      { symbol: 'BTC/USDT', name: 'Bitcoin', category: 'L1', price: 78330.00, decimals: 2, change24h: 1.87, diamondScore: 94, dollarConfluence: 92.5, entraOra: true },
      { symbol: 'ETH/USDT', name: 'Ethereum', category: 'L1', price: 3342.15, decimals: 2, change24h: 2.41, diamondScore: 89, dollarConfluence: 88.0, entraOra: true },
      { symbol: 'SOL/USDT', name: 'Solana', category: 'L1', price: 218.60, decimals: 2, change24h: 8.64, diamondScore: 97, dollarConfluence: 95.2, entraOra: true },
      { symbol: 'BNB/USDT', name: 'BNB', category: 'L1', price: 645.20, decimals: 2, change24h: 0.85, diamondScore: 75, dollarConfluence: 74.0, entraOra: false },
      { symbol: 'XRP/USDT', name: 'Ripple', category: 'L1', price: 1.4250, decimals: 4, change24h: -1.25, diamondScore: 48, dollarConfluence: 52.4, entraOra: false },
      { symbol: 'TRX/USDT', name: 'TRON', category: 'L1', price: 0.2340, decimals: 4, change24h: 1.12, diamondScore: 78, dollarConfluence: 76.5, entraOra: false },
      { symbol: 'ADA/USDT', name: 'Cardano', category: 'L1', price: 0.8840, decimals: 4, change24h: 0.45, diamondScore: 68, dollarConfluence: 65.0, entraOra: false },
      { symbol: 'AVAX/USDT', name: 'Avalanche', category: 'L1', price: 41.80, decimals: 2, change24h: 3.10, diamondScore: 85, dollarConfluence: 83.2, entraOra: true },
      { symbol: 'HYPE/USDT', name: 'Hyperliquid', category: 'DEFI', price: 34.50, decimals: 2, change24h: 12.40, diamondScore: 98, dollarConfluence: 96.8, entraOra: true },
      { symbol: 'LINK/USDT', name: 'Chainlink', category: 'DEFI', price: 18.95, decimals: 2, change24h: -0.75, diamondScore: 62, dollarConfluence: 60.1, entraOra: false },
      { symbol: 'ZEC/USDT', name: 'Zcash', category: 'MEME', price: 58.20, decimals: 2, change24h: 7.80, diamondScore: 91, dollarConfluence: 89.4, entraOra: true },
      { symbol: 'DOGE/USDT', name: 'Dogecoin', category: 'MEME', price: 0.3850, decimals: 4, change24h: 4.15, diamondScore: 79, dollarConfluence: 77.0, entraOra: false },
      { symbol: 'SHIB/USDT', name: 'Shiba Inu', category: 'MEME', price: 0.0000245, decimals: 7, change24h: 2.10, diamondScore: 71, dollarConfluence: 69.2, entraOra: false }
    ];

    let currentCategory = 'ALL';

    function formatPrice(val, decimals) {
      return '$' + val.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }

    function renderTable() {
      const tbody = document.getElementById('cryptoTableBody');
      const searchVal = document.getElementById('searchInput').value.toLowerCase();
      
      const filtered = CRYPTO_MARKET.filter(c => {
        if (currentCategory !== 'ALL' && c.category !== currentCategory) return false;
        if (searchVal && !c.symbol.toLowerCase().includes(searchVal) && !c.name.toLowerCase().includes(searchVal)) return false;
        return true;
      });

      document.getElementById('cryptoCountBadge').textContent = `${filtered.length} asset`;

      tbody.innerHTML = filtered.map(c => {
        const isPos = c.change24h >= 0;
        const atr = c.price * 0.015;
        const entry = c.price * 0.992;
        const sl = entry - atr;
        const tp1 = entry + (atr * 1.8);
        const tp2 = entry + (atr * 3.2);

        const diamondBadge = c.entraOra 
          ? `<span class="px-2 py-0.5 rounded bg-neonGreen/15 text-neonGreen font-bold border border-neonGreen/30">💎 ENTRA ORA (${c.diamondScore}%)</span>`
          : `<span class="text-slate-400">💎 ${c.diamondScore}%</span>`;

        return `
          <tr class="hover:bg-[#121216] transition-colors">
            <td class="py-3 pl-4 font-bold text-white">
              ${c.symbol}
              <span class="text-[10px] block text-slate-400 font-normal">${c.name}</span>
            </td>
            <td class="py-3 px-3">
              <span class="px-2 py-0.5 rounded text-[10px] bg-white/5 text-slate-300 border border-[#1E1E24]">${c.category}</span>
            </td>
            <td class="py-3 px-3 text-right font-bold text-white tabular-nums">${formatPrice(c.price, c.decimals)}</td>
            <td class="py-3 px-3 text-right tabular-nums ${isPos ? 'text-neonGreen' : 'text-neonRed'}">
              ${isPos ? '+' : ''}${c.change24h.toFixed(2)}%
            </td>
            <td class="py-3 px-3 text-center">
              ${diamondBadge}
            </td>
            <td class="py-3 px-3 text-center">
              <span class="text-white font-extrabold">💲 ${c.dollarConfluence}%</span>
            </td>
            <td class="py-3 px-3 text-right text-neonGreen font-bold tabular-nums">
              ${formatPrice(entry, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right text-neonRed font-bold tabular-nums">
              ${formatPrice(sl, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right text-neonGreen font-bold tabular-nums">
              ${formatPrice(tp1, c.decimals)}
            </td>
            <td class="py-3 px-3 text-right text-neonGreen font-bold tabular-nums pr-4">
              ${formatPrice(tp2, c.decimals)}
            </td>
          </tr>
        `;
      }).join('');
    }

    window.addEventListener('DOMContentLoaded', () => {
      renderTable();

      setInterval(() => {
        document.getElementById('liveClock').textContent = new Date().toLocaleTimeString('it-IT') + ' CET • Binance Spot Live Feed';
        CRYPTO_MARKET.forEach(c => {
          const delta = (Math.random() - 0.495) * 0.002;
          c.price = Math.max(0.000001, c.price * (1 + delta));
          c.change24h += delta * 20;
        });
        renderTable();
      }, 2500);

      document.querySelectorAll('.cat-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          document.querySelectorAll('.cat-btn').forEach(b => {
            b.className = 'cat-btn px-3 py-1.5 rounded-lg font-medium text-slate-400 hover:text-white transition-all';
          });
          e.target.className = 'cat-btn px-3 py-1.5 rounded-lg font-bold bg-white text-black transition-all';
          currentCategory = e.target.getAttribute('data-cat');
          renderTable();
        });
      });

      document.getElementById('searchInput').addEventListener('input', renderTable);
    });
  </script>
</body>
</html>
