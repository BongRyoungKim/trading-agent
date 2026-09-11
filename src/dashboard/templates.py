"""
Inline HTML templates — no Jinja2, no external CDN, no extra dependencies.
Pure f-string HTML with vanilla JS and inline SVG for charting.
"""
from __future__ import annotations

import json as _json

_ICON_PATHS: dict[str, str] = {
    "bolt": '<path d="M13 3 L6 13 H11 L10 21 L18 10 H13 Z"/>',
    "clipboard": (
        '<rect x="5" y="4" width="14" height="17" rx="1.5"/>'
        '<rect x="9" y="2.5" width="6" height="3" rx="1"/>'
        '<line x1="8" y1="11" x2="16" y2="11"/>'
        '<line x1="8" y1="15" x2="16" y2="15"/>'
    ),
    "lock": (
        '<rect x="5" y="11" width="14" height="10" rx="2"/>'
        '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>'
    ),
    "moon": '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z"/>',
    "sun": (
        '<circle cx="12" cy="12" r="4.2"/>'
        '<line x1="12" y1="2.5" x2="12" y2="5"/>'
        '<line x1="12" y1="19" x2="12" y2="21.5"/>'
        '<line x1="2.5" y1="12" x2="5" y2="12"/>'
        '<line x1="19" y1="12" x2="21.5" y2="12"/>'
        '<line x1="5.1" y1="5.1" x2="6.9" y2="6.9"/>'
        '<line x1="17.1" y1="17.1" x2="18.9" y2="18.9"/>'
        '<line x1="5.1" y1="18.9" x2="6.9" y2="17.1"/>'
        '<line x1="17.1" y1="6.9" x2="18.9" y2="5.1"/>'
    ),
    "check": '<path d="M4 12.5 L9 17.5 L20 5.5"/>',
    "cross": '<path d="M5 5 L19 19 M19 5 L5 19"/>',
}


def _icon(name: str, size: float = 14) -> str:
    """절제된 단색 라인 아이콘(SVG, stroke=currentColor) — 이모지 대체용.

    채워진 컬러풀한 아이콘이 아니라 얇고 균일한 획(stroke-width) 기반의 최소
    기하학적 형태만 사용한다. fill 없이 stroke만 써서 currentColor를 그대로
    상속하므로, 배치되는 버튼/제목의 텍스트 색(=팔레트)이 바뀌면 아이콘 색도
    자동으로 따라간다.
    """
    path = _ICON_PATHS[name]
    # 페이지 전역 CSS에 `svg{{width:100%;height:auto}}`(차트용) 규칙이 있어 폭 지정 없이
    # width/height 속성만 주면 컨테이너 100%로 늘어나 버린다 — inline style로 명시해 덮어쓴다.
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
        f'style="width:{size}px;height:{size}px;vertical-align:-2px;flex-shrink:0" '
        'fill="none" stroke="currentColor" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    )


def render_dashboard(
    status: dict,
    positions: list[dict],
    pnl: dict,
    trades: list[dict] | None = None,
    stats: dict | None = None,
    equity: list[dict] | None = None,
    balance: list[dict] | None = None,
    ticks: list[dict] | None = None,
    pending_params: dict | None = None,
) -> str:
    trades = trades or []
    stats = stats or {}
    equity = equity or []
    balance = balance or []
    ticks = ticks or []
    pending_params = pending_params or {"has_pending": False}

    mode = status.get("mode", "unknown").upper()
    paused = status.get("paused", False)
    circuit = status.get("circuit", "UNKNOWN")
    trading_hours = status.get("trading_hours", "24/7")
    trading_days = status.get("trading_days", "all")

    mode_switch_html = _render_mode_switch_btn(mode)
    icon_bolt = _icon("bolt", 15)
    icon_clipboard = _icon("clipboard", 14)
    icon_lock = _icon("lock", 14)
    icon_moon = _icon("moon", 15)
    icon_sun = _icon("sun", 15)
    icon_check = _icon("check", 13)
    icon_cross = _icon("cross", 13)

    # 엔진 상태 카드 — renderStatus() (JS)와 동일한 마크업이어야 최초 로드 후
    # refresh() 호출 시 레이아웃이 바뀌는 깜빡임이 생기지 않는다.
    mode_color = (
        "var(--signal-buy)" if mode == "LIVE"
        else "var(--accent)" if mode == "PAPER"
        else "var(--text-muted)"
    )
    mode_icon = icon_bolt if mode == "LIVE" else icon_clipboard if mode == "PAPER" else "○"
    state_color = "var(--signal-warn)" if paused else "var(--signal-buy)"
    state_icon = "⏸" if paused else "▶"
    circuit_color = "var(--signal-sell)" if circuit != "CLOSED" else "var(--signal-buy)"
    circuit_icon = "⚠" if circuit != "CLOSED" else "✓"
    hours_info = f"{trading_hours} UTC · {trading_days}"

    equity_svg = _render_equity_svg(equity)
    symbol_pnl_svg = _render_symbol_pnl_svg(trades)
    trade_filter_bar_html = _render_trade_filter_bar(trades)
    stats_html = _render_stats(stats)
    positions_html = _render_positions(positions)
    buy_html = _render_buy_history(trades, positions)
    sell_html = _render_sell_history(trades)
    pnl_html = _render_pnl(pnl)
    pending_params_html = _render_pending_params(pending_params)
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
  <script>
    // 테마 깜빡임(FOUC) 방지 — CSS가 적용되기 전에 저장된 테마를 <html> 속성으로 반영한다.
    (function() {{
      try {{
        var t = localStorage.getItem('dashboard-theme') || 'dark';
        document.documentElement.setAttribute('data-theme', t);
      }} catch (e) {{}}
    }})();
  </script>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>트레이딩 에이전트 대시보드</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    /* 라이트/다크 테마 변수 — 구조색 + 시그널(그린/레드/앰버)/강조(액센트)/차트 색까지 전면 변수화.
       그레이스케일(구조/텍스트) 외에 원색을 허용하는 토큰은 시그널 3색 + 액센트 1색뿐이다. */
    :root{{
      --bg:#0b0e14;--bg-inset:#11151d;--card-bg:#161b24;--border:#262d3a;
      --text:#c7ccd6;--text-strong:#f5f7fa;--text-muted:#8b93a6;--text-dim:#5b6478;
      --signal-buy:#1fae74;--signal-buy-bg:rgba(31,174,116,.14);
      --signal-sell:#e5484d;--signal-sell-bg:rgba(229,72,77,.14);
      --signal-warn:#d99a3d;--signal-warn-bg:rgba(217,154,61,.12);
      --accent:#5b8def;--accent-bg:rgba(91,141,239,.14);
      --chart-bg:#0a0d13;--chart-grid:#2a3242;--chart-grid-soft:#1c222e;--chart-highlight:rgba(255,255,255,.04);
      /* 차트 막대 전용 시그널 색 — 구조적 UI(버튼/배지)는 절제된 --signal-*를 쓰고,
         데이터 시각화(막대 그래프)만 어두운 배경 위에서 도드라지도록 채도/명도를 올린다 */
      --chart-buy:#16c784;--chart-sell:#ea3943;
      /* 타이포그래피 스케일 (7단계) + font-weight 3단계 — 페이지 전체에서 이 토큰만 사용 */
      --fs-1:.6875rem;--fs-2:.75rem;--fs-3:.8125rem;--fs-4:.875rem;--fs-5:1rem;--fs-6:1.25rem;--fs-7:1.1875rem;
      --fw-regular:400;--fw-medium:600;--fw-strong:700;
    }}
    html[data-theme="light"]{{
      --bg:#f4f5f7;--bg-inset:#eceef1;--card-bg:#ffffff;--border:#d9dce2;
      --text:#3a3f48;--text-strong:#14161b;--text-muted:#6b7280;--text-dim:#9aa1ad;
      --signal-buy:#0f8a54;--signal-buy-bg:rgba(15,138,84,.10);
      --signal-sell:#c62f35;--signal-sell-bg:rgba(198,47,53,.10);
      --signal-warn:#a3690b;--signal-warn-bg:rgba(163,105,11,.10);
      --accent:#3550b3;--accent-bg:rgba(53,80,179,.10);
      --chart-bg:#e7e9ed;--chart-grid:#c7cbd3;--chart-grid-soft:#d7dae0;--chart-highlight:rgba(0,0,0,.05);
      --chart-buy:#0fae6e;--chart-sell:#d92d3a;
    }}
    body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);font-size:var(--fs-3);min-height:100vh}}
    header{{background:var(--card-bg);border-bottom:1px solid var(--border);padding:1rem 2rem;display:flex;align-items:center;justify-content:space-between}}
    header h1{{font-size:var(--fs-7);font-weight:var(--fw-strong);color:var(--text-strong);display:flex;align-items:center;gap:.45rem}}
    .meta{{font-size:var(--fs-2);color:var(--text-muted)}}
    .theme-toggle-btn{{background:none;border:1px solid var(--border);border-radius:9999px;width:2.1rem;height:2.1rem;line-height:1;cursor:pointer;color:var(--text);display:flex;align-items:center;justify-content:center;padding:0}}
    main{{max-width:1200px;margin:0 auto;padding:1.5rem}}
    .top-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1.2rem;margin-bottom:1.5rem}}
    .card{{background:var(--card-bg);border:1px solid var(--border);border-radius:.75rem;padding:1.25rem}}
    .card h2{{font-size:var(--fs-1);font-weight:var(--fw-strong);text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:.9rem;display:flex;align-items:center;gap:.4rem}}
    .stat-row{{display:flex;justify-content:space-between;align-items:center;margin:.4rem 0}}
    .stat-label{{color:var(--text-muted);font-size:var(--fs-3)}}
    .stat-value{{font-weight:var(--fw-medium);font-size:var(--fs-4)}}
    .badge{{display:inline-block;padding:.2rem .6rem;border-radius:9999px;font-size:var(--fs-1);font-weight:var(--fw-strong)}}
    /* Tabs */
    .tabs{{display:flex;gap:.5rem;margin-bottom:1rem;border-bottom:1px solid var(--border);padding-bottom:.5rem}}
    .tab{{padding:.4rem 1rem;cursor:pointer;border-radius:.4rem .4rem 0 0;font-size:var(--fs-3);color:var(--text-muted);background:none;border:none}}
    .tab.active{{color:var(--text);background:var(--border)}}
    .tab-panel{{display:none}}.tab-panel.active{{display:block}}
    /* Trade filters (기간/종목/승패) */
    .trade-filters{{display:flex;flex-wrap:wrap;gap:.6rem;margin-bottom:1rem}}
    .trade-filters select{{flex:1 1 130px;min-width:110px}}
    select{{background:var(--card-bg);color:var(--text);border:1px solid var(--border);border-radius:.4rem;padding:.45rem .6rem;font-size:var(--fs-3);cursor:pointer}}
    /* Tables */
    table{{width:100%;border-collapse:collapse;font-size:var(--fs-3)}}
    th{{text-align:left;color:var(--text-muted);font-size:var(--fs-1);text-transform:uppercase;padding:.5rem 0;border-bottom:1px solid var(--border)}}
    td{{padding:.55rem 0;border-bottom:1px solid var(--bg-inset)}}
    tr:last-child td{{border-bottom:none}}
    /* Ticks table — centered, bold headers, cell borders */
    .ticks-table th{{text-align:center;font-weight:var(--fw-strong);color:var(--text-muted);font-size:var(--fs-2);border:1.5px solid var(--border);padding:.45rem .5rem;background:var(--bg-inset);text-transform:none}}
    .ticks-table td{{text-align:center;border:1px solid var(--bg-inset);padding:.5rem .4rem;border-bottom:1px solid var(--bg-inset)}}
    .ticks-table tr:last-child td{{border-bottom:1px solid var(--bg-inset)}}
    .empty{{color:var(--text-dim);font-size:var(--fs-4);text-align:center;padding:1.5rem 0}}
    .win{{color:var(--signal-buy)}}.loss{{color:var(--signal-sell)}}
    /* Controls */
    .controls{{display:flex;gap:.75rem;margin-top:1.25rem}}
    button{{padding:.55rem 1.3rem;border:1px solid transparent;border-radius:.35rem;font-weight:var(--fw-medium);letter-spacing:.01em;cursor:pointer;font-size:var(--fs-4);background:transparent;color:var(--text);transition:opacity .15s,background-color .15s}}
    button:hover{{opacity:.85}}
    /* 버튼: 원색 알약 → 절제된 아웃라인 우선, 상태를 바꾸는 주요 액션(LIVE 전환)만 solid 유지 */
    .btn-pause{{background:transparent;color:var(--signal-warn);border-color:var(--signal-warn)}}
    .btn-resume{{background:transparent;color:var(--signal-buy);border-color:var(--signal-buy)}}
    .btn-to-paper{{background:transparent;color:var(--text-muted);border-color:var(--border)}}
    .btn-live{{background:var(--signal-buy);color:#04140d;border-color:var(--signal-buy)}}
    .btn-locked{{background:var(--bg-inset);color:var(--text-dim);cursor:not-allowed;border-color:var(--border)}}
    .mode-switch{{margin-top:1rem;border-top:1px solid var(--border);padding-top:.9rem}}
    /* Strategy criteria — 배지 열/조건식 열/설명 열 폭을 고정한 3열 그리드로 세로 정렬을 맞춘다 */
    .criteria-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;margin-top:.6rem}}
    .criteria-col h3{{font-size:var(--fs-1);font-weight:var(--fw-strong);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem}}
    .criteria-col h3.buy{{color:var(--signal-buy)}}.criteria-col h3.sell{{color:var(--signal-sell)}}
    /* 배지(고정폭) | 조건식(1행)+설명(2행) 2열 그리드. 조건식·설명을 같은 줄에 나란히
       두면(구버전) 길이가 다른 항목끼리 줄바꿈 위치가 들쭉날쭉해져서, 조건식/설명을
       세로로 always 분리한다 — 배지 | 조건식(줄1) / 설명(줄2) 형태 고정. */
    .criteria-row{{display:grid;grid-template-columns:58px 1fr;column-gap:.6rem;align-items:start;padding:.32rem 0;font-size:var(--fs-3);color:var(--text)}}
    /* 그리드 아이템의 기본 min-width:auto(콘텐츠 기준)를 0으로 낮춰, 조건식/설명 텍스트가
       트랙 폭보다 길 때 카드 밖으로 넘치지 않고 트랙 안에서 줄바꿈되게 한다. */
    .criteria-row > *{{min-width:0;justify-self:start}}
    /* 배지 폭을 글자 수와 무관하게 고정(56~60px) — 이전엔 justify-self:start 때문에
       "EMA"(짧음)와 "MACD"(김)가 서로 다른 폭으로 보였다. 여기서만 폭을 명시해 나머지
       틱 패널 등 다른 곳의 .cp(짧은 단일/이중 글자 pill)에는 영향 없게 범위를 좁힌다. */
    .criteria-row .cp{{width:58px;text-align:center}}
    .criteria-row .cond-line code{{display:block;font-size:var(--fs-3);white-space:normal;word-break:keep-all}}
    .criteria-row .desc{{display:block;color:var(--text-muted);font-size:var(--fs-1);margin-top:.15rem}}
    /* 파라미터 pill — 연관 파라미터끼리 구분선으로 그룹핑, Timeframe은 메타값이라 별도 톤(액센트) */
    .param-group{{display:inline-flex;align-items:center;flex-wrap:wrap;gap:.3rem;padding-right:.6rem;margin:.15rem .6rem .15rem 0;border-right:1px solid var(--border)}}
    .param-group:last-of-type{{border-right:none;margin-right:0;padding-right:0}}
    .param-row{{display:inline-flex;align-items:center;gap:.3rem;background:var(--bg-inset);border-radius:.3rem;padding:.2rem .5rem;font-size:var(--fs-2)}}
    .param-key{{color:var(--text-muted)}}.param-val{{color:var(--text-strong);font-weight:var(--fw-medium)}}
    .param-tf{{display:inline-block;background:var(--accent-bg);color:var(--accent);border-radius:.3rem;padding:.2rem .55rem;font-size:var(--fs-2);font-weight:var(--fw-medium)}}
    /* Condition pill badges */
    .cp{{display:inline-block;padding:.15rem .35rem;border-radius:.25rem;font-size:var(--fs-1);font-weight:var(--fw-strong);margin:0 2px;vertical-align:middle;letter-spacing:.02em;white-space:nowrap;transition:opacity .2s}}
    .cp-dim{{background:var(--bg-inset);color:var(--text-dim)}}
    /* Buy condition (active) — unified green */
    .cp-buy{{background:var(--signal-buy-bg);color:var(--signal-buy);border:1px solid var(--signal-buy)}}
    /* Sell trigger (active) — unified red */
    .cp-sell{{background:var(--signal-sell-bg);color:var(--signal-sell);border:1px solid var(--signal-sell)}}
    /* Tick flash animation */
    @keyframes tickFlash{{0%{{background:var(--signal-buy-bg)}}100%{{background:transparent}}}}
    .tick-flash{{animation:tickFlash .8s ease-out}}
    .tick-live-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--signal-buy);margin-right:.4rem;animation:pulse 2s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
    .toast{{position:fixed;bottom:2rem;right:2rem;background:var(--border);padding:.7rem 1.2rem;border-radius:.5rem;font-size:var(--fs-4);display:none;z-index:99}}
    /* SVG chart — 모바일에서 텍스트가 안 뭉개지게 가로 스크롤 허용(아래 미디어쿼리에서 min-width 지정) */
    .chart-wrap{{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;background:var(--bg-inset);border-radius:.75rem;margin-top:.5rem;padding:.5rem}}
    svg{{width:100%;height:auto;display:block}}
    /* Stats grid */
    .stat-box{{background:var(--bg-inset);border-radius:.5rem;padding:.75rem;text-align:center}}
    .stat-box .val{{font-size:var(--fs-6);font-weight:var(--fw-strong);margin-bottom:.25rem}}
    .stat-box .lbl{{font-size:var(--fs-1);color:var(--text-muted);text-transform:uppercase}}
    .status-box{{background:var(--bg-inset);border:1px solid var(--border);border-radius:.5rem;padding:.65rem .5rem;text-align:center}}
    .wr-bar{{height:6px;background:var(--card-bg);border-radius:9999px;overflow:hidden;margin-top:.4rem}}
    .wr-fill{{height:100%;border-radius:9999px;transition:width .6s ease}}
    code{{background:var(--bg-inset);padding:.1rem .3rem;border-radius:.25rem;font-size:var(--fs-3)}}
    /* 2컬럼 레이아웃 (엔진상태+포트폴리오, 성과분석 승률박스 등) — 데스크톱 기본값,
       모바일에서는 아래 미디어쿼리로 1~2컬럼으로 접는다 */
    .row-2{{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;margin-bottom:1.2rem}}
    .pnl-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.6rem;margin-top:.6rem}}
    .stats-flex{{display:grid;grid-template-columns:150px 1fr;gap:1.5rem;align-items:center;margin-top:.6rem}}
    .stat-box-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem}}
    /* 표가 있는 영역은 화면이 좁아지면 컬럼을 찌그러뜨리는 대신 옆으로 스크롤되게 함 */
    #ticks-body,#tab-positions,#tab-buys,#tab-sells{{overflow-x:auto;-webkit-overflow-scrolling:touch}}

    /* ── 모바일 (폭 640px 이하: 대부분의 스마트폰) ─────────────────────────── */
    @media (max-width: 640px) {{
      header{{padding:.75rem 1rem;flex-wrap:wrap;row-gap:.3rem}}
      header h1{{font-size:1rem}}
      main{{padding:1rem .75rem}}
      .card{{padding:1rem}}
      .row-2{{grid-template-columns:1fr;gap:.9rem}}
      .pnl-grid{{grid-template-columns:1fr 1fr;gap:.5rem}}
      .stats-flex{{grid-template-columns:1fr;gap:.9rem}}
      .stat-box-grid{{grid-template-columns:1fr 1fr}}
      .criteria-grid{{grid-template-columns:1fr;gap:1.2rem}}
      .stat-box .val{{font-size:1.05rem}}
      /* 표는 컬럼이 많아 그대로 찌그러지면 못 읽으므로, 최소 너비를 줘서
         좁은 화면에서는 컬럼 폭을 유지한 채 옆으로 스크롤하게 만든다 */
      #ticks-body table{{min-width:720px}}
      #tab-positions table,#tab-buys table,#tab-sells table{{min-width:600px}}
      .chart-wrap svg{{min-width:700px}}
    }}
  </style>
