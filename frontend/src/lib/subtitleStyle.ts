import type { LayoutConfig, SubtitleStyle } from '../types/api';
import { clamp } from './layout';

export function defaultSubtitleStyle(
  position: LayoutConfig['caption_position'] = 'lower_middle',
): SubtitleStyle {
  return {
    font_family: 'Arial',
    font_size: 48,
    bold: true,
    text_color: '#FFFFFF',
    highlight_color: '#00FF00',
    outline_color: '#141414',
    outline_width: 3,
    background_color: '#000000',
    background_opacity: 0,
    x: 0.5,
    y: position === 'center' ? 0.5 : position === 'bottom' ? 0.88 : 0.73,
    width: 0.84,
  };
}

export function containSubtitleStyle(style: SubtitleStyle): SubtitleStyle {
  const width = clamp(style.width, 0.2, 1);
  return {
    ...style,
    width,
    x: clamp(style.x, width / 2, 1 - width / 2),
    y: clamp(style.y, 0.05, 0.95),
  };
}

// The opposite text edge stays fixed, including when dragging beyond the portrait.
export function resizeSubtitleWidth(
  style: SubtitleStyle,
  side: 'left' | 'right',
  dx: number,
): SubtitleStyle {
  if (!Number.isFinite(dx) || dx === 0) return style;
  const left = style.x - style.width / 2;
  const right = style.x + style.width / 2;
  const nextLeft = side === 'left' ? clamp(left + dx, 0, right - 0.2) : left;
  const nextRight = side === 'right' ? clamp(right + dx, left + 0.2, 1) : right;
  return { ...style, x: (nextLeft + nextRight) / 2, width: nextRight - nextLeft };
}
