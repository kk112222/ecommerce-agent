import { useState } from 'react';
import { Button, Typography } from 'antd';
import { PlusOutlined, MessageOutlined, DeleteOutlined } from '@ant-design/icons';
import type { SessionInfo } from './api';

const { Text } = Typography;

// PostHog 深色视觉：会话侧边栏的颜色常量
const C = {
  bg: '#1e1a28',
  border: '#2a2539',
  accent: '#F54E00',
  text: '#ece9f2',
  textWeak: '#7a748c',
};

/** ISO 时间 → "MM-DD HH:mm"（侧边栏显示最近活跃时间） */
function formatTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (!Number.isNaN(d.getTime())) {
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  return iso.slice(5, 16);
}

interface Props {
  sessions: SessionInfo[];
  activeId: string;            // 当前选中的会话 id（'' 表示未开始的新对话）
  onSelect: (sid: string) => void;
  onNew: () => void;
  onDelete: (sid: string) => void;
}

/** 单个会话项：点击切换，hover 显示删除按钮 */
function SessionItem({ s, active, onSelect, onDelete }: {
  s: SessionInfo; active: boolean; onSelect: () => void; onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onSelect}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 10px', borderRadius: 8, cursor: 'pointer', marginBottom: 2,
        background: active ? 'rgba(245,78,0,0.12)' : 'transparent',
        borderLeft: active ? `3px solid ${C.accent}` : '3px solid transparent',
      }}
    >
      <MessageOutlined style={{ fontSize: 13, color: active ? C.accent : C.textWeak, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, color: C.text, whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{s.title}</div>
        <div style={{ fontSize: 11, color: C.textWeak }}>{formatTime(s.updated_at)}</div>
      </div>
      <span
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title="删除会话"
        style={{
          color: '#e05b5b', fontSize: 13, cursor: 'pointer', flexShrink: 0,
          opacity: hover ? 1 : 0, transition: 'opacity .15s',
        }}
      >
        <DeleteOutlined />
      </span>
    </div>
  );
}

/** 会话侧边栏：新建按钮 + 会话列表（chat 视图左侧那列） */
export default function SessionSidebar({ sessions, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <div style={{
      width: 240, flexShrink: 0, background: C.bg, borderRight: `1px solid ${C.border}`,
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ padding: 12 }}>
        <Button block type="primary" icon={<PlusOutlined />} onClick={onNew}>新建对话</Button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        {sessions.length === 0 && (
          <Text style={{ fontSize: 12, color: C.textWeak, display: 'block', textAlign: 'center', marginTop: 24 }}>
            暂无历史会话
          </Text>
        )}
        {sessions.map(s => (
          <SessionItem
            key={s.id}
            s={s}
            active={s.id === activeId}
            onSelect={() => onSelect(s.id)}
            onDelete={() => onDelete(s.id)}
          />
        ))}
      </div>
    </div>
  );
}
