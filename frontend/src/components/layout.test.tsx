// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { LayoutEditor } from './LayoutEditor';
import { drawLayout } from './LayoutPreview';
import { layoutFixture, projectFixture } from '../test/fixtures';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { cropHandles, fullCrop, resizedCrop } from '../lib/layout';
import type { Project } from '../types/api';

const selectedLayout = {
  ...layoutFixture,
  facecam: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 },
};
const button = (name: string | RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement;
const coordinate = (name: string) => screen.getByLabelText(name) as HTMLInputElement;
const ratio = () => screen.getByRole('slider', { name: /Facecam share/ }) as HTMLInputElement;
const advanced = () => screen.getByText('Advanced · coordinates & captions').closest('details')!;
function openAdvanced() {
  if (!advanced().open) fireEvent.click(screen.getByText('Advanced · coordinates & captions'));
}
function loadSource(width = 1920, height = 1080) {
  const video = screen.getByLabelText('Source video preview') as HTMLVideoElement;
  Object.defineProperties(video, {
    videoWidth: { configurable: true, value: width },
    videoHeight: { configurable: true, value: height },
  });
  fireEvent.loadedMetadata(video);
  return video;
}
function drag(
  startTarget: Element,
  surface: Element,
  start: [number, number],
  end: [number, number],
) {
  fireEvent.pointerDown(startTarget, {
    clientX: start[0],
    clientY: start[1],
    button: 0,
    pointerId: 1,
  });
  fireEvent.pointerMove(surface, { clientX: end[0], clientY: end[1], button: 0, pointerId: 1 });
  fireEvent.pointerUp(surface, { clientX: end[0], clientY: end[1], button: 0, pointerId: 1 });
}
function unloadPrevented() {
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 20,
    top: 30,
    width: 800,
    height: 600,
    right: 820,
    bottom: 630,
    x: 20,
    y: 30,
    toJSON() {},
  });
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
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
  Object.defineProperties(HTMLElement.prototype, {
    setPointerCapture: { configurable: true, value: vi.fn() },
    hasPointerCapture: { configurable: true, value: () => false },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture');
  Reflect.deleteProperty(HTMLElement.prototype, 'hasPointerCapture');
});

