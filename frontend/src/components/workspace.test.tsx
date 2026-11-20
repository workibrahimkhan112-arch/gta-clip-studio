// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { ProjectWorkspace } from '../pages/ProjectWorkspace';
import { TooltipProvider } from './ui';
import { useStudio } from '../stores/studio';
import { api } from '../services/api';
import {
  healthFixture,
  highlightFixture,
  integrationsFixture,
  jobFixture,
  projectFixture,
  settingsFixture,
} from '../test/fixtures';
import type { Highlight } from '../types/api';

// Test workspace orchestration without coupling to crop or subtitle editor internals.
vi.mock('./LayoutEditor', () => ({
  LayoutEditor: () => {
    const [draft, setDraft] = useState('');
    return (
      <section aria-label="Layout editor">
        <input
          aria-label="Layout draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <video aria-label="Layout source" />
      </section>
    );
  },
}));
vi.mock('./HighlightEditor', () => ({
  HighlightEditor: ({
    highlight,
    onClose,
  }: {
    highlight: Highlight | null;
    onClose: () => void;
  }) =>
    highlight ? (
      <section aria-label="Subtitle editor">
        <p>{highlight.title}</p>
        <button onClick={onClose}>Close subtitles</button>
      </section>
    ) : null,
}));

const originalRefresh = useStudio.getState().refresh;
const originalScrollIntoView = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  'scrollIntoView',
);

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(api, 'project').mockResolvedValue(projectFixture);
  vi.spyOn(api, 'projectProgress').mockResolvedValue(null);
  vi.spyOn(api, 'projectEvents').mockResolvedValue({
    complete: true,
    events: [],
    detail: 'Stored scan results',
  });
  vi.spyOn(api, 'integrations').mockResolvedValue(integrationsFixture);
  useStudio.setState({
    data: {
      projects: [projectFixture],
      highlights: [highlightFixture],
      jobs: [],
      exports: [],
      settings: settingsFixture,
      health: {
        ...healthFixture,
        capabilities: {
          ...healthFixture.capabilities,
          faster_whisper: true,
          caption_model_cached: true,
        },
      },
      benchmarks: [],
    },
    loading: {},
    loaded: { projects: true, highlights: true, jobs: true, settings: true, health: true },
    errors: {},
    refresh: vi.fn().mockResolvedValue(undefined),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (originalScrollIntoView)
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScrollIntoView);
  else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView');
  useStudio.setState({ refresh: originalRefresh });
});

