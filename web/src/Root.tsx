import { useCallback, useEffect, useState } from "react";

import App from "./App";
import { api } from "./api";
import { Onboarding } from "./components/Onboarding";
import type { ConfigInfo } from "./types";

const REFRESH_MS = 20000;

/** Decide entre onboarding (sin repositorio de datos) y la app. */
export default function Root() {
  const [info, setInfo] = useState<ConfigInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(async () => {
    setChecking(true);
    try {
      const next = await api.config();
      setInfo(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const configured = info?.configured === true;
  useEffect(() => {
    if (!configured) return;
    // Rama / HEAD / dirty del repo de datos cambian por fuera (git, wizards).
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [configured, load]);

  if (!info && !error) return <p className="boot-error">Cargando configuración…</p>;
  if (!info || !info.configured) {
    return <Onboarding info={info && !info.configured ? info : null} error={error} checking={checking} onRetry={() => void load()} />;
  }
  return <App config={info} onRefreshConfig={() => void load()} />;
}
