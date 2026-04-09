// Trading Bot Dashboard - Client-side JS

const API = '';
let selectedPair = 'EURUSD';
let assetRegistry = {};
let btPairFilter = '';  // '' = All pairs for Backtests tab

const fmtPnl = v => '\u00a3' + Math.round(+v).toLocaleString('en-GB');

function priceDecimals() {
    return (assetRegistry[selectedPair] || {}).price_decimals || 5;
}

// Load asset registry and build pair selector
async function loadAssets() {
    try {
        const res = await fetch(`${API}/api/assets`);
        const data = await res.json();
        assetRegistry = data.assets;
        selectedPair = data.default;

        // Build pair selector if container exists
        const container = document.getElementById('pair-selector');
        if (container) {
            let html = '';
            for (const [name, spec] of Object.entries(data.assets)) {
                if (!spec.active) continue;
                const cls = name === selectedPair ? 'tf-btn active' : 'tf-btn';
                html += `<button class="${cls}" data-pair="${name}">${name}</button>`;
            }
            container.innerHTML = html;

            // Attach click handlers
            container.querySelectorAll('[data-pair]').forEach(btn => {
                btn.addEventListener('click', () => {
                    container.querySelectorAll('[data-pair]').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    selectedPair = btn.dataset.pair;
                    loadLivePrice();
                    loadStatus();
                    // Reload active tab data
                    const activeTab = document.querySelector('.tab.active')?.dataset.tab;
                    if (activeTab) loadTabData(activeTab);
                });
            });
        }
    } catch(e) {
        console.error('Assets load failed:', e);
    }
}

// Tab switching
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.tab).classList.add('active');

        // Load data for the active tab
        loadTabData(tab.dataset.tab);
    });
});

// Load data based on active tab
function loadTabData(tab) {
    switch(tab) {
        case 'overview': loadOverview(); break;
        case 'trades': populateTradeFilter().then(loadTrades); break;
        case 'chart': setTimeout(loadChart, 50); break;
        case 'backtest': loadBacktests(); break;
        case 'research': loadResearch(); break;
        case 'compare': loadComparisons(); break;
        case 'learning': loadLearning(); break;
        case 'data': loadDataHealth(); break;
    }
}

// Live price ticker
async function loadLivePrice() {
    try {
        const res = await fetch(`${API}/api/price?pair=${selectedPair}`);
        const data = await res.json();
        if (data.mid) {
            const asset = assetRegistry[selectedPair] || {};
            const decimals = asset.price_decimals || 5;
            const pipMult = 1 / (asset.pip_value || 0.0001);
            document.getElementById('live-price').textContent = `${selectedPair}: ${data.mid.toFixed(decimals)}`;
            document.getElementById('live-price').title = `Bid: ${data.bid.toFixed(decimals)} | Ask: ${data.ask.toFixed(decimals)} | Spread: ${(data.spread * pipMult).toFixed(1)} pips`;
        }
    } catch(e) {}
}

// Status bar
async function loadStatus() {
    try {
        const res = await fetch(`${API}/api/status?pair=${selectedPair}`);
        const data = await res.json();
        document.getElementById('capital').textContent = `Capital: \u00a3${data.capital.toLocaleString()}`;

        const pnlEl = document.getElementById('pnl');
        pnlEl.textContent = `P&L: \u00a3${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl.toLocaleString()}`;
        pnlEl.className = data.total_pnl >= 0 ? 'positive' : 'negative';

        if (data.live_price) {
            const decimals = (assetRegistry[selectedPair] || {}).price_decimals || 5;
            document.getElementById('live-price').textContent = `${selectedPair}: ${data.live_price.toFixed(decimals)}`;
        }

        document.getElementById('open-pos').textContent = `Open: ${data.open_positions}`;
        document.getElementById('signals-today').textContent = `Signals: ${data.signals_today}`;
        document.getElementById('last-update').textContent = `Updated: ${new Date().toLocaleTimeString()}`;
    } catch(e) {
        console.error('Status load failed:', e);
    }
}

