import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Film, RotateCcw } from '../icons';
import { api } from '../services/api';
import { Button } from './ui';
import { cn, time } from '../lib/utils';

export interface SeekTarget {
  time: number;
  token: number;
}
export function MediaPlayer({
  projectId,
  start = 0,
  end,
  seek,
  onTimeChange,
  className,
  caption,
  overlay,
  onVideo,
}: {
  projectId: string;
  start?: number;
  end?: number;
  seek?: SeekTarget;
  onTimeChange?: (seconds: number) => void;
  className?: string;
  caption?: string;
  overlay?: React.ReactNode;
  onVideo?: (video: HTMLVideoElement | null) => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [version, setVersion] = useState(0);
  const [aspect, setAspect] = useState(16 / 9);
  useEffect(() => {
    const source = ref.current;
    onVideo?.(source);
    return () => {
      source?.pause();
      onVideo?.(null);
    };
  }, [onVideo, projectId, version, error]);
  const target = seek?.time ?? start;
  useEffect(() => {
    setError(false);
    setLoaded(false);
  }, [projectId, version]);
  useEffect(() => {
    const video = ref.current;
    if (video && video.readyState >= 1)
      video.currentTime = Math.max(
        0,
        Math.min(target, Number.isFinite(video.duration) ? video.duration : target),
      );
  }, [projectId, start, target, seek?.token, loaded]);
  return (
    <div
      style={{ aspectRatio: aspect }}
      className={cn(
        'relative flex aspect-video min-h-0 items-center justify-center overflow-hidden bg-[#080a08]',
        className,
      )}
    >
      {error ? (
        <div role="status" className="flex flex-col items-center gap-3 px-6 py-10 text-center">
          <AlertTriangle className="size-6 text-amber-200/70" />
          <div>
            <p className="text-xs text-zinc-300">Preview unavailable</p>
            <p className="mx-auto mt-2 max-w-sm text-[11px] leading-relaxed text-zinc-500">
              The source may still be importing, missing, or use a codec this browser cannot play.
              The original recording is used for analysis and export.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setError(false);
              setLoaded(false);
              setVersion((v) => v + 1);
            }}
          >
            <RotateCcw />
            Retry preview
          </Button>
        </div>
      ) : (
        <>
          <video
            key={`${projectId}-${version}`}
            ref={ref}
            className="size-full object-contain"
            src={api.media(projectId)}
            controls
            preload="metadata"
            playsInline
            onLoadedMetadata={() => {
              setLoaded(true);
              if (ref.current?.videoWidth && ref.current.videoHeight)
                setAspect(ref.current.videoWidth / ref.current.videoHeight);
              if (ref.current) ref.current.currentTime = Math.max(0, target);
            }}
            onError={() => setError(true)}
            onTimeUpdate={(event) => {
              const video = event.currentTarget;
              if (end !== undefined && video.currentTime >= end) {
                video.pause();
                if (video.currentTime > end + 0.2) video.currentTime = end;
              }
              if (end !== undefined && video.currentTime < start - 0.2) video.currentTime = start;
              onTimeChange?.(video.currentTime);
            }}
            onPlay={(event) => {
              if (end !== undefined && event.currentTarget.currentTime >= end - 0.05)
                event.currentTarget.currentTime = start;
            }}
            aria-label="Source video preview"
          />
          {!loaded && (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#0d100d]">
              <Film className="size-7 text-zinc-600" strokeWidth={1.3} />
              <p className="text-[11px] text-zinc-500">Loading source preview…</p>
            </div>
          )}
          {loaded && overlay}
          {caption && (
            <div
              aria-live="off"
              className="pointer-events-none absolute bottom-[18%] left-[10%] right-[10%] text-center"
            >
              <span className="rounded bg-black/75 px-3 py-1.5 text-sm font-semibold text-white shadow-lg sm:text-lg">
                {caption}
              </span>
            </div>
          )}
          {end !== undefined && (
            <span className="pointer-events-none absolute left-3 top-3 rounded bg-black/70 px-2 py-1 font-mono text-[11px] text-zinc-300">
              Source {time(start)} – {time(end)}
            </span>
          )}
        </>
      )}
    </div>
  );
}
