import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError, formatSamples } from "../api";
import { groupManuscripts, type ManuscriptGroup } from "../manuscriptGroups";
import { useWorkbench } from "../store";

import { type Manuscript, statuses } from "../manuscripts";

interface DeletionFailure {
  item: Manuscript;
  message: string;
}

function canDelete(item: Manuscript) {
  return ["completed", "failed", "cancelled"].includes(item.status);
}

function HistoryCheckbox({
  label,
  checked,
  partial = false,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  partial?: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <input
      type="checkbox"
      className="history-checkbox"
      aria-label={label}
      checked={checked}
      disabled={disabled}
      onChange={onChange}
      ref={(node) => {
        if (node) node.indeterminate = partial;
      }}
    />
  );
}

export function TranscriptsPage() {
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Record<string, Manuscript>>({});
  const [deletionNotice, setDeletionNotice] = useState("");
  const [deletionFailures, setDeletionFailures] = useState<DeletionFailure[]>(
    [],
  );
  const [deletionProgress, setDeletionProgress] = useState({
    completed: 0,
    total: 0,
  });
  const setCurrentJob = useWorkbench((state) => state.setCurrentJob);
  const setPage = useWorkbench((state) => state.setPage);
  const query = useQuery({
    queryKey: ["manuscripts", offset],
    queryFn: ({ signal }) =>
      api<{ items: Manuscript[]; total: number }>(
        `/jobs?limit=30&offset=${String(offset)}`,
        { signal },
      ),
  });
  useEffect(() => {
    if (query.data && offset > 0 && offset >= query.data.total) {
      setOffset(Math.max(0, Math.floor((query.data.total - 1) / 30) * 30));
    }
  }, [query.data, offset]);

  const deleteDerived = useMutation({
    mutationFn: (id: string) =>
      api(`/jobs/${id}/derived-data`, { method: "DELETE" }),
    onSuccess: () => {
      setDeletionNotice("中间派生数据已清理，稿件与最终导出保留。");
    },
  });
  const deleteJobs = useMutation({
    mutationFn: async (items: Manuscript[]) => {
      const deleted: string[] = [];
      const failures: DeletionFailure[] = [];
      setDeletionProgress({ completed: 0, total: items.length });
      for (const [index, item] of items.entries()) {
        try {
          await api(`/jobs/${item.job_id}/local-data`, { method: "DELETE" });
          deleted.push(item.job_id);
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            deleted.push(item.job_id);
          } else {
            failures.push({
              item,
              message: error instanceof Error ? error.message : "删除失败",
            });
          }
        }
        setDeletionProgress({ completed: index + 1, total: items.length });
      }
      return { deleted, failures };
    },
    onSuccess: async ({ deleted, failures }) => {
      setSelected((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([id]) => !deleted.includes(id)),
        ),
      );
      setDeletionFailures(failures);
      setDeletionNotice(
        deleted.length
          ? `已删除 ${String(deleted.length)} 个任务及不再被引用的录音。`
          : "本次没有删除任务。",
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: ["manuscripts"] }),
        client.invalidateQueries({ queryKey: ["queue"] }),
      ]);
    },
  });
  const busy = deleteJobs.isPending || deleteDerived.isPending;
  const groups = groupManuscripts(query.data?.items ?? []);
  const selectedItems = Object.values(selected);
  const eligible = (query.data?.items ?? []).filter(canDelete);
  const pageSelected = eligible.filter((item) => selected[item.job_id]).length;

  function toggleItems(items: Manuscript[]) {
    const available = items.filter(canDelete);
    setSelected((current) => {
      const next = new Map(Object.entries(current));
      const remove = available.every((item) => current[item.job_id]);
      for (const item of available) {
        if (remove) next.delete(item.job_id);
        else next.set(item.job_id, item);
      }
      return Object.fromEntries(next);
    });
  }
  function confirmDeletion(items: Manuscript[]) {
    if (!items.length || busy) return;
    const names = items
      .slice(0, 5)
      .map((item) => `「${item.source_name}」`)
      .join("、");
    if (
      window.confirm(
        `永久删除所选 ${String(items.length)} 个任务、稿件、导出及不再被引用的录音副本？\n${names}${items.length > 5 ? "等" : ""}\n最初选择上传的原文件会保留。`,
      )
    ) {
      setDeletionNotice("");
      setDeletionFailures([]);
      deleteJobs.mutate(items);
    }
  }
  function renderGroup(group: ManuscriptGroup) {
    const available = group.items.filter(canDelete);
    const count = available.filter((item) => selected[item.job_id]).length;
    const headingId = `history-date-${group.key}`;
    const DateHeading = group.older ? "h3" : "h2";
    return (
      <section
        className="history-date-group"
        aria-labelledby={headingId}
        key={group.key}
      >
        <div className="history-date-heading">
          <DateHeading id={headingId}>
            {group.label}{" "}
            <span className="muted">· 本页 {group.items.length} 份</span>
          </DateHeading>
          <label className="history-select">
            <HistoryCheckbox
              label={`选择${group.label}的全部可删除任务`}
              checked={available.length > 0 && count === available.length}
              partial={count > 0 && count < available.length}
              disabled={busy || !available.length}
              onChange={() => {
                toggleItems(group.items);
              }}
            />
            选择本日
          </label>
        </div>
        <div className="manuscript-list">
          {group.items.map((item) => (
            <div
              className={`manuscript-entry ${selected[item.job_id] ? "is-selected" : ""}`}
              key={item.job_id}
            >
              <div className="manuscript-selection-row">
                <HistoryCheckbox
                  label={`选择 ${item.source_name}`}
                  checked={Boolean(selected[item.job_id])}
                  disabled={busy || !canDelete(item)}
                  onChange={() => {
                    toggleItems([item]);
                  }}
                />
                <button
                  className="panel manuscript-card"
                  type="button"
                  onClick={() => {
                    setCurrentJob(item.job_id);
                    setPage("transcript");
                  }}
                >
                  <strong>{item.source_name}</strong>
                  <span>
                    {new Date(item.created_at).toLocaleString()} ·{" "}
                    {formatSamples(item.duration_samples)} ·{" "}
                    {(
                      { ja: "日语", zh: "中文", en: "英语" } as Record<
                        string,
                        string
                      >
                    )[item.language] ?? item.language}
                  </span>
                  <span>
                    {statuses[item.status] ?? item.status} · 查看稿件 →
                  </span>
                </button>
              </div>
              <div className="toolbar compact manuscript-entry-actions">
                <button
                  type="button"
                  disabled={!canDelete(item) || busy}
                  onClick={() => {
                    setDeletionNotice("");
                    deleteDerived.mutate(item.job_id);
                  }}
                >
                  清理派生数据
                </button>
                <button
                  className="danger-button"
                  type="button"
                  disabled={!canDelete(item) || busy}
                  onClick={() => {
                    confirmDeletion([item]);
                  }}
                >
                  删除任务及录音
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="page-stack" aria-labelledby="manuscripts-title">
      <header className="page-header">
        <div>
          <h1 id="manuscripts-title">历史转录稿</h1>
          <p>按日期浏览稿件；可逐项、按日期或按页选择，跨页保留所选任务。</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setPage("upload");
          }}
        >
          导入新音频
        </button>
      </header>
      <div className="panel history-selection-toolbar">
        <label className="history-select">
          <HistoryCheckbox
            label="选择本页全部可删除任务"
            checked={eligible.length > 0 && pageSelected === eligible.length}
            partial={pageSelected > 0 && pageSelected < eligible.length}
            disabled={busy || !eligible.length}
            onChange={() => {
              toggleItems(eligible);
            }}
          />
          本页全选
        </label>
        <span role="status">已选择 {selectedItems.length} 项</span>
        <button
          type="button"
          disabled={busy || !selectedItems.length}
          onClick={() => {
            setSelected({});
          }}
        >
          取消选择
        </button>
        <button
          className="danger-button"
          type="button"
          disabled={busy || !selectedItems.length || query.isPending}
          onClick={() => {
            confirmDeletion(selectedItems);
          }}
        >
          {deleteJobs.isPending
            ? "删除中…"
            : `删除所选任务及录音（${String(selectedItems.length)}）`}
        </button>
      </div>
      <p className="muted">
        仅可删除已完成、失败或已取消的任务。录音被其他任务或裁剪片段引用时会保留。
      </p>
      {deleteJobs.isPending && (
        <div role="status">
          正在处理 {deletionProgress.completed} / {deletionProgress.total} 项
          <progress
            aria-label="批量删除进度"
            value={deletionProgress.completed}
            max={deletionProgress.total || 1}
          />
        </div>
      )}
      {query.isPending && <p role="status">正在加载历史稿件…</p>}
      {deletionNotice && <p role="status">{deletionNotice}</p>}
      {deletionFailures.length > 0 && (
        <div className="error-callout" role="alert">
          <p>
            {deletionFailures.length} 项未能删除，已保留；可重试或查看失败原因。
          </p>
          <ul>
            {deletionFailures.map(({ item, message }) => (
              <li key={item.job_id}>
                {item.source_name}：{message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {deleteDerived.error && (
        <p role="alert">删除失败：{deleteDerived.error.message}</p>
      )}
      {query.isError && (
        <div role="alert">
          <p>{query.error.message}</p>
          <button
            type="button"
            onClick={() => {
              void query.refetch();
            }}
          >
            重新加载
          </button>
        </div>
      )}
      {query.data && (
        <>
          <p className="muted">
            共 {query.data.total} 份稿件 · 按创建时间从新到旧排列 ·
            日期按本地时区显示
          </p>
          {query.data.total === 0 && (
            <p className="empty-state">暂无稿件，请先导入音频创建课堂任务。</p>
          )}
          {groups.filter((group) => !group.older).map(renderGroup)}
          {groups.some((group) => group.older) && (
            <section
              className="history-older"
              aria-labelledby="history-older-title"
            >
              <h2 id="history-older-title">更久以前</h2>
              {groups.filter((group) => group.older).map(renderGroup)}
            </section>
          )}
          <div className="toolbar">
            <button
              type="button"
              disabled={offset === 0 || busy}
              onClick={() => {
                setOffset(Math.max(0, offset - 30));
              }}
            >
              上一页
            </button>
            <span>第 {Math.floor(offset / 30) + 1} 页</span>
            <button
              type="button"
              disabled={offset + 30 >= query.data.total || busy}
              onClick={() => {
                setOffset(offset + 30);
              }}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
