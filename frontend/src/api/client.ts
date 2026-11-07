export interface HealthResponse {
  status: string;
  database: string;
}

export interface BusinessHour {
  /** 0=월요일 ... 6=일요일 */
  day_of_week: number;
  open_time: string | null;
  close_time: string | null;
  is_closed: boolean;
}

export interface Store {
  slug: string;
  name: string;
  description: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  website: string | null;
  timezone: string;
  business_hours: BusinessHour[];
}

export interface Conversation {
  id: string;
  visitor_id: string;
  status: "active" | "closed";
  created_at: string;
}

export interface ConversationSummary extends Conversation {
  last_message: string | null;
}

/** 데모용 손님. 인증이 아니라 같은 사람 대화를 고르는 고정 id다. */
export const DEMO_VISITORS = [
  { id: "demo-emma-walsh", name: "Emma Walsh" },
  { id: "demo-noah-bennett", name: "Noah Bennett" },
  { id: "demo-priya-shah", name: "Priya Shah" },
] as const;

export type DemoVisitorId = (typeof DEMO_VISITORS)[number]["id"];

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export async function fetchStore(): Promise<Store> {
  return request<Store>("/api/store");
}

export async function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

export async function fetchConversations(visitorId: string): Promise<ConversationSummary[]> {
  const query = new URLSearchParams({ visitor_id: visitorId });
  return request<ConversationSummary[]>(`/api/conversations?${query}`);
}

export async function createConversation(visitorId: string): Promise<Conversation> {
  return request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ visitor_id: visitorId }),
  });
}

export async function fetchConversation(conversationId: string): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${conversationId}`);
}

export async function fetchMessages(conversationId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/api/conversations/${conversationId}/messages`);
}

export interface StreamHandlers {
  onDelta: (text: string) => void;
}

/**
 * 답변을 SSE로 받는다. EventSource는 POST 본문을 보낼 수 없어서 fetch와 ReadableStream으로 직접 읽는다.
 * `completed` 이벤트가 오면 저장된 답변 메시지 id를 돌려준다.
 * 서버가 `error` 이벤트를 보내거나 `completed` 없이 연결이 끝나면 ApiError를 던진다.
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<string> {
  const res = await fetch(`/api/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ content }),
    signal,
  });
  // 스트림이 시작되기 전의 실패(404, 409, 422 등)는 일반 JSON 오류 응답이다.
  if (!res.ok) {
    throw await toApiError(res);
  }
  if (res.body === null) {
    throw new ApiError(res.status, "STREAM_INTERRUPTED", STREAM_INTERRUPTED_MESSAGE);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      for (;;) {
        const end = buffer.indexOf("\n\n");
        if (end === -1) {
          break;
        }
        const completedId = handleSseBlock(buffer.slice(0, end), handlers);
        buffer = buffer.slice(end + 2);
        if (completedId !== null) {
          return completedId;
        }
      }
    }
  } finally {
    // 끊기거나 일찍 끝나도 연결을 닫아 서버가 생성을 멈추게 한다.
    await reader.cancel().catch(() => undefined);
  }
  throw new ApiError(res.status, "STREAM_INTERRUPTED", STREAM_INTERRUPTED_MESSAGE);
}

const STREAM_INTERRUPTED_MESSAGE = "답변을 받는 도중 연결이 끊어졌습니다. 다시 시도해 주세요.";

/** 이벤트 한 덩어리를 처리한다. `completed`면 메시지 id를, 아니면 null을 돌려준다. */
function handleSseBlock(block: string, handlers: StreamHandlers): string | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const data: unknown = JSON.parse(dataLines.join("\n"));
  const text = stringField(data, "text");
  const messageId = stringField(data, "message_id");
  const code = stringField(data, "code");
  const message = stringField(data, "message");
  if (event === "message_delta" && text !== null) {
    handlers.onDelta(text);
  } else if (event === "completed" && messageId !== null) {
    return messageId;
  } else if (event === "error" && code !== null && message !== null) {
    throw new ApiError(200, code, message);
  }
  return null;
}

function stringField(value: unknown, key: string): string | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const entry = Object.entries(value).find(([name]) => name === key);
  return entry !== undefined && typeof entry[1] === "string" ? entry[1] : null;
}

export type TicketStatus = "open" | "resolved";

export interface Ticket {
  id: string;
  status: TicketStatus;
  question: string;
  escalation_reason: "no_knowledge" | "insufficient_knowledge";
  conversation_id: string;
  owner_answer: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface TicketMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface TicketDetail extends Ticket {
  source_message_id: string | null;
  conversation: Conversation & { messages: TicketMessage[] };
}

export interface OwnerReplyResult {
  ticket: Ticket;
  knowledge_entry_id: string;
  superseded_knowledge_id: string | null;
  conversation_message: TicketMessage;
}

export async function fetchTickets(status?: TicketStatus): Promise<Ticket[]> {
  const query = status === undefined ? "" : `?status=${status}`;
  return request<Ticket[]>(`/api/owner/tickets${query}`);
}

export async function fetchTicket(ticketId: string): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/owner/tickets/${ticketId}`);
}

export async function replyToTicket(ticketId: string, answer: string): Promise<OwnerReplyResult> {
  return request<OwnerReplyResult>(`/api/owner/tickets/${ticketId}/reply`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw await toApiError(res);
  }
  return (await res.json()) as T;
}

async function toApiError(res: Response): Promise<ApiError> {
  try {
    const body: unknown = await res.json();
    if (isErrorBody(body)) {
      return new ApiError(res.status, body.error.code, body.error.message);
    }
  } catch {
    // 본문이 JSON이 아니면 아래 일반 오류로 간다.
  }
  return new ApiError(res.status, "HTTP_ERROR", "요청에 실패했습니다.");
}

function isErrorBody(value: unknown): value is { error: { code: string; message: string } } {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error: unknown = value.error;
  if (typeof error !== "object" || error === null || !("code" in error) || !("message" in error)) {
    return false;
  }
  return typeof error.code === "string" && typeof error.message === "string";
}
