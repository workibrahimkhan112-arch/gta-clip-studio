import { useEffect, useRef, useState } from 'react';
import { Pause, Play, RotateCcw, Volume2, VolumeX } from '../icons';
import type { CaptionStyle, LayoutConfig, SubtitleStyle, TranscriptSegment } from '../types/api';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { subtitleTime } from '../lib/subtitles';
import { Button } from './ui';
import { LayoutPreview } from './LayoutPreview';
import { SubtitlePreview } from './SubtitlePreview';
import type { SeekTarget } from './MediaPlayer';

/** One decoded source drives portrait and word-timed previews; seeks are clip-relative. */
export function SubtitlePlayer({
  projectId,
  start,
  end,
  layout,
  segments,
  style,
  seek,
  onTimeChange,
  subtitleStyle,
  editingStyle = false,
  onStyleChange,
}: {
  projectId: string;
  start: number;
  end: number;
  layout: LayoutConfig;
  segments: TranscriptSegment[];
  style: CaptionStyle;
  subtitleStyle?: SubtitleStyle | null;
  editingStyle?: boolean;
  onStyleChange?: (style: SubtitleStyle) => void;
  seek?: SeekTarget;
  onTimeChange: (seconds: number) => void;
}) {
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const change = useRef(onTimeChange);
  const mounted = useRef(false);
  const outputWidth = useStudio((state) =>
    state.data.settings?.output_resolution === '1080x1920' ? 1080 : 720,
  );
  const duration = Math.max(0, end - start);
  change.current = onTimeChange;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!video) return;
    let frame = 0,
      lastDraw = 0,
      lastNotice = 0,
      stopped = false;
    const update = (force = false) => {
      if (stopped) return;
      if (video.currentTime >= end) {
        if (!video.paused) video.pause();
        if (video.currentTime > end + 0.01) video.currentTime = end;
      }
      const value = Math.max(0, Math.min(duration, video.currentTime - start));
      setSeconds(value);
      const now = performance.now();
      // Word preview updates at 30fps; the full editor list only needs 5Hz.
      if (force || now - lastNotice >= 200) {
        change.current(value);
        lastNotice = now;
      }
    };
    const tick = (now: number) => {
      if (stopped || video.paused || video.ended) return;
      if (now - lastDraw >= 33) {
        update();
        lastDraw = now;
      }
      if (!video.paused) frame = requestAnimationFrame(tick);
    };
    const play = () => {
      setPlaying(true);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    const pause = () => {
      setPlaying(false);
      cancelAnimationFrame(frame);
      update(true);
    };
    const seeked = () => update(true);
    const timeupdate = () => update();
    const volume = () => setMuted(video.muted);
    video.addEventListener('play', play);
    video.addEventListener('pause', pause);
    video.addEventListener('ended', pause);
    video.addEventListener('seeked', seeked);
    video.addEventListener('timeupdate', timeupdate);
    video.addEventListener('volumechange', volume);
    if (!video.paused) play();
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      video.removeEventListener('play', play);
      video.removeEventListener('pause', pause);
      video.removeEventListener('ended', pause);
      video.removeEventListener('seeked', seeked);
      video.removeEventListener('timeupdate', timeupdate);
      video.removeEventListener('volumechange', volume);
      video.pause();
    };
  }, [video, start, end, duration]);
  const seekTo = (value: number) => {
    if (!video || !ready) return;
    const relative = Math.max(0, Math.min(duration, Number.isFinite(value) ? value : 0));
    video.currentTime = start + relative;
    setSeconds(relative);
    change.current(relative);
  };
  useEffect(() => {
    if (ready && video) {
      const relative = Math.max(0, Math.min(duration, seek?.time ?? 0));
      video.currentTime = start + relative;
      setSeconds(relative);
      change.current(relative);
    }
  }, [video, ready, start, duration, seek?.token, seek?.time]);
  const play = async () => {
    if (!video || !ready || error) return;
    if (video.currentTime >= end - 0.02 || video.currentTime < start) seekTo(0);
    try {
      await video.play();
    } catch {
      if (mounted.current)
        setError('Could not play this source. Try another browser or retry the preview.');
    }
  };
  return (
    <div className="space-y-3" aria-label="Subtitle player">
      <video
        ref={setVideo}
        src={api.media(projectId)}
        className="sr-only"
        aria-label="Subtitle preview source"
        aria-hidden="true"
        tabIndex={-1}
        preload="metadata"
        playsInline
        onLoadedMetadata={(event) => {
          const source = event.currentTarget;
          if (!Number.isFinite(source.duration) || source.duration < start) {
            setError('This clip is outside the source video. Check the original recording.');
            return;
          }
          setReady(true);
          setError(null);
        }}
        onError={() => {
          setReady(false);
          setPlaying(false);
          setError('This browser cannot play the source. Analysis and export may still work.');
        }}
      />
      <LayoutPreview
        video={video}
        layout={layout}
        className="max-w-[min(260px,29dvh)] sm:max-w-[min(300px,29dvh)]"
      >
        <SubtitlePreview
          segments={segments}
          seconds={seconds}
          style={style}
          position={layout.caption_position}
          subtitleStyle={subtitleStyle}
          outputWidth={outputWidth}
          editable={editingStyle}
          onStyleChange={onStyleChange}
        />
      </LayoutPreview>
      {error && (
        <p role="status" aria-label="Preview error" className="text-xs text-amber-200">
          {error}{' '}
          <button
            className="underline"
            onClick={() => {
              setError(null);
              video?.load();
            }}
          >
            Retry preview
          </button>
        </p>
      )}
      <div className="mx-auto max-w-[360px] space-y-2">
        <input
          aria-label="Preview position"
          type="range"
          min={0}
          max={duration}
          step={0.01}
          value={seconds}
          disabled={!ready || !!error}
          onChange={(event) => seekTo(event.target.valueAsNumber)}
          className="w-full cursor-pointer accent-lime-300 disabled:opacity-40"
        />
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <Button
              variant="secondary"
              size="icon"
              aria-label={playing ? 'Pause preview' : 'Play preview'}
              disabled={!ready || !!error}
              onClick={() => {
                if (playing) video?.pause();
                else void play();
              }}
            >
              {playing ? <Pause /> : <Play />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Replay clip"
              disabled={!ready || !!error}
              onClick={() => {
                seekTo(0);
                void play();
              }}
            >
              <RotateCcw />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={muted ? 'Unmute preview' : 'Mute preview'}
              disabled={!ready || !!error}
              onClick={() => {
                if (video) {
                  video.muted = !video.muted;
                  setMuted(video.muted);
                }
              }}
            >
              {muted ? <VolumeX /> : <Volume2 />}
            </Button>
          </div>
          <output
            aria-label="Preview time"
            aria-live="off"
            className="font-mono text-[11px] text-zinc-400"
          >
            {subtitleTime(seconds)} / {subtitleTime(duration)}
          </output>
        </div>
      </div>
      <p className="text-center text-[11px] text-zinc-500">
        Live subtitle preview. Font wrapping may differ slightly in the export.
      </p>
    </div>
  );
}
