import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AudioClipEditor } from "../src/components/AudioClipEditor";

const state = vi.hoisted(() => {
  const events: Record<string, (value?: number) => void> = {};
  type RegionState = {
    start: number;
    end: number;
    setOptions: ReturnType<typeof vi.fn>;
    remove: ReturnType<typeof vi.fn>;
  };
  const regionEvents: Record<string, (region: RegionState) => void> = {};
  let time = 0;
  let playing = false;
  const wave = {
    on: (name: string, fn: (value?: number) => void) => {
      events[name] = fn;
    },
    pause: vi.fn(() => {
      playing = false;
      events.pause?.();
    }),
    play: vi.fn(() => {
      playing = true;
      events.play?.();
      return Promise.resolve();
    }),
    setTime: vi.fn((value: number) => {
      time = value;
      events.timeupdate?.(value);
    }),
    getCurrentTime: () => time,
    isPlaying: () => playing,
    getWidth: () => 600,
    getScroll: () => 0,
    getDecodedData: () => ({}),
    zoom: vi.fn(),
    setScrollTime: vi.fn(),
    setPlaybackRate: vi.fn(),
    setVolume: vi.fn(),
    destroy: vi.fn(),
  };
  const region: RegionState = {
    start: 0,
    end: 10,
    setOptions: vi.fn(),
    remove: vi.fn(),
  };
  return { events, regionEvents, wave, region };
});
vi.mock("wavesurfer.js", () => ({ default: { create: () => state.wave } }));
vi.mock("wavesurfer.js/dist/plugins/regions.esm.js", () => ({
  default: {
    create: () => ({
      on: (name: string, fn: (region: typeof state.region) => void) => {
        state.regionEvents[name] = fn;
      },
      addRegion: (options: { start: number; end: number }) => {
        Object.assign(state.region, options);
        state.regionEvents["region-created"]?.(state.region);
        return state.region;
      },
      enableDragSelection: () => vi.fn(),
    }),
  },
}));
vi.mock("wavesurfer.js/dist/plugins/timeline.esm.js", () => ({
  default: { create: vi.fn() },
}));
const recording = {
  id: "recording",
  source_name: "a.wav",
  duration_samples: 160000,
  sample_rate: 16000,
  channels: 1,
  audio_qc: {},
  media_url: "/audio",
};
function setup(duration_samples = recording.duration_samples) {
  const submit = vi.fn();
  const view = render(
    <AudioClipEditor
      recording={{ ...recording, duration_samples }}
      onSubmit={submit}
    />,
  );
  act(() => {
    state.events.ready?.();
  });
  return { submit, ...view };
}
afterEach(() => {
  cleanup();
  state.region.start = 0;
  state.region.end = 10;
  vi.clearAllMocks();
});

it("synchronizes formatted inputs and dragged boundaries and validates submission", () => {
  const { submit } = setup();
  fireEvent.change(screen.getByLabelText("片段开始时间"), {
    target: { value: "1.234" },
  });
  expect(state.region.setOptions).toHaveBeenCalledWith({
    start: 1.234,
    end: 10,
  });
  act(() => {
    Object.assign(state.region, { start: 2, end: 4 });
    state.regionEvents["region-updated"]?.(state.region);
  });
  expect(screen.getByLabelText<HTMLInputElement>("片段开始时间").value).toBe(
    "00:00:02.000",
  );
  fireEvent.click(screen.getByText("转写选段"));
  expect(submit).toHaveBeenCalledWith({
    start_sample: 32000,
    end_sample: 64000,
  });
  for (const value of ["", "bad", "00:61.000", "20", "2"]) {
    fireEvent.change(screen.getByLabelText("片段结束时间"), {
      target: { value },
    });
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "转写选段" })
        .disabled,
    ).toBe(true);
  }
  fireEvent.click(screen.getByText("全选"));
  expect(screen.getByLabelText<HTMLInputElement>("片段结束时间").value).toBe(
    "00:00:10.000",
  );
  fireEvent.click(screen.getByText("转写整段"));
  expect(submit).toHaveBeenLastCalledWith();
});

