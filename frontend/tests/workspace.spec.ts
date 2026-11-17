import { expect, test, type Page } from '@playwright/test';

async function backend(page: Page, invalid = false) {
  let status = 'uploaded'; let revision = 0; let validated = false; let processed = 0;
  let preparing = false; let progressReads = 0;
  const files = [{ id: 'f1', name: 'customers.csv', rows: 2, status: 'mapped', approved_document: {}, document: { source: { columns: ['email', 'tenure'], row_count: 2 }, llm: { used: false, error: null }, summary: {}, mappings: ['email', 'tenure'].map(column => ({ source_column: column, target_table: 'customers', target_column: column, method: 'exact', status: 'suggested', confidence: null, requires: [], explanation: 'Canonical match' })) } }];
  const batch = () => ({ id: 'b1', source_id: 's1', status, revision, files, review: {}, report: status === 'validation_failed' ? { errors: { detail: 'Missing required identifier', field: 'customers.source_customer_id' } } : { counts: { customers: 2 }, warnings: [] }, created_at: '2026-11-17T10:00:00Z', segmentation: { pending: 0, completed: 0, failed: 0, superseded: 0 }, activity: { operation_id: null, stage: null, started_at: null } });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, code = 200) => route.fulfill({ status: code, json: body });
    const preparationJobs = () => ['churn', 'clv'].map(key => ({ id: `job-${key}`, segment_key: key,
      status: progressReads > 1 ? 'completed' : 'running', total: 2, processed: progressReads > 1 ? 2 : 1,
      predicted: 1, unavailable: 0, reused: 0, member_count: 1, error_code: '', updated_at: '2026-11-17T10:00:00Z' }));
    if (path === '/api/audiences/prepare/') { preparing = true; return json({ jobs: preparationJobs() }, 202); }
    if (path === '/api/audiences/jobs/') { if (preparing) progressReads += 1; return json({ jobs: preparing ? preparationJobs() : [] }); }
    if (path === '/api/ingestion/sources/') return json({ sources: [{ id: 's1', namespace: 'shop', name: 'My shop', currency: 'GBP', analytics_profile: 'retail' }] });
    if (path === '/api/mapping/targets/') return json({ targets: ['email', 'tenure'].map(column => ({ table: 'customers', column, kind: 'direct', requires: [] })) });
    if (path === '/api/ingestion/batches/' && route.request().method() === 'POST') return json(batch(), 201);
    if (path === '/api/ingestion/batches/') return json({ count: 1, limit: 10, offset: 0, results: [batch()] });
    if (path.endsWith('/events/')) return json({ results: [{ id: 1, stage: 'upload', code: 'upload.completed', level: 'success', message: 'CSV files uploaded and stored privately.', occurred_at: '2026-11-17T10:00:00Z', metadata: {} }], next_after: 1, has_more: false, status, activity: {} });
    if (path.endsWith('/map/')) { status = 'mapped'; revision = 1; return json(batch()); }
    if (path.endsWith('/review/')) { status = 'reviewed'; revision = 2; return json(batch()); }
    if (path.endsWith('/validate/')) { status = invalid ? 'validation_failed' : 'validated'; validated = !invalid; return json(batch(), invalid ? 422 : 200); }
    if (path.endsWith('/process/')) { processed += 1; expect(validated).toBeTruthy(); status = 'completed'; return json(batch()); }
    if (path === '/api/ingestion/batches/b1/') return json(batch());
    if (path.startsWith('/api/kpis/')) {
      const key = path.split('/')[3];
      const results: Record<string, any> = { total_customers: { customers: 2 }, total_revenue: { revenue: 200 }, total_orders: { purchase_orders: 2 }, average_order_value: { average_order_value: 100 }, revenue_by_month: [{ month: '2026-10-01', revenue: 80 }, { month: '2026-11-01', revenue: 120 }] };
      return json({ key, unit: key.includes('revenue') || key.includes('value') ? 'currency' : 'count', results: { shop: results[key] || {} }, sources: { shop: { name: 'My shop', currency: 'GBP' } }, unavailable: {}, note: '' });
    }
    const audiences = ['churn', 'clv'].map(key => ({ key, name: key, description: key === 'churn' ? 'Customer snapshot churn classification.' : 'Predicted future revenue.', state: 'ready', member_count: 1, total_customers: 2, predicted_count: 2, pending_jobs: 0, membership_configured: true, last_computed_at: '2026-11-17T10:00:00Z', campaign: { enabled: false, label: 'Create campaign', reason: 'Coming soon' }, note: 'Explicit saved memberships.' }));
    if (path === '/api/audiences/') return json({ audiences });
    if (path === '/api/audiences/churn/') return json(audiences[0]);
    if (path === '/api/audiences/churn/customers/') return json({ count: 1, limit: 20, offset: 0, results: [{ customer_id: 'c1', email: 'one@example.com', source_customer_id: 'shop:001', assigned_at: '2026-11-17', prediction: { status: 'predicted', result: { prediction: { churn_probability: .75 } } } }] });
    if (path === '/api/customers/') return json({ count: 1, limit: 20, offset: 0, results: [{ id: 'c1', email: 'one@example.com', source_customer_id: 'shop:001', source: { code: 'shop', name: 'My shop' }, created_at: '2026-11-17' }] });
    return json({ detail: 'No test fixture for this endpoint' }, 404);
  });
  return { processed: () => processed };
}
async function connect(page: Page) {
  await page.goto('/'); await page.getByRole('button', { name: 'Connect your workspace', exact: true }).click();
  await page.getByLabel('Workspace token').fill('temporary-test-token');
  await page.getByRole('button', { name: 'Connect workspace', exact: true }).last().click();
  await expect(page.getByRole('dialog')).toBeHidden();
}

