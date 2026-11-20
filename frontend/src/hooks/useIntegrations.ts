import { useCallback, useEffect, useState } from 'react';
import { api } from '../services/api';
import { errorMessage } from '../lib/utils';
import type { Integrations } from '../types/api';

// Only public configuration flags enter this hook. Credentials stay in the password input.
export function useIntegrations() {
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    api
      .integrations()
      .then((value) => {
        if (!disposed) {
          setIntegrations(value);
          setError(null);
        }
      })
      .catch((err) => {
        if (!disposed) setError(errorMessage(err));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [revision]);
  return { integrations, setIntegrations, error, loading, refresh };
}
