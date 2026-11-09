import { type FormEvent, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import {
  ApiError,
  fetchTicket,
  fetchTickets,
  replyToTicket,
  type KnowledgeLifetime,
  type OwnerReplyResult,
  type Ticket,
  type TicketDetail,
  type TicketStatus,
} from "../api/client";

const MAX_ANSWER_LENGTH = 2000;

type Filter = TicketStatus | "all";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "open", label: "미처리" },
  { id: "resolved", label: "답변 완료" },
  { id: "all", label: "전체" },
];

const STATUS_LABEL: Record<TicketStatus, string> = {
  open: "미처리",
  resolved: "답변 완료",
};

const REASON_LABEL: Record<Ticket["escalation_reason"], string> = {
  no_knowledge: "관련 지식 없음",
  insufficient_knowledge: "있는 지식으로 답할 수 없음",
};

const LIFETIME_CHOICES: { id: KnowledgeLifetime; label: string }[] = [
  { id: "none", label: "이 손님에게만" },
  { id: "permanent", label: "계속 저장" },
  { id: "until", label: "날짜까지" },
];

const LIFETIME_NOTE: Record<KnowledgeLifetime, string | null> = {
  none: "이 답은 이 손님에게만 전달됩니다. 지식으로는 저장되지 않습니다.",
  permanent: null,
  until: "고른 날짜가 끝나면, 다음 날 0시부터 지식에서 빠집니다.",
};

function savedNotice(result: OwnerReplyResult): string {
  if (result.knowledge_entry_id === null) {
    return "답변을 등록했습니다. 이 손님에게만 전달하고 지식으로는 남기지 않았습니다.";
  }
  if (result.ticket.knowledge_lifetime === "until") {
    return result.superseded_knowledge_id === null
      ? "답변을 등록했습니다. 고른 날짜가 끝나는 자정까지 지식으로 씁니다."
      : "답변을 등록했습니다. 같은 질문의 이전 지식을 고른 날짜까지 쓰는 답으로 바꿨습니다.";
  }
  return result.superseded_knowledge_id === null
    ? "답변을 등록했습니다. 고객 대화에 전달되고 지식으로 저장되었습니다."
    : "답변을 등록했습니다. 같은 질문의 이전 지식을 이 답변으로 바꿨습니다.";
}

