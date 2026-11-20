import { describe, expect, it } from 'vitest';
import {
  changeCrop,
  containedBounds,
  cropPlacement,
  fullCrop,
  layoutExportProblem,
  movedCrop,
  projectLayout,
  redrawnCrop,
  resizedCrop,
  cropHandles,
  sourcePoint,
  validCrop,
} from './layout';
import { layoutFixture, projectFixture } from '../test/fixtures';

describe('normalized source crop geometry', () => {
  it('keeps legacy projects blurred and never creates a facecam', () => {
    expect(projectLayout(projectFixture)).toMatchObject({
      mode: 'blurred',
      facecam: null,
      split_ratio: 0.5,
    });
    expect(projectLayout({ ...projectFixture, layout: layoutFixture })).toEqual(layoutFixture);
    expect(layoutExportProblem(projectFixture)).toBeNull();
    expect(layoutExportProblem({ ...projectFixture, layout: layoutFixture })).toContain(
      'Select the embedded facecam',
    );
    expect(
      layoutExportProblem({ ...projectFixture, layout: { ...layoutFixture, facecam: fullCrop() } }),
    ).toBeNull();
  });
  it.each([1, 0.5, 0.2])('maps object-contain letterboxing at responsive scale %s', (scale) => {
    const box = { left: 20, top: 30, width: 800 * scale, height: 600 * scale };
    const bounds = containedBounds(box, 1920, 1080);
    expect(bounds.height).toBe(450 * scale);
    expect(bounds.top).toBe(30 + 75 * scale);
    expect(
      sourcePoint(bounds.left + bounds.width * 0.25, bounds.top + bounds.height * 0.75, bounds),
    ).toEqual({ x: 0.25, y: 0.75 });
    expect(sourcePoint(-100, 9000, bounds)).toEqual({ x: 0, y: 1 });
  });
  it('handles portrait source pillarboxing without stretching crop coordinates', () => {
    const bounds = containedBounds({ left: 0, top: 0, width: 800, height: 450 }, 1080, 1920);
    expect(bounds.height).toBe(450);
    expect(bounds.width).toBe(253.125);
    expect(sourcePoint(400, 225, bounds)).toEqual({ x: 0.5, y: 0.5 });
  });
  it('redraws in either direction, moves and resizes within source bounds', () => {
    const crop = redrawnCrop({ x: 0.75, y: 0.75 }, { x: 0.25, y: 0.25 });
    expect(crop).toEqual({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 });
    expect(movedCrop(crop, 2, -2)).toEqual({ x: 0.5, y: 0, width: 0.5, height: 0.5 });
    expect(changeCrop(crop, 'width', 2).width).toBe(0.75);
    expect(changeCrop(crop, 'x', 1).x).toBe(0.5);
    expect(changeCrop(crop, 'height', -1).height).toBe(0.001);
    expect(changeCrop(crop, 'width', NaN)).toEqual(crop);
    expect(validCrop(crop)).toBe(true);
    expect(validCrop({ ...crop, width: 0 })).toBe(false);
    expect(validCrop({ ...crop, x: 0.8 })).toBe(false);
  });
  it.each(cropHandles)(
    'resizes %s while fixing opposite edges and clamping to source bounds',
    (handle) => {
      const rect = { x: 0.2, y: 0.3, width: 0.4, height: 0.4 };
      for (const delta of [-2, -0.1, 0, 0.1, 2]) {
        const next = resizedCrop(rect, handle, delta, delta);
        expect(validCrop(next)).toBe(true);
        if (!handle.includes('w')) expect(next.x).toBeCloseTo(rect.x);
        if (!handle.includes('e')) expect(next.x + next.width).toBeCloseTo(rect.x + rect.width);
        if (!handle.includes('n')) expect(next.y).toBeCloseTo(rect.y);
        if (!handle.includes('s')) expect(next.y + next.height).toBeCloseTo(rect.y + rect.height);
      }
      expect(resizedCrop(rect, handle, NaN, 0)).toEqual(rect);
    },
  );
  it('keeps even tiny new crops inside the source corner', () => {
    expect(validCrop(redrawnCrop({ x: 1, y: 1 }, { x: 0.9999, y: 0.9999 }))).toBe(true);
  });
  it('uses source pixels with correct fit padding and fill clipping', () => {
    const crop = { x: 0.5, y: 0, width: 0.5, height: 0.5 };
    const panel = { x: 0, y: 0, width: 360, height: 320 };
    expect(cropPlacement(1920, 1080, crop, panel, 'fit')).toEqual([
      960, 0, 960, 540, 0, 58.75, 360, 202.5,
    ]);
    const fill = cropPlacement(1920, 1080, crop, panel, 'fill');
    expect(fill.slice(0, 4)).toEqual([960, 0, 960, 540]);
    expect(fill[4]).toBeLessThan(0);
    expect(fill[7]).toBe(320);
  });
});
