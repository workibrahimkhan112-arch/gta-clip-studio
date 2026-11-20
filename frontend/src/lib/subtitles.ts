import type { CaptionStyle, SubtitleStyle, TranscriptSegment, TranscriptWord } from '../types/api';

export interface PreviewWord {
  text: string;
  highlighted: boolean;
  lineBreak: boolean;
  separator?: string;
}

const emphasis = new Set([
  'wow',
  'what',
  'no',
  'run',
  'stop',
  'crazy',
  'insane',
  'look',
  'help',
  'boom',
]);

export function emphasizedWord(text: string): boolean {
  const token = text.replace(/^[.,!?;:"'()[\]]+|[.,!?;:"'()[\]]+$/g, '').toLowerCase();
  return (
    emphasis.has(token) ||
    (text.length > 2 && text.toLowerCase() !== text.toUpperCase() && text === text.toUpperCase())
  );
}

// Mirrors the renderer's grouping budget; the browser and libass shape/wrap the font separately.
function glyphUnits(character: string): number {
  if (/\p{Mark}/u.test(character)) return 0;
  if (/\s/u.test(character) || "ilI.,:;'!|".includes(character)) return 0.35;
  if (character.codePointAt(0)! > 127 || 'MW@%＼｛｝'.includes(character)) return 1.1;
  return character.toUpperCase() !== character.toLowerCase() &&
    character === character.toUpperCase()
    ? 0.8
    : 0.65;
}
function units(text: string): number {
  return Array.from(text).reduce((sum, character) => sum + glyphUnits(character), 0);
}
function styleCapacity(style: SubtitleStyle, outputWidth: number): number {
  const scale = outputWidth / 720;
  const padding = Math.max(
    style.outline_width * scale,
    style.background_opacity > 0 ? 3 * scale : 0,
  );
  const left = Math.ceil((style.x - style.width / 2) * outputWidth + padding);
  const right = Math.ceil((1 - style.x - style.width / 2) * outputWidth + padding);
  return Math.max(1.1, (outputWidth - left - right) / (style.font_size * scale));
}
function wordGroups(words: TranscriptWord[], capacity?: number): TranscriptWord[][] {
  const groups: TranscriptWord[][] = [];
  for (const word of words) {
    if (
      !Number.isFinite(word.start) ||
      !Number.isFinite(word.end) ||
      word.end <= word.start ||
      !word.word.trim()
    )
      continue;
    if (!groups.length) groups.push([]);
    let group = groups[groups.length - 1];
    const characters =
      group.reduce((sum, item) => sum + item.word.length + 1, 0) + word.word.length;
    if (
      group.length &&
      (group.length >= 10 ||
        (capacity === undefined
          ? characters > 56
          : units(group.map((item) => item.word).join(' ')) + 0.35 + units(word.word) >
            capacity * 2) ||
        word.start - group[group.length - 1].end > 0.8)
    ) {
      groups.push([]);
      group = groups[groups.length - 1];
    }
    group.push(word);
  }
  return groups;
}

/** Match the export's phrase grouping and real word timing; never estimate timing. */
export function subtitleAt(
  segments: TranscriptSegment[],
  seconds: number,
  style: CaptionStyle,
  subtitleStyle: SubtitleStyle | null = null,
  outputWidth = 720,
): PreviewWord[] {
  if (style === 'none' || !Number.isFinite(seconds)) return [];
  const segment = segments.find(
    (item) => seconds >= item.start && seconds < item.end && item.text.trim(),
  );
  if (!segment) return [];
  let words: { text: string; highlighted: boolean; separator?: string }[];
  if (segment.words.length) {
    const group = wordGroups(
      segment.words,
      subtitleStyle ? styleCapacity(subtitleStyle, outputWidth) : undefined,
    ).find(
      (items) =>
        items.length &&
        seconds >= items[0].start &&
        seconds < Math.max(...items.map((word) => word.end)),
    );
    if (!group) return [];
    const active = group.findIndex((word) => seconds >= word.start && seconds < word.end);
    words = group.map((word, index) => ({
      text: word.word,
      highlighted: style === 'animated' ? index === active : emphasizedWord(word.word),
    }));
  } else if (subtitleStyle) {
    words = Array.from(segment.text.trim().matchAll(/(\s*)(\S+)/gu), ([, separator, text]) => ({
      text,
      separator,
      highlighted: style === 'standard' && emphasizedWord(text),
    }));
  } else {
    words = segment.text
      .trim()
      .split(/\s+/)
      .map((text) => ({ text, highlighted: style === 'standard' && emphasizedWord(text) }));
  }
  let length = 0;
  return words.map((word, index) => {
    const lineBreak = !subtitleStyle && index > 0 && length + word.text.length + 1 > 28;
    length = lineBreak ? word.text.length : length + word.text.length + (index ? 1 : 0);
    return { ...word, lineBreak };
  });
}

export function subtitleTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return '—';
  const centiseconds = Math.round(Math.max(0, seconds) * 100);
  const whole = Math.floor(centiseconds / 100);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}.${String(centiseconds % 100).padStart(2, '0')}`;
}

/** Add at the playhead, moving to the end of an occupied cue without overlapping. */
export function insertSubtitle(segments: TranscriptSegment[], seconds: number, duration: number) {
  let start = Math.max(0, Math.min(duration, Number.isFinite(seconds) ? seconds : 0));
  for (const cue of segments) {
    if (start >= cue.start && start < cue.end) start = cue.end;
  }
  const next = segments.findIndex((cue) => cue.start > start);
  const end = Math.min(duration, start + 3, next >= 0 ? segments[next].start : duration);
  if (end - start < 0.01)
    throw new Error(
      'No room for another subtitle here. Move the playhead or adjust the last subtitle.',
    );
  const index = next >= 0 ? next : segments.length;
  const result = [...segments];
  result.splice(index, 0, { start, end, text: '', words: [] });
  return { segments: result, index };
}