</head>
<body>
<header>
  <h1>{icon_bolt} Trading Agent Dashboard</h1>
  <div style="display:flex;align-items:center;gap:.75rem">
    <span class="meta" id="meta">30초 자동 새로고침</span>
    <button type="button" class="theme-toggle-btn" id="theme-toggle" onclick="toggleTheme()" aria-label="라이트/다크 테마 전환">{icon_moon}</button>
  </div>
</header>
<main>
  <!-- Row 1: 엔진상태 + 포트폴리오 -->
  <div class="row-2">
    <div class="card" id="status-card">
      <h2>엔진 상태</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:.6rem 0">
        <div class="status-box" style="border-color:{mode_color}">
          <div style="color:{mode_color};font-weight:var(--fw-strong);font-size:var(--fs-5);display:flex;align-items:center;justify-content:center;gap:.35rem">{mode_icon}<span>{mode}</span></div>
          <div style="color:var(--text-dim);font-size:var(--fs-1);margin-top:.3rem">모드</div>
        </div>
        <div class="status-box" style="border-color:{state_color}">
          <div style="color:{state_color};font-weight:var(--fw-strong);font-size:var(--fs-5);display:flex;align-items:center;justify-content:center;gap:.35rem"><span>{state_icon}</span><span>{"PAUSED" if paused else "RUNNING"}</span></div>
          <div style="color:var(--text-dim);font-size:var(--fs-1);margin-top:.3rem">상태</div>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;background:var(--bg-inset);border-radius:.4rem;padding:.45rem .7rem;margin-bottom:.4rem">
        <span style="color:var(--text-muted);font-size:var(--fs-2)">서킷 브레이커</span>
        <span style="color:{circuit_color};font-weight:var(--fw-medium);font-size:var(--fs-3)">{circuit_icon} {circuit}</span>
      </div>
      <div style="color:var(--text-dim);font-size:var(--fs-2);text-align:right">{hours_info}</div>
      {mode_switch_html}
    </div>
    {pnl_html}
  </div>
  <!-- Row 2: 성과분석 -->
  {stats_html}

  <!-- Pending parameter change (performance-gate approval) -->
  <div id="pending-params-card" class="card" style="margin-bottom:1.2rem">{pending_params_html}</div>

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
        <polyline points="0,11 12,11 18,2 24,20 30,11 44,11" fill="none" stroke="var(--signal-buy)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="47" cy="11" r="3" fill="var(--signal-buy)" style="animation:pulse 2s infinite"/>
        <rect x="55" y="3" width="30" height="15" rx="3" fill="var(--signal-buy-bg)" stroke="var(--signal-buy)" stroke-width=".8"/>
        <text x="70" y="14.5" text-anchor="middle" font-family="system-ui,-apple-system,sans-serif" font-size="8" font-weight="800" letter-spacing=".08em" fill="var(--signal-buy)">LIVE</text>
        <text x="92" y="16" font-family="system-ui,-apple-system,sans-serif" font-size="12.5" font-weight="700" fill="var(--text-strong)">신호 평가 현황</text>
      </svg>
      <span id="tick-last-time" style="font-size:var(--fs-2);color:var(--text-dim);font-weight:var(--fw-regular)"></span>
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

  <!-- Symbol P&L comparison -->
  <div class="card" style="margin-bottom:1.2rem">
    <h2>심볼별 손익 비교</h2>
    <div class="chart-wrap" id="symbol-pnl-wrap">
      {symbol_pnl_svg}
    </div>
  </div>

  <!-- Tabs: Positions / Buy / Sell -->
  <div class="card">
    <div class="trade-filters" id="trade-filters">
      {trade_filter_bar_html}
    </div>
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
// 절제된 라인 아이콘(SVG, stroke=currentColor) — 이모지 대체. 서버에서 렌더된 동일한
// 마크업을 그대로 재사용해 SSR/CSR 아이콘이 항상 일치하도록 한다.
const ICON_BOLT = '{icon_bolt}';
const ICON_CLIPBOARD = '{icon_clipboard}';
const ICON_LOCK = '{icon_lock}';
const ICON_MOON = '{icon_moon}';
const ICON_SUN = '{icon_sun}';
const ICON_CHECK = '{icon_check}';
const ICON_CROSS = '{icon_cross}';
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}}
let _strategyParams = {{}};
let _positionsMap = {{}};  // symbol → position (entry_price, amount)
let _lastTrades = [];  // 필터(기간/종목/승패)가 적용되기 전 원본 거래 목록 캐시
async function refresh() {{
  try {{
    const [s, pos, pnl, trades, stats, eq, bal, ticks, strat, pendingParams] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/positions').then(r=>r.json()),
      fetch('/api/pnl').then(r=>r.json()),
      fetch('/api/trades?limit=200').then(r=>r.json()),
      fetch('/api/stats').then(r=>r.json()),
      fetch('/api/equity').then(r=>r.json()),
      fetch('/api/balance').then(r=>r.json()),
      fetch('/api/ticks').then(r=>r.json()),
      fetch('/api/strategy').then(r=>r.json()),
      fetch('/api/params/pending').then(r=>r.json()),
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
    renderPendingParams(pendingParams);
    (ticks || []).forEach(t => {{ _ticksMap[t.symbol] = t; }});
    renderTicks(ticks);
    renderPositions(pos);
    _lastTrades = trades || [];
    _populateSymbolFilter(_lastTrades);
    applyTradeFilters();
    renderSymbolPnl(_lastTrades);
    // Update positions tab count (buy/sell 탭 카운트는 applyTradeFilters()가 갱신)
    const mainTabs = document.querySelectorAll('.tabs .tab');
    if (mainTabs[0]) mainTabs[0].textContent = `오픈 포지션 (${{(pos||[]).length}})`;
  }} catch(e) {{ console.warn('Refresh failed', e); }}
}}
function renderStrategy(s) {{
  const el = document.getElementById('strategy-body');
  if (!el || !s || !s.name) {{ if(el) el.innerHTML = '<p class="empty">전략 정보 없음</p>'; return; }}
  _strategyParams = s.parameters || {{}};
  const p = _strategyParams;
  const tf = s.timeframe || '-';

  // 파라미터 pill — 연관 값끼리 그룹핑(EMA / RSI·과매수 / 변동성) + Timeframe은
  // 임계값이 아닌 메타 설정이라 별도 톤(.param-tf)으로 분리해 산만함을 줄인다.
  const pill = (k, v) => v != null
    ? `<span class="param-row"><span class="param-key">${{k}}</span><span class="param-val">${{v}}</span></span>` : '';
  const groups = [
    [pill('EMA Fast', p.ema_fast), pill('EMA Slow', p.ema_slow)],
    [pill('RSI Period', p.rsi_period), pill('RSI Min', p.rsi_min), pill('Overbought', p.overbought)],
    [pill('Vol Mult', p.vol_mult), pill('ATR Period', p.atr_period)],
  ];
  const pills = groups
    .map(g => g.filter(Boolean).join(''))
    .filter(Boolean)
    .map(g => `<span class="param-group">${{g}}</span>`)
    .join('') + `<span class="param-tf">Timeframe ${{tf}}</span>`;

  // Signal criteria — 배지 라벨 체계를 하나로 통일한다: 지표 약어(EMA/PROX/MACD/RSI/VOL/ADX)를
  // 매수·매도 공통 어휘로 쓰고, 매도(반대 방향) 조건만 ↓ 접미사로 구분한다(데스크로스 = EMA↓).
  const emaF = p.ema_fast || 9, emaS = p.ema_slow || 21;
  const rsiMin = p.rsi_min || 40, ob = p.overbought || 70;
  const vm = p.vol_mult || 1.5;
  const adxThr = p.adx_threshold || 25;
  const mw = p.macd_window || 3;

  const prox = p.ema_proximity_pct || 1.5;
  const buyCriteria = [
    ['EMA', `EMA${{emaF}} &gt; EMA${{emaS}}`, '상승 정렬 (골든크로스 포함)'],
    ['PROX', `가격 ≤ EMA${{emaF}} + ${{prox}}%`, '풀백 진입 — 눌림목만 허용'],
    ['MACD', `MACD histogram`, `음→양 전환 (${{mw}}봉 이내)`],
    ['RSI', `RSI ${{rsiMin}} ~ ${{ob}}`, '모멘텀 확인, 과매수 미도달'],
    ['VOL', `거래량 ≥ 평균 × ${{vm}}배`, '유동성 필터'],
    ['ADX', `ADX ≥ ${{adxThr}}`, '추세 강도 확인 (횡보 차단)'],
  ];
  const sellCriteria = [
    ['EMA↓', `EMA${{emaF}} &lt; EMA${{emaS}}`, '데스크로스 (추세 역전)'],
    ['MACD↓', 'MACD histogram', '양 → 음 전환, EMA 위에서'],
  ];

  // 배지(고정폭 58px, 글자 수 무관) | 조건식(1행)+설명(2행) — 조건식과 설명을 같은
  // 줄에 나란히 두면 길이가 제각각일 때 줄바꿈 위치가 들쭉날쭉해지므로, 항상
  // "조건식 줄 / 설명 줄"로 세로 분리해 고정한다.
  const mkRows = (arr, cls) => arr.map(([lbl, cond, desc]) =>
    `<div class="criteria-row">
      <span class="cp ${{cls}}">${{lbl}}</span>
      <div>
        <div class="cond-line"><code>${{cond}}</code></div>
        <div class="desc">${{desc}}</div>
      </div>
    </div>`
  ).join('');

  el.innerHTML = `
    <div style="margin-bottom:.85rem;display:flex;flex-wrap:wrap;align-items:center">${{pills}}</div>
    <div class="criteria-grid">
      <div class="criteria-col">
        <h3 class="buy">▲ 매수 조건 (AND 6개)</h3>
        ${{mkRows(buyCriteria, 'cp-buy')}}
      </div>
      <div class="criteria-col">
        <h3 class="sell">▼ 매도 조건 (OR 2개)</h3>
        ${{mkRows(sellCriteria, 'cp-sell')}}
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
  const modeColor = mode==='LIVE'?'var(--signal-buy)':mode==='PAPER'?'var(--accent)':'var(--text-muted)';
  const modeIcon  = mode==='LIVE'?ICON_BOLT:mode==='PAPER'?ICON_CLIPBOARD:'○';
  const stateColor = paused?'var(--signal-warn)':'var(--signal-buy)';
  const stateIcon  = paused?'⏸':'▶';
  const circuitColor = circuit!=='CLOSED'?'var(--signal-sell)':'var(--signal-buy)';
  const circuitIcon  = circuit!=='CLOSED'?'⚠':'✓';
  el.innerHTML = `
    <h2>엔진 상태</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:.6rem 0">
      <div class="status-box" style="border-color:${{modeColor}}">
        <div style="color:${{modeColor}};font-weight:var(--fw-strong);font-size:var(--fs-5);display:flex;align-items:center;justify-content:center;gap:.35rem">${{modeIcon}}<span>${{mode}}</span></div>
        <div style="color:var(--text-dim);font-size:var(--fs-1);margin-top:.3rem">모드</div>
      </div>
      <div class="status-box" style="border-color:${{stateColor}}">
        <div style="color:${{stateColor}};font-weight:var(--fw-strong);font-size:var(--fs-5);display:flex;align-items:center;justify-content:center;gap:.35rem"><span>${{stateIcon}}</span><span>${{paused?'PAUSED':'RUNNING'}}</span></div>
        <div style="color:var(--text-dim);font-size:var(--fs-1);margin-top:.3rem">상태</div>
      </div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;background:var(--bg-inset);border-radius:.4rem;padding:.45rem .7rem;margin-bottom:.4rem">
      <span style="color:var(--text-muted);font-size:var(--fs-2)">서킷 브레이커</span>
      <span style="color:${{circuitColor}};font-weight:var(--fw-medium);font-size:var(--fs-3)">${{circuitIcon}} ${{circuit}}</span>
    </div>
    <div style="color:var(--text-dim);font-size:var(--fs-2);text-align:right">${{hours}} UTC · ${{days}}</div>
    ${{renderModeSwitchBtn(mode)}}`;
}}
function renderModeSwitchBtn(mode) {{
  const unlockDate = new Date('2026-04-30T00:00:00+09:00');
  const now = new Date();
  const unlocked = now >= unlockDate;
  const diffMs = unlockDate - now;
  const diffDays = Math.ceil(diffMs / 86400000);

  if (mode === 'LIVE') {{
    return `<div class="mode-switch">
      <button class="btn-to-paper" onclick="switchMode('paper',this)" style="width:100%">${{ICON_CLIPBOARD}} PAPER 모드로 전환</button>
    </div>`;
  }}
  if (!unlocked) {{
    return `<div class="mode-switch">
      <button class="btn-locked" disabled style="width:100%">${{ICON_LOCK}} LIVE 전환 — D-${{diffDays}} (4/30 활성화)</button>
    </div>`;
  }}
  return `<div class="mode-switch">
    <button class="btn-live" onclick="switchMode('live',this)" style="width:100%">${{ICON_BOLT}} LIVE 모드로 전환</button>
  </div>`;
}}
async function switchMode(targetMode, btn) {{
  const label = targetMode === 'live' ? 'LIVE' : 'PAPER';
  const warn  = targetMode === 'live'
    ? '⚠️ LIVE 모드로 전환하면 실제 자산으로 거래됩니다.\\n정말 전환하시겠습니까?'
    : 'PAPER 모드로 전환합니다. 실거래가 중단됩니다.\\n계속하시겠습니까?';
  if (!confirm(warn)) return;
  if (btn) {{ if (btn.disabled) return; btn.disabled = true; btn.dataset.origText = btn.textContent; btn.textContent = '처리중…'; }}
  try {{
    const res = await fetch('/api/engine/set-mode', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{mode: targetMode}}),
    }});
    const data = await res.json();
    if (data.success) {{
      showToast(`${{label}} 모드 전환 중... 10초 후 재연결됩니다.`);
      setTimeout(() => location.reload(), 12000);
    }} else {{
      showToast('전환 실패: ' + data.message, true);
      if (btn) {{ btn.disabled = false; btn.textContent = btn.dataset.origText || btn.textContent; }}
    }}
  }} catch(e) {{
    showToast('오류: ' + e.message, true);
    if (btn) {{ btn.disabled = false; btn.textContent = btn.dataset.origText || btn.textContent; }}
  }}
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
    return `<span class="${{c}}" style="font-size:var(--fs-5);font-weight:var(--fw-strong)">${{s}}${{fmtKRW(v)}}</span>`;
  }};
  el.innerHTML = `
    <h2>포트폴리오</h2>
    <div class="pnl-grid">
      <div class="stat-box">
        <div class="val" style="font-size:var(--fs-5);color:var(--text-strong)">${{fmtKRW(cash)}}</div>
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
      <button class="btn-pause" onclick="engineAction('pause',this)" style="padding:.45rem;font-size:var(--fs-3);border-radius:.35rem;width:100%">⏸ 일시정지</button>
      <button class="btn-resume" onclick="engineAction('resume',this)" style="padding:.45rem;font-size:var(--fs-3);border-radius:.35rem;width:100%">▶ 재개</button>
    </div>`;
}}
function renderStats(stats) {{
  const el = document.getElementById('stats-card');
  if (!el) return;
  const total = stats.total_trades || 0;
  if (!total) {{ el.innerHTML = '<h2>성과 분석</h2><p class="empty">거래 없음</p>'; return; }}
  const wr = stats.win_rate_pct || 0;
  const pf = stats.profit_factor;  // null = 손실 거래 0건(무한대), state.py에서 그렇게 인코딩
  const avgWin = stats.avg_win || 0;
  const avgLoss = stats.avg_loss || 0;
  const totalPnl = stats.total_pnl || 0;
  const wrColor = wr>=50?'var(--signal-buy)':'var(--signal-sell)';
  const pfColor = (pf===null||pf>=1)?'var(--signal-buy)':'var(--signal-sell)';
  const pfDisplay = pf===null?'∞':pf.toFixed(2);
  const pnlCls = totalPnl>=0?'win':'loss';
  const sign = totalPnl>=0?'+':'';
  const fmtKRW = v => Math.round(Math.abs(v)).toLocaleString('ko-KR');
  const wins = Math.round(total*wr/100);
  el.innerHTML = `
    <h2>성과 분석 (${{total}}건)</h2>
    <div class="stats-flex">
      <div style="text-align:center">
        <div style="font-size:2.4rem;font-weight:var(--fw-strong);color:${{wrColor}};line-height:1.1">${{wr.toFixed(1)}}%</div>
        <div style="font-size:var(--fs-1);color:var(--text-dim);margin:.25rem 0 .3rem">승률</div>
        <div class="wr-bar"><div class="wr-fill" style="width:${{wr}}%;background:${{wrColor}}"></div></div>
        <div style="font-size:var(--fs-1);color:var(--text-dim);margin-top:.45rem">${{wins}}승 · ${{total-wins}}패 · 총 ${{total}}건</div>
      </div>
      <div class="stat-box-grid">
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
      // BUY/SELL 신호 감지 시 포지션·거래 이력 즉시 갱신
      if (tick.action === 'BUY' || tick.action === 'SELL') {{
        refreshPositionsTrades();
      }}
    }} catch(_) {{}}
  }};
  es.onerror = function() {{
    // reconnect automatically (browser handles it), suppress console noise
  }};
}}
function _renderTicksFromMap(updatedSymbol, prevTick) {{
  const items = Object.values(_ticksMap).sort((a, b) => {{
    const ra = a._rank != null ? a._rank : 9999;
    const rb = b._rank != null ? b._rank : 9999;
    return ra - rb;
  }});
  renderTicks(items, updatedSymbol, prevTick);
}}
// 신호평가 목록 행 클릭 시 업비트 해당 코인 거래소 페이지를 새 창(탭)으로 연다.
// 심볼 형식은 "BASE/QUOTE"(예: BTC/KRW) → 업비트 code 파라미터 형식인
// "CRIX.UPBIT.{{QUOTE}}-{{BASE}}"로 변환한다. 차트 봉 시간단위(1시간 등)를 URL
// 파라미터로 강제 지정하는 공식 방법은 업비트 쪽에서 확인되지 않아 별도로
// 지정하지 않는다 — 열리는 차트의 시간단위는 업비트 화면 자체의 기본값/직전
// 설정을 따른다.
function openUpbit(symbol) {{
  const [base, quote] = symbol.split('/');
  if (!base || !quote) return;
  const url = `https://upbit.com/exchange?code=CRIX.UPBIT.${{quote}}-${{base}}`;
  window.open(url, '_blank', 'noopener,noreferrer');
}}
function renderTicks(items, flashSymbol, prevTick) {{
  const el = document.getElementById('ticks-body');
  if (!el) return;
  if (!items || items.length === 0) {{ el.innerHTML = '<p class="empty">아직 평가 없음 (첫 tick 대기 중)</p>'; return; }}
  const actionColor = a => a==='BUY'?'var(--signal-buy)':a==='SELL'?'var(--signal-sell)':'var(--text-muted)';
  const actionBg = a => a==='BUY'?'var(--signal-buy-bg)':a==='SELL'?'var(--signal-sell-bg)':'var(--bg-inset)';
  const fmtPrice = (sym, v) => {{
    if (v == null) return '-';
    if (sym.includes('BTC') || sym.includes('ETH') || sym.includes('SOL') || sym.includes('TAO'))
      return '₩' + Math.round(v).toLocaleString('ko-KR');
    // BONK 등 1원 미만 초저가 코인은 2자리 반올림 시 0으로 뭉개지므로 소수 자리를 늘려 표시
    if (v < 1) return '₩' + v.toFixed(6);
    return '₩' + v.toLocaleString('ko-KR', {{maximumFractionDigits:2}});
  }};
  const fmtKRW = v => v != null ? '₩' + Math.round(v).toLocaleString('ko-KR') : '-';

  const buyPill  = (lbl, ok, title) => `<span class="cp ${{ok?'cp-buy':'cp-dim'}}" title="${{title}}">${{lbl}}</span>`;
  const sellPill = (lbl, on_, title) => `<span class="cp ${{on_?'cp-sell':'cp-dim'}}" title="${{title}}">${{lbl}}</span>`;

  const regimeBadge = r => {{
    const map = {{
      uptrend:   ['var(--signal-buy)',  'var(--signal-buy-bg)',  '상승'],
      downtrend: ['var(--signal-sell)', 'var(--signal-sell-bg)', '하락'],
      ranging:   ['var(--signal-warn)', 'var(--signal-warn-bg)', '횡보'],
    }};
    const [rc, rbg, rl] = map[r] || ['var(--text-muted)', 'var(--bg-inset)', r||'-'];
    return `<span class="badge" style="background:${{rbg}};color:${{rc}};font-size:var(--fs-1)">${{rl}}</span>`;
  }};
  const thead = `<table class="ticks-table"><thead><tr>
    <th style="width:2rem;text-align:center">#</th>
    <th>종목</th><th>국면</th><th>신호</th><th>현재가</th>
    <th title="거래대금 (KRW)">거래대금</th>
    <th title="RSI">RSI</th>
    <th title="MACD histogram">MACD</th>
    <th title="매수: E(EMA) M(MACD) R(RSI) V(VOL) A(ADX) P(근접) │ 매도: D(데스크로스) M(MACD↓)">조건</th>
    <th>시각</th>
  </tr></thead><tbody>`;

  const mkRows = (group, offset) => group.map((t, idx) => {{
    const rank = offset + idx + 1;
    const m = t.metadata || {{}};
    const c = m.cond || {{}};
    const rsi = m.rsi != null ? m.rsi.toFixed(1) : '-';
    const rsiColor = c.overbought ? 'var(--signal-sell)' : c.rsi_ok ? 'var(--signal-buy)' : 'var(--text-muted)';
    const macdVal = m.macd_hist != null ? (m.macd_hist>=0?'+':'')+m.macd_hist.toFixed(4) : '-';
    const macdColor = m.macd_hist > 0 ? 'var(--signal-buy)' : m.macd_hist < 0 ? 'var(--signal-sell)' : 'var(--text-muted)';
    const price = fmtPrice(t.symbol, m.price);
    const volKrw = fmtKRW(m.vol_krw);
    const ts = t.timestamp ? t.timestamp.substring(11,16) : '';
    const ac = actionColor(t.action);
    const actionChanged = flashSymbol === t.symbol && prevTick && prevTick.action !== t.action;
    const flash = flashSymbol === t.symbol ? ' tick-flash' : '';
    const actionDot = actionChanged ? `<span style="font-size:var(--fs-1);color:var(--signal-warn);margin-left:.3rem">▲</span>` : '';
    const hasCond = Object.keys(c).length > 0;
    const condCell = !hasCond ? '<td>-</td>' : `<td style="white-space:nowrap">
      ${{buyPill('E',c.above_ema,'EMA 상승 정렬')}}${{buyPill('M',c.macd_just_pos,'MACD 양전환')}}${{buyPill('R',c.rsi_ok,'RSI 범위 내')}}${{buyPill('V',c.vol_ok,'거래량 충분')}}${{buyPill('A',c.adx_ok,'ADX ≥25')}}${{buyPill('P',c.ema_proximity_ok,'EMA 근접')}}
      <span style="margin:0 3px;color:var(--border);font-size:var(--fs-1)">│</span>
      ${{sellPill('D',c.death_cross,'데스크로스')}}${{sellPill('M',c.macd_turned_neg,'MACD 음전환')}}
    </td>`;
    return `<tr class="${{flash}}" id="tick-row-${{t.symbol.replace('/','_')}}"
        onclick="openUpbit('${{t.symbol}}')" style="cursor:pointer"
        title="클릭 시 업비트 ${{t.symbol}} 거래소 페이지가 새 창으로 열립니다">
      <td style="text-align:center;color:var(--text-dim);font-size:var(--fs-1);font-weight:var(--fw-medium)">${{rank}}</td>
      <td><code>${{t.symbol}}</code></td>
      <td>${{regimeBadge(m.regime)}}</td>
      <td><span class="badge" style="background:${{actionBg(t.action)}};color:${{ac}}">${{t.action}}</span>${{actionDot}}</td>
      <td style="font-weight:var(--fw-medium)">${{price}}</td>
      <td style="color:var(--text-muted);font-size:var(--fs-3)">${{volKrw}}</td>
      <td style="color:${{rsiColor}};font-size:var(--fs-3)">${{rsi}}</td>
      <td style="color:${{macdColor}};font-size:var(--fs-3)">${{macdVal}}</td>
      ${{condCell}}
      <td style="color:var(--text-dim);font-size:var(--fs-2)">${{ts}}</td>
    </tr>`;
  }}).join('');

  // 1,000원 미만 저가 코인은 엔진의 최소 단가 필터(_MIN_ENTRY_PRICE_KRW)로 실제 진입이
  // 되지 않으므로, 신호평가 목록에도 노출하지 않고 그 다음 순위 종목으로 채운다.
  // (최초 100원 기준이었으나, 가격 구간별 거래 재분석 결과 100~1,000원 구간도
  // profit_factor 0.86으로 손실 우위라 1,000원으로 상향 — engine.py 참고)
  // price 메타데이터가 없는 항목(구버전 tick 등)은 안전하게 그대로 표기한다.
  const filtered = items.filter(t => {{
    const p = (t.metadata || {{}}).price;
    return p == null || p >= 1000;
  }});
  // 1-10위만 표기 (11-20위 탭은 상위 심볼 수 축소 이후 항상 비어 있어 제거됨)
  const top10 = filtered.slice(0, 10);
  const panelHtml = top10.length
    ? thead + mkRows(top10, 0) + '</tbody></table>'
    : '<p class="empty">데이터 없음</p>';

  el.innerHTML = `<div id="tick-panel-0" class="tick-panel">${{panelHtml}}</div>`;
}}
function renderBalance(items) {{
  const el = document.getElementById('balance-body');
  if (!el) return;
  if (!items || items.length === 0) {{ el.innerHTML = '<p class="empty">잔고 없음</p>'; return; }}
  const fmtKRW = v => '₩' + Math.round(v).toLocaleString('ko-KR');
  const fmtQty = v => parseFloat(v.toFixed(8)).toString();
  const fmtPrice = v => v >= 1 ? fmtKRW(v) : '₩' + v.toFixed(6);
  // 라벨/값 스타일을 카드 전체에서 통일한다 (요구사항: 라벨 1단계, 값 최대 2단계 + tabular-nums 우측정렬)
  const labelStyle = 'color:var(--text-muted);font-size:var(--fs-2);white-space:nowrap;flex-shrink:0';
  const rowStyle = 'display:flex;justify-content:space-between;margin:.25rem 0';
  const rowStyleTop = 'display:flex;justify-content:space-between;align-items:flex-start;margin:.25rem 0';
  const valueBase = 'font-variant-numeric:tabular-nums;white-space:nowrap';
  // 수익/본전/손해/정보없음 카드 스타일 (Python _balance_card_style()과 임계값·색상 동일하게 유지)
  // — 배경·테두리·아이콘색을 전부 var()로 이관해 라이트/다크 전환 시 자동으로 맞춰진다
  // (이전엔 배경이 다크 전제 하드코딩(#0f172a)이라 라이트 테마에서 카드 하나만 검게 떠 보였음).
  const cardStyle = (avgBuy, price) => {{
    if (avgBuy <= 0) return {{border: 'var(--border)', bg: 'var(--bg-inset)', icon: '', iconColor: 'var(--text-muted)'}};
    const pnlPct = (price / avgBuy - 1) * 100;
    if (pnlPct > 0.1) return {{border: 'var(--signal-buy)', bg: 'var(--signal-buy-bg)', icon: '▲', iconColor: 'var(--signal-buy)'}};
    if (pnlPct < -0.1) return {{border: 'var(--signal-sell)', bg: 'var(--signal-sell-bg)', icon: '▼', iconColor: 'var(--signal-sell)'}};
    return {{border: 'var(--signal-warn)', bg: 'var(--signal-warn-bg)', icon: '－', iconColor: 'var(--signal-warn)'}};
  }};
  const cards = items.map(b => {{
    const avgBuy = b.avg_buy_price || 0;
    const style = cardStyle(avgBuy, b.price || 0);
    const iconHtml = style.icon
      ? `<span style="color:${{style.iconColor}};font-size:var(--fs-2);margin-right:.35rem">${{style.icon}}</span>` : '';
    const usedRow = b.used > 0
      ? `<div style="${{rowStyle}}">
           <span style="${{labelStyle}}">주문중</span>
           <span style="${{valueBase}};color:var(--text-muted);font-size:var(--fs-3)">${{fmtQty(b.used)}}</span>
         </div>` : '';
    // 평가금액 + 평가손익을 한 줄로 병합: 큰 값(평가금액) 아래 보조텍스트(손익)를 붙인다
    let pnlSubHtml = '';
    if (avgBuy > 0) {{
      const pnlKrw = (b.eval_amount || 0) - (b.buy_amount || 0);
      const pnlPct = ((b.price || 0) / avgBuy - 1) * 100;
      const pnlSign = pnlKrw >= 0 ? '+' : '-';
      pnlSubHtml = `<span style="${{valueBase}};color:${{style.border}};font-size:var(--fs-1);font-weight:var(--fw-medium)">${{pnlSign}}${{fmtKRW(Math.abs(pnlKrw))}} (${{pnlSign}}${{Math.abs(pnlPct).toFixed(2)}}%)</span>`;
    }}
    return `<div style="background:${{style.bg}};border:1px solid ${{style.border}};border-left:3px solid ${{style.border}};border-radius:.6rem;padding:.9rem">
      <div style="font-size:var(--fs-5);font-weight:var(--fw-strong);color:var(--text-strong);margin-bottom:.65rem;display:flex;justify-content:space-between;align-items:baseline">
        <span>${{iconHtml}}${{b.currency}}</span>
        <span style="${{valueBase}};font-size:var(--fs-1);font-weight:var(--fw-regular);color:var(--text-muted)">${{fmtQty(b.free)}}</span>
      </div>
      ${{usedRow}}
      <div style="${{rowStyle}}">
        <span style="${{labelStyle}}">현재가</span>
        <span style="${{valueBase}};color:var(--text-muted);font-size:var(--fs-3)">${{fmtPrice(b.price || 0)}}</span>
      </div>
      <div style="${{rowStyle}}">
        <span style="${{labelStyle}}">매수평균가</span>
        <span style="${{valueBase}};color:var(--accent);font-size:var(--fs-3)">${{avgBuy > 0 ? fmtPrice(avgBuy) : '-'}}</span>
      </div>
      <div style="border-top:1px solid var(--border);margin:.55rem 0 .4rem"></div>
      <div style="${{rowStyle}}">
        <span style="${{labelStyle}}">매수금액</span>
        <span style="${{valueBase}};color:var(--accent);font-size:var(--fs-3)">${{b.buy_amount > 0 ? fmtKRW(b.buy_amount) : '-'}}</span>
      </div>
      <div style="${{rowStyleTop}}">
        <span style="${{labelStyle}}">평가금액</span>
        <span style="display:flex;flex-direction:column;align-items:flex-end;gap:.1rem">
          <span style="${{valueBase}};color:var(--signal-buy);font-weight:var(--fw-strong);font-size:var(--fs-5)">${{fmtKRW(b.eval_amount || 0)}}</span>
          ${{pnlSubHtml}}
        </span>
      </div>
    </div>`;
  }});
  const total = items.reduce((s, b) => s + (b.eval_amount || 0), 0);
  el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:.75rem">${{cards.join('')}}</div>
    <div class="stat-row" style="margin-top:.75rem;border-top:1px solid var(--border);padding-top:.6rem">
      <span class="stat-label">총 평가금액</span>
      <span class="stat-value" style="color:var(--signal-buy);font-weight:var(--fw-strong)">${{fmtKRW(total)}}</span>
    </div>`;
}}
function renderPositions(positions) {{
  const el = document.getElementById('tab-positions');
  if (!el) return;
  if (!positions || positions.length === 0) {{
    el.innerHTML = '<p class="empty">오픈 포지션 없음</p>';
    return;
  }}
  const fmtKRW = v => v != null ? '₩' + Math.round(v).toLocaleString('ko-KR') : '—';
  const rows = positions.map(p => {{
    const sl = p.stop_loss != null ? fmtKRW(p.stop_loss) : '—';
    const tp = p.take_profit != null ? fmtKRW(p.take_profit) : '—';
    const hi = p.highest_price != null ? fmtKRW(p.highest_price) : '—';
    const dt = p.entry_time ? p.entry_time.substring(0,16).replace('T',' ') : '-';
    const cur = p.current_price != null ? fmtKRW(p.current_price) : '—';
    const pnl = p.unrealized_pnl ?? 0;
    const pnlPct = p.pnl_pct ?? 0;
    const pnlColor = pnl >= 0 ? 'var(--signal-buy)' : 'var(--signal-sell)';
    const pnlStr = `<span style="color:${{pnlColor}};font-weight:var(--fw-medium)">₩${{Math.round(pnl).toLocaleString('ko-KR',{{signDisplay:'always'}})}} (${{pnlPct >= 0 ? '+' : ''}}${{pnlPct.toFixed(2)}}%)</span>`;
    return `<tr>
      <td><code>${{p.symbol}}</code></td>
      <td>${{(p.side||'').toUpperCase()}}</td>
      <td style="color:var(--text-muted)">${{p.amount != null ? p.amount.toFixed(4) : '-'}}</td>
      <td style="font-weight:var(--fw-medium)">${{fmtKRW(p.entry_price)}}</td>
      <td style="font-weight:var(--fw-medium)">${{cur}}</td>
      <td style="color:var(--accent)">${{hi}}</td>
      <td>${{pnlStr}}</td>
      <td style="color:var(--signal-sell)">${{sl}}</td>
      <td style="color:var(--signal-buy)">${{tp}}</td>
      <td style="color:var(--text-dim);font-size:var(--fs-3)">${{dt}}</td>
    </tr>`;
  }});
  el.innerHTML = `<table><thead><tr>
    <th>종목</th><th>방향</th><th>수량</th><th>진입가</th><th>현재가</th><th>신고가</th><th>평가손익</th><th>손절가</th><th>목표가</th><th>진입 시각</th>
  </tr></thead><tbody>${{rows.join('')}}</tbody></table>`;
}}
async function refreshPositionsTrades() {{
  try {{
    const [pos, trades, pnl] = await Promise.all([
      fetch('/api/positions').then(r=>r.json()),
      fetch('/api/trades?limit=200').then(r=>r.json()),
      fetch('/api/pnl').then(r=>r.json()),
    ]);
    _positionsMap = {{}};
    (pos || []).forEach(p => {{ _positionsMap[p.symbol] = p; }});
    renderPositions(pos);
    _lastTrades = trades || [];
    _populateSymbolFilter(_lastTrades);
    applyTradeFilters();
    renderSymbolPnl(_lastTrades);
    renderPnl(pnl);
    const mainTabs = document.querySelectorAll('.tabs .tab');
    if (mainTabs[0]) mainTabs[0].textContent = `오픈 포지션 (${{(pos||[]).length}})`;
  }} catch(e) {{ /* silent */ }}
}}
// 기간(전체/7일/30일/90일) · 종목 · 승패 필터 — 이미 불러온 _lastTrades 배열 안에서만 필터링(서버 재요청 없음)
function _filterTrades(trades) {{
  const period = (document.getElementById('filter-period') || {{}}).value || 'all';
  const symbol = (document.getElementById('filter-symbol') || {{}}).value || 'all';
  const result = (document.getElementById('filter-result') || {{}}).value || 'all';
  const now = Date.now();
  return (trades || []).filter(t => {{
    if (symbol !== 'all' && t.symbol !== symbol) return false;
    if (period !== 'all') {{
      const days = parseInt(period, 10);
      const ts = t.exit_time || t.entry_time;
      if (ts) {{
        const diffDays = (now - new Date(ts).getTime()) / 86400000;
        if (diffDays > days) return false;
      }}
    }}
    if (result !== 'all') {{
      const isWin = (t.pnl || 0) >= 0;
      if (result === 'win' && !isWin) return false;
      if (result === 'loss' && isWin) return false;
    }}
    return true;
  }});
}}
// 종목 드롭다운 옵션을 실제 거래 데이터에서 동적으로 채운다 (현재 선택값은 유지)
function _populateSymbolFilter(trades) {{
  const sel = document.getElementById('filter-symbol');
  if (!sel) return;
  const symbols = Array.from(new Set((trades || []).map(t => t.symbol))).sort();
  const current = sel.value;
  sel.innerHTML = '<option value="all">전체 종목</option>' +
    symbols.map(s => `<option value="${{s}}">${{s}}</option>`).join('');
  if (symbols.includes(current)) sel.value = current;
}}
// 필터 select 변경 시 호출 — 매수/매도 이력 탭과 탭 카운트를 함께 갱신한다
function applyTradeFilters() {{
  const filtered = _filterTrades(_lastTrades);
  renderBuyHistory(filtered);
  renderSellHistory(filtered);
  const mainTabs = document.querySelectorAll('.tabs .tab');
  if (mainTabs[1]) mainTabs[1].textContent = `매수 이력 (${{filtered.length}})`;
  if (mainTabs[2]) mainTabs[2].textContent = `매도 이력 (${{filtered.length}})`;
}}
function renderBuyHistory(trades) {{
  const el = document.getElementById('tab-buys');
  if (!el) return;
  const fmtKRW = v => '₩' + Math.round(v).toLocaleString('ko-KR');

  // Open positions (bought, not yet sold)
  const openRows = Object.values(_positionsMap)
    .filter(p => p.side === 'buy')
    .sort((a, b) => (b.entry_time || '').localeCompare(a.entry_time || ''))
    .map(p => {{
      const dt = p.entry_time ? p.entry_time.substring(0,16).replace('T',' ') : '-';
      const cost = p.entry_price * p.amount;
      return `<tr>
        <td style="color:var(--text-dim);font-size:var(--fs-3)">${{dt}}</td>
        <td><code>${{p.symbol}}</code></td>
        <td style="color:var(--text-muted)">${{p.amount != null ? p.amount.toFixed(6) : '-'}}</td>
        <td style="font-weight:var(--fw-medium);color:var(--signal-buy)">${{fmtKRW(p.entry_price)}}</td>
        <td style="color:var(--text-muted)">${{fmtKRW(cost)}}</td>
        <td><span style="background:var(--signal-buy-bg);color:var(--signal-buy);padding:.1rem .4rem;border-radius:9999px;font-size:var(--fs-1);font-weight:var(--fw-strong)">보유중</span></td>
      </tr>`;
    }});

  // Closed trades (entry info)
  const closedRows = (trades || []).map(t => {{
    const dt = t.entry_time ? t.entry_time.substring(0,16).replace('T',' ') : '-';
    const cost = t.entry_price * t.amount;
    return `<tr>
      <td style="color:var(--text-dim);font-size:var(--fs-3)">${{dt}}</td>
      <td><code>${{t.symbol}}</code></td>
      <td style="color:var(--text-muted)">${{t.amount != null && t.amount.toFixed ? t.amount.toFixed(6) : t.amount}}</td>
      <td style="font-weight:var(--fw-medium);color:var(--signal-buy)">${{fmtKRW(t.entry_price)}}</td>
      <td style="color:var(--text-muted)">${{fmtKRW(cost)}}</td>
      <td><span style="color:var(--text-dim);font-size:var(--fs-1)">청산</span></td>
    </tr>`;
  }});

  const allRows = [...openRows, ...closedRows];
  if (allRows.length === 0) {{ el.innerHTML = '<p class="empty">매수 이력 없음</p>'; return; }}
  el.innerHTML = `<table><thead><tr>
    <th>매수 시각</th><th>종목</th><th>수량</th><th>매수가</th><th>매수 금액</th><th>상태</th>
  </tr></thead><tbody>${{allRows.join('')}}</tbody></table>`;
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
    const cls = pnl >= 0 ? 'var(--signal-buy)' : 'var(--signal-sell)';
    const sign = pnl >= 0 ? '+' : '';
    return `<tr>
      <td style="color:var(--text-dim);font-size:var(--fs-3)">${{dt}}</td>
      <td><code>${{t.symbol}}</code></td>
      <td style="color:var(--text-muted)">${{t.amount.toFixed ? t.amount.toFixed(6) : t.amount}}</td>
      <td style="font-weight:var(--fw-medium);color:var(--signal-sell)">${{fmtKRW(t.exit_price)}}</td>
      <td style="color:${{cls}};font-weight:var(--fw-medium)">${{sign}}${{fmtKRW(pnl)}} (${{sign}}${{pct.toFixed(2)}}%)</td>
      <td style="color:var(--text-dim);font-size:var(--fs-1)">${{t.reason || '-'}}</td>
    </tr>`;
  }});
  el.innerHTML = `<table><thead><tr>
    <th>매도 시각</th><th>종목</th><th>수량</th><th>매도가</th><th>손익</th><th>사유</th>
  </tr></thead><tbody>${{rows.join('')}}</tbody></table>`;
}}
function renderEquity(pts) {{
  const wrap = document.getElementById('chart-wrap');
  if (!pts || pts.length === 0) {{ wrap.innerHTML = '<p class="empty">거래 없음 — 첫 청산 후 표시됩니다</p>'; return; }}

  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const prefix = `${{yyyy}}-${{mm}}`;
  const todayStr = today.toISOString().substring(0, 10);

  // 이번 달 날짜별 일일 손익 (API가 이미 일별 합산해서 줌)
  const byDate = {{}};
  pts.forEach(p => {{
    const date = p.time ? p.time.substring(0, 10) : '';
    if (date.startsWith(prefix)) byDate[date] = p.pnl || 0;
  }});

  // 1일~말일 전체 날짜 배열 (거래 없는 날은 0)
  const lastDay = new Date(yyyy, today.getMonth() + 1, 0).getDate();
  const allDates = [];
  for (let d = 1; d <= lastDay; d++) {{
    allDates.push(`${{prefix}}-${{String(d).padStart(2, '0')}}`);
  }}

  const dailyByDate = {{}};
  allDates.forEach(date => {{ dailyByDate[date] = byDate[date] || 0; }});

  const maxVal = Math.max(...allDates.map(d => dailyByDate[d]), 0);
  const minVal = Math.min(...allDates.map(d => dailyByDate[d]), 0);
  const maxAbs = Math.max(Math.abs(maxVal), Math.abs(minVal), 1);
  const range = Math.max(maxVal - minVal, 1);

  const W = 1000, H = 300, padL = 82, padR = 20, padT = 56, padB = 44;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;
  const n = allDates.length;
  // 막대:간격 = 약 62:38 (업계 관례 60~70% 막대) — 슬롯(중심 간 거리)의 62%를 막대
  // 폭으로 쓰되, 데이터가 적을 때(2~3개) 과도하게 두꺼워지지 않도록 상한(64px)을,
  // 30개까지 촘촘해져도 너무 가늘어지지 않도록 하한(6px)을 둔다.
  const slot = chartW / n;
  const barW = Math.min(Math.max(slot * 0.62, 6), 64);
  const zeroY = padT + chartH * maxVal / range;

  // 막대 값 라벨 겹침 방지: 막대 중심 간 실제 간격(slot)이 라벨 예상 폭보다 좁으면
  // (예: 실거래 30일치처럼 막대가 촘촘한 경우) 개별 막대 라벨을 전부 생략하고,
  // 우측 상단의 월 합계 숫자만 크게 보여준다 — 더미 2~3개 막대로만 검증해서
  // 놓쳤던 문제라, 실제 규모(30일)로 반드시 재확인한다.
  const equityLabelTexts = allDates.map(d => {{
    const v = dailyByDate[d];
    const sign = v >= 0 ? '+' : '';
    return `${{sign}}₩${{Math.round(v).toLocaleString('ko-KR')}}`;
  }});
  const maxEquityLabelLen = Math.max(...equityLabelTexts.map(t => t.length), 0);
  const showBarLabels = slot >= maxEquityLabelLen * 7 + 6;

  let svgParts = [];

  // 차트 영역 배경 — var(--chart-bg)는 라이트 테마에서 밝은 회색조로 자동 전환된다
  // (SVG 프레젠테이션 속성은 CSS 커스텀 프로퍼티를 그대로 참조할 수 있어 테마 토글 시
  // JS 재렌더 없이도 즉시 반영된다).
  svgParts.push(`<rect x="${{padL}}" y="${{padT}}" width="${{chartW}}" height="${{chartH}}" fill="var(--chart-bg)"/>`);

  // 오늘 날짜 하이라이트
  const todayIdx = allDates.indexOf(todayStr);
  if (todayIdx >= 0) {{
    const tx = padL + (todayIdx + 0.5) * chartW / n - barW / 2 - 4;
    svgParts.push(`<rect x="${{tx.toFixed(1)}}" y="${{padT}}" width="${{(barW + 8).toFixed(1)}}" height="${{chartH}}" fill="var(--chart-highlight)"/>`);
  }}

  // 배경 눈금선 (5단계)
  [-1, -0.5, 0, 0.5, 1].forEach(factor => {{
    const lineV = factor * maxAbs;
    const lineY = padT + chartH * (maxVal - lineV) / range;
    if (lineY >= padT && lineY <= padT + chartH) {{
      const isZero = factor === 0;
      svgParts.push(`<line x1="${{padL}}" y1="${{lineY.toFixed(1)}}" x2="${{W-padR}}" y2="${{lineY.toFixed(1)}}" stroke="var(--chart-grid)" stroke-width="1" stroke-dasharray="${{isZero ? '4' : '2'}}"/>`);
      const labelV = Math.round(Math.abs(lineV));
      const sign = lineV > 0 ? '+' : (lineV < 0 ? '-' : '');
      const labelColor = lineV > 0 ? 'var(--chart-buy)' : (lineV < 0 ? 'var(--chart-sell)' : 'var(--text-dim)');
      const labelText = isZero ? '0' : `${{sign}}₩${{labelV.toLocaleString('ko-KR')}}`;
      svgParts.push(`<text x="${{padL-6}}" y="${{(lineY+4).toFixed(1)}}" fill="${{labelColor}}" font-size="13" text-anchor="end" font-family="system-ui,sans-serif">${{labelText}}</text>`);
    }}
  }});

  // 막대 + 날짜 레이블
  allDates.forEach((date, i) => {{
    const v = dailyByDate[date];
    const x = padL + (i + 0.5) * chartW / n - barW / 2;
    const color = v >= 0 ? 'var(--chart-buy)' : 'var(--chart-sell)';
    const barH = v !== 0 ? Math.max(1, Math.abs(v) / range * chartH) : 1;
    const barColor = v !== 0 ? color : 'var(--chart-grid)';
    const opacity = date === todayStr ? '1.0' : '0.82';
    const y = v >= 0 ? zeroY - barH : zeroY;
    svgParts.push(`<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{barW.toFixed(1)}}" height="${{barH.toFixed(1)}}" fill="${{barColor}}" rx="2" opacity="${{opacity}}"/>`);

    if (showBarLabels && barH > 16 && v !== 0) {{
      const sign = v >= 0 ? '+' : '';
      const labelY = v >= 0 ? y - 4 : y + barH + 12;
      svgParts.push(`<text x="${{(x+barW/2).toFixed(1)}}" y="${{labelY.toFixed(1)}}" fill="${{color}}" font-size="12" text-anchor="middle" font-weight="600" font-family="system-ui,sans-serif">${{sign}}₩${{Math.round(v).toLocaleString('ko-KR')}}</text>`);
    }}

    const day = parseInt(date.substring(8));
    if (day === 1 || day % 5 === 0 || date === todayStr) {{
      const labelColor = date === todayStr ? 'var(--text-strong)' : 'var(--text-muted)';
      svgParts.push(`<text x="${{(x+barW/2).toFixed(1)}}" y="${{(H-10).toFixed(1)}}" fill="${{labelColor}}" font-size="13" text-anchor="middle" font-family="system-ui,sans-serif">${{day}}일</text>`);
    }}
  }});

  // 월 제목 + 월 합계 (오늘까지의 일별 손익 합산)
  const totalPnl = allDates.filter(d => d <= todayStr).reduce((s, d) => s + dailyByDate[d], 0);
  const totalSign = totalPnl >= 0 ? '+' : '';
  const totalColor = totalPnl >= 0 ? 'var(--chart-buy)' : 'var(--chart-sell)';
  svgParts.push(`<text x="${{padL}}" y="22" fill="var(--text-muted)" font-size="15" font-family="system-ui,sans-serif">${{yyyy}}년 ${{parseInt(mm)}}월 일별 손익</text>`);
  svgParts.push(`<text x="${{W-padR}}" y="26" fill="${{totalColor}}" font-size="20" text-anchor="end" font-weight="700" font-family="system-ui,sans-serif">${{totalSign}}₩${{Math.round(totalPnl).toLocaleString('ko-KR')}}</text>`);

  wrap.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">${{svgParts.join('')}}</svg>`;
}}
// 심볼별 실현 손익 합계 막대그래프 — 항상 필터 미적용 전체 거래(_lastTrades) 기준으로 그린다
// (거래내역 필터와 독립적으로 유지 — 과설계 방지)
function renderSymbolPnl(trades) {{
  const wrap = document.getElementById('symbol-pnl-wrap');
  if (!wrap) return;
  if (!trades || trades.length === 0) {{
    wrap.innerHTML = '<p class="empty">거래 없음 — 첫 청산 후 표시됩니다</p>';
    return;
  }}
  const totals = {{}};
  trades.forEach(t => {{ totals[t.symbol] = (totals[t.symbol] || 0) + (t.pnl || 0); }});
  const ranked = Object.entries(totals).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 8);
  if (ranked.length === 0) {{ wrap.innerHTML = '<p class="empty">거래 없음</p>'; return; }}

  const values = ranked.map(([, v]) => v);
  const maxVal = Math.max(...values, 0);
  const minVal = Math.min(...values, 0);
  const range = Math.max(maxVal - minVal, 1);
  const maxAbs = Math.max(Math.abs(maxVal), Math.abs(minVal), 1);

  const n = ranked.length;
  const W = 1000, H = 260, padL = 82, padR = 20, padT = 40, padB = 50;
  const chartW = W - padL - padR, chartH = H - padT - padB;
  // 막대:간격 ≈ 62:38(업계 관례), 심볼 수가 적을 때(2~3개) 과도하게 두꺼워지지
  // 않도록 상한을, 8~10개로 촘촘할 때도 너무 가늘어지지 않도록 하한을 둔다.
  const symSlot = chartW / n;
  const barW = Math.min(Math.max(symSlot * 0.62, 20), 90);
  const zeroY = padT + chartH * maxVal / range;

  // 막대 값 라벨 겹침 방지 — 실제 8개 심볼 규모에서 라벨끼리 겹치던 문제를
  // 픽셀 단위로 재확인하고, 슬롯 폭이 라벨 예상 폭보다 좁으면 전부 생략한다.
  const symLabelTexts = ranked.map(([, v]) => {{
    const sign = v >= 0 ? '+' : '';
    return `${{sign}}₩${{Math.round(v).toLocaleString('ko-KR')}}`;
  }});
  const maxSymLabelLen = Math.max(...symLabelTexts.map(t => t.length), 0);
  // 픽셀 추정만으로는 폰트/브라우저 렌더링 차이에 따라 실제로 겹치는 경우가 있어,
  // 심볼이 6개 이상(상위 8개까지 나올 수 있음)일 때는 보수적으로 항상 생략한다
  // (실사용에서 의미 있게 여러 개가 동시에 뜨는 건 보통 소수 심볼이라 3~5개까지는
  // 라벨을 유지해도 안전하다).
  const showSymBarLabels = symSlot >= maxSymLabelLen * 7 + 6 && n <= 5;

  const parts = [];
  parts.push(`<rect x="${{padL}}" y="${{padT}}" width="${{chartW}}" height="${{chartH}}" fill="var(--chart-bg)" rx="4"/>`);

  [-1, -0.5, 0, 0.5, 1].forEach(factor => {{
    const lineV = factor * maxAbs;
    const lineY = padT + chartH * (maxVal - lineV) / range;
    if (lineY < padT || lineY > padT + chartH) return;
    const isZero = factor === 0;
    const stroke = isZero ? 'var(--chart-grid)' : 'var(--chart-grid-soft)';
    const dash = isZero ? '' : '5,3';
    parts.push(`<line x1="${{padL}}" y1="${{lineY.toFixed(1)}}" x2="${{W-padR}}" y2="${{lineY.toFixed(1)}}" stroke="${{stroke}}" stroke-width="${{isZero?1.5:1}}" stroke-dasharray="${{dash}}"/>`);
    const labelV = Math.round(Math.abs(lineV));
    const lcolor = labelV===0 ? 'var(--text-dim)' : (lineV>0?'var(--chart-buy)':'var(--chart-sell)');
    const sign = labelV===0 ? '' : (lineV>0?'+':'-');
    const ltext = labelV===0 ? '0' : `${{sign}}₩${{labelV.toLocaleString('ko-KR')}}`;
    parts.push(`<text x="${{padL-8}}" y="${{(lineY+4.5).toFixed(1)}}" fill="${{lcolor}}" font-size="13" text-anchor="end" font-family="system-ui,sans-serif">${{ltext}}</text>`);
  }});

  ranked.forEach(([sym, v], i) => {{
    const x = padL + (i + 0.5) * chartW / n - barW / 2;
    const color = v >= 0 ? 'var(--chart-buy)' : 'var(--chart-sell)';
    const bh = v !== 0 ? Math.max(2, Math.abs(v) / range * chartH) : 2;
    const y = v >= 0 ? zeroY - bh : zeroY;
    parts.push(`<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{barW.toFixed(1)}}" height="${{bh.toFixed(1)}}" fill="${{color}}" rx="3" opacity="0.88"/>`);
    if (showSymBarLabels && bh > 20) {{
      const sign = v >= 0 ? '+' : '';
      const labelY = v >= 0 ? y - 7 : y + bh + 15;
      parts.push(`<text x="${{(x+barW/2).toFixed(1)}}" y="${{labelY.toFixed(1)}}" fill="${{color}}" font-size="12" text-anchor="middle" font-weight="700" font-family="system-ui,sans-serif">${{sign}}₩${{Math.round(v).toLocaleString('ko-KR')}}</text>`);
    }}
    const label = sym.includes('/') ? sym.split('/')[0] : sym;
    parts.push(`<text x="${{(x+barW/2).toFixed(1)}}" y="${{H-16}}" fill="var(--text-muted)" font-size="13" text-anchor="middle" font-family="system-ui,sans-serif">${{label}}</text>`);
  }});

  parts.push(`<text x="${{padL}}" y="24" fill="var(--text-muted)" font-size="15" font-weight="500" font-family="system-ui,sans-serif">심볼별 실현 손익 (상위 ${{n}}개)</text>`);

  wrap.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">${{parts.join('')}}</svg>`;
}}
function toggleTheme() {{
  const html = document.documentElement;
  const current = html.getAttribute('data-theme') || 'dark';
  const next = current === 'light' ? 'dark' : 'light';
  html.setAttribute('data-theme', next);
  try {{ localStorage.setItem('dashboard-theme', next); }} catch(e) {{}}
  _updateThemeToggleIcon(next);
}}
function _updateThemeToggleIcon(theme) {{
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = theme === 'light' ? ICON_SUN : ICON_MOON;
}}
async function engineAction(action, btn) {{
  if (btn) {{ if (btn.disabled) return; btn.disabled = true; btn.dataset.origText = btn.textContent; btn.textContent = '처리중…'; }}
  try {{
    const res = await fetch('/api/engine/'+action, {{method:'POST'}});
    const d = await res.json();
    showToast(d.message || action + ' 완료');
    setTimeout(refresh, 300);
  }} catch(e) {{ showToast('오류: '+e.message); }}
  finally {{ if (btn) {{ btn.disabled = false; btn.textContent = btn.dataset.origText || btn.textContent; }} }}
}}
function renderPendingParams(p) {{
  const el = document.getElementById('pending-params-card');
  if (!el) return;
  if (!p || !p.has_pending) {{
    el.innerHTML = '<h2>⚙️ 자동튜너 제안</h2><p class="empty">대기 중인 변경 없음</p>';
    return;
  }}
  const meta = p.meta || {{}};
  const allParams = {{...(p.strategy_params||{{}}), ...(p.risk||{{}})}};
  const rows = Object.entries(allParams).map(([k,v]) => {{
    const old = (meta[k]||{{}}).old ?? '?';
    return `<div class="stat-row"><span class="stat-label">${{k}}</span><span class="stat-value">${{old}} → ${{v}}</span></div>`;
  }}).join('');

  if (!p.validation) {{
    el.innerHTML = `<h2>⚙️ 자동튜너 제안 — 검증 대기</h2>${{rows}}
      <p style="color:var(--signal-warn);font-size:var(--fs-4);margin-top:.5rem">⏳ 백테스트 검증 대기 중</p>`;
    return;
  }}
  const v = p.validation;
  const b = v.baseline_metrics || {{}}, c = v.candidate_metrics || {{}};
  const fmt = (m,k,suf) => (typeof m[k]==='number') ? m[k].toFixed(2)+(suf||'') : '-';
  const metrics = `
    <div class="stat-row"><span class="stat-label">Profit Factor</span><span class="stat-value">${{fmt(b,'profit_factor')}} → ${{fmt(c,'profit_factor')}}</span></div>
    <div class="stat-row"><span class="stat-label">승률</span><span class="stat-value">${{fmt(b,'win_rate_pct','%')}} → ${{fmt(c,'win_rate_pct','%')}}</span></div>
    <div class="stat-row"><span class="stat-label">최대낙폭</span><span class="stat-value">${{fmt(b,'max_drawdown_pct','%')}} → ${{fmt(c,'max_drawdown_pct','%')}}</span></div>`;
  const status = v.passed
    ? `<p style="color:var(--signal-buy);font-weight:var(--fw-strong);margin:.5rem 0">${{ICON_CHECK}} 검증 통과</p>`
    : `<p style="color:var(--signal-sell);font-weight:var(--fw-strong);margin:.5rem 0">${{ICON_CROSS}} 검증 실패 — 승인 불가</p>`;
  const buttons = v.passed
    ? `<div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem">
         <button class="btn-resume" onclick="paramAction('approve',this)" style="padding:.5rem;border-radius:.4rem;width:100%">${{ICON_CHECK}} 승인 &amp; 적용</button>
         <button class="btn-pause" onclick="paramAction('reject',this)" style="padding:.5rem;border-radius:.4rem;width:100%">✖ 거부</button>
       </div>`
    : `<div style="margin-top:.75rem">
         <button class="btn-pause" onclick="paramAction('reject',this)" style="padding:.5rem;border-radius:.4rem;width:100%">✖ 제안 삭제</button>
       </div>`;
  el.innerHTML = `<h2>⚙️ 자동튜너 제안 — 승인 대기</h2>${{rows}}${{metrics}}${{status}}${{buttons}}`;
}}
async function paramAction(action, btn) {{
  if (action === 'approve' && !confirm('이 변경을 실거래에 반영할까요? 잠시 후 재시작됩니다.')) return;
  if (btn) {{ if (btn.disabled) return; btn.disabled = true; btn.dataset.origText = btn.textContent; btn.textContent = '처리중…'; }}
  try {{
    const res = await fetch('/api/params/'+action, {{method:'POST'}});
    const d = await res.json();
    showToast(d.message || action + ' 완료');
    setTimeout(refresh, 300);
  }} catch(e) {{ showToast('오류: '+e.message); }}
  finally {{ if (btn) {{ btn.disabled = false; btn.textContent = btn.dataset.origText || btn.textContent; }} }}
}}
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(()=>{{t.style.display='none'}}, 3000);
}}
setInterval(refresh, 30000);
setInterval(refreshPositionsTrades, 5000);
// Initialize on load
(function() {{
  // <head>의 동기 스크립트가 이미 반영한 저장된 테마에 토글 아이콘을 맞춘다
  _updateThemeToggleIcon(document.documentElement.getAttribute('data-theme') || 'dark');
  // Seed _ticksMap from server-rendered ticks data
  {_ticks_seed_js}
  _initTickStream();
  // Load strategy info immediately
  fetch('/api/strategy').then(r=>r.json()).then(renderStrategy).catch(()=>{{}});
  // Populate positions/trades immediately (don't wait for 5s interval)
  refreshPositionsTrades();
}})();
</script>
</body>
</html>"""


def _render_pnl(pnl: dict) -> str:
    """Mirrors renderPnl() in the client-side JS below (same dual server+client
    render pattern used elsewhere) — .pnl-grid 3박스 + 일시정지/재개 버튼까지
    JS와 마크업이 같아야 최초 로드 후 refresh() 시점에 레이아웃이 바뀌는
    깜빡임이 생기지 않는다.
    """
    cash = pnl.get("cash", 0.0)
    realized = pnl.get("realized_pnl", 0.0)
    unrealized = pnl.get("unrealized_pnl", 0.0)

    def fmt_krw(v: float) -> str:
        return f"₩{round(abs(v)):,}"

    def fmt_signed(v: float) -> str:
        cls = "win" if v >= 0 else "loss"
        sign = "+" if v >= 0 else "-"
        return (
            f'<span class="{cls}" style="font-size:var(--fs-5);font-weight:var(--fw-strong)">'
            f"{sign}{fmt_krw(v)}</span>"
        )

    return f"""<div class="card" id="pnl-card">
      <h2>포트폴리오</h2>
      <div class="pnl-grid">
        <div class="stat-box">
          <div class="val" style="font-size:var(--fs-5);color:var(--text-strong)">{fmt_krw(cash)}</div>
          <div class="lbl">현금 잔고</div>
        </div>
        <div class="stat-box">
          <div class="val">{fmt_signed(realized)}</div>
          <div class="lbl">실현 손익</div>
        </div>
        <div class="stat-box">
          <div class="val">{fmt_signed(unrealized)}</div>
          <div class="lbl">미실현 손익</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem">
        <button class="btn-pause" onclick="engineAction('pause',this)" style="padding:.45rem;font-size:var(--fs-3);border-radius:.35rem;width:100%">⏸ 일시정지</button>
        <button class="btn-resume" onclick="engineAction('resume',this)" style="padding:.45rem;font-size:var(--fs-3);border-radius:.35rem;width:100%">▶ 재개</button>
      </div>
    </div>"""


def _render_pending_params(pending: dict) -> str:
    """
    Auto-tuner proposal (src.config.live_params.PENDING_FILE) + its cached
    backtest validation result (VALIDATION_FILE, written by
    scripts/validate_params.py), with approve/reject buttons wired to
    POST /api/params/approve|reject. Mirrors renderPendingParams() in the
    client-side JS below (same dual server+client render pattern already
    used for pnl/stats).
    """
    if not pending.get("has_pending"):
        return '<h2>⚙️ 자동튜너 제안</h2><p class="empty">대기 중인 변경 없음</p>'

    meta = pending.get("meta", {})
    all_params = {**pending.get("strategy_params", {}), **pending.get("risk", {})}
    rows_html = "\n".join(
        f'<div class="stat-row"><span class="stat-label">{param}</span>'
        f'<span class="stat-value">{meta.get(param, {}).get("old", "?")} → {new_val}</span></div>'
        for param, new_val in all_params.items()
    )

    validation = pending.get("validation")
    if validation is None:
        return f"""<h2>⚙️ 자동튜너 제안 — 검증 대기</h2>
        {rows_html}
        <p style="color:var(--signal-warn);font-size:var(--fs-4);margin-top:.5rem">⏳ 백테스트 검증 대기 중</p>"""

    passed = validation.get("passed", False)
    b = validation.get("baseline_metrics", {})
    c = validation.get("candidate_metrics", {})

    def _fmt(m: dict, key: str, suffix: str = "") -> str:
        v = m.get(key)
        return f"{v:.2f}{suffix}" if isinstance(v, (int, float)) else "-"

    metrics_html = f"""
    <div class="stat-row"><span class="stat-label">Profit Factor</span><span class="stat-value">{_fmt(b,'profit_factor')} → {_fmt(c,'profit_factor')}</span></div>
    <div class="stat-row"><span class="stat-label">승률</span><span class="stat-value">{_fmt(b,'win_rate_pct','%')} → {_fmt(c,'win_rate_pct','%')}</span></div>
    <div class="stat-row"><span class="stat-label">최대낙폭</span><span class="stat-value">{_fmt(b,'max_drawdown_pct','%')} → {_fmt(c,'max_drawdown_pct','%')}</span></div>"""

    if passed:
        status_html = f'<p style="color:var(--signal-buy);font-weight:var(--fw-strong);margin:.5rem 0">{_icon("check", 13)} 검증 통과</p>'
        buttons_html = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.75rem">
          <button class="btn-resume" onclick="paramAction('approve',this)" style="padding:.5rem;border-radius:.4rem;width:100%">{_icon("check", 13)} 승인 &amp; 적용</button>
          <button class="btn-pause" onclick="paramAction('reject',this)" style="padding:.5rem;border-radius:.4rem;width:100%">✖ 거부</button>
        </div>"""
    else:
        status_html = f'<p style="color:var(--signal-sell);font-weight:var(--fw-strong);margin:.5rem 0">{_icon("cross", 13)} 검증 실패 — 승인 불가</p>'
        buttons_html = """
        <div style="margin-top:.75rem">
          <button class="btn-pause" onclick="paramAction('reject',this)" style="padding:.5rem;border-radius:.4rem;width:100%">✖ 제안 삭제</button>
        </div>"""

    return f"""<h2>⚙️ 자동튜너 제안 — 승인 대기</h2>
    {rows_html}
    {metrics_html}
    {status_html}
    {buttons_html}"""


