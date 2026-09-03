import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";

import { api, type Glossary } from "../api";

export function GlossaryPage() {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [canonical, setCanonical] = useState("");
  const [reading, setReading] = useState("");
  const [language, setLanguage] = useState<"zh" | "ja" | "en">("ja");
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
    mutationFn: ({ glossaryId, file }: { glossaryId: string; file: File }) =>
      api(`/glossaries/${glossaryId}/documents`, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-ClassScribe-Filename": file.name,
          "X-ClassScribe-Material-Kind": file.name.endsWith(".csv")
            ? "csv"
            : file.name.endsWith(".md")
              ? "markdown"
              : "txt",
          "X-ClassScribe-Language": language,
        },
        body: file,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["glossaries"] }),
  });
  const glossary =
    query.data?.find((item) => item.id === selected) ?? query.data?.[0];

  function addTerm(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!glossary || !canonical.trim() || !reading.trim()) return;
    update.mutate({
      id: glossary.id,
      terms: [
        {
          canonical,
          reading,
          aliases: [],
          language,
          weight: 1,
          source: "manual",
          confirmed: true,
        },
      ],
    });
    setCanonical("");
    setReading("");
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
              onChange={(event) => {
                setNewName(event.target.value);
              }}
              placeholder="新课程词典"
              value={newName}
            />
            <button type="submit">＋</button>
          </form>
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
                  导入 TXT / MD / CSV
                  <input
                    accept=".txt,.md,.csv"
                    onChange={(event) => {
                      const file = event.target.files?.item(0);
                      if (file)
                        material.mutate({ glossaryId: glossary.id, file });
                    }}
                    type="file"
                  />
                </label>
              </div>
              <form className="term-form" onSubmit={addTerm}>
                <input
                  onChange={(event) => {
                    setCanonical(event.target.value);
                  }}
                  placeholder="标准写法"
                  value={canonical}
                />
                <input
                  onChange={(event) => {
                    setReading(event.target.value);
                  }}
                  placeholder="读音 / reading"
                  value={reading}
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
                    <span>{term.canonical}</span>
                    <span>{term.reading}</span>
                    <span>
                      {term.language} · {term.source}
                      {term.confirmed ? " · 已确认" : " · 建议"}
                    </span>
                    <span>{term.weight.toFixed(2)}</span>
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
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
