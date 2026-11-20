// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { IntegrationSettings } from './IntegrationSettings';
import { ProjectEnhancements } from './ProjectEnhancements';
import { Layout } from './Layout';
import { HighlightEditor } from './HighlightEditor';
import { ExportControls } from './ExportControls';
import { Highlights } from '../pages/Highlights';
import { Exports } from '../pages/Exports';
import { Settings } from '../pages/Settings';
import { JobCard } from './JobCard';
import { TooltipProvider } from './ui';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import {
  aiReviewFixture,
  chatgptFixture,
  usageFixture,
  healthFixture,
  highlightFixture,
  integrationsFixture,
  jobFixture,
  layoutFixture,
  projectFixture,
  settingsFixture,
} from '../test/fixtures';
import type { Settings as StudioSettings } from '../types/api';

const originalRefresh = useStudio.getState().refresh;
function wrap(children: React.ReactNode) {
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <TooltipProvider>{children}</TooltipProvider>
    </MemoryRouter>
  );
}
function StoreEditor() {
  const highlight = useStudio((s) => s.data.highlights[0]);
  return <HighlightEditor highlight={highlight} onClose={() => {}} />;
}
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.spyOn(api, 'integrations').mockResolvedValue(integrationsFixture);
  vi.spyOn(api, 'chatgptStatus').mockResolvedValue(chatgptFixture);
  vi.spyOn(api, 'usage').mockResolvedValue(usageFixture);
  useStudio.setState({
    data: {
      projects: [projectFixture],
      highlights: [highlightFixture],
      jobs: [],
      exports: [],
      settings: settingsFixture,
      health: healthFixture,
      benchmarks: [],
    },
    loaded: {
      projects: true,
      highlights: true,
      jobs: true,
      exports: true,
      settings: true,
      health: true,
      benchmarks: true,
    },
    loading: {},
    errors: {},
    refresh: vi.fn().mockResolvedValue(undefined),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useStudio.setState({ refresh: originalRefresh });
});

describe('write-only local-server credentials', () => {
  it.each(['openai', 'gemini', 'groq'] as const)(
    'clears %s key inputs on save, stores only public status, and removes keys with an empty value',
    async (provider) => {
      const saved = {
        ...integrationsFixture,
        [provider]: { configured: true, source: 'local' as const },
      };
      vi.spyOn(api, 'saveIntegration').mockResolvedValue(saved);
      const storage = vi.spyOn(Storage.prototype, 'setItem');
      render(<IntegrationSettings />);
      expect(await screen.findAllByText('Not configured')).toHaveLength(3);
      const input = screen.getByLabelText(`${provider} API key`) as HTMLInputElement;
      expect(input.type).toBe('password');
      fireEvent.change(input, { target: { value: 'test-key-never-persist' } });
      fireEvent.click(screen.getByRole('button', { name: `Save ${provider} key` }));
      expect(input.value).toBe('');
      await waitFor(() =>
        expect(api.saveIntegration).toHaveBeenCalledWith(provider, 'test-key-never-persist'),
      );
      expect(await screen.findByText('Configured · local')).toBeTruthy();
      expect(storage).not.toHaveBeenCalled();
      expect(JSON.stringify(useStudio.getState().data)).not.toContain('test-key-never-persist');
      fireEvent.click(screen.getByRole('button', { name: `Remove ${provider} local key` }));
      await waitFor(() => expect(api.saveIntegration).toHaveBeenLastCalledWith(provider, ''));
    },
  );
  it('clears failed submissions and never echoes a secret-bearing server error', async () => {
    vi.spyOn(api, 'saveIntegration').mockRejectedValue(new Error('bad secret-key-123'));
    render(<IntegrationSettings />);
    fireEvent.change(screen.getByLabelText('groq API key'), {
      target: { value: 'secret-key-123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save groq key' }));
    expect((screen.getByLabelText('groq API key') as HTMLInputElement).value).toBe('');
    expect((await screen.findByRole('alert')).textContent).not.toContain('secret-key-123');
  });
});

