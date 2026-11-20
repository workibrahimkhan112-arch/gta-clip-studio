import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import type { CropRect, LayoutConfig } from '../types/api';
import { cropPlacement, fullCrop } from '../lib/layout';

export function drawLayout(
  ctx: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  layout: LayoutConfig,
) {
  const width = ctx.canvas.width,
    height = ctx.canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, width, height);
  const crop = (rect: CropRect, y: number, h: number, fit: 'fit' | 'fill') => {
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, y, width, h);
    ctx.clip();
    ctx.drawImage(
      video,
      ...cropPlacement(
        video.videoWidth,
        video.videoHeight,
        rect,
        { x: 0, y, width, height: h },
        fit,
      ),
    );
    ctx.restore();
  };
  if (layout.mode === 'split') {
    const top = Math.round(height * layout.split_ratio);
    // Never substitute gameplay (or an invented face region) for an unset facecam.
    if (layout.facecam) crop(layout.facecam, 0, top, layout.facecam_fit);
    crop(layout.gameplay, top, height - top, layout.gameplay_fit);
  } else {
    ctx.save();
    ctx.filter = 'blur(12px)';
    crop(fullCrop(), 0, height, 'fill');
    ctx.restore();
    crop(fullCrop(), 0, height, 'fit');
  }
}
export function LayoutPreview({
  video,
  layout,
  caption,
  children,
  className,
}: {
  video: HTMLVideoElement | null;
  layout: LayoutConfig;
  caption?: string;
  children?: ReactNode;
  className?: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    if (!video) {
      setAvailable(false);
      return;
    }
    let frame = 0,
      last = 0,
      stopped = false;
    const draw = () => {
      const ctx = canvas.current?.getContext('2d');
      if (!ctx || video.readyState < 2 || !video.videoWidth || video.error) {
        ctx?.clearRect(0, 0, 360, 640);
        setAvailable(false);
        return;
      }
      try {
        drawLayout(ctx, video, layout);
        setAvailable(true);
      } catch {
        setAvailable(false);
      }
    };
    // Bounded 360x640 canvas, at most 30fps, and no animation loop while paused.
    const tick = (now: number) => {
      if (stopped || video.paused || video.ended) return;
      if (now - last >= 33) {
        draw();
        last = now;
      }
      frame = requestAnimationFrame(tick);
    };
    const play = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    const reset = () => {
      setAvailable(false);
      canvas.current?.getContext('2d')?.clearRect(0, 0, 360, 640);
    };
    const events = ['loadeddata', 'seeked', 'timeupdate', 'pause', 'resize'] as const;
    events.forEach((event) => video.addEventListener(event, draw));
    video.addEventListener('play', play);
    video.addEventListener('error', reset);
    video.addEventListener('emptied', reset);
    draw();
    if (!video.paused) play();
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      events.forEach((event) => video.removeEventListener(event, draw));
      video.removeEventListener('play', play);
      video.removeEventListener('error', reset);
      video.removeEventListener('emptied', reset);
    };
  }, [video, layout]);
  return (
    <div
      className={cn(
        'relative mx-auto aspect-[9/16] w-full max-w-[240px] overflow-hidden rounded-md border border-border bg-black',
        className,
      )}
    >
      <canvas
        ref={canvas}
        width={360}
        height={640}
        className="size-full"
        aria-label="Live portrait crop preview"
      />
      {!available && (
        <p className="absolute inset-x-3 top-1/3 text-center text-[11px] text-zinc-500">
          Play or seek to preview.
        </p>
      )}
      {layout.mode === 'split' && !layout.facecam && (
        <div
          className="absolute inset-x-0 top-0 flex items-center justify-center border-b border-dashed border-primary/30 px-3 text-center text-[11px] text-primary/70"
          style={{ height: `${layout.split_ratio * 100}%` }}
        >
          Select your embedded facecam
        </div>
      )}
      {caption && (
        <div
          className="pointer-events-none absolute inset-x-[8%] text-center text-xs font-semibold text-white"
          style={{
            top:
              layout.caption_position === 'center'
                ? '50%'
                : layout.caption_position === 'bottom'
                  ? '90%'
                  : '72%',
            transform: 'translateY(-50%)',
          }}
        >
          <span className="bg-black/75 px-1">{caption}</span>
        </div>
      )}
      {children}
    </div>
  );
}