// OANDA account summary
async function loadOandaAccount() {
    try {
        const res = await fetch(`${API}/api/account`);
        const data = await res.json();
        if (data.error) {
            document.getElementById('oanda-account').innerHTML = `<p class="neutral">${data.error}</p>`;
            return;
        }
        const unrealizedClass = data.unrealized_pnl >= 0 ? 'positive' : 'negative';
        const stats = [
            ['Balance', `\u00a3${data.balance.toLocaleString()}`],
            ['NAV', `\u00a3${data.nav.toLocaleString()}`],
            ['Unrealized P&L', `\u00a3${data.unrealized_pnl >= 0 ? '+' : ''}${data.unrealized_pnl.toFixed(2)}`],
            ['Margin Used', `\u00a3${data.margin_used.toFixed(2)}`],
            ['Margin Available', `\u00a3${data.margin_available.toLocaleString()}`],
            ['Open Trades', data.open_trades],
        ];
        let html = '';
        for (const [label, value] of stats) {
            const cls = label === 'Unrealized P&L' ? unrealizedClass : '';
            html += `<div class="stat-row"><span class="stat-label">${label}</span><span class="stat-value ${cls}">${value}</span></div>`;
        }
        document.getElementById('oanda-account').innerHTML = html;
    } catch(e) {
        document.getElementById('oanda-account').innerHTML = '<p class="neutral">OANDA not connected</p>';
    }
}

// Overview tab
async function loadOverview() {
    loadStatus();
    loadLivePrice();
    loadOandaAccount();
    loadEquityCurve();
    loadRecentSignals();
    loadOpenPositions();
}

async function loadEquityCurve() {
    try {
        const res = await fetch(`${API}/api/equity?pair=${selectedPair}`);
        const data = await res.json();

        if (data.length < 2) {
            document.getElementById('equity-chart').innerHTML = '<p class="neutral">No trade data yet</p>';
            return;
        }

        const trace = {
            x: data.map(d => d.date).filter(d => d),
            y: data.map(d => d.equity),
            type: 'scatter',
            fill: 'tozeroy',
            fillcolor: 'rgba(96, 165, 250, 0.1)',
            line: { color: '#60a5fa', width: 2 },
        };

        Plotly.newPlot('equity-chart', [trace], {
            paper_bgcolor: '#111827',
            plot_bgcolor: '#111827',
            font: { color: '#94a3b8', size: 11 },
            margin: { t: 10, r: 20, b: 40, l: 60 },
            xaxis: { gridcolor: '#1e293b' },
            yaxis: { gridcolor: '#1e293b', tickprefix: '\u00a3' },
        }, { responsive: true, displayModeBar: false });
    } catch(e) {
        console.error('Equity load failed:', e);
    }
}

async function loadRecentSignals() {
    try {
        const res = await fetch(`${API}/api/signals?limit=5&pair=${selectedPair}`);
        const signals = await res.json();

        if (!signals.length) {
            document.getElementById('recent-signals').innerHTML = '<p class="neutral">No signals yet</p>';
            return;
        }

        let html = '';
        for (const s of signals) {
            const dirClass = s.direction === 'long' ? 'badge-long' : 'badge-short';
            const scoreColor = s.confluence_score >= 0.7 ? '#34d399' : s.confluence_score >= 0.5 ? '#fbbf24' : '#f87171';
            html += `
                <div class="signal-card">
                    <div class="signal-header">
                        <span class="badge ${dirClass}">${s.direction}</span>
                        <span style="font-size:12px;color:#94a3b8">${s.signal_type}</span>
                    </div>
                    <div style="font-size:13px">
                        Entry: ${s.entry_price.toFixed(priceDecimals())} | SL: ${s.stop_loss.toFixed(priceDecimals())} | TP: ${s.take_profit.toFixed(priceDecimals())}
                    </div>
                    <div class="score-bar">
                        <div class="score-fill" style="width:${s.confluence_score*100}%;background:${scoreColor}"></div>
                    </div>
                    <div style="font-size:11px;color:#94a3b8;margin-top:4px">
                        Score: ${(s.confluence_score*100).toFixed(0)}% | ${new Date(s.timestamp).toLocaleString()}
                    </div>
                </div>`;
        }
        document.getElementById('recent-signals').innerHTML = html;
    } catch(e) {
        console.error('Signals load failed:', e);
    }
}

