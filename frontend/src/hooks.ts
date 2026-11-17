import { useCallback, useEffect, useState } from 'react';
import { useConnection } from './connection';

export function useResource<T>(path: string | null, poll = 0) {
  const { api, connected } = useConnection();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion(value => value + 1), []);
  useEffect(() => {
    window.addEventListener('predictions-updated', refresh);
    return () => window.removeEventListener('predictions-updated', refresh);
  }, [refresh]);
  useEffect(() => {
    setData(null); setError(null);
    if (!path || !connected) { setLoading(false); return; }
    const controller = new AbortController(); let stopped = false; let timer: ReturnType<typeof setTimeout>;
    async function load() {
      setLoading(true);
      try { const result = await api.get<T>(path!, controller.signal); if (!stopped) { setData(result); setError(null); } }
      catch (e) { if (!stopped && !(e instanceof DOMException && e.name === 'AbortError')) setError((e as Error).message); }
      finally { if (!stopped) { setLoading(false); if (poll) timer = setTimeout(load, poll); } }
    }
    void load();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [api, connected, path, poll, version]);
  return { data, error, loading, refresh };
}

export function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => { const timer = setTimeout(() => setDebounced(value), delay); return () => clearTimeout(timer); }, [value, delay]);
  return debounced;
}
