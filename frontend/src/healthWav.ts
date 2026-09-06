export interface HealthWavMetadata {
  durationSamples: number;
  channels: 1;
  sampleRate: 16000;
}

function fourCC(bytes: Uint8Array, offset: number) {
  return String.fromCharCode(
    bytes[offset] ?? 0,
    bytes[offset + 1] ?? 0,
    bytes[offset + 2] ?? 0,
    bytes[offset + 3] ?? 0,
  );
}

export function inspectHealthWavBytes(buffer: ArrayBuffer): HealthWavMetadata {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  if (
    bytes.byteLength < 44 ||
    fourCC(bytes, 0) !== "RIFF" ||
    fourCC(bytes, 8) !== "WAVE" ||
    view.getUint32(4, true) + 8 !== bytes.byteLength
  ) {
    throw new Error("健康录音必须是完整的 RIFF/WAVE 文件");
  }

  let offset = 12;
  let format: {
    audioFormat: number;
    channels: number;
    sampleRate: number;
    byteRate: number;
    blockAlign: number;
    bitsPerSample: number;
  } | null = null;
  let dataSize: number | null = null;
  while (offset < bytes.byteLength) {
    if (offset + 8 > bytes.byteLength) {
      throw new Error("健康 WAV 包含截断的 chunk header");
    }
    const id = fourCC(bytes, offset);
    const size = view.getUint32(offset + 4, true);
    const start = offset + 8;
    const end = start + size;
    if (end > bytes.byteLength) {
      throw new Error("健康 WAV 包含截断的 chunk");
    }
    if (id === "fmt ") {
      if (format !== null || size < 16) {
        throw new Error("健康 WAV 的 fmt chunk 无效");
      }
      format = {
        audioFormat: view.getUint16(start, true),
        channels: view.getUint16(start + 2, true),
        sampleRate: view.getUint32(start + 4, true),
        byteRate: view.getUint32(start + 8, true),
        blockAlign: view.getUint16(start + 12, true),
        bitsPerSample: view.getUint16(start + 14, true),
      };
    } else if (id === "data") {
      if (dataSize !== null) {
        throw new Error("健康 WAV 只能包含一个 data chunk");
      }
      dataSize = size;
    }
    offset = end + (size % 2);
  }
  if (offset !== bytes.byteLength || format === null || dataSize === null) {
    throw new Error("健康 WAV 缺少有效的 fmt 或 data chunk");
  }
  if (
    format.audioFormat !== 1 ||
    format.channels !== 1 ||
    format.sampleRate !== 16_000 ||
    format.bitsPerSample !== 16 ||
    format.blockAlign !== 2 ||
    format.byteRate !== 32_000
  ) {
    throw new Error("健康 WAV 必须是 16 kHz、mono、16-bit PCM");
  }
  if (dataSize === 0 || dataSize % format.blockAlign !== 0) {
    throw new Error("健康 WAV 必须包含非空且帧对齐的 PCM 数据");
  }
  const durationSamples = dataSize / format.blockAlign;
  if (durationSamples > 15 * format.sampleRate) {
    throw new Error("健康 WAV 不能超过 15 秒");
  }
  return { durationSamples, channels: 1, sampleRate: 16_000 };
}

export async function inspectHealthWav(file: File): Promise<HealthWavMetadata> {
  return inspectHealthWavBytes(await file.arrayBuffer());
}
