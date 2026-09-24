import { create } from "zustand";

export const useAuthStatus = create<{ invalid: boolean }>(() => ({
  invalid: false,
}));
