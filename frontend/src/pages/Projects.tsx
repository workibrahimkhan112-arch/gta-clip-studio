import { useMemo, useState } from 'react';
import { Film, FolderOpen, Search, SlidersHorizontal, X } from 'lucide-react';
import { useStudio } from '../stores/studio';
import { Button, Card, Input, Select } from '../components/ui';
import {
  DependencyWarnings,
  EmptyState,
  ImportButton,
  PageHeading,
  ResourceState,
} from '../components/shared';
import { ProjectCard } from '../components/ProjectCard';

export function Projects() {
  const projects = useStudio((s) => s.data.projects);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState('newest');
  const filtered = useMemo(
    () =>
      projects
        .filter(
          (p) =>
            p.name.toLowerCase().includes(query.toLowerCase()) &&
            (status === 'all' || p.status === status),
        )
        .sort((a, b) =>
          sort === 'name'
            ? a.name.localeCompare(b.name)
            : sort === 'duration'
              ? (b.metadata.duration ?? 0) - (a.metadata.duration ?? 0)
              : b.created_at.localeCompare(a.created_at),
        ),
    [projects, query, status, sort],
  );
  return (
    <>
      <PageHeading title="Projects" description="Your imported recordings.">
        <ImportButton />
      </PageHeading>
      <DependencyWarnings />
      <ResourceState resource="projects">
        {projects.length ? (
          <>
            <div className="filter-bar">
              <div className="relative w-full sm:max-w-xs">
                <Search className="absolute left-3 top-3 size-3.5 text-zinc-500" />
                <Input
                  className="pl-9 pr-8"
                  aria-label="Search projects"
                  placeholder="Find a recording…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {query && (
                  <button
                    aria-label="Clear search"
                    onClick={() => setQuery('')}
                    className="absolute right-3 top-3"
                  >
                    <X className="size-3.5 text-zinc-500" />
                  </button>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <SlidersHorizontal className="mr-1 hidden size-3.5 text-zinc-500 sm:block" />
                <Select
                  className="w-36"
                  aria-label="Project status"
                  value={status}
                  onValueChange={setStatus}
                  options={[
                    { value: 'all', label: 'All statuses' },
                    ...['ready', 'importing', 'analyzing', 'analyzed', 'failed'].map((value) => ({
                      value,
                      label: value.charAt(0).toUpperCase() + value.slice(1),
                    })),
                  ]}
                />
                <Select
                  className="w-36"
                  aria-label="Sort projects"
                  value={sort}
                  onValueChange={setSort}
                  options={[
                    { value: 'newest', label: 'Newest first' },
                    { value: 'name', label: 'Name A–Z' },
                    { value: 'duration', label: 'Longest first' },
                  ]}
                />
              </div>
            </div>
            <div className="mb-4 flex items-center gap-2 text-[11px] text-zinc-500">
              <FolderOpen className="size-3" />
              {filtered.length} of {projects.length} projects
            </div>
            {filtered.length ? (
              <div className="project-grid">
                {filtered.map((project) => (
                  <ProjectCard project={project} key={project.id} />
                ))}
              </div>
            ) : (
              <Card>
                <EmptyState
                  icon={Search}
                  title="No matching recordings"
                  description="Try another name or reset the status filter."
                >
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setQuery('');
                      setStatus('all');
                    }}
                  >
                    Clear filters
                  </Button>
                </EmptyState>
              </Card>
            )}
          </>
        ) : (
          <Card>
            <EmptyState
              icon={Film}
              title="No projects yet"
              description="Upload a video, reference a local file, or import a public YouTube URL. Original local files are never moved or deleted."
            >
              <ImportButton />
            </EmptyState>
            <div className="flex flex-wrap justify-center gap-5 border-t border-border px-5 py-4 text-[11px] text-zinc-600">
              <span>Local file reference</span>
              <span>Video upload</span>
              <span>Public YouTube URL</span>
            </div>
          </Card>
        )}
      </ResourceState>
    </>
  );
}
