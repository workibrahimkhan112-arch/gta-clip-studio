import { useEffect, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  Check,
  Circle,
  Film,
  FolderPlus,
  Loader2,
  RefreshCw,
  X,
} from '../icons';
import type { AppIcon } from '../icons';
import { Link } from 'react-router-dom';
import { useStudio, type ResourceKey } from '../stores/studio';
import { useUI } from '../stores/ui';
import { Badge, Button, Card } from './ui';
import { cn, label } from '../lib/utils';

export function PageHeading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="page-heading">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-2">{eyebrow}</p>}
        <h1 className="text-base font-semibold leading-tight tracking-tight">{title}</h1>
        {description && (
          <p className="mt-2 max-w-2xl text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}
export function EmptyState({
  icon: Icon = Film,
  title,
  description,
  children,
  compact,
}: {
  icon?: AppIcon;
  title: string;
  description: string;
  children?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-5 text-center',
        compact ? 'py-10' : 'py-16',
      )}
    >
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-white/[0.07] bg-white/[0.025]">
        <Icon className="size-5 text-zinc-500" strokeWidth={1.5} />
      </div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-2 max-w-sm text-xs leading-relaxed text-muted-foreground">{description}</p>
      {children && <div className="mt-5 flex flex-wrap justify-center gap-2">{children}</div>}
    </div>
  );
}
export function ImportButton({
  size = 'default',
  variant = 'default',
  children,
}: {
  size?: 'sm' | 'default' | 'lg';
  variant?: 'default' | 'secondary' | 'outline';
  children?: React.ReactNode;
}) {
  return (
    <Button size={size} variant={variant} onClick={() => useUI.getState().setImportOpen(true)}>
      <FolderPlus />
      {children || 'Import footage'}
    </Button>
  );
}
export function StatusBadge({ status }: { status: string }) {
  const active = ['running', 'analyzing', 'importing'].includes(status);
  const variant = ['completed', 'analyzed', 'ready'].includes(status)
    ? 'lime'
    : ['failed', 'error'].includes(status)
      ? 'red'
      : active || status === 'queued'
        ? 'amber'
        : 'neutral';
  return (
    <Badge variant={variant}>
      {active ? (
        <Loader2 className="size-2.5 animate-spin" />
      ) : (
        <Circle className="size-1.5 fill-current" />
      )}
      {label(status)}
    </Badge>
  );
}
export function ErrorBanner({
  message,
  retry,
  className,
}: {
  message: string;
  retry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-3 rounded-md border border-red-300/15 bg-red-300/[0.04] p-3 text-xs text-red-200',
        className,
      )}
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words leading-relaxed">{message}</span>
      {retry && (
        <button
          onClick={retry}
          className="flex shrink-0 items-center gap-1.5 rounded px-1 py-0.5 text-[11px] underline underline-offset-4"
        >
          <RefreshCw className="size-3" />
          Retry
        </button>
      )}
    </div>
  );
}
export function ResourceState({
  resource,
  children,
}: {
  resource: ResourceKey;
  children: React.ReactNode;
}) {
  const loading = useStudio((s) => s.loading[resource]);
  const loaded = useStudio((s) => s.loaded[resource]);
  const error = useStudio((s) => s.errors[resource]);
  if (!loaded && !error)
    return (
      <div
        role="status"
        className="flex min-h-44 items-center justify-center gap-2 text-xs text-muted-foreground"
      >
        <Loader2 className="size-4 animate-spin text-primary" />
        Loading {resource}…
      </div>
    );
  return (
    <>
      {error && (
        <ErrorBanner
          className="mb-4"
          message={`${loaded ? 'Showing last received data. ' : ''}${error}`}
          retry={() => {
            void useStudio.getState().refresh([resource]);
          }}
        />
      )}
      {loaded && children}
      {loading && loaded && (
        <span className="sr-only" role="status">
          Refreshing {resource}
        </span>
      )}
    </>
  );
}
export function MediaThumbnail({
  src,
  alt,
  className,
  children,
}: {
  src: string;
  alt: string;
  className?: string;
  children?: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return (
    <div className={cn('relative aspect-video overflow-hidden bg-[#101410]', className)}>
      {!failed ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          onError={() => setFailed(true)}
          className="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
        />
      ) : (
        <div className="thumbnail-fallback flex size-full flex-col items-center justify-center gap-2">
          <Film className="size-6 text-zinc-600" strokeWidth={1.25} />
          <span className="text-[11px] uppercase tracking-[0.14em] text-zinc-600">
            Preview unavailable
          </span>
        </div>
      )}
      {children}
    </div>
  );
}
export function DependencyWarnings() {
  const health = useStudio((s) => s.data.health);
  const settings = useStudio((s) => s.data.settings);
  if (!health) return null;
  const { ffmpeg, ffprobe, yt_dlp } = health.capabilities;
  const captionSetup =
    settings?.caption_style !== 'none' &&
    settings?.caption_provider !== 'groq' &&
    (!health.capabilities.faster_whisper || !health.capabilities.caption_model_cached);
  if (ffmpeg && ffprobe && yt_dlp && !captionSetup) return null;
  return (
    <div className="mb-5 flex items-start gap-3 rounded-lg border border-amber-300/15 bg-amber-300/[0.035] px-4 py-3">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-200" />
      <div className="flex-1 text-xs leading-relaxed text-amber-100/80">
        <span className="font-medium text-amber-200">Setup needed. </span>
        {!ffmpeg && 'FFmpeg is missing; analysis and export are unavailable. '}
        {!ffprobe && 'FFprobe is missing; video metadata cannot be read. '}
        {!yt_dlp && 'yt-dlp is missing; YouTube import is unavailable. '}
        {captionSetup &&
          'Local subtitles need faster-whisper and a cached model. Rerun launcher setup, choose No captions, or opt into Groq in Settings. Local detection still works. '}
      </div>
      <Link
        className="shrink-0 text-[11px] text-amber-200 underline underline-offset-4"
        to="/settings"
      >
        Settings
      </Link>
    </div>
  );
}
export function SectionLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground transition-colors hover:text-primary"
    >
      {children}
      <ArrowUpRight className="size-3.5" />
    </Link>
  );
}
export function Metric({
  icon: Icon,
  label: text,
  value,
  detail,
}: {
  icon: AppIcon;
  label: string;
  value: React.ReactNode;
  detail: string;
}) {
  return (
    <Card className="min-w-0 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">{text}</span>
        <Icon className="size-3.5 text-zinc-500" />
      </div>
      <p className="mt-3 truncate font-mono text-base font-medium tracking-tight">{value}</p>
      <p className="mt-1 truncate text-[11px] text-zinc-500">{detail}</p>
    </Card>
  );
}
export function Toasts() {
  const toasts = useUI((s) => s.toasts);
  return (
    <div
      className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-[min(390px,calc(100vw-40px))] flex-col gap-2"
      aria-live="polite"
      aria-relevant="additions"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role={toast.variant === 'error' ? 'alert' : 'status'}
          className="pointer-events-auto flex gap-3 rounded-lg border border-white/10 bg-[#252b24] p-4 shadow-2xl"
        >
          {toast.variant === 'error' ? (
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-red-300" />
          ) : (
            <Check className="mt-0.5 size-4 shrink-0 text-primary" />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium">{toast.title}</p>
            {toast.message && (
              <p className="mt-1 break-words text-[11px] leading-relaxed text-muted-foreground">
                {toast.message}
              </p>
            )}
          </div>
          <button
            className="self-start rounded text-zinc-500 hover:text-white"
            aria-label="Dismiss notification"
            onClick={() => useUI.getState().dismiss(toast.id)}
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
