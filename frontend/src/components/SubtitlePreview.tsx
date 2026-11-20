import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import type { CaptionStyle, LayoutConfig, SubtitleStyle, TranscriptSegment } from '../types/api';
import { subtitleAt } from '../lib/subtitles';
import {
  containSubtitleStyle,
  defaultSubtitleStyle,
  resizeSubtitleWidth,
} from '../lib/subtitleStyle';

export function SubtitlePreview({
  segments,
  seconds,
  style,
  position,
  subtitleStyle = null,
  outputWidth = 720,
  editable = false,
  onStyleChange,
}: {
  segments: TranscriptSegment[];
  seconds: number;
  style: CaptionStyle;
  position: LayoutConfig['caption_position'];
  subtitleStyle?: SubtitleStyle | null;
  outputWidth?: number;
  editable?: boolean;
  onStyleChange?: (style: SubtitleStyle) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(240);
  const gesture = useRef<{
    id: number;
    x: number;
    y: number;
    width: number;
    height: number;
    style: SubtitleStyle;
    side: 'left' | 'right' | null;
  } | null>(null);
  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    const measure = () => {
      if (parent.clientWidth) setWidth(parent.clientWidth);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    measure();
    return () => observer.disconnect();
  }, []);
  const words = useMemo(
    () => subtitleAt(segments, seconds, style, subtitleStyle, outputWidth),
    [segments, seconds, style, subtitleStyle, outputWidth],
  );
  const effective = subtitleStyle ?? defaultSubtitleStyle(position);
  const editing = editable && !!onStyleChange && style !== 'none';
  const update = (event: PointerEvent) => {
    const active = gesture.current;
    if (!active || active.id !== event.pointerId) return;
    const dx = (event.clientX - active.x) / active.width;
    const dy = (event.clientY - active.y) / active.height;
    onStyleChange?.(
      active.side
        ? resizeSubtitleWidth(active.style, active.side, dx)
        : containSubtitleStyle({ ...active.style, x: active.style.x + dx, y: active.style.y + dy }),
    );
  };
  const keyboard = (event: KeyboardEvent, side: 'left' | 'right' | null) => {
    if (!editing) return;
    const step = event.shiftKey ? 0.05 : 0.005;
    const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    if (!dx && !dy) return;
    event.preventDefault();
    event.stopPropagation();
    onStyleChange?.(
      side
        ? resizeSubtitleWidth(effective, side, dx)
        : containSubtitleStyle({ ...effective, x: effective.x + dx, y: effective.y + dy }),
    );
  };
  const outline = (effective.outline_width * width) / 720;
  const padding = subtitleStyle
    ? Math.max(outline, effective.background_opacity > 0 ? (3 * width) / 720 : 0)
    : 0;
  return (
    <div
      ref={ref}
      className="pointer-events-none absolute inset-0"
      aria-label="Subtitle overlay"
      aria-live="off"
    >
      <div
        role={editing ? 'group' : undefined}
        aria-label={editing ? 'Move subtitle box' : undefined}
        tabIndex={editing ? 0 : undefined}
        className={
          editing
            ? 'pointer-events-auto absolute touch-none cursor-move text-center outline outline-1 outline-primary/80 focus-visible:ring-2 focus-visible:ring-white'
            : 'absolute text-center'
        }
        style={{
          ...(subtitleStyle
            ? {
                left: `${effective.x * 100}%`,
                top: `${effective.y * 100}%`,
                width: `${effective.width * 100}%`,
                transform: 'translate(-50%, -50%)',
              }
            : {
                left: '8%',
                right: '8%',
                ...(position === 'center'
                  ? { top: '50%', transform: 'translateY(-50%)' }
                  : { bottom: position === 'bottom' ? '12%' : '27%' }),
              }),
          minHeight: editing ? '1.3em' : undefined,
          fontFamily: `${effective.font_family}, sans-serif`,
          fontSize: (effective.font_size * width) / 720,
          fontWeight: effective.bold ? 700 : 400,
          lineHeight: 1.15,
          color: effective.text_color,
          overflowWrap: 'anywhere',
          whiteSpace: 'pre-wrap',
          paddingInline: padding,
          ...(subtitleStyle
            ? {
                WebkitTextStroke: `${outline * 2}px ${effective.outline_color}`,
                paintOrder: 'stroke fill',
              }
            : {
                textShadow:
                  '-1px -1px 0 #141414, 1px -1px 0 #141414, -1px 1px 0 #141414, 1px 1px 0 #141414, 0 2px 2px #000',
              }),
          visibility: style === 'none' ? 'hidden' : undefined,
        }}
        onKeyDown={(event) => keyboard(event, null)}
        onPointerDown={(event) => {
          if (!editing || event.button !== 0 || gesture.current) return;
          const bounds = ref.current!.getBoundingClientRect();
          if (!bounds.width || !bounds.height) return;
          const side = (event.target as HTMLElement).closest<HTMLElement>('[data-subtitle-side]')
            ?.dataset.subtitleSide as 'left' | 'right' | undefined;
          event.preventDefault();
          const handle = (event.target as HTMLElement).closest<HTMLElement>('[data-subtitle-side]');
          (handle ?? event.currentTarget).focus({ preventScroll: true });
          event.currentTarget.setPointerCapture(event.pointerId);
          gesture.current = {
            id: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            width: bounds.width,
            height: bounds.height,
            style: effective,
            side: side ?? null,
          };
        }}
        onPointerMove={update}
        onPointerUp={(event) => {
          if (gesture.current?.id !== event.pointerId) return;
          update(event);
          gesture.current = null;
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={(event) => {
          if (gesture.current?.id === event.pointerId) gesture.current = null;
        }}
        onLostPointerCapture={(event) => {
          if (gesture.current?.id === event.pointerId) gesture.current = null;
        }}
      >
        {words.length > 0 && (
          <span
            style={
              subtitleStyle && effective.background_opacity > 0
                ? {
                    backgroundColor: `${effective.background_color}${Math.round(
                      effective.background_opacity * 255,
                    )
                      .toString(16)
                      .padStart(2, '0')}`,
                    boxDecorationBreak: 'clone',
                    WebkitBoxDecorationBreak: 'clone',
                    padding: `${padding}px 0`,
                  }
                : undefined
            }
          >
            {words.map((word, index) => (
              <Fragment key={index}>
                {word.lineBreak ? <br /> : (word.separator ?? (index > 0 ? ' ' : null))}
                <span
                  data-highlighted={word.highlighted || undefined}
                  style={{
                    color: word.highlighted ? effective.highlight_color : effective.text_color,
                  }}
                >
                  {word.text}
                </span>
              </Fragment>
            ))}
          </span>
        )}
        {editing &&
          (['left', 'right'] as const).map((side) => (
            <button
              key={side}
              type="button"
              aria-label={`Resize subtitle ${side} edge`}
              data-subtitle-side={side}
              className="absolute top-1/2 h-8 w-4 -translate-y-1/2 cursor-ew-resize rounded border border-black bg-primary/90 focus-visible:ring-2 focus-visible:ring-white"
              style={{
                [side]: 0,
                WebkitTextStroke: '0px',
                transform: `translate(${side === 'left' ? '-50%' : '50%'}, -50%)`,
              }}
              onKeyDown={(event) => keyboard(event, side)}
            />
          ))}
      </div>
    </div>
  );
}
