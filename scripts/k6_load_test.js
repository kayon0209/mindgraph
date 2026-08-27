// MindGraph 本地负载压测（k6）
// 目标：/api/v1/health（纯 web 层） + /api/v1/config/public（真实 container 链路）
// 运行：k6 run scripts/k6_load_test.js
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = 'http://127.0.0.1:8123';

export const options = {
  scenarios: {
    web_layer: {
      executor: 'constant-vus',
      vus: 50,
      duration: '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
  },
};

export default function () {
  // 纯 web 层（无 DB/索引访问）
  const h = http.get(`${BASE}/api/v1/health`);
  check(h, { 'health 200': (r) => r.status === 200 });

  // 真实 container 链路（读取索引状态/配置）
  const c = http.get(`${BASE}/api/v1/config/public`);
  check(c, { 'config 200': (r) => r.status === 200 });

  sleep(0.1); // 每个 VU 循环间间隔，避免打满单核
}
