import { useMemo, useState } from 'react';
import { Search, Sparkles, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useStudio } from '../stores/studio';
import { label } from '../lib/utils';
import { Badge, Button, Card, Input, Select } from '../components/ui';
import { EmptyState, PageHeading, ResourceState } from '../components/shared';
import { ExportControls } from '../components/ExportControls';
import { HighlightCard } from '../components/HighlightCard';
import { HighlightEditor } from '../components/HighlightEditor';

export function Highlights() {
  const { highlights, projects } = useStudio((s) => s.data);
  const [query, setQuery] = useState('');
  const [projectId, setProjectId] = useState('all');
  const [category, setCategory] = useState('all');
  const [sort, setSort] = useState('score');
  const [selection, setSelection] = useState('all');
  const [editorId, setEditorId] = useState<string | null>(null);
  const categories = [...new Set(highlights.map((h) => h.category))].sort();
  const filtered = useMemo(
    () =>
      highlights
        .filter(
          (h) =>
            (projectId === 'all' || h.project_id === projectId) &&
            (category === 'all' || h.category === category) &&
            (selection === 'all' || (selection === 'selected' ? h.selected : !h.selected)) &&
            h.title.toLowerCase().includes(query.toLowerCase()),
        )
        .sort((a, b) =>
          sort === 'score'
            ? b.score - a.score
            : sort === 'ai'
              ? (b.ai_review?.score ?? -1) - (a.ai_review?.score ?? -1) || b.score - a.score
              : sort === 'time'
                ? a.start - b.start
                : b.duration - a.duration,
        ),
    [highlights, projectId, category, selection, query, sort],
  );
  return (
    <>
      <PageHeading title="Highlights" description="Review clips and select the ones to export.">
        <Badge variant="lime">
          <Sparkles className="size-3" />
          Detection-ranked
        </Badge>
      </PageHeading>
      <ResourceState resource="highlights">
        {highlights.length ? (
          <>
            <div className="filter-bar items-start">
              <div className="relative w-full sm:max-w-[260px]">
                <Search className="absolute left-3 top-3 size-3.5 text-zinc-500" />
                <Input
                  className="pl-9 pr-8"
                  aria-label="Search highlights"
                  placeholder="Search highlights…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {query && (
                  <button
                    aria-label="Clear search"
                    className="absolute right-3 top-3"
                    onClick={() => setQuery('')}
                  >
                    <X className="size-3.5 text-zinc-500" />
                  </button>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <Select
                  className="w-40"
                  aria-label="Filter by project"
                  value={projectId}
                  onValueChange={setProjectId}
                  options={[
                    { value: 'all', label: 'All projects' },
                    ...projects.map((p) => ({ value: p.id, label: p.name })),
                  ]}
                />
                <Select
                  className="w-36"
                  aria-label="Filter by category"
                  value={category}
                  onValueChange={setCategory}
                  options={[
                    { value: 'all', label: 'All categories' },
                    ...categories.map((value) => ({ value, label: label(value) })),
                  ]}
                />
                <Select
                  className="w-36"
                  aria-label="Filter by selection"
                  value={selection}
                  onValueChange={setSelection}
                  options={[
                    { value: 'all', label: 'All highlights' },
                    { value: 'selected', label: 'Selected only' },
                    { value: 'unselected', label: 'Unselected only' },
                  ]}
                />
                <Select
                  className="w-36"
                  aria-label="Sort highlights"
                  value={sort}
                  onValueChange={setSort}
                  options={[
                    { value: 'score', label: 'Detection score' },
                    { value: 'ai', label: 'AI review score' },
                    { value: 'time', label: 'Source time' },
                    { value: 'duration', label: 'Longest first' },
                  ]}
                />
              </div>
            </div>
            <ExportControls highlights={filtered} />
            {filtered.length ? (
              <>
                <p className="mb-4 text-[11px] text-zinc-500">
                  {filtered.length} highlights · categories describe heuristic signals, not proven
                  events
                </p>
                <div className="highlight-grid">
                  {filtered.map((highlight) => (
                    <HighlightCard
                      key={highlight.id}
                      highlight={highlight}
                      projectName={projects.find((p) => p.id === highlight.project_id)?.name}
                      onEdit={(h) => setEditorId(h.id)}
                    />
                  ))}
                </div>
              </>
            ) : (
              <Card>
                <EmptyState
                  icon={Search}
                  title="No matching highlights"
                  description="Try another search or clear the filters."
                >
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setQuery('');
                      setProjectId('all');
                      setCategory('all');
                      setSelection('all');
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
              icon={Sparkles}
              title="No highlights yet"
              description="Open a project and run analysis, then review the results before exporting."
            >
              <Button asChild>
                <Link to="/projects">Open projects</Link>
              </Button>
            </EmptyState>
          </Card>
        )}
      </ResourceState>
      <HighlightEditor
        highlight={highlights.find((h) => h.id === editorId) ?? null}
        onClose={() => setEditorId(null)}
      />
    </>
  );
}
