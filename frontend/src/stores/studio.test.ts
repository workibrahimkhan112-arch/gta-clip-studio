import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStudio } from './studio';
import { highlightFixture, jobFixture, layoutFixture, projectFixture } from '../test/fixtures';

beforeEach(() =>
  useStudio.setState({
    data: { ...useStudio.getState().data, highlights: [highlightFixture], jobs: [] },
    loading: {},
    loaded: { highlights: true },
    errors: {},
  }),
);
afterEach(() => vi.unstubAllGlobals());
function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
describe('resource consistency', () => {
  it('keeps the newest response when requests finish out of order', async () => {
    const old = deferredResponse();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockReturnValueOnce(old.promise)
        .mockResolvedValueOnce(
          new Response(JSON.stringify([{ ...highlightFixture, title: 'New response' }])),
        ),
    );
    const first = useStudio.getState().refresh(['highlights']);
    await useStudio.getState().refresh(['highlights']);
    old.resolve(new Response(JSON.stringify([highlightFixture])));
    await first;
    expect(useStudio.getState().data.highlights[0].title).toBe('New response');
  });
  it('does not let an in-flight GET overwrite a saved caption edit', async () => {
    const old = deferredResponse();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(old.promise));
    const refresh = useStudio.getState().refresh(['highlights']);
    useStudio.getState().upsertHighlight({ ...highlightFixture, title: 'User saved this' });
    old.resolve(new Response(JSON.stringify([highlightFixture])));
    await refresh;
    expect(useStudio.getState().data.highlights[0].title).toBe('User saved this');
    expect(useStudio.getState().loading.highlights).toBe(false);
  });
  it('protects a saved project layout from an older in-flight GET', async () => {
    useStudio.setState({ loaded: { projects: true } });
    const old = deferredResponse();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(old.promise));
    const refresh = useStudio.getState().refresh(['projects']);
    const updated = { ...projectFixture, layout: layoutFixture };
    useStudio.getState().upsertProject(updated);
    old.resolve(new Response(JSON.stringify([projectFixture])));
    await refresh;
    expect(useStudio.getState().data.projects[0].layout).toEqual(layoutFixture);
    expect(useStudio.getState().loading.projects).toBe(false);
  });
  it('allows the initial full list to finish after a single workspace project loads', async () => {
    const old = deferredResponse();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(old.promise));
    const refresh = useStudio.getState().refresh(['projects']);
    useStudio.getState().upsertProject(projectFixture);
    old.resolve(new Response(JSON.stringify([projectFixture, { ...projectFixture, id: 'other' }])));
    await refresh;
    expect(useStudio.getState().loaded.projects).toBe(true);
    expect(useStudio.getState().data.projects).toHaveLength(2);
  });
  it('keeps last received data with a visible error on refresh failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Offline')));
    await useStudio.getState().refresh(['highlights']);
    expect(useStudio.getState().data.highlights).toEqual([highlightFixture]);
    expect(useStudio.getState().loaded.highlights).toBe(true);
    expect(useStudio.getState().errors.highlights).toContain('local server');
  });
  it('does not replace a newer SSE job with an older REST response', async () => {
    const old = deferredResponse();
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(old.promise));
    const refresh = useStudio.getState().refresh(['jobs']);
    useStudio.getState().acceptJobs([{ ...jobFixture, status: 'completed', progress: 1 }]);
    old.resolve(new Response(JSON.stringify([jobFixture])));
    await refresh;
    expect(useStudio.getState().data.jobs[0].status).toBe('completed');
  });
});
