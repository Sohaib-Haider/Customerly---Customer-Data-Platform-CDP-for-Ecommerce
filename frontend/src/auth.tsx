import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiClient, ApiError } from './api';
import { useConnection } from './connection';

export type StoreChoice = { id: string; name: string; role: string };
type Status = 'disabled' | 'loading' | 'signed_out' | 'needs_store' | 'choose_store' | 'ready' | 'error';
type Workspace = { status: 'ok'; token: string; expires_in: number; store: { id: string; name: string }; role: string; email: string }
  | { status: 'needs_store'; email: string } | { status: 'choose_store'; email: string; stores: StoreChoice[] };

/** Account sign-in is on unless VITE_ACCOUNT_SIGNIN=false (developer-token mode, used by browser tests). */
const enabled = import.meta.env.VITE_ACCOUNT_SIGNIN !== 'false';
// Account endpoints use the HttpOnly stay-signed-in cookie, never a bearer token.
const accounts = new ApiClient('');

/** Readable message from an account API error (field errors, details, network problems). */
export function problem(error: unknown): string {
  if (error instanceof ApiError) {
    const body = error.body || {};
    const fields = ['detail', 'email', 'password', 'name', 'full_name', 'current_password', 'new_password'].flatMap(key => body[key] ?? []);
    if (fields.length) return fields.join(' ');
  }
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
export function code(error: unknown): string { return error instanceof ApiError ? error.body?.code ?? '' : ''; }

type Auth = {
  enabled: boolean; status: Status; email: string; store: { id: string; name: string } | null; stores: StoreChoice[]; error: string;
  googleClientId: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signInWithGoogle: (credential: string) => Promise<void>;
  forgotPassword: (email: string) => Promise<void>;
  resetPassword: (token: string, password: string) => Promise<void>;
  createStore: (name: string) => Promise<void>;
  chooseStore: (storeId: string) => Promise<void>;
  signOut: () => Promise<void>;
};
const Context = createContext<Auth>(null!);

/**
 * Django owns accounts (apps/users): Google sign-in or email + password.
 * Signing in sets an HttpOnly cookie; this
 * provider trades it for the short-lived workspace token every page already
 * uses, renews that token before it expires and again whenever an API reports
 * it expired.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const connection = useConnection();
  const [status, setStatus] = useState<Status>(enabled ? 'loading' : 'disabled');
  const [email, setEmail] = useState('');
  const [store, setStore] = useState<{ id: string; name: string } | null>(null);
  const [stores, setStores] = useState<StoreChoice[]>([]);
  const [error, setError] = useState('');
  const [googleClientId, setGoogleClientId] = useState<string | null>(null);
  const renewal = useRef<number | undefined>(undefined);
  const connect = useRef(connection.connect); connect.current = connection.connect;

  const reset = useCallback((message = '') => {
    window.clearTimeout(renewal.current); connection.disconnect();
    setStore(null); setStores([]); setError(message); setStatus('signed_out');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const apply = useCallback(async (result: Workspace): Promise<string | null> => {
    setEmail(result.email);
    if (result.status === 'needs_store') { setStatus('needs_store'); return null; }
    if (result.status === 'choose_store') { setStores(result.stores); setStatus('choose_store'); return null; }
    await connect.current(result.token);
    setStore(result.store); setError(''); setStatus('ready');
    window.clearTimeout(renewal.current);
    renewal.current = window.setTimeout(() => { void session(result.store.id).catch(() => reset('Please sign in again.')); },
      Math.max(60, result.expires_in - 120) * 1000);
    return result.token;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const session = useCallback(async (storeId?: string) =>
    apply(await accounts.post<Workspace>('/api/auth/session/', storeId ? { store_id: storeId } : {})), [apply]);

  // Resume a previous sign-in (the cookie survives reloads for up to 30 days).
  useEffect(() => {
    if (!enabled) return;
    accounts.get<{ google_client_id: string | null }>('/api/auth/config/').then(c => setGoogleClientId(c.google_client_id)).catch(() => undefined);
    session().catch(e => {
      if (e instanceof ApiError && e.status === 401) setStatus('signed_out');
      else { setError(problem(e)); setStatus('error'); }
    });
    return () => window.clearTimeout(renewal.current);
  }, [session]);

  // When an API reports an expired workspace token, renew it silently; sign out if that fails.
  const storeId = store?.id;
  useEffect(() => {
    if (status !== 'ready') { connection.setRenewer(null); return; }
    connection.setRenewer(async () => {
      try { const token = await session(storeId); if (token) return token; throw new Error(); }
      catch { reset('Your session ended. Please sign in again.'); return null; }
    });
    return () => connection.setRenewer(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, storeId, session]);

  const value: Auth = {
    enabled, status, email, store, stores, error, googleClientId,
    signIn: async (address, password) => { setError(''); await apply(await accounts.post<Workspace>('/api/auth/signin/', { email: address.trim(), password })); },
    signUp: async (address, password) => { setError(''); await apply(await accounts.post<Workspace>('/api/auth/signup/', { email: address.trim(), password })); },
    signInWithGoogle: async credential => { setError(''); await apply(await accounts.post<Workspace>('/api/auth/google/', { credential })); },
    forgotPassword: async address => { await accounts.post('/api/auth/forgot-password/', { email: address.trim() }); },
    resetPassword: async (token, password) => { await accounts.post('/api/auth/reset-password/', { token, password }); },
    createStore: async name => { await apply(await accounts.post<Workspace>('/api/auth/store/', { name: name.trim() })); },
    chooseStore: async id => { setStatus('loading'); try { await session(id); } catch (e) { setError(problem(e)); setStatus('error'); } },
    signOut: async () => { await accounts.post('/api/auth/signout/', {}).catch(() => undefined); reset(); },
  };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAuth() { return useContext(Context); }