describe('real source layout editor', () => {
  it('focuses source and portrait, starts at Facecam, and keeps numbers collapsed without guessing a crop', () => {
    const { container } = render(
      <LayoutEditor project={{ ...projectFixture, layout: layoutFixture }} />,
    );
    expect(container.querySelectorAll('video')).toHaveLength(1);
    expect(screen.getByLabelText('Live portrait crop preview')).toBeTruthy();
    expect(button(/1 Facecam Required/).getAttribute('aria-pressed')).toBe('true');
    expect(button(/2 Gameplay Full frame/)).toBeTruthy();
    expect(button('Edit crops').getAttribute('aria-pressed')).toBe('true');
    expect(ratio().value).toBe('50');
    expect(advanced().open).toBe(false);
    expect(coordinate('facecam x').value).toBe('');
    expect(coordinate('facecam width').value).toBe('');
    expect(screen.getByRole('status').textContent).toContain('Facecam required');
    expect(screen.queryByLabelText('Crop region')).toBeNull();
    expect(screen.queryByLabelText('Crop tool')).toBeNull();
    fireEvent.change(ratio(), { target: { value: '60' } });
    expect(button('Save layout').disabled).toBe(true);
    expect(button('Discard layout edits').disabled).toBe(false);
  });

  it('draws letterboxed source coordinates and PATCHes the complete normalized layout', async () => {
    const project = { ...projectFixture, layout: layoutFixture };
    vi.spyOn(api, 'saveLayout').mockImplementation(async (_id, layout) => ({ ...project, layout }));
    const { container } = render(<LayoutEditor project={project} />);
    const video = loadSource();
    expect(video.controls).toBe(false);
    const surface = screen.getByRole('group', { name: 'Facecam crop canvas' });
    // 16:9 inside 800x600 starts at y=105, not y=30.
    drag(surface, surface, [100, 150], [340, 285]);
    openAdvanced();
    expect(coordinate('facecam x').value).toBe('0.1');
    expect(coordinate('facecam y').value).toBe('0.1');
    expect(coordinate('facecam width').value).toBe('0.3');
    fireEvent.change(coordinate('facecam width'), { target: { value: '2' } });
    expect(coordinate('facecam width').value).toBe('0.9');
    fireEvent.click(button('Save layout'));
    await waitFor(() =>
      expect(api.saveLayout).toHaveBeenCalledWith('project-1', {
        ...layoutFixture,
        facecam: { x: 0.1, y: 0.1, width: 0.9, height: 0.30000000000000004 },
      }),
    );
    await waitFor(() => expect(screen.getByText('Saved layout')).toBeTruthy());
    expect(button('Save layout').disabled).toBe(true);
    expect(unloadPrevented()).toBe(false);
    expect(container.querySelectorAll('video')).toHaveLength(1);
    expect(
      useStudio.getState().data.projects.find((item) => item.id === project.id)?.layout
        ?.split_ratio,
    ).toBe(0.5);
  });

  it('moves inside, resizes from the handle without a grab-offset jump, and redraws outside without tool menus', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
    loadSource();
    openAdvanced();
    const surface = screen.getByRole('group', { name: 'Facecam crop canvas' });
    drag(button('Move facecam crop'), surface, [180, 195], [260, 240]);
    expect(coordinate('facecam x').value).toBe('0.2');
    expect(coordinate('facecam y').value).toBe('0.2');
    expect(coordinate('facecam width').value).toBe('0.3');
    // Grab just inside the corner; resizing uses the pointer delta, not its absolute position.
    drag(button('Resize facecam crop'), surface, [412, 322], [492, 367]);
    expect(coordinate('facecam width').value).toBe('0.4');
    expect(coordinate('facecam height').value).toBe('0.4');
    drag(surface, surface, [740, 465], [500, 330]);
    expect(coordinate('facecam x').value).toBe('0.6');
    expect(coordinate('facecam y').value).toBe('0.5');
    expect(coordinate('facecam width').value).toBe('0.3');
    expect(coordinate('facecam height').value).toBe('0.3');
  });

  it.each(cropHandles)(
    'supports the %s handle with letterboxed pointer deltas and keyboard resizing',
    (handle) => {
      render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
      loadSource();
      openAdvanced();
      expect(screen.getAllByRole('button', { name: /^Resize facecam crop/ })).toHaveLength(8);
      const control = button(`Resize facecam crop${handle === 'se' ? '' : ` ${handle}`}`);
      const surface = screen.getByRole('group', { name: 'Facecam crop canvas' });
      coordinate('facecam x').focus();
      const x = 20 + (handle.includes('w') ? 0.1 : handle.includes('e') ? 0.4 : 0.25) * 800;
      const y = 105 + (handle.includes('n') ? 0.1 : handle.includes('s') ? 0.4 : 0.25) * 450;
      drag(control, surface, [x, y], [x + 40, y + 22.5]);
      expect(document.activeElement).toBe(control);
      let expected = resizedCrop(selectedLayout.facecam, handle, 0.05, 0.05);
      for (const field of ['x', 'y', 'width', 'height'] as const)
        expect(Number(coordinate(`facecam ${field}`).value)).toBeCloseTo(expected[field], 5);
      fireEvent.keyDown(control, { key: 'ArrowLeft' });
      expected = resizedCrop(expected, handle, -0.01, 0);
      for (const field of ['x', 'y', 'width', 'height'] as const)
        expect(Number(coordinate(`facecam ${field}`).value)).toBeCloseTo(expected[field], 5);
    },
  );

  it('lets a full-frame gameplay crop be drawn directly and reset without changing the facecam', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
    loadSource();
    fireEvent.click(button(/2 Gameplay Full frame/));
    const surface = screen.getByRole('group', { name: 'Gameplay crop canvas' });
    drag(button('Move gameplay crop'), surface, [180, 195], [660, 465]);
    openAdvanced();
    expect(coordinate('gameplay x').value).toBe('0.2');
    expect(coordinate('gameplay y').value).toBe('0.2');
    expect(coordinate('gameplay width').value).toBe('0.6');
    expect(coordinate('gameplay height').value).toBe('0.6');
    expect(coordinate('facecam x').value).toBe('0.1');
    expect(coordinate('facecam width').value).toBe('0.3');
    fireEvent.click(button('Reset gameplay to full frame'));
    expect(coordinate('gameplay x').value).toBe('0');
    expect(coordinate('gameplay width').value).toBe('1');
    expect(button(/2 Gameplay Full frame/)).toBeTruthy();
    expect(button('Save layout').disabled).toBe(true);
  });

  it('ignores other pointers and stops updating after a crop gesture is cancelled', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
    loadSource();
    openAdvanced();
    const surface = screen.getByRole('group', { name: 'Facecam crop canvas' });
    fireEvent.pointerDown(surface, { clientX: 180, clientY: 195, button: 0, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 260, clientY: 240, pointerId: 2 });
    expect(coordinate('facecam x').value).toBe('0.1');
    fireEvent.pointerMove(surface, { clientX: 260, clientY: 240, pointerId: 1 });
    expect(coordinate('facecam x').value).toBe('0.2');
    fireEvent.pointerCancel(surface, { pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 340, clientY: 285, pointerId: 1 });
    expect(coordinate('facecam x').value).toBe('0.2');
  });

  it('maps pillarboxed coordinates and clamps moves and corner resizing to the source', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: layoutFixture }} />);
    loadSource(600, 800);
    const surface = screen.getByRole('group', { name: 'Facecam crop canvas' });
    // 3:4 inside 800x600 is 450px wide, beginning at x=195.
    drag(surface, surface, [240, 90], [375, 270]);
    openAdvanced();
    expect(coordinate('facecam x').value).toBe('0.1');
    expect(coordinate('facecam y').value).toBe('0.1');
    expect(coordinate('facecam width').value).toBe('0.3');
    expect(coordinate('facecam height').value).toBe('0.3');
    drag(button('Move facecam crop'), surface, [285, 150], [-100, -100]);
    expect(coordinate('facecam x').value).toBe('0');
    expect(coordinate('facecam y').value).toBe('0');
    drag(button('Resize facecam crop'), surface, [330, 210], [1000, 1000]);
    expect(coordinate('facecam width').value).toBe('1');
    expect(coordinate('facecam height').value).toBe('1');
    expect(button('Save layout').disabled).toBe(false);
  });

  it('provides keyboard movement and resizing with bounded normalized coordinates', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
    loadSource();
    openAdvanced();
    fireEvent.keyDown(button('Move facecam crop'), { key: 'ArrowRight' });
    fireEvent.keyDown(button('Move facecam crop'), { key: 'ArrowUp', shiftKey: true });
    expect(coordinate('facecam x').value).toBe('0.11');
    expect(coordinate('facecam y').value).toBe('0');
    fireEvent.keyDown(button('Resize facecam crop'), { key: 'ArrowDown', shiftKey: true });
    expect(coordinate('facecam height').value).toBe('0.4');
    for (let index = 0; index < 9; index++)
      fireEvent.keyDown(button('Resize facecam crop'), { key: 'ArrowRight', shiftKey: true });
    expect(coordinate('facecam width').value).toBe('0.89');
  });

  it('lets keyboard users create an unset facecam only after entering a complete valid rectangle', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: layoutFixture }} />);
    openAdvanced();
    expect(advanced().open).toBe(true);
    expect(button('Set facecam crop').disabled).toBe(true);
    for (const [field, value] of Object.entries({ x: '0.1', y: '0.2', width: '1', height: '0.2' }))
      fireEvent.change(coordinate(`facecam ${field}`), { target: { value } });
    expect(button('Set facecam crop').disabled).toBe(true);
    expect(button('Save layout').disabled).toBe(true);
    fireEvent.change(coordinate('facecam width'), { target: { value: '0.3' } });
    expect(button('Set facecam crop').disabled).toBe(false);
    fireEvent.click(button('Set facecam crop'));
    expect(button(/1 Facecam Selected/)).toBeTruthy();
    expect(coordinate('facecam y').value).toBe('0.2');
    expect(button('Save layout').disabled).toBe(false);
    fireEvent.change(coordinate('facecam width'), { target: { value: '2' } });
    expect(coordinate('facecam width').value).toBe('0.9');
    fireEvent.change(coordinate('facecam x'), { target: { value: '' } });
    expect(coordinate('facecam x').value).toBe('0.1');
    fireEvent.click(button('Clear facecam'));
    expect(coordinate('facecam x').value).toBe('');
    expect(button('Set facecam crop').disabled).toBe(true);
    expect(button('Save layout').disabled).toBe(true);
  });

  it('separates crop editing from native playback controls while preserving the one source and seek callbacks', () => {
    const onTimeChange = vi.fn();
    const { container } = render(
      <LayoutEditor
        project={{ ...projectFixture, layout: selectedLayout }}
        seek={{ time: 12, token: 1 }}
        onTimeChange={onTimeChange}
      />,
    );
    const video = loadSource();
    expect(video.currentTime).toBe(12);
    expect(video.controls).toBe(false);
    expect(screen.getByLabelText('Source crop selection')).toBeTruthy();
    fireEvent.click(button('Play / seek'));
    expect(video.controls).toBe(true);
    expect(screen.queryByLabelText('Source crop selection')).toBeNull();
    expect(screen.getByText('Playback · crop editing off.')).toBeTruthy();
    video.currentTime = 18;
    fireEvent.timeUpdate(video);
    expect(onTimeChange).toHaveBeenLastCalledWith(18);
    fireEvent.click(button(/2 Gameplay Full frame/));
    expect(video.controls).toBe(false);
    expect(screen.getByRole('group', { name: 'Gameplay crop canvas' })).toBeTruthy();
    fireEvent.play(video);
    expect(video.controls).toBe(true);
    expect(screen.queryByLabelText('Source crop selection')).toBeNull();
    expect(container.querySelectorAll('video')).toHaveLength(1);
    expect(screen.getByLabelText('Source video preview')).toBe(video);
  });

  it('keeps both region scaling choices and ratio presets in the persisted layout', async () => {
    const project = { ...projectFixture, layout: selectedLayout };
    vi.spyOn(api, 'saveLayout').mockImplementation(async (_id, layout) => ({ ...project, layout }));
    render(<LayoutEditor project={project} />);
    fireEvent.click(button('30/70'));
    expect(ratio().value).toBe('30');
    fireEvent.click(button('50/50'));
    expect(ratio().value).toBe('50');
    fireEvent.change(ratio(), { target: { value: '65' } });
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Facecam scaling' })).getByRole('button', {
        name: 'Fit',
      }),
    );
    fireEvent.click(button(/2 Gameplay Full frame/));
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Gameplay scaling' })).getByRole('button', {
        name: 'Fill',
      }),
    );
    fireEvent.click(button('Save layout'));
    await waitFor(() =>
      expect(api.saveLayout).toHaveBeenCalledWith(project.id, {
        ...selectedLayout,
        split_ratio: 0.65,
        facecam_fit: 'fit',
        gameplay_fit: 'fill',
      }),
    );
    await waitFor(() => expect(screen.getByText('Saved layout')).toBeTruthy());
  });

  it('hides split-only controls in blurred mode and permits saving without a facecam', async () => {
    const project = { ...projectFixture, layout: layoutFixture };
    vi.spyOn(api, 'saveLayout').mockImplementation(async (_id, layout) => ({ ...project, layout }));
    render(<LayoutEditor project={project} />);
    const video = loadSource();
    fireEvent.click(button('Blurred'));
    expect(video.controls).toBe(true);
    expect(screen.queryByRole('group', { name: 'Crop steps' })).toBeNull();
    expect(screen.queryByRole('slider')).toBeNull();
    expect(screen.queryByLabelText('Source crop selection')).toBeNull();
    expect(screen.queryByLabelText('facecam x')).toBeNull();
    expect(button('Save layout').disabled).toBe(false);
    fireEvent.click(button('Save layout'));
    await waitFor(() =>
      expect(api.saveLayout).toHaveBeenCalledWith(project.id, {
        ...layoutFixture,
        mode: 'blurred',
      }),
    );
    await waitFor(() => expect(screen.getByText('Saved layout')).toBeTruthy());
  });

  it('preserves the legacy blurred default instead of inventing split crops', () => {
    render(<LayoutEditor project={projectFixture} />);
    expect(button('Blurred').getAttribute('aria-pressed')).toBe('true');
    expect(button('Save layout').disabled).toBe(true);
    fireEvent.click(button('Split'));
    expect(button(/1 Facecam Required/)).toBeTruthy();
    expect(ratio().value).toBe('50');
    expect(button('Save layout').disabled).toBe(true);
  });

  it('warns while dirty and discards back to the saved layout', () => {
    render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
    expect(unloadPrevented()).toBe(false);
    fireEvent.change(ratio(), { target: { value: '60' } });
    expect(unloadPrevented()).toBe(true);
    expect(screen.getByText('Unsaved preview')).toBeTruthy();
    fireEvent.click(button('Discard layout edits'));
    expect(ratio().value).toBe('50');
    expect(unloadPrevented()).toBe(false);
    expect(button('Save layout').disabled).toBe(true);
  });

  it('accepts saved project refreshes without overwriting a dirty local draft', () => {
    const project = { ...projectFixture, layout: selectedLayout };
    const { rerender } = render(<LayoutEditor project={project} />);
    rerender(
      <LayoutEditor project={{ ...project, layout: { ...selectedLayout, split_ratio: 0.3 } }} />,
    );
    expect(ratio().value).toBe('30');
    expect(screen.getByText('Saved layout')).toBeTruthy();
    fireEvent.change(ratio(), { target: { value: '60' } });
    rerender(
      <LayoutEditor project={{ ...project, layout: { ...selectedLayout, split_ratio: 0.7 } }} />,
    );
    expect(ratio().value).toBe('60');
    expect(unloadPrevented()).toBe(true);
    fireEvent.click(button('Discard layout edits'));
    expect(ratio().value).toBe('70');
    expect(unloadPrevented()).toBe(false);
  });

  it('keeps a failed save dirty, displays the error, and permits retry', async () => {
    vi.spyOn(api, 'saveLayout').mockRejectedValue(new Error('Disk unavailable'));
    render(<LayoutEditor project={{ ...projectFixture, layout: selectedLayout }} />);
    fireEvent.change(ratio(), { target: { value: '60' } });
    fireEvent.click(button('Save layout'));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Disk unavailable'));
    expect(ratio().value).toBe('60');
    expect(button('Save layout').disabled).toBe(false);
    expect(unloadPrevented()).toBe(true);
  });

  it('locks pending edits and does not apply a late save response to a different project', async () => {
    let resolve!: (saved: Project) => void;
    vi.spyOn(api, 'saveLayout').mockImplementation(
      () =>
        new Promise<Project>((done) => {
          resolve = done;
        }),
    );
    const project = { ...projectFixture, layout: selectedLayout };
    const { rerender } = render(<LayoutEditor project={project} />);
    loadSource();
    fireEvent.change(ratio(), { target: { value: '60' } });
    fireEvent.click(button('Save layout'));
    expect(button(/1 Facecam Selected/).matches(':disabled')).toBe(true);
    expect(ratio().matches(':disabled')).toBe(true);
    expect(screen.queryByLabelText('Source crop selection')).toBeNull();
    rerender(
      <LayoutEditor
        project={{ ...project, id: 'project-2', layout: { ...selectedLayout, split_ratio: 0.3 } }}
      />,
    );
    await act(async () => {
      resolve({ ...project, layout: { ...selectedLayout, split_ratio: 0.6 } });
    });
    expect(ratio().value).toBe('30');
    expect(button('Save layout').disabled).toBe(true);
    expect(unloadPrevented()).toBe(false);
  });

  it('disables saving when either configured source rectangle is invalid', () => {
    const project = {
      ...projectFixture,
      layout: { ...selectedLayout, gameplay: { ...fullCrop(), width: 0 } },
    };
    const { rerender } = render(<LayoutEditor project={project} />);
    fireEvent.change(ratio(), { target: { value: '60' } });
    expect(button('Save layout').disabled).toBe(true);
    fireEvent.click(button('Discard layout edits'));
    rerender(
      <LayoutEditor
        project={{
          ...project,
          layout: { ...selectedLayout, facecam: { ...selectedLayout.facecam, x: 0.9 } },
        }}
      />,
    );
    fireEvent.change(ratio(), { target: { value: '60' } });
    expect(button('Save layout').disabled).toBe(true);
  });

  it('draws actual crops from the same video with an exact 50/50 split and no invented facecam', () => {
    const video = document.createElement('video');
    Object.defineProperties(video, { videoWidth: { value: 1920 }, videoHeight: { value: 1080 } });
    const ctx = {
      canvas: { width: 360, height: 640 },
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      save: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      drawImage: vi.fn(),
      restore: vi.fn(),
    };
    drawLayout(ctx as unknown as CanvasRenderingContext2D, video, layoutFixture);
    expect(ctx.drawImage).toHaveBeenCalledOnce();
    expect(ctx.drawImage.mock.calls[0][0]).toBe(video);
    expect(ctx.rect).toHaveBeenCalledWith(0, 320, 360, 320);
    ctx.drawImage.mockClear();
    ctx.rect.mockClear();
    drawLayout(ctx as unknown as CanvasRenderingContext2D, video, {
      ...layoutFixture,
      facecam: { x: 0, y: 0, width: 0.2, height: 0.2 },
      gameplay: fullCrop(),
    });
    expect(ctx.drawImage).toHaveBeenCalledTimes(2);
    expect(ctx.drawImage.mock.calls.every((call) => call[0] === video)).toBe(true);
    expect(ctx.rect.mock.calls).toEqual([
      [0, 0, 360, 320],
      [0, 320, 360, 320],
    ]);
    expect(ctx.drawImage.mock.calls[0].slice(1, 5)).toEqual([0, 0, 384, 216]);
  });
});
