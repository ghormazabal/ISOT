import { useEffect, useMemo, useRef, useState } from "react";

import { isOffBranch } from "../dataRepo";
import { SEARCH_THRESHOLD, filterRepos, repoState } from "../repos";
import type { DataRepoStatus, RepoStatus } from "../types";

type Props = {
  /** Repositorio de la vista (estado fresco de /api/config). */
  current: DataRepoStatus;
  repos: RepoStatus[];
  activeId: string;
  onSelect: (repoId: string) => void;
};

function repoTitle(repo: DataRepoStatus): string {
  return [
    `Repositorio de datos: ${repo.name} (${repo.id})`,
    repo.remote_url ? `${repo.remote}: ${repo.remote_url}` : null,
    repo.available === false
      ? `no disponible: ${repo.error ?? ""}`
      : repo.git
        ? `rama ${repo.branch ?? "(detached)"} · HEAD ${repo.short ?? "—"}`
        : "sin git: Comparar y wizards deshabilitados",
    repo.dirty ? "hay cambios sin commit en el clon" : null,
    isOffBranch(repo) ? `se esperaba la rama ${repo.expected_branch}` : null,
  ]
    .filter(Boolean)
    .join("\n");
}

/** Cabecera: repositorio activo y, con varios configurados, selector desplegable. */
export function RepoSwitcher({ current, repos, activeId, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const many = repos.length > 1;
  const withSearch = repos.length > SEARCH_THRESHOLD;
  const visible = useMemo(() => filterRepos(repos, query), [repos, query]);
  const anyEdit = repos.some((item) => item.edit);
  const offBranch = isOffBranch(current);
  const state = repoState({ available: current.available !== false, git: current.git, dirty: current.dirty, error: current.error ?? null });

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    if (withSearch) window.setTimeout(() => searchRef.current?.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, withSearch]);

  const badge = (
    <>
      <span className="data-repo-icon" aria-hidden="true">⎇</span>
      <span className="data-repo-name">{current.name}</span>
      {state.kind === "unavailable" ? (
        <span className="data-repo-branch is-off">no disponible</span>
      ) : current.git ? (
        <>
          <span className={offBranch ? "data-repo-branch is-off" : "data-repo-branch"}>{current.branch ?? "detached"}</span>
          <span className="data-repo-sha mono">{current.short ?? "—"}</span>
        </>
      ) : (
        <span className="data-repo-branch is-off">sin git</span>
      )}
      {current.dirty ? (
        <span className="data-repo-dirty" aria-label="cambios sin commit">
          ● sin commit
        </span>
      ) : null}
    </>
  );

  if (!many) {
    return (
      <span className="data-repo" id="data-repo-badge" title={repoTitle(current)} data-dirty={current.dirty ? "1" : "0"}>
        {badge}
      </span>
    );
  }

  const choose = (repoId: string) => {
    setOpen(false);
    setQuery("");
    if (repoId !== activeId) onSelect(repoId);
  };

  return (
    <div className="repo-switcher" ref={rootRef}>
      <button
        type="button"
        className={open ? "data-repo is-switcher is-open" : "data-repo is-switcher"}
        id="data-repo-badge"
        title={repoTitle(current)}
        data-dirty={current.dirty ? "1" : "0"}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {badge}
        <span className="repo-switcher-count" aria-label={`${repos.length} repositorios`}>
          {repos.length}
        </span>
        <span className="repo-switcher-caret" aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div className="repo-switcher-menu" id="repo-switcher-menu" role="listbox" aria-label="Repositorios de datos">
          <div className="repo-switcher-head">
            Repositorios de datos <span className="muted">· {repos.length}</span>
          </div>
          {withSearch ? (
            <input
              ref={searchRef}
              id="repo-switcher-search"
              className="repo-switcher-search"
              placeholder="Buscar repositorio…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && visible[0]) choose(visible[0].id);
              }}
            />
          ) : null}
          <ul>
            {visible.map((repo) => {
              const itemState = repoState(repo);
              const active = repo.id === activeId;
              return (
                <li key={repo.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    id={`repo-option-${repo.id}`}
                    className={`repo-option is-${itemState.kind}${active ? " is-active" : ""}`}
                    title={repoTitle(repo)}
                    onClick={() => choose(repo.id)}
                  >
                    <span className="repo-option-main">
                      <span className="repo-option-check" aria-hidden="true">{active ? "✓" : ""}</span>
                      <span className="repo-option-name">{repo.name}</span>
                      {repo.default ? <span className="repo-tag">por defecto</span> : null}
                      {anyEdit && !repo.edit && repo.available ? <span className="repo-tag">solo lectura</span> : null}
                    </span>
                    <span className="repo-option-meta">
                      <span className="mono">{repo.id}</span>
                      {repo.available && repo.git ? (
                        <>
                          <span className={isOffBranch(repo) ? "data-repo-branch is-off" : "data-repo-branch"}>{repo.branch ?? "detached"}</span>
                          <span className="mono">{repo.short ?? "—"}</span>
                        </>
                      ) : null}
                      <span className={`repo-state is-${itemState.kind}`}>{itemState.label}</span>
                    </span>
                    {!repo.available && repo.error ? <span className="repo-option-error">{repo.error}</span> : null}
                  </button>
                </li>
              );
            })}
            {visible.length === 0 ? <li className="repo-option-empty muted">Sin coincidencias</li> : null}
          </ul>
          <div className="repo-switcher-foot muted">
            Se configuran en <span className="mono">isot.yaml</span> → <span className="mono">repos</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
