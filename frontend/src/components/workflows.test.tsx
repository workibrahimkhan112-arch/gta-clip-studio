// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { HighlightEditor } from './HighlightEditor';
import { ExportControls } from './ExportControls';
import { JobCard } from './JobCard';
import { Settings } from '../pages/Settings';
import { TooltipProvider } from './ui';
import { useStudio } from '../stores/studio';
import { api } from '../services/api';
import {
  healthFixture,
  chatgptFixture,
  usageFixture,
  integrationsFixture,
  highlightFixture,
  jobFixture,
  projectFixture,
  settingsFixture,
} from '../test/fixtures';
import type { Highlight } from '../types/api';

const originalRefresh = useStudio.getState().refresh;
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.spyOn(api, 'integrations').mockResolvedValue(integrationsFixture);
  vi.spyOn(api, 'chatgptStatus').mockResolvedValue(chatgptFixture);
  vi.spyOn(api, 'usage').mockResolvedValue(usageFixture);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
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
    loading: {},
    loaded: {
      projects: true,
      highlights: true,
      settings: true,
      health: true,
      jobs: true,
      benchmarks: true,
    },
    errors: {},
    refresh: vi.fn().mockResolvedValue(undefined),
  });
  vi.spyOn(api, 'patchHighlight').mockImplementation(async (_id, patch) => ({
    ...useStudio.getState().data.highlights[0],
    ...patch,
  }));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useStudio.setState({ refresh: originalRefresh });
});
function wrap(children: React.ReactNode) {
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <TooltipProvider>{children}</TooltipProvider>
    </MemoryRouter>
  );
}
function editor(highlight: Highlight = highlightFixture, onClose = vi.fn()) {
  useStudio.setState((state) => ({ data: { ...state.data, highlights: [highlight] } }));
  render(wrap(<HighlightEditor highlight={highlight} onClose={onClose} />));
  return onClose;
}

