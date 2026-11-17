import Decimal from 'decimal.js';

export function number(value: unknown, decimals = 0): string {
  if (value === null || value === undefined || value === '') return '—';
  try { const d = new Decimal(String(value)); return d.isFinite() ? d.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ',') : '—'; }
  catch { return String(value); }
}
export function money(value: unknown, currency?: string | null) { return `${currency ? `${currency} ` : ''}${number(value, 2)}`; }
export function date(value: unknown, withTime = false): string {
  if (!value) return 'Not available';
  const text = String(value);
  if (!withTime && /^\d{4}-\d{2}-\d{2}$/.test(text)) return new Date(`${text}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  const parsed = new Date(text); return Number.isNaN(parsed.getTime()) ? text : withTime ? parsed.toLocaleString() : parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}
export function label(value: string) { return value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()); }
export function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not available';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
