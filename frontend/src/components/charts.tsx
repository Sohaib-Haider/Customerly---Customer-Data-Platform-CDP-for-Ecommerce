import { useId, useState } from 'react';
import { number } from '../format';
import { Empty } from './ui';

const COLORS = ['#3975ee', '#9c72ef', '#35bdad', '#f5b84a', '#ef799b'];
export function AreaChart({ values, labels, title, currency }: { values: number[]; labels: string[]; title: string; currency?: string | null }) {
  const id = useId().replaceAll(':', ''); const [active, setActive] = useState<number | null>(null);
  if (!values.length) return <Empty title="A clearer picture starts with data">This chart will appear when suitable records are available.</Empty>;
  const width = 620, height = 205, left = 48, bottom = 175;
  const min = Math.min(0, ...values), max = Math.max(1, ...values), span = max - min || 1;
  const x = (i: number) => left + i * (width - left - 15) / Math.max(1, values.length - 1);
  const y = (value: number) => 15 + (max - value) / span * (bottom - 15);
  const line = values.map((value, i) => `${i ? 'L' : 'M'}${x(i)},${y(value)}`).join(' ');
  return <div className="chart-wrap"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. ${values.length} observations.`}><defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop stopColor="#3975ee" stopOpacity=".23" /><stop offset="1" stopColor="#3975ee" stopOpacity=".015" /></linearGradient></defs>{[0, .5, 1].map(t => <g key={t}><line x1={left} x2={width} y1={15 + t * 160} y2={15 + t * 160} stroke="#edf0f6" strokeDasharray="4 5" /><text x="0" y={19 + t * 160} fill="#929bac" fontSize="10">{number(max - span * t)}</text></g>)}<path d={`${line} L${x(values.length - 1)},${bottom} L${x(0)},${bottom} Z`} fill={`url(#${id})`} /><path d={line} stroke="#3975ee" strokeWidth="3" fill="none" strokeLinejoin="round" />{values.map((value, i) => <g key={i} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)} tabIndex={0} onFocus={() => setActive(i)} onBlur={() => setActive(null)} aria-label={`${labels[i]}: ${currency || ''} ${number(value, 2)}`}><circle cx={x(i)} cy={y(value)} r="10" fill="transparent" /><circle cx={x(i)} cy={y(value)} r={active === i ? 5 : 3} fill="#3975ee" stroke="white" strokeWidth="2" />{(values.length < 8 || i % Math.ceil(values.length / 6) === 0) && <text x={x(i)} y="198" textAnchor="middle" fontSize="10" fill="#929bac">{labels[i]}</text>}</g>)}</svg><div className="chart-caption" role="status">{active === null ? `${title} · ${currency || 'source values'}` : `${labels[active]} — ${currency || ''} ${number(values[active], 2)}`}</div></div>;
}
export function Donut({ items, title }: { items: { name: string; value: number }[]; title: string }) {
  const total = items.reduce((sum, item) => sum + item.value, 0); let offset = 0;
  return <div className="donut-layout"><div className="donut"><svg viewBox="0 0 120 120" role="img" aria-label={title}><circle cx="60" cy="60" r="47" stroke="#eef2f8" strokeWidth="13" fill="none" />{items.map((item, i) => { const length = total ? item.value / total * 295.31 : 0; const old = offset; offset += length; return <circle key={item.name} cx="60" cy="60" r="47" stroke={COLORS[i % COLORS.length]} strokeWidth="13" fill="none" strokeDasharray={`${length} ${295.31 - length}`} strokeDashoffset={-old} transform="rotate(-90 60 60)"><title>{item.name}: {item.value}</title></circle>; })}</svg><div><strong>{number(total)}</strong><span>{title}</span></div></div><div className="chart-legend">{items.length ? items.map((item, i) => <div key={item.name}><i style={{ background: COLORS[i % COLORS.length] }} /><span>{item.name}</span><b>{number(item.value)}</b></div>) : <p className="muted">No records yet</p>}</div></div>;
}
