import { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Statistic, Row, Col, Button, Spin,
} from 'antd';
import {
  ShoppingCartOutlined, WarningOutlined, ReloadOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { fetchDashboard, fetchDashboardInsight, type DashboardData } from './api';
import { Area, Bar, Column } from '@ant-design/plots';
import ReactMarkdown from 'react-markdown';

const { Title, Text } = Typography;

// markdown 洞察渲染：深色主题适配，列表项紧凑、行高放宽
const mdComponents = {
  p: ({ node: _n, ...props }: any) => <p style={{ margin: '4px 0' }} {...props} />,
  ul: ({ node: _n, ...props }: any) => <ul style={{ margin: '8px 0', paddingLeft: 20 }} {...props} />,
  li: ({ node: _n, ...props }: any) => <li style={{ margin: '6px 0', lineHeight: 1.8 }} {...props} />,
  strong: ({ node: _n, ...props }: any) => <strong style={{ color: C.text }} {...props} />,
};

/** 机器人图标（PostHog 珊瑚橙渐变 SVG，替代 emoji） */
function RobotIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <defs>
        <linearGradient id="dash-robot-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ff8a4d" />
          <stop offset="100%" stopColor="#F54E00" />
        </linearGradient>
      </defs>
      <rect x="4" y="8" width="16" height="10" rx="3" stroke="url(#dash-robot-grad)" strokeWidth="1.5" />
      <circle cx="9" cy="13" r="1.2" fill="#ff8a4d" />
      <circle cx="15" cy="13" r="1.2" fill="#ff8a4d" />
      <path d="M12 8 V5 M12 5 C10.5 5 9.5 4 9.5 2.5" stroke="url(#dash-robot-grad)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M12 8 V5 M12 5 C13.5 5 14.5 4 14.5 2.5" stroke="url(#dash-robot-grad)" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

// PostHog 视觉语言 → 局部样式常量（与 App.tsx 主题一致）
const C = {
  bg: '#15131c',
  surface: '#1e1a28',
  surface2: '#262233',
  border: '#332d45',
  borderSoft: '#2a2539',
  accent: '#F54E00',
  violet: '#8B7CF6',
  text: '#ece9f2',
  textSec: '#a6a0b8',
  textWeak: '#7a748c',
  mono: "'JetBrains Mono','Source Code Pro',Consolas,monospace",
};

/** 数据看板页：统计卡 + 三张图表（近7日趋势 / 分类销售 / 会员对比） */
export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);

  const load = useCallback(async () => {
    try {
      setDashboard(await fetchDashboard());
    } catch {
      // 静默：Spin 常驻说明后端没起来
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
  const axisText = { fill: C.textSec, fontSize: 11 };
  const yuan = (v: string) => `¥${Number(v).toLocaleString()}`;
  // 图表 tooltip：深色浮层，文字用浅色（文字不穿数据色）
  const tip = { background: C.surface2, border: `1px solid ${C.border}`, color: C.text, boxShadow: '0 4px 16px rgba(0,0,0,0.4)' };

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '24px 28px', background: C.bg }}>
      {/* 页头：标题 + 刷新 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
        <Title level={4} style={{ margin: 0, color: C.text, fontSize: 20 }}>数据看板</Title>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>

      {!dashboard ? (
        <div style={{ textAlign: 'center', marginTop: 80 }}><Spin /></div>
      ) : (
        <>
          {/* ============ 顶部统计卡（数字用等宽字体） ============ */}
          <Row gutter={14} style={{ marginBottom: 14 }}>
            <Col span={6}>
              <Card size="small" style={{ background: C.surface, borderColor: C.borderSoft }}
                styles={{ body: { padding: '16px 20px' } }}>
                <Statistic
                  title={<span style={{ fontSize: 12, color: C.textSec }}>今日销售额</span>}
                  value={dashboard["今日销售额(元)"]} precision={2} prefix="¥" suffix="元"
                  valueStyle={{ fontFamily: C.mono, fontSize: 22, color: C.text }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small" style={{ background: C.surface, borderColor: C.borderSoft }}
                styles={{ body: { padding: '16px 20px' } }}>
                <Statistic
                  title={<span style={{ fontSize: 12, color: C.textSec }}>今日订单数</span>}
                  value={dashboard["今日订单数"]}
                  prefix={<ShoppingCartOutlined style={{ color: C.textSec }} />} suffix="单"
                  valueStyle={{ fontFamily: C.mono, fontSize: 22, color: C.text }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small" style={{ background: C.surface, borderColor: dashboard["库存预警数"] > 0 ? '#ff5a5f' : C.borderSoft }}
                styles={{ body: { padding: '16px 20px' } }}>
                <Statistic
                  title={<span style={{ fontSize: 12, color: C.textSec }}>库存预警</span>}
                  value={dashboard["库存预警数"]}
                  prefix={<WarningOutlined style={{ color: dashboard["库存预警数"] > 0 ? '#ff5a5f' : '#7fd15c' }} />}
                  suffix="个商品"
                  valueStyle={{ fontFamily: C.mono, fontSize: 22, color: dashboard["库存预警数"] > 0 ? '#ff5a5f' : C.text }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small" style={{ background: C.surface, borderColor: C.borderSoft }}
                styles={{ body: { padding: '16px 20px' } }}>
                <Statistic
                  title={<span style={{ fontSize: 12, color: C.textSec }}>VIP 人均消费</span>}
                  value={dashboard["会员消费"]?.["vip"]?.["人均消费(元)"] ?? 0}
                  precision={2} prefix="¥" suffix="元"
                  valueStyle={{ fontFamily: C.mono, fontSize: 22, color: C.text }} />
              </Card>
            </Col>
          </Row>

          {/* ============ 近7日销售趋势（单序列面积图，无需图例） ============ */}
          <Card size="small" title={<span style={{ fontSize: 13, color: C.text }}>近7日销售额趋势</span>}
            style={{ marginBottom: 14, background: C.surface, borderColor: C.borderSoft }}
            styles={{ body: { padding: '8px 14px' } }}>
            <Area
              data={trendData}
              xField="date"
              yField="amount"
              height={190}
              style={{ fill: 'l(270) 0:#F54E0022 1:#F54E0000', stroke: C.accent, lineWidth: 2 }}
              axis={{
                x: { label: axisText, tick: false, line: false },
                y: { label: axisText, grid: { line: { stroke: C.borderSoft, lineWidth: 1 } } },
              }}
              tooltip={{ style: tip, items: [{ channel: 'y', name: '销售额(元)' }] }}
            />
          </Card>

          {/* ============ 分类销售 + 会员消费对比 ============ */}
          <Row gutter={14}>
            <Col span={12}>
              <Card size="small" title={<span style={{ fontSize: 13, color: C.text }}>分类销售额</span>}
                style={{ background: C.surface, borderColor: C.borderSoft }}
                styles={{ body: { padding: '8px 14px' } }}>
                <Bar
                  data={catData}
                  xField="amount"
                  yField="category"
                  height={Math.max(catData.length * 30, 160)}
                  style={{ fill: C.accent, radiusTopRight: 4, radiusBottomRight: 4, maxWidth: 24 }}
                  label={{ text: 'amount', position: 'right', style: { fill: C.textSec, fontSize: 11 } }}
                  axis={{
                    y: { label: { fill: C.textSec, fontSize: 12 }, tick: false, line: false },
                    x: { label: { ...axisText, formatter: yuan }, grid: { line: { stroke: C.borderSoft, lineWidth: 1 } } },
                  }}
                  tooltip={{ style: tip }}
                />
              </Card>
            </Col>
            <Col span={12}>
              <Card size="small" title={<span style={{ fontSize: 13, color: C.text }}>会员消费对比</span>}
                style={{ background: C.surface, borderColor: C.borderSoft }}
                styles={{ body: { padding: '8px 14px' } }}>
                <Column
                  data={memData}
                  xField="level"
                  yField="value"
                  colorField="metric"
                  height={240}
                  style={{ radiusTopLeft: 4, radiusTopRight: 4, maxWidth: 26 }}
                  scale={{ color: { range: [C.accent, C.violet] } }}
                  legend={{ color: { title: false, itemLabelFill: C.textSec, itemLabelFontSize: 11 } }}
                  axis={{
                    x: { label: { fill: C.textSec, fontSize: 12 }, tick: false, line: false },
                    y: { label: { ...axisText, formatter: yuan }, grid: { line: { stroke: C.borderSoft, lineWidth: 1 } } },
                  }}
                  tooltip={{ style: tip }}
                />
              </Card>
            </Col>
          </Row>

          {/* ============ AI 经营洞察：看板会说话（差异化核心，复用分析 Agent） ============ */}
          <Card
            size="small"
            title={
              <span style={{ fontSize: 13, color: C.text, display: 'flex', alignItems: 'center', gap: 6 }}>
                <RobotIcon /> AI 经营洞察
              </span>
            }
            extra={
              <Button
                size="small" type="primary" icon={<ThunderboltOutlined />}
                loading={insightLoading} onClick={() => generateInsight(true)}
                style={{ fontWeight: 600 }}
              >
                {insight ? '重新生成' : '生成洞察'}
              </Button>
            }
            style={{
              marginTop: 14, background: C.surface, borderColor: C.borderSoft,
              borderLeft: `3px solid ${C.accent}`,
            }}
            styles={{ body: { padding: '12px 16px' } }}
          >
            {insightError && (
              <Text style={{ color: '#ff5a5f', fontSize: 13 }}>洞察生成失败：{insightError}</Text>
            )}
            {!insight && !insightError && (
              insightLoading ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: C.textSec, fontSize: 13 }}>
                  <Spin size="small" /> 分析 Agent 正在解读经营数据…
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: C.textSec, fontSize: 13 }}>
                  <ThunderboltOutlined style={{ color: C.accent }} />
                  把当前经营数据交给分析 Agent，自动解读涨跌异常、库存风险，并给出运营建议。
                </div>
              )
            )}
            {insight && (
              <div style={{ color: C.text }}>
                <ReactMarkdown components={mdComponents}>{insight.text}</ReactMarkdown>
                <Text style={{ fontSize: 11, color: C.textWeak, fontFamily: C.mono }}>
                  生成耗时 {insight.costMs}ms{insight.fromCache ? ' · 当天缓存' : ''}
                </Text>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
