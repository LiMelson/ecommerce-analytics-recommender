let csrf = '';
let unauthorized = () => {};
const base = import.meta.env.VITE_API_BASE || '/api/v1';

export function setSession(value, handler) {
  csrf = value || '';
  if (handler) unauthorized = handler;
}
export function queryString(values) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== '' && value !== undefined && value !== null)
      query.set(key, value);
  return query.size ? `?${query}` : '';
}
export async function api(path, { method = 'GET', body, blob = false } = {}) {
  const headers = { Accept: 'application/json' };
  if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers,
      body,
      credentials: 'include',
    });
  } catch {
    throw new Error('无法连接后端，请确认 API 服务已经启动。');
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== '/auth/login') unauthorized();
    const error = new Error(
      payload.error?.message || `请求失败（${response.status}）`,
    );
    error.status = response.status;
    error.details = payload.error?.details;
    error.requestId = payload.request_id;
    throw error;
  }
  return blob ? response.blob() : response.json();
}
export function saveBlob(name, content) {
  const url = URL.createObjectURL(
    content instanceof Blob
      ? content
      : new Blob([content], { type: 'text/csv;charset=utf-8' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
