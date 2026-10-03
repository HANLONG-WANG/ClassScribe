// Keep the text-only policy in sync with classscribe/readability.py and the shared fixtures.
export const paragraphCharacters = 300;
export const paragraphSentences = 6;
export const paragraphPauseMs = 1200;

const terminators = new Set("。！？.!?");
const closingMarks = new Set("\"'”’」』】）》〉]}");
const abbreviations = new Set(
  "mr mrs ms dr prof sr jr st vs etc e.g i.e no fig inc ltd approx dept a.m p.m".split(
    " ",
  ),
);

function periodBoundary(text: string, index: number): boolean {
  const previous = text[index - 1] ?? "";
  const following = text[index + 1] ?? "";
  if (/\p{Decimal_Number}/u.test(following)) return false;
  if (/[A-Za-z0-9]/.test(previous) && /[A-Za-z0-9]/.test(following))
    return false;
  let start = index;
  while (start > 0 && /[A-Za-z.]/.test(text[start - 1] ?? "")) start--;
  const word = text.slice(start, index + 1);
  if (
    abbreviations.has(word.slice(0, -1).toLowerCase()) ||
    /^(?:[A-Za-z]\.){2,}$/.test(word)
  )
    return false;
  return !(
    /^[A-Z]\.$/.test(word) && /^[A-Z]/.test(text.slice(index + 1).trimStart())
  );
}

export function sentenceSpans(text: string): [number, number][] {
  const spans: [number, number][] = [];
  let start = 0;
  let index = 0;
  while (index < text.length) {
    const blankLine =
      text[index] === "\n" ? /^\n[ \t\r]*\n/.exec(text.slice(index)) : null;
    const boundary =
      terminators.has(text[index] ?? "") &&
      (text[index] !== "." || periodBoundary(text, index));
    if (!boundary && !blankLine) {
      index++;
      continue;
    }
    let end = index + (blankLine ? blankLine[0].length : 1);
    if (boundary) {
      while (end < text.length && terminators.has(text[end] ?? "")) end++;
      while (end < text.length && closingMarks.has(text[end] ?? "")) end++;
    }
    while (end < text.length && /\s/u.test(text[end] ?? "")) end++;
    if (text.slice(start, end).trim()) {
      spans.push([start, end]);
      start = end;
    }
    index = end;
  }
  if (start < text.length || spans.length === 0)
    spans.push([start, text.length]);
  return spans;
}

interface ParagraphSource {
  id: string;
  language: string;
  speaker_id?: string | null;
  start_sample: number;
  end_sample: number;
  timing_quality?: string;
}

export interface ParagraphPart<T> {
  source: T;
  start: number;
  end: number;
  text: string;
  separator: string;
}

export function buildReadableParagraphs<T extends ParagraphSource>(
  sources: readonly T[],
  textFor: (source: T) => string,
  include: (source: T) => boolean = () => true,
): ParagraphPart<T>[][] {
  const paragraphs: ParagraphPart<T>[][] = [];
  const continuations = new Set<number>();
  let current: ParagraphPart<T>[] = [];
  let characters = 0;
  let previous: T | undefined;
  const flush = (nextSoft = false) => {
    if (current.length) {
      paragraphs.push(current);
      if (nextSoft) continuations.add(paragraphs.length);
    }
    current = [];
    characters = 0;
  };
  for (const source of sources) {
    if (!include(source)) {
      flush();
      previous = undefined;
      continue;
    }
    const text = textFor(source);
    const timed =
      source.end_sample > source.start_sample &&
      source.timing_quality !== "invalid";
    const previousTimed =
      previous &&
      previous.end_sample > previous.start_sample &&
      previous.timing_quality !== "invalid";
    const sourceBoundary =
      previous &&
      (source.speaker_id !== previous.speaker_id ||
        source.language !== previous.language ||
        (timed &&
          previousTimed &&
          (source.start_sample - previous.end_sample) / 16 >=
            paragraphPauseMs));
    for (const [start, end] of sentenceSpans(text)) {
      const piece = text.slice(start, end);
      const length = Array.from(piece.trim()).length;
      const last = current.at(-1);
      const hardBoundary =
        (start === 0 && sourceBoundary) ||
        (last && /\n[ \t\r]*\n\s*$/.test(last.text));
      const sizeBoundary =
        current.length >= paragraphSentences ||
        characters + length > paragraphCharacters;
      if (last && (hardBoundary || sizeBoundary)) flush(!hardBoundary);
      current.push({ source, start, end, text: piece, separator: "" });
      characters += length;
    }
    previous = source;
  }
  flush();
  for (const index of continuations) {
    const before = paragraphs[index - 1];
    const tail = paragraphs[index];
    if (!before || !tail || tail.length !== 1 || before.length < 4) continue;
    for (let count = Math.min(2, before.length - 3); count > 0; count--) {
      const balanced = before.slice(-count).concat(tail);
      const length = balanced.reduce(
        (total, part) => total + Array.from(part.text.trim()).length,
        0,
      );
      if (length <= paragraphCharacters) {
        paragraphs[index - 1] = before.slice(0, -count);
        paragraphs[index] = balanced;
        break;
      }
    }
  }
  return paragraphs.map((parts) =>
    parts.map((part, index) => {
      const before = parts[index - 1];
      const separator =
        before &&
        before.source.id !== part.source.id &&
        before.text &&
        part.text &&
        !/\s$/.test(before.text) &&
        !/^\s/.test(part.text) &&
        !["zh", "ja"].includes(part.source.language)
          ? " "
          : "";
      return { ...part, separator };
    }),
  );
}
