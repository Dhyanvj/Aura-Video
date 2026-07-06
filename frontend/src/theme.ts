import { useEffect, useState } from "react";

export type ThemeMode = "light" | "dark";
const STORAGE_KEY = "aura-theme";

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;
}

function readStoredTheme(): ThemeMode | null {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : null;
}

function applyTheme(mode: ThemeMode) {
  document.documentElement.classList.toggle("dark", mode === "dark");
}

// Applied once at module load (before React mounts) so there's no
// light-mode flash on a dark-preferring system, or vice versa.
applyTheme(readStoredTheme() ?? (systemPrefersDark() ? "dark" : "light"));

export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(() => readStoredTheme() ?? (systemPrefersDark() ? "dark" : "light"));

  useEffect(() => {
    applyTheme(mode);
  }, [mode]);

  const setTheme = (next: ThemeMode) => {
    localStorage.setItem(STORAGE_KEY, next);
    setMode(next);
  };

  return [mode, setTheme];
}
