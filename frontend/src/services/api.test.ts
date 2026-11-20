import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, request, responseError, uploadVideo } from './api';
import { layoutFixture, subtitleStyleFixture } from '../test/fixtures';

afterEach(() => vi.unstubAllGlobals());
describe('contract API client', () => {
  it('uses relative API paths, JSON bodies, and documented caption styles', async () => {
    const fetch = vi
      .fn()
      .mockImplementation(async () => new Response(JSON.stringify({ id: 'job' }), { status: 202 }));
    vi.stubGlobal('fetch', fetch);
    await api.exportHighlight('clip/a', 'animated');
    expect(fetch).toHaveBeenCalledWith(
      '/api/highlights/clip%2Fa/export',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ caption_style: 'animated' }),
      }),
    );
    await api.batchExport(['one', 'two'], 'none');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/exports/batch',
      expect.objectContaining({
        body: JSON.stringify({ highlight_ids: ['one', 'two'], caption_style: 'none' }),
      }),
    );
  });
  it('uses the exact layout, caption, review and write-only integration contracts', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await api.saveLayout('project/a', layoutFixture);
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/projects/project%2Fa/layout',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify(layoutFixture) }),
    );
    await api.generateCaptions('clip/a');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/highlights/clip%2Fa/captions',
      expect.objectContaining({ method: 'POST', body: '{"force":false}' }),
    );
    await api.generateCaptions('clip/a', true);
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/highlights/clip%2Fa/captions',
      expect.objectContaining({ body: '{"force":true}' }),
    );
    await api.projectCaptions('project/a', true);
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/projects/project%2Fa/captions',
      expect.objectContaining({ method: 'POST', body: '{"force":true}' }),
    );
    await api.scoreProject('project/a');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/projects/project%2Fa/score',
      expect.objectContaining({ method: 'POST', body: '{"force":false}' }),
    );
    await api.scoreProject('project/a', true);
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/projects/project%2Fa/score',
      expect.objectContaining({ body: '{"force":true}' }),
    );
    await api.integrations();
    expect(fetch).toHaveBeenLastCalledWith('/api/integrations', expect.any(Object));
    await api.saveIntegration('gemini', 'test-secret');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/integrations/gemini',
      expect.objectContaining({ method: 'PUT', body: '{"api_key":"test-secret"}' }),
    );
    await api.saveIntegration('groq', '');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/integrations/groq',
      expect.objectContaining({ method: 'PUT', body: '{"api_key":""}' }),
    );
    expect(
      fetch.mock.calls.every(
        ([url]) => !String(url).includes('?') && !String(url).includes('test-secret'),
      ),
    ).toBe(true);
  });
  it('uses V2 OAuth, usage, titles, OpenAI key and nullable subtitle-style contracts', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await api.chatgptStatus();
    expect(fetch).toHaveBeenLastCalledWith('/api/chatgpt', expect.any(Object));
    await api.connectChatGPT();
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/chatgpt/connect',
      expect.objectContaining({ method: 'POST', body: '{}' }),
    );
    await api.connectChatGPT('account/a');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/chatgpt/connect',
      expect.objectContaining({ body: '{"account_id":"account/a"}' }),
    );
    await api.selectChatGPT('account/a');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/chatgpt/select',
      expect.objectContaining({ method: 'POST', body: '{"account_id":"account/a"}' }),
    );
    await api.disconnectChatGPT('account/a');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/chatgpt/disconnect',
      expect.objectContaining({ method: 'POST', body: '{"account_id":"account/a"}' }),
    );
    await api.chatgptModels();
    expect(fetch).toHaveBeenLastCalledWith('/api/chatgpt/models', expect.any(Object));
    await api.usage();
    expect(fetch).toHaveBeenLastCalledWith('/api/usage', expect.any(Object));
    await api.suggestTitles('clip/a');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/highlights/clip%2Fa/titles',
      expect.objectContaining({ method: 'POST', body: '{"force":false}' }),
    );
    await api.suggestTitles('clip/a', true);
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/highlights/clip%2Fa/titles',
      expect.objectContaining({ body: '{"force":true}' }),
    );
    await api.patchHighlight('clip/a', { subtitle_style: subtitleStyleFixture });
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/highlights/clip%2Fa',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ subtitle_style: subtitleStyleFixture }),
      }),
    );
    await api.patchHighlight('clip/a', { subtitle_style: null });
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/highlights/clip%2Fa',
      expect.objectContaining({ body: '{"subtitle_style":null}' }),
    );
    await api.saveIntegration('openai', 'write-only-test-key');
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/integrations/openai',
      expect.objectContaining({ method: 'PUT', body: '{"api_key":"write-only-test-key"}' }),
    );
    expect(
      fetch.mock.calls.every(
        ([url]) => !String(url).includes('?') && !String(url).includes('write-only-test-key'),
      ),
    ).toBe(true);
  });
  it('supports 204 delete responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api.deleteProject('project')).resolves.toBeUndefined();
  });
  it('surfaces the actual server error instead of a success state', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: 'Cancel queued work first' }), { status: 409 }),
        ),
    );
    await expect(api.deleteProject('project')).rejects.toMatchObject({
      status: 409,
      message: 'Cancel queued work first',
    });
  });
  it('formats FastAPI field validation errors', () => {
    expect(
      responseError({ detail: [{ loc: ['body', 'max_clips'], msg: 'Must be at most 50' }] }, 422),
    ).toBe('max_clips: Must be at most 50');
    expect(responseError(null, 500)).toContain('500');
  });
  it('reports backend connectivity failures and keeps explicit aborts intact', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Network failed')));
    await expect(request('/health')).rejects.toBeInstanceOf(ApiError);
    await expect(request('/health')).rejects.toThrow('port 8000');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Cancelled', 'AbortError')));
    await expect(request('/health')).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('preserves relative transcript and word timestamps in a patch', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const transcript = [
      { start: 0, end: 2, text: 'Real words', words: [{ start: 0.1, end: 0.8, word: 'Real' }] },
    ];
    await api.patchHighlight('clip', { transcript });
    expect(fetch).toHaveBeenCalledWith(
      '/api/highlights/clip',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ transcript }) }),
    );
  });
});

