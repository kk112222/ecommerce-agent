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
