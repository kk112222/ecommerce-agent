import { useState } from 'react';
import { Modal } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import type { SessionInfo } from './api';

/** ISO 时间 → "MM-DD HH:mm"（侧边栏显示最近活跃时间，等宽字体保证对齐） */
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

/** 单个会话项：点击切换，hover 出编辑/删除，双击标题直接重命名 */
function SessionItem({ s, active, onSelect, onRename, onDelete }: {
  s: SessionInfo; active: boolean;
  onSelect: () => void; onRename: Props['onRename']; onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

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

  if (editing) {
    return (
      <div style={{ padding: '4px 6px' }}>
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') setEditing(false);
          }}
          onBlur={commit}
          style={{
            width: '100%', padding: '4px 6px', borderRadius: 4,
            background: 'var(--elev)', border: '1px solid var(--text-3)',
            color: 'var(--text)', fontSize: 12.5, outline: 'none', fontFamily: 'inherit',
          }}
        />
      </div>
    );
  }

  return (
    <div
      className={`session-item ${active ? 'active' : ''}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onSelect}
      onDoubleClick={startEdit}
    >
      <div className="st">
        <div className="st-title">{s.title}</div>
        <div className="st-meta">
          {formatTime(s.updated_at)}{s.msg_count ? ` · ${s.msg_count} 条` : ''}
        </div>
      </div>
      {hover && (
        <span className="st-ops">
          <EditOutlined
            className="session-ops-ico" title="重命名"
            onClick={(e) => { e.stopPropagation(); startEdit(); }}
          />
          <DeleteOutlined
            className="session-ops-ico danger" title="删除会话"
            onClick={(e) => { e.stopPropagation(); confirmDelete(); }}
          />
        </span>
      )}
    </div>
  );
}

/** 会话侧边栏：新建入口 + 会话列表（聊天视图左侧那一列） */
export default function SessionSidebar({ sessions, activeId, onSelect, onNew, onRename, onDelete }: Props) {
  return (
    <aside className="sessions">
      <div className="sessions-head">
        <span className="sessions-title">会话</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>{sessions.length}</span>
      </div>
      <div className="sessions-new" onClick={onNew}>
        <PlusOutlined style={{ fontSize: 11 }} /> 新建对话
      </div>
      <div className="sessions-list">
        {sessions.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-3)', textAlign: 'center', marginTop: 24 }}>
            暂无历史会话
          </div>
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
    </aside>
  );
}