function LocationControls() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="Current URL">{location.pathname + location.search}</output>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  );
}
async function workspace(search = '') {
  await act(async () => {
    render(
      <MemoryRouter
        initialEntries={[`/projects/${projectFixture.id}${search}`]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <TooltipProvider>
          <Routes>
            <Route
              path="/projects/:projectId"
              element={
                <>
                  <ProjectWorkspace />
                  <LocationControls />
                </>
              }
            />
          </Routes>
        </TooltipProvider>
      </MemoryRouter>,
    );
  });
}
function disclosure(name: string) {
  return screen.getByText(name, { exact: true }).closest('details') as HTMLDetailsElement;
}

describe('project workspace views', () => {
  it.each(['', '?tab=review', '?tab=unknown'])('defaults to Review for %s', async (search) => {
    await workspace(search);
    expect(screen.getByRole('tab', { name: 'Review' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByLabelText('Source video preview')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Event timeline' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: `Preview ${highlightFixture.title}` })).toBeNull();
    expect(screen.queryByRole('button', { name: /Export selected/ })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Layout editor' })).toBeNull();
    expect(screen.queryByText('Project details')).toBeNull();
    expect(screen.queryByText('Subtitles & AI review', { exact: true })).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Highlights' }));
    expect(screen.getByRole('button', { name: `Preview ${highlightFixture.title}` })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Export selected/ })).toBeTruthy();
    expect(disclosure('Subtitles & AI review').open).toBe(false);
    await user.click(screen.getByRole('tab', { name: 'Activity' }));
    expect(disclosure('Project details').open).toBe(false);
  });

  it('opens a dedicated Layout view from a deep link', async () => {
    await workspace('?tab=layout');
    expect(screen.getByRole('tab', { name: 'Layout' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('region', { name: 'Layout editor' })).toBeTruthy();
    expect(screen.queryByLabelText('Source video preview')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Event timeline' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Export selected/ })).toBeNull();
    expect(screen.queryByRole('button', { name: `Preview ${highlightFixture.title}` })).toBeNull();
    expect(screen.queryByText('Subtitles & AI review', { exact: true })).toBeNull();
  });

  it('preserves query parameters, browser history, and layout drafts across tab changes', async () => {
    const user = userEvent.setup();
    await workspace('?keep=1');
    await user.click(screen.getByRole('tab', { name: 'Layout' }));
    expect(screen.getByLabelText('Current URL').textContent).toBe(
      '/projects/project-1?keep=1&tab=layout',
    );
    await user.type(screen.getByLabelText('Layout draft'), 'unsaved crop');
    await user.click(screen.getByRole('tab', { name: 'Review' }));
    expect(screen.getByLabelText('Current URL').textContent).toBe('/projects/project-1?keep=1');
    expect(screen.queryByRole('region', { name: 'Layout editor' })).toBeNull();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Back' }));
    await waitFor(() => {
      expect(screen.getByLabelText('Current URL').textContent).toBe(
        '/projects/project-1?keep=1&tab=layout',
      );
      expect(screen.getByRole('tab', { name: 'Layout' }).getAttribute('aria-selected')).toBe(
        'true',
      );
    });
    expect((screen.getByLabelText('Layout draft') as HTMLInputElement).value).toBe('unsaved crop');
    expect(screen.queryByRole('heading', { name: 'Event timeline' })).toBeNull();
  });

  it('keeps source seeking and subtitle editing in Review', async () => {
    const user = userEvent.setup();
    await workspace();
    const video = screen.getByLabelText('Source video preview') as HTMLVideoElement;
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 });
    fireEvent.loadedMetadata(video);
    await user.click(
      screen.getByRole('button', { name: `Jump to ${highlightFixture.title}, 0:10` }),
    );
    expect(video.currentTime).toBe(highlightFixture.start);
    fireEvent.change(screen.getByRole('slider', { name: 'Seek source video' }), {
      target: { value: '32' },
    });
    expect(video.currentTime).toBe(32);
    await user.click(screen.getAllByRole('button', { name: 'Edit subtitles' })[0]);
    expect(screen.getByRole('region', { name: 'Subtitle editor' })).toBeTruthy();
  });

  it('keeps active jobs and failures visible while completed jobs stay collapsed in both views', async () => {
    const user = userEvent.setup();
    useStudio.setState((state) => ({
      data: {
        ...state.data,
        jobs: [
          { ...jobFixture, status: 'running', stage: 'rendering', progress: 0.4 },
          {
            ...jobFixture,
            id: 'failed',
            status: 'failed',
            stage: 'failed',
            error: 'Output folder is not writable',
          },
          {
            ...jobFixture,
            id: 'completed',
            status: 'completed',
            stage: 'completed',
            detail: 'Earlier export finished',
          },
        ],
      },
    }));
    vi.spyOn(api, 'cancelJob').mockResolvedValue({ ...jobFixture, status: 'cancelled' });
    await workspace();
    for (const view of ['Review', 'Layout']) {
      await user.click(screen.getByRole('tab', { name: view }));
      expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('40');
      expect(screen.getByRole('button', { name: 'Cancel export job' })).toBeTruthy();
      expect(screen.getByRole('alert').textContent).toContain('Output folder is not writable');
    }
    await user.click(screen.getByRole('tab', { name: 'Activity' }));
    expect(screen.getByText('Earlier export finished').closest('details')?.open).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Cancel export job' }));
    await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith(jobFixture.id));
  });

  it('opens subtitle tools for selected clips', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'projectCaptions').mockResolvedValue({ ...jobFixture, kind: 'captions' });
    await workspace();
    await user.click(screen.getByRole('tab', { name: 'Highlights' }));
    await user.click(screen.getByText('Subtitles & AI review', { exact: true }));
    expect(disclosure('Subtitles & AI review').open).toBe(true);
    await user.click(screen.getByRole('checkbox', { name: /Replace existing transcripts/ }));
    await user.click(screen.getByRole('button', { name: 'Generate subtitles' }));
    await waitFor(() =>
      expect(api.projectCaptions).toHaveBeenCalledWith(projectFixture.id, true, [
        highlightFixture.id,
      ]),
    );
  });

  it('retains import progress without mounting either video editor', async () => {
    const user = userEvent.setup();
    const project = {
      ...projectFixture,
      status: 'importing' as const,
      highlight_count: 0,
      metadata: {},
      source_path: '',
    };
    useStudio.setState((state) => ({
      data: { ...state.data, projects: [project], highlights: [] },
    }));
    vi.mocked(api.project).mockResolvedValue(project);
    await workspace('?tab=layout');
    expect(screen.getByText('Layout available after import finishes.')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Layout editor' })).toBeNull();
    await user.click(screen.getByRole('tab', { name: 'Review' }));
    expect(screen.getByText('Source available after import finishes.')).toBeTruthy();
    expect(screen.queryByLabelText('Source video preview')).toBeNull();
    expect(screen.getByRole('button', { name: 'Find highlights' }).hasAttribute('disabled')).toBe(
      true,
    );
  });

  it('shows progress-fetch errors outside the collapsed details in Layout', async () => {
    vi.mocked(api.projectProgress).mockRejectedValue(new Error('Progress is unavailable'));
    await workspace('?tab=layout');
    expect(screen.getByRole('alert').textContent).toContain('Progress is unavailable');
    expect(screen.getByRole('alert').closest('details')).toBeNull();
  });
});
