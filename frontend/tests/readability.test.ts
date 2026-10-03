// @vitest-environment node
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { buildReadableParagraphs, sentenceSpans } from "../src/readability";

interface Source {
  text: string;
  language: string;
  speaker_id: string;
  start_sample: number;
  end_sample: number;
}
const fixtures = JSON.parse(
  readFileSync(
    new URL("../../tests/fixtures/readability.json", import.meta.url),
    "utf8",
  ),
) as {
  sentences: { text: string; pieces: string[] }[];
  paragraphs: { sources: Source[]; groups: string[][] }[];
};

it("matches the shared multilingual sentence examples without dropping text", () => {
  for (const example of fixtures.sentences) {
    const pieces = sentenceSpans(example.text).map(([start, end]) =>
      example.text.slice(start, end),
    );
    expect(pieces).toEqual(example.pieces);
    expect(pieces.join("")).toBe(example.text);
  }
});

it("matches the backend paragraph policy and keeps original source references", () => {
  for (const example of fixtures.paragraphs) {
    const sources = example.sources.map((source, index) => ({
      ...source,
      id: String(index),
    }));
    const paragraphs = buildReadableParagraphs(
      sources,
      (source) => source.text,
    );
    expect(paragraphs.map((parts) => parts.map((part) => part.text))).toEqual(
      example.groups,
    );
    for (const part of paragraphs.flat())
      expect(part.source).toBe(sources[Number(part.source.id)]);
  }
});

it("uses a soft length limit and preserves even a sentence longer than the target", () => {
  const sources = [
    {
      id: "a",
      language: "zh",
      start_sample: 0,
      end_sample: 0,
      text: "甲".repeat(160) + "。" + "乙".repeat(160) + "。",
    },
  ];
  expect(
    buildReadableParagraphs(sources, (source) => source.text),
  ).toHaveLength(2);
  expect(buildReadableParagraphs(sources, () => "甲".repeat(500))).toHaveLength(
    1,
  );
});

it("does not merge across hidden low-confidence filter gaps or manufacture pauses from invalid times", () => {
  const sources = [
    {
      id: "a",
      language: "en",
      start_sample: 0,
      end_sample: 16000,
      text: "One.",
      included: true,
    },
    {
      id: "b",
      language: "en",
      start_sample: 16000,
      end_sample: 32000,
      text: "Hidden.",
      included: false,
    },
    {
      id: "c",
      language: "en",
      start_sample: 32000,
      end_sample: 48000,
      text: "Three.",
      included: true,
    },
  ];
  expect(
    buildReadableParagraphs(
      sources,
      (source) => source.text,
      (source) => source.included,
    ),
  ).toHaveLength(2);
  const invalid = sources.map((source) => ({
    ...source,
    start_sample: 0,
    end_sample: 0,
    timing_quality: "invalid",
  }));
  expect(
    buildReadableParagraphs(invalid, (source) => source.text),
  ).toHaveLength(1);
});
it("balances a short tail while retaining English separators and hard boundaries", () => {
  const sources = Array.from({ length: 7 }, (_, index) => ({
    id: String(index),
    language: "en",
    speaker_id: "a",
    text: "Sentence.",
    start_sample: index * 16000,
    end_sample: (index + 1) * 16000,
  }));
  const paragraphs = buildReadableParagraphs(sources, (source) => source.text);
  expect(paragraphs.map((parts) => parts.length)).toEqual([4, 3]);
  expect(
    paragraphs.map((parts) =>
      parts.map((part) => part.separator + part.text).join(""),
    ),
  ).toEqual([
    "Sentence. Sentence. Sentence. Sentence.",
    "Sentence. Sentence. Sentence.",
  ]);
  const separated = sources.map((source, index) => ({
    ...source,
    speaker_id: index === 6 ? "b" : "a",
  }));
  expect(
    buildReadableParagraphs(separated, (source) => source.text).map(
      (parts) => parts.length,
    ),
  ).toEqual([6, 1]);
});
