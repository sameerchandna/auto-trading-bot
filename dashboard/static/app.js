// Trading Bot Dashboard - Client-side JS

const API = '';

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
        case 'trades': loadTrades(); break;
        case 'chart': loadChart(); break;
        case 'backtest': loadBacktests(); break;
        case 'learning': loadLearning(); break;
    }
}

// Status bar
async function loadStatus() {
    try {
        const res = await fetch(`${API}/api/status`);
        const data = await res.json();
        document.getElementById('capital').textContent = `Capital: \u00a3${data.capital.toLocaleString()}`;

        const pnlEl = document.getElementById('pnl');
        pnlEl.textContent = `P&L: \u00a3${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl.toLocaleString()}`;
        pnlEl.className = data.total_pnl >= 0 ? 'positive' : 'negative';

        document.getElementById('open-pos').textContent = `Open: ${data.open_positions}`;
        document.getElementById('signals-today').textContent = `Signals: ${data.signals_today}`;
        document.getElementById('last-update').textContent = `Updated: ${new Date().toLocaleTimeString()}`;
    } catch(e) {
        console.error('Status load failed:', e);
    }
}

// Overview tab
async function loadOverview() {
    loadStatus();
    loadEquityCurve();
    loadRecentSignals();
    loadOpenPositions();
}

async function loadEquityCurve() {
    try {
        const res = await fetch(`${API}/api/equity`);
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
        const res = await fetch(`${API}/api/signals?limit=5`);
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
                        Entry: ${s.entry_price.toFixed(5)} | SL: ${s.stop_loss.toFixed(5)} | TP: ${s.take_profit.toFixed(5)}
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
        const res = await fetch(`${API}/api/trades?limit=10`);
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
                        Entry: ${t.entry_price.toFixed(5)} | SL: ${t.stop_loss?.toFixed(5) || '--'} | TP: ${t.take_profit?.toFixed(5) || '--'}
                    </div>
                    <div style="font-size:11px;color:#94a3b8;margin-top:4px">
                        Risk: \u00a3${t.size ? (t.size * 0.0001).toFixed(2) : '--'} | ${new Date(t.opened_at).toLocaleString()}
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
        const res = await fetch(`${API}/api/status`);
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
async function loadTrades() {
    try {
        const res = await fetch(`${API}/api/trades?limit=50`);
        const trades = await res.json();

        let html = '';
        for (const t of trades) {
            const dirClass = t.direction === 'long' ? 'badge-long' : 'badge-short';
            const statusClass = t.status === 'open' ? 'badge-open' : 'badge-closed';
            const pnlClass = (t.pnl || 0) >= 0 ? 'positive' : 'negative';

            html += `<tr>
                <td>${t.id}</td>
                <td><span class="badge ${dirClass}">${t.direction}</span></td>
                <td>${t.signal_type || '--'}</td>
                <td>${t.entry_price.toFixed(5)}</td>
                <td>${t.exit_price ? t.exit_price.toFixed(5) : '--'}</td>
                <td>${t.stop_loss ? t.stop_loss.toFixed(5) : '--'}</td>
                <td>${t.take_profit ? t.take_profit.toFixed(5) : '--'}</td>
                <td class="${pnlClass}">${t.pnl_pips ? t.pnl_pips.toFixed(1) : '--'}</td>
                <td class="${pnlClass}">${t.pnl ? '\u00a3' + t.pnl.toFixed(2) : '--'}</td>
                <td>${t.confluence_score ? (t.confluence_score * 100).toFixed(0) + '%' : '--'}</td>
                <td><span class="badge ${statusClass}">${t.status}</span></td>
                <td>${t.opened_at ? new Date(t.opened_at).toLocaleDateString() : '--'}</td>
            </tr>`;
        }
        document.getElementById('trades-body').innerHTML = html || '<tr><td colspan="12" class="neutral">No trades yet</td></tr>';
    } catch(e) {
        console.error('Trades load failed:', e);
    }
}

// Chart tab
async function loadChart() {
    const tf = document.getElementById('tf-select').value;
    try {
        const res = await fetch(`${API}/api/candles/${tf}?limit=200`);
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

        Plotly.newPlot('price-chart', [trace], {
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

document.getElementById('tf-select').addEventListener('change', loadChart);

// Backtest tab
async function loadBacktests() {
    try {
        const res = await fetch(`${API}/api/backtests`);
        const runs = await res.json();

        let html = '';
        for (const r of runs) {
            const pnlClass = r.total_pnl >= 0 ? 'positive' : 'negative';
            html += `<tr>
                <td>${r.id}</td>
                <td>${new Date(r.timestamp).toLocaleDateString()}</td>
                <td>${new Date(r.start_date).toLocaleDateString()} - ${new Date(r.end_date).toLocaleDateString()}</td>
                <td>${r.total_trades}</td>
                <td>${(r.win_rate * 100).toFixed(1)}%</td>
                <td class="${pnlClass}">\u00a3${r.total_pnl.toFixed(2)}</td>
                <td>${(r.max_drawdown * 100).toFixed(1)}%</td>
                <td>${r.sharpe_ratio.toFixed(2)}</td>
            </tr>`;
        }
        document.getElementById('backtest-body').innerHTML = html || '<tr><td colspan="8" class="neutral">No backtests yet. Run: python main.py backtest</td></tr>';
    } catch(e) {
        console.error('Backtests load failed:', e);
    }
}

// Learning tab
async function loadLearning() {
    try {
        const res = await fetch(`${API}/api/learning`);
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
                        <span class="stat-value ${pnlClass}">\u00a3${stats.total_pnl.toFixed(2)}</span>
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

// Initial load
loadOverview();
loadAccountSummary();

// Auto-refresh every 60 seconds
setInterval(() => {
    loadStatus();
    const activeTab = document.querySelector('.tab.active')?.dataset.tab;
    if (activeTab) loadTabData(activeTab);
}, 60000);
