"""
Inline HTML templates — no Jinja2, no external CDN, no extra dependencies.
Pure f-string HTML with vanilla JS and inline SVG for charting.
"""
from __future__ import annotations

import json as _json


def render_dashboard(
    status: dict,
    positions: list[dict],
    pnl: dict,
    trades: list[dict] | None = None,
    stats: dict | None = None,
    equity: list[dict] | None = None,
    balance: list[dict] | None = None,
    ticks: list[dict] | None = None,
) -> str:
    trades = trades or []
    stats = stats or {}
    equity = equity or []
    balance = balance or []
    ticks = ticks or []

    mode = status.get("mode", "unknown").upper()
    paused = status.get("paused", False)
    circuit = status.get("circuit", "UNKNOWN")
    trading_hours = status.get("trading_hours", "24/7")
    trading_days = status.get("trading_days", "all")

    state_badge = _badge("PAUSED", "#f59e0b") if paused else _badge("RUNNING", "#10b981")
    circuit_badge = _badge(circuit, "#ef4444" if circuit != "CLOSED" else "#10b981")
    mode_badge = _badge(mode, "#6366f1")
    hours_info = f"{trading_hours} UTC | Days: {trading_days}"

    equity_svg = _render_equity_svg(equity)
    stats_html = _render_stats(stats)
    positions_html = _render_positions(positions)
    buy_html = _render_buy_history(trades)
    sell_html = _render_sell_history(trades)
    pnl_html = _render_pnl(pnl)
    balance_html = _render_balance(balance)
    ticks_html = _render_ticks(ticks)
    # Seed the client-side _ticksMap with server-rendered data so the table is
    # immediately populated and SSE updates are merged into it.
    _ticks_seed_js = "\n".join(
        f"_ticksMap[{_json.dumps(t['symbol'])}] = {_json.dumps(t)};"
        for t in ticks
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>트레이딩 에이전트 대시보드</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
    header{{background:#1e293b;border-bottom:1px solid #334155;padding:1rem 2rem;display:flex;align-items:center;justify-content:space-between}}
    header h1{{font-size:1.2rem;font-weight:700;color:#f1f5f9}}
    .meta{{font-size:.75rem;color:#64748b}}
    main{{max-width:1200px;margin:0 auto;padding:1.5rem}}
    .top-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1.2rem;margin-bottom:1.5rem}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:.75rem;padding:1.25rem}}
    .card h2{{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:.9rem}}
    .stat-row{{display:flex;justify-content:space-between;align-items:center;margin:.4rem 0}}
    .stat-label{{color:#94a3b8;font-size:.875rem}}
    .stat-value{{font-weight:600;font-size:.95rem}}
    .badge{{display:inline-block;padding:.2rem .6rem;border-radius:9999px;font-size:.7rem;font-weight:700}}
    /* Tabs */
    .tabs{{display:flex;gap:.5rem;margin-bottom:1rem;border-bottom:1px solid #334155;padding-bottom:.5rem}}
    .tab{{padding:.4rem 1rem;cursor:pointer;border-radius:.4rem .4rem 0 0;font-size:.85rem;color:#64748b;background:none;border:none}}
    .tab.active{{color:#e2e8f0;background:#334155}}
    .tab-panel{{display:none}}.tab-panel.active{{display:block}}
    /* Tables */
    table{{width:100%;border-collapse:collapse;font-size:.82rem}}
    th{{text-align:left;color:#64748b;font-size:.72rem;text-transform:uppercase;padding:.5rem 0;border-bottom:1px solid #334155}}
    td{{padding:.55rem 0;border-bottom:1px solid #1e293b}}
    tr:last-child td{{border-bottom:none}}
    /* Ticks table — centered, bold headers, cell borders */
    .ticks-table th{{text-align:center;font-weight:700;color:#94a3b8;font-size:.75rem;border:1.5px solid #334155;padding:.45rem .5rem;background:#0f172a;text-transform:none}}
    .ticks-table td{{text-align:center;border:1px solid #1e293b;padding:.5rem .4rem;border-bottom:1px solid #1e293b}}
    .ticks-table tr:last-child td{{border-bottom:1px solid #1e293b}}
    .empty{{color:#475569;font-size:.875rem;text-align:center;padding:1.5rem 0}}
    .win{{color:#10b981}}.loss{{color:#ef4444}}
    /* Controls */
    .controls{{display:flex;gap:.75rem;margin-top:1.25rem}}
    button{{padding:.55rem 1.3rem;border:none;border-radius:.4rem;font-weight:600;cursor:pointer;font-size:.875rem;transition:opacity .15s}}
    button:hover{{opacity:.85}}
    .btn-pause{{background:#f59e0b;color:#000}}.btn-resume{{background:#10b981;color:#000}}
    /* Strategy criteria */
    .criteria-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:.5rem}}
    .criteria-col h3{{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem}}
    .criteria-col h3.buy{{color:#10b981}}.criteria-col h3.sell{{color:#ef4444}}
    .criteria-row{{display:flex;align-items:flex-start;gap:.4rem;margin:.3rem 0;font-size:.8rem;color:#cbd5e1}}
    .criteria-row .ci{{font-size:.9rem;flex-shrink:0}}
    .param-row{{display:inline-flex;align-items:center;gap:.3rem;margin:.2rem .3rem;background:#0f172a;border-radius:.3rem;padding:.2rem .5rem;font-size:.78rem}}
    .param-key{{color:#64748b}}.param-val{{color:#f1f5f9;font-weight:600}}
    /* Condition pill badges */
    .cp{{display:inline-block;padding:.15rem .35rem;border-radius:.25rem;font-size:.72rem;font-weight:700;margin:0 2px;vertical-align:middle;letter-spacing:.02em;transition:opacity .2s}}
    .cp-dim{{background:#1e293b;color:#3d5060}}
    /* Buy condition (active) — unified green */
    .cp-buy{{background:#10b98120;color:#34d399;border:1px solid #10b981}}
    /* Sell trigger (active) — unified red */
    .cp-sell{{background:#ef444425;color:#f87171;border:1px solid #ef4444}}
    /* Tick flash animation */
    @keyframes tickFlash{{0%{{background:#1e4a3a}}100%{{background:transparent}}}}
    .tick-flash{{animation:tickFlash .8s ease-out}}
    .tick-live-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:#10b981;margin-right:.4rem;animation:pulse 2s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
    .toast{{position:fixed;bottom:2rem;right:2rem;background:#334155;padding:.7rem 1.2rem;border-radius:.5rem;font-size:.875rem;display:none;z-index:99}}
    /* SVG chart */
    .chart-wrap{{width:100%;overflow:hidden;background:#0f172a;border-radius:.5rem;margin-top:.5rem}}
    svg{{width:100%;height:180px}}
    /* Stats grid */
    .stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin-top:.25rem}}
    .stat-box{{background:#0f172a;border-radius:.5rem;padding:.75rem;text-align:center}}
    .stat-box .val{{font-size:1.25rem;font-weight:700;margin-bottom:.25rem}}
    .stat-box .lbl{{font-size:.7rem;color:#64748b;text-transform:uppercase}}
    .status-box{{background:#0f172a;border:1px solid #334155;border-radius:.5rem;padding:.65rem .5rem;text-align:center}}
    .wr-bar{{height:6px;background:#1e293b;border-radius:9999px;overflow:hidden;margin-top:.4rem}}
    .wr-fill{{height:100%;border-radius:9999px;transition:width .6s ease}}
    code{{background:#0f172a;padding:.1rem .3rem;border-radius:.25rem;font-size:.8rem}}
  </style>
</head>
<body>
<header>
  <h1>⚡ Trading Agent Dashboard</h1>
  <span class="meta" id="meta">30초 자동 새로고침</span>
</header>
<main>
  <!-- Row 1: 엔진상태 + 포트폴리오 -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;margin-bottom:1.2rem">
    <div class="card" id="status-card">
      <h2>엔진 상태</h2>
      <div class="stat-row"><span class="stat-label">모드</span>{mode_badge}</div>
      <div class="stat-row"><span class="stat-label">상태</span>{state_badge}</div>
      <div class="stat-row"><span class="stat-label">서킷 브레이커</span>{circuit_badge}</div>
      <div class="stat-row"><span class="stat-label">거래 시간</span><span class="stat-value" style="font-size:.78rem;color:#94a3b8">{hours_info}</span></div>
    </div>
    {pnl_html}
  </div>
  <!-- Row 2: 성과분석 -->
  {stats_html}

  <!-- Balance card -->
  <div id="balance-card" class="card" style="margin-bottom:1.2rem">
    <h2>주문가능 코인 잔고</h2>
    <div id="balance-body">{balance_html}</div>
  </div>

  <!-- Strategy criteria -->
  <div class="card" style="margin-bottom:1.2rem" id="strategy-card">
    <h2>전략 파라미터 &amp; 신호 기준</h2>
    <div id="strategy-body">로딩 중...</div>
  </div>

  <!-- Live signal evaluation -->
  <div class="card" style="margin-bottom:1.2rem">
    <h2 style="display:flex;align-items:center;gap:.5rem">
      <svg viewBox="0 0 230 22" style="width:auto!important;height:22px!important;display:inline-block;vertical-align:middle;overflow:visible" xmlns="http://www.w3.org/2000/svg">
        <polyline points="0,11 12,11 18,2 24,20 30,11 44,11" fill="none" stroke="#10b981" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="47" cy="11" r="3" fill="#10b981" style="animation:pulse 2s infinite"/>
        <rect x="55" y="3" width="30" height="15" rx="3" fill="#10b98118" stroke="#10b981" stroke-width=".8"/>
        <text x="70" y="14.5" text-anchor="middle" font-family="system-ui,-apple-system,sans-serif" font-size="8" font-weight="800" letter-spacing=".08em" fill="#10b981">LIVE</text>
        <text x="92" y="16" font-family="system-ui,-apple-system,sans-serif" font-size="12.5" font-weight="700" fill="#f1f5f9">신호 평가 현황</text>
      </svg>
      <span id="tick-last-time" style="font-size:.7rem;color:#475569;font-weight:400"></span>
    </h2>
    <div id="ticks-body">{ticks_html}</div>
  </div>

  <!-- Equity curve -->
  <div class="card" style="margin-bottom:1.2rem">
    <h2>자산 곡선 (누적 손익)</h2>
    <div class="chart-wrap" id="chart-wrap">
      {equity_svg}
    </div>
  </div>

  <!-- Tabs: Positions / Buy / Sell -->
  <div class="card">
    <div class="tabs">
      <button class="tab active" onclick="switchTab('positions',this)">오픈 포지션 ({len(positions)})</button>
      <button class="tab" onclick="switchTab('buys',this)">매수 이력 ({len(trades)})</button>
      <button class="tab" onclick="switchTab('sells',this)">매도 이력 ({len(trades)})</button>
    </div>
    <div id="tab-positions" class="tab-panel active">{positions_html}</div>
    <div id="tab-buys" class="tab-panel">{buy_html}</div>
    <div id="tab-sells" class="tab-panel">{sell_html}</div>
  </div>

</main>
<div class="toast" id="toast"></div>
<script>
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}}
let _strategyParams = {{}};
let _positionsMap = {{}};  // symbol → position (entry_price, amount)
async function refresh() {{
  try {{
    const [s, pos, pnl, trades, stats, eq, bal, ticks, strat] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/positions').then(r=>r.json()),
      fetch('/api/pnl').then(r=>r.json()),
      fetch('/api/trades').then(r=>r.json()),
      fetch('/api/stats').then(r=>r.json()),
      fetch('/api/equity').then(r=>r.json()),
      fetch('/api/balance').then(r=>r.json()),
      fetch('/api/ticks').then(r=>r.json()),
      fetch('/api/strategy').then(r=>r.json()),
    ]);
    document.getElementById('meta').textContent = '새로고침: ' + new Date().toLocaleTimeString();
    // Update positions map for tick table lookup
    _positionsMap = {{}};
    (pos || []).forEach(p => {{ _positionsMap[p.symbol] = p; }});
    renderStatus(s);
    renderPnl(pnl);
    renderStats(stats);
    renderEquity(eq);
    renderBalance(bal);
    renderStrategy(strat);
    renderTicks(ticks);
    renderBuyHistory(trades);
    renderSellHistory(trades);
    // Update tab counts
    const tabs = document.querySelectorAll('.tab');
    if (tabs[1]) tabs[1].textContent = `매수 이력 (${{trades.length}})`;
    if (tabs[2]) tabs[2].textContent = `매도 이력 (${{trades.length}})`;
  }} catch(e) {{ console.warn('Refresh failed', e); }}
}}
function renderStrategy(s) {{
  const el = document.getElementById('strategy-body');
  if (!el || !s || !s.name) {{ if(el) el.innerHTML = '<p class="empty">전략 정보 없음</p>'; return; }}
  _strategyParams = s.parameters || {{}};
  const p = _strategyParams;
  const tf = s.timeframe || '-';

  // Parameter pills
  const paramMap = {{
    'EMA Fast': p.ema_fast, 'EMA Slow': p.ema_slow,
    'RSI Period': p.rsi_period, 'RSI Min': p.rsi_min,
    'Overbought': p.overbought, 'Vol Mult': p.vol_mult,
    'ATR Period': p.atr_period, 'Timeframe': tf,
  }};
  const pills = Object.entries(paramMap)
    .filter(([,v]) => v != null)
    .map(([k,v]) => `<span class="param-row"><span class="param-key">${{k}}</span><span class="param-val">${{v}}</span></span>`)
    .join('');

  // Signal criteria with icons
  const emaF = p.ema_fast || 9, emaS = p.ema_slow || 21;
  const rsiMin = p.rsi_min || 40, ob = p.overbought || 70;
  const vm = p.vol_mult || 1.5;
  const adxThr = p.adx_threshold || 25;
  const mw = p.macd_window || 3;

  const prox = p.ema_proximity_pct || 1.5;
  const buyCriteria = [
    [`EMA${{emaF}} &gt; EMA${{emaS}}`, '상승 정렬 (골든크로스 포함)'],
    [`가격 ≤ EMA${{emaF}} + ${{prox}}%`, '풀백 진입 — 눌림목만 허용'],
    [`MACD histogram`, `음→양 전환 (${{mw}}봉 이내)`],
    [`RSI ${{rsiMin}} ~ ${{ob}}`, '모멘텀 확인, 과매수 미도달'],
    [`거래량 ≥ 평균 × ${{vm}}배`, '유동성 필터'],
    [`ADX ≥ ${{adxThr}}`, '추세 강도 확인 (횡보 차단)'],
  ];
  const sellCriteria = [
    [`EMA${{emaF}} &lt; EMA${{emaS}}`, '데스크로스 (추세 역전)'],
    ['MACD histogram', '양 → 음 전환, EMA 위에서'],
  ];
  const buyPillLabels  = ['EMA','PROX','MACD','RSI','VOL','ADX'];
  const sellPillLabels = ['데스크로스','MACD↓'];

  const mkBuyCriteriaRows = arr => arr.map(([cond, desc], i) =>
    `<div class="criteria-row">
      <span class="cp cp-buy" style="flex-shrink:0">${{buyPillLabels[i]}}</span>
      <span><code>${{cond}}</code> <span style="color:#64748b">${{desc}}</span></span>
    </div>`
  ).join('');

  const mkSellCriteriaRows = arr => arr.map(([cond, desc], i) =>
    `<div class="criteria-row">
      <span class="cp cp-sell" style="flex-shrink:0">${{sellPillLabels[i]}}</span>
      <span><code>${{cond}}</code> <span style="color:#64748b">${{desc}}</span></span>
    </div>`
  ).join('');

  el.innerHTML = `
    <div style="margin-bottom:.75rem;display:flex;flex-wrap:wrap">${{pills}}</div>
    <div class="criteria-grid">
      <div class="criteria-col">
        <h3 class="buy">▲ 매수 조건 (AND 6개)</h3>
        ${{mkBuyCriteriaRows(buyCriteria)}}
      </div>
      <div class="criteria-col">
        <h3 class="sell">▼ 매도 조건 (OR 2개)</h3>
        ${{mkSellCriteriaRows(sellCriteria)}}
      </div>
    </div>`;
}}
function mkBadge(text, color) {{
  return `<span class="badge" style="background:${{color}}20;color:${{color}}">${{text}}</span>`;
}}
function renderStatus(s) {{
  const el = document.getElementById('status-card');
  if (!el) return;
  const mode = (s.mode || 'unknown').toUpperCase();
  const paused = s.paused || false;
  const circuit = s.circuit || 'UNKNOWN';
  const hours = s.trading_hours || '24/7';
  const days = s.trading_days || 'all';
  const modeColor = mode==='LIVE'?'#10b981':mode==='PAPER'?'#6366f1':'#64748b';
  const modeIcon  = mode==='LIVE'?'⚡':mode==='PAPER'?'📋':'○';
  const stateColor = paused?'#f59e0b':'#10b981';
  const stateIcon  = paused?'⏸':'▶';
  const circuitColor = circuit!=='CLOSED'?'#ef4444':'#10b981';
  const circuitIcon  = circuit!=='CLOSED'?'⚠':'✓';
  el.innerHTML = `
    <h2>엔진 상태</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:.6rem 0">
      <div class="status-box" style="border-color:${{modeColor}}50">
        <div style="font-size:1.5rem;margin-bottom:.2rem">${{modeIcon}}</div>
        <div style="color:${{modeColor}};font-weight:700;font-size:.95rem">${{mode}}</div>
        <div style="color:#475569;font-size:.68rem;margin-top:.15rem">모드</div>
      </div>
      <div class="status-box" style="border-color:${{stateColor}}50">
        <div style="font-size:1.5rem;margin-bottom:.2rem">${{stateIcon}}</div>
        <div style="color:${{stateColor}};font-weight:700;font-size:.95rem">${{paused?'PAUSED':'RUNNING'}}</div>
        <div style="color:#475569;font-size:.68rem;margin-top:.15rem">상태</div>
      </div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;background:#0f172a;border-radius:.4rem;padding:.45rem .7rem;margin-bottom:.4rem">
      <span style="color:#64748b;font-size:.75rem">서킷 브레이커</span>
      <span style="color:${{circuitColor}};font-weight:600;font-size:.8rem">${{circuitIcon}} ${{circuit}}</span>
    </div>
    <div style="color:#475569;font-size:.7rem;text-align:right">${{hours}} UTC · ${{days}}</div>`;
}}
function renderPnl(pnl) {{
  const el = document.getElementById('pnl-card');
  if (!el) return;
  const cash = pnl.cash || 0;
  const realized = pnl.realized_pnl || 0;
  const unrealized = pnl.unrealized_pnl || 0;
  const fmtKRW = v => '₩' + Math.round(Math.abs(v)).toLocaleString('ko-KR');
  const fmtSigned = v => {{
    const c = v>=0?'win':'loss'; const s=v>=0?'+':'-';
    return `<span class="${{c}}" style="font-size:1.05rem;font-weight:700">${{s}}${{fmtKRW(v)}}</span>`;
  }};
  el.innerHTML = `
    <h2>포트폴리오</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.6rem;margin-top:.6rem">
      <div class="stat-box">
        <div class="val" style="font-size:1rem;color:#f1f5f9">${{fmtKRW(cash)}}</div>
        <div class="lbl">현금 잔고</div>
      </div>
      <div class="stat-box">
        <div class="val">${{fmtSigned(realized)}}</div>
        <div class="lbl">실현 손익</div>
      </div>
      <div class="stat-box">
        <div class="val">${{fmtSigned(unrealized)}}</div>
        <div class="lbl">미실현 손익</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem">
      <button class="btn-pause" onclick="engineAction('pause')" style="padding:.45rem;font-size:.8rem;border-radius:.4rem;width:100%">⏸ 일시정지</button>
      <button class="btn-resume" onclick="engineAction('resume')" style="padding:.45rem;font-size:.8rem;border-radius:.4rem;width:100%">▶ 재개</button>
    </div>`;
}}
function renderStats(stats) {{
  const el = document.getElementById('stats-card');
  if (!el) return;
  const total = stats.total_trades || 0;
  if (!total) {{ el.innerHTML = '<h2>성과 분석</h2><p class="empty">거래 없음</p>'; return; }}
  const wr = stats.win_rate_pct || 0;
  const pf = stats.profit_factor || 0;
  const avgWin = stats.avg_win || 0;
  const avgLoss = stats.avg_loss || 0;
  const totalPnl = stats.total_pnl || 0;
  const wrColor = wr>=50?'#10b981':'#ef4444';
  const pfColor = pf>=1?'#10b981':'#ef4444';
  const pfDisplay = pf>=999?'∞':pf.toFixed(2);
  const pnlCls = totalPnl>=0?'win':'loss';
  const sign = totalPnl>=0?'+':'';
  const fmtKRW = v => Math.round(Math.abs(v)).toLocaleString('ko-KR');
  const wins = Math.round(total*wr/100);
  el.innerHTML = `
    <h2>성과 분석</h2>
    <div style="display:grid;grid-template-columns:150px 1fr;gap:1.5rem;align-items:center;margin-top:.6rem">
      <div style="text-align:center">
        <div style="font-size:2.4rem;font-weight:800;color:${{wrColor}};line-height:1.1">${{wr.toFixed(1)}}%</div>
        <div style="font-size:.7rem;color:#64748b;margin:.25rem 0 .3rem">승률</div>
        <div class="wr-bar"><div class="wr-fill" style="width:${{wr}}%;background:${{wrColor}}"></div></div>
        <div style="font-size:.68rem;color:#475569;margin-top:.45rem">${{wins}}승 · ${{total-wins}}패 · 총 ${{total}}건</div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem">
        <div class="stat-box">
          <div class="val" style="color:${{pfColor}}">${{pfDisplay}}</div>
          <div class="lbl">수익 팩터</div>
        </div>
        <div class="stat-box">
          <div class="val win">+₩${{fmtKRW(avgWin)}}</div>
          <div class="lbl">평균 수익</div>
        </div>
        <div class="stat-box">
          <div class="val loss">-₩${{fmtKRW(avgLoss)}}</div>
          <div class="lbl">평균 손실</div>
        </div>
        <div class="stat-box">
          <div class="val ${{pnlCls}}">${{sign}}₩${{fmtKRW(totalPnl)}}</div>
          <div class="lbl">총 손익</div>
        </div>
      </div>
    </div>`;
}}
// Tick SSE state
const _ticksMap = {{}};
function _initTickStream() {{
  const es = new EventSource('/api/ticks/stream');
  es.onmessage = function(e) {{
    try {{
      const tick = JSON.parse(e.data);
      const prev = _ticksMap[tick.symbol];
      _ticksMap[tick.symbol] = tick;
      _renderTicksFromMap(tick.symbol, prev);
      const el = document.getElementById('tick-last-time');
      if (el) el.textContent = '최근: ' + new Date().toLocaleTimeString('ko-KR');
    }} catch(_) {{}}
  }};
  es.onerror = function() {{
    // reconnect automatically (browser handles it), suppress console noise
  }};
}}
function _renderTicksFromMap(updatedSymbol, prevTick) {{
  const items = Object.values(_ticksMap).sort((a,b)=>a.symbol<b.symbol?-1:1);
  renderTicks(items, updatedSymbol, prevTick);
}}
function renderTicks(items, flashSymbol, prevTick) {{
  const el = document.getElementById('ticks-body');
  if (!el) return;
  if (!items || items.length === 0) {{ el.innerHTML = '<p class="empty">아직 평가 없음 (첫 tick 대기 중)</p>'; return; }}
  const actionColor = a => a==='BUY'?'#10b981':a==='SELL'?'#ef4444':'#64748b';
  const fmtPrice = (sym, v) => {{
    if (v == null) return '-';
    if (sym.includes('BTC') || sym.includes('ETH') || sym.includes('SOL') || sym.includes('TAO'))
      return '₩' + Math.round(v).toLocaleString('ko-KR');
    return '₩' + v.toLocaleString('ko-KR', {{maximumFractionDigits:2}});
  }};
  const p = _strategyParams;
  // Colored pill condition badge helpers — buy unified green, sell unified red
  const buyPill  = (label, ok, title)  => `<span class="cp ${{ok ? 'cp-buy' : 'cp-dim'}}" title="${{title}}">${{label}}</span>`;
  const sellPill = (label, on_, title) => `<span class="cp ${{on_ ? 'cp-sell' : 'cp-dim'}}" title="${{title}}">${{label}}</span>`;

  const rows = items.map(t => {{
    const m = t.metadata || {{}};
    const c = m.cond || {{}};
    const rsi = m.rsi != null ? m.rsi.toFixed(1) : '-';
    const rsiColor = c.overbought ? '#ef4444' : c.rsi_ok ? '#10b981' : '#94a3b8';
    const macdVal = m.macd_hist != null ? (m.macd_hist>=0?'+':'')+m.macd_hist.toFixed(4) : '-';
    const macdColor = m.macd_hist > 0 ? '#10b981' : m.macd_hist < 0 ? '#ef4444' : '#94a3b8';
    const price = fmtPrice(t.symbol, m.price);
    const ts = t.timestamp ? t.timestamp.substring(11,19) : '';
    const ac = actionColor(t.action);
    const actionChanged = flashSymbol === t.symbol && prevTick && prevTick.action !== t.action;
    const flash = flashSymbol === t.symbol ? ' tick-flash' : '';
    const actionDot = actionChanged ? `<span style="font-size:.65rem;color:#f59e0b;margin-left:.3rem">▲</span>` : '';
    const volRatio = m.vol_ratio != null ? m.vol_ratio.toFixed(2)+'x' : '-';
    const emaDiff = (m.ema_fast != null && m.ema_slow != null)
      ? (m.ema_fast - m.ema_slow >= 0 ? '+' : '') + (m.ema_fast - m.ema_slow).toFixed(2)
      : '-';
    const emaDiffColor = c.above_ema ? '#10b981' : '#ef4444';

    const hasCond = Object.keys(c).length > 0;
    const condCell = !hasCond ? '<td>-</td>' : `<td style="white-space:nowrap">
      ${{buyPill('E',c.above_ema,'EMA 상승 정렬')}}${{buyPill('M',c.macd_just_pos,'MACD 양전환')}}${{buyPill('R',c.rsi_ok,'RSI 범위 내')}}${{buyPill('V',c.vol_ok,'거래량 충분')}}${{buyPill('A',c.adx_ok,'ADX 추세 강도 ≥25')}}
      <span style="margin:0 3px;color:#334155;font-size:.7rem">│</span>
      ${{sellPill('D',c.death_cross,'데스크로스')}}${{sellPill('O',c.overbought,'과매수')}}${{sellPill('M',c.macd_turned_neg,'MACD 음전환')}}
    </td>`;

    return `<tr class="${{flash}}" id="tick-row-${{t.symbol.replace('/','_')}}">
      <td><code>${{t.symbol}}</code></td>
      <td><span class="badge" style="background:${{ac}}22;color:${{ac}}">${{t.action}}</span>${{actionDot}}</td>
      <td style="font-weight:600">${{price}}</td>
      <td style="color:${{emaDiffColor}};font-size:.8rem">${{emaDiff}}</td>
      <td style="color:${{macdColor}};font-size:.8rem">${{macdVal}}</td>
      <td style="color:${{rsiColor}};font-size:.8rem">${{rsi}}</td>
      <td style="color:#94a3b8;font-size:.8rem">${{volRatio}}</td>
      ${{condCell}}
      <td style="color:#475569;font-size:.75rem">${{ts}}</td>
    </tr>`;
  }});
  el.innerHTML = `<table class="ticks-table"><thead><tr>
    <th>종목</th><th>신호</th><th>현재가</th>
    <th title="EMA Fast - EMA Slow">EMA차이</th>
    <th title="MACD histogram">MACD Hist</th>
    <th title="RSI">RSI</th>
    <th title="거래량배율 (20봉 평균 대비)">거래량</th>
    <th title="매수: E(EMA) M(MACD) R(RSI) V(VOL) │ 매도: D(데스크로스) O(과매수) M(MACD↓)">조건</th>
    <th>시각</th>
  </tr></thead><tbody>${{rows.join('')}}</tbody></table>`;
}}
function renderBalance(items) {{
  const el = document.getElementById('balance-body');
  if (!el) return;
  if (!items || items.length === 0) {{ el.innerHTML = '<p class="empty">잔고 없음</p>'; return; }}
  const fmtKRW = v => '₩' + Math.round(v).toLocaleString('ko-KR');
  const fmtQty = v => parseFloat(v.toFixed(8)).toString();
  const fmtPrice = v => v >= 1 ? fmtKRW(v) : '₩' + v.toFixed(6);
  const cards = items.map(b => {{
    const avgBuy = b.avg_buy_price || 0;
    const usedRow = b.used > 0
      ? `<div style="display:flex;justify-content:space-between;margin:.25rem 0">
           <span style="color:#64748b;font-size:.75rem">주문중</span>
           <span style="color:#64748b;font-size:.75rem">${{fmtQty(b.used)}}</span>
         </div>` : '';
    return `<div style="background:#0f172a;border:1px solid #334155;border-radius:.6rem;padding:.9rem">
      <div style="font-size:1rem;font-weight:800;color:#f1f5f9;margin-bottom:.65rem;display:flex;justify-content:space-between;align-items:baseline">
        <span>${{b.currency}}</span>
        <span style="font-size:.7rem;font-weight:400;color:#64748b">${{fmtQty(b.free)}}</span>
      </div>
      ${{usedRow}}
      <div style="display:flex;justify-content:space-between;margin:.25rem 0">
        <span style="color:#64748b;font-size:.78rem">현재가</span>
        <span style="color:#94a3b8;font-size:.82rem">${{fmtPrice(b.price || 0)}}</span>
      </div>
      <div style="display:flex;justify-content:space-between;margin:.25rem 0">
        <span style="color:#64748b;font-size:.78rem">매수평균가</span>
        <span style="color:#60a5fa;font-size:.82rem">${{avgBuy > 0 ? fmtPrice(avgBuy) : '-'}}</span>
      </div>
      <div style="border-top:1px solid #1e293b;margin:.55rem 0 .4rem"></div>
      <div style="display:flex;justify-content:space-between;margin:.25rem 0">
        <span style="color:#64748b;font-size:.78rem">매수금액</span>
        <span style="color:#a78bfa;font-size:.82rem">${{b.buy_amount > 0 ? fmtKRW(b.buy_amount) : '-'}}</span>
      </div>
      <div style="display:flex;justify-content:space-between;margin:.25rem 0">
        <span style="color:#64748b;font-size:.78rem">평가금액</span>
        <span style="color:#10b981;font-weight:700;font-size:.88rem">${{fmtKRW(b.eval_amount || 0)}}</span>
      </div>
    </div>`;
  }});
  const total = items.reduce((s, b) => s + (b.eval_amount || 0), 0);
  el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.75rem">${{cards.join('')}}</div>
    <div class="stat-row" style="margin-top:.75rem;border-top:1px solid #334155;padding-top:.6rem">
      <span class="stat-label">총 평가금액</span>
      <span class="stat-value" style="color:#10b981;font-weight:700">${{fmtKRW(total)}}</span>
    </div>`;
}}
function renderBuyHistory(trades) {{
  const el = document.getElementById('tab-buys');
  if (!el) return;
  if (!trades || trades.length === 0) {{ el.innerHTML = '<p class="empty">매수 이력 없음</p>'; return; }}
  const fmtKRW = v => '₩' + Math.round(v).toLocaleString('ko-KR');
  const rows = trades.map(t => {{
    const dt = t.entry_time ? t.entry_time.substring(0,16).replace('T',' ') : '-';
    const cost = t.entry_price * t.amount;
    return `<tr>
      <td style="color:#64748b;font-size:.78rem">${{dt}}</td>
      <td><code>${{t.symbol}}</code></td>
      <td style="color:#94a3b8">${{t.amount.toFixed ? t.amount.toFixed(6) : t.amount}}</td>
      <td style="font-weight:600;color:#10b981">${{fmtKRW(t.entry_price)}}</td>
      <td style="color:#94a3b8">${{fmtKRW(cost)}}</td>
    </tr>`;
  }});
  el.innerHTML = `<table><thead><tr>
    <th>매수 시각</th><th>종목</th><th>수량</th><th>매수가</th><th>매수 금액</th>
  </tr></thead><tbody>${{rows.join('')}}</tbody></table>`;
}}
function renderSellHistory(trades) {{
  const el = document.getElementById('tab-sells');
  if (!el) return;
  if (!trades || trades.length === 0) {{ el.innerHTML = '<p class="empty">매도 이력 없음</p>'; return; }}
  const fmtKRW = v => '₩' + Math.round(v).toLocaleString('ko-KR');
  const rows = trades.map(t => {{
    const dt = t.exit_time ? t.exit_time.substring(0,16).replace('T',' ') : '-';
    const pnl = t.pnl || 0;
    const pct = t.pnl_pct || 0;
    const cls = pnl >= 0 ? '#10b981' : '#ef4444';
    const sign = pnl >= 0 ? '+' : '';
    return `<tr>
      <td style="color:#64748b;font-size:.78rem">${{dt}}</td>
      <td><code>${{t.symbol}}</code></td>
      <td style="color:#94a3b8">${{t.amount.toFixed ? t.amount.toFixed(6) : t.amount}}</td>
      <td style="font-weight:600;color:#ef4444">${{fmtKRW(t.exit_price)}}</td>
      <td style="color:${{cls}};font-weight:600">${{sign}}${{fmtKRW(pnl)}} (${{sign}}${{pct.toFixed(2)}}%)</td>
      <td style="color:#475569;font-size:.72rem">${{t.reason || '-'}}</td>
    </tr>`;
  }});
  el.innerHTML = `<table><thead><tr>
    <th>매도 시각</th><th>종목</th><th>수량</th><th>매도가</th><th>손익</th><th>사유</th>
  </tr></thead><tbody>${{rows.join('')}}</tbody></table>`;
}}
function renderEquity(pts) {{
  const wrap = document.getElementById('chart-wrap');
  if (!pts || pts.length === 0) {{ wrap.innerHTML = '<p class="empty">거래 없음 — 첫 청산 후 표시됩니다</p>'; return; }}

  // ── 이번 달 1일~오늘 날짜 배열 생성 ────────────────────────────────
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const prefix = `${{yyyy}}-${{mm}}`;
  const todayStr = today.toISOString().substring(0, 10);

  // 이번 달 거래 데이터만 필터링: 날짜별 마지막 누적 손익
  const byDate = {{}};
  pts.forEach(p => {{
    const date = p.time ? p.time.substring(0, 10) : '';
    if (date.startsWith(prefix)) byDate[date] = p.pnl || 0;
  }});

  // 1일~오늘까지 전체 날짜 배열 생성 (carry-forward)
  const allDates = [];
  for (let d = 1; d <= today.getDate(); d++) {{
    allDates.push(`${{prefix}}-${{String(d).padStart(2, '0')}}`);
  }}

  // 거래 없는 날은 직전 누적값 유지
  let carry = 0;
  const cumByDate = {{}};
  allDates.forEach(date => {{
    if (date in byDate) carry = byDate[date];
    cumByDate[date] = carry;
  }});

  const maxAbs = Math.max(...allDates.map(d => Math.abs(cumByDate[d])), 1);
  const W = 1000, H = 180, padL = 58, padR = 14, padT = 28, padB = 28;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;
  const n = allDates.length;
  const barW = Math.max(6, chartW / n - 3);

  // 0원 기준선 위치: 최대/최소 비율로 계산
  const maxVal = Math.max(...allDates.map(d => cumByDate[d]), 0);
  const minVal = Math.min(...allDates.map(d => cumByDate[d]), 0);
  const range = Math.max(maxVal - minVal, 1);
  const zeroY = padT + chartH * maxVal / range;

  let svgParts = [];

  // 배경 눈금선 (3개)
  [-1, 0, 1].forEach(factor => {{
    const lineV = factor * maxAbs;
    const lineY = padT + chartH * (maxVal - lineV) / range;
    if (lineY >= padT && lineY <= padT + chartH) {{
      const sign = lineV > 0 ? '+' : '';
      svgParts.push(`<line x1="${{padL}}" y1="${{lineY.toFixed(1)}}" x2="${{W-padR}}" y2="${{lineY.toFixed(1)}}" stroke="#1e293b" stroke-width="1" stroke-dasharray="${{factor===0?'4':'2'}}"/>`);
      const labelV = Math.round(Math.abs(lineV));
      if (labelV > 0) {{
        svgParts.push(`<text x="${{padL-4}}" y="${{(lineY+3).toFixed(1)}}" fill="#475569" font-size="9" text-anchor="end">${{sign}}${{lineV >= 0 ? '' : '-'}}${{labelV.toLocaleString('ko-KR')}}</text>`);
      }}
    }}
  }});

  // 막대 + 날짜 레이블
  allDates.forEach((date, i) => {{
    const v = cumByDate[date];
    const x = padL + (i + 0.5) * chartW / n - barW / 2;
    const color = v >= 0 ? '#10b981' : '#ef4444';
    const barH = Math.max(1, Math.abs(v) / range * chartH);
    const y = v >= 0 ? zeroY - barH : zeroY;
    svgParts.push(`<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{barW.toFixed(1)}}" height="${{barH.toFixed(1)}}" fill="${{color}}" rx="1" opacity="0.85"/>`);

    // 막대 위 값 표시 (막대가 충분히 클 때만)
    if (barH > 14) {{
      const sign = v >= 0 ? '+' : '';
      const labelY = v >= 0 ? y - 3 : y + barH + 10;
      svgParts.push(`<text x="${{(x+barW/2).toFixed(1)}}" y="${{labelY.toFixed(1)}}" fill="${{color}}" font-size="8" text-anchor="middle" font-weight="600">${{sign}}${{Math.round(v).toLocaleString('ko-KR')}}</text>`);
    }}

    // 날짜 레이블: 1일, 5일 단위
    const day = parseInt(date.substring(8));
    if (day === 1 || day % 5 === 0 || date === todayStr) {{
      const labelColor = date === todayStr ? '#f8fafc' : '#64748b';
      svgParts.push(`<text x="${{(x+barW/2).toFixed(1)}}" y="${{(H-6).toFixed(1)}}" fill="${{labelColor}}" font-size="9" text-anchor="middle">${{day}}일</text>`);
    }}
  }});

  // 월 제목 + 누적 합계
  const totalPnl = cumByDate[todayStr] || 0;
  const totalSign = totalPnl >= 0 ? '+' : '';
  const totalColor = totalPnl >= 0 ? '#10b981' : '#ef4444';
  svgParts.push(`<text x="${{padL}}" y="16" fill="#94a3b8" font-size="10">${{yyyy}}년 ${{parseInt(mm)}}월 누적 손익</text>`);
  svgParts.push(`<text x="${{W-padR}}" y="16" fill="${{totalColor}}" font-size="11" text-anchor="end" font-weight="700">${{totalSign}}₩${{Math.round(totalPnl).toLocaleString('ko-KR')}}</text>`);

  wrap.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">${{svgParts.join('')}}</svg>`;
}}
async function engineAction(action) {{
  try {{
    const res = await fetch('/api/engine/'+action, {{method:'POST'}});
    const d = await res.json();
    showToast(d.message || action + ' 완료');
    setTimeout(refresh, 300);
  }} catch(e) {{ showToast('오류: '+e.message); }}
}}
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(()=>{{t.style.display='none'}}, 3000);
}}
setInterval(refresh, 30000);
// Initialize on load
(function() {{
  // Seed _ticksMap from server-rendered ticks data
  {_ticks_seed_js}
  _initTickStream();
  // Load strategy info immediately
  fetch('/api/strategy').then(r=>r.json()).then(renderStrategy).catch(()=>{{}});
}})();
</script>
</body>
</html>"""


def _badge(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}20;color:{color}">{text}</span>'


def _render_pnl(pnl: dict) -> str:
    cash = pnl.get("cash", 0.0)
    realized = pnl.get("realized_pnl", 0.0)
    unrealized = pnl.get("unrealized_pnl", 0.0)

    def fmt(v: float) -> str:
        sign = "+" if v >= 0 else ""
        cls = "win" if v >= 0 else "loss"
        return f'<span class="{cls}">{sign}{v:,.2f}</span>'

    return f"""<div class="card" id="pnl-card">
      <h2>포트폴리오</h2>
      <div class="stat-row"><span class="stat-label">현금 잔고</span><span class="stat-value">{cash:,.2f}</span></div>
      <div class="stat-row"><span class="stat-label">실현 손익</span><span class="stat-value">{fmt(realized)}</span></div>
      <div class="stat-row"><span class="stat-label">미실현 손익</span><span class="stat-value">{fmt(unrealized)}</span></div>
    </div>"""


def _render_stats(stats: dict) -> str:
    if not stats:
        return """<div class="card" id="stats-card" style="margin-bottom:1.2rem"><h2>성과 분석</h2><p class="empty">거래 없음</p></div>"""

    total = stats.get("total_trades", 0)
    wr = stats.get("win_rate_pct", 0.0)
    pf = stats.get("profit_factor", 0.0)
    avg_win = stats.get("avg_win", 0.0)
    avg_loss = stats.get("avg_loss", 0.0)
    total_pnl = stats.get("total_pnl", 0.0)

    wr_color = "#10b981" if wr >= 50 else "#ef4444"
    pf_display = f"{pf:.2f}" if pf != float("inf") else "∞"
    pnl_class = "win" if total_pnl >= 0 else "loss"
    sign = "+" if total_pnl >= 0 else ""

    return f"""<div class="card" id="stats-card" style="margin-bottom:1.2rem">
      <h2>성과 분석 ({total}건)</h2>
      <div class="stats-grid">
        <div class="stat-box"><div class="val" style="color:{wr_color}">{wr:.1f}%</div><div class="lbl">승률</div></div>
        <div class="stat-box"><div class="val">{pf_display}</div><div class="lbl">수익 팩터</div></div>
        <div class="stat-box"><div class="val win">+{avg_win:,.2f}</div><div class="lbl">평균 수익</div></div>
        <div class="stat-box"><div class="val loss">-{avg_loss:,.2f}</div><div class="lbl">평균 손실</div></div>
        <div class="stat-box"><div class="val {pnl_class}">{sign}{total_pnl:,.2f}</div><div class="lbl">총 손익</div></div>
      </div>
    </div>"""


def _render_equity_svg(equity: list[dict]) -> str:
    """Render current-month cumulative P&L bar chart as inline SVG."""
    from datetime import date as _date, timedelta

    if not equity:
        return '<p class="empty">거래 없음 — 첫 청산 후 표시됩니다</p>'

    today = _date.today()
    prefix = today.strftime("%Y-%m")

    # 이번 달 날짜별 마지막 누적 손익
    by_date: dict[str, float] = {}
    for pt in equity:
        d = pt.get("time", "")[:10]
        if d.startswith(prefix):
            by_date[d] = float(pt.get("pnl", 0.0))

    # 1일~오늘 전체 날짜 carry-forward
    all_dates: list[str] = []
    cur = today.replace(day=1)
    while cur <= today:
        all_dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    if not all_dates:
        return '<p class="empty">거래 없음</p>'

    carry = 0.0
    cum: dict[str, float] = {}
    for d in all_dates:
        if d in by_date:
            carry = by_date[d]
        cum[d] = carry

    values = [cum[d] for d in all_dates]
    max_val = max(max(values), 0.0)
    min_val = min(min(values), 0.0)
    v_range = max(max_val - min_val, 1.0)
    max_abs = max(abs(max_val), abs(min_val), 1.0)

    W, H = 1000, 180
    pad_l, pad_r, pad_t, pad_b = 58, 14, 28, 28
    chart_w = W - pad_l - pad_r
    chart_h = H - pad_t - pad_b
    n = len(all_dates)
    bar_w = max(6.0, chart_w / n - 3)
    zero_y = pad_t + chart_h * max_val / v_range

    parts: list[str] = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">']

    # 눈금선 (상단/0/하단)
    for factor in (-1, 0, 1):
        line_v = factor * max_abs
        line_y = pad_t + chart_h * (max_val - line_v) / v_range
        if pad_t <= line_y <= pad_t + chart_h:
            dash = "4" if factor == 0 else "2"
            parts.append(
                f'<line x1="{pad_l}" y1="{line_y:.1f}" x2="{W-pad_r}" y2="{line_y:.1f}" '
                f'stroke="#1e293b" stroke-width="1" stroke-dasharray="{dash}"/>'
            )
            if abs(line_v) > 0:
                sign = "+" if line_v > 0 else "-"
                parts.append(
                    f'<text x="{pad_l - 4}" y="{line_y + 3:.1f}" fill="#475569" '
                    f'font-size="9" text-anchor="end">{sign}₩{round(abs(line_v)):,}</text>'
                )

    # 막대 + 날짜 레이블
    today_str = today.strftime("%Y-%m-%d")
    for i, d in enumerate(all_dates):
        v = cum[d]
        x = pad_l + (i + 0.5) * chart_w / n - bar_w / 2
        color = "#10b981" if v >= 0 else "#ef4444"
        bh = max(1.0, abs(v) / v_range * chart_h)
        y = zero_y - bh if v >= 0 else zero_y
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'fill="{color}" rx="1" opacity="0.85"/>'
        )
        # 막대 값 레이블 (막대가 충분히 클 때)
        if bh > 14:
            sign = "+" if v >= 0 else ""
            label_y = y - 3 if v >= 0 else y + bh + 10
            parts.append(
                f'<text x="{x + bar_w/2:.1f}" y="{label_y:.1f}" fill="{color}" '
                f'font-size="8" text-anchor="middle" font-weight="600">{sign}₩{round(v):,}</text>'
            )
        # 날짜 레이블: 1일, 5단위, 오늘
        day_num = int(d[8:])
        if day_num == 1 or day_num % 5 == 0 or d == today_str:
            label_color = "#f8fafc" if d == today_str else "#64748b"
            parts.append(
                f'<text x="{x + bar_w/2:.1f}" y="{H - 6}" fill="{label_color}" '
                f'font-size="9" text-anchor="middle">{day_num}일</text>'
            )

    # 월 제목 + 누적 합계
    total = cum.get(today_str, 0.0)
    total_color = "#10b981" if total >= 0 else "#ef4444"
    sign = "+" if total >= 0 else ""
    year, mon = today.year, today.month
    parts.append(
        f'<text x="{pad_l}" y="16" fill="#94a3b8" font-size="10">{year}년 {mon}월 누적 손익</text>'
    )
    parts.append(
        f'<text x="{W - pad_r}" y="16" fill="{total_color}" font-size="11" '
        f'text-anchor="end" font-weight="700">{sign}₩{round(total):,}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _render_positions(positions: list[dict]) -> str:
    if not positions:
        return '<p class="empty">오픈 포지션 없음</p>'

    rows = []
    for pos in positions:
        sl = f"{pos['stop_loss']:,.2f}" if pos.get("stop_loss") else "—"
        tp = f"{pos['take_profit']:,.2f}" if pos.get("take_profit") else "—"
        rows.append(
            f"<tr>"
            f"<td><code>{pos['symbol']}</code></td>"
            f"<td>{pos['side'].upper()}</td>"
            f"<td>{pos['amount']:.6f}</td>"
            f"<td>{pos['entry_price']:,.2f}</td>"
            f"<td>{sl}</td><td>{tp}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>종목</th><th>방향</th><th>수량</th>"
        "<th>진입가</th><th>손절가</th><th>목표가</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_ticks(ticks: list[dict]) -> str:
    if not ticks:
        return '<p class="empty">아직 평가 없음 (첫 tick 대기 중)</p>'
    action_color = {"BUY": "#10b981", "SELL": "#ef4444", "HOLD": "#64748b"}
    rows = []
    for t in ticks:
        action = t.get("action", "HOLD")
        color = action_color.get(action, "#64748b")
        meta = t.get("metadata") or {}
        price = meta.get("price")
        sym = t["symbol"]
        if price is not None:
            if any(k in sym for k in ("BTC", "ETH", "SOL", "TAO")):
                price_str = f"₩{price:,.0f}"
            else:
                price_str = f"₩{price:,.2f}"
        else:
            price_str = "-"
        rsi = f"{meta['rsi']:.1f}" if "rsi" in meta else "-"
        macd_h = meta.get("macd_hist")
        macd_str = f"{macd_h:+.4f}" if macd_h is not None else "-"
        ts = (t.get("timestamp") or "")[-8:-3]
        rows.append(
            f"<tr>"
            f"<td><code>{sym}</code></td>"
            f"<td><span class='badge' style='background:{color}22;color:{color}'>{action}</span></td>"
            f"<td style='font-weight:600'>{price_str}</td>"
            f"<td style='color:#94a3b8;font-size:.78rem'>RSI {rsi}</td>"
            f"<td style='color:#94a3b8;font-size:.78rem'>MACD {macd_str}</td>"
            f"<td style='color:#475569;font-size:.72rem'>{ts}</td>"
            f"</tr>"
        )
    return (
        '<table class="ticks-table"><thead><tr>'
        "<th>종목</th><th>신호</th><th>현재가</th><th>RSI</th><th>MACD Hist</th><th>시각</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_balance(balance: list[dict]) -> str:
    if not balance:
        return '<p class="empty">잔고 없음</p>'

    cards = []
    for b in balance:
        currency = b["currency"]
        free = b["free"]
        used = b["used"]
        price = b.get("price", 0.0)
        eval_amount = b.get("eval_amount", 0)
        avg_buy_price = b.get("avg_buy_price", 0.0)
        buy_amount = b.get("buy_amount", 0)

        fmt_qty = f"{free:.8f}".rstrip("0").rstrip(".")
        fmt_price = f"₩{price:,.0f}" if price >= 1 else f"₩{price:.6f}"
        fmt_eval = f"₩{eval_amount:,}"
        fmt_avg = (
            f"₩{avg_buy_price:,.0f}" if avg_buy_price >= 1
            else f"₩{avg_buy_price:.6f}" if avg_buy_price > 0
            else "-"
        )
        fmt_buy_amt = f"₩{buy_amount:,}" if buy_amount > 0 else "-"
        used_row = (
            f'<div style="display:flex;justify-content:space-between;margin:.25rem 0">'
            f'<span style="color:#64748b;font-size:.75rem">주문중</span>'
            f'<span style="color:#64748b;font-size:.75rem">{f"{used:.8f}".rstrip("0").rstrip(".")}</span>'
            f'</div>'
        ) if used > 0 else ""

        cards.append(
            f'<div style="background:#0f172a;border:1px solid #334155;border-radius:.6rem;padding:.9rem">'
            f'<div style="font-size:1rem;font-weight:800;color:#f1f5f9;margin-bottom:.65rem;'
            f'display:flex;justify-content:space-between;align-items:baseline">'
            f'<span>{currency}</span>'
            f'<span style="font-size:.7rem;font-weight:400;color:#64748b">{fmt_qty}</span>'
            f'</div>'
            f'{used_row}'
            f'<div style="display:flex;justify-content:space-between;margin:.25rem 0">'
            f'<span style="color:#64748b;font-size:.78rem">현재가</span>'
            f'<span style="color:#94a3b8;font-size:.82rem">{fmt_price}</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;margin:.25rem 0">'
            f'<span style="color:#64748b;font-size:.78rem">매수평균가</span>'
            f'<span style="color:#60a5fa;font-size:.82rem">{fmt_avg}</span>'
            f'</div>'
            f'<div style="border-top:1px solid #1e293b;margin:.55rem 0 .4rem"></div>'
            f'<div style="display:flex;justify-content:space-between;margin:.25rem 0">'
            f'<span style="color:#64748b;font-size:.78rem">매수금액</span>'
            f'<span style="color:#a78bfa;font-size:.82rem">{fmt_buy_amt}</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;margin:.25rem 0">'
            f'<span style="color:#64748b;font-size:.78rem">평가금액</span>'
            f'<span style="color:#10b981;font-weight:700;font-size:.88rem">{fmt_eval}</span>'
            f'</div>'
            f'</div>'
        )

    total_eval = sum(b.get("eval_amount", 0) for b in balance)
    grid = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.75rem">'
        + "".join(cards)
        + "</div>"
        + f'<div class="stat-row" style="margin-top:.75rem;border-top:1px solid #334155;padding-top:.6rem">'
        f'<span class="stat-label">총 평가금액</span>'
        f'<span class="stat-value" style="color:#10b981;font-weight:700">₩{total_eval:,}</span>'
        f"</div>"
    )
    return grid


def _render_buy_history(trades: list[dict]) -> str:
    if not trades:
        return '<p class="empty">매수 이력 없음</p>'
    rows = []
    for t in trades:
        entry_dt = t["entry_time"][:16].replace("T", " ")
        cost = t["entry_price"] * t["amount"]
        rows.append(
            f"<tr>"
            f"<td style='color:#64748b;font-size:.78rem'>{entry_dt}</td>"
            f"<td><code>{t['symbol']}</code></td>"
            f"<td style='color:#94a3b8'>{t['amount']:.6f}</td>"
            f"<td style='font-weight:600;color:#10b981'>₩{t['entry_price']:,.0f}</td>"
            f"<td style='color:#94a3b8'>₩{cost:,.0f}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>매수 시각</th><th>종목</th><th>수량</th><th>매수가</th><th>매수 금액</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_sell_history(trades: list[dict]) -> str:
    if not trades:
        return '<p class="empty">매도 이력 없음</p>'
    rows = []
    for t in trades:
        exit_dt = t["exit_time"][:16].replace("T", " ")
        pnl = t["pnl"]
        sign = "+" if pnl >= 0 else ""
        cls = "win" if t["is_win"] else "loss"
        rows.append(
            f"<tr>"
            f"<td style='color:#64748b;font-size:.78rem'>{exit_dt}</td>"
            f"<td><code>{t['symbol']}</code></td>"
            f"<td style='color:#94a3b8'>{t['amount']:.6f}</td>"
            f"<td style='font-weight:600;color:#ef4444'>₩{t['exit_price']:,.0f}</td>"
            f"<td class='{cls}'>{sign}₩{pnl:,.0f} ({sign}{t['pnl_pct']:.2f}%)</td>"
            f"<td style='color:#475569;font-size:.72rem'>{t['reason']}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>매도 시각</th><th>종목</th><th>수량</th><th>매도가</th><th>손익</th><th>사유</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
