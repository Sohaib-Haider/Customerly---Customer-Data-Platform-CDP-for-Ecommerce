import { useEffect, useRef, useState } from 'react';
import { useConnection } from '../connection';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, ArrowUpRight, RefreshCw, Search, ShieldCheck, Sparkles, Users } from 'lucide-react';
import { useDebounced, useResource } from '../hooks';
import { query } from '../api';
import { date, money, number } from '../format';
import { Avatar, Badge, ConnectionRequired, DisabledCampaign, Empty, ErrorNotice, Loading, Modal, Notice, PageHeader, Pagination, Status } from '../components/ui';
import { DataIllustration } from '../components/illustrations';
import type { Audience, Page, Row, PreparationJob } from '../types';

export function Audiences() {
  const audiences = useResource<{ audiences: Audience[] }>('/api/audiences/', 2000);
  const jobs = useResource<{ jobs: PreparationJob[] }>('/api/audiences/jobs/', 2000);
  const { api, connected, expired } = useConnection();
  const [prepareOpen, setPrepareOpen] = useState(false);
  const [threshold, setThreshold] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<PreparationJob[]>([]);
  const currentApi = useRef(api);
  currentApi.current = api;
  const notified = useRef('');
  useEffect(() => { setAccepted([]); setPrepareError(null); setSubmitting(false); notified.current = ''; }, [api]);
  const currentJobs = accepted.length && !accepted.every(job => jobs.data?.jobs.some(saved => saved.id === job.id))
    ? accepted : jobs.data?.jobs || accepted;
  const busy = submitting || currentJobs.some(job => ['queued', 'running'].includes(job.status));
  const finished = currentJobs.length > 0 && currentJobs.every(job => job.status === 'completed');
  useEffect(() => {
    const signature = currentJobs.map(job => job.id).join(',');
    if (finished && notified.current !== signature) {
      notified.current = signature;
      window.dispatchEvent(new Event('predictions-updated'));
    }
  }, [finished, currentJobs]);
  async function prepare() {
    setSubmitting(true); setPrepareError(null);
    try {
      const result = await api.post<{ jobs: PreparationJob[] }>('/api/audiences/prepare/', { clv_threshold: threshold });
      if (currentApi.current !== api) return;
      setAccepted(result.jobs); setPrepareOpen(false); jobs.refresh(); audiences.refresh();
    } catch (error) { if (currentApi.current === api) setPrepareError((error as Error).message); }
    finally { if (currentApi.current === api) setSubmitting(false); }
  }
  return <><PageHeader eyebrow="MAKE EVERY CONNECTION COUNT" title="The right people. A clearer purpose." description="Explore the customers in your saved audiences, with predictions that explain the context."><button className="button primary" disabled={busy || !connected || expired} onClick={() => setPrepareOpen(true)}><Sparkles size={16} />{busy ? 'Preparing audiences…' : 'Prepare audiences'}</button></PageHeader><ConnectionRequired><ErrorNotice error={prepareError} /><ErrorNotice error={jobs.error} retry={jobs.refresh} />{busy && <Notice>Preparing audiences in the background. Counts update after each saved batch; you can leave this page.</Notice>}{finished && <Notice>Both audiences finished processing. Open an audience to see its customers.</Notice>}{currentJobs.filter(job => job.status === 'queued').map(job => <Notice key={job.id}>{job.segment_key === 'churn' ? 'Churn' : 'CLV'} is queued. The background worker must be running to process it.</Notice>)}<div className="audience-banner"><div><Badge tone="blue">TWO MODELS, ONE CUSTOMER VIEW</Badge><h2>Discover who needs attention.<br />And who’s ready for what’s next.</h2><p>Churn risk and future value, grounded in the data you actually have.</p></div><DataIllustration variant="purple" /></div><ErrorNotice error={audiences.error} retry={audiences.refresh} />{audiences.loading && !audiences.data && <Loading />}<div className="audience-grid">{audiences.data?.audiences.map((audience, i) => <article className={`panel audience-card ${i ? 'purple' : 'blue'}`} key={audience.key}><Link className="audience-card-main" to={`/audiences/${audience.key}`}><div className="audience-card-top"><span className="audience-symbol">{i ? <Sparkles size={26} /> : <ShieldCheck size={26} />}</span><ArrowUpRight size={22} /></div><Status value={audience.state} /><PreparationProgress job={currentJobs.find(job => job.segment_key === audience.key)} /><h2>{audience.key === 'churn' ? 'Churn risk' : 'Future high-value / CLV'}</h2><p>{audience.description}</p><div className="audience-count"><strong>{number(audience.member_count)}</strong><span>audience members</span></div><div className="audience-card-meta"><span>{number(audience.predicted_count)} saved predictions</span><span>Updated {date(audience.last_computed_at)}</span></div><span className="audience-open">Explore customers<ArrowRight size={16} /></span></Link><div className="audience-card-footer"><DisabledCampaign /><small>Activation is on the horizon</small></div></article>)}</div><Notice>Churn risk includes customers the model classifies as churn. CLV includes customers meeting your chosen predicted revenue cutoff. Audiences can overlap.</Notice></ConnectionRequired>{prepareOpen && <Modal title="Prepare audiences" onClose={() => setPrepareOpen(false)}><p>Start background preparation for both audiences. Customers are processed in batches, saved predictions are reused, and counts update as batches finish. Churn uses the model’s churn label.</p><form onSubmit={event => { event.preventDefault(); void prepare(); }}><label>Minimum predicted 90-day revenue for the CLV audience (GBP)<input type="number" min="0" step="any" required value={threshold} onChange={event => setThreshold(event.target.value)} placeholder="Enter your revenue cutoff" /></label><p className="small muted">Customers meeting this amount join the CLV audience. Your database amounts are unchanged.</p><button className="button primary" type="submit" disabled={submitting}>{submitting ? 'Starting…' : 'Prepare audiences'}</button></form></Modal>}</>;
}

