import { useQueryClient } from "@tanstack/react-query";
import { flushTranscriptSave, useTranscriptSaves } from "./transcriptSaves";
import { ExportsPage } from "./pages/ExportsPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { IBusPage } from "./pages/IBusPage";
import { JobPage } from "./pages/JobPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TranscriptPage } from "./pages/TranscriptPage";
import { UploadPage } from "./pages/UploadPage";
import { type Page, useWorkbench } from "./store";

const navigation: { id: Page; label: string; mark: string }[] = [
  { id: "upload", label: "导入", mark: "↑" },
  { id: "job", label: "任务", mark: "◌" },
  { id: "transcript", label: "转录稿", mark: "≡" },
  { id: "exports", label: "导出", mark: "↓" },
  { id: "glossary", label: "词典", mark: "Aa" },
  { id: "models", label: "模型", mark: "◇" },
  { id: "ibus", label: "IBus", mark: "●" },
  { id: "settings", label: "设置", mark: "⚙" },
];

export function App() {
  const client = useQueryClient();
  const drafts = useTranscriptSaves((state) => state.drafts);
  const unsaved = Object.entries(drafts).filter(
    ([, draft]) => draft.status !== "saved",
  );
  const page = useWorkbench((state) => state.page);
  const setPage = useWorkbench((state) => state.setPage);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => {
            setPage("upload");
          }}
          type="button"
        >
          <span>CS</span>
          <div>
            <strong>ClassScribe</strong>
            <small>local speech workspace</small>
          </div>
        </button>
        <nav aria-label="主导航">
          {navigation.map((item) => (
            <button
              aria-current={page === item.id ? "page" : undefined}
              key={item.id}
              onClick={() => {
                setPage(item.id);
              }}
              type="button"
            >
              <i>{item.mark}</i>
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="local-state">
          <span />
          <div>
            <strong>本机模式</strong>
            <small>无遥测 · 无上传</small>
          </div>
        </div>
      </aside>
      <main className="workspace">
        {unsaved.length > 0 && (
          <div role="status">
            {unsaved.some(([, draft]) => draft.status === "error")
              ? "文本保存失败，草稿已保留"
              : "文本待保存或保存中…"}
            {unsaved
              .filter(([, draft]) => draft.status === "error")
              .map(([id, draft]) => (
                <button
                  key={id}
                  title={draft.error}
                  onClick={() => {
                    void flushTranscriptSave(id, client);
                  }}
                  type="button"
                >
                  重试保存
                </button>
              ))}
          </div>
        )}
        <PageContent page={page} />
        <footer>
          ClassScribe 不承诺零 CER/WER；低置信度会标记，但不会阻塞自动导出。
        </footer>
      </main>
    </div>
  );
}

function PageContent({ page }: { page: Page }) {
  if (page === "upload") return <UploadPage />;
  if (page === "job") return <JobPage />;
  if (page === "transcript") return <TranscriptPage />;
  if (page === "models") return <ModelsPage />;
  if (page === "glossary") return <GlossaryPage />;
  if (page === "exports") return <ExportsPage />;
  if (page === "ibus") return <IBusPage />;
  return <SettingsPage />;
}
