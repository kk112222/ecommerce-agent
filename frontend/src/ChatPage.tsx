import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Input, Button, Card, Typography, Space, Tag, message, Upload,
} from 'antd';
import {
  SendOutlined, RobotOutlined, UserOutlined,
  UploadOutlined, FileTextOutlined, ProfileOutlined, ToolOutlined, DownloadOutlined,
} from '@ant-design/icons';
import {
  sendMessageStream, uploadFile, fetchSessionMessages,
  fetchMyDocuments, downloadDocument,
  type StreamEvent, type PlanItem, type UploadResult, type GeneratedDocument,
} from './api';
import TracePanel, { type TraceStep } from './TracePanel';
import ReactMarkdown from 'react-markdown';
import VizBlock, { parseViz } from './VizBlock';

const { Text, Title } = Typography;
const { TextArea } = Input;

// ============ PostHog 视觉语言 → 局部样式常量 ============
// （主题色在 App.tsx 的 ConfigProvider 里统一定义，这里只放组件局部要用的）
const C = {
  bg: '#15131c',          // 页面底
  surface: '#1e1a28',     // 卡片表面
  surface2: '#262233',    // 次级表面（子任务卡片）
  border: '#332d45',
  borderSoft: '#2a2539',
  accent: '#F54E00',      // 珊瑚橙主色
  accentText: '#1a1723',  // 橙底上的深色文字
  yellow: '#FFE14D',      // PostHog 荧光黄（辅助强调）
  text: '#ece9f2',
  textSec: '#a6a0b8',
  textWeak: '#7a748c',
  mono: "'JetBrains Mono','Source Code Pro',Consolas,monospace",
};

// 意图 → 显示文案 + 强调色（supervisor 条件边分类结果；图标用 SVG，不堆 emoji）
const intentLabels: Record<string, string> = {
  analysis: '数据分析',
  content: '内容生成',
  service: '客服问答',
  document: '文档处理',
};
const intentColor: Record<string, string> = {
  analysis: '#ff7a3d',
  content: '#e8c92f',
  service: '#9a8cff',
  document: '#4fd1c5',
};

// markdown 渲染样式：深色主题适配，标题用 antd 排版
const mdComponents = {
  h1: ({ node: _n, ...props }: any) => <Title level={4} style={{ marginTop: 12, marginBottom: 8, color: C.text }} {...props} />,
  h2: ({ node: _n, ...props }: any) => <Title level={5} style={{ marginTop: 12, marginBottom: 8, color: C.text }} {...props} />,
  h3: ({ node: _n, ...props }: any) => <Title level={5} style={{ marginTop: 10, marginBottom: 6, color: C.text }} {...props} />,
  p: ({ node: _n, ...props }: any) => <p style={{ margin: '4px 0' }} {...props} />,
  ul: ({ node: _n, ...props }: any) => <ul style={{ margin: '4px 0', paddingLeft: 20 }} {...props} />,
  ol: ({ node: _n, ...props }: any) => <ol style={{ margin: '4px 0', paddingLeft: 20 }} {...props} />,
  li: ({ node: _n, ...props }: any) => <li style={{ margin: '2px 0' }} {...props} />,
  blockquote: ({ node: _n, ...props }: any) => (
    <blockquote style={{ margin: '4px 0', paddingLeft: 12, borderLeft: '3px solid #332d45', color: C.textSec }} {...props} />
  ),
  a: ({ node: _n, ...props }: any) => <a style={{ color: '#ff7a3d' }} {...props} />,
  strong: ({ node: _n, ...props }: any) => <strong style={{ color: C.text }} {...props} />,
};

/** 助手消息渲染：普通 markdown + 识别末尾 ```viz 块渲染成图表/表格。
 * 流式中块没闭合 → 正文照常打字机，图表区显示占位；report 完整后自动替换成真图 */
function MarkdownViz({ content }: { content: string }) {
  const { body, viz, incomplete } = parseViz(content);
  return (
    <>
      <ReactMarkdown components={mdComponents}>{body}</ReactMarkdown>
      {viz !== null && <VizBlock raw={viz} incomplete={incomplete} />}
    </>
  );
}

interface ChatMsg {
  role: 'user' | 'assistant' | 'plan' | 'subtask';
  content: string;
  plan?: PlanItem[];
  subtaskId?: string;
}

interface ChatPageProps {
  initialSessionId: string;                      // 进入本会话时 App 传入的会话 id（'' = 新对话）
  onSessionIdChange: (sid: string) => void;      // 会话确定/变化时通知 App（刷新列表 + 高亮）
}

