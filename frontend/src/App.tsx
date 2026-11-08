import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { fetchStore } from "./api/client";
import Layout from "./components/Layout";
import CustomerChatPage from "./pages/CustomerChatPage";
import OwnerTicketsPage from "./pages/OwnerTicketsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<DefaultStoreRedirect to="chat" />} />
      <Route path="/chat" element={<DefaultStoreRedirect to="chat" />} />
      <Route path="/owner" element={<DefaultStoreRedirect to="owner" />} />
      <Route path="/:storeSlug" element={<Layout />}>
        <Route index element={<Navigate to="chat" replace />} />
        <Route path="chat" element={<StorePage page="chat" />} />
        <Route path="owner" element={<StorePage page="owner" />} />
      </Route>
      <Route path="*" element={<p>페이지를 찾을 수 없습니다.</p>} />
    </Routes>
  );
}

function DefaultStoreRedirect({ to }: { to: "chat" | "owner" }) {
  const [slug, setSlug] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetchStore()
      .then((store) => setSlug(store.slug))
      .catch(() => setFailed(true));
  }, []);

  if (failed) {
    return <p>매장 정보를 불러오지 못했습니다.</p>;
  }
  if (slug === null) {
    return null;
  }
  return <Navigate to={`/${slug}/${to}`} replace />;
}

function StorePage({ page }: { page: "chat" | "owner" }) {
  const { storeSlug } = useParams();
  if (page === "chat") {
    return <CustomerChatPage key={storeSlug} />;
  }
  return <OwnerTicketsPage key={storeSlug} />;
}
