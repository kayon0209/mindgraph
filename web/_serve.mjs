// MindGraph 极轻静态服务器：serve dist + /api 代理到 8000（含 SSE）
import http from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const ROOT = new URL("./dist/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const API = "http://127.0.0.1:8000";
const PORT = 5174;
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
  ".jpg": "image/jpeg", ".ico": "image/x-icon", ".woff2": "font/woff2", ".map": "application/json",
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  if (url.pathname.startsWith("/api/")) {
    const proxyReq = http.request(
      API + url.pathname + url.search,
      { method: req.method, headers: { ...req.headers, host: "127.0.0.1:8000" } },
      (proxied) => {
        res.writeHead(proxied.statusCode, proxied.headers);
        proxied.pipe(res);
      },
    );
    proxyReq.on("error", (err) => {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "api_unreachable", detail: String(err) }));
    });
    req.pipe(proxyReq);
    return;
  }
  let filePath = join(ROOT, normalize(url.pathname).replace(/^([\\/])+/, ""));
  if (!existsSync(filePath) || statSync(filePath).isDirectory()) filePath = join(ROOT, "index.html");
  const type = MIME[extname(filePath)] ?? "application/octet-stream";
  res.writeHead(200, { "content-type": type });
  createReadStream(filePath).pipe(res);
});

server.listen(PORT, "127.0.0.1", () => console.log(`mindgraph web on http://127.0.0.1:${PORT}`));
