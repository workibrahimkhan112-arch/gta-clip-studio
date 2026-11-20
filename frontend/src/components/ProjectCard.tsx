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
        <MediaThumbnail src={api.thumbnail(project.id)} alt={`Preview of ${project.name}`}>
          <span className="absolute left-3 top-3">
            <StatusBadge status={project.status} />
          </span>
          <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[11px] text-zinc-200">
            {time(project.metadata.duration)}
          </span>
        </MediaThumbnail>
        <div className="p-3.5">
          <h3
            className="truncate text-xs font-semibold group-hover:text-primary"
            title={project.name}
          >
            {project.name}
          </h3>
          <p className="mt-1 text-[11px] text-zinc-500">
            {date(project.created_at)} · {project.highlight_count} highlight
            {project.highlight_count === 1 ? '' : 's'}
          </p>
        </div>
      </Card>
    </Link>
  );
}