class TestXHR {
  static latest: TestXHR;
  upload: {
    onprogress?: (event: { loaded: number; total: number; lengthComputable: boolean }) => void;
  } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  status = 201;
  response: unknown = { id: 'uploaded' };
  responseType = '';
  open = vi.fn();
  send = vi.fn();
  abort = vi.fn(() => this.onabort?.());
  constructor() {
    TestXHR.latest = this;
  }
}
describe('real multipart upload transport', () => {
  it('reports only XHR transfer measurements and uses the contracted field', async () => {
    vi.stubGlobal('XMLHttpRequest', TestXHR);
    const progress = vi.fn();
    const file = new File(['video'], 'recording.mp4', { type: 'video/mp4' });
    const promise = uploadVideo(file, progress);
    const xhr = TestXHR.latest;
    expect(xhr.open).toHaveBeenCalledWith('POST', '/api/projects/local');
    const form = xhr.send.mock.calls[0] as unknown as [FormData];
    expect((form[0].get('file') as File).name).toBe('recording.mp4');
    xhr.upload.onprogress?.({ loaded: 3, total: 10, lengthComputable: true });
    expect(progress).toHaveBeenLastCalledWith(0.3);
    xhr.upload.onprogress?.({ loaded: 3, total: 0, lengthComputable: false });
    expect(progress).toHaveBeenLastCalledWith(null);
    xhr.onload?.();
    await expect(promise).resolves.toEqual({ id: 'uploaded' });
  });
  it('cancels the transfer when its AbortSignal is aborted', async () => {
    vi.stubGlobal('XMLHttpRequest', TestXHR);
    const controller = new AbortController();
    const promise = uploadVideo(new File(['video'], 'recording.mp4'), vi.fn(), controller.signal);
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
    expect(TestXHR.latest.abort).toHaveBeenCalledOnce();
  });
});