export default function OwnerTicketsPage() {
  const { storeSlug = "" } = useParams();
  const [filter, setFilter] = useState<Filter>("open");
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [lifetime, setLifetime] = useState<KnowledgeLifetime>("none");
  const [expiresOn, setExpiresOn] = useState("");
  const [sending, setSending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let ignore = false;
    fetchTickets(storeSlug, filter === "all" ? undefined : filter)
      .then((list) => {
        if (ignore) {
          return;
        }
        setTickets(list);
        setListError(null);
        setSelectedId((current) =>
          current !== null && list.some((ticket) => ticket.id === current) ? current : (list[0]?.id ?? null),
        );
      })
      .catch((caught: unknown) => {
        if (ignore) {
          return;
        }
        setListError(messageOf(caught));
      });
    return () => {
      ignore = true;
    };
  }, [filter, reloadKey, storeSlug]);

  useEffect(() => {
    if (selectedId === null) {
      return;
    }
    let ignore = false;
    fetchTicket(storeSlug, selectedId)
      .then((loaded) => {
        if (!ignore) {
          setDetail(loaded);
          setDetailError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!ignore) {
          setDetailError(messageOf(caught));
        }
      });
    return () => {
      ignore = true;
    };
  }, [reloadKey, selectedId, storeSlug]);

  function selectTicket(ticketId: string) {
    setSelectedId(ticketId);
    setDraft("");
    setLifetime("none");
    setExpiresOn("");
    setReplyError(null);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const answer = draft.trim();
    if (selectedId === null || answer === "" || sending) {
      return;
    }
    setSending(true);
    setReplyError(null);
    setNotice(null);
    try {
      const result = await replyToTicket(
        storeSlug,
        selectedId,
        answer,
        lifetime,
        lifetime === "until" ? expiresOn : null,
      );
      setDraft("");
      setLifetime("none");
      setExpiresOn("");
      setNotice(savedNotice(result));
      // 미처리 목록에서는 방금 답한 문의가 빠지므로, 그 문의를 답변 완료에서 그대로 보여 준다.
      setFilter("resolved");
      setTickets(null);
      setReloadKey((current) => current + 1);
    } catch (caught: unknown) {
      setReplyError(messageOf(caught));
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) {
        setReloadKey((current) => current + 1);
      }
    } finally {
      setSending(false);
    }
  }

  const showing = detail !== null && detail.id === selectedId ? detail : null;
  const listEmpty = tickets !== null && tickets.length === 0;
  const showDetail = !listEmpty && (selectedId !== null || notice !== null || detailError !== null);

  return (
    <div className="owner">
      <section className="owner-list" aria-label="문의 목록">
        <h1>문의</h1>
        <div className="owner-filters" role="group" aria-label="상태 필터">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={filter === item.id ? "owner-filter owner-filter-active" : "owner-filter"}
              aria-pressed={filter === item.id}
              onClick={() => {
                if (item.id !== filter) {
                  setFilter(item.id);
                  setTickets(null);
                  setListError(null);
                }
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        {listError !== null && (
          <p className="chat-error" role="alert">
            {listError}
          </p>
        )}
        {tickets === null && listError === null && <p className="chat-empty">문의 목록을 불러오는 중입니다.</p>}
        {tickets !== null && tickets.length === 0 && (
          <p className="chat-empty">
            {filter === "open"
              ? "처리할 문의가 없습니다."
              : filter === "resolved"
                ? "답변한 문의가 없습니다."
                : "문의가 없습니다."}
          </p>
        )}
        {tickets !== null && tickets.length > 0 && (
          <ul className="ticket-list">
            {tickets.map((ticket) => (
              <li key={ticket.id}>
                <button
                  type="button"
                  className={ticket.id === selectedId ? "ticket-item ticket-item-active" : "ticket-item"}
                  aria-current={ticket.id === selectedId ? "true" : undefined}
                  onClick={() => selectTicket(ticket.id)}
                >
                  <span className={`badge badge-${ticket.status}`}>{STATUS_LABEL[ticket.status]}</span>
                  <span className="ticket-question">{ticket.question}</span>
                  <time dateTime={ticket.created_at}>{formatDateTime(ticket.created_at)}</time>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {showDetail && (
        <section className="owner-detail" aria-label="문의 상세">
          {notice !== null && (
            <p className="owner-notice" role="status">
              {notice}
            </p>
          )}
          {selectedId !== null && showing === null && detailError === null && (
            <p className="chat-empty">문의를 불러오는 중입니다.</p>
          )}
          {detailError !== null && showing === null && (
            <p className="chat-error" role="alert">
              {detailError}
            </p>
          )}
          {showing !== null && (
            <>
              <header className="owner-detail-head">
                <span className={`badge badge-${showing.status}`}>{STATUS_LABEL[showing.status]}</span>
                <span className="owner-reason">{REASON_LABEL[showing.escalation_reason]}</span>
                <time dateTime={showing.created_at}>{formatDateTime(showing.created_at)} 접수</time>
              </header>
              <h2 className="owner-question">{showing.question}</h2>
              {showing.status === "resolved" && showing.owner_answer !== null && (
                <div className="owner-answer">
                  <h3>등록된 답변</h3>
                  <p>{showing.owner_answer}</p>
                  <p className={validityClass(showing)}>{validityText(showing)}</p>
                  {showing.resolved_at !== null && (
                    <time dateTime={showing.resolved_at}>{formatDateTime(showing.resolved_at)} 등록</time>
                  )}
                </div>
              )}
              {showing.status === "open" && (
                <form className="owner-form" onSubmit={onSubmit}>
                  <fieldset className="owner-lifetime">
                    <legend>이 답의 저장</legend>
                    {LIFETIME_CHOICES.map((choice) => (
                      <label key={choice.id}>
                        <input
                          type="radio"
                          name="knowledge-lifetime"
                          value={choice.id}
                          checked={lifetime === choice.id}
                          disabled={sending}
                          onChange={() => setLifetime(choice.id)}
                        />
                        {choice.label}
                      </label>
                    ))}
                  </fieldset>
                  {lifetime === "until" && (
                    <label className="owner-until" htmlFor="owner-expires-on">
                      언제까지
                      <input
                        id="owner-expires-on"
                        type="date"
                        value={expiresOn}
                        min={todayInputValue()}
                        required
                        disabled={sending}
                        onChange={(event) => setExpiresOn(event.target.value)}
                      />
                    </label>
                  )}
                  {LIFETIME_NOTE[lifetime] !== null && <p className="chat-note">{LIFETIME_NOTE[lifetime]}</p>}
                  <label htmlFor="owner-answer">답변</label>
                  <textarea
                    id="owner-answer"
                    value={draft}
                    maxLength={MAX_ANSWER_LENGTH}
                    rows={4}
                    placeholder="고객에게 전달할 답변을 입력하세요"
                    disabled={sending}
                    onChange={(event) => setDraft(event.target.value)}
                  />
                  <div className="owner-form-bar">
                    <span>
                      {draft.trim().length}/{MAX_ANSWER_LENGTH}
                    </span>
                    <button
                      type="submit"
                      disabled={sending || draft.trim() === "" || (lifetime === "until" && expiresOn === "")}
                    >
                      {sending ? "등록 중" : "답변 등록"}
                    </button>
                  </div>
                  {replyError !== null && (
                    <p className="chat-error" role="alert">
                      {replyError}
                    </p>
                  )}
                </form>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}

function todayInputValue(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function messageOf(caught: unknown): string {
  if (caught instanceof ApiError) {
    return caught.message;
  }
  return "요청에 실패했습니다. 다시 시도해 주세요.";
}

function validityClass(ticket: TicketDetail): string {
  if (ticket.knowledge_active === false || knowledgeExpired(ticket)) {
    return "owner-validity owner-validity-ended";
  }
  if (ticket.knowledge_lifetime === "none" || ticket.knowledge_active === null) {
    return "owner-validity";
  }
  return "owner-validity owner-validity-open";
}

function validityText(ticket: TicketDetail): string {
  if (ticket.knowledge_active === false) {
    return "이 답은 더 이상 지식으로 쓰이지 않습니다.";
  }
  if (ticket.knowledge_lifetime === "none" || ticket.knowledge_active === null) {
    return "이 손님에게만 전달되었습니다.";
  }
  if (ticket.knowledge_expires_at !== null) {
    const until = formatValidity(ticket.knowledge_expires_at);
    if (knowledgeExpired(ticket)) {
      return until === ""
        ? "기한이 지나 이 답은 더 이상 지식으로 쓰이지 않습니다."
        : `${until}에 기한이 지나 더 이상 지식으로 쓰이지 않습니다.`;
    }
    return until === "" ? "기한이 있는 지식입니다." : `${until}까지 유효`;
  }
  return "기한 없이 유효";
}

function knowledgeExpired(ticket: TicketDetail): boolean {
  if (ticket.knowledge_expires_at === null) {
    return false;
  }
  const expiresAt = new Date(ticket.knowledge_expires_at).getTime();
  return !Number.isNaN(expiresAt) && expiresAt <= Date.now();
}

function formatValidity(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
