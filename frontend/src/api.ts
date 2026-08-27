/**
 * API 请求层 —— 封装与后端的所有通信
 */
import { getToken } from './auth';

/** 构造带鉴权的请求头（自动附加 Bearer token） */
function authHeaders(): Record<string, string> {
  const token = getToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export interface ChatResponse {
  reply: string;
  session_id: string;
}

/** 看板数据 */
export interface DashboardData {
  "今日销售额(元)": number;
  "今日订单数": number;
  "近7日趋势": { date: string; amount: number }[];
  "库存预警数": number;
  "会员消费": Record<string, { "总消费(元)": number; "人均消费(元)": number }>;
  "分类销售": Record<string, number>;
}

/**
 * 发送聊天消息
 */
export async function sendMessage(
  message: string,
  sessionId: string,
): Promise<ChatResponse> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) throw new Error(`请求失败: ${res.status}`);
  return res.json();
}

/**
 * 获取看板数据
 */
export async function fetchDashboard(): Promise<DashboardData> {
  const res = await fetch('/api/dashboard', { headers: authHeaders() });
  if (!res.ok) throw new Error(`请求失败: ${res.status}`);
  return res.json();
}

/** AI 经营洞察的返回结构 */
export interface DashboardInsight {
  insight: string;        // LLM 生成的 markdown 洞察
  cost_ms: number;        // 生成耗时（毫秒）
  error: string | null;   // LLM 失败时的错误信息（正常为 null）
}

/** 获取 AI 经营洞察（后端把看板数据快照喂给 LLM，返回自然语言解读） */
export async function fetchDashboardInsight(): Promise<DashboardInsight> {
  const res = await fetch('/api/dashboard/insight', { headers: authHeaders() });
  if (!res.ok) throw new Error(`请求失败: ${res.status}`);
  return res.json();
}

/** 计划项（Planner 拆出的子任务） */
export interface PlanItem {
  id: string;
  task: string;
  tool_hint: string;
}

/** SSE 流式事件类型（对应后端 supervisor 的 on_event） */
export type StreamEvent =
  | { type: "intent"; intent: string }    // 意图分类结果：analysis/content/service
  | { type: "plan"; plan: PlanItem[] }
  | { type: "subtask"; id: string; task: string; result: string }
  | { type: "token"; content: string }   // 报告逐字片段（打字机效果）
  | { type: "report"; report: string }
  | { type: "session"; session_id: string };

/**
 * 流式聊天 —— SSE 推送思考过程 + token
 * @param message 用户消息
 * @param sessionId 会话 ID
 * @param onEvent 每个 SSE 事件的回调
 * @returns session_id
 */
export async function sendMessageStream(
  message: string,
  sessionId: string,
  onEvent: (event: StreamEvent) => void,
): Promise<string> {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  if (!res.ok) throw new Error(`请求失败: ${res.status}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalSessionId = sessionId;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event: StreamEvent = JSON.parse(line.slice(6));
          if (event.type === 'session') {
            finalSessionId = event.session_id;
          }
          onEvent(event);
        } catch { /* 解析失败跳过 */ }
      }
    }
  }

  return finalSessionId;
}

/** 构造仅带 token 的请求头 —— multipart 上传不能预设 Content-Type，
 * 否则 fetch 不会自动补 boundary，后端会解析失败 */
function authTokenHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 会话元信息（对应后端 /api/sessions 返回的列表项） */
export interface SessionInfo {
  id: string;
  title: string;
  msg_count: number;
  updated_at: string;
}

/** 某会话的一条历史消息（对应后端 /api/sessions/{sid}/messages） */
export interface HistoryMsg {
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

/** 获取当前用户的会话列表（侧边栏用，按最近活跃倒序） */
export async function fetchSessions(): Promise<SessionInfo[]> {
  const res = await fetch('/api/sessions', { headers: authHeaders() });
  if (!res.ok) throw new Error(`请求失败: ${res.status}`);
  return res.json();
}

/** 获取某个会话的历史消息（切换会话时加载） */
export async function fetchSessionMessages(sid: string): Promise<HistoryMsg[]> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}/messages`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`请求失败: ${res.status}`);
  return res.json();
}

/** 重命名会话标题 */
export async function renameSession(sid: string, title: string): Promise<void> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`重命名失败: ${res.status}`);
}

/** 彻底删除会话（后端连消息一起物理删除，不可恢复） */
export async function deleteSession(sid: string): Promise<void> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE', headers: authHeaders() });
  if (!res.ok) throw new Error(`删除失败: ${res.status}`);
}

/** 上传文件的解析结果（对应后端 /api/upload 返回） */
export interface UploadResult {
  filename: string;
  chars: number;
  preview: string;
}

/**
 * 上传数据文件（CSV/PDF/MD...）到当前会话
 * @param file 选择的文件
 * @param sessionId 会话 ID（上传的文件挂在这个会话下，聊天时 Agent 才能读到）
 */
export async function uploadFile(file: File, sessionId: string): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  form.append('session_id', sessionId);
  const res = await fetch('/api/upload', {
    method: 'POST',
    headers: authTokenHeaders(),
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === 'string' ? detail : `上传失败: ${res.status}`);
  }
  return res.json();
}
