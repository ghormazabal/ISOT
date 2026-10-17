/** Selección del repositorio de datos (varios repos en isot.yaml): lógica pura. */
import type { RepoStatus } from "./types";

export const REPO_STORAGE_KEY = "isot.repo";
export const REPO_PARAM = "repo";
/** Con más repositorios que esto, el selector muestra un buscador. */
export const SEARCH_THRESHOLD = 5;

type StorageLike = Pick<Storage, "getItem" | "setItem">;

/** Orden de preferencia: ?repo= de la URL → último elegido (localStorage) → default_repo → primero. */
export function pickRepo(
  repos: Pick<RepoStatus, "id">[],
  defaultRepo: string | null,
  fromUrl: string | null,
  fromStorage: string | null,
): string | null {
  const known = new Set(repos.map((item) => item.id));
  for (const candidate of [fromUrl, fromStorage, defaultRepo]) {
    if (candidate && known.has(candidate)) return candidate;
  }
  return repos[0]?.id ?? null;
}

/** Filtra por id, nombre, carpeta o rama (sin distinguir mayúsculas). */
export function filterRepos<T extends Pick<RepoStatus, "id" | "name" | "path" | "branch">>(repos: T[], query: string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return repos;
  return repos.filter((item) =>
    [item.id, item.name, item.path, item.branch ?? ""].some((value) => value.toLowerCase().includes(needle)),
  );
}

/** Estado corto de un repositorio para el selector. */
export function repoState(repo: Pick<RepoStatus, "available" | "git" | "dirty" | "error">): {
  kind: "unavailable" | "nogit" | "dirty" | "clean";
  label: string;
} {
  if (!repo.available) return { kind: "unavailable", label: "no disponible" };
  if (!repo.git) return { kind: "nogit", label: "sin git" };
  if (repo.dirty) return { kind: "dirty", label: "● sin commit" };
  return { kind: "clean", label: "limpio" };
}

/** URL con ?repo=<id> (el resto de la query y el hash se conservan). */
export function withRepoParam(href: string, repoId: string | null): string {
  const url = new URL(href, "http://localhost");
  if (repoId) url.searchParams.set(REPO_PARAM, repoId);
  else url.searchParams.delete(REPO_PARAM);
  const query = url.searchParams.toString();
  return `${url.pathname}${query ? `?${query}` : ""}${url.hash}`;
}

export function repoFromUrl(href: string): string | null {
  return new URL(href, "http://localhost").searchParams.get(REPO_PARAM);
}

export function loadStoredRepo(storage: StorageLike | null): string | null {
  try {
    return storage?.getItem(REPO_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function storeRepo(storage: StorageLike | null, repoId: string): void {
  try {
    storage?.setItem(REPO_STORAGE_KEY, repoId);
  } catch {
    /* modo privado */
  }
}
