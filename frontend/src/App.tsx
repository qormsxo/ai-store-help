import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import CustomerChatPage from "./pages/CustomerChatPage";
import OwnerTicketsPage from "./pages/OwnerTicketsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<CustomerChatPage />} />
        <Route path="/owner" element={<OwnerTicketsPage />} />
        <Route path="*" element={<p>페이지를 찾을 수 없습니다.</p>} />
      </Route>
    </Routes>
  );
}
