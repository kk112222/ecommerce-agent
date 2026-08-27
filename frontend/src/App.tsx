import { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, theme, Layout, Menu, Button, Typography, message } from 'antd';
import { RobotOutlined, MessageOutlined, BarChartOutlined, LogoutOutlined } from '@ant-design/icons';
import ChatPage from './ChatPage';
import DashboardPage from './DashboardPage';
import LoginPage from './LoginPage';
import SessionSidebar from './SessionSidebar';
import { isLoggedIn, clearToken } from './auth';
import { fetchSessions, renameSession, deleteSession, type SessionInfo } from './api';

const { Sider, Content } = Layout;
const { Text } = Typography;

/**
 * PostHog 视觉语言 → antd 深色主题 tokens
 * 配色：深藏紫蓝底 + 珊瑚橙主色（#F54E00）+ 荧光黄辅助 + 等宽数字
 * 中文适配：正文行高放宽（1.7）、字号克制（14px）、信息密度适中
 */
const posthogTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: '#F54E00',        // 珊瑚橙（PostHog 标志色）
    colorInfo: '#F54E00',
    colorLink: '#ff7a3d',           // 链接用浅一档的橙，深色底上更清晰
    colorBgBase: '#15131c',         // 页面底色：深藏紫蓝
    colorBgContainer: '#1e1a28',    // 卡片/面板表面
    colorBgElevated: '#262233',     // 浮层（下拉/弹窗）
    colorBorder: '#332d45',
    colorBorderSecondary: '#2a2539',
    colorTextBase: '#ece9f2',
    colorText: '#ece9f2',
    colorTextSecondary: '#a6a0b8',
    colorTextTertiary: '#7a748c',
    borderRadius: 10,               // 圆角系统：统一 10px
    fontSize: 14,                   // 中文正文字号克制
    lineHeight: 1.7,                // 中文行高放宽，避免挤在一起
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
  },
  components: {
    Button: {
      primaryColor: '#1a1723',      // 橙底深字（PostHog 风格，同时满足 WCAG AA 对比度）
      fontWeight: 600,
      controlHeight: 36,
    },
    Card: {
      borderRadiusLG: 12,
      headerBg: 'transparent',
    },
    Input: {
      controlHeight: 36,
    },
  },
};

type View = 'chat' | 'dashboard';

/** 登录后的主布局：左侧功能侧边栏 + 会话列表 + 右侧内容区（视图切换） */
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
    <Layout style={{ height: '100dvh', background: '#15131c' }}>
      {/* 左侧 Sider：品牌标 + 功能导航 + 底部退出 */}
      <Sider width={208} style={{ background: '#1e1a28', borderRight: '1px solid #2a2539' }}>
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '16px 18px 14px' }}>
            <div style={{
              width: 28, height: 28, borderRadius: 7, background: '#F54E00',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <RobotOutlined style={{ color: '#1a1723', fontSize: 15 }} />
            </div>
            <Text strong style={{ color: '#ece9f2', fontSize: 14 }}>电商运营 AI</Text>
          </div>
          <Menu
            mode="inline"
            theme="dark"
            style={{ background: 'transparent', borderInlineEnd: 'none' }}
            selectedKeys={[view]}
            onClick={({ key }) => setView(key as View)}
            items={[
              { key: 'chat', icon: <MessageOutlined />, label: '智能问答' },
              { key: 'dashboard', icon: <BarChartOutlined />, label: '数据看板' },
            ]}
          />
          <div style={{ flex: 1 }} />
          <div style={{ padding: 16 }}>
            <Button block icon={<LogoutOutlined />} onClick={handleLogout}>退出登录</Button>
          </div>
        </div>
      </Sider>
      {/* 会话列表：仅在聊天视图显示（数据看板不需要） */}
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
      {/* 右侧内容区：按侧边栏选择渲染对应功能页 */}
      <Content style={{ background: '#15131c', overflow: 'hidden' }}>
        {view === 'chat' ? (
          <ChatPage
            key={chatKey}
            initialSessionId={activeSessionId}
            onSessionIdChange={handleSessionIdChange}
          />
        ) : (
          <DashboardPage />
        )}
      </Content>
    </Layout>
  );
}

function App() {
  const [authed, setAuthed] = useState(isLoggedIn());

  return (
    <ConfigProvider theme={posthogTheme}>
      {authed ? (
        <AppLayout onLogout={() => setAuthed(false)} />
      ) : (
        <LoginPage onSuccess={() => setAuthed(true)} />
      )}
    </ConfigProvider>
  );
}

export default App;
