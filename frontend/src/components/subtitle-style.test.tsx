// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HighlightEditor } from './HighlightEditor';
import { SubtitlePreview } from './SubtitlePreview';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import {
  chatgptFixture,
  healthFixture,
  highlightFixture,
  integrationsFixture,
  jobFixture,
  projectFixture,
  settingsFixture,
  subtitleStyleFixture,
} from '../test/fixtures';
import type { Highlight } from '../types/api';

const originalData = useStudio.getState().data;
const authored: Highlight = {
  ...highlightFixture,
  transcript: [
    {
      start: 0,
      end: 2,
      text: 'Actual words',
      words: [
        { start: 0, end: 0.5, word: 'Actual' },
        { start: 0.8, end: 1.5, word: 'words' },
      ],
    },
  ],
  subtitle_style: subtitleStyleFixture,
};
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.spyOn(api, 'integrations').mockResolvedValue(integrationsFixture);
  vi.spyOn(api, 'chatgptStatus').mockResolvedValue(chatgptFixture);
  vi.spyOn(api, 'exportHighlight').mockResolvedValue(jobFixture);
  vi.spyOn(api, 'patchHighlight').mockImplementation(async (_id, patch) => ({
    ...useStudio.getState().data.highlights[0],
    ...patch,
  }));
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  useStudio.setState({
    data: {
      ...originalData,
      projects: [projectFixture],
      highlights: [authored],
      jobs: [],
      settings: settingsFixture,
      health: healthFixture,
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useStudio.setState({ data: originalData });
});
function Editor() {
  const highlight = useStudio((state) => state.data.highlights[0]);
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <HighlightEditor highlight={highlight} onClose={() => {}} />
    </MemoryRouter>
  );
}
function stylePanel() {
  fireEvent.click(screen.getByRole('button', { name: 'Style' }));
}
function unloadPrevented() {
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

describe('persisted visual subtitle editor', () => {
  it('offers bundled Inter and Montserrat for subtitle preview', () => {
    render(<Editor />);
    stylePanel();
    fireEvent.click(screen.getByRole('combobox', { name: 'Font' }));
    expect(screen.getByRole('option', { name: 'Inter' })).toBeTruthy();
    fireEvent.click(screen.getByRole('option', { name: 'Montserrat' }));
    expect(screen.getByRole('group', { name: 'Move subtitle box' }).style.fontFamily).toContain(
      'Montserrat',
    );
  });
  it('opens saved style on the one actual preview and PATCHes only changed style before export', async () => {
    const view = render(<Editor />);
    stylePanel();
    expect(document.querySelectorAll('video')).toHaveLength(1);
    expect((screen.getByRole('slider', { name: 'Font size' }) as HTMLInputElement).value).toBe(
      '54',
    );
    expect(screen.getByRole('combobox', { name: 'Font' }).textContent).toContain('Verdana');
    expect(screen.getByText('Advanced typography').closest('details')?.open).toBe(false);
    const box = screen.getByRole('group', { name: 'Move subtitle box' });
    expect(box.style.left).toBe('45%');
    expect(box.style.top).toBe('60%');
    expect(box.style.width).toBe('70%');
    expect(box.style.fontWeight).toBe('400');
    expect(box.textContent).toContain('Actual words');
    fireEvent.change(screen.getByRole('slider', { name: 'Font size' }), {
      target: { value: '62' },
    });
    expect(unloadPrevented()).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save & export' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenCalledWith(authored.id, {
        subtitle_style: { ...subtitleStyleFixture, font_size: 62 },
      }),
    );
    await waitFor(() => expect(api.exportHighlight).toHaveBeenCalledWith(authored.id, 'standard'));
    expect(useStudio.getState().data.highlights[0].transcript).toEqual(authored.transcript);
    view.unmount();
    render(<Editor />);
    stylePanel();
    expect((screen.getByRole('slider', { name: 'Font size' }) as HTMLInputElement).value).toBe(
      '62',
    );
  });
  it('sends explicit null for reset but never creates a style object just by opening controls', async () => {
    render(<Editor />);
    stylePanel();
    expect(api.patchHighlight).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to legacy style' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenCalledWith(authored.id, { subtitle_style: null }),
    );
    expect(useStudio.getState().data.highlights[0].subtitle_style).toBeNull();
  });
  it('preserves unsaved style through live updates and asks before discarding it', () => {
    render(<Editor />);
    stylePanel();
    fireEvent.change(screen.getByRole('slider', { name: 'Text width' }), {
      target: { value: '0.5' },
    });
    act(() =>
      useStudio
        .getState()
        .upsertHighlight({ ...authored, subtitle_style: { ...subtitleStyleFixture, width: 0.8 } }),
    );
    expect((screen.getByRole('slider', { name: 'Text width' }) as HTMLInputElement).value).toBe(
      '0.5',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByText('Discard unsaved edits?')).toBeTruthy();
    expect(api.patchHighlight).not.toHaveBeenCalled();
  });
  it('hides No captions without discarding style and never fabricates animated timing after a text edit', () => {
    render(<Editor />);
    fireEvent.change(screen.getByLabelText('Caption 1 text'), {
      target: { value: 'Manual correction' },
    });
    fireEvent.click(screen.getByRole('combobox', { name: 'Export captions' }));
    fireEvent.click(screen.getByRole('option', { name: 'Animated captions' }));
    expect(
      (screen.getByRole('button', { name: 'Save & export' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      screen.getByLabelText('Subtitle overlay').querySelector('[data-highlighted]'),
    ).toBeNull();
    fireEvent.click(screen.getByRole('combobox', { name: 'Export captions' }));
    fireEvent.click(screen.getByRole('option', { name: 'No captions' }));
    expect(screen.getByLabelText('Subtitle overlay').textContent).toBe('');
    expect(useStudio.getState().data.highlights[0].subtitle_style).toEqual(subtitleStyleFixture);
  });
});

describe('subtitle box interaction', () => {
  it('shows a movable outline without invented words when no caption is active', () => {
    const change = vi.fn();
    render(
      <SubtitlePreview
        segments={[]}
        seconds={0}
        style="standard"
        position="bottom"
        editable
        onStyleChange={change}
      />,
    );
    expect(screen.getByRole('group', { name: 'Move subtitle box' }).textContent).toBe('');
    fireEvent.keyDown(screen.getByRole('group', { name: 'Move subtitle box' }), { key: 'ArrowUp' });
    expect(change).toHaveBeenCalledWith(
      expect.objectContaining({ y: 0.875, font_family: 'Arial' }),
    );
  });
  it('drags the position and text-width handle using portrait dimensions without a grab-offset jump', () => {
    vi.stubGlobal(
      'PointerEvent',
      class extends MouseEvent {
        readonly pointerId: number;
        constructor(type: string, init: PointerEventInit = {}) {
          super(type, init);
          this.pointerId = init.pointerId ?? 1;
        }
      },
    );
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 10,
      top: 20,
      width: 200,
      height: 400,
      right: 210,
      bottom: 420,
      x: 10,
      y: 20,
      toJSON() {},
    });
    Object.defineProperties(HTMLElement.prototype, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      hasPointerCapture: { configurable: true, value: () => false },
    });
    try {
      const change = vi.fn();
      render(
        <SubtitlePreview
          segments={[]}
          seconds={0}
          style="standard"
          position="center"
          subtitleStyle={subtitleStyleFixture}
          editable
          onStyleChange={change}
        />,
      );
      const box = screen.getByRole('group', { name: 'Move subtitle box' });
      fireEvent.pointerDown(box, { clientX: 110, clientY: 220, button: 0, pointerId: 1 });
      fireEvent.pointerMove(box, { clientX: 130, clientY: 260, pointerId: 1 });
      expect(change).toHaveBeenLastCalledWith(expect.objectContaining({ x: 0.55, y: 0.7 }));
      fireEvent.pointerUp(box, { clientX: 130, clientY: 260, pointerId: 2 });
      fireEvent.pointerMove(box, { clientX: 140, clientY: 280, pointerId: 1 });
      expect(change.mock.calls.at(-1)![0].x).toBeCloseTo(0.6);
      fireEvent.pointerCancel(box, { pointerId: 1 });
      const left = screen.getByRole('button', { name: 'Resize subtitle left edge' });
      fireEvent.pointerDown(left, { clientX: 31, clientY: 260, button: 0, pointerId: 1 });
      fireEvent.pointerMove(box, { clientX: 51, clientY: 260, pointerId: 1 });
      const next = change.mock.calls.at(-1)![0];
      expect(next.width).toBeCloseTo(0.6);
      expect(next.x + next.width / 2).toBeCloseTo(0.8);
      expect(document.activeElement).toBe(left);
    } finally {
      Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture');
      Reflect.deleteProperty(HTMLElement.prototype, 'hasPointerCapture');
    }
  });
  it.each(['left', 'right'] as const)(
    'allows keyboard width resizing from the %s side, preserving the opposite edge',
    (side) => {
      const change = vi.fn();
      render(
        <SubtitlePreview
          segments={authored.transcript}
          seconds={1}
          style="animated"
          position="center"
          subtitleStyle={subtitleStyleFixture}
          editable
          onStyleChange={change}
        />,
      );
      fireEvent.keyDown(screen.getByRole('button', { name: `Resize subtitle ${side} edge` }), {
        key: 'ArrowRight',
        shiftKey: true,
      });
      expect(change).toHaveBeenCalledOnce();
      const next = change.mock.calls[0][0];
      const sign = side === 'left' ? 1 : -1;
      expect(next.x + (sign * next.width) / 2).toBeCloseTo(
        subtitleStyleFixture.x + (sign * subtitleStyleFixture.width) / 2,
      );
      expect(next.width).toBeCloseTo(side === 'left' ? 0.65 : 0.75);
      expect(
        screen.getByLabelText('Subtitle overlay').querySelector('[data-highlighted]')?.textContent,
      ).toBe('words');
    },
  );
});