it("keeps editing drafts and applies minute timecodes on Enter", () => {
  setup();
  const input = screen.getByLabelText<HTMLInputElement>("片段开始时间");
  fireEvent.change(input, { target: { value: "1." } });
  expect(input.value).toBe("1.");
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "试听选段" })
      .disabled,
  ).toBe(true);
  fireEvent.change(input, { target: { value: "0:01.234" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(input.value).toBe("00:00:01.234");
  expect(screen.getByText(/时长 00:00:08.766/)).toBeTruthy();
});

it("pauses and resumes a preview and stops at B", () => {
  setup();
  fireEvent.change(screen.getByLabelText("片段开始时间"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("片段结束时间"), {
    target: { value: "4" },
  });
  fireEvent.click(screen.getByText("试听选段"));
  expect(state.wave.getCurrentTime()).toBe(2);
  act(() => {
    state.wave.setTime(3);
  });
  fireEvent.click(screen.getByRole("button", { name: "暂停" }));
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  expect(state.wave.getCurrentTime()).toBe(3);
  act(() => {
    state.events.timeupdate?.(4.01);
  });
  expect(state.wave.isPlaying()).toBe(false);
  expect(state.wave.getCurrentTime()).toBe(4);
});

it("loops at the recording end and clears preview mode on waveform seeking", () => {
  setup();
  fireEvent.click(screen.getByLabelText("循环"));
  fireEvent.click(screen.getByText("试听选段"));
  act(() => {
    state.wave.pause();
    state.events.finish?.();
  });
  expect(state.wave.getCurrentTime()).toBe(0);
  expect(state.wave.isPlaying()).toBe(true);
  fireEvent.change(screen.getByLabelText("片段结束时间"), {
    target: { value: "4" },
  });
  fireEvent.click(screen.getByText("试听选段"));
  act(() => {
    state.events.timeupdate?.(4.01);
  });
  expect(state.wave.getCurrentTime()).toBe(0);
  act(() => {
    state.events.interaction?.();
    state.wave.setTime(7);
  });
  expect(state.wave.getCurrentTime()).toBe(7);
  expect(state.wave.isPlaying()).toBe(true);
});

it("marks the cursor with scoped shortcuts and ignores input and button Space", () => {
  setup();
  const editor = screen.getByRole("group", { name: "音频范围编辑器" });
  act(() => {
    state.wave.setTime(2);
  });
  fireEvent.keyDown(editor, { key: "i" });
  expect(screen.getByLabelText<HTMLInputElement>("片段开始时间").value).toBe(
    "00:00:02.000",
  );
  act(() => {
    state.wave.setTime(4);
  });
  fireEvent.click(
    screen.getByRole("button", { name: "将当前播放位置设为 B 终点" }),
  );
  expect(screen.getByLabelText<HTMLInputElement>("片段结束时间").value).toBe(
    "00:00:04.000",
  );
  fireEvent.keyDown(screen.getByLabelText("片段开始时间"), { key: " " });
  fireEvent.keyDown(screen.getByRole("button", { name: "播放" }), {
    key: " ",
  });
  expect(state.wave.play).not.toHaveBeenCalled();
  fireEvent.keyDown(editor, { key: " " });
  expect(state.wave.isPlaying()).toBe(true);
  fireEvent.keyDown(editor, { key: "ArrowRight" });
  expect(state.wave.getCurrentTime()).toBe(9);
  fireEvent.keyDown(editor, { key: "o" });
  expect(screen.getByLabelText<HTMLInputElement>("片段结束时间").value).toBe(
    "00:00:09.000",
  );
});

it("zooms independently of range, speed and volume", () => {
  setup();
  fireEvent.change(screen.getByLabelText("片段开始时间"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("片段结束时间"), {
    target: { value: "4" },
  });
  fireEvent.click(screen.getByText("放大选段"));
  expect(state.wave.zoom).toHaveBeenCalledWith(600 / 2.3);
  expect(state.wave.setScrollTime.mock.lastCall?.[0]).toBeCloseTo(1.85);
  fireEvent.click(screen.getByText("全貌"));
  expect(state.wave.zoom).toHaveBeenLastCalledWith(0);
  fireEvent.click(screen.getByRole("button", { name: "放大波形" }));
  expect(state.wave.zoom).toHaveBeenLastCalledWith(96);
  fireEvent.change(screen.getByLabelText("试听倍速"), {
    target: { value: "1.5" },
  });
  fireEvent.change(screen.getByLabelText("试听音量"), {
    target: { value: "0.5" },
  });
  expect(state.wave.setPlaybackRate).toHaveBeenCalledWith(1.5, true);
  expect(state.wave.setVolume).toHaveBeenLastCalledWith(0.5);
  expect(screen.getByLabelText<HTMLInputElement>("片段开始时间").value).toBe(
    "2",
  );
});

it("reports media errors and retains time-based submission", async () => {
  const { submit } = setup();
  state.wave.play.mockRejectedValueOnce(new Error("blocked"));
  await act(async () => {
    fireEvent.click(screen.getByText("试听选段"));
    await Promise.resolve();
  });
  expect(screen.getByRole("alert").textContent).toContain("无法播放");
  act(() => {
    state.events.error?.();
  });
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "播放" }).disabled,
  ).toBe(true);
  fireEvent.change(screen.getByLabelText("片段开始时间"), {
    target: { value: "1" },
  });
  fireEvent.click(screen.getByText("转写选段"));
  expect(submit).toHaveBeenCalledWith({
    start_sample: 16000,
    end_sample: 160000,
  });
});

it("preserves the full sample boundary when duration rounds up to a millisecond", () => {
  const { submit } = setup(160009);
  fireEvent.click(screen.getByText("转写选段"));
  expect(submit).toHaveBeenCalledWith({ start_sample: 0, end_sample: 160009 });
});
