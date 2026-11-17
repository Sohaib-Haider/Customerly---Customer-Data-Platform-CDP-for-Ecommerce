import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { Activity, ArrowLeft, ArrowRight, CalendarDays, ChevronRight, FileText, Mail, Phone, RefreshCw, Search, ShoppingBag, Users } from 'lucide-react';
import { useResource, useDebounced } from '../hooks';
import { query } from '../api';
import { date, display, label, money, number } from '../format';
import { Avatar, Badge, ConnectionRequired, Empty, ErrorNotice, Loading, Modal, PageHeader, Pagination, Status } from '../components/ui';
import { Donut } from '../components/charts';
import type { Page, Row, Source } from '../types';

export function Customers() {
  const [params] = useSearchParams(); const [search, setSearch] = useState(params.get('search') || ''); const [source, setSource] = useState(''); const [offset, setOffset] = useState(0); const term = useDebounced(search);
  useEffect(() => { setSearch(params.get('search') || ''); setOffset(0); }, [params]);
  const customers = useResource<Page>(`/api/customers/${query({ search: term, source, offset, limit: 20 })}`);
  const sources = useResource<{ sources: Source[] }>('/api/ingestion/sources/');
  const sourceOptions: Record<string, string> = { ret: 'Online Retail II', dnb: 'Dunnhumby', chr: 'Churn snapshots', evb: 'Behavior events', msg: 'Messaging', cpn: 'Coupons' };
  sources.data?.sources.forEach(item => { sourceOptions[item.namespace] = item.name; });
  customers.data?.results.forEach(item => { sourceOptions[item.source?.code] = item.source?.name || item.source?.code; });
  return <><PageHeader eyebrow="THE PEOPLE BEHIND YOUR DATA" title="Your customers" description="One connected view of every customer in your store."><Link className="button primary" to="/uploads/new"><Users size={17} />Import customers</Link></PageHeader><ConnectionRequired><section className="panel directory"><div className="directory-toolbar"><div className="search-field"><Search size={17} /><input aria-label="Search customer directory" value={search} placeholder="Search by email, phone or source ID…" onChange={event => { setSearch(event.target.value); setOffset(0); }} /></div><div className="filter-group"><select aria-label="Customer source" value={source} onChange={event => { setSource(event.target.value); setOffset(0); }}><option value="">All sources</option>{Object.entries(sourceOptions).filter(([code]) => code && code !== 'undefined').map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select><button className="icon-button" aria-label="Refresh customers" onClick={customers.refresh}><RefreshCw size={17} /></button></div></div><ErrorNotice error={customers.error} retry={customers.refresh} />{customers.loading && !customers.data ? <Loading /> : customers.data?.results.length ? <><div className="table-scroll"><table><thead><tr><th>Customer</th><th>Source</th><th>Email</th><th>Phone</th><th>Added to store</th><th /></tr></thead><tbody>{customers.data.results.map(customer => <tr key={customer.id}><td><Link className="customer-link" to={`/customers/${customer.id}`}><Avatar name={customer.email || customer.source_customer_id} /><span><b>{customer.email || customer.source_customer_id}</b><small>{customer.source_customer_id}</small></span></Link></td><td><Badge tone="blue">{customer.source?.name || customer.source?.code || 'Unknown'}</Badge></td><td>{customer.email || <span className="muted">Not available</span>}</td><td>{customer.phone_number || <span className="muted">Not available</span>}</td><td>{date(customer.created_at)}</td><td><Link className="icon-button" aria-label={`Open ${customer.source_customer_id}`} to={`/customers/${customer.id}`}><ArrowRight size={17} /></Link></td></tr>)}</tbody></table></div><Pagination count={customers.data.count} limit={20} offset={offset} onChange={setOffset} /></> : <Empty title={search ? 'No customers match this search' : 'Your customers will feel right at home'} action={!search && <Link className="button primary" to="/uploads/new">Upload customer data<ArrowRight size={16} /></Link>}>{search ? 'Try a source ID, email address or phone number.' : 'Import data to begin building a connected customer view.'}</Empty>}</section></ConnectionRequired></>;
}

function Prediction({ item }: { item: Row }) {
  const result = item.result || {}; const prediction = result.prediction; const isChurn = item.segment_key === 'churn';
  return <article className={`prediction-card ${isChurn ? 'blue' : 'purple'}`}><div className="section-heading"><h3>{isChurn ? 'Churn risk' : 'Future high-value / CLV'}</h3><Status value={item.status} /></div>{item.status === 'predicted' && prediction ? <><strong>{isChurn ? `${number(Number(prediction.churn_probability) * 100, 1)}%` : money(prediction.predicted_revenue, prediction.currency || result.metadata?.currency || 'GBP')}</strong><p>{isChurn ? 'Saved churn probability' : `Predicted positive purchase revenue · next ${prediction.horizon_days || 90} days`}</p>{isChurn && <div className="probability-track"><i style={{ width: `${Math.max(0, Math.min(100, Number(prediction.churn_probability) * 100))}%` }} /></div>}</> : <p className="prediction-reasons">{result.reasons?.map((reason: Row) => reason.message).join(' ') || 'No usable prediction is stored.'}</p>}<small>Computed {date(item.computed_at, true)}{item.observation_date ? ` · Cutoff ${date(item.observation_date)}` : ''}</small><details><summary>Model & eligibility details</summary><dl>{Object.entries(result.metadata || {}).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>)}</dl>{result.eligibility && <p className="small">Eligibility: {result.eligibility.eligible ? 'Eligible at scoring time' : 'Unavailable at scoring time'}</p>}</details></article>;
}

