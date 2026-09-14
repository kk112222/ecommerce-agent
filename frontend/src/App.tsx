import { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, message } from 'antd';
import { MessageOutlined, BarChartOutlined, LogoutOutlined } from '@ant-design/icons';
import ChatPage from './ChatPage';
import DashboardPage from './DashboardPage';
import LoginPage from './LoginPage';
import SessionSidebar from './SessionSidebar';
import { isLoggedIn, clearToken } from './auth';
import { fetchSessions, renameSession, deleteSession, type SessionInfo } from './api';
import { codexTheme } from './theme';

type View = 'chat' | 'dashboard';

/** 登录后的主布局：左侧图标栏 + 会话列表列 + 右侧内容区（Codex 式的三段式外壳） */
function AppLayout({ onLogout }: { onLogout: () => void }) {
  const [view, setView] = useState<View>('chat');
  // 多会话状态：列表 / 当前会话 / 重挂载键
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeSessionId, setActiveSessionId] = useState('');  // '' = 未开始的新对话
  const [chatKey, setChatKey] = useState(0);                   // 新建/切换会话时自增，强制 ChatPage 重挂载

  const refreshSessions = useCallback(async () => {
    try { setSessions(await fetchSessions()); } catch { /* 后端未启动，静默 */ }
  }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  /** 新建对话：切到空会话，等 ChatPage 发消息后由 onSessionIdChange 带回真实 sid */
  function newChat() {
    setActiveSessionId('');
    setChatKey(k => k + 1);
  }
  /** 点击侧边栏某会话：切到它并重挂载 ChatPage（挂载时拉历史） */
  function selectSession(sid: string) {
    setActiveSessionId(sid);
    setChatKey(k => k + 1);
  }
  /** ChatPage 里会话确定/变化时回调：只更新高亮 + 刷新列表，不重置 chatKey（否则会清掉正在展示的对话） */
  function handleSessionIdChange(sid: string) {
    setActiveSessionId(sid);
    refreshSessions();
  }
  async function handleDeleteSession(sid: string) {
    try {
      await deleteSession(sid);
    } catch {
      message.error('删除会话失败');
      return;
    }
    if (sid === activeSessionId) newChat();   // 删的是当前会话 → 回到新对话
    refreshSessions();
  }
  /** 重命名会话：成功后刷新列表（标题会变） */
  async function handleRenameSession(sid: string, title: string) {
    try {
      await renameSession(sid, title);
      refreshSessions();
    } catch {
      message.error('重命名失败');
    }
  }

  function handleLogout() {
    clearToken();
    onLogout();
  }

  return (
    <div className="app">
      {/* 左侧图标栏：只有图标，标题留给自己看（Codex 的 rail 就是窄窄一条） */}
      <nav className="rail">
        <div className="rail-logo" title="掌柜 · 电商运营 AI 助手">掌</div>
        <div
          className={`rail-btn ${view === 'chat' ? 'active' : ''}`}
          title="智能问答" onClick={() => setView('chat')}
        >
          <MessageOutlined />
        </div>
        <div
          className={`rail-btn ${view === 'dashboard' ? 'active' : ''}`}
          title="数据看板" onClick={() => setView('dashboard')}
        >
          <BarChartOutlined />
        </div>
        <div className="rail-spacer" />
        <div className="rail-btn" title="退出登录" onClick={handleLogout}>
          <LogoutOutlined />
        </div>
      </nav>

      {/* 会话列表：只在聊天视图显示（看板不需要） */}
      {view === 'chat' && (
        <SessionSidebar
          sessions={sessions}
          activeId={activeSessionId}
          onSelect={selectSession}
          onNew={newChat}
          onRename={handleRenameSession}
          onDelete={handleDeleteSession}
        />
      )}

      <main className="content">
        {view === 'chat' ? (
          <ChatPage
            key={chatKey}
            initialSessionId={activeSessionId}
            onSessionIdChange={handleSessionIdChange}
          />
        ) : (
          <DashboardPage />
        )}
      </main>
    </div>
  );
}

function App() {
  const [authed, setAuthed] = useState(isLoggedIn());

  return (
    <ConfigProvider theme={codexTheme}>
      {authed ? (
        <AppLayout onLogout={() => setAuthed(false)} />
      ) : (
        <LoginPage onSuccess={() => setAuthed(true)} />
      )}
    </ConfigProvider>
  );
}

export default App;
