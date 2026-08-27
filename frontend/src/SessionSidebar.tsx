import { useState } from 'react';
import { Button, Typography, Input, Modal } from 'antd';
import { PlusOutlined, MessageOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons';
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
  onRename: (sid: string, title: string) => void;
  onDelete: (sid: string) => void;
}

/** 单个会话项：点击切换，hover 出编辑/删除按钮，双击标题直接重命名 */
function SessionItem({ s, active, onSelect, onRename, onDelete }: {
  s: SessionInfo; active: boolean;
  onSelect: () => void; onRename: Props['onRename']; onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  /** 进入编辑态，回填当前标题 */
  function startEdit() {
    setDraft(s.title);
    setEditing(true);
  }
  /** Enter / 失焦提交重命名；空标题或没变化就不提交 */
  async function commit() {
    const t = draft.trim();
    if (t && t !== s.title) await onRename(s.id, t);
    setEditing(false);
  }
  /** 删除前二次确认（物理删除不可恢复） */
  function confirmDelete() {
    Modal.confirm({
      title: '删除会话',
      content: `「${s.title}」及其全部消息将被永久删除，此操作不可恢复。`,
      okText: '彻底删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: onDelete,
    });
  }

  // 编辑态：输入框替换整行，Enter 提交 / Esc 取消 / 失焦提交
  if (editing) {
    return (
      <div style={{ padding: '6px 10px', marginBottom: 2 }}>
        <Input
          size="small"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onPressEnter={commit}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false); }}
          style={{ background: '#262233', borderColor: C.accent, color: C.text }}
        />
      </div>
    );
  }

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onSelect}
      onDoubleClick={startEdit}
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
      {/* hover 才显示的操作按钮 */}
      <span style={{ display: 'flex', gap: 6, flexShrink: 0, opacity: hover ? 1 : 0, transition: 'opacity .15s' }}>
        <EditOutlined
          onClick={(e) => { e.stopPropagation(); startEdit(); }}
          title="重命名"
          style={{ color: C.textWeak, fontSize: 13, cursor: 'pointer' }}
        />
        <DeleteOutlined
          onClick={(e) => { e.stopPropagation(); confirmDelete(); }}
          title="删除会话"
          style={{ color: '#e05b5b', fontSize: 13, cursor: 'pointer' }}
        />
      </span>
    </div>
  );
}

/** 会话侧边栏：新建按钮 + 会话列表（chat 视图左侧那列） */
export default function SessionSidebar({ sessions, activeId, onSelect, onNew, onRename, onDelete }: Props) {
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
            onRename={onRename}
            onDelete={() => onDelete(s.id)}
          />
        ))}
      </div>
    </div>
  );
}
