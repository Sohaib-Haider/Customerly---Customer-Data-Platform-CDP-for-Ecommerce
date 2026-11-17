import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError, query } from '../api';
import { money } from '../format';
import { buildReview } from '../pages/Review';

afterEach(() => vi.unstubAllGlobals());
describe('backend integration contracts', () => {
  it('identifies non-JSON backend responses by status and endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500,
      json: async () => { throw new SyntaxError('HTML response'); } }));
    await expect(new ApiClient('test').post('/api/audiences/prepare/', {}))
      .rejects.toThrow('HTTP 500) for /api/audiences/prepare/');
  });
  it('sends the in-memory bearer token and preserves multipart boundaries', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({ id: 'batch' }) }); vi.stubGlobal('fetch', fetcher);
    const form = new FormData(); form.append('files', new File(['id\n001'], 'customers.csv'));
    await new ApiClient('temporary-test-token').post('/api/ingestion/batches/', form);
    const request = fetcher.mock.calls[0][1];
    expect(request.headers.get('Authorization')).toBe('Bearer temporary-test-token');
    expect(request.headers.has('Content-Type')).toBe(false);
    expect(request.body).toBe(form);
    expect(localStorage.length).toBe(0);
  });
  it('does not retry a failed write and exposes structured validation data', async () => {
    const body = { id: 'batch', status: 'validation_failed', report: { errors: { field: 'orders.customer_id' } } };
    const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => body }); vi.stubGlobal('fetch', fetcher);
    const result = new ApiClient('test').post('/api/ingestion/batches/batch/validate/', { revision: 2 });
    await expect(result).rejects.toMatchObject({ status: 422, body });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('handles token expiry explicitly', async () => {
    const expired = vi.fn(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ detail: 'expired' }) }));
    await expect(new ApiClient('test', expired).get('/api/customers/')).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledOnce();
  });
  it('encodes searches and keeps money formatting exact', () => {
    expect(query({ search: 'name+tag@example.com', offset: 0, source: '' })).toContain('search=name%2Btag%40example.com');
    expect(money('9007199254740993.25', 'GBP')).toBe('GBP 9,007,199,254,740,993.25');
  });
  it('only submits explicitly approved fields and preserves occurrence policy', () => {
    const review = buildReview([{ file_id: 'file', fields: [{ source: 'mail', target: 'customers.email', decision: 'approved' }, { source: 'extra', target: 'customers.tenure', decision: 'pending' }, { source: 'ignored', target: '', decision: 'ignored' }],
      bindings: [{ source_column: 'client', target: 'orders.customer_id' }], calculations: [], identity_columns: { order_items: ['line'], events: [] }, deduplicate: false }],
      { churn: { enabled: true, update_memberships: false, membership_threshold: '', observation_date: '' }, clv: { enabled: false, update_memberships: false, membership_threshold: '', observation_date: '' } });
    expect(review.files[0].set_targets).toEqual([{ source_column: 'mail', target_table: 'customers', target_column: 'email' }]);
    expect(review.files[0].reject).toEqual(['ignored']);
    expect(review.files[0].identity_columns).toEqual({ order_items: ['line'] });
    expect(review.segment_options.churn).not.toHaveProperty('membership_threshold');
    expect(review.files[0].deduplicate).toBe(false);
  });
});
