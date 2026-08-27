import { Spin, Typography } from 'antd';
import { CloseOutlined, ToolOutlined } from '@ant-design/icons';
import type { PlanItem } from './api';

const { Text } = Typography;

/** 闪电图标（PostHog 珊瑚橙渐变 SVG，替代 emoji） */
function BoltIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <defs>
        <linearGradient id="trace-bolt-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ff8a4d" />
          <stop offset="100%" stopColor="#F54E00" />
        </linearGradient>
      </defs>
      <path d="M13 2 L4.5 13.5 H10.5 L9 22 L19.5 10 H13.5 Z" fill="url(#trace-bolt-grad)" />
    </svg>
  );
}

// PostHog 深色视觉：执行时间线的颜色常量
const C = {
  surface: '#1e1a28',
  border: '#332d45',
  borderSoft: '#2a2539',
  accent: '#F54E00',
  text: '#ece9f2',
  textSec: '#a6a0b8',
  textWeak: '#7a748c',
  ok: '#7fd15c',
  err: '#ff5a5f',
  mono: "'JetBrains Mono','Source Code Pro',Consolas,monospace",
};

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

/** 耗时格式化：<1s 显示 ms，≥1s 显示 x.xs */
function fmtMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** 单行步骤：左侧状态节点 + 标签 + 右侧耗时 + 详情 */
function TraceRow({ s }: { s: TraceStep }) {
  const node = s.status === 'running' ? (
    <Spin size="small" />
  ) : s.status === 'done' ? (
    <span style={{
      width: 10, height: 10, borderRadius: '50%', background: C.ok,
      display: 'inline-block',
    }} />
  ) : s.status === 'error' ? (
    <CloseOutlined style={{ color: C.err, fontSize: 12 }} />
  ) : (
    <span style={{ width: 10, height: 10, borderRadius: '50%', background: C.textWeak, display: 'inline-block' }} />
  );

  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <div style={{ width: 20, height: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        {node}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
          <Text style={{
            fontSize: 13, fontWeight: 500,
            color: s.status === 'running' ? C.text : s.status === 'done' ? C.textSec : s.status === 'error' ? C.err : C.textWeak,
          }}>{s.label}</Text>
          {s.costMs != null && (
            <Text style={{ fontSize: 11, color: C.textWeak, fontFamily: C.mono, flexShrink: 0 }}>{fmtMs(s.costMs)}</Text>
          )}
        </div>
        {/* plan 步骤：展开子任务列表 */}
        {s.kind === 'plan' && s.plan && (
          <div style={{ marginTop: 4, paddingLeft: 2 }}>
            {s.plan.map((p, i) => (
              <div key={p.id} style={{ fontSize: 12, color: C.textSec, lineHeight: 1.7 }}>{i + 1}. {p.task}</div>
            ))}
          </div>
        )}
        {/* subtask 步骤：工具提示 + 结果摘要 */}
        {s.kind === 'subtask' && (s.toolHint || s.result) && (
          <div style={{ marginTop: 4, paddingLeft: 2 }}>
            {s.toolHint && (
              <Text style={{ fontSize: 11, color: C.textWeak, display: 'flex', alignItems: 'center', gap: 4 }}>
                <ToolOutlined style={{ fontSize: 11 }} /> {s.toolHint}
              </Text>
            )}
            {s.result && (
              <div style={{ fontSize: 12, color: C.textSec, whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
                {s.result}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** 执行时间线：一次提问的 Agent 执行过程（意图→计划→子任务→报告） */
export default function TracePanel({ steps }: { steps: TraceStep[] }) {
  if (steps.length === 0) return null;
  return (
    <div style={{
      margin: '4px auto 16px', width: '82%', borderRadius: 10,
      background: C.surface, border: `1px solid ${C.borderSoft}`,
      borderLeft: `3px solid ${C.accent}`, padding: '12px 14px 12px 18px',
    }}>
      <Text style={{ fontSize: 12, color: C.textWeak, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
        <BoltIcon /> 执行过程
      </Text>
      {/* 相对定位容器 + 绝对定位竖线（左 10px 处穿过节点圆心），形成时间线 */}
      <div style={{ position: 'relative', marginTop: 10, paddingLeft: 2 }}>
        {steps.length > 1 && (
          <div style={{
            position: 'absolute', left: 10, top: 10, bottom: 4, width: 2,
            background: C.borderSoft,
          }} />
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {steps.map(s => <TraceRow key={s.id} s={s} />)}
        </div>
      </div>
    </div>
  );
}
