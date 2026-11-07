import { type FormEvent, useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";

import {
  ApiError,
  DEMO_VISITORS,
  createConversation,
  fetchConversation,
  fetchConversations,
  fetchMessages,
  streamMessage,
  type BusinessHour,
  type ChatMessage,
  type ConversationSummary,
  type DemoVisitorId,
  type Store,
} from "../api/client";
import type { StoreState } from "../components/Layout";

// 백엔드 MessageCreate와 같은 제한이다.
const MAX_MESSAGE_LENGTH = 2000;
// 사장 답변은 이 화면을 새로고침하지 않아도 이 간격으로 나타난다.
const REFRESH_MS = 2000;
const DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"] as const;

export default function CustomerChatPage() {
  const storeState = useOutletContext<StoreState>();
  // null이면 첫 화면(손님 선택)이다. 고른 뒤에만 그 손님의 대화 목록을 불러온다.
  const [visitorId, setVisitorId] = useState<DemoVisitorId | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [closed, setClosed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const conversationId = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const streamAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (visitorId === null) {
      return;
    }
    let ignore = false;
    fetchConversations(visitorId)
      .then((list) => {
        if (ignore) {
          return;
        }
        setConversations(list);
        setListError(null);
      })
      .catch((caught: unknown) => {
        if (!ignore) {
          setListError(messageOf(caught));
        }
      });
    return () => {
      ignore = true;
    };
  }, [visitorId]);

  useEffect(() => {
    if (visitorId === null || activeId === null || sending) {
      return;
    }
    let ignore = false;
    Promise.all([fetchConversation(activeId), fetchMessages(activeId)])
      .then(([conversation, history]) => {
        if (ignore) {
          return;
        }
        setMessages(history);
        setClosed(conversation.status === "closed");
      })
      .catch((caught: unknown) => {
        if (ignore) {
          return;
        }
        if (caught instanceof ApiError && caught.status === 404) {
          rememberConversation(visitorId, null);
          setActiveId(null);
          setMessages([]);
          return;
        }
        setError(messageOf(caught));
      });
    return () => {
      ignore = true;
    };
  }, [activeId, sending, visitorId]);

  // 보고 있는 대화에 사장 답변이 붙으면 새로고침 없이 보여 준다. 답변을 받는 중에는 조각을 덮어쓰지 않는다.
  useEffect(() => {
    if (visitorId === null || activeId === null || sending) {
      return;
    }
    const id = activeId;
    const timer = setInterval(() => {
      fetchMessages(id)
        .then((history) => {
          setMessages((current) => (sameMessages(current, history) ? current : history));
        })
        .catch(() => undefined);
      fetchConversations(visitorId)
        .then((list) => setConversations(list))
        .catch(() => undefined);
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [activeId, sending, visitorId]);

  // 화면을 떠나면 진행 중인 스트림을 끊는다. 서버는 연결 종료를 보고 생성을 멈춘다.
  useEffect(() => () => streamAbort.current?.abort(), []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, sending]);

  function chooseVisitor(id: DemoVisitorId) {
    setMessages([]);
    setClosed(false);
    setError(null);
    setListError(null);
    setDraft("");
    setConversations([]);
    setActiveId(null);
    conversationId.current = null;
    setVisitorId(id);
  }

  function leaveVisitor() {
    if (sending) {
      return;
    }
    setMessages([]);
    setClosed(false);
    setError(null);
    setDraft("");
    setConversations([]);
    setActiveId(null);
    conversationId.current = null;
    setVisitorId(null);
  }

  function requireVisitor(): DemoVisitorId {
    if (visitorId === null) {
      throw new Error("손님이 선택되지 않았습니다.");
    }
    return visitorId;
  }

  function selectConversation(id: string) {
    if (sending || id === activeId) {
      return;
    }
    rememberConversation(requireVisitor(), id);
    setError(null);
    setDraft("");
    setActiveId(id);
  }

  function startNewChat() {
    rememberConversation(requireVisitor(), null);
    setActiveId(null);
    setMessages([]);
    setDraft("");
    setClosed(false);
    setError(null);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (content === "" || sending || closed || storeState.kind === "missing") {
      return;
    }
    // 서버는 질문을 먼저 저장하고 답변을 조각으로 보낸다. 첫 조각이 오기 전까지는 화면용 질문만 보여 준다.
    const pending: ChatMessage = {
      id: `pending-${crypto.randomUUID()}`,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    const pendingAnswerId = `pending-${crypto.randomUUID()}`;
    const abort = new AbortController();
    streamAbort.current = abort;
    setMessages((current) => [...current, pending]);
    setDraft("");
    setSending(true);
    setError(null);
    try {
      const id = await ensureConversation();
      const savedId = await streamMessage(
        id,
        content,
        {
          onDelta: (text) => setMessages((current) => appendAnswerText(current, pendingAnswerId, text)),
        },
        abort.signal,
      );
      // 완료되면 화면용 답변을 저장된 메시지로 취급한다.
      setMessages((current) =>
        current.map((message) => (message.id === pendingAnswerId ? { ...message, id: savedId } : message)),
      );
      setConversations(await fetchConversations(requireVisitor()));
    } catch (caught: unknown) {
      if (abort.signal.aborted) {
        return;
      }
      setError(messageOf(caught));
      if (caught instanceof ApiError && caught.code === "CONVERSATION_CLOSED") {
        setClosed(true);
      }
      // 중간에 실패하면 서버는 답변을 저장하지 않는다. 받던 조각은 버리고, 고객 메시지는 서버 목록으로 맞춘다.
      const id = conversationId.current;
      if (id === null) {
        setMessages((current) =>
          current.filter((message) => message.id !== pending.id && message.id !== pendingAnswerId),
        );
      } else {
        try {
          setMessages(await fetchMessages(id));
        } catch {
          // 목록 조회까지 실패하면 받던 조각만 지우고 화면용 메시지를 그대로 둔다.
          setMessages((current) => current.filter((message) => message.id !== pendingAnswerId));
        }
      }
    } finally {
      if (streamAbort.current === abort) {
        streamAbort.current = null;
      }
      setSending(false);
    }
  }

  async function ensureConversation(): Promise<string> {
    const visitor = requireVisitor();
    const current = conversationId.current;
    if (current !== null) {
      return current;
    }
    const created = await createConversation(visitor);
    rememberConversation(visitor, created.id);
    setActiveId(created.id);
    return created.id;
  }

  function rememberConversation(visitor: string, id: string | null) {
    conversationId.current = id;
    if (id === null) {
      localStorage.removeItem(conversationKey(visitor));
      return;
    }
    localStorage.setItem(conversationKey(visitor), id);
  }

  const store = storeState.kind === "ready" ? storeState.store : null;
  const visitor = DEMO_VISITORS.find((item) => item.id === visitorId) ?? null;
  if (visitor === null) {
    return (
      <section className="visitor-picker" aria-label="데모 손님 선택">
        <h1>누구로 입장할까요?</h1>
        <p>고르면 그 손님의 대화 목록이 나옵니다.</p>
        <div className="visitor-switch" role="group" aria-label="데모 손님">
          {DEMO_VISITORS.map((item) => (
            <button key={item.id} type="button" className="visitor-button" onClick={() => chooseVisitor(item.id)}>
              {item.name}
            </button>
          ))}
        </div>
      </section>
    );
  }
  // 첫 조각이 오기 전에는 마지막 메시지가 고객 질문이다.
  const waitingForAnswer = sending && messages[messages.length - 1]?.role === "user";
  const inputDisabled = sending || closed || storeState.kind === "missing";

  return (
    <div className="chat-layout">
      <aside className="chat-side" aria-label="대화 목록">
        <h2>{visitor.name}의 대화</h2>
        <button type="button" className="button-secondary" onClick={startNewChat} disabled={sending}>
          새 대화
        </button>
        {listError !== null && (
          <p className="chat-error" role="alert">
            {listError}
          </p>
        )}
        {conversations.length === 0 && listError === null && <p className="chat-empty">대화가 없습니다.</p>}
        {conversations.length > 0 && (
          <ul className="conversation-list">
            {conversations.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={item.id === activeId ? "conversation-item conversation-item-active" : "conversation-item"}
                  aria-current={item.id === activeId ? "true" : undefined}
                  disabled={sending}
                  onClick={() => selectConversation(item.id)}
                >
                  <span className="conversation-preview">{item.last_message ?? "메시지 없음"}</span>
                  <time dateTime={item.created_at}>
                    {formatTime(item.created_at)}
                    {item.status === "closed" ? " · 종료" : ""}
                  </time>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="chat" aria-label="고객 채팅">
        <div className="chat-toolbar">
          <h1>{visitor.name}</h1>
          <button type="button" className="button-secondary" onClick={leaveVisitor} disabled={sending}>
            다른 손님
          </button>
        </div>
        <div className="chat-log" aria-live="polite">
          {messages.length === 0 && !sending && (
            <p className="chat-empty">
              {activeId === null
                ? "왼쪽에서 대화를 고르거나, 메시지를 적어 새 대화를 시작하세요."
                : "매장에 궁금한 점을 물어보세요. 답하기 어려운 문의는 매장에 전달합니다."}
            </p>
          )}
          {messages.map((message) => (
            <article key={message.id} className={`bubble bubble-${message.role}`}>
              <p>{message.content}</p>
              <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
            </article>
          ))}
          {waitingForAnswer && <p className="chat-pending">답변을 작성하고 있습니다. 잠시만 기다려 주세요.</p>}
          <div ref={bottom} />
        </div>
        {error !== null && (
          <p className="chat-error" role="alert">
            {error}
          </p>
        )}
        {closed && <p className="chat-note">종료된 대화입니다. 새 대화를 시작해 주세요.</p>}
        <form className="chat-form" onSubmit={onSubmit}>
          <label className="visually-hidden" htmlFor="chat-input">
            메시지
          </label>
          <textarea
            id="chat-input"
            value={draft}
            maxLength={MAX_MESSAGE_LENGTH}
            rows={3}
            placeholder="메시지를 입력하세요"
            disabled={inputDisabled}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <button type="submit" disabled={inputDisabled || draft.trim() === ""}>
            보내기
          </button>
        </form>
      </section>
      {store !== null && <StoreCard store={store} />}
    </div>
  );
}

function StoreCard({ store }: { store: Store }) {
  return (
    <aside className="store-card">
      <h2>{store.name}</h2>
      {store.description !== null && <p>{store.description}</p>}
      <dl>
        {store.address !== null && (
          <>
            <dt>주소</dt>
            <dd>{store.address}</dd>
          </>
        )}
        {store.phone !== null && (
          <>
            <dt>전화</dt>
            <dd>
              <a href={`tel:${store.phone}`}>{store.phone}</a>
            </dd>
          </>
        )}
      </dl>
      {store.business_hours.length > 0 && (
        <>
          <h3>영업시간</h3>
          <ul className="hours">
            {store.business_hours.map((hour) => (
              <li key={hour.day_of_week}>
                <span>{DAY_NAMES[hour.day_of_week]}</span>
                <span>{formatHour(hour)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

function conversationKey(visitorId: string): string {
  return `ai-store-help.conversation.${visitorId}`;
}

function sameMessages(current: ChatMessage[], next: ChatMessage[]): boolean {
  if (current.length !== next.length) {
    return false;
  }
  return current.every(
    (message, index) => message.id === next[index]?.id && message.content === next[index]?.content,
  );
}

function appendAnswerText(current: ChatMessage[], answerId: string, text: string): ChatMessage[] {
  if (current.some((message) => message.id === answerId)) {
    return current.map((message) =>
      message.id === answerId ? { ...message, content: message.content + text } : message,
    );
  }
  return [...current, { id: answerId, role: "assistant", content: text, created_at: new Date().toISOString() }];
}

function messageOf(caught: unknown): string {
  if (caught instanceof ApiError) {
    return caught.message;
  }
  return "메시지를 보내지 못했습니다. 다시 시도해 주세요.";
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

function formatHour(hour: BusinessHour): string {
  if (hour.is_closed || hour.open_time === null || hour.close_time === null) {
    return "휴무";
  }
  return `${hour.open_time.slice(0, 5)}–${hour.close_time.slice(0, 5)}`;
}