export default function ChatPage({ initialSessionId, onSessionIdChange }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(initialSessionId);
  const [intent, setIntent] = useState('');          // 当前问题被分类到的意图
  const [steps, setSteps] = useState<TraceStep[]>([]);   // 本次提问的执行时间线
  const [uploaded, setUploaded] = useState<UploadResult[]>([]);   // 本会话已上传的文件
  const [uploading, setUploading] = useState(false);
  const [docs, setDocs] = useState<GeneratedDocument[]>([]);      // 本会话已生成的文件（可下载）
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, steps]);

  // 挂载时若带初始会话 id（从侧边栏切进来的历史会话），拉取历史消息填充
  useEffect(() => {
    if (!initialSessionId) return;
    let alive = true;
    (async () => {
      try {
        const history = await fetchSessionMessages(initialSessionId);
        if (alive) setMessages(history.map(h => ({ role: h.role, content: h.content })));
      } catch { /* 后端未启动或会话已删，保持空 */ }
    })();
    return () => { alive = false; };   // 防卸载后 setState
  }, [initialSessionId]);

  // 切会话时拉一次"我生成过的文档"，只留本会话的 —— 刷新页面后下载入口不丢
  useEffect(() => {
    if (!sessionId) { setDocs([]); return; }
    let alive = true;
    (async () => {
      try {
        const all = await fetchMyDocuments();
        if (alive) setDocs(all.filter(d => d.session_id === sessionId));
      } catch { /* 未登录/后端未启动，忽略 */ }
    })();
    return () => { alive = false; };
  }, [sessionId]);

  // 追加/覆盖最后一个 assistant 气泡（报告打字机累积用）
  const appendAssistant = useCallback((content: string, replace = false) => {
    setMessages(prev => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last && last.role === 'assistant') {
        copy[copy.length - 1] = { ...last, content: replace ? content : last.content + content };
      } else {
        copy.push({ role: 'assistant', content });
      }
      return copy;
    });
  }, []);

  // 上传前确保有一个会话 ID：上传的文件挂在这个会话下，聊天时 Agent 才能读到
  // 新对话还没 sid 时现场生成一个，并上报 App（高亮 + 刷新列表）
  const ensureSessionId = () => {
    if (!sessionId) {
      const id = Math.random().toString(36).slice(2, 10);
      setSessionId(id);
      onSessionIdChange(id);
      return id;
    }
    return sessionId;
  };

  // 选择文件后上传到后端（后端解析成文本，Agent 提问时注入做竞品对比）
  async function handleUpload(file: File) {
    const sid = ensureSessionId();
    setUploading(true);
    try {
      const r = await uploadFile(file, sid);
      setUploaded(prev => [...prev, r]);
      message.success(`已上传 ${r.filename}（${r.chars} 字符），可直接提问做对比分析`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  }

  async function handleSend() {
    const text = input.trim();
    if (!text) return;

    setMessages(prev => [...prev, { role: 'user', content: text }]);
    setInput('');
    setLoading(true);

    // 新一轮执行：重置时间线，先点亮"识别意图"
    setSteps([{ id: 'intent', kind: 'intent', label: '识别意图', status: 'running', startedAt: Date.now() }]);

    try {
      await sendMessageStream(text, sessionId, (event: StreamEvent) => {
        switch (event.type) {
          case 'intent':
            setIntent(event.intent);
            finishStep('intent', { label: `识别意图 · ${intentLabels[event.intent] ?? event.intent}` });
            // 只有"数据分析"链路才有拆解计划 + 子任务阶段；
            // 内容生成/客服问答是 ReAct 直接出结果，只推 report，不能加 plan 步骤（否则永远转圈）
            if (event.intent === 'analysis') {
              pushStep({ id: 'plan', kind: 'plan', label: '拆解执行计划', status: 'running', startedAt: Date.now() });
            }
            break;
          case 'plan':
            finishStep('plan', { plan: event.plan });
            // 计划拆出来后，为每个子任务建一个 running 步骤（执行完逐个打勾）
            event.plan.forEach((p, i) => {
              pushStep({
                id: p.id, kind: 'subtask',
                label: `子任务 ${i + 1}：${p.task}`,
                status: 'running', startedAt: Date.now(),
                toolHint: p.tool_hint,
              });
            });
            break;
          case 'subtask':
            finishStep(event.id, { result: event.result.slice(0, 200) });
            break;
          case 'token':
            // 第一次收到 token = 合成器开始流式生成报告，点亮"生成报告"步骤
            pushStepIfAbsent({ id: 'report', kind: 'report', label: '生成最终报告', status: 'running', startedAt: Date.now() });
            appendAssistant(event.content);
            break;
          case 'report':
            appendAssistant(event.report, true);
            finishStep('report', {});
            setLoading(false);
            break;
          case 'document':
            // 文档已落盘：立刻给出下载入口（按 path 去重，避免同轮重复推送）
            setDocs(prev => prev.some(d => d.path === event.path) ? prev : [...prev, event]);
            pushStepIfAbsent({
              id: `doc-${event.path}`, kind: 'report',
              label: `生成文档 · ${event.filename}`, status: 'done', startedAt: Date.now(),
            });
            break;
          case 'error':
            // 后端链路异常（LLM 失败等）：标红 + 报错 + 结束 loading，别让界面永久转圈
            setSteps(prev => prev.map(s => s.status === 'running' ? { ...s, status: 'error' } : s));
            message.error(event.message || '分析失败');
            setLoading(false);
            break;
          case 'session':
            setSessionId(event.session_id);
            onSessionIdChange(event.session_id);   // 通知 App 刷新会话列表 + 高亮
            break;
        }
      });
    } catch {
      // 请求失败：把还卡在 running 的步骤标红，别让它一直转
      setSteps(prev => prev.map(s => s.status === 'running' ? { ...s, status: 'error' } : s));
      message.error('请求失败，请确认后端已启动');
    } finally {
      setLoading(false);
    }
  }

  // ============ 执行时间线的三个操作（SSE 事件驱动） ============

  /** 完成某步骤：算耗时 + 打勾 */
  const finishStep = (id: string, patch: Partial<TraceStep>) => {
    setSteps(prev => prev.map(s =>
      s.id === id ? {
        ...s, ...patch, status: 'done',
        costMs: Date.now() - (s.startedAt ?? Date.now()),
      } : s
    ));
  };

  /** 追加一个新步骤 */
  const pushStep = (step: TraceStep) => {
    setSteps(prev => [...prev, step]);
  };

  /** 仅当不存在时追加（防止 token 事件多次点亮"生成报告"） */
  const pushStepIfAbsent = (step: TraceStep) => {
    setSteps(prev => prev.some(s => s.id === step.id) ? prev : [...prev, step]);
  };

  // 下载生成文档（带 token 走 fetch 拿 blob，见 api.ts 的说明）
  async function handleDownload(d: GeneratedDocument) {
    try {
      await downloadDocument(d.session_id, d.filename);
    } catch (e) {
      message.error(e instanceof Error ? e.message : '下载失败');
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', height: '100dvh', display: 'flex', flexDirection: 'column', background: C.bg }}>
      {/* ============ 顶部标题栏 ============ */}
      <Card size="small" style={{ borderRadius: 0, borderTop: 0, borderBottom: `1px solid ${C.borderSoft}`, background: C.surface }}
        styles={{ body: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 24px' } }}>
        <Space size={12}>
          <Title level={5} style={{ margin: 0, fontSize: 15, color: C.text }}>智能问答</Title>
          {intent && (
            <Tag style={{ background: 'rgba(245,78,0,0.14)', color: intentColor[intent], borderColor: 'rgba(245,78,0,0.4)', margin: 0 }}>
              {intentLabels[intent]}
            </Tag>
          )}
          {sessionId && (
            <Tag style={{ background: 'transparent', borderColor: C.border, color: C.textSec, margin: 0 }}>会话 {sessionId}</Tag>
          )}
        </Space>
      </Card>

      {/* ============ 消息列表 ============ */}
      <div ref={listRef} style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', background: C.bg }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', marginTop: 64, color: C.textWeak }}>
            <RobotOutlined style={{ fontSize: 44, marginBottom: 16, color: C.accent }} />
            <br />
            <Text style={{ color: C.textSec, fontSize: 15 }}>
              我是你的电商运营助手
            </Text>
            <br />
            <Text style={{ color: C.textWeak, fontSize: 13 }}>
              查销售 ｜ 库存预警 ｜ 会员分析 ｜ 文案生成 ｜ 上传数据做竞品对比
            </Text>
          </div>
        )}

        {messages.map((msg, i) => {
          if (msg.role === 'plan') {
            return (
              <div key={i} style={{ display: 'flex', justifyContent: 'center', marginBottom: 12 }}>
                <Card size="small" style={{ width: '82%', background: C.surface, borderColor: C.borderSoft, borderLeft: `3px solid ${C.accent}` }}
                  styles={{ body: { padding: '12px 16px' } }}>
                  <Text strong style={{ color: C.text, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <ProfileOutlined style={{ color: C.accent }} /> 执行计划
                  </Text>
                  {msg.plan?.map((p, idx) => (
                    <div key={p.id} style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      <Tag color="orange" style={{ margin: 0 }}>{idx + 1}</Tag>
                      <Text style={{ fontSize: 13, color: C.textSec }}>{p.task}</Text>
                    </div>
                  ))}
                </Card>
              </div>
            );
          }
          if (msg.role === 'subtask') {
            return (
              <div key={i} style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 8 }}>
                <div style={{ maxWidth: '82%', width: '100%' }}>
                  <div style={{
                    padding: '10px 14px', borderRadius: 10, background: C.surface2,
                    border: `1px solid ${C.border}`, fontSize: 13, color: '#c8b9ff',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  }}>
                    <Text style={{ fontSize: 11, color: C.textWeak, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <ToolOutlined style={{ fontSize: 11 }} /> 子任务 {msg.subtaskId} 结果
                    </Text>
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
              <div style={{ display: 'flex', maxWidth: '78%', gap: 8, alignItems: 'flex-start' }}>
                {msg.role === 'assistant' && <RobotOutlined style={{ fontSize: 18, color: C.accent, marginTop: 10 }} />}
                {msg.role === 'user' ? (
                  // 用户消息：珊瑚橙底深字（PostHog 风格）
                  <div style={{
                    padding: '10px 16px', borderRadius: 12,
                    background: C.accent, color: C.accentText,
                    whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.8, fontWeight: 500,
                  }}>
                    {msg.content}
                  </div>
                ) : (
                  // 助手消息：深色表面 + 细边框，markdown 渲染
                  <div style={{
                    padding: '12px 16px', borderRadius: 12,
                    background: C.surface, color: C.text, wordBreak: 'break-word', lineHeight: 1.8,
                    border: `1px solid ${C.borderSoft}`, flex: 1,
                  }}>
                    <MarkdownViz content={msg.content} />
                  </div>
                )}
                {msg.role === 'user' && <UserOutlined style={{ fontSize: 18, color: C.textSec, marginTop: 10 }} />}
              </div>
            </div>
          );
        })}

        {/* 生成的文档：Agent 落盘后立刻出现在这里，点一下就能下载（P0-3 闭环的那一半） */}
        {docs.length > 0 && (
          <Card size="small" style={{ marginTop: 12, background: C.surface, borderColor: C.borderSoft, borderLeft: '3px solid #4fd1c5' }}
            styles={{ body: { padding: '10px 16px' } }}>
            <Text strong style={{ fontSize: 13, color: C.text, display: 'flex', alignItems: 'center', gap: 6 }}>
              <FileTextOutlined style={{ color: '#4fd1c5' }} /> 本次生成的文件
            </Text>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              {docs.map(d => (
                <Button key={d.path} size="small" icon={<DownloadOutlined />}
                  onClick={() => handleDownload(d)}
                  style={{ fontWeight: 500 }}>
                  {d.filename}（{(d.bytes / 1024).toFixed(1)} KB）
                </Button>
              ))}
            </div>
          </Card>
        )}

        {/* 本次提问的执行时间线：意图→计划→子任务→报告 */}
        {steps.length > 0 && <TracePanel steps={steps} />}
      </div>

      {/* ============ 底部：上传区 + 输入区 ============ */}
      <Card size="small" style={{ borderRadius: 0, borderBottom: 0, borderTop: `1px solid ${C.borderSoft}`, background: C.surface }}
        styles={{ body: { padding: '10px 24px 12px' } }}>
        {/* 上传数据条：CSV/PDF/MD，Agent 提问时注入做竞品对比 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <Upload
            accept=".csv,.tsv,.md,.pdf,.docx,.html,.htm"
            showUploadList={false}
            beforeUpload={(file) => { handleUpload(file as File); return false; }}
          >
            <Button size="small" icon={<UploadOutlined />} loading={uploading} style={{ fontWeight: 500 }}>
              上传数据
            </Button>
          </Upload>
          <Text style={{ fontSize: 12, color: C.textWeak }}>CSV / PDF / MD（≤10MB），供竞品对比分析</Text>
          {uploaded.map(u => (
            <Tag
              key={u.filename}
              icon={<FileTextOutlined />}
              closable
              onClose={() => setUploaded(prev => prev.filter(x => x !== u))}
              style={{ background: 'rgba(255,225,77,0.1)', color: C.yellow, borderColor: 'rgba(255,225,77,0.35)', fontSize: 12 }}
            >
              {u.filename}
            </Tag>
          ))}
        </div>

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
