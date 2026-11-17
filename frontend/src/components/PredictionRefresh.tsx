import { useEffect, useState } from 'react';
import { useConnection } from '../connection';
import { ErrorNotice } from './ui';

// Refreshing or reconnecting the application resumes bounded database backfill.
export function PredictionRefresh() {
  const { api, connected, expired } = useConnection();
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    if (!connected || expired) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setError(null);
    const run = async () => {
      try {
        const result = await api.post<{ remaining: boolean }>('/api/segments/refresh/', {});
        if (!cancelled && result.remaining) timer = setTimeout(run, 250);
        if (!cancelled && !result.remaining) window.dispatchEvent(new Event('predictions-updated'));
      } catch (failure) {
        if (!cancelled) setError(failure instanceof Error ? failure : new Error('Prediction generation failed.'));
      }
    };
    void run();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, connected, expired]);
  return error ? <ErrorNotice error={error.message} /> : null;
}
