import { pickDefaultRefs } from "./compareRefs.ts";
import type { RefsPayload } from "./types.ts";

/** Ref del working tree del repo de datos (cambios sin commit incluidos). */
export const LOCAL_REF = "local";
export const LOCAL_LABEL = "Local (working tree)";
export const BASE_PARAM = "base";
export const HEAD_PARAM = "head";

export type CompareRefs = { base: string; head: string };

const LOCAL_ALIASES = new Set(["local", "working", "worktree", "working-tree"]);
// Igual que el backend (isot.gitdiff.SAFE_REF): HEAD, SHA (4-40 hex) o nombre de rama/tag; ~N/^N opcionales.
const SAFE_REF = /^(?:HEAD|[0-9a-fA-F]{4,40}|[A-Za-z0-9][A-Za-z0-9._/-]*)(?:[~^][0-9]+)*$/;

export function isLocalRef(value: string | null | undefined): boolean {
  return LOCAL_ALIASES.has((value ?? "").trim().toLowerCase());
}

/** `local` normalizado, una ref con sintaxis válida, o null. La validación real la hace el backend (git rev-parse --verify). */
export function normalizeRef(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  if (!text) return null;
  if (isLocalRef(text)) return LOCAL_REF;
  if (text.length > 200 || text.includes("..") || !SAFE_REF.test(text)) return null;
  return text;
}

export function looksLikeSha(value: string): boolean {
  return /^[0-9a-f]{7,40}$/i.test(value.trim());
}

/** Texto corto para la barra: «Local», el nombre de rama/tag o el SHA corto. */
export function refLabel(ref: string, sha?: string | null): string {
  if (isLocalRef(ref)) return "Local";
  if (looksLikeSha(ref)) return ref.slice(0, 7);
  return sha && ref === sha ? sha.slice(0, 7) : ref;
}

/** `?base=…&head=…` → refs (ambas válidas) o null. */
export function compareFromUrl(href: string): CompareRefs | null {
  const url = new URL(href, "http://x");
  const base = normalizeRef(url.searchParams.get(BASE_PARAM));
  const head = normalizeRef(url.searchParams.get(HEAD_PARAM));
  return base && head ? { base, head } : null;
}

/** Escribe (o quita, con null) base/head en la URL conservando el resto (repo, tab, sub…). */
export function withCompareParams(href: string, refs: CompareRefs | null): string {
  const url = new URL(href, "http://x");
  if (refs) {
    url.searchParams.set(BASE_PARAM, refs.base);
    url.searchParams.set(HEAD_PARAM, refs.head);
  } else {
    url.searchParams.delete(BASE_PARAM);
    url.searchParams.delete(HEAD_PARAM);
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Por defecto: lo que cambió en local respecto de main/master (o HEAD si no hay ramas). */
export function defaultCompare(payload: RefsPayload | null): CompareRefs {
  const base = payload ? pickDefaultRefs(payload).base : "";
  return { base: base || "HEAD", head: LOCAL_REF };
}

export function swapRefs(refs: CompareRefs): CompareRefs {
  return { base: refs.head, head: refs.base };
}

/** Guardar escribe en el working tree: solo con head = Local. */
export function headIsWritable(refs: CompareRefs | null): boolean {
  return !refs || isLocalRef(refs.head);
}
