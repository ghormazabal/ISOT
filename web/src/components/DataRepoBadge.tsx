import { isOffBranch } from "../dataRepo";
import type { ConfigInfo } from "../types";

type Props = { info: Extract<ConfigInfo, { configured: true }> };

/** Repositorio de datos activo: nombre, rama, HEAD corto y si hay cambios sin commit. */
export function DataRepoBadge({ info }: Props) {
  const repo = info.data_repo;
  const offBranch = isOffBranch(repo);
  const title = [
    `Repositorio de datos: ${repo.name}`,
    repo.remote_url ? `${repo.remote}: ${repo.remote_url}` : null,
    repo.git ? `rama ${repo.branch ?? "(detached)"} · HEAD ${repo.short ?? "—"}` : "sin git: Comparar y wizards deshabilitados",
    repo.dirty ? "hay cambios sin commit en el clon" : null,
    offBranch ? `se esperaba la rama ${repo.expected_branch}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span className="data-repo" id="data-repo-badge" title={title} data-dirty={repo.dirty ? "1" : "0"}>
      <span className="data-repo-icon" aria-hidden="true">⎇</span>
      <span className="data-repo-name">{repo.name}</span>
      {repo.git ? (
        <>
          <span className={offBranch ? "data-repo-branch is-off" : "data-repo-branch"}>{repo.branch ?? "detached"}</span>
          <span className="data-repo-sha mono">{repo.short ?? "—"}</span>
        </>
      ) : (
        <span className="data-repo-branch is-off">sin git</span>
      )}
      {repo.dirty ? (
        <span className="data-repo-dirty" aria-label="cambios sin commit">
          ● sin commit
        </span>
      ) : null}
    </span>
  );
}
