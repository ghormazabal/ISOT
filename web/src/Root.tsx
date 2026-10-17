import { useCallback, useEffect, useRef, useState } from "react";

import App from "./App";
import { api, setApiRepo } from "./api";
import { Onboarding } from "./components/Onboarding";
import { RepoUnavailable } from "./components/RepoUnavailable";
import { loadStoredRepo, pickRepo, repoFromUrl, storeRepo, withRepoParam } from "./repos";
import type { ConfigInfo } from "./types";
import { hasSavedDraft } from "./wizardDraft";

const REFRESH_MS = 20000;

function localStore(): Storage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

/**
 * Elige el repositorio de datos (?repo= → último usado → default_repo) y decide
 * entre onboarding, «repositorio no disponible» y la app. Cambiar de repositorio
 * remonta la app (sitio, refs de Comparar y hallazgos vuelven a ese repo).
 */
export default function Root() {
  const [info, setInfo] = useState<ConfigInfo | null>(null);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const repoRef = useRef<string | null>(null);

  const loadConfig = useCallback(async (id: string | null) => {
    setApiRepo(id);
    const next = await api.config();
    // Si mientras tanto se eligió otro repositorio, esta respuesta ya no sirve.
    if (repoRef.current === id) setInfo(next);
    return next;
  }, []);

  const select = useCallback(
    async (id: string | null, history: "push" | "replace" | "none" = "push") => {
      repoRef.current = id;
      setRepoId(id);
      if (id) {
        storeRepo(localStore(), id);
        const url = withRepoParam(window.location.href, id);
        if (history === "push" && url !== window.location.pathname + window.location.search + window.location.hash) {
          window.history.pushState({ repo: id }, "", url);
        } else if (history === "replace") {
          window.history.replaceState({ repo: id }, "", url);
        }
      }
      await loadConfig(id);
    },
    [loadConfig],
  );

  const boot = useCallback(async () => {
    setChecking(true);
    try {
      const list = await api.repos();
      if (!list.configured) {
        repoRef.current = null;
        setRepoId(null);
        setApiRepo(null);
        setInfo(await api.config());
      } else {
        const id = pickRepo(list.repos, list.default_repo, repoFromUrl(window.location.href), loadStoredRepo(localStore()));
        await select(id, "replace");
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  }, [select]);

  useEffect(() => {
    void boot();
  }, [boot]);

  const refresh = useCallback(async () => {
    if (!repoRef.current) return boot();
    try {
      const next = await loadConfig(repoRef.current);
      if (next.configured && !next.repos.some((item) => item.id === repoRef.current)) await boot();
    } catch {
      await boot();
    }
  }, [boot, loadConfig]);

  useEffect(() => {
    const onPop = () => {
      const id = repoFromUrl(window.location.href);
      if (id && id !== repoRef.current) void select(id, "none");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [select]);

  const configured = info?.configured === true;
  useEffect(() => {
    if (!configured) return;
    // Rama / HEAD / dirty de cada repo cambian por fuera (git, wizards).
    const timer = window.setInterval(() => void refresh(), REFRESH_MS);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [configured, refresh]);

  const switchRepo = useCallback(
    (next: string) => {
      if (next === repoRef.current) return;
      const current = info?.configured ? info : null;
      const fromName = current?.data_repo.name ?? repoRef.current;
      const toName = current?.repos.find((item) => item.id === next)?.name ?? next;
      if (
        hasSavedDraft(repoRef.current) &&
        !window.confirm(
          `Hay un borrador del wizard sin confirmar en «${fromName}». Queda guardado y lo recuperas al volver a ese repositorio.\n\n¿Cambiar a «${toName}»?`,
        )
      ) {
        return;
      }
      void select(next);
    },
    [info, select],
  );

  if (!info && !error) return <p className="boot-error">Cargando configuración…</p>;
  if (!info || !info.configured) {
    return <Onboarding info={info && !info.configured ? info : null} error={error} checking={checking} onRetry={() => void boot()} />;
  }
  if (info.active_repo !== repoId) return <p className="boot-error">Cargando repositorio…</p>;
  if (info.data_repo.available === false) {
    return <RepoUnavailable info={info} checking={checking} onSwitch={switchRepo} onRetry={() => void refresh()} />;
  }
  return <App key={repoId} config={info} onRefreshConfig={() => void refresh()} onSwitchRepo={switchRepo} />;
}
