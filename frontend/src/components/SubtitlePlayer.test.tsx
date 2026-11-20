// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SubtitlePlayer } from './SubtitlePlayer';
import { projectLayout } from '../lib/layout';
import { projectFixture } from '../test/fixtures';

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: true });
    fireEvent.pause(this);
  });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: false });
    fireEvent.play(this);
    return Promise.resolve();
  });
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function player() {
  const change = vi.fn();
  const view = render(
    <SubtitlePlayer
      projectId="project-1"
      start={120}
      end={130}
      layout={projectLayout(projectFixture)}
      segments={[
        {
          start: 1,
          end: 3,
          text: 'Actual words',
          words: [
            { start: 1, end: 1.4, word: 'Actual' },
            { start: 1.5, end: 2.5, word: 'words' },
          ],
        },
      ]}
      style="animated"
      onTimeChange={change}
    />,
  );
  const source = view.container.querySelector('video')!;
  Object.defineProperties(source, {
    duration: { configurable: true, value: 500 },
    readyState: { configurable: true, value: 4 },
    videoWidth: { configurable: true, value: 1920 },
    videoHeight: { configurable: true, value: 1080 },
  });
  fireEvent.loadedMetadata(source);
  return { ...view, source, change };
}

describe('single-source subtitle player', () => {
  it('seeks within the original file while presenting clip-relative controls', () => {
    const { container, source, change } = player();
    expect(container.querySelectorAll('video')).toHaveLength(1);
    expect(container.querySelectorAll('canvas')).toHaveLength(1);
    expect(source.currentTime).toBe(120);
    fireEvent.change(screen.getByRole('slider', { name: 'Preview position' }), {
      target: { value: '4.25' },
    });
    expect(source.currentTime).toBe(124.25);
    expect(screen.getByLabelText('Preview time').textContent).toBe('0:04.25 / 0:10.00');
    expect(change).toHaveBeenLastCalledWith(4.25);
  });
  it('updates active-word styling after a real media seek event', () => {
    const { source } = player();
    source.currentTime = 121.7;
    fireEvent.seeked(source);
    const overlay = screen.getByLabelText('Subtitle overlay');
    expect(overlay.textContent).toBe('Actual words');
    expect(overlay.querySelector('[data-highlighted]')?.textContent).toBe('words');
  });
  it('plays, pauses at the clip end, replays and mutes the one source', async () => {
    const { source } = player();
    fireEvent.click(screen.getByRole('button', { name: 'Play preview' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause preview' })).toBeTruthy());
    source.currentTime = 131;
    fireEvent.timeUpdate(source);
    expect(source.currentTime).toBe(130);
    expect(source.paused).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Replay clip' }));
    expect(source.currentTime).toBe(120);
    fireEvent.click(screen.getByRole('button', { name: 'Mute preview' }));
    expect(source.muted).toBe(true);
    expect(screen.getByRole('button', { name: 'Unmute preview' })).toBeTruthy();
  });
  it('disables playback and reports unsupported browser media without hiding edits', () => {
    const { source } = player();
    fireEvent.error(source);
    expect(screen.getByRole('status', { name: 'Preview error' }).textContent).toContain(
      'cannot play',
    );
    expect(
      (screen.getByRole('button', { name: 'Play preview' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