describe('caption and editorial jobs', () => {
  it('collapses and restores the desktop sidebar', () => {
    render(wrap(<Layout />));
    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeTruthy();
  });
  it('queues caption retry without touching detection or sending force implicitly', async () => {
    vi.spyOn(api, 'generateCaptions').mockResolvedValue({ ...jobFixture, kind: 'captions' });
    useStudio.setState((s) => ({
      data: {
        ...s.data,
        highlights: [
          { ...highlightFixture, caption_status: 'failed', caption_error: 'Model unavailable' },
        ],
      },
    }));
    render(wrap(<StoreEditor />));
    fireEvent.click(screen.getByRole('button', { name: 'Retry captions' }));
    await waitFor(() => expect(api.generateCaptions).toHaveBeenCalledWith('highlight-1', false));
    expect(useStudio.getState().data.highlights[0].score).toBe(70);
    expect(useStudio.getState().data.jobs[0].kind).toBe('captions');
  });
  it('refreshes displayed generated text and preserves unsaved manual edits on subsequent updates', async () => {
    render(wrap(<StoreEditor />));
    const segment = { start: 0, end: 2, text: 'Generated real speech', words: [] };
    act(() =>
      useStudio
        .getState()
        .upsertHighlight({ ...highlightFixture, transcript: [segment], caption_status: 'ready' }),
    );
    expect(((await screen.findByLabelText('Caption 1 text')) as HTMLTextAreaElement).value).toBe(
      'Generated real speech',
    );
    fireEvent.change(screen.getByLabelText('Caption 1 text'), {
      target: { value: 'Unsaved correction' },
    });
    act(() =>
      useStudio.getState().upsertHighlight({
        ...highlightFixture,
        transcript: [{ ...segment, text: 'New server transcript' }],
      }),
    );
    expect((screen.getByLabelText('Caption 1 text') as HTMLTextAreaElement).value).toBe(
      'Unsaved correction',
    );
  });
  it('requires explicit force to replace an existing transcript', async () => {
    vi.spyOn(api, 'generateCaptions').mockResolvedValue({ ...jobFixture, kind: 'captions' });
    useStudio.setState((s) => ({
      data: {
        ...s.data,
        highlights: [
          {
            ...highlightFixture,
            transcript: [{ start: 0, end: 2, text: 'Manual transcript', words: [] }],
          },
        ],
      },
    }));
    render(wrap(<StoreEditor />));
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(api.generateCaptions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Replace subtitles' }));
    await waitFor(() => expect(api.generateCaptions).toHaveBeenCalledWith('highlight-1', true));
  });
  it('only queues selected highlights for subtitles', async () => {
    vi.spyOn(api, 'projectCaptions').mockResolvedValue({ ...jobFixture, kind: 'captions' });
    render(
      wrap(
        <ProjectEnhancements
          projectId="project-1"
          highlights={[
            highlightFixture,
            { ...highlightFixture, id: 'highlight-2', selected: false },
          ]}
        />,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Generate subtitles' }));
    await waitFor(() =>
      expect(api.projectCaptions).toHaveBeenCalledWith('project-1', false, ['highlight-1']),
    );
  });
  it('disables project tools when no clips are selected', () => {
    render(
      wrap(
        <ProjectEnhancements
          projectId="project-1"
          highlights={[{ ...highlightFixture, selected: false }]}
        />,
      ),
    );
    expect(
      (screen.getByRole('button', { name: 'Generate subtitles' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Review highlights with AI' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
  it('offers selected clip tools from the Highlights page', async () => {
    vi.spyOn(api, 'projectCaptions').mockResolvedValue({ ...jobFixture, kind: 'captions' });
    render(wrap(<Highlights />));
    fireEvent.click(screen.getByText('AI review & subtitles for selected clips'));
    fireEvent.click(screen.getByRole('button', { name: 'Generate subtitles' }));
    await waitFor(() =>
      expect(api.projectCaptions).toHaveBeenCalledWith('project-1', false, ['highlight-1']),
    );
  });
  it('deletes one highlight after confirmation', async () => {
    vi.spyOn(api, 'deleteHighlight').mockResolvedValue(undefined);
    render(wrap(<Highlights />));
    fireEvent.click(screen.getByRole('button', { name: 'Delete Test highlight' }));
    expect(api.deleteHighlight).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Delete highlight' }));
    await waitFor(() => expect(api.deleteHighlight).toHaveBeenCalledWith('highlight-1'));
    await waitFor(() => expect(useStudio.getState().data.highlights).toHaveLength(0));
  });
  it('confirms before deleting an exported video', async () => {
    vi.spyOn(api, 'deleteExport').mockResolvedValue(undefined);
    useStudio.setState((s) => ({
      data: {
        ...s.data,
        exports: [
          {
            id: 'export-1',
            project_id: 'project-1',
            highlight_id: 'highlight-1',
            job_id: 'job-1',
            status: 'completed',
            output_path: 'clip.mp4',
            filename: 'clip.mp4',
            created_at: '2026-01-01T00:00:00Z',
            error: null,
            download_url: '/api/exports/export-1/file',
          },
        ],
      },
    }));
    render(wrap(<Exports />));
    fireEvent.click(screen.getByRole('button', { name: 'Delete export clip.mp4' }));
    expect(api.deleteExport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Delete video' }));
    await waitFor(() => expect(api.deleteExport).toHaveBeenCalledWith('export-1'));
  });
  it('honors the saved No captions opt-out for explicit and batch generation', () => {
    vi.spyOn(api, 'generateCaptions');
    vi.spyOn(api, 'projectCaptions');
    useStudio.getState().setSettings({ ...settingsFixture, caption_style: 'none' });
    const { unmount } = render(wrap(<StoreEditor />));
    expect(
      (screen.getByRole('button', { name: 'Generate captions' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(api.generateCaptions).not.toHaveBeenCalled();
    unmount();
    render(wrap(<ProjectEnhancements projectId="project-1" highlights={[highlightFixture]} />));
    expect(
      (
        screen.getByRole('button', {
          name: 'Generate subtitles',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(api.projectCaptions).not.toHaveBeenCalled();
  });
  it.each([
    [
      { ...settingsFixture, ai_scoring_provider: 'none' },
      integrationsFixture,
      'Choose OpenAI, Gemini, or Groq',
    ],
    [
      { ...settingsFixture, ai_scoring_provider: 'gemini' },
      integrationsFixture,
      'Enable and save cloud consent',
    ],
    [
      { ...settingsFixture, ai_scoring_provider: 'groq', cloud_consent: true },
      integrationsFixture,
      'Configure a groq API key',
    ],
  ] as const)(
    'blocks review until provider, consent and credentials are configured',
    async (settings, integrations, text) => {
      useStudio.getState().setSettings(settings as StudioSettings);
      vi.mocked(api.integrations).mockResolvedValue(integrations);
      render(wrap(<ProjectEnhancements projectId="project-1" highlights={[highlightFixture]} />));
      await waitFor(() => expect(screen.getByText(new RegExp(text))).toBeTruthy());
      expect(
        (screen.getByRole('button', { name: 'Review highlights with AI' }) as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    },
  );
  it('queues manual review from current settings and server status without resending settings or keys', async () => {
    const score = vi.spyOn(api, 'scoreProject').mockResolvedValue({ ...jobFixture, kind: 'score' });
    render(wrap(<ProjectEnhancements projectId="project-1" highlights={[highlightFixture]} />));
    act(() =>
      useStudio.getState().setSettings({
        ...settingsFixture,
        ai_scoring_provider: 'gemini',
        cloud_consent: true,
        ai_scoring_model: 'custom-model',
      }),
    );
    vi.mocked(api.integrations).mockResolvedValue({
      ...integrationsFixture,
      gemini: { configured: true, source: 'environment' },
    });
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Refresh key status' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh key status' }));
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Review highlights with AI' }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByLabelText('Bypass cached reviews (may use additional quota)'));
    fireEvent.click(screen.getByRole('button', { name: 'Review highlights with AI' }));
    await waitFor(() => expect(score).toHaveBeenCalledWith('project-1', true, ['highlight-1']));
    expect(useStudio.getState().data.highlights[0]).toEqual(highlightFixture);
  });
  it.each(['captions', 'score', 'titles'] as const)(
    'labels %s job failures truthfully without removing highlights',
    (kind) => {
      render(
        wrap(
          <JobCard
            job={{ ...jobFixture, kind, status: 'failed', error: 'Provider unavailable' }}
          />,
        ),
      );
      expect(
        screen.getByText(
          kind === 'captions'
            ? 'Caption generation'
            : kind === 'titles'
              ? 'Title suggestions'
              : 'Editorial AI review',
        ),
      ).toBeTruthy();
      expect(screen.getByRole('alert').textContent).toContain('Provider unavailable');
      expect(useStudio.getState().data.highlights).toHaveLength(1);
    },
  );
});

describe('layout guards and separate scores', () => {
  it('blocks both batch and individual split exports until the actual facecam is saved', () => {
    useStudio.setState((s) => ({
      data: { ...s.data, projects: [{ ...projectFixture, layout: layoutFixture }] },
    }));
    const { unmount } = render(wrap(<ExportControls highlights={[highlightFixture]} />));
    expect(
      (screen.getByRole('button', { name: /Export selected/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByRole('link', { name: /Open.*layout/ }).getAttribute('href')).toBe(
      '/projects/project-1?tab=layout',
    );
    unmount();
    render(wrap(<StoreEditor />));
    expect(
      (screen.getByRole('button', { name: 'Save & export' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
  it('shows local and AI scores separately, with rubric, evidence and review errors', () => {
    useStudio.setState((s) => ({
      data: {
        ...s.data,
        highlights: [
          { ...highlightFixture, ai_review: aiReviewFixture, ai_error: 'Quota reached' },
        ],
      },
    }));
    render(wrap(<StoreEditor />));
    fireEvent.click(screen.getByText('Clip details'));
    expect(screen.getByText('82/100')).toBeTruthy();
    expect(screen.getByText('Hook')).toBeTruthy();
    expect(screen.getByText(/Transcript only · no visual judgment/)).toBeTruthy();
    expect(screen.getByText(/Quota reached.*previous review/)).toBeTruthy();
    expect(useStudio.getState().data.highlights[0].score).toBe(70);
  });
  it('offers explicit AI sorting while leaving local score sorting as default', () => {
    render(wrap(<Highlights />));
    const sort = screen.getByRole('combobox', { name: 'Sort highlights' });
    expect(sort.textContent).toContain('Detection score');
    fireEvent.click(sort);
    expect(screen.getByRole('option', { name: 'AI review score' })).toBeTruthy();
  });
  it('saves OpenAI plan mode and explicit daily request, budget and output controls', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'saveSettings').mockImplementation(async (settings) => settings);
    render(wrap(<Settings />));
    await user.click(screen.getByRole('tab', { name: 'AI review' }));
    fireEvent.click(screen.getByRole('combobox', { name: 'Review provider' }));
    fireEvent.click(screen.getByRole('option', { name: 'OpenAI' }));
    fireEvent.click(screen.getByRole('combobox', { name: 'OpenAI connection mode' }));
    fireEvent.click(screen.getByRole('option', { name: 'ChatGPT plan · preview' }));
    fireEvent.change(screen.getByLabelText('Daily cloud request limit'), {
      target: { value: '7' },
    });
    fireEvent.change(screen.getByLabelText('Daily API budget (USD)'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('Maximum API output tokens'), {
      target: { value: '128' },
    });
    fireEvent.click(screen.getByLabelText('Automatic AI review'));
    fireEvent.click(screen.getByLabelText('Allow cloud processing'));
    fireEvent.click(screen.getByRole('button', { name: 'Save preferences' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith({
        ...settingsFixture,
        ai_scoring_provider: 'openai',
        openai_auth_mode: 'chatgpt',
        ai_scoring_model: '',
        ai_daily_request_limit: 7,
        ai_daily_budget_usd: 0,
        ai_max_output_tokens: 128,
        auto_ai_review: true,
        cloud_consent: true,
      }),
    );
    expect(screen.getByText(/API requests are disabled by the zero-dollar budget/)).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: 'Connections & usage' }));
    expect(screen.getByText(/Unpriced API models are blocked/)).toBeTruthy();
  });
  it('saves complete cloud and caption settings including consent without ever including API keys', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'saveSettings').mockImplementation(async (settings) => settings);
    render(wrap(<Settings />));
    await user.click(screen.getByRole('tab', { name: 'AI review' }));
    fireEvent.change(screen.getByLabelText('Review model'), { target: { value: 'custom-model' } });
    fireEvent.change(screen.getByLabelText('Maximum review candidates'), {
      target: { value: '6' },
    });
    fireEvent.change(screen.getByLabelText('Frames per candidate'), { target: { value: '3' } });
    fireEvent.click(screen.getByLabelText('Allow cloud processing'));
    fireEvent.click(screen.getByRole('button', { name: 'Save preferences' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith({
        ...settingsFixture,
        ai_scoring_model: 'custom-model',
        ai_scoring_max_candidates: 6,
        ai_scoring_frames: 3,
        cloud_consent: true,
      }),
    );
    expect(vi.mocked(api.saveSettings).mock.calls[0][0]).not.toHaveProperty('api_key');
    expect(vi.mocked(api.saveSettings).mock.calls[0][0]).not.toHaveProperty('openai_api_key');
  });
  it('keeps unsaved settings edits when switching sections', async () => {
    const user = userEvent.setup();
    render(wrap(<Settings />));
    fireEvent.change(screen.getByLabelText('Maximum highlights'), { target: { value: '12' } });
    await user.click(screen.getByRole('tab', { name: 'Export & subtitles' }));
    expect(screen.getByRole('combobox', { name: 'Subtitle style' })).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: 'Analysis' }));
    expect((screen.getByLabelText('Maximum highlights') as HTMLInputElement).value).toBe('12');
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
  });
});
