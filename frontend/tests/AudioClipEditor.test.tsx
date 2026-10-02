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
  const events: Record<string, (...args: number[]) => void> = {};
  const regionEvents: Record<
    string,
    (region: { start: number; end: number }) => void
  > = {};
  return {
    events,
    regionEvents,
    pause: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    setTime: vi.fn(),
    setOptions: vi.fn(),
  };
});
vi.mock("wavesurfer.js", () => ({
  default: {
    create: () => ({
      on: (name: string, fn: (...args: number[]) => void) => {
        state.events[name] = fn;
      },
      pause: state.pause,
      play: state.play,
      setTime: state.setTime,
      destroy: vi.fn(),
    }),
  },
}));
vi.mock("wavesurfer.js/dist/plugins/regions.esm.js", () => ({
  default: {
    create: () => ({
      on: (
        name: string,
        fn: (region: { start: number; end: number }) => void,
      ) => {
        state.regionEvents[name] = fn;
      },
      addRegion: vi.fn(),
      getRegions: () => [{ setOptions: state.setOptions }],
    }),
  },
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("keeps inputs and region in sync, validates range and stops preview", () => {
  const submit = vi.fn();
  render(
    <AudioClipEditor
      recording={{
        id: "recording",
        source_name: "a.wav",
        duration_samples: 160000,
        sample_rate: 16000,
        channels: 1,
        audio_qc: {},
        media_url: "/audio",
      }}
      onSubmit={submit}
    />,
  );
  act(() => {
    state.events.ready?.();
  });
  fireEvent.change(screen.getByLabelText("片段开始秒"), {
    target: { value: "1.234" },
  });
  expect(state.setOptions).toHaveBeenCalledWith({ start: 1.234, end: 10 });
  act(() => {
    state.regionEvents["region-updated"]?.({ start: 2, end: 4 });
  });
  expect(screen.getByLabelText<HTMLInputElement>("片段开始秒").value).toBe("2");
  fireEvent.click(screen.getByText("试听选段"));
  expect(state.setTime).toHaveBeenCalledWith(2);
  expect(state.play).toHaveBeenCalled();
  act(() => {
    state.events.timeupdate?.(4.01);
  });
  expect(state.pause).toHaveBeenCalled();
  fireEvent.click(screen.getByText("剪辑并转写"));
  expect(submit).toHaveBeenCalledWith({
    start_sample: 32000,
    end_sample: 64000,
  });
  fireEvent.change(screen.getByLabelText("片段结束秒"), {
    target: { value: "20" },
  });
  expect(screen.getByText<HTMLButtonElement>("剪辑并转写").disabled).toBe(true);
});
