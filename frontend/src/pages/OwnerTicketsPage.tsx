import { type FormEvent, useEffect, useState } from "react";

import {
  ApiError,
  fetchTicket,
  fetchTickets,
  replyToTicket,
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

export default function OwnerTicketsPage() {
  const [filter, setFilter] = useState<Filter>("open");
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let ignore = false;
    fetchTickets(filter === "all" ? undefined : filter)
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
  }, [filter, reloadKey]);

  useEffect(() => {
    if (selectedId === null) {
      return;
    }
    let ignore = false;
    fetchTicket(selectedId)
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
  }, [selectedId, reloadKey]);

  function selectTicket(ticketId: string) {
    setSelectedId(ticketId);
    setDraft("");
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
      const result = await replyToTicket(selectedId, answer);
      setDraft("");
      setNotice(
        result.superseded_knowledge_id === null
          ? "답변을 등록했습니다. 고객 대화에 전달되고 지식으로 저장되었습니다."
          : "답변을 등록했습니다. 같은 질문의 이전 지식을 이 답변으로 바꿨습니다.",
      );
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
                  {showing.resolved_at !== null && (
                    <time dateTime={showing.resolved_at}>{formatDateTime(showing.resolved_at)} 등록</time>
                  )}
                </div>
              )}
              {showing.status === "open" && (
                <form className="owner-form" onSubmit={onSubmit}>
                  <label htmlFor="owner-answer">답변</label>
                  <p className="chat-note">
                    고객 대화에 AI 답변으로 전달되고, 이후 비슷한 질문에 쓰이는 지식으로 저장됩니다.
                  </p>
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
                    <button type="submit" disabled={sending || draft.trim() === ""}>
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

function messageOf(caught: unknown): string {
  if (caught instanceof ApiError) {
    return caught.message;
  }
  return "요청에 실패했습니다. 다시 시도해 주세요.";
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
