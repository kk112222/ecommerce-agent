// 聊天消息里的可视化块：解析 Synthesizer 附在报告末尾的 ```viz 代码块，渲染成真图表/表格。
// 协议：块内是一行 JSON，如 {"type":"line","title":"近7日销售额","data":[{"x":"08-28","y":12000}]}
// 类型：line=趋势面积 / column=纵向对比 / bar=横向对比 / table=明细表格
// 容错三层：块没闭合（流式中）→ 显示占位；JSON 坏 / type 不认识 → 只提示不崩，正文结论不受影响
import { Area, Column, Bar } from '@ant-design/plots';
import { Table, Typography, Spin } from 'antd';

const { Text } = Typography;

// PostHog 视觉常量（仅本模块用到的色）
const C = {
  text: '#ece9f2',
  textSec: '#a6a0b8',
  textWeak: '#7a748c',
  surface2: '#262233',
  border: '#332d45',
  accent: '#F54E00',
  mono: "'JetBrains Mono','Source Code Pro',Consolas,monospace",
};

// plots 图深色通用样式
const axis = { label: { fill: C.textSec, fontSize: 11 } };
const tip = { background: C.surface2, border: `1px solid ${C.border}`, color: C.text, boxShadow: '0 4px 16px rgba(0,0,0,0.4)' };

export interface ParsedMarkdown {
  body: string;            // 剔除 viz 块后的正文 markdown
  viz: string | null;      // viz 块内原始文本（未闭合时是残缺的，只有到最后 report 才是完整 JSON）
  incomplete: boolean;     // true = 块还没闭合（流式生成中），正文先渲染、图表占位
}

/** 把一段 markdown 里的 ```viz 块抽出来：正文里删掉块，块内容单独返回 */
export function parseViz(content: string): ParsedMarkdown {
  const idx = content.indexOf('```viz');
  if (idx === -1) return { body: content, viz: null, incomplete: false };
  const rest = content.slice(idx + 6);         // ```viz 之后的内容
  const close = rest.indexOf('```');           // 找闭合
  if (close === -1) return { body: content.slice(0, idx), viz: rest, incomplete: true };
  const raw = rest.slice(0, close).trim();
  const tail = rest.slice(close + 3);          // 块后的残留（协议要求块在末尾，理论为空）
  return { body: content.slice(0, idx) + tail, viz: raw, incomplete: false };
}

interface VizBlockProps {
  raw: string;           // viz 块内原始文本
  incomplete: boolean;   // true = 还在流式生成，仅占位
}

/** 渲染一个 viz 块（图表或表格），解析失败只给一行提示，不崩页面 */
export default function VizBlock({ raw, incomplete }: VizBlockProps) {
  if (incomplete) {
    return (
      <div style={{ marginTop: 10, color: C.textSec, fontSize: 13 }}>
        <Spin size="small" /> 正在生成可视化…
      </div>
    );
  }

  let cfg: { type?: string; title?: string; data?: unknown } = {};
  try {
    cfg = JSON.parse(raw);
  } catch {
    return <Text style={{ fontSize: 12, color: C.textWeak, fontFamily: C.mono }}>〔可视化数据解析失败，以上方文字结论为准〕</Text>;
  }
  if (!Array.isArray(cfg.data) || !cfg.type) {
    return <Text style={{ fontSize: 12, color: C.textWeak, fontFamily: C.mono }}>〔可视化数据格式异常，以上方文字结论为准〕</Text>;
  }

  // 数据行规范成 {x,y}，供 plots 用
  const rows = cfg.data.map(d => ({ x: String((d as any).x ?? ''), y: Number((d as any).y) }));

  let chart: React.ReactNode = null;
  switch (cfg.type) {
    case 'line':   // 趋势 → 面积图（同看板近7日图）
      chart = (
        <Area data={rows} xField="x" yField="y" height={210}
          style={{ fill: 'l(270) 0:#F54E0022 1:#F54E0000', stroke: C.accent, lineWidth: 2 }}
          axis={{ x: { label: axis.label, tick: false, line: false }, y: { label: axis.label, grid: { line: { stroke: C.border, lineWidth: 1 } } } }}
          tooltip={{ style: tip, items: [{ channel: 'y', name: '数值' }] }} />
      );
      break;
    case 'column': // 分类并排 → 纵向柱状
      chart = (
        <Column data={rows} xField="x" yField="y" height={210}
          style={{ fill: C.accent, radiusTopLeft: 4, radiusTopRight: 4, maxWidth: 34 }}
          axis={{ x: { label: axis.label, tick: false, line: false }, y: { label: axis.label, grid: { line: { stroke: C.border, lineWidth: 1 } } } }}
          tooltip={{ style: tip }} />
      );
      break;
    case 'bar':    // 横向对比 → {x}=类目放 y 轴，{y}=数值放 x 轴
      chart = (
        <Bar data={rows} xField="y" yField="x" height={Math.max(rows.length * 34, 140)}
          style={{ fill: C.accent, radiusTopRight: 4, radiusBottomRight: 4, maxWidth: 26 }}
          label={{ text: 'y', position: 'right', style: { fill: C.textSec, fontSize: 11 } }}
          axis={{
            x: { label: axis.label, grid: { line: { stroke: C.border, lineWidth: 1 } } },
            y: { label: axis.label, tick: false, line: false },
          }}
          tooltip={{ style: tip }} />
      );
      break;
    case 'table':  // 明细 → antd 表格
      const first = cfg.data[0] as Record<string, unknown>;
      const cols = Object.keys(first ?? {}).map(k => ({ title: k, dataIndex: k, key: k }));
      chart = (
        <Table size="small" columns={cols} dataSource={(cfg.data as object[]).map((r, i) => ({ ...r, key: i }))}
          pagination={false}
          style={{ background: 'transparent' }}
          locale={{ emptyText: <span style={{ color: C.textWeak }}>无数据</span> }} />
      );
      break;
    default:
      return <Text style={{ fontSize: 12, color: C.textWeak, fontFamily: C.mono }}>〔暂不支持的可视化类型：{cfg.type}〕</Text>;
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ marginBottom: 6 }}>
        <Text strong style={{ color: C.text, fontSize: 13 }}>{cfg.title ?? '数据可视化'}</Text>
      </div>
      {chart}
    </div>
  );
}