async function loadOpenPositions() {
    try {
        const res = await fetch(`${API}/api/trades?limit=10&pair=${selectedPair}`);
        const trades = await res.json();
        const open = trades.filter(t => t.status === 'open');

        if (!open.length) {
            document.getElementById('open-positions').innerHTML = '<p class="neutral">No open positions</p>';
            return;
        }

        let html = '';
        for (const t of open) {
            const dirClass = t.direction === 'long' ? 'badge-long' : 'badge-short';
            html += `
                <div class="signal-card">
                    <div class="signal-header">
                        <span class="badge ${dirClass}">${t.direction}</span>
                        <span style="font-size:12px">Size: ${t.size.toLocaleString()}</span>
                    </div>
                    <div style="font-size:13px">
                        Entry: ${t.entry_price.toFixed(priceDecimals())} | SL: ${t.stop_loss?.toFixed(priceDecimals()) || '--'} | TP: ${t.take_profit?.toFixed(priceDecimals()) || '--'}
                    </div>
                    <div style="font-size:11px;color:#94a3b8;margin-top:4px">
                        Risk: \u00a3${t.risk_amount ? t.risk_amount.toFixed(2) : '--'} | ${new Date(t.opened_at).toLocaleString()}
                    </div>
                </div>`;
        }
        document.getElementById('open-positions').innerHTML = html;
    } catch(e) {
        console.error('Open positions load failed:', e);
    }
}

async function loadAccountSummary() {
    try {
        const res = await fetch(`${API}/api/status?pair=${selectedPair}`);
        const data = await res.json();

        const stats = [
            ['Starting Capital', `\u00a3${data.starting_capital.toLocaleString()}`],
            ['Current Capital', `\u00a3${data.capital.toLocaleString()}`],
            ['Total P&L', `\u00a3${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl.toLocaleString()}`],
            ['Return', `${((data.total_pnl / data.starting_capital) * 100).toFixed(2)}%`],
            ['Total Trades', data.closed_positions],
            ['Open Positions', data.open_positions],
        ];

        let html = '';
        for (const [label, value] of stats) {
            const cls = label === 'Total P&L' ? (data.total_pnl >= 0 ? 'positive' : 'negative') : '';
            html += `<div class="stat-row"><span class="stat-label">${label}</span><span class="stat-value ${cls}">${value}</span></div>`;
        }
        document.getElementById('account-summary').innerHTML = html;
    } catch(e) {
        document.getElementById('account-summary').innerHTML = '<p class="neutral">Loading...</p>';
    }
}

// Trades tab
function formatDuration(mins) {
    if (mins == null) return '--';
    if (mins < 60) return `${mins}m`;
    if (mins < 1440) return `${Math.floor(mins/60)}h ${mins%60}m`;
    const days = Math.floor(mins / 1440);
    const hours = Math.floor((mins % 1440) / 60);
    return `${days}d ${hours}h`;
}

async function populateTradeFilter() {
    try {
        const res = await fetch(`${API}/api/backtests?pair=${selectedPair}`);
        const runs = await res.json();
        const sel = document.getElementById('trade-bt-filter');
        if (!sel) return;

        const currentVal = sel.value;
        sel.innerHTML = '<option value="live">Live Trades</option>';
        for (const r of runs) {
            if (!r.total_trades) continue;
            const start = new Date(r.start_date).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'2-digit'});
            const end = new Date(r.end_date).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'2-digit'});
            const label = `BT#${r.id} ${r.config} | ${start}-${end} | ${r.total_trades}T ${(r.win_rate*100).toFixed(0)}%WR`;
            sel.innerHTML += `<option value="bt_${r.id}">${label}</option>`;
        }
        // Restore previous selection if still valid
        if (currentVal && [...sel.options].some(o => o.value === currentVal)) {
            sel.value = currentVal;
        }
    } catch(e) { console.error('populateTradeFilter failed:', e); }
}

