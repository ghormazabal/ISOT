/** Lógica pura sobre el repositorio de datos activo y la biblioteca de templates. */
import type { DataRepoStatus, LibraryTemplate, TemplateIndexEntry } from "./types";

/** Templates para el wizard: los del repo de datos y, después, los de la biblioteca que el repo no tiene. */
export function mergeLibraryTemplates(templates: TemplateIndexEntry[], library: LibraryTemplate[]): TemplateIndexEntry[] {
  const present = new Set(templates.map((item) => item.name));
  const extra = library
    .filter((item) => !present.has(item.name))
    .map((item) => ({
      name: item.name,
      site: "global",
      scope: "global" as const,
      version: item.version,
      distro: item.distro,
      cni: null,
      path: item.path,
      used_by: [],
      library: item.library,
    }));
  return [...templates, ...extra];
}

/** ¿El clon está en otra rama que la declarada en data_repo.branch? */
export function isOffBranch(repo: Pick<DataRepoStatus, "branch" | "expected_branch">): boolean {
  return Boolean(repo.expected_branch && repo.branch && repo.expected_branch !== repo.branch);
}

/** Texto corto de la cabecera: «nombre · rama · sha [● sin commit]». */
export function repoSummary(repo: DataRepoStatus): string {
  if (!repo.git) return `${repo.name} · sin git`;
  const parts = [repo.name, repo.branch ?? "detached", repo.short ?? "—"];
  return parts.join(" · ") + (repo.dirty ? " ● sin commit" : "");
}
