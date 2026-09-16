import { useState, useRef, useEffect, useCallback } from 'react';
import { Upload, message } from 'antd';
import { ArrowUpOutlined, PaperClipOutlined, FileOutlined } from '@ant-design/icons';
import {
  sendMessageStream, uploadFile, fetchSessionMessages,
  fetchMyDocuments, downloadDocument,
  type StreamEvent, type UploadResult, type GeneratedDocument, type LlmUsage,
} from './api';
import TracePanel, { type TraceStep } from './TracePanel';
import ReactMarkdown from 'react-markdown';
import VizBlock, { parseViz } from './VizBlock';
import { INTENT_DOT, INTENT_LABEL } from './theme';

/** 空态示例：点一下就填进输入框（比一句"我是你的助手"有用） */
const HINTS = [
  '这周营业额为什么低，怎么提高销量',
  '哪些商品库存快没了，需要补货',
  '给苹果15写三条淘宝标题，关键词 超薄、快充',
  '退货要多久？运费谁出？',
  '把这份周报精简成要点并存成 md',
];

/** 助手消息渲染：markdown 正文 + 末尾 ```viz 块渲染成图表/表格
 * 流式中块没闭合 → 正文照常打字机，图表区显示占位；report 完整后自动替换成真图 */
function MarkdownViz({ content }: { content: string }) {
  const { body, viz, incomplete } = parseViz(content);
  return (
    <div className="prose">
      <ReactMarkdown>{body}</ReactMarkdown>
      {viz !== null && <VizBlock raw={viz} incomplete={incomplete} />}
    </div>
  );
}

interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
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
  const [usage, setUsage] = useState<LlmUsage | null>(null);      // 上一轮的 LLM 用量（P2-11）
  const listRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, steps]);

  // 输入框随内容长高（最多 160px，超过内部滚动）—— 不引第三方 autosize
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

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

  // 追加/覆盖最后一个 assistant 消息（报告打字机累积用）
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
    setUsage(null);   // 上一轮的用量不留在这一轮，避免看错

    try {
      await sendMessageStream(text, sessionId, (event: StreamEvent) => {
        switch (event.type) {
          case 'intent':
            setIntent(event.intent);
            finishStep('intent', { label: `识别意图 · ${INTENT_LABEL[event.intent] ?? event.intent}` });
            // 只有"数据分析"链路才有拆解计划 + 子任务阶段；
            // 内容生成/客服问答是 ReAct 直接出结果，只推 report，不能加 plan 步骤（否则永远转圈）
            if (event.intent === 'analysis') {
              pushStep({ id: 'plan', kind: 'plan', label: '拆解执行计划', status: 'running', startedAt: Date.now() });
            }
            break;
          case 'plan':
            finishStep('plan', { plan: event.plan, skill: event.skill });
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
          case 'usage':
            // 本轮用量汇总：不打断流程，只在报告下面挂一行小字（P2-11）
            setUsage(event.usage);
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

  // 活动流排在"正在生成的回答"之前（Codex 的观感：先看它干了什么，再看结论）。
  // 纯展示层重排，不动任何状态逻辑：最后一条若是 assistant，就把它拎出来放到时间线后面。
  const lastIsAssistant = messages.length > 0 && messages[messages.length - 1].role === 'assistant';
  const leading = lastIsAssistant ? messages.slice(0, -1) : messages;
  const trailing = lastIsAssistant ? messages.slice(-1) : [];

  const renderMsg = (msg: ChatMsg, i: number, streaming: boolean) => (
    msg.role === 'user' ? (
      <div className="turn-user" key={i}>
        <span className="mark">›</span>
        <div className="body">{msg.content}</div>
      </div>
    ) : (
      <div className="turn-ai" key={i}>
        <div className="role">
          <span>assistant</span>
          {streaming && <span className="caret" />}
        </div>
        <MarkdownViz content={msg.content} />
      </div>
    )
  );

  return (
    <div className="chat">
      {/* 顶部：极细的一行元信息（意图 / 会话 id 用等宽字体） */}
      <div className="chat-head">
        <span className="title">智能问答</span>
        {intent && (
          <span className="chip">
            <span className="chip-dot" style={{ background: INTENT_DOT[intent] ?? 'var(--text-3)' }} />
            {INTENT_LABEL[intent] ?? intent}
          </span>
        )}
        {sessionId && <span className="chip">session {sessionId}</span>}
        <span style={{ flex: 1 }} />
        {loading && (
          <span className="chip" style={{ border: 'none', background: 'transparent' }}>
            <span className="chip-dot" style={{ background: 'var(--warn)', animation: 'blink 1.2s steps(2) infinite' }} />
            运行中
          </span>
        )}
      </div>

      {/* 消息流 */}
      <div className="chat-body" ref={listRef}>
        <div className="stream">
          {messages.length === 0 && (
            <div className="empty">
              <h1>掌柜</h1>
              <p>电商运营 AI 助手 · 查数分析 / 文案生成 / 售后问答 / 文档处理</p>
              <div className="empty-hints">
                {HINTS.map(h => (
                  <div className="empty-hint" key={h} onClick={() => setInput(h)}>
                    <span className="k">›</span>
                    <span>{h}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {leading.map((m, i) => renderMsg(m, i, false))}

          {/* 执行时间线：本次提问的 Agent 活动（活动 → 结论 的顺序） */}
          {steps.length > 0 && <TracePanel steps={steps} />}

          {trailing.map((m, i) => renderMsg(m, leading.length + i, loading))}

          {/* 生成的文件：Agent 落盘后立刻出现，点一行就能下载 */}
          {docs.length > 0 && (
            <div className="docs">
              <div className="docs-head">生成的文件</div>
              {docs.map(d => (
                <div className="doc-row" key={d.path} onClick={() => handleDownload(d)}>
                  <FileOutlined style={{ fontSize: 12 }} />
                  <span>{d.filename}</span>
                  <span className="size">{(d.bytes / 1024).toFixed(1)} KB ↓</span>
                </div>
              ))}
            </div>
          )}

          {/* 本轮 LLM 用量：一行等宽小字，成本和延迟不再是黑盒 */}
          {usage && (
            <div className="usage">
              <span>usage</span>
              <span>{usage.calls} calls</span>
              <span className="sep">·</span>
              <span>{usage.total_tokens.toLocaleString()} tokens</span>
              <span className="sep">·</span>
              <span>{(usage.elapsed_ms / 1000).toFixed(1)}s</span>
              {usage.estimated_calls > 0 && <span>（{usage.estimated_calls} 次估算）</span>}
              {usage.exhausted && (
                <span style={{ color: 'var(--warn)' }}>
                  已触及{usage.exhausted === 'tokens' ? ' token' : '时间'}上限，报告被截断
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 底部输入区：一个框 + 一行工具提示，没有多余的卡片 */}
      <div className="composer">
        <div className="composer-inner">
          <div className="composer-box">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={loading ? '生成中…' : '问点什么 — Enter 发送，Shift+Enter 换行'}
              disabled={loading}
            />
            <button
              className="send-btn"
              onClick={handleSend}
              disabled={loading || !input.trim()}
              title="发送"
            >
              <ArrowUpOutlined />
            </button>
          </div>
          <div className="composer-bar">
            <Upload
              accept=".csv,.tsv,.md,.pdf,.docx,.html,.htm"
              showUploadList={false}
              beforeUpload={(file) => { handleUpload(file as File); return false; }}
            >
              <span className="tool" style={{ opacity: uploading ? 0.5 : 1 }}>
                <PaperClipOutlined /> 上传数据
              </span>
            </Upload>
            {uploaded.map(u => (
              <span
                className="tool pip" key={u.filename} title={`${u.chars} 字符，点击移除`}
                onClick={() => setUploaded(prev => prev.filter(x => x !== u))}
              >
                {u.filename} ×
              </span>
            ))}
            <span className="spacer" />
            <span>CSV / PDF / MD ≤ 10MB，供竞品对比分析</span>
          </div>
        </div>
      </div>
    </div>
  );
}
