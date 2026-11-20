import { ArrowUpRight, Film, HardDrive, Play, Youtube } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { Project } from '../types/api';
import { api } from '../services/api';
import { date, time } from '../lib/utils';
import { MediaThumbnail, StatusBadge } from './shared';
import { Card } from './ui';

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="group block min-w-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      <Card className="overflow-hidden transition-colors group-hover:border-white/20">
        <MediaThumbnail
          src={api.thumbnail(project.id)}
          alt={`Preview of ${project.name}`}
          className="border-b border-border"
        >
          <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
          <span className="absolute left-3 top-3">
            <StatusBadge status={project.status} />
          </span>
          <div className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
            <span className="flex size-10 items-center justify-center rounded-full border border-white/20 bg-black/40 text-white backdrop-blur-sm">
              <Play className="ml-0.5 size-4 fill-white" />
            </span>
          </div>
          <span className="absolute bottom-2.5 right-3 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[11px] text-zinc-200">
            {time(project.metadata.duration)}
          </span>
          <span className="absolute bottom-3 left-3 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-300">
            {project.source_type === 'youtube' ? (
              <Youtube className="size-3" />
            ) : (
              <HardDrive className="size-3" />
            )}
            {project.source_type === 'youtube' ? 'YouTube import' : 'Local recording'}
          </span>
        </MediaThumbnail>
        <div className="p-4">
          <div className="flex items-start justify-between gap-3">
            <h3
              className="truncate text-xs font-semibold leading-5 group-hover:text-primary"
              title={project.name}
            >
              {project.name}
            </h3>
            <ArrowUpRight className="mt-0.5 size-3.5 shrink-0 text-zinc-600 group-hover:text-primary" />
          </div>
          <p className="mt-1 text-[11px] text-zinc-500">
            {date(project.created_at)}
            {project.metadata.width && project.metadata.height
              ? ` · ${project.metadata.width} × ${project.metadata.height}`
              : ''}
          </p>
          <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
            <span className="flex items-center gap-1.5 text-[11px] text-zinc-400">
              <Film className="size-3 text-zinc-500" />
              {project.highlight_count} highlight{project.highlight_count === 1 ? '' : 's'}
            </span>
            <span className="text-[11px] text-zinc-600">Open workspace</span>
          </div>
        </div>
      </Card>
    </Link>
  );
}
