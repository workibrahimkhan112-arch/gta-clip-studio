import type { CropRect, LayoutConfig, Project } from '../types/api';

export const fullCrop = (): CropRect => ({ x: 0, y: 0, width: 1, height: 1 });
export function projectLayout(project: Project): LayoutConfig {
  return structuredClone(
    project.layout ?? {
      mode: 'blurred',
      facecam: null,
      gameplay: fullCrop(),
      split_ratio: 0.5,
      facecam_fit: 'fill',
      gameplay_fit: 'fit',
      caption_position: 'lower_middle',
    },
  );
}
export function layoutExportProblem(project?: Project): string | null {
  if (!project) return 'Project is not loaded. Open its workspace before exporting.';
  return project.layout?.mode === 'split' && !project.layout.facecam
    ? 'Select the embedded facecam in the project layout editor, or use the blurred preset.'
    : null;
}
export function validCrop(rect: CropRect): boolean {
  return (
    Object.values(rect).every(Number.isFinite) &&
    rect.x >= 0 &&
    rect.y >= 0 &&
    rect.width > 0 &&
    rect.height > 0 &&
    rect.x + rect.width <= 1 + 1e-9 &&
    rect.y + rect.height <= 1 + 1e-9
  );
}
export const clamp = (n: number, min = 0, max = 1) => Math.min(max, Math.max(min, n));
export function changeCrop(rect: CropRect, field: keyof CropRect, value: number): CropRect {
  if (!Number.isFinite(value)) return rect;
  const next = { ...rect };
  if (field === 'x') next.x = clamp(value, 0, 1 - rect.width);
  if (field === 'y') next.y = clamp(value, 0, 1 - rect.height);
  if (field === 'width') next.width = clamp(value, 0.001, 1 - rect.x);
  if (field === 'height') next.height = clamp(value, 0.001, 1 - rect.y);
  return next;
}
export interface Bounds {
  left: number;
  top: number;
  width: number;
  height: number;
}
// Exclude object-contain letterboxing; pointer coordinates always refer to source pixels.
export function containedBounds(box: Bounds, sourceWidth: number, sourceHeight: number): Bounds {
  if (!box.width || !box.height || !sourceWidth || !sourceHeight)
    return { ...box, width: 0, height: 0 };
  const scale = Math.min(box.width / sourceWidth, box.height / sourceHeight);
  const width = sourceWidth * scale,
    height = sourceHeight * scale;
  return {
    left: box.left + (box.width - width) / 2,
    top: box.top + (box.height - height) / 2,
    width,
    height,
  };
}
export function sourcePoint(clientX: number, clientY: number, bounds: Bounds) {
  return {
    x: clamp((clientX - bounds.left) / bounds.width),
    y: clamp((clientY - bounds.top) / bounds.height),
  };
}
export function redrawnCrop(a: { x: number; y: number }, b: { x: number; y: number }): CropRect {
  const x = clamp(Math.min(a.x, b.x), 0, 0.999),
    y = clamp(Math.min(a.y, b.y), 0, 0.999);
  return {
    x,
    y,
    width: clamp(Math.abs(b.x - a.x), 0.001, 1 - x),
    height: clamp(Math.abs(b.y - a.y), 0.001, 1 - y),
  };
}
export function movedCrop(rect: CropRect, dx: number, dy: number): CropRect {
  return {
    ...rect,
    x: clamp(rect.x + dx, 0, 1 - rect.width),
    y: clamp(rect.y + dy, 0, 1 - rect.height),
  };
}
export const cropHandles = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'] as const;
export type CropHandle = (typeof cropHandles)[number];

export function resizedCrop(rect: CropRect, handle: CropHandle, dx: number, dy: number): CropRect {
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return rect;
  if (
    (!dx || (!handle.includes('e') && !handle.includes('w'))) &&
    (!dy || (!handle.includes('n') && !handle.includes('s')))
  )
    return rect;
  const min = 0.001;
  const right = rect.x + rect.width,
    bottom = rect.y + rect.height;
  const left = handle.includes('w') ? clamp(rect.x + dx, 0, right - min) : rect.x;
  const top = handle.includes('n') ? clamp(rect.y + dy, 0, bottom - min) : rect.y;
  const nextRight = handle.includes('e') ? clamp(right + dx, rect.x + min, 1) : right;
  const nextBottom = handle.includes('s') ? clamp(bottom + dy, rect.y + min, 1) : bottom;
  return { x: left, y: top, width: nextRight - left, height: nextBottom - top };
}

export function cropPlacement(
  sourceWidth: number,
  sourceHeight: number,
  rect: CropRect,
  target: { x: number; y: number; width: number; height: number },
  fit: 'fit' | 'fill',
) {
  const sx = rect.x * sourceWidth,
    sy = rect.y * sourceHeight;
  const sw = rect.width * sourceWidth,
    sh = rect.height * sourceHeight;
  const scale = (fit === 'fill' ? Math.max : Math.min)(target.width / sw, target.height / sh);
  const dw = sw * scale,
    dh = sh * scale;
  return [
    sx,
    sy,
    sw,
    sh,
    target.x + (target.width - dw) / 2,
    target.y + (target.height - dh) / 2,
    dw,
    dh,
  ] as const;
}
