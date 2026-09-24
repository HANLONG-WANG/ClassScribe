import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";

import { api, filenameHeaders, type Glossary, type GlossaryTerm } from "../api";

export function GlossaryPage() {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [canonical, setCanonical] = useState("");
  const [reading, setReading] = useState("");
  const [aliases, setAliases] = useState("");
  const [weight, setWeight] = useState("1");
  const [language, setLanguage] = useState<"zh" | "ja" | "en">("ja");
  const [materialSource, setMaterialSource] = useState<
    "auto" | "handout" | "textbook"
  >("auto");
  const [editing, setEditing] = useState<GlossaryTerm | null>(null);
  const [editReading, setEditReading] = useState("");
  const [editAliases, setEditAliases] = useState("");
  const [editLanguage, setEditLanguage] = useState<"zh" | "ja" | "en">("ja");
  const [editWeight, setEditWeight] = useState("1");
  const [editConfirmed, setEditConfirmed] = useState(true);
  const [termError, setTermError] = useState("");
  const query = useQuery({
    queryKey: ["glossaries"],
    queryFn: () => api<Glossary[]>("/glossaries"),
  });
  const create = useMutation({
    mutationFn: () =>
      api<Glossary>("/glossaries", {
        method: "POST",
        body: JSON.stringify({ name: newName }),
      }),
    onSuccess: async (value) => {
      setNewName("");
      setSelected(value.id);
      await client.invalidateQueries({ queryKey: ["glossaries"] });
    },
  });
  const update = useMutation({
    mutationFn: ({ id, terms }: { id: string; terms: unknown[] }) =>
      api<Glossary>(`/glossaries/${id}/terms`, {
        method: "PUT",
        body: JSON.stringify({ terms }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["glossaries"] }),
  });
  const deleteGlossary = useMutation({
    mutationFn: (id: string) => api(`/glossaries/${id}`, { method: "DELETE" }),
    onSuccess: (_value, id) => {
      client.setQueryData<Glossary[]>(["glossaries"], (items = []) =>
        items.filter((item) => item.id !== id),
      );
      setSelected((current) => (current === id ? null : current));
      setCanonical("");
      setReading("");
      return client.invalidateQueries({ queryKey: ["glossaries"] });
    },
  });
  const remove = useMutation({
    mutationFn: ({
      glossaryId,
      termId,
    }: {
      glossaryId: string;
      termId: string;
    }) =>
      api(`/glossaries/${glossaryId}/terms/${termId}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["glossaries"] }),
  });
  const material = useMutation({
    mutationFn: ({
      glossaryId,
      file,
      source,
    }: {
      glossaryId: string;
      file: File;
      source: string;
    }) =>
      api(`/glossaries/${glossaryId}/documents`, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          ...filenameHeaders(file.name),
          "X-ClassScribe-Material-Kind": source,
          "X-ClassScribe-Language": language,
        },
        body: file,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["glossaries"] }),
  });
  const glossary =
    query.data?.find((item) => item.id === selected) ?? query.data?.[0];

  function importFile(glossaryId: string, file: File) {
    const suffix = file.name.split(".").pop()?.toLowerCase();
    const kind = (
      {
        txt: "txt",
        md: "markdown",
        markdown: "markdown",
        csv: "csv",
        pdf: "pdf",
        pptx: "pptx",
      } as Record<string, string>
    )[suffix ?? ""];
    if (!kind) return;
    material.mutate({
      glossaryId,
      file,
      source:
        kind === "csv" || materialSource === "auto" ? kind : materialSource,
    });
  }

  function addTerm(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!glossary || !canonical.trim() || !reading.trim()) return;
    const parsedAliases = aliases
      .split(/[,，;；|]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (
      new Set(parsedAliases).size !== parsedAliases.length ||
      parsedAliases.includes(canonical.trim())
    ) {
      setTermError("别名必须互不重复，且不能与标准写法相同。");
      return;
    }
    setTermError("");
    update.mutate(
      {
        id: glossary.id,
        terms: [
          {
            canonical,
            reading,
            aliases: parsedAliases,
            language,
            weight: Number(weight),
            source: "manual",
            confirmed: true,
          },
        ],
      },
      {
        onSuccess: () => {
          setCanonical("");
          setReading("");
          setAliases("");
          setWeight("1");
        },
      },
    );
  }

  function beginEdit(term: GlossaryTerm) {
    setEditing(term);
    setEditReading(term.reading);
    setEditAliases(term.aliases.join(", "));
    setEditLanguage(term.language as "zh" | "ja" | "en");
    setEditWeight(String(term.weight));
    setEditConfirmed(term.confirmed);
    setTermError("");
  }

  async function saveEdit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!glossary || !editing) return;
    const parsedAliases = editAliases
      .split(/[,，;；|]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (
      new Set(parsedAliases).size !== parsedAliases.length ||
      parsedAliases.includes(editing.canonical)
    ) {
      setTermError("别名必须互不重复，且不能与标准写法相同。");
      return;
    }
    if (!editConfirmed && Number(editWeight) > 0.3) {
      setTermError("未确认建议的权重不能超过 0.30。");
      return;
    }
    setTermError("");
    try {
      await update.mutateAsync({
        id: glossary.id,
        terms: [
          {
            canonical: editing.canonical,
            reading: editReading,
            aliases: parsedAliases,
            language: editLanguage,
            weight: Number(editWeight),
            source: editing.source,
            confirmed: editConfirmed,
          },
        ],
      });
      if (editLanguage !== editing.language) {
        await remove.mutateAsync({
          glossaryId: glossary.id,
          termId: editing.id,
        });
      }
      setEditing(null);
    } catch {
      /* Mutation errors are shown above the glossary. */
    }
  }

  return (
    <section
      className="page-stack glossary-page"
      aria-labelledby="glossary-title"
    >
      <header className="page-header">
        <div>
          <p className="eyebrow">Course context</p>
          <h1 id="glossary-title">课程词典</h1>
          <p>手工词条权重最高；材料抽取只生成低权重建议，确认后才提升。</p>
        </div>
      </header>
      {deleteGlossary.isError && (
        <p className="notice" role="alert">
          删除失败：{deleteGlossary.error.message}
        </p>
      )}
      {create.isError && (
        <p className="error-callout" role="alert">
          创建词典失败：{create.error.message}
        </p>
      )}
      {update.isError && (
        <p className="error-callout" role="alert">
          保存词条失败：{update.error.message}
        </p>
      )}
      {material.isError && (
        <p className="error-callout" role="alert">
          材料导入失败：{material.error.message}
        </p>
      )}
      {remove.isError && (
        <p className="error-callout" role="alert">
          删除词条失败：{remove.error.message}
        </p>
      )}
      {termError && (
        <p className="error-callout" role="alert">
          {termError}
        </p>
      )}
      <div className="split-layout">
        <aside className="panel collection-list">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (newName.trim()) create.mutate();
            }}
          >
            <input
              aria-label="新词典名称"
              maxLength={256}
              onChange={(event) => {
                setNewName(event.target.value);
              }}
              placeholder="新课程词典"
              value={newName}
            />
            <button type="submit">＋</button>
          </form>
          <small>词典名称最多 256 字。</small>
          {(query.data ?? []).map((item) => (
            <button
              className={item.id === glossary?.id ? "active" : ""}
              key={item.id}
              onClick={() => {
                setSelected(item.id);
              }}
              type="button"
            >
              <strong>{item.name}</strong>
              <small>
                {item.terms.length} 个词条 · v{item.version}
              </small>
            </button>
          ))}
        </aside>
        <div className="panel collection-detail">
          {!glossary ? (
            <p className="empty-state">创建第一个课程词典。</p>
          ) : (
            <>
              <div className="card-top">
                <div>
                  <p className="eyebrow">Active glossary</p>
                  <h2>{glossary.name}</h2>
                </div>
                <label className="file-button">
                  导入 TXT / MD / CSV / PDF / PPTX
                  <input
                    accept=".txt,.md,.markdown,.csv,.pdf,.pptx"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) importFile(glossary.id, file);
                    }}
                    type="file"
                  />
                </label>
              </div>
              <label>
                材料来源
                <select
                  value={materialSource}
                  onChange={(event) => {
                    setMaterialSource(
                      event.target.value as "auto" | "handout" | "textbook",
                    );
                  }}
                >
                  <option value="auto">按文件类型</option>
                  <option value="handout">课程讲义</option>
                  <option value="textbook">教材</option>
                </select>
              </label>
              <div className="toolbar compact">
                <button
                  className="danger-button"
                  disabled={
                    deleteGlossary.isPending ||
                    update.isPending ||
                    material.isPending ||
                    remove.isPending
                  }
                  onClick={() => {
                    if (
                      window.confirm(
                        `删除词典「${glossary.name}」？其中的词条和已导入材料也会删除，无法撤销。`,
                      )
                    ) {
                      deleteGlossary.mutate(glossary.id);
                    }
                  }}
                  type="button"
                >
                  {deleteGlossary.isPending ? "正在删除…" : "删除词典"}
                </button>
              </div>
              <form className="term-form" onSubmit={addTerm}>
                <input
                  aria-label="标准写法（必填）"
                  required
                  onChange={(event) => {
                    setCanonical(event.target.value);
                  }}
                  placeholder="标准写法"
                  value={canonical}
                />
                <input
                  aria-label="读音（必填）"
                  required
                  onChange={(event) => {
                    setReading(event.target.value);
                  }}
                  placeholder="读音 / reading"
                  value={reading}
                />
                <input
                  aria-label="别名（以逗号分隔）"
                  placeholder="别名（以逗号分隔）"
                  value={aliases}
                  onChange={(event) => {
                    setAliases(event.target.value);
                  }}
                />
                <input
                  aria-label="权重"
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  required
                  value={weight}
                  onChange={(event) => {
                    setWeight(event.target.value);
                  }}
                />
                <select
                  value={language}
                  onChange={(event) => {
                    setLanguage(event.target.value as "zh" | "ja" | "en");
                  }}
                >
                  <option value="zh">中文</option>
                  <option value="ja">日本語</option>
                  <option value="en">English</option>
                </select>
                <button className="primary-button" type="submit">
                  加入词典
                </button>
              </form>
              <div className="term-table" role="table">
                <div role="row">
                  <strong>词条</strong>
                  <strong>读音</strong>
                  <strong>语言 / 来源</strong>
                  <strong>权重</strong>
                  <span />
                </div>
                {glossary.terms.map((term) => (
                  <div key={term.id} role="row">
                    <span>
                      {term.canonical}
                      {term.aliases.length > 0 && (
                        <small>别名：{term.aliases.join("、")}</small>
                      )}
                    </span>
                    <span>{term.reading}</span>
                    <span>
                      {term.language} · {term.source}
                      {term.confirmed ? " · 已确认" : " · 建议"}
                    </span>
                    <span>{term.weight.toFixed(2)}</span>
                    <span className="term-actions">
                      {!term.confirmed && (
                        <button
                          type="button"
                          onClick={() => {
                            update.mutate({
                              id: glossary.id,
                              terms: [
                                {
                                  canonical: term.canonical,
                                  reading: term.reading,
                                  aliases: term.aliases,
                                  language: term.language,
                                  weight: 1,
                                  source: term.source,
                                  confirmed: true,
                                },
                              ],
                            });
                          }}
                        >
                          确认
                        </button>
                      )}
                      <button
                        type="button"
                        aria-label={`编辑 ${term.canonical}`}
                        onClick={() => {
                          beginEdit(term);
                        }}
                      >
                        编辑
                      </button>
                      <button
                        aria-label={`删除 ${term.canonical}`}
                        onClick={() => {
                          remove.mutate({
                            glossaryId: glossary.id,
                            termId: term.id,
                          });
                        }}
                        type="button"
                      >
                        ×
                      </button>
                    </span>
                  </div>
                ))}
              </div>
              {editing && (
                <form
                  className="term-edit-form"
                  onSubmit={(event) => {
                    void saveEdit(event);
                  }}
                >
                  <h3>编辑词条：{editing.canonical}</h3>
                  <label>
                    读音
                    <input
                      required
                      value={editReading}
                      onChange={(event) => {
                        setEditReading(event.target.value);
                      }}
                    />
                  </label>
                  <label>
                    别名（以逗号分隔）
                    <input
                      value={editAliases}
                      onChange={(event) => {
                        setEditAliases(event.target.value);
                      }}
                    />
                  </label>
                  <label>
                    语言
                    <select
                      value={editLanguage}
                      onChange={(event) => {
                        setEditLanguage(
                          event.target.value as "zh" | "ja" | "en",
                        );
                      }}
                    >
                      <option value="zh">中文</option>
                      <option value="ja">日本語</option>
                      <option value="en">English</option>
                    </select>
                  </label>
                  <label>
                    权重
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      required
                      value={editWeight}
                      onChange={(event) => {
                        setEditWeight(event.target.value);
                      }}
                    />
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={editConfirmed}
                      onChange={(event) => {
                        setEditConfirmed(event.target.checked);
                      }}
                    />{" "}
                    已确认
                  </label>
                  <div className="toolbar compact">
                    <button
                      className="primary-button"
                      type="submit"
                      disabled={update.isPending || remove.isPending}
                    >
                      保存词条
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(null);
                      }}
                    >
                      取消
                    </button>
                  </div>
                </form>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
