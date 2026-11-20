// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ImportDialog } from './ImportDialog';
import { TooltipProvider } from './ui';
import { useStudio } from '../stores/studio';
import { useUI } from '../stores/ui';
import { api } from '../services/api';
import { healthFixture, jobFixture, projectFixture } from '../test/fixtures';

const originalRefresh = useStudio.getState().refresh;
beforeEach(() => {
  useUI.setState({ importOpen: true, toasts: [] });
  useStudio.setState({
    data: { ...useStudio.getState().data, projects: [], jobs: [], health: healthFixture },
    refresh: vi.fn().mockResolvedValue(undefined),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useUI.setState({ importOpen: false });
  useStudio.setState({ refresh: originalRefresh });
});
function dialog() {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <TooltipProvider>
        <ImportDialog />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

describe('import workflows', () => {
  it('references an absolute local server path through the documented endpoint', async () => {
    const local = vi.spyOn(api, 'local').mockResolvedValue(projectFixture);
    const user = userEvent.setup();
    dialog();
    await user.click(screen.getByRole('tab', { name: 'Local path' }));
    await user.type(screen.getByLabelText('Absolute path on the server'), 'F:\\recording.mp4');
    await user.click(screen.getByRole('button', { name: 'Import footage' }));
    await waitFor(() => expect(local).toHaveBeenCalledWith('F:\\recording.mp4'));
    expect(useStudio.getState().data.projects[0].id).toBe(projectFixture.id);
    expect(useUI.getState().importOpen).toBe(false);
  });
  it('rejects a relative path without calling the API', async () => {
    const local = vi.spyOn(api, 'local');
    const user = userEvent.setup();
    dialog();
    await user.click(screen.getByRole('tab', { name: 'Local path' }));
    await user.type(screen.getByLabelText('Absolute path on the server'), 'recording.mp4');
    await user.click(screen.getByRole('button', { name: 'Import footage' }));
    expect(screen.getByRole('alert').textContent).toContain('absolute path');
    expect(local).not.toHaveBeenCalled();
  });
  it('queues a public YouTube import and retains its actual job response', async () => {
    const job = { ...jobFixture, kind: 'youtube' as const };
    const youtube = vi.spyOn(api, 'youtube').mockResolvedValue({ project: projectFixture, job });
    const user = userEvent.setup();
    dialog();
    await user.click(screen.getByRole('tab', { name: 'YouTube' }));
    await user.type(
      screen.getByLabelText('Public YouTube video URL'),
      'https://youtu.be/testvideo',
    );
    await user.click(screen.getByRole('button', { name: 'Import footage' }));
    await waitFor(() => expect(youtube).toHaveBeenCalledWith('https://youtu.be/testvideo'));
    expect(useStudio.getState().data.jobs[0]).toEqual(job);
  });
  it('rejects non-YouTube hosts and does not fabricate a download job', async () => {
    const youtube = vi.spyOn(api, 'youtube');
    const user = userEvent.setup();
    dialog();
    await user.click(screen.getByRole('tab', { name: 'YouTube' }));
    await user.type(
      screen.getByLabelText('Public YouTube video URL'),
      'https://youtube.com.example.org/video',
    );
    await user.click(screen.getByRole('button', { name: 'Import footage' }));
    expect(screen.getByRole('alert').textContent).toContain('valid public');
    expect(youtube).not.toHaveBeenCalled();
    expect(useStudio.getState().data.jobs).toEqual([]);
  });
});
