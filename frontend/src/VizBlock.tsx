// 聊天消息里的可视化块：解析 Synthesizer 附在报告末尾的 ```viz 代码块，渲染成真图表/表格。
// 协议：块内是一行 JSON，如 {"type":"line","title":"近7日销售额","data":[{"x":"08-28","y":12000}]}
// 类型：line=趋势面积 / column=纵向对比 / bar=横向对比 / table=明细表格
// 容错三层：块没闭合（流式中）→ 显示占位；JSON 坏 / type 不认识 → 只提示不崩，正文结论不受影响
// 视觉：与全局令牌同一套（近黑中性 + 一支冷色做数据色），样式在 index.css 的 .viz-* 里
import { Area, Column, Bar } from '@ant-design/plots';

const V = {
  mark: '#58a6ff',
  grid: '#232323',
  label: '#a1a1a1',
  tipBg: '#151515',
  tipBorder: '#232323',
  text: '#ededed',
};

const axis = { label: { fill: V.label, fontSize: 11 } };
const tip = { background: V.tipBg, border: `1px solid ${V.tipBorder}`, color: V.text };

export interface ParsedMarkdown {
  body: string;            // 剔除 viz 块后的正文 markdown
  viz: string | null;      // viz 块内原始文本（未闭合时是残缺的，只有到最后 report 才是完整 JSON）
  incomplete: boolean;     // true = 块还没闭合（流式生成中），正文先渲染、图表占位
}

/** 把一段 markdown 里的 viz 代码块抽出来：正文里删掉块，块内容单独返回 */
export function parseViz(content: string): ParsedMarkdown {
  const idx = content.indexOf('```viz');
  if (idx === -1) return { body: content, viz: null, incomplete: false };
  const rest = content.slice(idx + 6);         // viz 标记之后的内容
  const close = rest.indexOf('```');          // 找闭合
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
    return <div className="viz-empty">◐ 正在生成可视化…</div>;
  }

  let cfg: { type?: string; title?: string; data?: unknown } = {};
  try {
    cfg = JSON.parse(raw);
  } catch {
    return <div className="viz-empty">〔可视化数据解析失败，以上方文字结论为准〕</div>;
  }
  if (!Array.isArray(cfg.data) || !cfg.type) {
    return <div className="viz-empty">〔可视化数据格式异常，以上方文字结论为准〕</div>;
  }

  // 数据行规范成 {x,y}，供 plots 用
  const rows = cfg.data.map(d => ({ x: String((d as any).x ?? ''), y: Number((d as any).y) }));

  let chart: React.ReactNode = null;
  switch (cfg.type) {
    case 'line':   // 趋势 → 面积图（同看板近 7 日图）
      chart = (
        <Area data={rows} xField="x" yField="y" height={210}
          style={{ fill: 'l(270) 0:#58a6ff22 1:#58a6ff00', stroke: V.mark, lineWidth: 1.6 }}
          axis={{
            x: { label: axis.label, tick: false, line: false },
            y: { label: axis.label, grid: { line: { stroke: V.grid, lineWidth: 1 } } },
          }}
          tooltip={{ style: tip, items: [{ channel: 'y', name: '数值' }] }} />
      );
      break;
    case 'column': // 分类并排 → 纵向柱状
      chart = (
        <Column data={rows} xField="x" yField="y" height={210}
          style={{ fill: V.mark, radiusTopLeft: 3, radiusTopRight: 3, maxWidth: 32 }}
          axis={{
            x: { label: axis.label, tick: false, line: false },
            y: { label: axis.label, grid: { line: { stroke: V.grid, lineWidth: 1 } } },
          }}
          tooltip={{ style: tip }} />
      );
      break;
    case 'bar':    // 横向对比 → {x}=类目放 y 轴，{y}=数值放 x 轴
      chart = (
        <Bar data={rows} xField="y" yField="x" height={Math.max(rows.length * 34, 140)}
          style={{ fill: V.mark, radiusTopRight: 3, radiusBottomRight: 3, maxWidth: 22 }}
          label={{ text: 'y', position: 'right', style: { fill: V.label, fontSize: 11 } }}
          axis={{
            x: { label: axis.label, grid: { line: { stroke: V.grid, lineWidth: 1 } } },
            y: { label: axis.label, tick: false, line: false },
          }}
          tooltip={{ style: tip }} />
      );
      break;
    case 'table': { // 明细 → 朴素表格（不用 antd Table，省一层样式冲突）
      const cols = Object.keys((cfg.data[0] as Record<string, unknown>) ?? {});
      chart = (
        <table className="viz-table">
          <thead>
            <tr>{cols.map(c => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {(cfg.data as Record<string, unknown>[]).map((row, i) => (
              <tr key={i}>{cols.map(c => <td key={c}>{String(row[c] ?? '')}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
      break;
    }
    default:
      return <div className="viz-empty">〔暂不支持的可视化类型：{cfg.type}〕</div>;
  }

  return (
    <div className="viz">
      <div className="viz-title">{cfg.title ?? '数据可视化'}</div>
      {chart}
    </div>
  );
}