export function PreparationProgress({ job }: { job?: PreparationJob | null }) {
  if (!job) return null;
  return <div className="small" role="status" aria-live="polite" style={{ marginTop: 12 }}>
    <b>{job.status === 'queued' ? 'Waiting for worker' : job.status === 'running' ? 'Processing…' : job.status === 'completed' ? 'Completed' : 'Failed — press Prepare audiences to resume'}</b>
    <p>{number(job.processed)} / {number(job.total)} customers checked · {number(job.predicted)} predicted · {number(job.unavailable)} unavailable · {number(job.reused)} saved results reused</p>
    {job.status === 'failed' && <p>Reason: {job.error_code}. Earlier saved batches are preserved.</p>}
    <small>Last progress {date(job.updated_at)}</small>
  </div>;
}

export function AudienceMembers() {
  const { key = '' } = useParams(); const [search, setSearch] = useState(''); const [offset, setOffset] = useState(0); const term = useDebounced(search);
  const audience = useResource<Audience>(`/api/audiences/${key}/`, 5000);
  const members = useResource<Page>(`/api/audiences/${key}/customers/${query({ search: term, offset, limit: 20 })}`, 3000);
  const value = audience.data;
  return <><Link className="back-link" to="/audiences"><ArrowLeft size={15} />All audiences</Link><PageHeader eyebrow="SAVED AUDIENCE" title={key === 'churn' ? 'Churn risk' : key === 'clv' ? 'Future high-value / CLV' : 'Audience'} description={value?.description}><DisabledCampaign /></PageHeader><ConnectionRequired><ErrorNotice error={audience.error} retry={audience.refresh} />{value && <><div className="audience-detail-stats panel"><div><span>Audience members</span><strong>{number(value.member_count)}</strong></div><div><span>Store customers</span><strong>{number(value.total_customers)}</strong></div><div><span>Saved predictions</span><strong>{number(value.predicted_count)}</strong></div><div><span>Current status</span><Status value={value.state} /></div></div><PreparationProgress job={value.preparation} />{value.state === 'not_configured' && <Notice>Open All audiences and press Prepare audiences to build customer lists from saved predictions.</Notice>}{value.state === 'processing' && <Notice>Audience preparation is running. Customer lists update after each committed batch.</Notice>}<section className="panel directory"><div className="directory-toolbar"><div className="search-field"><Search size={17} /><input value={search} aria-label="Search audience members" placeholder="Search audience members…" onChange={event => { setSearch(event.target.value); setOffset(0); }} /></div><button className="icon-button" aria-label="Refresh audience members" onClick={() => { members.refresh(); audience.refresh(); }}><RefreshCw size={17} /></button></div><ErrorNotice error={members.error} retry={members.refresh} />{members.loading && !members.data ? <Loading /> : members.data?.results.length ? <><div className="table-scroll"><table><thead><tr><th>Customer</th><th>{key === 'churn' ? 'Churn prediction' : 'Predicted revenue'}</th><th>Assigned</th><th>Prediction status</th><th /></tr></thead><tbody>{members.data.results.map((member: Row) => { const prediction = member.prediction?.result?.prediction; return <tr key={member.customer_id}><td><Link className="customer-link" to={`/customers/${member.customer_id}`}><Avatar name={member.email || member.source_customer_id} /><span><b>{member.email || member.source_customer_id}</b><small>{member.source_customer_id}</small></span></Link></td><td>{prediction ? key === 'churn' ? `${number(Number(prediction.churn_probability) * 100, 1)}%` : money(prediction.predicted_revenue, prediction.currency || 'GBP') : 'Not available'}{member.prediction?.computed_at && <small className="block muted">Computed {date(member.prediction.computed_at)}</small>}</td><td>{date(member.assigned_at)}</td><td>{member.prediction ? <Status value={member.prediction.status} /> : <Badge>No prediction</Badge>}</td><td><Link className="text-button" to={`/customers/${member.customer_id}`}>View profile<ArrowRight size={14} /></Link></td></tr>; })}</tbody></table></div><Pagination count={members.data.count} offset={offset} limit={20} onChange={setOffset} /></> : <Empty title={search ? 'No members match your search' : 'No saved members in this audience'} action={<Link className="button" to="/uploads/new">Review upload policies<ArrowRight size={16} /></Link>}>{search ? 'Try another email, phone number or source ID.' : 'This may mean no configured membership policy, unavailable evidence, or no customers meeting the chosen threshold.'}</Empty>}</section><p className="small muted">{value.note} Assignment and prediction timestamps can differ. Campaign execution is coming later.</p></>}</ConnectionRequired></>;
}