async function loadTrades() {
    try {
        const sel = document.getElementById('trade-bt-filter');
        const val = sel ? sel.value : 'live';

        let url;
        if (!val || val === 'live') {
            url = `${API}/api/trades?source=live&pair=${selectedPair}`;
        } else {
            const btId = val.replace('bt_', '');
            url = `${API}/api/trades?bt_id=${btId}&pair=${selectedPair}`;
        }
        const res = await fetch(url);
        const trades = await res.json();

        // Summary stats
        const closed = trades.filter(t => t.status === 'closed');
        const wins = closed.filter(t => t.outcome === 'win');
        const losses = closed.filter(t => t.outcome === 'loss');
        const totalPnl = closed.reduce((s, t) => s + (t.pnl || 0), 0);
        const winRate = closed.length > 0 ? (wins.length / closed.length * 100).toFixed(1) : '0.0';

        // Average RR
        const rrValues = closed.filter(t => t.actual_rr != null).map(t => t.actual_rr);
        const avgRR = rrValues.length > 0 ? (rrValues.reduce((a, b) => a + b, 0) / rrValues.length) : 0;
        const avgWinRR = wins.filter(t => t.actual_rr != null).length > 0
            ? wins.filter(t => t.actual_rr != null).reduce((s, t) => s + t.actual_rr, 0) / wins.filter(t => t.actual_rr != null).length : 0;
        const avgLossRR = losses.filter(t => t.actual_rr != null).length > 0
            ? losses.filter(t => t.actual_rr != null).reduce((s, t) => s + t.actual_rr, 0) / losses.filter(t => t.actual_rr != null).length : 0;

        const summaryEl = document.getElementById('trade-summary');
        if (summaryEl) {
            const pnlClass = totalPnl >= 0 ? 'positive' : 'negative';
            summaryEl.innerHTML = `
                <strong>${closed.length}</strong> trades |
                <span class="positive">${wins.length}W</span> /
                <span class="negative">${losses.length}L</span> |
                WR: <strong>${winRate}%</strong> |
                Avg RR: <strong>${avgRR >= 0 ? '+' : ''}${avgRR.toFixed(2)}R</strong>
                (W: +${avgWinRR.toFixed(2)}R / L: ${avgLossRR.toFixed(2)}R) |
                P&L: <span class="${pnlClass}"><strong>${fmtPnl(totalPnl)}</strong></span>`;
        }

        let html = '';
        for (const t of trades) {
            const dirClass = t.direction === 'long' ? 'badge-long' : 'badge-short';
            const pnlClass = (t.pnl || 0) >= 0 ? 'positive' : 'negative';

            // Result badge
            let resultBadge = '--';
            if (t.outcome === 'win') resultBadge = '<span class="badge badge-win">WIN</span>';
            else if (t.outcome === 'loss') resultBadge = '<span class="badge badge-loss">LOSS</span>';
            else if (t.outcome === 'breakeven') resultBadge = '<span class="badge badge-be">BE</span>';
            else if (t.status === 'open') resultBadge = '<span class="badge badge-open">OPEN</span>';

            // Source tag
            const isBacktest = t.tags && t.tags.includes('backtest');
            const sourceTag = isBacktest ? '<span class="badge badge-bt">BT</span>' : '<span class="badge badge-live">LIVE</span>';

            html += `<tr>
                <td>${t.id}</td>
                <td>${resultBadge}</td>
                <td><span class="badge ${dirClass}">${t.direction}</span></td>
                <td>${t.signal_type || '--'}</td>
                <td>${t.entry_price.toFixed(priceDecimals())}</td>
                <td>${t.exit_price ? t.exit_price.toFixed(priceDecimals()) : '--'}</td>
                <td>${t.stop_loss ? t.stop_loss.toFixed(priceDecimals()) : '--'}</td>
                <td>${t.take_profit ? t.take_profit.toFixed(priceDecimals()) : '--'}</td>
                <td>${t.planned_rr != null ? t.planned_rr.toFixed(1) + ':1' : '--'}</td>
                <td class="${pnlClass}">${t.actual_rr != null ? (t.actual_rr >= 0 ? '+' : '') + t.actual_rr.toFixed(2) + 'R' : '--'}</td>
                <td class="${pnlClass}">${t.pnl_pips != null ? t.pnl_pips.toFixed(1) : '--'}</td>
                <td class="${pnlClass}">${t.pnl != null ? fmtPnl(t.pnl) : '--'}</td>
                <td>${t.risk_amount ? '\u00a3' + t.risk_amount.toFixed(2) : '--'}</td>
                <td>${t.confluence_score ? (t.confluence_score * 100).toFixed(0) + '%' : '--'}</td>
                <td>${formatDuration(t.duration_mins)}</td>
                <td>${t.opened_at ? new Date(t.opened_at).toLocaleDateString() : '--'}</td>
                <td>${t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '--'}</td>
                <td>${sourceTag}</td>
            </tr>`;
        }
        document.getElementById('trades-body').innerHTML = html || '<tr><td colspan="18" class="neutral">No trades yet</td></tr>';
    } catch(e) {
        console.error('Trades load failed:', e);
    }
}

// Trade filter dropdown
document.getElementById('trade-bt-filter')?.addEventListener('change', loadTrades);

// Chart tab
let selectedTf = '4h';

document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        selectedTf = btn.dataset.tf;
        loadChart();
    });
});

