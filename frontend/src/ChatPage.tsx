import { useState, useRef, useEffect, useCallback } from 'react';
import { Input, Button, Card, Typography, Space, Tag, message, Statistic, Row, Col, Collapse } from 'antd';
import {
  SendOutlined, RobotOutlined, UserOutlined, DeleteOutlined,
  DashboardOutlined, ShoppingCartOutlined,
  WarningOutlined, LogoutOutlined,
} from '@ant-design/icons';
import { sendMessageStream, fetchDashboard, type DashboardData, type StreamEvent, type PlanItem } from './api';
import { clearToken } from './auth';
import ReactMarkdown from 'react-markdown';

const { Text, Title } = Typography;
const { TextArea } = Input;

// 意图 → 显示文案（supervisor 条件边分类结果）
const intentLabels: Record<string, string> = {
  analysis: '📊 数据分析',
  content: '✍️ 内容生成',
  service: '🛟 客服问答',
};

// markdown 渲染样式：标题用 antd 排版，和整体风格统一
const mdComponents = {
  h1: ({ node: _n, ...props }: any) => <Title level={4} style={{ marginTop: 12, marginBottom: 8 }} {...props} />,
  h2: ({ node: _n, ...props }: any) => <Title level={5} style={{ marginTop: 12, marginBottom: 8 }} {...props} />,
  h3: ({ node: _n, ...props }: any) => <Title level={5} style={{ marginTop: 10, marginBottom: 6 }} {...props} />,
  p: ({ node: _n, ...props }: any) => <p style={{ margin: '4px 0' }} {...props} />,
  ul: ({ node: _n, ...props }: any) => <ul style={{ margin: '4px 0', paddingLeft: 20 }} {...props} />,
  ol: ({ node: _n, ...props }: any) => <ol style={{ margin: '4px 0', paddingLeft: 20 }} {...props} />,
  li: ({ node: _n, ...props }: any) => <li style={{ margin: '2px 0' }} {...props} />,
  blockquote: ({ node: _n, ...props }: any) => (
    <blockquote style={{ margin: '4px 0', paddingLeft: 12, borderLeft: '3px solid #d9d9d9', color: '#888' }} {...props} />
  ),
};

interface ChatMsg {
  role: 'user' | 'assistant' | 'plan' | 'subtask';
  content: string;
  plan?: PlanItem[];    // plan 消息专用：Planner 拆出的子任务列表
  subtaskId?: string;   // subtask 消息专用：哪个子任务
}

interface ChatPageProps {
  onLogout?: () => void;
}

