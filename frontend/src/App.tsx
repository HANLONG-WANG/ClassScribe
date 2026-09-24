import { useQueryClient } from "@tanstack/react-query";
import {
  discardTranscriptDraft,
  flushTranscriptSave,
  loadLatestTranscriptDraft,
  saveRebasedTranscriptDraft,
  useTranscriptSaves,
} from "./transcriptSaves";
import { ExportsPage } from "./pages/ExportsPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { IBusPage } from "./pages/IBusPage";
import { QueuePage } from "./pages/QueuePage";
import { JobPage } from "./pages/JobPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TranscriptPage } from "./pages/TranscriptPage";
import { TranscriptsPage } from "./pages/TranscriptsPage";
import { UploadPage } from "./pages/UploadPage";
import { type Page, useWorkbench } from "./store";
import { useAuthStatus } from "./authStatus";

const navigation: { id: Page; label: string; mark: string }[] = [
  { id: "upload", label: "导入", mark: "↑" },
  { id: "queue", label: "任务队列", mark: "◌" },
  { id: "transcripts", label: "转录稿", mark: "≡" },
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
  const authInvalid = useAuthStatus((state) => state.invalid);
  const setPage = useWorkbench((state) => state.setPage);
  const selectSegment = useWorkbench((state) => state.selectSegment);
  function navigate(next: Page) {
    setPage(next);
    window.scrollTo(0, 0);
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => {
            navigate("upload");
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
              aria-label={item.label}
              title={item.label}
              aria-current={
                page === item.id ||
                (page === "job" && item.id === "queue") ||
                (page === "transcript" && item.id === "transcripts")
                  ? "page"
                  : undefined
              }
              key={item.id}
              onClick={() => {
                navigate(item.id);
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
        {authInvalid && (
          <div className="error-callout" role="alert">
            本机 API 鉴权失败，当前数据不可读取。请在设置中重新应用正确的 API
            token。
            <button
              type="button"
              onClick={() => {
                navigate("settings");
              }}
            >
              前往设置
            </button>
          </div>
        )}
        {unsaved.length > 0 && (
          <div role="status">
            {unsaved.some(([, draft]) => draft.status === "error")
              ? "文本保存失败，草稿已保留"
              : "文本待保存或保存中…"}
            {unsaved
              .filter(([, draft]) => draft.status === "error")
              .map(([id, draft]) => (
                <span key={id}>
                  {draft.error && <span>{draft.error}</span>}
                  <button
                    type="button"
                    onClick={() => {
                      selectSegment(id);
                      navigate("transcript");
                    }}
                  >
                    打开草稿
                  </button>
                  {draft.conflict ? (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          void loadLatestTranscriptDraft(id);
                        }}
                      >
                        读取服务器最新版
                      </button>
                      {draft.serverVersion !== undefined && (
                        <button
                          type="button"
                          onClick={() => {
                            void saveRebasedTranscriptDraft(id, client);
                          }}
                        >
                          确认用草稿保存
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          discardTranscriptDraft(id, client);
                        }}
                      >
                        放弃草稿
                      </button>
                    </>
                  ) : (
                    <button
                      title={draft.error}
                      onClick={() => {
                        void flushTranscriptSave(id, client);
                      }}
                      type="button"
                    >
                      重试保存
                    </button>
                  )}
                </span>
              ))}
          </div>
        )}
        {(!authInvalid || page === "settings") && <PageContent page={page} />}
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
  if (page === "queue") return <QueuePage />;
  if (page === "transcript") return <TranscriptPage />;
  if (page === "transcripts") return <TranscriptsPage />;
  if (page === "models") return <ModelsPage />;
  if (page === "glossary") return <GlossaryPage />;
  if (page === "exports") return <ExportsPage />;
  if (page === "ibus") return <IBusPage />;
  return <SettingsPage />;
}
