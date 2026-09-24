import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GlossaryPage } from "../src/pages/GlossaryPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setup(fail = false) {
  let items = [
    { id: "one", name: "词典一", terms: [], version: 1 },
    { id: "two", name: "词典二", terms: [], version: 1 },
  ];
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      if (fail)
        return Promise.resolve(
          new Response(JSON.stringify({ error: { detail: "删除被拒绝" } }), {
            status: 409,
          }),
        );
      items = items.filter((item) => url !== `/api/v1/glossaries/${item.id}`);
      return Promise.resolve(new Response(JSON.stringify({ deleted: true })));
    }
    return Promise.resolve(new Response(JSON.stringify(items)));
  });
  vi.stubGlobal("fetch", fetch);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <GlossaryPage />
    </QueryClientProvider>,
  );
  return { fetch, confirm };
}

it("supports cancellation, selects a remaining glossary, and handles deletion of the last one", async () => {
  const { fetch, confirm } = setup();
  await screen.findByRole("heading", { name: "词典一" });
  confirm.mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(
    false,
  );
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  await screen.findByRole("heading", { name: "词典二" });
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("词典一"));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "删除词典" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  await screen.findByText("创建第一个课程词典。");
});

it("shows deletion errors and keeps the glossary available", async () => {
  setup(true);
  await screen.findByRole("heading", { name: "词典一" });
  fireEvent.click(screen.getByRole("button", { name: "删除词典" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("删除被拒绝");
  expect(screen.getByRole("heading", { name: "词典一" })).toBeVisible();
  expect(screen.getByRole("button", { name: "删除词典" })).toBeEnabled();
});

it("reimports the same file and sends case-insensitive formats and material sources", async () => {
  const { fetch } = setup();
  await screen.findByRole("heading", { name: "词典一" });
  const input = document.querySelector('.file-button input[type="file"]');
  if (!input) throw new Error("missing material picker");
  const csv = new File(["canonical\nterm"], "TERMS.CSV");
  fireEvent.change(input, { target: { files: [csv] } });
  await waitFor(() => {
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
  });
  expect((input as HTMLInputElement).value).toBe("");
  fireEvent.change(input, { target: { files: [csv] } });
  await waitFor(() => {
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(2);
  });
  const csvRequest = fetch.mock.calls.find(
    ([, init]) => init?.method === "POST",
  );
  expect(
    new Headers(csvRequest?.[1]?.headers).get("X-ClassScribe-Material-Kind"),
  ).toBe("csv");

  fireEvent.change(screen.getByLabelText("材料来源"), {
    target: { value: "handout" },
  });
  fireEvent.change(input, {
    target: { files: [new File(["pdf"], "lecture.PDF")] },
  });
  await waitFor(() => {
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(3);
  });
  const pdfRequest = fetch.mock.calls.filter(
    ([, init]) => init?.method === "POST",
  )[2];
  expect(
    new Headers(pdfRequest?.[1]?.headers).get("X-ClassScribe-Material-Kind"),
  ).toBe("handout");
  fireEvent.change(screen.getByLabelText("材料来源"), {
    target: { value: "textbook" },
  });
  fireEvent.change(input, {
    target: { files: [new File(["pptx"], "chapter.PPTX")] },
  });
  await waitFor(() => {
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(4);
  });
  const pptxRequest = fetch.mock.calls.filter(
    ([, init]) => init?.method === "POST",
  )[3];
  expect(
    new Headers(pptxRequest?.[1]?.headers).get("X-ClassScribe-Material-Kind"),
  ).toBe("textbook");
});

it("confirms a suggestion while retaining its source and exposes editable term details", async () => {
  let term = {
    id: "term-1",
    canonical: "人工知能",
    reading: "じんこうちのう",
    aliases: ["AI"],
    language: "ja",
    weight: 0.2,
    source: "txt",
    confirmed: false,
  };
  const fetch = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      if (typeof init.body !== "string") throw new Error("expected JSON body");
      const parsed = JSON.parse(init.body) as {
        terms: Array<Partial<typeof term>>;
      };
      term = { ...term, ...parsed.terms[0] };
    }
    return Promise.resolve(
      new Response(
        JSON.stringify([
          { id: "one", name: "词典一", terms: [term], version: 1 },
        ]),
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <GlossaryPage />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "词典一" });
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => {
    expect(term.confirmed).toBe(true);
  });
  expect(term.source).toBe("txt");
  expect(term.weight).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "编辑 人工知能" }));
  const form = document.querySelector(".term-edit-form");
  if (!form) throw new Error("missing term editor");
  expect(
    within(form as HTMLElement).getByLabelText("别名（以逗号分隔）"),
  ).toHaveValue("AI");
  expect(within(form as HTMLElement).getByLabelText("读音")).toHaveValue(
    "じんこうちのう",
  );
});
