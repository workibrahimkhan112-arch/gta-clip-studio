import { useEffect, useState } from 'react';
import type { TranscriptWord } from '../types/api';
import { Button, Textarea } from './ui';

export function WordTimingsEditor({
  words,
  start,
  end,
  disabled,
  onApply,
}: {
  words: TranscriptWord[];
  start: number;
  end: number;
  disabled: boolean;
  onApply: (words: TranscriptWord[]) => void;
}) {
  const [draft, setDraft] = useState(JSON.stringify(words, null, 2));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDraft(JSON.stringify(words, null, 2));
  }, [words]);
  const apply = () => {
    try {
      const value: unknown = JSON.parse(draft);
      if (!Array.isArray(value) || value.length > 1000)
        throw new Error('Use an array of at most 1000 words.');
      let previous = start;
      for (const word of value) {
        if (
          !word ||
          typeof word !== 'object' ||
          typeof word.word !== 'string' ||
          !word.word.trim() ||
          word.word.length > 150 ||
          typeof word.start !== 'number' ||
          typeof word.end !== 'number' ||
          !Number.isFinite(word.start) ||
          !Number.isFinite(word.end) ||
          word.start < start ||
          word.start < previous ||
          word.end > end + 0.01 ||
          word.end <= word.start
        ) {
          throw new Error(
            'Each word needs text and ordered start/end seconds within this caption.',
          );
        }
        previous = word.start;
      }
      onApply(value.map((word) => ({ start: word.start, end: word.end, word: word.word.trim() })));
      setError(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Invalid word timings JSON.');
    }
  };
  return (
    <details className="mt-2 text-[11px] text-zinc-400">
      <summary className="cursor-pointer py-1">Word timings · animated captions</summary>
      <p className="my-2 leading-relaxed">
        Edit start, end, and word values in seconds from the clip start. Apply before saving.
      </p>
      <Textarea
        aria-label="Word timings JSON"
        className="min-h-32 font-mono text-[11px]"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        disabled={disabled}
        spellCheck={false}
      />
      {error && (
        <p role="alert" className="mt-2 text-red-300">
          {error}
        </p>
      )}
      <Button variant="secondary" size="sm" className="mt-2" onClick={apply} disabled={disabled}>
        Apply word timings
      </Button>
    </details>
  );
}
