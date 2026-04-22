import { create } from "zustand";
import type { UUID } from "../api/client";

export type View = "library" | "upload" | "schema" | "reader" | "regen" | "settings";

interface UIState {
  view: View;
  selectedBookId: UUID | null;
  selectedSectionId: UUID | null;
  activeJobId: UUID | null;
  selectedRegenId: UUID | null;
  setView: (v: View) => void;
  selectBook: (id: UUID | null) => void;
  selectSection: (id: UUID | null) => void;
  setJob: (id: UUID | null) => void;
  setRegenId: (id: UUID | null) => void;
}

export const useUI = create<UIState>((set) => ({
  view: "library",
  selectedBookId: null,
  selectedSectionId: null,
  activeJobId: null,
  selectedRegenId: null,
  setView: (view) => set({ view }),
  selectBook: (id) => set({ selectedBookId: id, selectedSectionId: null, selectedRegenId: null }),
  selectSection: (id) => set({ selectedSectionId: id }),
  setJob: (id) => set({ activeJobId: id }),
  setRegenId: (id) => set({ selectedRegenId: id }),
}));
