import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ModelInfo } from "../src/api";
import { ModelPicker } from "../src/pages/ModelPicker";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const model: ModelInfo = {
  id: "qwen3_asr_1_7b",
  name: "Qwen3 ASR 1.7B",
  revision: "a".repeat(40),
  repository: "Qwen/model",
  languages: ["zh", "ja", "en"],
  tasks: ["asr"],
  modes: ["batch", "streaming"],
  capabilities: { hotwords: true, punctuation: true },
  enabled: true,
  experimental: false,
  manifest_available: true,
  manifest_sha256: null,
  estimated_download_bytes: 0,
  installed_size_bytes: 0,
  remote_code_file_count: 0,
  component_source_count: 0,
  component_sources: [],
  worker_implemented: true,
  installable: true,
  install_block_reason: null,
  estimated_vram_mb: 4096,
  installation: { state: "healthy" },
  benchmark: {},
};
it("opens model cards with capabilities and selects a model without submitting the form", () => {
  const onChange = vi.fn();
  const onSubmit = vi.fn();
  render(
    <form onSubmit={onSubmit}>
      <ModelPicker models={[model]} value="" onChange={onChange} />
    </form>,
  );
  const trigger = screen.getByRole("button", { name: "主模型 自动最佳" });
  fireEvent.click(trigger);
  const dialog = within(screen.getByRole("dialog", { name: "选择主模型" }));
  expect(dialog.getByText(/中日英统一正文识别/)).toBeVisible();
  expect(dialog.getByText(/热词／上下文/)).toBeVisible();
  expect(dialog.getByText("预计显存 4.0 GiB")).toBeVisible();
  expect(dialog.getByText("已安装")).toBeVisible();
  fireEvent.click(dialog.getByRole("button", { name: /Qwen3 ASR 1.7B/ }));
  expect(onChange).toHaveBeenCalledWith(model.id);
  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
it("focuses the selected card, handles Escape and outside clicks, and supports automatic selection", () => {
  const onChange = vi.fn();
  render(<ModelPicker models={[model]} value={model.id} onChange={onChange} />);
  const trigger = screen.getByRole("button", { name: "主模型 Qwen3 ASR 1.7B" });
  fireEvent.click(trigger);
  expect(
    screen.getByRole("button", { name: /Qwen3 ASR 1.7B.*已安装/ }),
  ).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(trigger);
  fireEvent.pointerDown(
    screen.getByRole("dialog").parentElement as HTMLElement,
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(trigger);
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: /自动最佳/,
    }),
  );
  expect(onChange).toHaveBeenCalledWith("");
});
it("keeps an empty list usable and confines Tab navigation to the open picker", () => {
  render(<ModelPicker models={[]} value="" onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "主模型 自动最佳" }));
  expect(screen.getByText(/当前没有已启用/)).toBeVisible();
  const close = screen.getByRole("button", { name: "关闭模型选单" });
  const automatic = within(screen.getByRole("dialog")).getByRole("button", {
    name: /自动最佳/,
  });
  automatic.focus();
  fireEvent.keyDown(automatic, { key: "Tab" });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(automatic).toHaveFocus();
});
