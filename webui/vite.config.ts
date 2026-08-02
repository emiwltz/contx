import { defineConfig } from "vite";

export default defineConfig({
  base: "/",
  build: {
    outDir: "../contx/web/static",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8711",
    },
  },
});
