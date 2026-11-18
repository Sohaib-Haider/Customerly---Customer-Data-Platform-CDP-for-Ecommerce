import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiClient, type Renew } from './api';

type Connection = { api: ApiClient; connected: boolean; expired: boolean; modal: boolean; open: () => void; close: () => void; connect: (token: string) => Promise<void>; disconnect: () => void; setRenewer: (renewer: Renew | null) => void };
const Context = createContext<Connection>(null!);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState('');
  const [expired, setExpired] = useState(false);
  const [modal, setModal] = useState(false);
  const invalidate = useCallback(() => { setExpired(true); setModal(true); }, []);
  // Set by the sign-in flow; concurrent 401s share one renewal.
  const renewer = useRef<Renew | null>(null); const pending = useRef<ReturnType<Renew> | null>(null);
  const renew = useCallback<Renew>(() => {
    if (!renewer.current) return Promise.resolve(undefined);
    pending.current ??= renewer.current().finally(() => { pending.current = null; });
    return pending.current;
  }, []);
  const api = useMemo(() => new ApiClient(token, invalidate, renew), [token, invalidate, renew]);
  const connect = async (candidate: string) => {
    const value = candidate.trim().replace(/^Bearer\s+/i, '');
    if (!value) throw new Error('Enter a store-user token to continue.');
    await new ApiClient(value).get('/api/ingestion/sources/');
    setToken(value); setExpired(false); setModal(false);
  };
  return <Context.Provider value={{ api, connected: !!token, expired, modal, open: () => setModal(true), close: () => setModal(false), connect,
    disconnect: () => { setToken(''); setExpired(false); setModal(false); }, setRenewer: (fn: Renew | null) => { renewer.current = fn; } }}>{children}</Context.Provider>;
}
export function useConnection() { return useContext(Context); }
