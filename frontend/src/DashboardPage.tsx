import { useState, useEffect, useCallback } from 'react';
import { fetchDashboard, fetchDashboardInsight, type DashboardData } from './api';
import { Area, Bar, Column } from '@ant-design/plots';
import ReactMarkdown from 'react-markdown';

// 图表配色：数据用一支冷色（info），网格/坐标轴用中性灰 —— 和全局令牌同一套语言
const V = {
  mark: '#58a6ff',
  mark2: '#a1a1a1',
  grid: '#232323',
  label: '#a1a1a1',
  tipBg: '#151515',
  tipBorder: '#232323',
  text: '#ededed',
};

/** 数据看板页：统计卡 + 三张图表（近7日趋势 / 分类销售 / 会员对比）+ AI 经营洞察 */
export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);

  const load = useCallback(async () => {
    try {
      setDashboard(await fetchDashboard());
    } catch {
      // 静默：一直显示"加载中"说明后端没起来
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ============ AI 经营洞察（看板底部：打开自动加载，命中当天缓存秒开） ============
  const [insight, setInsight] = useState<{ text: string; costMs: number; fromCache?: boolean } | null>(null);
  const [insightLoading, setInsightLoading] = useState(false);
  const [insightError, setInsightError] = useState<string | null>(null);

  /** refresh=false 走当天缓存（命中秒开）；true 强制重新生成 */
  const generateInsight = useCallback(async (refresh = false) => {
    setInsightLoading(true);
    setInsightError(null);
    try {
      const r = await fetchDashboardInsight(refresh);
      if (r.error) {                       // 后端 LLM 调用失败：提示，不崩页面
        setInsightError(r.error);
        setInsight(null);
      } else {
        setInsight({ text: r.insight, costMs: r.cost_ms, fromCache: r.from_cache });
      }
    } catch (e) {
      setInsightError(e instanceof Error ? e.message : '生成失败');
      setInsight(null);
    } finally {
      setInsightLoading(false);
    }
  }, []);

  // 打开看板自动加载一次：命中当天缓存秒开，未命中自动生成（首次略等）
  useEffect(() => { generateInsight(false); }, [generateInsight]);

  // ============ 看板数据 → 图表数据 ============
  const trendData = dashboard?.["近7日趋势"] ?? [];
  const catData = Object.entries(dashboard?.["分类销售"] ?? {}).map(([category, amount]) => ({ category, amount }));
  const memData = Object.entries(dashboard?.["会员消费"] ?? {}).flatMap(([level, s]) => [
    { level: level.toUpperCase(), metric: '总消费', value: s["总消费(元)"] },
    { level: level.toUpperCase(), metric: '人均消费', value: s["人均消费(元)"] },
  ]);
  const axisText = { fill: V.label, fontSize: 11 };
  const yuan = (v: string) => `¥${Number(v).toLocaleString()}`;
  const tip = { background: V.tipBg, border: `1px solid ${V.tipBorder}`, color: V.text };

  const lowStock = dashboard?.["库存预警数"] ?? 0;

  return (
    <div className="dash">
      <div className="dash-inner">
        {/* 页头 */}
        <div className="dash-head">
          <span className="dash-title">数据看板</span>
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={load}>↻ 刷新</button>
        </div>

        {!dashboard ? (
          <div className="dash-loading">加载中…</div>
        ) : (
          <>
            {/* 统计卡：数字一律等宽，方便竖着比对 */}
            <div className="stat-grid">
              <div className="stat">
                <div className="stat-label">今日销售额</div>
                <div className="stat-value">
                  ¥{dashboard["今日销售额(元)"].toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  <span className="unit">元</span>
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">今日订单数</div>
                <div className="stat-value">
                  {dashboard["今日订单数"]}<span className="unit">单</span>
                </div>
              </div>
              <div className={`stat ${lowStock > 0 ? 'warn' : ''}`}>
                <div className="stat-label">库存预警</div>
                <div className="stat-value">
                  {lowStock}<span className="unit">个商品</span>
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">VIP 人均消费</div>
                <div className="stat-value">
                  ¥{(dashboard["会员消费"]?.["vip"]?.["人均消费(元)"] ?? 0)
                    .toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  <span className="unit">元</span>
                </div>
              </div>
            </div>

            {/* 近 7 日趋势 */}
            <div className="panel">
              <div className="panel-head"><span>近 7 日销售额趋势</span><span className="k">AREA</span></div>
              <div className="panel-body">
                <Area
                  data={trendData}
                  xField="date"
                  yField="amount"
                  height={190}
                  style={{ fill: 'l(270) 0:#58a6ff22 1:#58a6ff00', stroke: V.mark, lineWidth: 1.6 }}
                  axis={{
                    x: { label: axisText, tick: false, line: false },
                    y: { label: axisText, grid: { line: { stroke: V.grid, lineWidth: 1 } } },
                  }}
                  tooltip={{ style: tip, items: [{ channel: 'y', name: '销售额(元)' }] }}
                />
              </div>
            </div>

            {/* 分类销售 + 会员消费对比 */}
            <div className="grid2">
              <div className="panel">
                <div className="panel-head"><span>分类销售额</span><span className="k">BAR</span></div>
                <div className="panel-body">
                  <Bar
                    data={catData}
                    xField="amount"
                    yField="category"
                    height={Math.max(catData.length * 30, 160)}
                    style={{ fill: V.mark, radiusTopRight: 3, radiusBottomRight: 3, maxWidth: 22 }}
                    label={{ text: 'amount', position: 'right', style: { fill: V.label, fontSize: 11 } }}
                    axis={{
                      y: { label: { fill: V.label, fontSize: 12 }, tick: false, line: false },
                      x: { label: { ...axisText, formatter: yuan }, grid: { line: { stroke: V.grid, lineWidth: 1 } } },
                    }}
                    tooltip={{ style: tip }}
                  />
                </div>
              </div>
              <div className="panel">
                <div className="panel-head"><span>会员消费对比</span><span className="k">COLUMN</span></div>
                <div className="panel-body">
                  <Column
                    data={memData}
                    xField="level"
                    yField="value"
                    colorField="metric"
                    height={240}
                    style={{ radiusTopLeft: 3, radiusTopRight: 3, maxWidth: 24 }}
                    scale={{ color: { range: [V.mark, V.mark2] } }}
                    legend={{ color: { title: false, itemLabelFill: V.label, itemLabelFontSize: 11 } }}
                    axis={{
                      x: { label: { fill: V.label, fontSize: 12 }, tick: false, line: false },
                      y: { label: { ...axisText, formatter: yuan }, grid: { line: { stroke: V.grid, lineWidth: 1 } } },
                    }}
                    tooltip={{ style: tip }}
                  />
                </div>
              </div>
            </div>

            {/* AI 经营洞察：看板会说话（差异化核心，复用分析 Agent） */}
            <div className="panel insight">
              <div className="panel-head">
                <span>AI 经营洞察</span>
                <button
                  className="btn primary"
                  onClick={() => generateInsight(true)}
                  disabled={insightLoading}
                >
                  {insightLoading ? '生成中…' : insight ? '重新生成' : '生成洞察'}
                </button>
              </div>
              <div className="panel-body">
                {insightError && (
                  <div style={{ color: 'var(--err)', fontSize: 12.5 }}>洞察生成失败：{insightError}</div>
                )}
                {!insight && !insightError && (
                  <div className="viz-empty">
                    {insightLoading
                      ? '◐ 分析 Agent 正在解读经营数据…'
                      : '把当前经营数据交给分析 Agent，自动解读涨跌异常、库存风险，并给出运营建议。'}
                  </div>
                )}
                {insight && (
                  <>
                    <div className="prose">
                      <ReactMarkdown>{insight.text}</ReactMarkdown>
                    </div>
                    <div className="meta-line">
                      生成耗时 {insight.costMs}ms{insight.fromCache ? ' · 当天缓存' : ''}
                    </div>
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
