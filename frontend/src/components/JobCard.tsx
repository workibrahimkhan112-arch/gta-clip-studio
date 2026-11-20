import { Clock3, Gauge, Square, Timer, Waves } from 'lucide-react';
import type { Job } from '../types/api';
import { api } from '../services/api';
import { isActive, label, time } from '../lib/utils';
import { useAction } from '../hooks/useAction';
import { useStudio } from '../stores/studio';
import { Badge, Button, Progress } from './ui';
import { StatusBadge } from './shared';

export function JobCard({ job, compact = false }: { job: Job; compact?: boolean }) {
  const { run, pending } = useAction();
  const active = isActive(job);
  const progress = job.progress === null ? null : Math.min(1, Math.max(0, job.progress));
  const cancel = () =>
    run(async () => {
      const result = await api.cancelJob(job.id);
      useStudio.getState().upsertJob(result);
      await useStudio.getState().refresh(['jobs', 'exports', 'projects', 'highlights']);
    }, 'Cancellation requested');
  return (
    <div className="min-w-0 rounded-lg border border-border bg-black/10 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Waves className="size-3.5 text-primary" />
            <h3 className="text-xs font-medium">
              {job.kind === 'analyze'
                ? 'Highlight analysis'
                : job.kind === 'youtube'
                  ? 'YouTube import'
                  : job.kind === 'captions'
                    ? 'Caption generation'
                    : job.kind === 'score'
                      ? 'Editorial AI review'
                      : job.kind === 'titles'
                        ? 'Title suggestions'
                        : 'Clip export'}
            </h3>
            <StatusBadge status={job.status} />
          </div>
          <p className="mt-2 break-words text-[11px] text-muted-foreground">
            {label(
              job.stage || (job.status === 'queued' ? 'Waiting for processing slot' : job.status),
            )}
          </p>
        </div>
        {active && (
          <Button
            variant="ghost"
            size="sm"
            loading={pending}
            onClick={() => {
              void cancel();
            }}
            aria-label={`Cancel ${job.kind} job`}
          >
            <Square className="!size-3" />
            Cancel
          </Button>
        )}
      </div>
      {active && (
        <>
          <div className="mb-1.5 mt-4 flex justify-between gap-3 text-[11px] text-muted-foreground">
            <span>Current stage</span>
            <span className="font-mono">
              {progress === null ? 'Not reported' : `${Math.round(progress * 100)}%`}
            </span>
          </div>
          <Progress value={progress} label={`${label(job.stage)} stage progress`} />
        </>
      )}
      {job.detail && (
        <p className="mt-3 break-words text-[11px] leading-relaxed text-zinc-400">{job.detail}</p>
      )}
      {job.error && (
        <p role="alert" className="mt-3 break-words text-xs leading-relaxed text-red-300">
          {job.error}
        </p>
      )}
      {!compact && (
        <div className="mt-4 grid grid-cols-2 gap-y-3 border-t border-border pt-3 sm:grid-cols-4">
          <JobMetric
            icon={Clock3}
            label="Processed"
            value={
              job.total_seconds > 0
                ? `${time(job.processed_seconds)} / ${time(job.total_seconds)}`
                : 'Not reported'
            }
          />
          <JobMetric icon={Timer} label="Elapsed" value={time(job.elapsed_seconds)} />
          <JobMetric
            icon={Gauge}
            label="Speed"
            value={job.speed_ratio == null ? 'Not reported' : `${job.speed_ratio.toFixed(2)}×`}
          />
          <JobMetric
            icon={Timer}
            label="Stage ETA"
            value={job.eta_seconds == null ? 'Not reported' : time(job.eta_seconds)}
          />
        </div>
      )}
      {!compact && (
        <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
          Stage progress is reported by the server, not an overall estimate. Jobs run one at a time.
        </p>
      )}
      {compact && active && (
        <div className="mt-3 flex flex-wrap gap-2">
          {job.speed_ratio != null && <Badge>{job.speed_ratio.toFixed(2)}× realtime</Badge>}
          {job.eta_seconds != null && <Badge>Stage ETA {time(job.eta_seconds)}</Badge>}
        </div>
      )}
    </div>
  );
}
function JobMetric({
  icon: Icon,
  label: text,
  value,
}: {
  icon: typeof Clock3;
  label: string;
  value: string;
}) {
  return (
    <div>
      <p className="mb-1 flex items-center gap-1 text-[11px] uppercase tracking-wider text-zinc-500">
        <Icon className="size-2.5" />
        {text}
      </p>
      <p className="font-mono text-[11px] text-zinc-300">{value}</p>
    </div>
  );
}