export default function ChatPage({ onLogout }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [intent, setIntent] = useState('');   // 当前问题被分类到的意图
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const data = await fetchDashboard();
      setDashboard(data);
    } catch { /* 静默 */ }
  }, []);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);
  useEffect(() => {
    if (messages.length > 0 && !loading) loadDashboard();
  }, [loading, messages.length, loadDashboard]);
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  // 追加/覆盖最后一个 assistant 气泡（报告打字机累积用）
  const appendAssistant = useCallback((content: string, replace = false) => {
    setMessages(prev => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last && last.role === 'assistant') {
        // 已有报告气泡：追加片段（打字机）或整体覆盖（收尾）
        copy[copy.length - 1] = { ...last, content: replace ? content : last.content + content };
      } else {
        copy.push({ role: 'assistant', content });
      }
      return copy;
    });
  }, []);

  async function handleSend() {
    const text = input.trim();
    if (!text) return;

    // 用户消息
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    setInput('');
    setLoading(true);

    // 流式接收
    const pendingMsgs: ChatMsg[] = [];
    const addPending = (msg: ChatMsg) => {
      pendingMsgs.push(msg);
      setMessages(prev => [...prev, msg]);
    };

    try {
      await sendMessageStream(text, sessionId, (event: StreamEvent) => {
        switch (event.type) {
          case 'intent':
            setIntent(event.intent);   // 显示路由到了哪条链路
            break;
          case 'plan':
            addPending({ role: 'plan', content: '', plan: event.plan });
            break;
          case 'subtask':
            addPending({ role: 'subtask', content: event.result, subtaskId: event.id });
            break;
          case 'token':
            appendAssistant(event.content);      // 逐字累积，打字机效果
            break;
          case 'report':
            appendAssistant(event.report, true); // 用完整报告覆盖，防丢字
            setLoading(false);
            break;
          case 'session':
            setSessionId(event.session_id);
            break;
        }
      });
    } catch {
      message.error('请求失败，请确认后端已启动');
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleClear() {
    setMessages([]);
    setSessionId('');
  }

  function handleLogout() {
    clearToken();
    onLogout?.();
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* 顶部标题栏 */}
      <Card size="small" style={{ borderRadius: 0, borderTop: 0 }}
        styles={{ body: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } }}>
        <Space>
          <RobotOutlined style={{ fontSize: 20, color: '#1677ff' }} />
          <Title level={5} style={{ margin: 0 }}>电商运营 AI Agent</Title>
          {intent && <Tag color="geekblue">{intentLabels[intent]}</Tag>}
          {sessionId && <Tag color="blue">会话: {sessionId}</Tag>}
        </Space>
        <Space>
          <Button icon={<DashboardOutlined />} size="small" onClick={loadDashboard}>刷新看板</Button>
          <Button icon={<DeleteOutlined />} size="small" onClick={handleClear} disabled={messages.length === 0}>
            清空
          </Button>
          <Button icon={<LogoutOutlined />} size="small" danger onClick={handleLogout}>
            退出登录
          </Button>
        </Space>
      </Card>

      {/* 数据看板 */}
      {dashboard && (
        <div style={{ padding: '12px 16px', background: '#fafafa', borderBottom: '1px solid #f0f0f0' }}>
          <Row gutter={12}>
            <Col span={6}>
              <Card size="small">
                <Statistic title="今日销售额" value={dashboard["今日销售额(元)"]}
                  precision={2} prefix="¥" suffix="元" />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <Statistic title="今日订单数" value={dashboard["今日订单数"]}
                  prefix={<ShoppingCartOutlined />} suffix="单" />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small" style={dashboard["库存预警数"] > 0 ? { borderColor: '#ff4d4f' } : undefined}>
                <Statistic title="库存预警" value={dashboard["库存预警数"]}
                  prefix={<WarningOutlined style={{ color: dashboard["库存预警数"] > 0 ? '#ff4d4f' : '#52c41a' }} />}
                  suffix="个商品"
                  valueStyle={dashboard["库存预警数"] > 0 ? { color: '#ff4d4f' } : undefined} />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <Statistic title="VIP 人均消费"
                  value={dashboard["会员消费"]?.["vip"]?.["人均消费(元)"] ?? 0}
                  precision={2} prefix="¥" suffix="元" />
              </Card>
            </Col>
          </Row>
          <Collapse ghost size="small" items={[{
            key: 'detail', label: <Text type="secondary">📊 分类销售 & 会员对比</Text>,
            children: (
              <Row gutter={12}>
                <Col span={12}>
                  <Card size="small" title="分类销售额">
                    {Object.entries(dashboard["分类销售"]).map(([cat, amt]) => (
                      <div key={cat} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                        <Text>{cat}</Text>
                        <Text strong>¥{amt.toLocaleString()}</Text>
                      </div>
                    ))}
                  </Card>
                </Col>
                <Col span={12}>
                  <Card size="small" title="会员消费对比">
                    {Object.entries(dashboard["会员消费"]).map(([level, stats]) => (
                      <div key={level} style={{ marginBottom: 8 }}>
                        <Tag color={level === 'svip' ? 'gold' : level === 'vip' ? 'blue' : 'default'}>
                          {level.toUpperCase()}
                        </Tag>
                        <Text type="secondary">总消费: ¥{stats["总消费(元)"].toLocaleString()} | </Text>
                        <Text type="secondary">人均: ¥{stats["人均消费(元)"].toLocaleString()}</Text>
                      </div>
                    ))}
                  </Card>
                </Col>
              </Row>
            ),
          }]} />
        </div>
      )}

      {/* 消息列表 */}
      <div ref={listRef} style={{ flex: 1, overflowY: 'auto', padding: '16px 24px', background: '#f5f5f5' }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', marginTop: 60, color: '#999' }}>
            <RobotOutlined style={{ fontSize: 48, marginBottom: 16 }} />
            <br />
            <Text type="secondary">
              我是你的电商运营助手<br />
              📊 查询销售 ｜ 📦 库存预警 ｜ 👤 会员分析 ｜ ✍️ 内容生成
            </Text>
          </div>
        )}

        {messages.map((msg, i) => {
          if (msg.role === 'plan') {
            return (
              <div key={i} style={{ display: 'flex', justifyContent: 'center', marginBottom: 12 }}>
                <Card size="small" style={{ width: '80%', background: '#f0f5ff' }}
                  styles={{ body: { padding: '12px 16px' } }}>
                  <Text strong>📋 执行计划</Text>
                  {msg.plan?.map((p, idx) => (
                    <div key={p.id} style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      <Tag color="blue">{idx + 1}</Tag>
                      <Text style={{ fontSize: 13 }}>{p.task}</Text>
                    </div>
                  ))}
                </Card>
              </div>
            );
          }
          if (msg.role === 'subtask') {
            return (
              <div key={i} style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 8 }}>
                <div style={{ maxWidth: '80%', width: '100%' }}>
                  <div style={{
                    padding: '8px 14px', borderRadius: 10, background: '#f9f0ff',
                    border: '1px solid #d3adf7', fontSize: 13, color: '#531dab',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>🔧 子任务 {msg.subtaskId} 结果</Text>
                    <br />
                    {msg.content}
                  </div>
                </div>
              </div>
            );
          }
          return (
            <div key={i} style={{
              display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 16,
            }}>
              <div style={{ display: 'flex', maxWidth: '75%', gap: 8 }}>
                {msg.role === 'assistant' && <RobotOutlined style={{ fontSize: 20, color: '#1677ff', marginTop: 8 }} />}
                {msg.role === 'user' ? (
                  // 用户消息：纯文本气泡
                  <div style={{
                    padding: '10px 16px', borderRadius: 12,
                    background: '#1677ff', color: '#fff',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.8,
                  }}>
                    {msg.content}
                  </div>
                ) : (
                  // 助手消息：markdown 渲染（报告标题/加粗/列表）
                  <div style={{
                    padding: '10px 16px', borderRadius: 12,
                    background: '#fff', color: '#333', wordBreak: 'break-word', lineHeight: 1.8,
                    boxShadow: '0 1px 3px rgba(0,0,0,0.08)', flex: 1,
                  }}>
                    <ReactMarkdown components={mdComponents}>{msg.content}</ReactMarkdown>
                  </div>
                )}
                {msg.role === 'user' && <UserOutlined style={{ fontSize: 20, color: '#1677ff', marginTop: 8 }} />}
              </div>
            </div>
          );
        })}
      </div>

      {/* 底部输入区 */}
      <Card size="small" style={{ borderRadius: 0, borderBottom: 0 }}>
        <Space.Compact style={{ width: '100%' }}>
          <TextArea value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown} placeholder="输入问题，Enter 发送..."
            autoSize={{ minRows: 1, maxRows: 4 }} disabled={loading}
            style={{ resize: 'none' }} />
          <Button type="primary" icon={<SendOutlined />}
            onClick={handleSend} loading={loading} style={{ height: 'auto' }}>
            发送
          </Button>
        </Space.Compact>
      </Card>
    </div>
  );
}
