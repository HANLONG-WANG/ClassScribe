import { describe, expect, it } from "vitest";

import { inspectHealthWavBytes } from "../src/healthWav";

function writeFourCC(bytes: Uint8Array, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) {
    bytes[offset + index] = value.charCodeAt(index);
  }
}

function wav({
  frames = 1_600,
  sampleRate = 16_000,
  channels = 1,
  bitsPerSample = 16,
  audioFormat = 1,
}: {
  frames?: number;
  sampleRate?: number;
  channels?: number;
  bitsPerSample?: number;
  audioFormat?: number;
} = {}) {
  const blockAlign = (channels * bitsPerSample) / 8;
  const dataSize = frames * blockAlign;
  const buffer = new ArrayBuffer(44 + dataSize);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  writeFourCC(bytes, 0, "RIFF");
  view.setUint32(4, buffer.byteLength - 8, true);
  writeFourCC(bytes, 8, "WAVE");
  writeFourCC(bytes, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, audioFormat, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeFourCC(bytes, 36, "data");
  view.setUint32(40, dataSize, true);
  return buffer;
}

describe("health WAV inspection", () => {
  it("accepts a non-empty 16 kHz mono 16-bit PCM WAV of at most 15 seconds", () => {
    expect(inspectHealthWavBytes(wav())).toEqual({
      durationSamples: 1_600,
      channels: 1,
      sampleRate: 16_000,
    });
    expect(
      inspectHealthWavBytes(wav({ frames: 15 * 16_000 })).durationSamples,
    ).toBe(15 * 16_000);
  });

  it.each([
    ["empty", { frames: 0 }, "非空"],
    ["wrong rate", { sampleRate: 48_000 }, "16 kHz"],
    ["stereo", { channels: 2 }, "mono"],
    ["wrong width", { bitsPerSample: 8 }, "16-bit"],
    ["compressed", { audioFormat: 3 }, "PCM"],
    ["too long", { frames: 15 * 16_000 + 1 }, "15 秒"],
  ])("rejects %s health audio", (_name, options, message) => {
    expect(() => inspectHealthWavBytes(wav(options))).toThrow(message);
  });

  it("rejects truncated RIFF bytes", () => {
    const truncated = wav().slice(0, 43);
    expect(() => inspectHealthWavBytes(truncated)).toThrow("RIFF/WAVE");
  });
});
