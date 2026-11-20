import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Each node is a separate backend process. The dashboard reaches node X at /n/X/...
const NODES: Record<string, number> = { hub: 7400, alpha: 7401, bravo: 7402, charlie: 7403 };
const proxy = Object.fromEntries(
  Object.entries(NODES).map(([id, port]) => [
    `/n/${id}`,
    { target: `http://127.0.0.1:${port}`, changeOrigin: true, rewrite: (p: string) => p.replace(`/n/${id}`, "") },
  ]),
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, host: "127.0.0.1", proxy },
  preview: { port: 4173, host: "127.0.0.1", proxy },
});
