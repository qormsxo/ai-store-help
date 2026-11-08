import { useEffect, useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";

import { fetchHealth, fetchStore, type Store } from "../api/client";

type BackendStatus = "checking" | "ok" | "down";
export type StoreState = { kind: "loading" } | { kind: "ready"; store: Store } | { kind: "missing" };

const STATUS_LABEL: Record<BackendStatus, string> = {
  checking: "서버 확인 중",
  ok: "서버 연결됨",
  down: "서버 연결 안 됨",
};

export default function Layout() {
  const { storeSlug } = useParams();
  const [status, setStatus] = useState<BackendStatus>("checking");
  const [storeState, setStoreState] = useState<StoreState>({ kind: "loading" });

  useEffect(() => {
    fetchHealth()
      .then(() => setStatus("ok"))
      .catch(() => setStatus("down"));
  }, []);

  useEffect(() => {
    if (storeSlug === undefined) {
      return;
    }
    let ignore = false;
    setStoreState({ kind: "loading" });
    fetchStore(storeSlug)
      .then((store) => {
        if (!ignore) {
          setStoreState({ kind: "ready", store });
        }
      })
      .catch(() => {
        if (!ignore) {
          setStoreState({ kind: "missing" });
        }
      });
    return () => {
      ignore = true;
    };
  }, [storeSlug]);

  const title = storeState.kind === "ready" ? storeState.store.name : "AI Store Help";

  return (
    <div className="app">
      <header className="app-header">
        <strong className="app-title">{title}</strong>
        <nav className="app-nav">
          <NavLink to={`/${storeSlug}/chat`}>고객 채팅</NavLink>
          <NavLink to={`/${storeSlug}/owner`}>사장 문의 관리</NavLink>
        </nav>
        <span className={`status status-${status}`}>{STATUS_LABEL[status]}</span>
      </header>
      {storeState.kind === "missing" && (
        <div className="store-banner" role="alert">
          매장 정보를 불러오지 못했습니다. 관리자에게 문의해 주세요.
        </div>
      )}
      <main className="app-main">
        <Outlet context={storeState} />
      </main>
    </div>
  );
}