async function loadChart() {
    const tf = selectedTf;
    try {
        const res = await fetch(`${API}/api/candles/${tf}?limit=200&pair=${selectedPair}`);
        const candles = await res.json();

        if (!candles.length) {
            document.getElementById('price-chart').innerHTML = '<p class="neutral">No candle data. Run: python main.py fetch</p>';
            return;
        }

        const trace = {
            x: candles.map(c => c.timestamp),
            open: candles.map(c => c.open),
            high: candles.map(c => c.high),
            low: candles.map(c => c.low),
            close: candles.map(c => c.close),
            type: 'candlestick',
            increasing: { line: { color: '#34d399' }, fillcolor: '#065f46' },
            decreasing: { line: { color: '#f87171' }, fillcolor: '#7f1d1d' },
        };

        const chartEl = document.getElementById('price-chart');
        chartEl.innerHTML = '';
        Plotly.newPlot(chartEl, [trace], {
            paper_bgcolor: '#111827',
            plot_bgcolor: '#0a0e17',
            font: { color: '#94a3b8', size: 11 },
            margin: { t: 20, r: 40, b: 40, l: 60 },
            xaxis: {
                gridcolor: '#1e293b',
                rangeslider: { visible: false },
            },
            yaxis: { gridcolor: '#1e293b', side: 'right' },
            dragmode: 'pan',
        }, { responsive: true, scrollZoom: true });
    } catch(e) {
        console.error('Chart load failed:', e);
    }
}

// Backtest tab — local pair filter (independent of global selectedPair)
function initBtPairFilter() {
    const container = document.getElementById('bt-pair-filter');
    if (!container || container.dataset.built) return;
    container.dataset.built = '1';

    const pairs = Object.keys(assetRegistry).filter(k => assetRegistry[k].active);
    let html = `<button class="tf-btn${btPairFilter === '' ? ' active' : ''}" data-btpair="">All</button>`;
    for (const p of pairs) {
        html += `<button class="tf-btn${btPairFilter === p ? ' active' : ''}" data-btpair="${p}">${p}</button>`;
    }
    container.innerHTML = html;
    container.querySelectorAll('[data-btpair]').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('[data-btpair]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            btPairFilter = btn.dataset.btpair;
            loadBacktests();
        });
    });
}

async function loadBacktests() {
    initBtPairFilter();
    try {
        const res = await fetch(`${API}/api/backtests?pair=${btPairFilter}`);
        const runs = await res.json();

        let html = '';
        for (const r of runs) {
            const pnlClass = r.total_pnl >= 0 ? 'positive' : 'negative';
            const longWr = r.long_trades ? ((r.long_wins / r.long_trades) * 100).toFixed(0) : '-';
            const shortWr = r.short_trades ? ((r.short_wins / r.short_trades) * 100).toFixed(0) : '-';
            const longPnlClass = r.long_pnl >= 0 ? 'positive' : 'negative';
            const shortPnlClass = r.short_pnl >= 0 ? 'positive' : 'negative';
            html += `<tr>
                <td>${r.id}</td>
                <td>${r.pair}</td>
                <td>${new Date(r.timestamp).toLocaleDateString()}</td>
                <td>${new Date(r.start_date).toLocaleDateString()} - ${new Date(r.end_date).toLocaleDateString()}</td>
                <td>${r.total_trades}</td>
                <td>${(r.win_rate * 100).toFixed(1)}%</td>
                <td class="${pnlClass}">${fmtPnl(r.total_pnl)}</td>
                <td><span class="badge badge-long">${r.long_trades}</span> ${longWr}% <span class="${longPnlClass}">${fmtPnl(r.long_pnl)}</span></td>
                <td><span class="badge badge-short">${r.short_trades}</span> ${shortWr}% <span class="${shortPnlClass}">${fmtPnl(r.short_pnl)}</span></td>
                <td>${(r.max_drawdown * 100).toFixed(1)}%</td>
                <td>${r.sharpe_ratio.toFixed(2)}</td>
            </tr>`;
        }
        document.getElementById('backtest-body').innerHTML = html || '<tr><td colspan="11" class="neutral">No backtests yet. Run: python main.py backtest</td></tr>';
    } catch(e) {
        console.error('Backtests load failed:', e);
    }
}

