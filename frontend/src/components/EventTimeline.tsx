import { ScanLine } from 'lucide-react';
import type { Highlight, DetectionEvent } from '../types/api';
import { cn, time } from '../lib/utils';
import { Badge } from './ui';

export function EventTimeline({
  duration,
  position,
  highlights,
  events = [],
  focusedId,
  onSeek,
  onSelect,
}: {
  duration: number;
  position: number;
  highlights: Highlight[];
  events?: DetectionEvent[];
  focusedId: string | null;
  onSeek: (seconds: number) => void;
  onSelect: (highlight: Highlight) => void;
}) {
  const valid = duration > 0 && Number.isFinite(duration);
  return (
    <div className="border-t border-border bg-black/10 px-5 pb-5 pt-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-[11px] font-medium">
          <ScanLine className="size-3.5 text-zinc-500" />
          Event timeline
        </h3>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] text-zinc-500">
            {time(position)} / {valid ? time(duration) : '—'}
          </span>
          <Badge>
            {events.length} signal peaks · {highlights.length} clips
          </Badge>
        </div>
      </div>
      {valid ? (
        <>
          <div className="relative mx-1 h-[84px] rounded-md border border-white/5 bg-[#101410]">
            <div className="pointer-events-none absolute inset-0 flex justify-between">
              {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                <div key={n} className="h-full w-px bg-white/[0.035]" />
              ))}
            </div>
            {events.map((event) => (
              <button
                key={event.id}
                className="absolute bottom-0 w-1 rounded-t-sm bg-lime-200/25 hover:bg-lime-200/60 focus-visible:outline focus-visible:outline-primary"
                style={{
                  left: `${Math.max(0, Math.min(99.8, (event.time / duration) * 100))}%`,
                  height: `${Math.max(3, Math.min(24, (event.score / 100) * 24))}px`,
                }}
                title={`${event.category.replaceAll('_', ' ')} · ${time(event.time)} · score ${event.score.toFixed(0)}`}
                aria-label={`Seek measured signal at ${time(event.time)}`}
                onClick={() => onSeek(event.time)}
              />
            ))}
            {highlights.map((highlight, i) => {
              const left = Math.max(0, Math.min(100, (highlight.start / duration) * 100));
              const width = Math.max(
                0,
                Math.min(100 - left, ((highlight.end - highlight.start) / duration) * 100),
              );
              return (
                <button
                  key={highlight.id}
                  style={{ left: `${left}%`, width: `${width}%`, top: `${9 + (i % 3) * 22}px` }}
                  className={cn(
                    'absolute h-4 min-w-[5px] overflow-hidden rounded-sm border border-primary/20 bg-primary/15 text-left text-[11px] text-primary/70 outline-none transition-colors hover:bg-primary/35 focus-visible:ring-2 focus-visible:ring-primary',
                    focusedId === highlight.id && 'border-primary/70 bg-primary/35 text-primary',
                  )}
                  title={`${highlight.title} · ${time(highlight.start)}–${time(highlight.end)} · Score ${highlight.score.toFixed(0)}`}
                  aria-label={`Jump to ${highlight.title}, ${time(highlight.start)}`}
                  onClick={() => onSelect(highlight)}
                >
                  <span className="px-1.5">{highlight.title}</span>
                </button>
              );
            })}
            <div
              className="pointer-events-none absolute -bottom-1 -top-1 z-10 w-px bg-white/70"
              style={{ left: `${Math.max(0, Math.min(100, (position / duration) * 100))}%` }}
            >
              <div className="absolute -left-1 top-0 size-2 rotate-45 bg-white/90" />
            </div>
            {!highlights.length && (
              <p className="absolute inset-0 flex items-center justify-center text-[11px] text-zinc-600">
                No highlights yet
              </p>
            )}
          </div>
          <input
            className="mt-3 h-3 w-full cursor-pointer accent-lime-300"
            aria-label="Seek source video"
            type="range"
            min={0}
            max={duration}
            step="0.1"
            value={Math.min(position, duration)}
            onChange={(e) => onSeek(e.target.valueAsNumber)}
          />
          <div className="mt-1 flex justify-between font-mono text-[11px] text-zinc-600">
            {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
              <span key={fraction}>{time(duration * fraction)}</span>
            ))}
          </div>
        </>
      ) : (
        <div className="rounded-md border border-dashed border-border py-7 text-center text-[11px] text-zinc-600">
          Timeline available after source metadata loads.
        </div>
      )}
    </div>
  );
}