function OrderModal({ customerId, orderId, onClose }: { customerId: string; orderId: string; onClose: () => void }) {
  const [offset, setOffset] = useState(0); const resource = useResource<Row>(`/api/customers/${customerId}/orders/${orderId}/${query({ offset, limit: 20 })}`);
  return <Modal title="Order details" onClose={onClose} wide><ErrorNotice error={resource.error} retry={resource.refresh} />{resource.loading && <Loading />}{resource.data && <><div className="order-summary"><div><span className="eyebrow">SOURCE ORDER</span><h3>{resource.data.order.source_order_id}</h3><p>{date(resource.data.order.order_date)}</p></div><div><span className="eyebrow">NET REVENUE</span><h2>{money(resource.data.order.net_revenue, resource.data.order.source?.currency)}</h2></div></div><div className="table-scroll"><table><thead><tr><th>Product</th><th>Quantity</th><th>Original unit price</th><th>Recorded sales</th><th>Discount fields</th></tr></thead><tbody>{resource.data.items.results.map((item: Row) => <tr key={item.id}><td><b>{item.product_name || item.source_product_id || 'Unnamed product'}</b><small className="block muted">{item.brand || item.category || item.source_product_id}</small></td><td>{number(item.quantity)}</td><td>{item.unit_price === null ? 'Not recorded' : money(item.unit_price, resource.data!.order.source?.currency)}</td><td>{money(item.sales_value, resource.data!.order.source?.currency)}</td><td><small>Retail {number(item.retail_disc, 2)} · Coupon {number(item.coupon_disc, 2)}<br />Match {number(item.coupon_match_disc, 2)} · Other {number(item.other_discount, 2)}</small></td></tr>)}</tbody></table></div><Pagination count={resource.data.items.count} offset={offset} limit={20} onChange={setOffset} /><p className="small muted">Values are shown as stored. Discounts are not subtracted from recorded sales a second time.</p></>}</Modal>;
}

function History({ customerId, kind, currency }: { customerId: string; kind: string; currency?: string | null }) {
  const [offset, setOffset] = useState(0); const [order, setOrder] = useState<string | null>(null); const history = useResource<Page>(`/api/customers/${customerId}/${kind}/${query({ offset, limit: 20 })}`);
  const columns = kind === 'orders' ? ['Order', 'Date', 'Recorded sales', 'Lines', ''] : kind === 'events' ? ['Event', 'When', 'Session', 'Category / brand', 'Recorded price'] : kind === 'messages' ? ['Message', 'Sent', 'Channel', 'Opened', 'Clicked', 'Purchase flag'] : ['Coupon', 'Campaign', 'Redeemed', 'Recorded'];
  return <section className="panel"><div className="section-heading"><h2>{label(kind)} history</h2><Badge>{history.data?.count ?? '—'} records</Badge></div><ErrorNotice error={history.error} retry={history.refresh} />{history.loading && !history.data ? <Loading /> : history.data?.results.length ? <><div className="table-scroll"><table><thead><tr>{columns.map((column, i) => <th key={i}>{column}</th>)}</tr></thead><tbody>{history.data.results.map(item => <tr key={item.id}>{kind === 'orders' ? <><td><b>{item.source_order_id}</b></td><td>{date(item.order_date)}</td><td>{money(item.net_revenue, item.source?.currency || currency)}</td><td>{number(item.line_count)}</td><td><button className="text-button" onClick={() => setOrder(item.id)}>View items<ArrowRight size={14} /></button></td></> : kind === 'events' ? <><td><Badge tone="blue">{label(item.event_type)}</Badge></td><td>{date(item.event_timestamp, true)}</td><td>{item.session_id}</td><td>{item.category_code || item.brand || 'Not recorded'}</td><td>{item.price === null ? 'Not recorded' : money(item.price, currency)}</td></> : kind === 'messages' ? <><td>{item.source_message_id}</td><td>{date(item.sent_at, true)}</td><td><Badge tone="purple">{item.channel_norm}</Badge></td><td>{display(item.is_opened)}</td><td>{display(item.is_clicked)}</td><td>{display(item.is_purchased)}</td></> : <><td>{item.coupon_id}</td><td>{item.campaign_id}</td><td>{display(item.redemption_status)}</td><td>{date(item.created_at, true)}</td></>}</tr>)}</tbody></table></div><Pagination count={history.data.count} offset={offset} limit={20} onChange={setOffset} /></> : <Empty title={`No ${kind} records available`}>This means no stored evidence in the available data, not proof that the customer has never taken that action.</Empty>}{order && <OrderModal customerId={customerId} orderId={order} onClose={() => setOrder(null)} />}</section>;
}

