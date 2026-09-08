import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ModelInfo } from "../src/api";
import { ClassroomModelGuide } from "../src/pages/ClassroomModelGuide";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const model = (
  id: string,
  installed: boolean,
  tasks = ["asr"],
  modes = ["batch"],
): ModelInfo => ({
  id,
  name: id,
  revision: "a".repeat(40),
  repository: "owner/model",
  languages: ["zh", "ja", "en"],
  tasks,
  modes,
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
  estimated_vram_mb: 0,
  installation: { state: installed ? "healthy" : "not_installed" },
  benchmark: {},
});
const models = [
  model("missing", false),
  model("local-best", true),
  model("backup", true),
  model("structure", true, ["asr", "diarization", "timestamps"]),
  model("stream-only", true, ["asr"], ["streaming"]),
];
function view(strict: boolean, language = "ja", primaryModel = "") {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            ["zh", "ja", "en"].map((language) => ({
              language,
              scenario: "classroom",
              models: ["missing"],
              effective_models: [
                "missing",
                "structure",
                "stream-only",
                "local-best",
                "backup",
              ],
              bootstrap_models: ["backup", "local-best"],
            })),
          ),
        ),
      ),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ClassroomModelGuide
        models={models}
        language={language}
        primaryModel={primaryModel}
        strict={strict}
      />
    </QueryClientProvider>,
  );
}
it("uses installed compatible candidates from the effective ranking", async () => {
  view(false);
  await screen.findByText("local-best（已安装）", { selector: "p" });
  const route = within(screen.getByRole("article"));
  expect(route.getByText(/预计主模型/).parentElement).toHaveTextContent(
    "local-best（已安装）",
  );
  expect(route.getByText(/按需复核/).parentElement).toHaveTextContent(
    "backup（已安装）",
  );
  expect(route.queryByText(/structure|stream-only/)).not.toBeInTheDocument();
  expect(route.getByText(/missing（尚未就绪）/)).toBeInTheDocument();
});
it("keeps auxiliary models explicit in strict mode and routes automatic language per language", async () => {
  view(true, "auto_mixed", "local-best");
  expect(screen.getAllByRole("article")).toHaveLength(3);
  expect(await screen.findAllByText(/关闭；仅使用指定正文模型/)).toHaveLength(
    3,
  );
  expect(screen.getByText(/两者为当前课堂流程必需/)).toHaveTextContent(
    "FireRedVAD",
  );
  expect(screen.getByText(/此语言模式必需/)).toHaveTextContent("FireRedLID");
  expect(screen.getByText(/不表示整条流水线只加载一个模型/)).toBeVisible();
});
it("uses bootstrap review candidates after manually choosing a primary model", async () => {
  view(false, "ja", "local-best");
  await screen.findByText("backup（已安装）", { selector: "p" });
  expect(screen.getByText(/按需复核/).parentElement).toHaveTextContent(
    "backup（已安装）",
  );
  expect(screen.queryByText(/missing（尚未就绪）/)).not.toBeInTheDocument();
});
