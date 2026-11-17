import { useEffect, useRef, type ReactNode } from 'react';
import { AlertCircle, ArrowRight, Check, ChevronLeft, ChevronRight, Database, LoaderCircle, LockKeyhole, PlugZap, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useConnection } from '../connection';
import { label } from '../format';

export function PageHeader({ eyebrow, title, description, children }: { eyebrow?: string; title: string; description?: string; children?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div><div className="heading-actions">{children}</div></div>;
}
export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone}`}>{children}</span>; }
export function Status({ value }: { value: string }) {
  const success = ['completed', 'ready', 'saved', 'predicted', 'approved', 'validated'].includes(value);
  const warning = ['not_configured', 'unregistered', 'unavailable', 'needs_review', 'superseded'].includes(value);
  const failed = value.includes('failed');
  return <Badge tone={failed ? 'red' : success ? 'green' : warning ? 'amber' : 'blue'}><i />{label(value)}</Badge>;
}
export function ErrorNotice({ error, retry }: { error?: string | null; retry?: () => void }) {
  return error ? <div className="notice error" role="alert"><AlertCircle size={18} /><div>{error}</div>{retry && <button className="text-button" onClick={retry}>Try again</button>}</div> : null;
}
export function Notice({ children }: { children: ReactNode }) { return <div className="notice"><Database size={18} /><div>{children}</div></div>; }
export function Loading({ text = 'Loading your data…' }: { text?: string }) { return <div className="loading" role="status"><LoaderCircle className="spin" size={22} />{text}</div>; }
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Database size={26} /></div><h3>{title}</h3><p>{children}</p>{action}</div>;
}
export function ConnectionRequired({ children }: { children: ReactNode }) {
  const { connected, open } = useConnection();
  return connected ? <>{children}</> : <div className="panel"><Empty title="Let’s connect your workspace" action={<button className="button primary" onClick={open}><PlugZap size={17} />Connect workspace</button>}>Use your temporary store-user token to securely load your store’s data. No sample records are mixed into your workspace.</Empty></div>;
}
export function Pagination({ count, offset, limit, onChange }: { count: number; offset: number; limit: number; onChange: (offset: number) => void }) {
  return <div className="pagination"><span>{count ? `${offset + 1}–${Math.min(offset + limit, count)} of ${count.toLocaleString()}` : 'No records'}</span><div><button className="icon-button" aria-label="Previous page" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}><ChevronLeft size={17} /></button><span>Page {Math.floor(offset / limit) + 1}</span><button className="icon-button" aria-label="Next page" disabled={offset + limit >= count} onClick={() => onChange(offset + limit)}><ChevronRight size={17} /></button></div></div>;
}
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const box = useRef<HTMLDivElement>(null); const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null; const old = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const frame = box.current;
    const focusables = () => frame?.querySelectorAll<HTMLElement>('button:not([disabled]),input,select,textarea,a[href],[tabindex="0"]') ?? [];
    (focusables()[0] ?? frame)?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current();
      if (event.key === 'Tab') {
        const items = focusables(); if (!items.length) return;
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
        if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
      }
    };
    document.addEventListener('keydown', keydown);
    return () => { document.body.style.overflow = old; document.removeEventListener('keydown', keydown); previous?.focus(); };
  }, []);
  return <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}><div ref={box} tabIndex={-1} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={20} /></button></div>{children}</div></div>;
}
export function DisabledCampaign() { return <span className="campaign-tooltip" tabIndex={0} aria-label="Create campaign. Coming soon"><button className="button campaign" disabled><LockKeyhole size={15} />Create campaign</button><span role="tooltip">Coming soon</span></span>; }
export function Avatar({ name, large = false }: { name: string; large?: boolean }) { return <span className={`avatar ${large ? 'large' : ''}`}>{name.slice(0, 2).toUpperCase()}</span>; }
export function SuccessDialog({ pending, onClose }: { pending: boolean; onClose: () => void }) {
  return <Modal title="Import complete" onClose={onClose}><div className="success-display"><div className="confetti" aria-hidden="true">{Array.from({ length: 18 }, (_, i) => <i key={i} style={{ left: `${5 + i * 5}%`, animationDelay: `${i % 5 * .08}s`, background: ['#2563eb', '#a78bfa', '#2dd4bf', '#fbbf24'][i % 4] }} />)}</div><div className="success-circle"><Check size={45} strokeWidth={2.5} /></div><h2>Your data has been saved.</h2><p>{pending ? 'Your audiences are being prepared. You can explore your data while predictions finish.' : 'Your store’s data is ready to explore. Prediction availability is shown separately for each customer.'}</p><div className="stack"><Link onClick={onClose} className="button primary" to="/">Open dashboard<ArrowRight size={17} /></Link><Link onClick={onClose} className="button" to="/customers">View customers</Link><Link onClick={onClose} className="text-button" to="/audiences">View audiences</Link></div></div></Modal>;
}
