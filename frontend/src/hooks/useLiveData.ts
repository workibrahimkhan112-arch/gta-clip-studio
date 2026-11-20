import { useEffect } from 'react';
import { useStudio } from '../stores/studio';
import type { Job } from '../types/api';

export function useLiveData() {
  useEffect(() => {
    const store = useStudio.getState;
    let disposed = false;
    let pollBusy = false;
    let live = false;
    let lastEvent = 0;
    let lastSnapshot = '';
    const updateRelated = (jobs: Job[]) => {
      const snapshot = jobs
        .map((job) => `${job.id}:${job.status}`)
        .sort()
        .join('|');
      if (snapshot !== lastSnapshot) {
        lastSnapshot = snapshot;
        void store().refresh(['projects', 'highlights', 'exports', 'health']);
      }
    };
    void store().refresh();
    const source = new EventSource('/api/events');
    source.onopen = () => {
      live = true;
      lastEvent = Date.now();
      store().setConnection('live');
    };
    source.addEventListener('update', (event) => {
      try {
        const payload: unknown = JSON.parse((event as MessageEvent<string>).data);
        if (
          !payload ||
          typeof payload !== 'object' ||
          !('jobs' in payload) ||
          !Array.isArray(payload.jobs)
        )
          throw new Error('Invalid event');
        const jobs = payload.jobs as Job[];
        lastEvent = Date.now();
        live = true;
        store().setConnection('live');
        store().acceptJobs(jobs);
        updateRelated(jobs);
      } catch {
        live = false;
        store().setConnection('polling');
      }
    });
    source.onerror = () => {
      live = false;
      store().setConnection('polling');
    };
    const interval = window.setInterval(async () => {
      if (disposed || pollBusy) return;
      if (live && Date.now() - lastEvent < 8000) return;
      pollBusy = true;
      try {
        await store().refresh(['jobs', 'projects', 'highlights', 'exports']);
        if (disposed) return;
        store().setConnection(store().errors.jobs ? 'offline' : 'polling');
        updateRelated(store().data.jobs);
      } finally {
        pollBusy = false;
      }
    }, 4000);
    const background = window.setInterval(() => {
      if (!disposed) void store().refresh(['health', 'projects', 'highlights', 'exports']);
    }, 20000);
    const onFocus = () => {
      void store().refresh(['jobs', 'projects', 'highlights', 'exports', 'health']);
    };
    window.addEventListener('focus', onFocus);
    return () => {
      disposed = true;
      source.close();
      clearInterval(interval);
      clearInterval(background);
      window.removeEventListener('focus', onFocus);
    };
  }, []);
}
