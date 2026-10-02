import { expect, it } from "vitest";
import { formatClipTime, parseClipTime } from "../src/audioClipTime";

it("accepts seconds, minute and hour timecodes and rejects malformed values", () => {
  expect(parseClipTime(" 1.234 ")).toBe(1.234);
  expect(parseClipTime("12:34.500")).toBe(754.5);
  expect(parseClipTime("01:12:34.500")).toBe(4354.5);
  expect(parseClipTime("72:34")).toBe(4354);
  for (const value of [
    "",
    "1.",
    "-1",
    "NaN",
    "Infinity",
    "1e2",
    "00:60",
    "00:60:01",
    "1:2:3:4",
    ":12",
    "1.2345",
  ])
    expect(parseClipTime(value)).toBeNull();
});
it("formats hour boundaries with millisecond precision", () => {
  expect(formatClipTime(0)).toBe("00:00:00.000");
  expect(formatClipTime(1.234)).toBe("00:00:01.234");
  expect(formatClipTime(3599.9999)).toBe("01:00:00.000");
});
