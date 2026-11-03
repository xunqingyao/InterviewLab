import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const env = loadEnv(mode, root, "WEB_");
  const target = env.WEB_API_TARGET || "http://127.0.0.1:8000";
  return {
    envDir: root,
    plugins: [react()],
    server: {
      host: env.WEB_HOST || "127.0.0.1",
      port: Number(env.WEB_PORT || 5173),
      strictPort: true,
      proxy: { "/api": target, "/health": target, "/auth": target },
    },
  };
});
