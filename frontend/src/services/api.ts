import type {
  Benchmark,
  ChatGPTStatus,
  ChatGPTModel,
  SubtitleStyle,
  UsageSummary,
  CloudProvider,
  Integrations,
  LayoutConfig,
  EventSnapshot,
  CaptionStyle,
  CleanupResult,
  ExportRecord,
  Health,
  Highlight,
  Job,
  Project,
  Settings,
  TranscriptSegment,
} from '../types/api';

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export function responseError(data: unknown, status: number): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail))
      return detail
        .map(
          (entry: { loc?: unknown[]; msg?: string }) =>
            `${entry.loc?.filter((v) => v !== 'body').join('.') || 'Request'}: ${entry.msg || 'Invalid value'}`,
        )
        .join('; ');
  }
  return `Request failed (${status}). Please try again.`;
}
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
      signal: init.signal ?? AbortSignal.timeout(30000),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(
      'Cannot reach the local server. Check that the backend is running on port 8000.',
      0,
    );
  }
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    throw new ApiError(responseError(data, response.status), response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});
const id = encodeURIComponent;
export const api = {
  projects: () => request<Project[]>('/projects'),
  project: (projectId: string, signal?: AbortSignal) =>
    request<Project>(`/projects/${id(projectId)}`, { signal }),
  local: (path: string) => request<Project>('/projects/local', json('POST', { path })),
  youtube: (url: string) =>
    request<{ project: Project; job: Job }>('/projects/youtube', json('POST', { url })),
  analyze: (projectId: string, force = false) =>
    request<Job>(`/projects/${id(projectId)}/analyze`, json('POST', { force })),
  saveLayout: (projectId: string, layout: LayoutConfig) =>
    request<Project>(`/projects/${id(projectId)}/layout`, json('PATCH', layout)),
  generateCaptions: (highlightId: string, force = false) =>
    request<Job>(`/highlights/${id(highlightId)}/captions`, json('POST', { force })),
  projectCaptions: (projectId: string, force = false) =>
    request<Job>(`/projects/${id(projectId)}/captions`, json('POST', { force })),
  scoreProject: (projectId: string, force = false) =>
    request<Job>(`/projects/${id(projectId)}/score`, json('POST', { force })),
  suggestTitles: (highlightId: string, force = false) =>
    request<Job>(`/highlights/${id(highlightId)}/titles`, json('POST', { force })),
  chatgptStatus: () => request<ChatGPTStatus>('/chatgpt'),
  connectChatGPT: (account_id?: string) =>
    request<{ authorization_url: string; expires_at: string }>(
      '/chatgpt/connect',
      json('POST', account_id ? { account_id } : {}),
    ),
  selectChatGPT: (account_id: string) =>
    request<ChatGPTStatus>('/chatgpt/select', json('POST', { account_id })),
  disconnectChatGPT: (account_id?: string) =>
    request<ChatGPTStatus>('/chatgpt/disconnect', json('POST', account_id ? { account_id } : {})),
  cancelChatGPT: () => request<ChatGPTStatus>('/chatgpt/cancel', json('POST')),
  chatgptModels: () => request<ChatGPTModel[]>('/chatgpt/models'),
  usage: () => request<UsageSummary>('/usage'),
  integrations: () => request<Integrations>('/integrations'),
  saveIntegration: (provider: CloudProvider, api_key: string) =>
    request<Integrations>(`/integrations/${provider}`, json('PUT', { api_key })),
  deleteProject: (projectId: string) => request<void>(`/projects/${id(projectId)}`, json('DELETE')),
  projectEvents: (projectId: string) => request<EventSnapshot>(`/projects/${id(projectId)}/events`),
  projectHighlights: (projectId: string) =>
    request<Highlight[]>(`/projects/${id(projectId)}/highlights`),
  projectProgress: (projectId: string) =>
    request<Job | null>(`/projects/${id(projectId)}/progress`),
  highlights: () => request<Highlight[]>('/highlights'),
  patchHighlight: (
    highlightId: string,
    patch: {
      selected?: boolean;
      title?: string;
      transcript?: TranscriptSegment[];
      subtitle_style?: SubtitleStyle | null;
    },
  ) => request<Highlight>(`/highlights/${id(highlightId)}`, json('PATCH', patch)),
  exportHighlight: (highlightId: string, caption_style: CaptionStyle) =>
    request<Job>(`/highlights/${id(highlightId)}/export`, json('POST', { caption_style })),
  batchExport: (highlight_ids: string[], caption_style: CaptionStyle) =>
    request<Job[]>('/exports/batch', json('POST', { highlight_ids, caption_style })),
  exports: () => request<ExportRecord[]>('/exports'),
  jobs: () => request<Job[]>('/jobs'),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${id(jobId)}/cancel`, json('POST')),
  health: () => request<Health>('/health'),
  settings: () => request<Settings>('/settings'),
  saveSettings: (settings: Settings) => request<Settings>('/settings', json('PUT', settings)),
  benchmarks: () => request<Benchmark[]>('/benchmarks'),
  cleanup: () => request<CleanupResult>('/cache/cleanup', json('POST')),
  media: (projectId: string) => `/api/projects/${id(projectId)}/media`,
  thumbnail: (projectId: string) => `/api/projects/${id(projectId)}/thumbnail`,
};

export function uploadVideo(
  file: File,
  onProgress: (fraction: number | null) => void,
  signal?: AbortSignal,
): Promise<Project> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener('abort', abort);
    xhr.open('POST', '/api/projects/local');
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) =>
      onProgress(event.lengthComputable ? event.loaded / event.total : null);
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as Project);
      else reject(new ApiError(responseError(xhr.response, xhr.status), xhr.status));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new ApiError('Upload failed. Check the local server connection and try again.', 0));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException('Upload cancelled', 'AbortError'));
    };
    if (signal?.aborted) {
      reject(new DOMException('Upload cancelled', 'AbortError'));
      return;
    }
    signal?.addEventListener('abort', abort, { once: true });
    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });
}
