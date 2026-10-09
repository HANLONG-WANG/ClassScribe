import { expect, it } from "vitest";
import { groupManuscripts } from "../src/manuscriptGroups";
import type { Manuscript } from "../src/manuscripts";

function item(id: string, date: Date): Manuscript {
  return {
    job_id: id,
    source_name: `${id}.wav`,
    duration_samples: 16000,
    status: "completed",
    language: "ja",
    created_at: date.toISOString(),
  };
}

it("groups local calendar dates and keeps multiple recordings together", () => {
  const now = new Date(2026, 9, 9, 0, 5);
  const items = [
    item("older", new Date(2026, 8, 8, 12)),
    item("yesterday", new Date(2026, 9, 8, 23, 50)),
    item("today-1", new Date(2026, 9, 9, 0, 1)),
    item("recent", new Date(2026, 9, 5, 12)),
    item("today-2", new Date(2026, 9, 9, 0, 2)),
  ];
  const groups = groupManuscripts(items, now);
  expect(
    groups.map((group) => group.items.map((record) => record.job_id)),
  ).toEqual([["today-1", "today-2"], ["yesterday"], ["recent"], ["older"]]);
  expect(groups.map((group) => group.older)).toEqual([
    false,
    false,
    false,
    true,
  ]);
  expect(groups[0]?.label).toBe("今天");
  expect(groups[1]?.label).toBe("昨天");
  expect(groups[2]?.label).toContain("10月5日");
  expect(groups[3]?.label).toContain("2026年9月8日");
});

it("handles year boundaries and malformed dates without hiding records", () => {
  const records = [
    item("previous-year", new Date(2026, 11, 31, 23, 55)),
    { ...item("unknown", new Date()), created_at: "invalid" },
  ];
  const groups = groupManuscripts(records, new Date(2027, 0, 1, 0, 5));
  expect(groups[0]?.label).toBe("昨天");
  expect(groups[1]?.label).toBe("日期未知");
  expect(groups[1]?.items).toHaveLength(1);
  expect(groupManuscripts([])).toEqual([]);
});
