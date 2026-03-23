"""
HTML 回测报告生成器 + 结果导出工具。

使用 Plotly 生成交互式图表，首次使用 CDN 加载 Plotly JS。
如需完全离线，将 include_plotlyjs 改为 True。

使用方式：
    from backtest.report import generate_report, save_json, save_grid_csv
    report_path = generate_report(
        metrics=metrics,
        equity_curve=broker.equity_curve,
        trades=broker.get_trades(),
        filled_orders=broker.get_filled_orders(),
        bars=bars,
        title="MyStrategy | BTC-USDT",
    )
    save_json(metrics, "mystr", "./output/")
    save_grid_csv(grid_results, "mystr", "./output/")
    print_grid_table(grid_results, ["param1", "param2"])

依赖：pip install plotly pandas
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.models import BarData, OrderData, TradeData

logger = logging.getLogger(__name__)


def generate_report(
    metrics: dict,
    equity_curve: list[tuple[datetime, Decimal]],
    trades: list,
    filled_orders: list,
    bars: list,
    benchmark_equity: list[tuple[datetime, Decimal]] | None = None,
    title: str = "Backtest Report",
    output_dir: str = "./output/",
) -> str:
    """
    生成 HTML 回测报告，返回文件路径。

    报告包含 6 个区域：
      ① KPI 卡片行
      ② 权益曲线 + 回撤图
      ③ K 线图 + 买卖信号
      ④ 月度收益热力图
      ⑤ 逐笔交易盈亏条形图
      ⑥ 详细指标表格
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        raise ImportError(
            "报告生成需要 plotly，请运行：pip install plotly"
        )

    os.makedirs(output_dir, exist_ok=True)

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = title.replace("/", "_").replace(" ", "_").replace("|", "_")
    filename = f"{safe_title}_{now_str}.html"
    filepath = os.path.join(output_dir, filename)

    # ─────────────────────── 数据准备 ────────────────────────────

    eq_times = [ts for ts, _ in equity_curve]
    eq_values = [float(v) for _, v in equity_curve]

    init_val = eq_values[0] if eq_values else 1.0

    # 权益收益率
    eq_returns_pct = [(v / init_val - 1) * 100 for v in eq_values]

    # 回撤序列
    drawdowns = _calc_drawdown_series(eq_values)

    # 基准序列（如果提供）
    bm_times = [ts for ts, _ in benchmark_equity] if benchmark_equity else []
    bm_values = [float(v) for _, v in benchmark_equity] if benchmark_equity else []

    # ─────────────────────── ① KPI 卡片 ─────────────────────────

    kpi_html = _build_kpi_cards(metrics)

    # ─────────────────────── ② 权益曲线 + 回撤 ───────────────────

    equity_fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.04,
    )

    equity_fig.add_trace(
        go.Scatter(
            x=eq_times, y=eq_values,
            name="策略权益",
            line=dict(color="#42a5f5", width=2),
            hovertemplate=(
                "%{x|%Y-%m-%d %H:%M}<br>"
                "权益: %{y:,.2f}<br>"
                "<extra></extra>"
            ),
        ),
        row=1, col=1,
    )

    if bm_values:
        equity_fig.add_trace(
            go.Scatter(
                x=bm_times, y=bm_values,
                name="基准",
                line=dict(color="#78909c", width=1.5, dash="dash"),
                hovertemplate="%{x|%Y-%m-%d %H:%M}<br>基准: %{y:,.2f}<extra></extra>",
            ),
            row=1, col=1,
        )

    equity_fig.add_trace(
        go.Scatter(
            x=eq_times, y=drawdowns,
            name="回撤%",
            fill="tozeroy",
            fillcolor="rgba(239,83,80,0.35)",
            line=dict(color="rgba(239,83,80,0.8)", width=1),
            hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.2f}%<extra></extra>",
        ),
        row=2, col=1,
    )

    equity_fig.update_layout(
        height=500,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)", bordercolor="#21262d",
        ),
        margin=dict(l=60, r=20, t=20, b=20),
        hovermode="x unified",
    )
    equity_fig.update_xaxes(gridcolor="#21262d", showgrid=True, zeroline=False)
    equity_fig.update_yaxes(gridcolor="#21262d", showgrid=True, zeroline=False)
    equity_html = equity_fig.to_html(full_html=False, include_plotlyjs=False)

    # ─────────────────────── ③ K 线图 + 信号 ─────────────────────

    candle_html = _build_candlestick_chart(bars, filled_orders)

    # ─────────────────────── ④ 月度热力图 ────────────────────────

    monthly_html = _build_monthly_heatmap(equity_curve)

    # ─────────────────────── ⑤ 逐笔盈亏 ─────────────────────────

    pnl_html = _build_trade_pnl_chart(filled_orders)

    # ─────────────────────── ⑥ 每笔明细 ─────────────────────────

    trade_detail_html = _build_trade_detail_table(filled_orders)

    # ─────────────────────── ⑦ 指标表格 ─────────────────────────

    table_html = _build_metrics_table(metrics)

    # ─────────────────────── 组装 HTML ───────────────────────────

    start_date = metrics.get("start_date", "N/A")
    end_date = metrics.get("end_date", "N/A")
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # FIX: [回测报告需注明资金费率未计入，避免实盘 PnL 与回测偏差被忽视]
    funding_rate_note = (
        "⚠️ 注意：本报告未计入永续合约资金费率（每 8 小时结算），"
        "实盘 PnL 会因此偏差，请结合资金费率历史数据修正。"
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  /* ── Reset & Base ── */
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0d1117;
    color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding: 28px 36px 56px;
    min-width: 860px;
  }}

  /* ── Header ── */
  .report-header {{
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 28px;
    padding-bottom: 18px;
    border-bottom: 1px solid #21262d;
  }}
  .report-header h1 {{
    font-size: 22px;
    font-weight: 700;
    color: #e6edf3;
    letter-spacing: -0.3px;
    margin-bottom: 6px;
  }}
  .header-meta {{
    display: flex;
    align-items: center;
    gap: 6px;
    color: #8b949e;
    font-size: 13px;
  }}
  .header-meta .sep {{ color: #30363d; }}
  .header-tag {{
    display: inline-block;
    background: #1f6feb22;
    color: #58a6ff;
    border: 1px solid #1f6feb55;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 7px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
  }}
  .gen-time {{
    font-size: 12px;
    color: #6e7681;
    text-align: right;
    line-height: 1.8;
  }}

  /* ── Section ── */
  .section {{
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 20px 24px;
    margin-bottom: 18px;
  }}
  .section-title {{
    font-size: 13px;
    font-weight: 600;
    color: #8b949e;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
  }}
  .section-title::before {{
    content: '';
    display: inline-block;
    width: 3px;
    height: 14px;
    background: #388bfd;
    border-radius: 2px;
    flex-shrink: 0;
  }}

  /* ── KPI Cards ── */
  .kpi-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .kpi-card {{
    flex: 1 1 150px;
    min-width: 140px;
    border-radius: 8px;
    padding: 16px 18px 14px;
    border: 1px solid transparent;
    transition: transform 0.15s, box-shadow 0.15s;
  }}
  .kpi-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.35);
  }}
  .kpi-card.positive {{
    background: linear-gradient(135deg, #0d2818 0%, #0f2a1c 100%);
    border-color: #196c37;
  }}
  .kpi-card.negative {{
    background: linear-gradient(135deg, #2d1117 0%, #280f15 100%);
    border-color: #6e1c24;
  }}
  .kpi-card.neutral {{
    background: linear-gradient(135deg, #1c2128 0%, #1a1f27 100%);
    border-color: #30363d;
  }}
  .kpi-card.warning {{
    background: linear-gradient(135deg, #271d08 0%, #221905 100%);
    border-color: #6b5a1a;
  }}
  .kpi-icon {{ font-size: 16px; margin-bottom: 10px; display: block; }}
  .kpi-value {{
    font-size: 26px;
    font-weight: 700;
    line-height: 1.1;
    letter-spacing: -0.5px;
    font-variant-numeric: tabular-nums;
  }}
  .kpi-card.positive .kpi-value {{ color: #3fb950; }}
  .kpi-card.negative .kpi-value {{ color: #f85149; }}
  .kpi-card.neutral  .kpi-value {{ color: #c9d1d9; }}
  .kpi-card.warning  .kpi-value {{ color: #d29922; }}
  .kpi-label {{
    font-size: 11.5px;
    color: #6e7681;
    margin-top: 7px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}

  /* ── Metrics Table ── */
  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    border: 1px solid #21262d;
    border-radius: 8px;
    overflow: hidden;
  }}
  @media (max-width: 860px) {{
    .metrics-grid {{ grid-template-columns: 1fr; }}
  }}
  .metrics-col {{ border-right: 1px solid #21262d; }}
  .metrics-col:last-child {{ border-right: none; }}
  .metrics-col-header {{
    font-size: 11px;
    font-weight: 600;
    color: #388bfd;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    padding: 9px 14px;
    background: #0d1117;
    border-bottom: 1px solid #21262d;
  }}
  .metrics-table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .metrics-table tr {{ transition: background 0.1s; }}
  .metrics-table tr:hover {{ background: rgba(56,139,253,0.05); }}
  .metrics-table td {{
    padding: 7px 14px;
    font-size: 13px;
    border-bottom: 1px solid #21262d;
    vertical-align: middle;
  }}
  .metrics-table tr:last-child td {{ border-bottom: none; }}
  .metrics-table td:first-child {{
    color: #8b949e;
    width: 52%;
  }}
  .metrics-table td:last-child {{
    text-align: right;
    color: #e6edf3;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }}
  .positive-val {{ color: #3fb950 !important; }}
  .negative-val {{ color: #f85149 !important; }}

  /* ── Trade Detail Table ── */
  .trade-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }}
  .trade-table th {{
    background: #0d1117;
    color: #388bfd;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 8px 10px;
    border-bottom: 1px solid #21262d;
    text-align: left;
    white-space: nowrap;
  }}
  .trade-table td {{
    padding: 6px 10px;
    border-bottom: 1px solid #21262d;
    color: #c9d1d9;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }}
  .trade-table tr:last-child td {{ border-bottom: none; }}
  .trade-table tr:hover td {{ background: rgba(56,139,253,0.05); }}
  .trade-table .td-pos {{ color: #3fb950; font-weight: 600; }}
  .trade-table .td-neg {{ color: #f85149; font-weight: 600; }}
</style>
</head>
<body>

<div class="report-header">
  <div>
    <h1>{title}</h1>
    <div class="header-meta">
      <span class="header-tag">回测报告</span>
      <span class="sep">·</span>
      <span>📅 {start_date}</span>
      <span class="sep">→</span>
      <span>{end_date}</span>
    </div>
  </div>
  <div class="gen-time">生成时间<br>{gen_time}</div>
</div>

<div style="background:#161b22;border:1px solid #6b5a1a;border-radius:8px;padding:12px 20px;
            margin-bottom:18px;color:#d29922;font-size:13px;">
  {funding_rate_note}
</div>

<div class="section">
  <div class="section-title">核心指标</div>
  {kpi_html}
</div>

<div class="section">
  <div class="section-title">权益曲线 &amp; 回撤</div>
  {equity_html}
</div>

<div class="section">
  <div class="section-title">K 线走势 &amp; 交易信号</div>
  {candle_html}
</div>

<div class="section">
  <div class="section-title">月度收益热力图</div>
  {monthly_html}
</div>

<div class="section">
  <div class="section-title">逐笔交易盈亏</div>
  {pnl_html}
</div>

<div class="section">
  <div class="section-title">每笔交易明细</div>
  {trade_detail_html}
</div>

<div class="section">
  <div class="section-title">详细指标</div>
  <div class="metrics-grid">
    {table_html}
  </div>
</div>

</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return filepath


# ─────────────────────── 辅助函数 ────────────────────────────────


def _format_duration(seconds: float) -> str:
    """将秒数格式化为可读字符串（分钟 / 小时 / 天）"""
    if seconds < 0:
        return "0 分钟"
    if seconds < 3600:
        return f"{int(seconds / 60)} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时"
    return f"{seconds / 86400:.1f} 天"


def _build_trade_detail_table(filled_orders: list) -> str:
    """
    生成每笔完整交易的 HTML 明细表格。

    使用 FIFO 配对策略：
    - 开仓订单：order.pnl == 0（尚未产生盈亏）
    - 平仓订单：order.pnl != 0（产生实际盈亏）
    - 按 inst_id 分组，时间顺序先进先出配对

    每行包含：入场时间、入场价、出场时间、出场价、数量、
              盈亏(USDT)、盈亏率%、持仓时长。
    """
    from collections import deque
    from core.enums import OrderSide

    if not filled_orders:
        return "<p style='color:#90a4ae;padding:12px'>无交易记录</p>"

    entry_queues: dict[str, deque] = {}
    rows_html: list[str] = []

    for order in filled_orders:
        if order.order_id.startswith("FORCE-"):
            continue
        pnl  = float(order.pnl)
        inst = order.inst_id

        if pnl == 0.0:
            # 开仓单：入队
            if inst not in entry_queues:
                entry_queues[inst] = deque()
            entry_queues[inst].append({
                "time":  order.update_time,
                "price": float(order.filled_price),
                "qty":   float(order.filled_quantity),
                "side":  order.side,
            })
        else:
            # 平仓单：出队配对
            q = entry_queues.get(inst)
            if not q:
                continue
            entry      = q.popleft()
            hold_secs  = (order.update_time - entry["time"]).total_seconds()
            hold_str   = _format_duration(hold_secs)
            entry_cost = entry["price"] * entry["qty"]
            pnl_pct    = (pnl / entry_cost * 100) if entry_cost else 0.0
            direction  = "多 ↑" if entry["side"] == OrderSide.BUY else "空 ↓"
            pnl_cls    = "td-pos" if pnl >= 0 else "td-neg"
            idx        = len(rows_html) + 1

            rows_html.append(
                f"<tr>"
                f"<td>{idx}</td>"
                f"<td>{direction}</td>"
                f"<td>{entry['time'].strftime('%Y-%m-%d %H:%M')}</td>"
                f"<td>{entry['price']:.4f}</td>"
                f"<td>{order.update_time.strftime('%Y-%m-%d %H:%M')}</td>"
                f"<td>{float(order.filled_price):.4f}</td>"
                f"<td>{entry['qty']:.4f}</td>"
                f"<td class='{pnl_cls}'>{pnl:+.2f}</td>"
                f"<td class='{pnl_cls}'>{pnl_pct:+.2f}%</td>"
                f"<td>{hold_str}</td>"
                f"</tr>"
            )

    if not rows_html:
        return "<p style='color:#90a4ae;padding:12px'>无完整交易轮次（开仓+平仓配对）</p>"

    return f"""<div style="overflow-x:auto">
<table class="trade-table">
  <thead>
    <tr>
      <th>#</th><th>方向</th>
      <th>入场时间</th><th>入场价</th>
      <th>出场时间</th><th>出场价</th>
      <th>数量</th>
      <th>盈亏(USDT)</th><th>盈亏率</th><th>持仓时长</th>
    </tr>
  </thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
</div>"""


def _calc_drawdown_series(equities: list[float]) -> list[float]:
    """计算每个时点的回撤百分比（负值）"""
    if not equities:
        return []
    peak = equities[0]
    result = []
    for v in equities:
        if v > peak:
            peak = v
        dd = (v / peak - 1) * 100 if peak > 0 else 0.0
        result.append(dd)
    return result


def _build_kpi_cards(metrics: dict) -> str:
    """生成 KPI 卡片行 HTML"""

    def _card(label: str, value: str, cls: str, icon: str) -> str:
        return f"""<div class="kpi-card {cls}">
          <span class="kpi-icon">{icon}</span>
          <div class="kpi-value">{value}</div>
          <div class="kpi-label">{label}</div>
        </div>"""

    total_ret = metrics.get("total_return_pct", 0.0)
    annual_ret = metrics.get("annual_return_pct", 0.0)
    sharpe    = metrics.get("sharpe_ratio", 0.0)
    max_dd    = metrics.get("max_drawdown_pct", 0.0)
    win_rate  = metrics.get("win_rate_pct", 0.0)
    pf        = metrics.get("profit_factor", 0.0)

    cards = [
        _card("总收益率",  f"{total_ret:+.2f}%",
              "positive" if total_ret >= 0 else "negative", "📈" if total_ret >= 0 else "📉"),
        _card("年化收益率", f"{annual_ret:+.2f}%",
              "positive" if annual_ret >= 0 else "negative", "📊"),
        _card("Sharpe 比率", f"{sharpe:.3f}",
              "positive" if sharpe >= 1.0 else ("neutral" if sharpe >= 0 else "negative"), "⚡"),
        _card("最大回撤",   f"{max_dd:.2f}%",
              "warning", "🔻"),
        _card("胜率",      f"{win_rate:.1f}%",
              "positive" if win_rate >= 50 else "negative", "🎯"),
        _card("盈亏比",    f"{pf:.3f}",
              "positive" if pf >= 1.0 else "negative", "⚖️"),
    ]
    return f'<div class="kpi-row">{"".join(cards)}</div>'


def _build_candlestick_chart(bars: list, filled_orders: list) -> str:
    """生成 K 线图 + 买卖信号"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return "<p style='color:#90a4ae'>需要 plotly 才能显示 K 线图</p>"

    if not bars:
        return "<p style='color:#90a4ae'>无 K 线数据</p>"

    times  = [b.timestamp for b in bars]
    opens  = [float(b.open)  for b in bars]
    highs  = [float(b.high)  for b in bars]
    lows   = [float(b.low)   for b in bars]
    closes = [float(b.close) for b in bars]

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=times, open=opens, high=highs, low=lows, close=closes,
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
        name="K线",
    ))

    # ── 买卖信号 ──────────────────────────────────────────────────
    from core.enums import OrderSide

    # create_time = 策略触发信号那根 K 线的时间（bar[n]）
    # update_time = 订单实际成交那根 K 线的时间（bar[n+1]）
    # 信号标记显示在触发K线上，所以用 create_time
    bar_by_time = {b.timestamp: b for b in bars}

    buy_times,  buy_prices,  buy_texts  = [], [], []
    sell_times, sell_prices, sell_texts = [], [], []

    for order in filled_orders:
        if order.order_id.startswith("FORCE-"):
            continue
        bar_match = bar_by_time.get(order.create_time)
        if bar_match is None:
            continue
        price   = float(order.filled_price)
        qty     = float(order.filled_quantity)
        pnl     = float(order.pnl)
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        if order.side == OrderSide.BUY:
            buy_times.append(bar_match.timestamp)
            buy_prices.append(float(bar_match.low) * 0.997)
            buy_texts.append(f"买入  @{price:.4f}<br>数量: {qty}  PnL: {pnl_str}")
        else:
            sell_times.append(bar_match.timestamp)
            sell_prices.append(float(bar_match.high) * 1.003)
            sell_texts.append(f"卖出  @{price:.4f}<br>数量: {qty}  PnL: {pnl_str}")

    if buy_times:
        fig.add_trace(go.Scatter(
            x=buy_times, y=buy_prices,
            mode="markers",
            name="买入",
            marker=dict(
                symbol="triangle-up",
                size=14,
                color="#f0e040",           # 亮黄：与绿色 K 线明显区分
                line=dict(color="#ffffff", width=1.2),
            ),
            hovertext=buy_texts,
            hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>%{hovertext}<extra>买入</extra>",
        ))

    if sell_times:
        fig.add_trace(go.Scatter(
            x=sell_times, y=sell_prices,
            mode="markers",
            name="卖出",
            marker=dict(
                symbol="triangle-down",
                size=14,
                color="#bf5af2",           # 亮紫：与红色 K 线明显区分
                line=dict(color="#ffffff", width=1.2),
            ),
            hovertext=sell_texts,
            hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>%{hovertext}<extra>卖出</extra>",
        ))

    fig.update_layout(
        height=500,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        xaxis_rangeslider_visible=True,
        xaxis_rangeslider=dict(bgcolor="#161b22", bordercolor="#21262d"),
        margin=dict(l=60, r=20, t=10, b=20),
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    bgcolor="rgba(0,0,0,0)", bordercolor="#21262d"),
    )
    fig.update_xaxes(gridcolor="#21262d", zeroline=False)
    fig.update_yaxes(gridcolor="#21262d", zeroline=False)

    return fig.to_html(full_html=False, include_plotlyjs=False)


def _build_monthly_heatmap(equity_curve: list[tuple[datetime, Decimal]]) -> str:
    """生成月度收益热力图"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return "<p style='color:#90a4ae'>需要 plotly 才能显示热力图</p>"

    if len(equity_curve) < 2:
        return "<p style='color:#90a4ae'>数据不足，无法生成月度热力图</p>"

    # 按月聚合：取每月最后一个权益值
    monthly: dict[tuple[int, int], float] = {}
    for ts, eq in equity_curve:
        key = (ts.year, ts.month)
        monthly[key] = float(eq)

    # 计算月度收益率
    sorted_keys = sorted(monthly.keys())
    prev_val: dict[int, float] = {}  # 上月末权益
    monthly_ret: dict[tuple[int, int], float] = {}

    init_val = float(equity_curve[0][1])
    prev_end = init_val

    for i, key in enumerate(sorted_keys):
        year, month = key
        cur_val = monthly[key]

        if i == 0:
            monthly_ret[key] = (cur_val / init_val - 1) * 100
        else:
            prev_key = sorted_keys[i - 1]
            prev_val_cur = monthly[prev_key]
            monthly_ret[key] = (cur_val / prev_val_cur - 1) * 100

    # 构建矩阵
    years = sorted(set(k[0] for k in sorted_keys))
    months_label = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    z_matrix = []
    text_matrix = []
    row_labels = []

    for year in years:
        row = []
        text_row = []
        for m in range(1, 13):
            ret = monthly_ret.get((year, m), None)
            row.append(ret if ret is not None else 0.0)
            text_row.append(f"{ret:.2f}%" if ret is not None else "")
        z_matrix.append(row)
        text_matrix.append(text_row)
        row_labels.append(str(year))

    # 年度合计列
    annual_col = []
    for year in years:
        year_rets = [monthly_ret.get((year, m), 0.0) for m in range(1, 13)
                     if (year, m) in monthly_ret]
        if year_rets:
            # 复利年度收益
            annual = (1.0)
            for r in year_rets:
                annual *= (1 + r / 100)
            annual_col.append((annual - 1) * 100)
        else:
            annual_col.append(0.0)

    col_labels = months_label + ["全年"]
    for i, yr in enumerate(years):
        z_matrix[i].append(annual_col[i])
        text_matrix[i].append(f"{annual_col[i]:.2f}%")

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=col_labels,
        y=row_labels,
        text=text_matrix,
        texttemplate="%{text}",
        colorscale=[
            [0.0, "#da3633"],
            [0.5, "#161b22"],
            [1.0, "#2ea043"],
        ],
        zmid=0,
        showscale=False,
        hovertemplate="%{y}年 %{x}<br>收益: %{z:.2f}%<extra></extra>",
    ))

    fig.update_layout(
        height=max(130, len(years) * 44 + 80),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        margin=dict(l=60, r=20, t=10, b=40),
        xaxis=dict(side="top", gridcolor="#21262d"),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)


def _build_trade_pnl_chart(filled_orders: list) -> str:
    """生成逐笔交易盈亏条形图"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return "<p style='color:#90a4ae'>需要 plotly 才能显示盈亏图</p>"

    close_orders = [o for o in filled_orders if float(o.pnl) != 0.0 and not o.order_id.startswith("FORCE-")]
    if not close_orders:
        return "<p style='color:#90a4ae'>无平仓交易记录</p>"

    indices = list(range(1, len(close_orders) + 1))
    pnls = [float(o.pnl) for o in close_orders]
    colors = ["#26a69a" if p >= 0 else "#ef5350" for p in pnls]
    hover_texts = [
        f"第{i}笔 | {o.side.value} | 品种:{o.inst_id}<br>"
        f"成交价:{float(o.filled_price):.4f} | 数量:{float(o.filled_quantity):.4f}<br>"
        f"盈亏:{float(o.pnl):.2f}"
        for i, o in enumerate(close_orders, start=1)
    ]

    fig = go.Figure(go.Bar(
        x=indices,
        y=pnls,
        marker_color=colors,
        hovertext=hover_texts,
        hovertemplate="%{hovertext}<extra></extra>",
    ))

    fig.update_layout(
        height=350,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        margin=dict(l=60, r=20, t=10, b=40),
        xaxis=dict(title="交易序号", gridcolor="#21262d", zeroline=False),
        yaxis=dict(title="盈亏 (USDT)", gridcolor="#21262d", zeroline=True,
                   zerolinecolor="#30363d", zerolinewidth=1),
        hovermode="x",
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)


def _build_metrics_table(metrics: dict) -> str:
    """生成三列指标表格 HTML"""

    def _fmt(key: str, v) -> str:
        if v is None:
            return "N/A"
        if isinstance(v, str):
            return v
        if isinstance(v, int):
            return f"{v:,}"
        if isinstance(v, float):
            if "pct" in key or "rate" in key or "return" in key:
                color = "positive-val" if v > 0 else ("negative-val" if v < 0 else "")
                return f'<span class="{color}">{v:+.2f}%</span>'
            if "ratio" in key:
                color = "positive-val" if v > 0 else ("negative-val" if v < 0 else "")
                return f'<span class="{color}">{v:.4f}</span>'
            if "capital" in key or "equity" in key or "pnl" in key or "fee" in key or "win" in key or "loss" in key or "expectancy" in key:
                color = "positive-val" if v > 0 else ("negative-val" if v < 0 else "")
                return f'<span class="{color}">${v:,.2f}</span>'
            return f"{v:.4f}"
        return str(v)

    def _row(label: str, key: str, m: dict) -> str:
        v = m.get(key, "N/A")
        return f"<tr><td>{label}</td><td>{_fmt(key, v)}</td></tr>"

    m = metrics

    col1_rows = [
        ("初始资金", "initial_capital"),
        ("最终权益", "final_equity"),
        ("总盈亏", "total_pnl"),
        ("总收益率", "total_return_pct"),
        ("年化收益率", "annual_return_pct"),
        ("回测天数", "backtest_days"),
        ("开始日期", "start_date"),
        ("结束日期", "end_date"),
    ]
    col2_rows = [
        ("最大回撤", "max_drawdown_pct"),
        ("最大回撤金额", "max_drawdown_amount"),
        ("最大回撤持续", "max_drawdown_duration_days"),
        ("年化波动率", "annual_volatility_pct"),
        ("夏普比率", "sharpe_ratio"),
        ("Sortino 比率", "sortino_ratio"),
        ("卡玛比率", "calmar_ratio"),
        ("总手续费", "total_fees"),
    ]
    col3_rows = [
        ("总交易次数", "total_trades"),
        ("完整轮次", "round_trips"),
        ("平均持仓时长(h)", "avg_holding_hours"),
        ("盈利次数", "win_trades"),
        ("亏损次数", "loss_trades"),
        ("胜率", "win_rate_pct"),
        ("盈亏比", "profit_factor"),
        ("最大单笔盈利", "max_win"),
        ("最大单笔亏损", "max_loss"),
        ("平均盈利", "avg_win"),
        ("平均亏损", "avg_loss"),
        ("最大连续盈利", "max_consecutive_wins"),
        ("最大连续亏损", "max_consecutive_losses"),
        ("期望收益", "expectancy"),
    ]

    def _build_col(title: str, rows: list[tuple]) -> str:
        inner = "".join(_row(label, key, m) for label, key in rows)
        return f"""<div class="metrics-col">
          <div class="metrics-col-header">{title}</div>
          <table class="metrics-table">{inner}</table>
        </div>"""

    return (
        _build_col("收益指标", col1_rows)
        + _build_col("风险指标", col2_rows)
        + _build_col("交易统计", col3_rows)
    )


# ─────────────────────── 导出工具 ────────────────────────────────


def ensure_dir(directory: str | Path) -> Path:
    """确保目录存在，不存在则递归创建，返回 Path 对象。"""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_json(
    metrics: dict[str, Any],
    strategy_name: str,
    output_dir: str | Path,
    extra: dict | None = None,
) -> Path:
    """
    将回测指标保存为 JSON 文件。

    文件命名：{output_dir}/{strategy_name}_{timestamp}.json

    Args:
        metrics:       BacktestEngine.run() 返回的指标字典
        strategy_name: 策略标识，用于文件名（如 "rsi"）
        output_dir:    输出目录
        extra:         附加信息（策略参数等），合并到 JSON 顶层

    Returns:
        保存的文件路径
    """
    out_path = ensure_dir(output_dir)
    filename = out_path / f"{strategy_name}_{_timestamp_str()}.json"

    payload = {"strategy": strategy_name, "generated_at": _timestamp_str()}
    if extra:
        payload.update(extra)
    payload["metrics"] = metrics

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    logger.info("回测报告已保存：%s", filename)
    return filename


def save_grid_csv(
    results: list[dict[str, Any]],
    strategy_name: str,
    output_dir: str | Path,
) -> Path:
    """
    将网格搜索结果（每行 = 一组参数 + 指标）保存为 CSV 文件。

    Args:
        results:       list of { **params, **metrics } 字典
        strategy_name: 策略标识
        output_dir:    输出目录

    Returns:
        保存的文件路径
    """
    if not results:
        raise ValueError("results 为空，无法生成 CSV")

    out_path = ensure_dir(output_dir)
    filename = out_path / f"{strategy_name}_grid_{_timestamp_str()}.csv"

    fieldnames = list(results[0].keys())
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    logger.info("网格搜索 CSV 已保存：%s（共 %d 行）", filename, len(results))
    return filename


def print_grid_table(
    results: list[dict[str, Any]],
    param_keys: list[str],
    metric_keys: list[str] | None = None,
) -> None:
    """
    在控制台打印参数网格搜索结果表格。

    Args:
        results:     list of { **params, **metrics }
        param_keys:  要展示的参数列名（显示在左侧）
        metric_keys: 要展示的指标列名（显示在右侧）；
                     默认展示 total_return_pct / sharpe_ratio /
                              max_drawdown_pct / total_trades
    """
    if metric_keys is None:
        metric_keys = [
            "total_return_pct", "sharpe_ratio",
            "max_drawdown_pct", "total_trades",
        ]

    col_widths = {k: max(len(k), 8) for k in param_keys + metric_keys}

    # 表头
    header = "  " + "  ".join(f"{k:>{col_widths[k]}}" for k in param_keys + metric_keys)
    print(header)
    print("  " + "-" * (len(header) - 2))

    for row in results:
        cells = []
        for k in param_keys:
            v = row.get(k, "")
            cells.append(f"{v:>{col_widths[k]}}")
        for k in metric_keys:
            v = row.get(k, 0)
            if isinstance(v, float):
                cells.append(f"{v:>{col_widths[k]}.3f}")
            else:
                cells.append(f"{v:>{col_widths[k]}}")
        print("  " + "  ".join(cells))


def print_best_summary(best_row: dict[str, Any], engine) -> None:
    """打印最优参数的完整绩效摘要。"""
    param_str = "  ".join(f"{k}={v}" for k, v in best_row.items()
                           if k not in ("total_return_pct", "sharpe_ratio",
                                        "max_drawdown_pct", "total_trades"))
    print(f"\n最优参数: {param_str}\n")
    print(engine.performance.summary())