// Research tab — reads research/test_history.json via /api/research
async function loadResearch() {
    try {
        const res = await fetch(`${API}/api/research`);
        const data = await res.json();
        if (data.error) {
            document.getElementById('research-body').innerHTML =
                `<tr><td colspan="10" class="negative">${data.error}</td></tr>`;
            return;
        }

        const anchor = (data.baselines && data.baselines.anchor) || {};
        const rolling = (data.baselines && data.baselines.rolling) || {};
        const pctA = v => (v == null ? '-' : (v * 100).toFixed(1) + '%');
        let baselineHtml = '';
        if (anchor.profit_factor != null) {
            baselineHtml += `Anchor: PF ${anchor.profit_factor.toFixed(2)} · WR ${pctA(anchor.win_rate)} · DD ${pctA(anchor.max_drawdown_pct)} · ${anchor.trades} trades`;
        }
        if (rolling.profit_factor != null) {
            baselineHtml += ` &nbsp;|&nbsp; Rolling: PF ${rolling.profit_factor.toFixed(2)} · WR ${pctA(rolling.win_rate)} · DD ${pctA(rolling.max_drawdown_pct)}`;
        }
        document.getElementById('research-baselines').innerHTML = baselineHtml || 'No baseline set';

        const b = data.budget || {};
        document.getElementById('research-budget').textContent =
            `Tests this quarter: ${b.tests_this_quarter || 0} / escalation at ${b.escalate_bar_after || 500} · Daily cap: ${b.daily_cap || 5}`;

        const tests = data.tests || [];
        let html = '';
        for (const t of tests) {
            const verdict = t.verdict || '?';
            let vClass = 'neutral';
            if (verdict === 'PROMOTED_CANDIDATE') vClass = 'positive';
            else if (verdict.startsWith('REJECTED')) vClass = 'negative';
            else if (verdict.startsWith('FLAGGED')) vClass = 'neutral';

            const pct = v => (v == null ? '-' : (v * 100).toFixed(0) + '%');
            const pf = v => (v == null ? '-' : v.toFixed(2));
            const tested = t.tested_at ? new Date(t.tested_at).toLocaleString() : '-';
            const oosPf = t.oos_profit_factor == null ? '-' : t.oos_profit_factor.toFixed(2);
            const reason = (t.verdict_reason || '').replace(/</g, '&lt;');

            html += `<tr>
                <td title="${t.params_hash || ''}">${t.id || '-'}</td>
                <td>${tested}</td>
                <td>${t.mutation || '-'}</td>
                <td>${pf(t.median_profit_factor)}</td>
                <td>${pct(t.median_win_rate)}</td>
                <td>${pct(t.median_max_drawdown_pct)}</td>
                <td>${pct(t.walk_forward_pass_rate)}</td>
                <td>${oosPf}</td>
                <td class="${vClass}">${verdict.replace(/_/g, ' ')}</td>
                <td style="font-size:11px;color:#64748b">${reason}</td>
            </tr>`;
        }
        document.getElementById('research-body').innerHTML = html ||
            '<tr><td colspan="10" class="neutral">No research runs yet. Run: python -m scheduler.research_job</td></tr>';
    } catch(e) {
        console.error('Research load failed:', e);
        document.getElementById('research-body').innerHTML =
            `<tr><td colspan="10" class="negative">Failed to load: ${e}</td></tr>`;
    }
}

// Comparisons tab
const COMPARE_METRICS = [
    { key: 'id',                 label: 'BT #',              fmt: v => '#'+(+v),                          higherBetter: false  },
    { key: 'total_trades',       label: 'Trades',            fmt: v => +v,                                higherBetter: false  },
    { key: 'wins',               label: 'Wins',              fmt: v => +v,                                higherBetter: true   },
    { key: 'losses',             label: 'Losses',            fmt: v => +v,                                higherBetter: false  },
    { key: 'win_rate',           label: 'Win Rate',          fmt: v => ((+v)*100).toFixed(1)+'%',         higherBetter: true   },
    { key: 'profit_factor',      label: 'Profit Factor',     fmt: v => isFinite(+v) ? (+v).toFixed(2) : 'N/A', higherBetter: true },
    { key: 'total_pnl',          label: 'Total P&L',         fmt: v => fmtPnl(v),                         higherBetter: true   },
    { key: 'expectancy_pips',    label: 'Expectancy (pips)', fmt: v => ((+v)>=0?'+':'')+(+v).toFixed(1),  higherBetter: true   },
    { key: 'max_drawdown',       label: 'Max Drawdown',      fmt: v => ((+v)*100).toFixed(1)+'%',         higherBetter: false  },
    { key: 'sharpe_ratio',       label: 'Sharpe Ratio',      fmt: v => isFinite(+v) ? (+v).toFixed(2) : 'N/A', higherBetter: true },
    { key: 'avg_win_pips',       label: 'Avg Win (pips)',    fmt: v => (+v).toFixed(1),                   higherBetter: true   },
    { key: 'avg_loss_pips',      label: 'Avg Loss (pips)',   fmt: v => (+v).toFixed(1),                   higherBetter: false  },
    { key: 'consecutive_losses', label: 'Max Con. Losses',   fmt: v => +v,                                higherBetter: false  },
];

