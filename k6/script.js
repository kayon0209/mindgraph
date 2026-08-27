import http from 'k6/http';
import { check } from 'k6';

const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';
const apiKey = __ENV.API_KEY || '';

export const options = {
  vus: Number(__ENV.VUS || 1),
  duration: __ENV.DURATION || '10s',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<800'],
  },
};

export default function () {
  const response = http.get(`${baseUrl}/api/v1/health`, {
    headers: apiKey ? { 'X-API-Key': apiKey } : {},
    tags: { endpoint: 'health' },
  });
  check(response, { 'health endpoint responds': (value) => value.status === 200 });
}
