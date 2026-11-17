import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { ApiClient } from './api';

type Connection = { api: ApiClient; connected: boolean; expired: boolean; modal: boolean; open: () => void; close: () => void; connect: (token: string) => Promise<void>; disconnect: () => void };
const Context = createContext<Connection>(null!);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState('');
  const [expired, setExpired] = useState(false);
  const [modal, setModal] = useState(false);
  const invalidate = useCallback(() => { setExpired(true); setModal(true); }, []);
  const api = useMemo(() => new ApiClient(token, invalidate), [token, invalidate]);
  const connect = async (candidate: string) => {
    const value = candidate.trim().replace(/^Bearer\s+/i, '');
    if (!value) throw new Error('Enter a store-user token to continue.');
    await new ApiClient(value).get('/api/ingestion/sources/');
    setToken(value); setExpired(false); setModal(false);
  };
  return <Context.Provider value={{ api, connected: !!token, expired, modal, open: () => setModal(true), close: () => setModal(false), connect,
    disconnect: () => { setToken(''); setExpired(false); setModal(false); } }}>{children}</Context.Provider>;
}
export function useConnection() { return useContext(Context); }
