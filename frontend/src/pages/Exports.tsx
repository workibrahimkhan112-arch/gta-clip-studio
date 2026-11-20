import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  Clock3,
  Download,
  FileVideo,
  FolderOpen,
  History,
  Search,
} from 'lucide-react';
import { useStudio } from '../stores/studio';
import { date, isActive } from '../lib/utils';
import { Badge, Button, Card, CardHeader, CardTitle, Input, Select } from '../components/ui';
import { EmptyState, PageHeading, ResourceState, StatusBadge } from '../components/shared';
import { JobCard } from '../components/JobCard';

export function Exports() {
  const { exports, jobs, projects, highlights, settings } = useStudio((s) => s.data);
  const loaded = useStudio((s) => s.loaded.exports);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const active = jobs
    .filter((j) => j.kind === 'export' && isActive(j))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const history = jobs
    .filter((j) => j.kind === 'export' && !isActive(j))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const filtered = [...exports]
    .filter(
      (e) =>
        (!query ||
          `${e.filename} ${highlights.find((h) => h.id === e.highlight_id)?.title || ''}`
            .toLowerCase()
            .includes(query.toLowerCase())) &&
        (status === 'all' || e.status === status),
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const completed = exports.filter((e) => e.status === 'completed').length;
  return (
    <>
      <PageHeading title="Exports" description="Queued jobs and exported files.">
        <Button variant="secondary" asChild>
          <Link to="/highlights">
            <FolderOpen />
            Choose highlights
          </Link>
        </Button>
      </PageHeading>
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        {[
          { label: 'Completed clips', value: loaded ? completed : '—', icon: Download },
          {
            label: 'Queued / running',
            value: useStudio.getState().loaded.jobs ? active.length : '—',
            icon: Clock3,
          },
          {
            label: 'Default output',
            value: settings?.output_resolution
              ? settings.output_resolution.replace('x', ' × ')
              : 'Not loaded',
            icon: FileVideo,
          },
        ].map(({ label, value, icon: Icon }) => (
          <Card key={label} className="flex items-center gap-4 px-5 py-4">
            <div className="flex size-9 items-center justify-center rounded-lg border border-border bg-black/10">
              <Icon className="size-4 text-zinc-500" />
            </div>
            <div>
              <p className="text-[11px] text-zinc-500">{label}</p>
              <p className="mt-1 font-mono text-base font-medium">{value}</p>
            </div>
          </Card>
        ))}
      </div>
      <ResourceState resource="jobs">
        {active.length > 0 && (
          <section className="mb-7">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-xs font-semibold">
                <Clock3 className="size-3.5 text-amber-200" />
                Export queue<Badge variant="amber">{active.length}</Badge>
              </h2>
              <span className="text-[11px] text-zinc-600">One processing job at a time</span>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {active.map((job) => (
                <div key={job.id}>
                  <p className="mb-1.5 truncate px-1 text-[11px] text-zinc-500">
                    {projects.find((p) => p.id === job.project_id)?.name || 'Export job'} ·{' '}
                    {job.id.slice(0, 8)}
                  </p>
                  <JobCard job={job} />
                </div>
              ))}
            </div>
          </section>
        )}
      </ResourceState>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Download className="size-4 text-zinc-500" />
            Rendered files
          </CardTitle>
          <Badge>{loaded ? exports.length : '—'}</Badge>
        </CardHeader>
        <div className="p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="relative w-full sm:max-w-xs">
              <Search className="absolute left-3 top-3 size-3.5 text-zinc-500" />
              <Input
                className="pl-9"
                placeholder="Find an export…"
                aria-label="Search exports"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <Select
              className="w-40"
              aria-label="Export status filter"
              value={status}
              onValueChange={setStatus}
              options={[
                { value: 'all', label: 'All statuses' },
                { value: 'completed', label: 'Completed' },
                { value: 'running', label: 'Running' },
                { value: 'queued', label: 'Queued' },
                { value: 'failed', label: 'Failed' },
                { value: 'cancelled', label: 'Cancelled' },
              ]}
            />
          </div>
          <ResourceState resource="exports">
            {filtered.length ? (
              <div className="space-y-2">
                {filtered.map((record) => {
                  const title = highlights.find((h) => h.id === record.highlight_id)?.title;
                  const download = record.status === 'completed';
                  return (
                    <div
                      key={record.id}
                      className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-black/10 px-4 py-3.5"
                    >
                      <div className="flex size-10 shrink-0 items-center justify-center rounded-md border border-white/5 bg-white/[0.02]">
                        <FileVideo className="size-5 text-zinc-500" />
                      </div>
                      <div className="min-w-0 flex-1 basis-36">
                        <h3 className="truncate text-xs font-medium" title={record.filename}>
                          {record.filename || title || 'Clip export'}
                        </h3>
                        <p className="mt-1 truncate text-[11px] text-zinc-500">
                          {title ? `${title} · ` : ''}
                          {date(record.created_at)}
                        </p>
                        {record.error && (
                          <p role="alert" className="mt-2 break-words text-[11px] text-red-300">
                            {record.error}
                          </p>
                        )}
                        {record.output_path && (
                          <p
                            className="mt-1.5 truncate font-mono text-[11px] text-zinc-600"
                            title={record.output_path}
                          >
                            {record.output_path}
                          </p>
                        )}
                      </div>
                      <StatusBadge status={record.status} />
                      {download ? (
                        <Button variant="secondary" size="sm" asChild>
                          <a href={`/api/exports/${encodeURIComponent(record.id)}/file`} download>
                            <Download />
                            Download
                          </a>
                        </Button>
                      ) : (
                        <Button variant="ghost" size="sm" asChild>
                          <Link to={`/projects/${record.project_id}`}>
                            Project
                            <ArrowUpRight />
                          </Link>
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                compact
                icon={Download}
                title={exports.length ? 'No matching exports' : 'No exports yet'}
                description={
                  exports.length
                    ? 'Try a different search or status filter.'
                    : 'Select highlights and a subtitle style to export. Downloads are available after rendering finishes.'
                }
              >
                {!exports.length && (
                  <Button asChild size="sm">
                    <Link to="/highlights">Choose highlights</Link>
                  </Button>
                )}
              </EmptyState>
            )}
          </ResourceState>
          <p className="mt-4 border-t border-border pt-3 text-[11px] leading-relaxed text-zinc-600">
            Output directory:{' '}
            <span className="font-mono">
              {settings
                ? settings.output_folder || 'storage/exports (server default)'
                : 'Settings not loaded'}
            </span>
            . Files are served by the local backend; browser download errors may indicate a missing
            output file.
          </p>
        </div>
      </Card>
      {history.length > 0 && (
        <details className="mt-6 rounded-lg border border-border bg-card">
          <summary className="flex cursor-pointer items-center gap-2 px-5 py-4 text-xs font-medium">
            <History className="size-4 text-zinc-500" />
            Recent export jobs<Badge>{history.length}</Badge>
          </summary>
          <div className="grid gap-3 border-t border-border p-4 lg:grid-cols-2">
            {history.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        </details>
      )}
    </>
  );
}
