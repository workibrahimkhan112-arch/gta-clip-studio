import { useCallback, useRef, useState } from 'react';
import { errorMessage } from '../lib/utils';
import { useUI } from '../stores/ui';

export function useAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const run = useCallback(
    async <T>(task: () => Promise<T>, success?: string): Promise<T | undefined> => {
      if (busy.current) return undefined;
      busy.current = true;
      setPending(true);
      setError(null);
      try {
        const result = await task();
        if (success) useUI.getState().toast(success);
        return result;
      } catch (err) {
        const message = errorMessage(err);
        setError(message);
        useUI.getState().toast('Action could not be completed', message, 'error');
        return undefined;
      } finally {
        busy.current = false;
        setPending(false);
      }
    },
    [],
  );
  return { pending, error, run, clearError: () => setError(null) };
}
