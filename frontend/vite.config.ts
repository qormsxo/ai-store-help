import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// 브라우저는 항상 /api로 호출하고, 개발 서버가 백엔드로 프록시한다 (CORS 불필요).
// Docker에서는 VITE_PROXY_TARGET=http://backend:8000 으로 주입된다.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const proxyTarget = env.VITE_PROXY_TARGET ?? "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      proxy: {
        "/api": { target: proxyTarget, changeOrigin: true },
      },
    },
  };
});