def _render_stats(stats: dict) -> str:
    """Mirrors renderStats() in the client-side JS below (same dual server+client
    render pattern used elsewhere) — 마크업 구조(.stats-flex 큰 승률 표시 +
    .stat-box-grid 4박스)가 어긋나면 최초 로드 후 JS refresh() 시점에 레이아웃이
    한 번 바뀌는 깜빡임이 생기므로 반드시 JS 쪽과 동일하게 유지한다.
    """
    total = stats.get("total_trades", 0) if stats else 0
    if not total:
        return """<div class="card" id="stats-card" style="margin-bottom:1.2rem"><h2>성과 분석</h2><p class="empty">거래 없음</p></div>"""

    wr = stats.get("win_rate_pct", 0.0)
    pf = stats.get("profit_factor", 0.0)
    avg_win = stats.get("avg_win", 0.0)
    avg_loss = stats.get("avg_loss", 0.0)
    total_pnl = stats.get("total_pnl", 0.0)

    wr_color = "var(--signal-buy)" if wr >= 50 else "var(--signal-sell)"
    pf_color = "var(--signal-buy)" if (pf is None or pf >= 1) else "var(--signal-sell)"
    pf_display = f"{pf:.2f}" if pf is not None else "∞"
    pnl_class = "win" if total_pnl >= 0 else "loss"
    sign = "+" if total_pnl >= 0 else ""
    wins = round(total * wr / 100)

    return f"""<div class="card" id="stats-card" style="margin-bottom:1.2rem">
      <h2>성과 분석 ({total}건)</h2>
      <div class="stats-flex">
        <div style="text-align:center">
          <div style="font-size:2.4rem;font-weight:var(--fw-strong);color:{wr_color};line-height:1.1">{wr:.1f}%</div>
          <div style="font-size:var(--fs-1);color:var(--text-dim);margin:.25rem 0 .3rem">승률</div>
          <div class="wr-bar"><div class="wr-fill" style="width:{wr}%;background:{wr_color}"></div></div>
          <div style="font-size:var(--fs-1);color:var(--text-dim);margin-top:.45rem">{wins}승 · {total - wins}패 · 총 {total}건</div>
        </div>
        <div class="stat-box-grid">
          <div class="stat-box">
            <div class="val" style="color:{pf_color}">{pf_display}</div>
            <div class="lbl">수익 팩터</div>
          </div>
          <div class="stat-box">
            <div class="val win">+₩{abs(avg_win):,.2f}</div>
            <div class="lbl">평균 수익</div>
          </div>
          <div class="stat-box">
            <div class="val loss">-₩{abs(avg_loss):,.2f}</div>
            <div class="lbl">평균 손실</div>
          </div>
          <div class="stat-box">
            <div class="val {pnl_class}">{sign}₩{abs(total_pnl):,.2f}</div>
            <div class="lbl">총 손익</div>
          </div>
        </div>
      </div>
    </div>"""