test('blue-and-white overview, responsive shell, and private connection', async ({ page }) => {
  await backend(page); await connect(page);
  await expect(page.getByRole('heading', { name: 'A clearer view of your customers.' })).toBeVisible();
  await expect(page.getByText('GBP 200.00')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Create campaign' })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  await page.screenshot({ path: 'test-results/dashboard-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
  await page.getByRole('link', { name: 'Customers', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Your customers', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/customers-mobile.png', fullPage: true });
});

test('upload maps automatically and cannot save without preprocessing', async ({ page }) => {
  const state = await backend(page); await connect(page);
  await page.getByRole('link', { name: 'Upload data', exact: true }).click();
  await page.getByLabel('Source', { exact: true }).selectOption('s1');
  await page.getByLabel('Choose CSV files').setInputFiles({ name: 'customers.csv', mimeType: 'text/csv', buffer: Buffer.from('email,tenure\none@example.com,12\ntwo@example.com,24') });
  await page.getByRole('button', { name: 'Upload & detect data' }).click();
  await expect(page.getByRole('heading', { name: 'customers.csv', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Approve mapped fields' }).click();
  await page.getByRole('button', { name: 'Approve & preprocess' }).click();
  await expect(page.getByRole('heading', { name: 'Your data has been saved.' })).toBeVisible();
  expect(state.processed()).toBe(1);
  await page.getByRole('link', { name: 'View customers', exact: true }).last().click();
  await expect(page.getByText('one@example.com').first()).toBeVisible();
});

test('failed validation never calls save or shows a success animation', async ({ page }) => {
  const state = await backend(page, true); await connect(page);
  await page.getByRole('link', { name: 'Upload data', exact: true }).click();
  await page.getByLabel('Source', { exact: true }).selectOption('s1');
  await page.getByLabel('Choose CSV files').setInputFiles({ name: 'customers.csv', mimeType: 'text/csv', buffer: Buffer.from('email,tenure\none@example.com,12') });
  await page.getByRole('button', { name: 'Upload & detect data' }).click();
  await page.getByRole('button', { name: 'Approve mapped fields' }).click();
  await page.getByRole('button', { name: 'Approve & preprocess' }).click();
  await expect(page.getByText('Missing required identifier')).toBeVisible();
  expect(state.processed()).toBe(0);
  await expect(page.getByRole('heading', { name: 'Your data has been saved.' })).toHaveCount(0);
});

test('audience cards open actual member lists and campaigns remain disabled', async ({ page }) => {
  await backend(page); await connect(page);
  await page.getByRole('link', { name: 'Audiences', exact: false }).first().click();
  await expect(page.getByRole('button', { name: 'Create campaign' })).toHaveCount(2);
  await expect(page.getByRole('button', { name: 'Create campaign' }).first()).toBeDisabled();
  await page.screenshot({ path: 'test-results/audiences-desktop.png', fullPage: true });
  await page.getByRole('link').filter({ hasText: 'Churn risk' }).last().click();
  await expect(page.getByRole('link', { name: 'one@example.com shop:001' })).toBeVisible();
  await expect(page.getByText('75.0%')).toBeVisible();
});

test('prepare audiences queues once and polls independent model progress to completion', async ({ page }) => {
  await backend(page); await connect(page);
  await page.getByRole('link', { name: 'Audiences', exact: false }).first().click();
  await page.getByRole('button', { name: 'Prepare audiences', exact: true }).click();
  await page.getByLabel('Minimum predicted 90-day revenue for the CLV audience (GBP)').fill('200');
  const requests: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/audiences/prepare/')) requests.push(request.method()); });
  await page.getByRole('dialog').getByRole('button', { name: 'Prepare audiences', exact: true }).click();
  await expect(page.getByText('Preparing audiences in the background.', { exact: false })).toBeVisible();
  await expect(page.getByText('Both audiences finished processing.', { exact: false })).toBeVisible({ timeout: 12000 });
  expect(requests).toEqual(['POST']);
  await expect(page.getByRole('button', { name: 'Prepare audiences', exact: true })).toBeEnabled();
});
