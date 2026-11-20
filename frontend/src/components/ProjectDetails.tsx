import { FileVideo, HardDrive } from 'lucide-react';
import type { Project } from '../types/api';
import { bytes, date, time } from '../lib/utils';
import { Card, CardHeader, CardTitle } from './ui';

export function ProjectDetails({ project }: { project: Project }) {
  const { metadata: m } = project;
  const facts = [
    ['Duration', time(m.duration)],
    ['Resolution', m.width && m.height ? `${m.width} × ${m.height}` : 'Not available'],
    ['Frame rate', m.fps != null ? `${m.fps.toFixed(2)} fps` : 'Not available'],
    ['Video codec', m.video_codec || 'Not available'],
    ['Audio codec', m.audio_codec || 'Not available'],
    ['File size', bytes(m.file_size)],
    ['Imported', date(project.created_at)],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileVideo className="size-3.5 text-zinc-500" />
          Source information
        </CardTitle>
      </CardHeader>
      <dl className="space-y-3.5 p-4">
        {facts.map(([name, value]) => (
          <div key={name} className="flex items-start justify-between gap-4 text-[11px]">
            <dt className="shrink-0 text-zinc-500">{name}</dt>
            <dd className="min-w-0 break-words text-right font-mono text-zinc-300">{value}</dd>
          </div>
        ))}
        <div className="border-t border-border pt-3">
          <dt className="mb-2 flex items-center gap-1.5 text-[11px] text-zinc-500">
            <HardDrive className="size-3" />
            {project.source_type === 'youtube' ? 'Original URL' : 'Server source path'}
          </dt>
          <dd className="break-all rounded bg-black/20 p-2.5 font-mono text-[11px] leading-relaxed text-zinc-500">
            {project.source_type === 'youtube'
              ? project.source_url || 'Not available'
              : project.source_path || 'Not available'}
          </dd>
        </div>
        {m.audio_streams && (
          <div className="border-t border-border pt-3">
            <dt className="mb-2 text-[11px] text-zinc-500">
              Audio streams ({m.audio_streams.length})
            </dt>
            <dd className="space-y-2">
              {m.audio_streams.length ? (
                m.audio_streams.map((stream) => (
                  <p key={stream.index} className="font-mono text-[11px] text-zinc-500">
                    #{stream.index} · {stream.codec} · {stream.sample_rate} Hz · {stream.channels}{' '}
                    ch
                  </p>
                ))
              ) : (
                <p className="text-[11px] text-zinc-600">This recording has no audio stream.</p>
              )}
            </dd>
          </div>
        )}
      </dl>
    </Card>
  );
}