describe('caption editor', () => {
  it('saves manually entered text with clip-relative timestamps without Whisper', async () => {
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole('button', { name: 'Add caption' }));
    await user.type(screen.getByLabelText('Caption 1 text'), 'Words actually spoken');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenCalledWith('highlight-1', {
        transcript: [{ start: 0, end: 3, text: 'Words actually spoken', words: [] }],
      }),
    );
    expect(useStudio.getState().data.highlights[0].transcript[0].text).toBe(
      'Words actually spoken',
    );
  });
  it('rejects empty caption rows before making a request', async () => {
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole('button', { name: 'Add caption' }));
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(screen.getByRole('alert').textContent).toContain('text cannot be empty');
    expect(api.patchHighlight).not.toHaveBeenCalled();
  });
  it('preserves untouched word timing and clears timing only for edited segments', async () => {
    const user = userEvent.setup();
    const original = {
      start: 0,
      end: 2,
      text: 'Original words',
      words: [{ start: 0.1, end: 1, word: 'Original' }],
    };
    editor({ ...highlightFixture, transcript: [original] });
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(api.patchHighlight).toHaveBeenCalledWith('highlight-1', {}));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Saved' }).hasAttribute('disabled')).toBe(false),
    );
    expect(useStudio.getState().data.highlights[0].transcript).toEqual([original]);
    await user.clear(screen.getByLabelText('Caption 1 text'));
    await user.type(screen.getByLabelText('Caption 1 text'), 'Corrected words');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenLastCalledWith(
        'highlight-1',
        expect.objectContaining({
          transcript: [{ ...original, text: 'Corrected words', words: [] }],
        }),
      ),
    );
  });
  it('accepts supplied word timings for animated captions without guessing them', async () => {
    editor({
      ...highlightFixture,
      transcript: [{ start: 0, end: 3, text: 'Actual words', words: [] }],
    });
    fireEvent.click(screen.getByText('Timing and words'));
    fireEvent.click(screen.getByText('Word timings · animated captions'));
    const words = [
      { start: 0.2, end: 1, word: 'Actual' },
      { start: 1.2, end: 2.5, word: 'words' },
    ];
    fireEvent.change(screen.getByLabelText('Word timings JSON'), {
      target: { value: JSON.stringify(words) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply word timings' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenCalledWith(
        'highlight-1',
        expect.objectContaining({
          transcript: [{ start: 0, end: 3, text: 'Actual words', words }],
        }),
      ),
    );
  });
  it('rejects word timings outside their caption before applying them', () => {
    editor({
      ...highlightFixture,
      transcript: [{ start: 0, end: 3, text: 'Actual words', words: [] }],
    });
    fireEvent.click(screen.getByText('Timing and words'));
    fireEvent.click(screen.getByText('Word timings · animated captions'));
    fireEvent.change(screen.getByLabelText('Word timings JSON'), {
      target: { value: '[{"start":0,"end":9,"word":"Actual"}]' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply word timings' }));
    expect(screen.getByRole('alert').textContent).toContain('within this caption');
    expect(api.patchHighlight).not.toHaveBeenCalled();
  });
  it('previews live text edits at a clicked cue using one source video', () => {
    editor({
      ...highlightFixture,
      transcript: [{ start: 1, end: 3, text: 'Original subtitle', words: [] }],
    });
    const video = screen.getByLabelText('Subtitle preview source') as HTMLVideoElement;
    Object.defineProperties(video, {
      duration: { configurable: true, value: 100 },
      readyState: { configurable: true, value: 4 },
    });
    fireEvent.loadedMetadata(video);
    fireEvent.click(screen.getByRole('button', { name: 'Preview subtitle 1' }));
    expect(video.currentTime).toBe(highlightFixture.start + 1);
    fireEvent.change(screen.getByLabelText('Caption 1 text'), {
      target: { value: 'Corrected subtitle' },
    });
    expect(screen.getByLabelText('Subtitle overlay').textContent).toBe('Corrected subtitle');
    expect(document.querySelectorAll('video')).toHaveLength(1);
    expect(screen.getByLabelText('Preview time').textContent).toContain('0:01.00');
  });
  it('does not invent animated timing after text edits and exports No captions explicitly', async () => {
    vi.spyOn(api, 'exportHighlight').mockResolvedValue(jobFixture);
    editor({
      ...highlightFixture,
      transcript: [{ start: 0, end: 3, text: 'Real text', words: [] }],
    });
    fireEvent.click(screen.getByRole('combobox', { name: 'Export captions' }));
    fireEvent.click(screen.getByRole('option', { name: 'Animated captions' }));
    expect(
      (screen.getByRole('button', { name: 'Save & export' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/Animated subtitles need word timings/)).toBeTruthy();
    fireEvent.click(screen.getByRole('combobox', { name: 'Export captions' }));
    fireEvent.click(screen.getByRole('option', { name: 'No captions' }));
    expect(screen.getByLabelText('Subtitle overlay').textContent).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Save & export' }));
    await waitFor(() =>
      expect(api.exportHighlight).toHaveBeenCalledWith(highlightFixture.id, 'none'),
    );
  });
  it('requires explicit confirmation before discarding unsaved edits', async () => {
    const user = userEvent.setup();
    const close = editor();
    await user.click(screen.getByText('Clip details'));
    await user.type(screen.getByLabelText('Clip title'), ' edited');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(close).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(close).toHaveBeenCalledOnce();
  });
});

describe('exports and persisted settings', () => {
  it('explains and blocks batches larger than the backend limit', () => {
    render(
      wrap(
        <ExportControls
          highlights={Array.from({ length: 51 }, (_, i) => ({
            ...highlightFixture,
            id: `clip-${i}`,
            selected: true,
          }))}
        />,
      ),
    );
    expect(screen.getByRole('button', { name: /Export selected/ }).hasAttribute('disabled')).toBe(
      true,
    );
    expect(screen.getByText(/Select at most 50 clips/)).toBeTruthy();
  });
  it('sends only selected visible IDs and the chosen style to batch export', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'batchExport').mockResolvedValue([jobFixture]);
    render(
      wrap(
        <ExportControls
          highlights={[
            highlightFixture,
            { ...highlightFixture, id: 'unselected', selected: false },
          ]}
        />,
      ),
    );
    await user.click(screen.getByRole('button', { name: /Export selected/ }));
    await waitFor(() => expect(api.batchExport).toHaveBeenCalledWith(['highlight-1'], 'standard'));
    expect(useStudio.getState().data.jobs[0].progress).toBeNull();
  });
  it('persists select-all changes without re-patching already selected clips', async () => {
    const user = userEvent.setup();
    render(
      wrap(
        <ExportControls
          highlights={[
            highlightFixture,
            { ...highlightFixture, id: 'unselected', selected: false },
          ]}
        />,
      ),
    );
    await user.click(screen.getByRole('checkbox', { name: 'Select all visible highlights' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenCalledWith('unselected', { selected: true }),
    );
    expect(api.patchHighlight).toHaveBeenCalledOnce();
  });
  it('does not invent unknown progress, speed, or ETA and cancels through the API', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'cancelJob').mockResolvedValue({ ...jobFixture, status: 'cancelled' });
    render(wrap(<JobCard job={jobFixture} />));
    expect(screen.getByRole('progressbar').hasAttribute('aria-valuenow')).toBe(false);
    expect(screen.getAllByText('Not reported').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Cancel export job' }));
    await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith('job-1'));
    expect(useStudio.getState().data.jobs[0].status).toBe('cancelled');
  });
  it('PUTs the complete settings object and updates the persisted form', async () => {
    vi.spyOn(api, 'saveSettings').mockImplementation(async (settings) => settings);
    const user = userEvent.setup();
    render(wrap(<Settings />));
    fireEvent.change(screen.getByLabelText('Maximum highlights'), { target: { value: '12' } });
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith({ ...settingsFixture, max_clips: 12 }),
    );
    await act(async () => {});
    expect(useStudio.getState().data.settings?.max_clips).toBe(12);
    expect(screen.getByText('All preferences saved on server')).toBeTruthy();
  });
});
