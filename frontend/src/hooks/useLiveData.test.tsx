// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { useLiveData } from './useLiveData';
import { useStudio } from '../stores/studio';
import { jobFixture } from '../test/fixtures';

class TestEventSource {
  static latest: TestEventSource;
  onopen?: () => void;
  onerror?: () => void;
  listeners = new Map<string, (event: { data: string }) => void>();
  close = vi.fn();
  constructor(public url: string) {
    TestEventSource.latest = this;
  }
  addEventListener(name: string, callback: (event: { data: string }) => void) {
    this.listeners.set(name, callback);
  }
  update(payload: unknown) {
    this.listeners.get('update')?.({ data: JSON.stringify(payload) });
  }
}
const originalRefresh = useStudio.getState().refresh;
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('EventSource', TestEventSource);
  useStudio.setState({
    refresh: vi.fn().mockResolvedValue(undefined),
    connection: 'connecting',
    errors: {},
    data: { ...useStudio.getState().data, jobs: [] },
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useStudio.setState({ refresh: originalRefresh });
});

describe('native SSE with REST fallback', () => {
  it('accepts actual job events and refreshes related resources only on status changes', () => {
    const { unmount } = renderHook(() => useLiveData());
    const source = TestEventSource.latest;
    expect(source.url).toBe('/api/events');
    act(() => {
      source.onopen?.();
      source.update({ jobs: [jobFixture] });
    });
    expect(useStudio.getState().connection).toBe('live');
    expect(useStudio.getState().data.jobs[0].progress).toBeNull();
    const refresh = vi.mocked(useStudio.getState().refresh);
    const calls = refresh.mock.calls.length;
    act(() => source.update({ jobs: [{ ...jobFixture, progress: 0.2 }] }));
    expect(refresh.mock.calls.length).toBe(calls);
    act(() => source.update({ jobs: [{ ...jobFixture, status: 'completed', progress: 1 }] }));
    expect(refresh.mock.calls.length).toBe(calls + 1);
    unmount();
    expect(source.close).toHaveBeenCalledOnce();
  });
  it.each(['captions', 'score', 'titles'] as const)(
    'refreshes actual highlights after %s jobs finish or fail',
    (kind) => {
      renderHook(() => useLiveData());
      const source = TestEventSource.latest;
      act(() => source.update({ jobs: [{ ...jobFixture, kind, status: 'running' }] }));
      const refresh = vi.mocked(useStudio.getState().refresh);
      refresh.mockClear();
      act(() => source.update({ jobs: [{ ...jobFixture, kind, status: 'completed' }] }));
      expect(refresh).toHaveBeenCalledWith(['projects', 'highlights', 'exports', 'health']);
      refresh.mockClear();
      act(() => source.update({ jobs: [{ ...jobFixture, kind, status: 'failed' }] }));
      expect(refresh).toHaveBeenCalledWith(['projects', 'highlights', 'exports', 'health']);
    },
  );
  it('falls back to polling every four seconds and identifies offline responses', async () => {
    renderHook(() => useLiveData());
    act(() => TestEventSource.latest.onerror?.());
    expect(useStudio.getState().connection).toBe('polling');
    await act(() => vi.advanceTimersByTimeAsync(4000));
    expect(useStudio.getState().refresh).toHaveBeenCalledWith([
      'jobs',
      'projects',
      'highlights',
      'exports',
    ]);
    act(() => useStudio.setState({ errors: { jobs: 'Cannot reach local server' } }));
    await act(() => vi.advanceTimersByTimeAsync(4000));
    expect(useStudio.getState().connection).toBe('offline');
    act(() => TestEventSource.latest.update({ jobs: [jobFixture] }));
    expect(useStudio.getState().connection).toBe('live');
    expect(useStudio.getState().errors.jobs).toBeNull();
  });
  it('polls when an open stream goes silent, and rejects malformed payloads', async () => {
    renderHook(() => useLiveData());
    act(() => TestEventSource.latest.onopen?.());
    const refresh = vi.mocked(useStudio.getState().refresh);
    const count = refresh.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(4000));
    expect(refresh.mock.calls.length).toBe(count);
    await act(() => vi.advanceTimersByTimeAsync(4000));
    expect(refresh.mock.calls.length).toBeGreaterThan(count);
    act(() => TestEventSource.latest.update({ invalid: true }));
    expect(useStudio.getState().connection).toBe('polling');
  });
});
