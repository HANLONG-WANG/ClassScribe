import { create } from "zustand";
import { readStorage, writeStorage } from "./storage";

export type Page =
  | "upload"
  | "job"
  | "queue"
  | "transcript"
  | "transcripts"
  | "models"
  | "glossary"
  | "exports"
  | "settings"
  | "ibus";
export type TextLayer = "raw" | "faithful" | "smart" | "user";

interface WorkbenchState {
  page: Page;
  currentJobId: string | null;
  selectedSegmentId: string | null;
  textLayer: TextLayer;
  lowConfidenceOnly: boolean;
  setPage: (page: Page) => void;
  setCurrentJob: (jobId: string) => void;
  selectSegment: (segmentId: string | null) => void;
  setTextLayer: (layer: TextLayer) => void;
  setLowConfidenceOnly: (value: boolean) => void;
}

const restoredJobId = readStorage("classscribe-current-job");
const pageKey = "classscribe-current-page";
const savedPage = readStorage(pageKey);
const restoredPage: Page = (
  [
    "upload",
    "job",
    "queue",
    "transcript",
    "transcripts",
    "models",
    "glossary",
    "exports",
    "settings",
    "ibus",
  ] as string[]
).includes(savedPage ?? "")
  ? (savedPage as Page)
  : "upload";

export const useWorkbench = create<WorkbenchState>((set) => ({
  page:
    (restoredPage === "job" || restoredPage === "transcript") && !restoredJobId
      ? "upload"
      : restoredPage,
  currentJobId: restoredJobId,
  selectedSegmentId: null,
  textLayer: "smart",
  lowConfidenceOnly: false,
  setPage: (page) => {
    writeStorage(pageKey, page);
    set({ page });
  },
  setCurrentJob: (currentJobId) => {
    writeStorage("classscribe-current-job", currentJobId);
    writeStorage(pageKey, "job");
    set({
      currentJobId,
      page: "job",
      selectedSegmentId: null,
      lowConfidenceOnly: false,
    });
  },
  selectSegment: (selectedSegmentId) => {
    set({ selectedSegmentId });
  },
  setTextLayer: (textLayer) => {
    set({ textLayer });
  },
  setLowConfidenceOnly: (lowConfidenceOnly) => {
    set({ lowConfidenceOnly });
  },
}));