async function loadComparisons() {
    try {
        // Fetch all pairs so the comparisons view is always cross-pair
        const res = await fetch(`${API}/api/compare`);
        const groups = await res.json();

        // Re-group by (pair, period) — the API groups only by period, so split further
        const byPairPeriod = {};
        for (const g of groups) {
            for (const r of g.runs) {
                const key = `${r.pair || 'EURUSD'} | ${g.period}`;
                if (!byPairPeriod[key]) byPairPeriod[key] = { pair: r.pair || 'EURUSD', period: g.period, runs: [] };
                byPairPeriod[key].runs.push(r);
            }
        }

        // Only show groups with ≥2 runs — oldest run acts as the reference
        const comparable = Object.values(byPairPeriod)
            .filter(g => g.runs.length >= 2)
            .sort((a, b) => a.pair.localeCompare(b.pair) || a.period.localeCompare(b.period));

        if (!comparable.length) {
            document.getElementById('compare-groups').innerHTML = '<div class="card"><p class="neutral">No comparisons yet — need ≥2 backtest runs for the same pair and period.</p></div>';
            return;
        }

        let html = '';
        let tableIdx = 0;
        for (const group of comparable) {
            // Sort oldest first → oldest = reference row
            const sorted = [...group.runs].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
            const reference = sorted[0];
            const variants = sorted.slice(1);

            let headerCols = '<th>Config</th>';
            for (const m of COMPARE_METRICS) headerCols += `<th>${m.label}</th>`;

            let rows = '';

            // Reference row (oldest run for this pair+period)
            let refRow = `<td class="compare-metric">${reference.config} <span style="font-size:10px;color:#64748b">(ref)</span></td>`;
            for (const m of COMPARE_METRICS) {
                const val = reference[m.key] != null ? reference[m.key] : 0;
                refRow += `<td>${m.fmt(val)}</td>`;
            }
            rows += `<tr>${refRow}</tr>`;

            for (const v of variants) {
                let varRow = `<td class="compare-metric">${v.config}</td>`;
                for (const m of COMPARE_METRICS) {
                    const val = v[m.key] != null ? v[m.key] : 0;
                    const refVal = reference[m.key] != null ? reference[m.key] : 0;
                    const better = m.higherBetter ? val > refVal : val < refVal;
                    const worse  = m.higherBetter ? val < refVal : val > refVal;
                    const cls = better ? ' class="positive"' : worse ? ' class="negative"' : '';
                    varRow += `<td${cls}>${m.fmt(val)}</td>`;
                }
                rows += `<tr>${varRow}</tr>`;
            }

            const capturedIdx = tableIdx++;
            html += `
                <div class="card full-width" style="margin-bottom:24px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                        <h3 style="margin:0">${group.pair} &mdash; ${group.period}</h3>
                        <div style="display:flex;align-items:center;gap:12px">
                            <span style="font-size:12px;color:#94a3b8">${variants.length} run(s) vs reference</span>
                            <button class="dl-xlsx-btn" onclick="downloadCompareTable(${capturedIdx})" title="Download as Excel">
                                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M8 2v8M8 10l-3-3M8 10l3-3M3 13h10"/>
                                </svg>
                            </button>
                        </div>
                    </div>
                    <div style="overflow-x:auto">
                    <table class="compare-table">
                        <thead><tr>${headerCols}</tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                    </div>
                </div>`;
        }

        document.getElementById('compare-groups').innerHTML = html;
    } catch(e) {
        console.error('Comparisons load failed:', e);
        document.getElementById('compare-groups').innerHTML = '<div class="card"><p class="neutral">Failed to load comparisons</p></div>';
    }
}

