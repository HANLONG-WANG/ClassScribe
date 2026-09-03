import { create } from "zustand";

export type Page =
  | "upload"
  | "job"
  | "transcript"
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

export const useWorkbench = create<WorkbenchState>((set) => ({
  page: "upload",
  currentJobId: null,
  selectedSegmentId: null,
  textLayer: "smart",
  lowConfidenceOnly: false,
  setPage: (page) => {
    set({ page });
  },
  setCurrentJob: (currentJobId) => {
    set({ currentJobId, page: "job", selectedSegmentId: null });
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
