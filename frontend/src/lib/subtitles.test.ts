import { describe, expect, it } from 'vitest';
import { emphasizedWord, insertSubtitle, subtitleAt, subtitleTime } from './subtitles';
import type { TranscriptSegment } from '../types/api';

const transcript: TranscriptSegment[] = [
  {
    start: 0,
    end: 2,
    text: 'Real wow',
    words: [
      { start: 0.25, end: 0.7, word: 'Real' },
      { start: 0.9, end: 1.5, word: 'wow' },
    ],
  },
];

describe('subtitle preview timing', () => {
  it('highlights the actual active word, not an estimated share of a segment', () => {
    expect(subtitleAt(transcript, 0.4, 'animated').map((word) => word.highlighted)).toEqual([
      true,
      false,
    ]);
    expect(subtitleAt(transcript, 1, 'animated').map((word) => word.highlighted)).toEqual([
      false,
      true,
    ]);
    expect(subtitleAt(transcript, 0.8, 'animated').map((word) => word.highlighted)).toEqual([
      false,
      false,
    ]);
    expect(subtitleAt(transcript, 0.1, 'animated')).toEqual([]);
    expect(subtitleAt(transcript, 1.6, 'animated')).toEqual([]);
  });
  it('uses attention-word styling for standard captions and hides No captions', () => {
    expect(subtitleAt(transcript, 0.4, 'standard').map((word) => word.highlighted)).toEqual([
      false,
      true,
    ]);
    expect(subtitleAt(transcript, 0.4, 'none')).toEqual([]);
    expect(subtitleAt(transcript, NaN, 'standard')).toEqual([]);
  });
  it('never invents word timing for manually edited text', () => {
    const manual = [{ start: 0, end: 2, text: 'NO guessed timing', words: [] }];
    expect(subtitleAt(manual, 0.4, 'animated').map((word) => word.highlighted)).toEqual([
      false,
      false,
      false,
    ]);
    expect(subtitleAt(manual, 0.4, 'standard')[0].highlighted).toBe(true);
  });
  it('groups words and breaks lines using the export limits', () => {
    const words = Array.from({ length: 11 }, (_, i) => ({
      start: i / 10,
      end: i / 10 + 0.08,
      word: `w${i}`,
    }));
    const grouped = [{ start: 0, end: 2, text: words.map((w) => w.word).join(' '), words }];
    expect(subtitleAt(grouped, 1.02, 'animated').map((word) => word.text)).toEqual(['w10']);
    const long = [{ start: 0, end: 2, text: '1234567890123456789012345678 next', words: [] }];
    expect(subtitleAt(long, 0.5, 'standard')[1].lineBreak).toBe(true);
    expect(emphasizedWord('WOW!')).toBe(true);
    expect(emphasizedWord('СТОП')).toBe(true);
    expect(emphasizedWord('123')).toBe(false);
  });
  it('keeps original transcripts untouched', () => {
    const before = JSON.stringify(transcript);
    subtitleAt(transcript, 0.4, 'animated');
    expect(JSON.stringify(transcript)).toBe(before);
  });
  it('formats clip-relative centiseconds with correct carry', () => {
    expect(subtitleTime(1.25)).toBe('0:01.25');
    expect(subtitleTime(59.999)).toBe('1:00.00');
    expect(subtitleTime(NaN)).toBe('—');
  });
});

describe('add subtitle at playhead', () => {
  const existing = [
    { start: 0, end: 2, text: 'first', words: [] },
    { start: 6, end: 8, text: 'second', words: [] },
  ];
  it('inserts into a gap without overlapping the next cue', () => {
    const result = insertSubtitle(existing, 4, 10);
    expect(result.index).toBe(1);
    expect(result.segments[1]).toEqual({ start: 4, end: 6, text: '', words: [] });
    expect(existing).toHaveLength(2);
  });
  it('moves to the end of the current cue and rejects a full clip', () => {
    expect(insertSubtitle(existing, 1, 10).segments[1].start).toBe(2);
    expect(() => insertSubtitle(existing, 10, 10)).toThrow('No room');
  });
});