def _render_equity_svg(equity: list[dict]) -> str:
    """Render current-month daily P&L bar chart as inline SVG."""
    import calendar as _cal
    from datetime import date as _date, timedelta

    if not equity:
        return '<p class="empty">거래 없음 — 첫 청산 후 표시됩니다</p>'

    today = _date.today()
    prefix = today.strftime("%Y-%m")
    last_day = _cal.monthrange(today.year, today.month)[1]
    month_end = today.replace(day=last_day)

    by_date: dict[str, float] = {}
    for pt in equity:
        d = pt.get("time", "")[:10]
        if d.startswith(prefix):
            by_date[d] = float(pt.get("pnl", 0.0))

    all_dates: list[str] = []
    cur = today.replace(day=1)
    while cur <= month_end:
        all_dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    if not all_dates:
        return '<p class="empty">거래 없음</p>'

    daily: dict[str, float] = {d: by_date.get(d, 0.0) for d in all_dates}

    values = [daily[d] for d in all_dates]
    max_val = max(max(values), 0.0)
    min_val = min(min(values), 0.0)
    v_range = max(max_val - min_val, 1.0)
    max_abs = max(abs(max_val), abs(min_val), 1.0)

    W, H = 1000, 300
    pad_l, pad_r, pad_t, pad_b = 82, 20, 56, 44
    chart_w = W - pad_l - pad_r
    chart_h = H - pad_t - pad_b
    n = len(all_dates)
    # 막대:간격 ≈ 62:38(업계 관례 60~70% 막대) — 슬롯(중심 간 거리)의 62%를 막대 폭으로
    # 쓰되, 데이터가 적을 때 과도하게 두꺼워지지 않도록 상한(64px)을, 30개까지
    # 촘촘해져도 너무 가늘어지지 않도록 하한(6px)을 둔다.
    slot = chart_w / n
    bar_w = min(max(slot * 0.62, 6.0), 64.0)
    zero_y = pad_t + chart_h * max_val / v_range
    today_str = today.strftime("%Y-%m-%d")

    # 막대 값 라벨 겹침 방지 — 막대 중심 간 실제 간격(slot)이 라벨 예상 폭보다
    # 좁으면(실거래 30일치처럼 촘촘한 경우) 개별 막대 라벨을 전부 생략하고
    # 우측 상단 월 합계 숫자만 보여준다.
    def _equity_label(v: float) -> str:
        sign_ch = "+" if v >= 0 else ""
        return f"{sign_ch}₩{round(v):,}"

    max_equity_label_len = max((len(_equity_label(daily[d])) for d in all_dates), default=0)
    show_bar_labels = slot >= max_equity_label_len * 7 + 6

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block">'
    ]

    # 차트 영역 배경 — var(--chart-bg)는 라이트 테마에서 밝은 회색조로 자동 전환된다
    parts.append(
        f'<rect x="{pad_l}" y="{pad_t}" width="{chart_w}" height="{chart_h}" '
        f'fill="var(--chart-bg)" rx="4"/>'
    )

    # 눈금선 5레벨: ±max, ±mid, 0
    for factor in (-1.0, -0.5, 0.0, 0.5, 1.0):
        line_v = factor * max_abs
        line_y = pad_t + chart_h * (max_val - line_v) / v_range
        if not (pad_t <= line_y <= pad_t + chart_h):
            continue
        if factor == 0.0:
            stroke, stroke_w, dash = "var(--chart-grid)", "1.5", ""
        else:
            stroke, stroke_w, dash = "var(--chart-grid-soft)", "1", "5,3"
        parts.append(
            f'<line x1="{pad_l}" y1="{line_y:.1f}" x2="{W - pad_r}" y2="{line_y:.1f}" '
            f'stroke="{stroke}" stroke-width="{stroke_w}" stroke-dasharray="{dash}"/>'
        )
        label_v = round(abs(line_v))
        if label_v == 0:
            lcolor = "var(--text-dim)"
            ltext = "0"
        else:
            lcolor = "var(--chart-buy)" if line_v > 0 else "var(--chart-sell)"
            sign_ch = "+" if line_v > 0 else "-"
            ltext = f"{sign_ch}₩{label_v:,}"
        parts.append(
            f'<text x="{pad_l - 8}" y="{line_y + 4.5:.1f}" fill="{lcolor}" '
            f'font-size="13" text-anchor="end" font-family="system-ui,sans-serif">{ltext}</text>'
        )

    # 막대 + 날짜 레이블
    for i, d in enumerate(all_dates):
        v = daily[d]
        x = pad_l + (i + 0.5) * chart_w / n - bar_w / 2
        color = "var(--chart-buy)" if v >= 0 else "var(--chart-sell)"
        bh = max(2.0, abs(v) / v_range * chart_h) if v != 0 else 2.0
        bar_color = color if v != 0 else "var(--chart-grid)"
        y = zero_y - bh if v >= 0 else zero_y
        is_today = d == today_str
        opacity = "1" if is_today else "0.82"

        if is_today:
            parts.append(
                f'<rect x="{x - 1:.1f}" y="{pad_t}" width="{bar_w + 2:.1f}" '
                f'height="{chart_h}" fill="var(--chart-highlight)" rx="2"/>'
            )

        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'fill="{bar_color}" rx="2" opacity="{opacity}"/>'
        )

        # 막대 위 값 레이블 (충분히 클 때 + 라벨끼리 안 겹칠 때만)
        if show_bar_labels and bh > 22 and v != 0:
            sign_ch = "+" if v >= 0 else ""
            label_y = y - 7 if v >= 0 else y + bh + 15
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" fill="{color}" '
                f'font-size="12" text-anchor="middle" font-weight="700" '
                f'font-family="system-ui,sans-serif">{sign_ch}₩{round(v):,}</text>'
            )

        # 날짜 레이블: 1일·5단위·오늘
        day_num = int(d[8:])
        if day_num == 1 or day_num % 5 == 0 or is_today:
            lcolor = "var(--text-strong)" if is_today else "var(--text-muted)"
            fw = "700" if is_today else "400"
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{H - 13}" fill="{lcolor}" '
                f'font-size="13" text-anchor="middle" font-weight="{fw}" '
                f'font-family="system-ui,sans-serif">{day_num}일</text>'
            )

    # 헤더: 월 제목 (좌) + 누적 합계 (우)
    total = sum(daily.get(d, 0.0) for d in all_dates if d <= today_str)
    total_color = "var(--chart-buy)" if total >= 0 else "var(--chart-sell)"
    sign_ch = "+" if total >= 0 else ""
    year, mon = today.year, today.month
    parts.append(
        f'<text x="{pad_l}" y="30" fill="var(--text-muted)" font-size="15" font-weight="500" '
        f'font-family="system-ui,sans-serif">{year}년 {mon}월 일별 손익</text>'
    )
    parts.append(
        f'<text x="{W - pad_r}" y="32" fill="{total_color}" font-size="26" '
        f'text-anchor="end" font-weight="800" font-family="system-ui,sans-serif">'
        f'{sign_ch}₩{round(total):,}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _render_symbol_pnl_svg(trades: list[dict]) -> str:
    """Render top-N (by |realized P&L|) symbols as an inline SVG bar chart.

    Mirrors renderSymbolPnl() in the client-side JS below (same dual
    server+client render pattern already used for the equity curve).
    Always uses the full trades list — intentionally independent of the
    trade-history filter UI to keep scope minimal.
    """
    if not trades:
        return '<p class="empty">거래 없음 — 첫 청산 후 표시됩니다</p>'

    totals: dict[str, float] = {}
    for t in trades:
        sym = t.get("symbol", "-")
        totals[sym] = totals.get(sym, 0.0) + float(t.get("pnl", 0.0))

    if not totals:
        return '<p class="empty">거래 없음</p>'

    top_n = 8
    ranked = sorted(totals.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top_n]

    values = [v for _, v in ranked]
    max_val = max(max(values), 0.0)
    min_val = min(min(values), 0.0)
    v_range = max(max_val - min_val, 1.0)
    max_abs = max(abs(max_val), abs(min_val), 1.0)

    n = len(ranked)
    W, H = 1000, 260
    pad_l, pad_r, pad_t, pad_b = 82, 20, 40, 50
    chart_w = W - pad_l - pad_r
    chart_h = H - pad_t - pad_b
    # 막대:간격 ≈ 62:38(업계 관례), 심볼 수가 적을 때 과도하게 두꺼워지지 않도록
    # 상한을, 8개로 촘촘할 때도 너무 가늘어지지 않도록 하한을 둔다.
    sym_slot = chart_w / n
    bar_w = min(max(sym_slot * 0.62, 20.0), 90.0)
    zero_y = pad_t + chart_h * max_val / v_range

    # 막대 값 라벨 겹침 방지 — 실제 8개 심볼 규모에서 라벨끼리 겹치던 문제를
    # 슬롯 폭 대비 라벨 예상 폭으로 판단해 필요하면 전부 생략한다.
    def _sym_label(v: float) -> str:
        sign_ch = "+" if v >= 0 else ""
        return f"{sign_ch}₩{round(v):,}"

    max_sym_label_len = max((len(_sym_label(v)) for _, v in ranked), default=0)
    # 픽셀 추정만으로는 폰트/브라우저 렌더링 차이로 실제 겹침이 남을 수 있어,
    # 심볼이 6개 이상일 때는 보수적으로 항상 생략한다(3~5개까지는 라벨 유지).
    show_sym_bar_labels = sym_slot >= max_sym_label_len * 7 + 6 and n <= 5

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block">'
    ]
    parts.append(
        f'<rect x="{pad_l}" y="{pad_t}" width="{chart_w}" height="{chart_h}" '
        f'fill="var(--chart-bg)" rx="4"/>'
    )

    for factor in (-1.0, -0.5, 0.0, 0.5, 1.0):
        line_v = factor * max_abs
        line_y = pad_t + chart_h * (max_val - line_v) / v_range
        if not (pad_t <= line_y <= pad_t + chart_h):
            continue
        if factor == 0.0:
            stroke, stroke_w, dash = "var(--chart-grid)", "1.5", ""
        else:
            stroke, stroke_w, dash = "var(--chart-grid-soft)", "1", "5,3"
        parts.append(
            f'<line x1="{pad_l}" y1="{line_y:.1f}" x2="{W - pad_r}" y2="{line_y:.1f}" '
            f'stroke="{stroke}" stroke-width="{stroke_w}" stroke-dasharray="{dash}"/>'
        )
        label_v = round(abs(line_v))
        if label_v == 0:
            lcolor, ltext = "var(--text-dim)", "0"
        else:
            lcolor = "var(--chart-buy)" if line_v > 0 else "var(--chart-sell)"
            sign_ch = "+" if line_v > 0 else "-"
            ltext = f"{sign_ch}₩{label_v:,}"
        parts.append(
            f'<text x="{pad_l - 8}" y="{line_y + 4.5:.1f}" fill="{lcolor}" '
            f'font-size="13" text-anchor="end" font-family="system-ui,sans-serif">{ltext}</text>'
        )

    for i, (sym, v) in enumerate(ranked):
        x = pad_l + (i + 0.5) * chart_w / n - bar_w / 2
        color = "var(--chart-buy)" if v >= 0 else "var(--chart-sell)"
        bh = max(2.0, abs(v) / v_range * chart_h) if v != 0 else 2.0
        y = zero_y - bh if v >= 0 else zero_y
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'fill="{color}" rx="3" opacity="0.88"/>'
        )
        if show_sym_bar_labels and bh > 20:
            sign_ch = "+" if v >= 0 else ""
            label_y = y - 7 if v >= 0 else y + bh + 15
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" fill="{color}" '
                f'font-size="12" text-anchor="middle" font-weight="700" '
                f'font-family="system-ui,sans-serif">{sign_ch}₩{round(v):,}</text>'
            )
        label = sym.split("/")[0] if "/" in sym else sym
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{H - 16}" fill="var(--text-muted)" '
            f'font-size="13" text-anchor="middle" '
            f'font-family="system-ui,sans-serif">{label}</text>'
        )

    parts.append(
        f'<text x="{pad_l}" y="24" fill="var(--text-muted)" font-size="15" font-weight="500" '
        f'font-family="system-ui,sans-serif">심볼별 실현 손익 (상위 {n}개)</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _render_trade_filter_bar(trades: list[dict]) -> str:
    """Static filter controls (기간/종목/승패) shown above the position/buy/sell tabs.

    Filtering itself happens client-side only (_filterTrades() in the JS below)
    against already-fetched trade data — this function only renders the initial
    server-side markup, including symbol <option>s seeded from the first-paint
    trades list so the dropdown isn't empty before JS runs.
    """
    symbols = sorted({t["symbol"] for t in trades if t.get("symbol")})
    symbol_options = "".join(f'<option value="{s}">{s}</option>' for s in symbols)
    return f"""<select id="filter-period" onchange="applyTradeFilters()">
      <option value="all">전체 기간</option>
      <option value="7">최근 7일</option>
      <option value="30">최근 30일</option>
      <option value="90">최근 90일</option>
    </select>
    <select id="filter-symbol" onchange="applyTradeFilters()">
      <option value="all">전체 종목</option>
      {symbol_options}
    </select>
    <select id="filter-result" onchange="applyTradeFilters()">
      <option value="all">전체</option>
      <option value="win">승</option>
      <option value="loss">패</option>
    </select>"""


def _render_positions(positions: list[dict]) -> str:
    if not positions:
        return '<p class="empty">오픈 포지션 없음</p>'

    rows = []
    for pos in positions:
        sl = f"{pos['stop_loss']:,.2f}" if pos.get("stop_loss") else "—"
        tp = f"{pos['take_profit']:,.2f}" if pos.get("take_profit") else "—"
        hi = f"{pos['highest_price']:,.2f}" if pos.get("highest_price") else "—"
        entry_time = pos.get("entry_time", "")
        dt = entry_time[:16].replace("T", " ") if entry_time else "-"
        cur = pos.get("current_price")
        cur_str = f"{cur:,.2f}" if cur is not None else "—"
        pnl = pos.get("unrealized_pnl", 0.0)
        pnl_pct = pos.get("pnl_pct", 0.0)
        pnl_color = "var(--signal-buy)" if pnl >= 0 else "var(--signal-sell)"
        pnl_str = f"<span style='color:{pnl_color};font-weight:var(--fw-medium)'>₩{pnl:+,.0f} ({pnl_pct:+.2f}%)</span>"
        rows.append(
            f"<tr>"
            f"<td><code>{pos['symbol']}</code></td>"
            f"<td>{pos['side'].upper()}</td>"
            f"<td>{pos['amount']:.4f}</td>"
            f"<td>{pos['entry_price']:,.2f}</td>"
            f"<td style='font-weight:var(--fw-medium)'>{cur_str}</td>"
            f"<td style='color:var(--accent)'>{hi}</td>"
            f"<td>{pnl_str}</td>"
            f"<td style='color:var(--signal-sell)'>{sl}</td><td style='color:var(--signal-buy)'>{tp}</td>"
            f"<td style='color:var(--text-dim);font-size:var(--fs-3)'>{dt}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>종목</th><th>방향</th><th>수량</th>"
        "<th>진입가</th><th>현재가</th><th>신고가</th><th>평가손익</th>"
        "<th>손절가</th><th>목표가</th><th>진입 시각</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_ticks(ticks: list[dict]) -> str:
    if not ticks:
        return '<p class="empty">아직 평가 없음 (첫 tick 대기 중)</p>'
    # (fg, bg) 쌍 — 배지 배경은 항상 fg의 저채도 틴트 버전(var(--signal-*-bg))을 쓴다.
    action_style = {
        "BUY": ("var(--signal-buy)", "var(--signal-buy-bg)"),
        "SELL": ("var(--signal-sell)", "var(--signal-sell-bg)"),
        "HOLD": ("var(--text-muted)", "var(--bg-inset)"),
    }
    # ticks already ranked by 24h quoteVolume from state.get_ticks()
    thead = (
        '<table class="ticks-table"><thead><tr>'
        "<th style='width:2rem;text-align:center'>#</th>"
        "<th>종목</th><th>국면</th><th>신호</th><th>현재가</th><th>거래대금</th><th>RSI</th><th>MACD</th>"
        "<th title='매수: E M R V A P │ 매도: D M'>조건</th><th>시각</th>"
        "</tr></thead><tbody>"
    )

    def _pill(label: str, active: bool, buy: bool) -> str:
        cls = ("cp-buy" if buy else "cp-sell") if active else "cp-dim"
        return f"<span class='cp {cls}'>{label}</span>"

    def _row(rank: int, t: dict) -> str:
        action = t.get("action", "HOLD")
        color, color_bg = action_style.get(action, ("var(--text-muted)", "var(--bg-inset)"))
        meta = t.get("metadata") or {}
        cond = meta.get("cond") or {}
        price = meta.get("price")
        sym = t["symbol"]
        price_str = (
            f"₩{price:,.0f}" if price is not None and any(k in sym for k in ("BTC", "ETH", "SOL", "TAO"))
            else f"₩{price:,.2f}" if price is not None
            else "-"
        )
        rsi = f"{meta['rsi']:.1f}" if "rsi" in meta else "-"
        macd_h = meta.get("macd_hist")
        macd_str = f"{macd_h:+.4f}" if macd_h is not None else "-"
        vol_krw = meta.get("vol_krw")
        vol_str = f"₩{vol_krw:,.0f}" if vol_krw else "-"
        regime = meta.get("regime", "")
        regime_map = {
            "uptrend":   ("var(--signal-buy)", "var(--signal-buy-bg)", "상승"),
            "downtrend": ("var(--signal-sell)", "var(--signal-sell-bg)", "하락"),
            "ranging":   ("var(--signal-warn)", "var(--signal-warn-bg)", "횡보"),
        }
        rc, rc_bg, rl = regime_map.get(regime, ("var(--text-muted)", "var(--bg-inset)", regime or "-"))
        regime_html = f"<span class='badge' style='background:{rc_bg};color:{rc};font-size:var(--fs-1)'>{rl}</span>"
        ts = (t.get("timestamp") or "")[-8:-3]
        if cond:
            cond_html = (
                _pill("E", cond.get("above_ema", False), True)
                + _pill("M", cond.get("macd_just_pos", False), True)
                + _pill("R", cond.get("rsi_ok", False), True)
                + _pill("V", cond.get("vol_ok", False), True)
                + _pill("A", cond.get("adx_ok", False), True)
                + _pill("P", cond.get("ema_proximity_ok", False), True)
                + "<span style='margin:0 3px;color:var(--border);font-size:var(--fs-1)'>│</span>"
                + _pill("D", cond.get("death_cross", False), False)
                + _pill("M", cond.get("macd_turned_neg", False), False)
            )
        else:
            cond_html = "-"
        return (
            f"<tr>"
            f"<td style='text-align:center;color:var(--text-dim);font-size:var(--fs-1);font-weight:var(--fw-medium)'>{rank}</td>"
            f"<td><code>{sym}</code></td>"
            f"<td>{regime_html}</td>"
            f"<td><span class='badge' style='background:{color_bg};color:{color}'>{action}</span></td>"
            f"<td style='font-weight:var(--fw-medium)'>{price_str}</td>"
            f"<td style='color:var(--text-muted);font-size:var(--fs-3)'>{vol_str}</td>"
            f"<td style='color:var(--text-muted);font-size:var(--fs-3)'>{rsi}</td>"
            f"<td style='color:var(--text-muted);font-size:var(--fs-3)'>{macd_str}</td>"
            f"<td style='white-space:nowrap'>{cond_html}</td>"
            f"<td style='color:var(--text-dim);font-size:var(--fs-2)'>{ts}</td>"
            f"</tr>"
        )

    # 1-10위만 표기 (11-20위 탭은 상위 심볼 수 축소 이후 항상 비어 있어 제거됨)
    top10 = ticks[0:10]
    if top10:
        rows_html = "".join(
            [thead] + [_row(j + 1, t) for j, t in enumerate(top10)] + ["</tbody></table>"]
        )
    else:
        rows_html = '<p class="empty">데이터 없음</p>'
    return f'<div id="tick-panel-0" class="tick-panel">{rows_html}</div>'


def _balance_card_style(avg_buy_price: float, price: float) -> tuple[str, str, str, str]:
    """코인 카드의 수익/본전/손해/정보없음 상태에 따른 스타일을 산출한다.

    avg_buy_price가 없으면(매수이력 없음) 손익 계산이 불가능하므로 중립으로 처리한다.
    Returns: (border_color, bg_tint, icon, icon_color)
    """
    if avg_buy_price <= 0:
        return "var(--border)", "var(--bg-inset)", "", "var(--text-muted)"
    pnl_pct = (price / avg_buy_price - 1) * 100
    if pnl_pct > 0.1:
        return "var(--signal-buy)", "var(--signal-buy-bg)", "▲", "var(--signal-buy)"
    if pnl_pct < -0.1:
        return "var(--signal-sell)", "var(--signal-sell-bg)", "▼", "var(--signal-sell)"
    return "var(--signal-warn)", "var(--signal-warn-bg)", "－", "var(--signal-warn)"


def _render_balance(balance: list[dict]) -> str:
    if not balance:
        return '<p class="empty">잔고 없음</p>'

    # 라벨/값 스타일을 카드 전체에서 통일한다 (요구사항: 라벨 1단계, 값 최대 2단계 + tabular-nums 우측정렬)
    label_style = "color:var(--text-muted);font-size:var(--fs-2);white-space:nowrap;flex-shrink:0"
    row_style = "display:flex;justify-content:space-between;margin:.25rem 0"
    row_style_top = "display:flex;justify-content:space-between;align-items:flex-start;margin:.25rem 0"
    value_base = "font-variant-numeric:tabular-nums;white-space:nowrap"

    cards = []
    for b in balance:
        currency = b["currency"]
        free = b["free"]
        used = b["used"]
        price = b.get("price", 0.0)
        eval_amount = b.get("eval_amount", 0)
        avg_buy_price = b.get("avg_buy_price", 0.0)
        buy_amount = b.get("buy_amount", 0)

        border_color, bg_tint, pnl_icon, icon_color = _balance_card_style(avg_buy_price, price)
        icon_html = (
            f'<span style="color:{icon_color};font-size:var(--fs-2);margin-right:.35rem">{pnl_icon}</span>'
            if pnl_icon else ""
        )

        fmt_qty = f"{free:.8f}".rstrip("0").rstrip(".")
        fmt_price = f"₩{price:,.0f}" if price >= 1 else f"₩{price:.6f}"
        fmt_eval = f"₩{eval_amount:,}"
        fmt_avg = (
            f"₩{avg_buy_price:,.0f}" if avg_buy_price >= 1
            else f"₩{avg_buy_price:.6f}" if avg_buy_price > 0
            else "-"
        )
        fmt_buy_amt = f"₩{buy_amount:,}" if buy_amount > 0 else "-"

        # 평가금액 + 평가손익을 한 줄로 병합: 큰 값(평가금액) 아래 보조텍스트(손익)를 붙인다
        pnl_sub_html = ""
        if avg_buy_price > 0:
            pnl_krw = eval_amount - buy_amount
            pnl_pct = (price / avg_buy_price - 1) * 100
            pnl_sign = "+" if pnl_krw >= 0 else "-"
            pnl_sub_html = (
                f'<span style="{value_base};color:{border_color};font-size:var(--fs-1);font-weight:var(--fw-medium)">'
                f'{pnl_sign}₩{abs(pnl_krw):,.0f} ({pnl_sign}{abs(pnl_pct):.2f}%)</span>'
            )

        used_row = (
            f'<div style="{row_style}">'
            f'<span style="{label_style}">주문중</span>'
            f'<span style="{value_base};color:var(--text-muted);font-size:var(--fs-3)">'
            f'{f"{used:.8f}".rstrip("0").rstrip(".")}</span>'
            f'</div>'
        ) if used > 0 else ""

        cards.append(
            f'<div style="background:{bg_tint};border:1px solid {border_color};'
            f'border-left:3px solid {border_color};border-radius:.6rem;padding:.9rem">'
            f'<div style="font-size:var(--fs-5);font-weight:var(--fw-strong);color:var(--text-strong);margin-bottom:.65rem;'
            f'display:flex;justify-content:space-between;align-items:baseline">'
            f'<span>{icon_html}{currency}</span>'
            f'<span style="{value_base};font-size:var(--fs-1);font-weight:var(--fw-regular);color:var(--text-muted)">{fmt_qty}</span>'
            f'</div>'
            f'{used_row}'
            f'<div style="{row_style}">'
            f'<span style="{label_style}">현재가</span>'
            f'<span style="{value_base};color:var(--text-muted);font-size:var(--fs-3)">{fmt_price}</span>'
            f'</div>'
            f'<div style="{row_style}">'
            f'<span style="{label_style}">매수평균가</span>'
            f'<span style="{value_base};color:var(--accent);font-size:var(--fs-3)">{fmt_avg}</span>'
            f'</div>'
            f'<div style="border-top:1px solid var(--border);margin:.55rem 0 .4rem"></div>'
            f'<div style="{row_style}">'
            f'<span style="{label_style}">매수금액</span>'
            f'<span style="{value_base};color:var(--accent);font-size:var(--fs-3)">{fmt_buy_amt}</span>'
            f'</div>'
            f'<div style="{row_style_top}">'
            f'<span style="{label_style}">평가금액</span>'
            f'<span style="display:flex;flex-direction:column;align-items:flex-end;gap:.1rem">'
            f'<span style="{value_base};color:var(--signal-buy);font-weight:var(--fw-strong);font-size:var(--fs-5)">{fmt_eval}</span>'
            f'{pnl_sub_html}'
            f'</span>'
            f'</div>'
            f'</div>'
        )

    total_eval = sum(b.get("eval_amount", 0) for b in balance)
    grid = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:.75rem">'
        + "".join(cards)
        + "</div>"
        + f'<div class="stat-row" style="margin-top:.75rem;border-top:1px solid var(--border);padding-top:.6rem">'
        f'<span class="stat-label">총 평가금액</span>'
        f'<span class="stat-value" style="color:var(--signal-buy);font-weight:var(--fw-strong)">₩{total_eval:,}</span>'
        f"</div>"
    )
    return grid


def _render_buy_history(trades: list[dict], positions: list[dict] | None = None) -> str:
    rows = []
    # Open positions first (보유중)
    for pos in (positions or []):
        if pos.get("side") != "buy":
            continue
        entry_time = pos.get("entry_time", "")
        dt = entry_time[:16].replace("T", " ") if entry_time else "-"
        cost = pos["entry_price"] * pos["amount"]
        rows.append(
            f"<tr>"
            f"<td style='color:var(--text-dim);font-size:var(--fs-3)'>{dt}</td>"
            f"<td><code>{pos['symbol']}</code></td>"
            f"<td style='color:var(--text-muted)'>{pos['amount']:.6f}</td>"
            f"<td style='font-weight:var(--fw-medium);color:var(--signal-buy)'>₩{pos['entry_price']:,.0f}</td>"
            f"<td style='color:var(--text-muted)'>₩{cost:,.0f}</td>"
            f"<td><span style='background:var(--signal-buy-bg);color:var(--signal-buy);padding:.1rem .4rem;"
            f"border-radius:9999px;font-size:var(--fs-1);font-weight:var(--fw-strong)'>보유중</span></td>"
            f"</tr>"
        )
    # Closed trades
    for t in (trades or []):
        entry_dt = t["entry_time"][:16].replace("T", " ")
        cost = t["entry_price"] * t["amount"]
        rows.append(
            f"<tr>"
            f"<td style='color:var(--text-dim);font-size:var(--fs-3)'>{entry_dt}</td>"
            f"<td><code>{t['symbol']}</code></td>"
            f"<td style='color:var(--text-muted)'>{t['amount']:.6f}</td>"
            f"<td style='font-weight:var(--fw-medium);color:var(--signal-buy)'>₩{t['entry_price']:,.0f}</td>"
            f"<td style='color:var(--text-muted)'>₩{cost:,.0f}</td>"
            f"<td style='color:var(--text-dim);font-size:var(--fs-1)'>청산</td>"
            f"</tr>"
        )
    if not rows:
        return '<p class="empty">매수 이력 없음</p>'
    return (
        "<table><thead><tr>"
        "<th>매수 시각</th><th>종목</th><th>수량</th><th>매수가</th><th>매수 금액</th><th>상태</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_mode_switch_btn(mode: str) -> str:
    """Server-side render of the mode switch button (shown on initial page load)."""
    import math
    from datetime import datetime, timedelta, timezone

    kst = timezone(timedelta(hours=9))
    unlock_dt = datetime(2026, 4, 30, 0, 0, 0, tzinfo=kst)
    now = datetime.now(tz=kst)
    unlocked = now >= unlock_dt

    if mode.upper() == "LIVE":
        return (
            '<div class="mode-switch">'
            '<button class="btn-to-paper" onclick="switchMode(\'paper\',this)" style="width:100%">'
            f"{_icon('clipboard', 14)} PAPER 모드로 전환</button></div>"
        )

    if not unlocked:
        diff_days = math.ceil((unlock_dt - now).total_seconds() / 86400)
        return (
            f'<div class="mode-switch">'
            f'<button class="btn-locked" disabled style="width:100%">'
            f"{_icon('lock', 14)} LIVE 전환 — D-{diff_days} (4/30 활성화)</button></div>"
        )

    return (
        '<div class="mode-switch">'
        '<button class="btn-live" onclick="switchMode(\'live\',this)" style="width:100%">'
        f"{_icon('bolt', 15)} LIVE 모드로 전환</button></div>"
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
            f"<td style='color:var(--text-dim);font-size:var(--fs-3)'>{exit_dt}</td>"
            f"<td><code>{t['symbol']}</code></td>"
            f"<td style='color:var(--text-muted)'>{t['amount']:.6f}</td>"
            f"<td style='font-weight:var(--fw-medium);color:var(--signal-sell)'>₩{t['exit_price']:,.0f}</td>"
            f"<td class='{cls}'>{sign}₩{pnl:,.0f} ({sign}{t['pnl_pct']:.2f}%)</td>"
            f"<td style='color:var(--text-dim);font-size:var(--fs-1)'>{t['reason']}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>매도 시각</th><th>종목</th><th>수량</th><th>매도가</th><th>손익</th><th>사유</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
