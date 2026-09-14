import type { PlanItem } from './api';

export type StepKind = 'intent' | 'plan' | 'subtask' | 'report';
export type StepStatus = 'pending' | 'running' | 'done' | 'error';

/** 执行时间线上的一个步骤（ChatPage 里由 SSE 事件驱动更新） */
export interface TraceStep {
  id: string;
  kind: StepKind;
  label: string;
  status: StepStatus;
  startedAt?: number;   // 进入 running 的时间戳，done 时用它算耗时
  costMs?: number;      // 步骤耗时（done/error 时填充）
  plan?: PlanItem[];    // plan 步骤携带：拆出的子任务列表
  toolHint?: string;    // subtask 步骤携带：计划里提示用什么工具
  result?: string;      // subtask 步骤携带：该子任务的执行结果摘要
}

/** 耗时格式化：<1s 显示 ms，≥1s 显示 x.xs（等宽字体保证竖排对齐） */
function fmtMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** 状态字形：running ◐（闪烁）/ done ✓ / error ✗ / pending ·（Codex 的 activity 就是这种极简记号） */
function glyph(status: StepStatus) {
  if (status === 'done') return { ch: '✓', color: 'var(--ok)' };
  if (status === 'error') return { ch: '✗', color: 'var(--err)' };
  if (status === 'running') return { ch: '◐', color: 'var(--text)' };
  return { ch: '·', color: 'var(--text-3)' };
}

/** 单行步骤：状态字形 + 标签 + 耗时，详情（计划/工具/结果）折在下面 */
function TraceRow({ s }: { s: TraceStep }) {
  const g = glyph(s.status);
  return (
    <div className={`trace-row ${s.status}`}>
      <div
        className="trace-node"
        style={{ color: g.color, animation: s.status === 'running' ? 'blink 1.2s steps(2) infinite' : undefined }}
      >
        {g.ch}
      </div>
      <div className="trace-main">
        <div className="trace-line">
          <span className="trace-label">{s.label}</span>
          {s.costMs != null && <span className="trace-cost">{fmtMs(s.costMs)}</span>}
        </div>
        {/* plan 步骤：把拆出来的子任务和它们的工具提示摊开 */}
        {s.kind === 'plan' && s.plan && (
          <ul className="act-list">
            {s.plan.map((p, i) => (
              <li key={p.id}>
                <span className="idx">{i + 1}</span>
                <span>{p.task}</span>
                {p.tool_hint && <span className="hint">{p.tool_hint}</span>}
              </li>
            ))}
          </ul>
        )}
        {/* subtask 步骤：工具名 + 结果摘要（结果折叠，避免长文本淹没时间线） */}
        {s.kind === 'subtask' && (s.toolHint || s.result) && (
          <div className="trace-sub">
            {s.toolHint && <div className="k">{s.toolHint}</div>}
            {s.result && (
              <details className="act-out">
                <summary>查看结果</summary>
                <pre>{s.result}</pre>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** 执行时间线：一次提问的 Agent 执行过程（意图 → 计划 → 子任务 → 报告） */
export default function TracePanel({ steps }: { steps: TraceStep[] }) {
  if (steps.length === 0) return null;
  const done = steps.filter(s => s.status === 'done').length;
  return (
    <div className="trace">
      <div className="trace-head">
        <span>AGENT ACTIVITY</span>
        <span>{done}/{steps.length}</span>
      </div>
      <div className="trace-body">
        {steps.map(s => <TraceRow key={s.id} s={s} />)}
      </div>
    </div>
  );
}
