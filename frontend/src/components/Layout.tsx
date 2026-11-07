import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { fetchHealth } from "../api/client";

type BackendStatus = "checking" | "ok" | "down";

const STATUS_LABEL: Record<BackendStatus, string> = {
  checking: "서버 확인 중",
  ok: "서버 연결됨",
  down: "서버 연결 안 됨",
};

export default function Layout() {
  const [status, setStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    fetchHealth()
      .then(() => setStatus("ok"))
      .catch(() => setStatus("down"));
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <strong className="app-title">AI Store Help</strong>
        <nav className="app-nav">
          <NavLink to="/chat">고객 채팅</NavLink>
          <NavLink to="/owner">사장 문의 관리</NavLink>
        </nav>
        <span className={`status status-${status}`}>{STATUS_LABEL[status]}</span>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
