export class ApiError extends Error {
  constructor(public status: number, public body: any) {
    super(readError(body, status));
    this.name = 'ApiError';
  }
}

export function readError(body: unknown, status?: number): string {
  if (status === 401) return 'Your workspace token is missing or expired. Reconnect with a fresh store-user token.';
  if (typeof body === 'string') return body.slice(0, 1000);
  if (Array.isArray(body)) return body.map(item => readError(item)).join(' ');
  if (body && typeof body === 'object') return Object.entries(body).map(([key, value]) => `${key === 'detail' ? '' : `${key.replaceAll('_', ' ')}: `}${readError(value)}`).join(' ').slice(0, 1500);
  return `The request could not complete${status ? ` (${status})` : ''}. Please try again.`;
}

/** Renews the workspace token after a 401: a new token, null if renewal failed, undefined if not available. */
export type Renew = () => Promise<string | null | undefined>;

export class ApiClient {
  constructor(private token: string, private onExpired: () => void = () => {}, private renew?: Renew) {}

  async request<T = any>(path: string, options: RequestInit = {}, retried = false): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set('Accept', 'application/json');
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    let response: Response;
    try { response = await fetch(path, { ...options, headers, cache: 'no-store' }); }
    catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error;
      throw new Error('Cannot reach the backend. Check that Django is running and the development proxy is configured.');
    }
    if (response.status === 401 && !retried) {
      // Signed-in users: fetch a fresh workspace token and retry this request once.
      const fresh = this.renew ? await this.renew().catch(() => null) : undefined;
      if (fresh) return new ApiClient(fresh, this.onExpired).request<T>(path, options, true);
      if (fresh === undefined) this.onExpired(); // developer-token mode: ask for a new token
    } else if (response.status === 401) this.onExpired();
    const body = await response.json().catch(() => {
      throw new ApiError(response.status, { detail: `The backend returned a non-JSON response (HTTP ${response.status}) for ${path.split('?')[0]}. Restart Django and check its terminal for errors. If the status is 404, confirm the updated backend routes are running.` });
    });
    if (!response.ok) throw new ApiError(response.status, body);
    return body as T;
  }
  get<T = any>(path: string, signal?: AbortSignal) { return this.request<T>(path, { signal }); }
  post<T = any>(path: string, body: unknown) {
    return this.request<T>(path, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body) });
  }
  async download(path: string, filename: string) {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${this.token}` }, cache: 'no-store' });
    if (response.status === 401) this.onExpired();
    if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => ({})));
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function query(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)); });
  return params.toString() ? `?${params}` : '';
}