function recorded(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.some(recorded);
  if (typeof value === 'object') return Object.values(value).some(recorded);
  return true;
}

export function CustomerProfile() {
  const { id = '' } = useParams();
  const profile = useResource<Row>(`/api/customers/${id}/360/`);
  const [tab, setTab] = useState('overview');
  const data = profile.data;
  const customer = data?.customer;
  const name = customer?.email || customer?.source_customer_id || 'Customer profile';
  const purchases = (data?.purchases?.by_source || []).filter((item: Row) => Number(item.recorded_orders ?? item.purchase_orders ?? 0) > 0);
  const events = (data?.activity?.events || []).filter((item: Row) => Number(item.count) > 0);
  const messages = (data?.activity?.messages || []).filter((item: Row) => Number(item.sent) > 0);
  const coupons = data?.activity?.coupons;
  const hasCoupons = Number(coupons?.recorded) > 0;
  const memberships = data?.segments?.memberships || [];
  const predictions = (data?.segments?.predictions || []).filter((item: Row) => {
    const prediction = item.result?.prediction;
    const value = item.segment_key === 'churn' ? prediction?.churn_probability : prediction?.predicted_revenue;
    return item.status === 'predicted' && recorded(value) && Number.isFinite(Number(value));
  });
  const attributes = Object.entries(customer || {}).filter(([key, value]) =>
    !['id', 'store_id', 'email', 'phone_number', 'created_at', 'updated_at'].includes(key) && recorded(value));
  const snapshot = data?.feature_snapshot?.data;
  const features = Object.entries(snapshot || {}).filter(([key, value]) =>
    !['customer_id', 'store_id', 'computed_at'].includes(key) && recorded(value));
  const tabs = ['overview', ...(purchases.length ? ['orders'] : []), ...(events.length ? ['events'] : []),
    ...(messages.length ? ['messages'] : []), ...(hasCoupons ? ['coupons'] : [])];
  const selectedTab = tabs.includes(tab) ? tab : 'overview';

  return <>
    <Link className="back-link" to="/customers"><ArrowLeft size={15} />All customers</Link>
    <PageHeader eyebrow="CUSTOMER 360" title="Every interaction. One customer." description="A connected view of their available history, preferences and predictions." />
    <ConnectionRequired>
      <ErrorNotice error={profile.error} retry={profile.refresh} />
      {profile.loading && !data && <Loading />}
      {data && <>
        <section className="panel profile-header">
          <div className="profile-identity"><Avatar name={name} large /><div>
            {(data.source.name || data.source.code) && <Badge tone="blue">{data.source.name || data.source.code}</Badge>}
            <h2>{name}</h2><span className="small muted">{customer.source_customer_id}</span>
          </div></div>
          <div className="profile-contact">
            {customer.email && <span><Mail size={15} />{customer.email}</span>}
            {customer.phone_number && <span><Phone size={15} />{customer.phone_number}</span>}
            {customer.created_at && <span><CalendarDays size={15} />Added {date(customer.created_at)}</span>}
          </div>
          <button className="icon-button" aria-label="Refresh customer profile" onClick={profile.refresh}><RefreshCw size={17} /></button>
        </section>
        <div className="tabs" role="tablist" aria-label="Customer sections">
          {tabs.map(item => <button role="tab" aria-selected={selectedTab === item} className={selectedTab === item ? 'active' : ''} onClick={() => setTab(item)} key={item}>{item === 'events' ? 'Activity' : label(item)}</button>)}
        </div>
        {selectedTab === 'overview' ? <>
          {(purchases.length > 0 || events.length > 0) && <div className="profile-summary-grid available-profile-grid">
            {purchases.map((summary: Row) => <section className="panel purchase-summary" key={summary.source.code}>
              <div className="section-heading"><h3>Actual purchase history</h3><Badge tone="teal">{summary.source.name || summary.source.code}</Badge></div>
              <div className="purchase-stats">
                {recorded(summary.observed_net_revenue) && <div><span>Observed net revenue</span><strong>{money(summary.observed_net_revenue, summary.source.currency)}</strong></div>}
                {recorded(summary.purchase_orders) && <div><span>Purchase orders</span><strong>{number(summary.purchase_orders)}</strong></div>}
                {recorded(summary.average_order_value) && <div><span>Average order value</span><strong>{money(summary.average_order_value, summary.source.currency)}</strong></div>}
              </div>
              {(summary.first_purchase_date || summary.last_purchase_date) && <div className="purchase-dates">
                {summary.first_purchase_date && <span>First purchase <b>{date(summary.first_purchase_date)}</b></span>}
                {summary.last_purchase_date && <span>Last purchase <b>{date(summary.last_purchase_date)}</b></span>}
              </div>}
              <p className="small muted">Actual values over the available data window, including nonpositive orders. Future predictions appear separately.</p>
            </section>)}
            {events.length > 0 && <section className="panel"><div className="section-heading"><h3>Behavior at a glance</h3><Activity size={18} className="muted" /></div><Donut title="events" items={events.map((item: Row) => ({ name: label(item.event_type), value: Number(item.count) }))} /></section>}
          </div>}
          {predictions.length > 0 && <>
            <div className="section-heading"><div><h2>Predictive insights</h2><p>Saved model outputs and computation times</p></div></div>
            <div className="prediction-grid available-profile-grid">{predictions.map((item: Row) => <Prediction key={item.segment_id} item={item} />)}</div>
          </>}
          {(memberships.length > 0 || messages.length > 0 || hasCoupons) && <div className="profile-bottom available-profile-grid">
            {memberships.length > 0 && <section className="panel"><div className="section-heading"><h2>Audience memberships</h2><Users size={18} className="muted" /></div>
              {memberships.map((item: Row) => <div className="membership-row" key={item.segment_id}><span><b>{item.name}</b><small>Assigned {date(item.assigned_at, true)}</small></span><Badge tone="green">Member</Badge></div>)}
            </section>}
            {(messages.length > 0 || hasCoupons) && <section className="panel"><div className="section-heading"><h2>{messages.length && hasCoupons ? 'Messaging & coupons' : messages.length ? 'Messaging' : 'Coupons'}</h2><Mail size={18} className="muted" /></div>
              {messages.map((item: Row) => <div className="engagement-row" key={item.channel}><Badge tone="purple">{item.channel}</Badge><span>{item.sent} sent</span>{recorded(item.opened) && <span>{item.opened} opened</span>}{recorded(item.clicked) && <span>{item.clicked} clicked</span>}</div>)}
              {hasCoupons && <p className="small muted">Coupon records: {number(coupons.recorded)}{recorded(coupons.redeemed) ? ` · Redeemed: ${number(coupons.redeemed)}` : ''}</p>}
            </section>}
          </div>}
          {(attributes.length > 0 || features.length > 0) && <details className="panel attributes">
            <summary><FileText size={17} /><h3>Recorded attributes & feature snapshot</h3><ChevronRight size={17} /></summary>
            {attributes.length > 0 && <dl>{attributes.map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>)}</dl>}
            {features.length > 0 && <><h3>Stored features</h3>{snapshot.computed_at && <p className="small muted">Computed {date(snapshot.computed_at, true)}. This snapshot is not refreshed by opening the profile.</p>}<dl>{features.map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>)}</dl></>}
          </details>}
        </> : <History key={`${id}-${selectedTab}`} customerId={id} kind={selectedTab} currency={data.source.currency} />}
        <details className="profile-notes"><summary>How to read this profile</summary>{data.notes.map((note: string) => <p key={note}>{note}</p>)}</details>
      </>}
    </ConnectionRequired>
  </>;
}