// Download comparison table as CSV (Excel-compatible)
function downloadCompareTable(groupIdx) {
    const tables = document.querySelectorAll('#compare-groups .compare-table');
    if (!tables[groupIdx]) return;

    const table = tables[groupIdx];
    const rows = [];

    // Header row
    const headers = [];
    table.querySelectorAll('thead th').forEach(th => headers.push(th.textContent.trim()));
    rows.push(headers);

    // Data rows (skip delta rows)
    table.querySelectorAll('tbody tr').forEach(tr => {
        const cells = [];
        tr.querySelectorAll('td').forEach(td => cells.push(td.textContent.trim()));
        rows.push(cells);
    });

    // Build CSV
    const csv = rows.map(r => r.map(c => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const card = tables[groupIdx].closest('.card');
    const period = card ? card.querySelector('h3')?.textContent.trim().replace(/[^a-zA-Z0-9_-]/g, '_') : 'comparison';
    a.href = url;
    a.download = `comparison_${period}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// Learning tab
async function loadLearning() {
    try {
        const res = await fetch(`${API}/api/learning?pair=${selectedPair}`);
        const data = await res.json();

        // Setup stats
        let html = '';
        for (const [type, stats] of Object.entries(data.setup_stats)) {
            const wrClass = stats.win_rate >= 0.5 ? 'positive' : 'negative';
            const pnlClass = stats.total_pnl >= 0 ? 'positive' : 'negative';
            html += `
                <div class="signal-card">
                    <div class="signal-header">
                        <strong>${type}</strong>
                        <span>${stats.total} trades</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Win Rate</span>
                        <span class="stat-value ${wrClass}">${(stats.win_rate * 100).toFixed(1)}%</span>
                    </div>
                    <div class="stat-row">
                        <span class="stat-label">Total P&L</span>
                        <span class="stat-value ${pnlClass}">${fmtPnl(stats.total_pnl)}</span>
                    </div>
                </div>`;
        }
        document.getElementById('setup-stats').innerHTML = html || '<p class="neutral">No trade data yet</p>';

        // Parameter history
        if (data.parameter_history.length > 0) {
            const trace = {
                x: data.parameter_history.map(p => p.timestamp),
                y: data.parameter_history.map(p => p.score),
                type: 'scatter',
                line: { color: '#fbbf24', width: 2 },
            };
            Plotly.newPlot('param-chart', [trace], {
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: { color: '#94a3b8', size: 11 },
                margin: { t: 10, r: 20, b: 40, l: 50 },
                xaxis: { gridcolor: '#1e293b' },
                yaxis: { gridcolor: '#1e293b', title: 'Performance Score' },
            }, { responsive: true, displayModeBar: false });
        } else {
            document.getElementById('param-chart').innerHTML = '<p class="neutral">No parameter history yet</p>';
        }
    } catch(e) {
        console.error('Learning load failed:', e);
    }
}

// Data Health tab
async function loadDataHealth() {
    const btn = document.getElementById('data-refresh-btn');
    if (btn) { btn.textContent = 'Loading...'; btn.disabled = true; }

    try {
        const res = await fetch(`${API}/api/data-health`);
        const data = await res.json();

        const tfs = ['15m', '1h', '4h', '1d', '1wk'];
        let html = '';

        for (const row of data.assets) {
            const latestDates = tfs.map(tf => row.timeframes[tf]?.to).filter(Boolean);
            const latest = latestDates.length ? latestDates.sort().at(-1) : '—';

            let cells = `<td><strong>${row.asset}</strong></td><td style="color:#64748b">${row.asset_class}</td>`;

            for (const tf of tfs) {
                const tf_data = row.timeframes[tf];
                if (!tf_data || !tf_data.count) {
                    cells += `<td style="color:#ef4444">—</td><td style="color:#ef4444">—</td>`;
                    continue;
                }
                const countOk = tf_data.count > 0;
                const dateOk = tf_data.ok;
                const countCls = countOk ? '' : 'style="color:#ef4444"';
                const dateCls = dateOk ? '' : 'style="color:#f59e0b"';
                cells += `<td ${countCls}>${tf_data.count.toLocaleString()}</td>`;
                cells += `<td ${dateCls}>${tf_data.from || '—'}</td>`;
            }

            cells += `<td style="color:#94a3b8">${latest}</td>`;
            html += `<tr>${cells}</tr>`;
        }

        document.getElementById('data-health-body').innerHTML = html || '<tr><td colspan="13" class="neutral">No data</td></tr>';

        const ts = new Date(data.checked_at + 'Z').toLocaleTimeString();
        const el = document.getElementById('data-checked-at');
        if (el) el.textContent = `Last checked: ${ts}`;
    } catch(e) {
        console.error('Data health load failed:', e);
        document.getElementById('data-health-body').innerHTML = '<tr><td colspan="13" class="neutral">Failed to load</td></tr>';
    } finally {
        if (btn) { btn.textContent = 'Refresh from DB'; btn.disabled = false; }
    }
}

// Initial load
loadAssets().then(() => loadOverview());

// Live price refresh every 5 seconds
setInterval(loadLivePrice, 5000);

// Full data refresh every 30 seconds
setInterval(() => {
    loadStatus();
    loadOandaAccount();
    const activeTab = document.querySelector('.tab.active')?.dataset.tab;
    if (activeTab) loadTabData(activeTab);
}, 30000);
