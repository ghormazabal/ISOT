/**
 * «Cambios»: lo que «Guardar» dejó en el working tree del repo de datos y el commit aparte
 * (ver isot/worktree.py). Funciones puras para agrupar, seleccionar y elegir la rama destino.
 */
import type { WorktreeArea, WorktreeFile, WorktreeStatus } from "./types";

export const AREA_ORDER: WorktreeArea[] = ["output", "templates", "renders", "sites", "index", "other"];

export const AREA_LABEL: Record<WorktreeArea, string> = {
  output: "Salida de renders",
  templates: "Templates",
  renders: "renders.yaml",
  sites: "Datos de sitios",
  index: "Índice",
  other: "Otros",
};

export const WORKTREE_STATUS_LABEL: Record<string, string> = {
  modified: "modificado",
  added: "añadido",
  deleted: "borrado",
  untracked: "nuevo",
};

/** Ramas en las que nunca se hace commit (igual que el backend). */
export const PROTECTED_BRANCHES = new Set(["main", "master", "HEAD"]);

export type AreaGroup = { id: WorktreeArea; label: string; files: WorktreeFile[] };

/** Archivos agrupados por área, en orden fijo y sin grupos vacíos. */
export function groupByArea(files: WorktreeFile[]): AreaGroup[] {
  return AREA_ORDER.map((id) => ({ id, label: AREA_LABEL[id], files: files.filter((file) => file.area === id) })).filter((group) => group.files.length);
}

/** Selección inicial: todo lo pendiente salvo el índice (se regenera e incluye solo). */
export function defaultSelection(files: WorktreeFile[]): Set<string> {
  return new Set(files.filter((file) => file.area !== "index").map((file) => file.path));
}

/** Selección tras recargar el estado: conserva lo elegido que sigue pendiente y añade lo nuevo. */
export function mergeSelection(previous: Set<string> | null, before: WorktreeFile[], files: WorktreeFile[]): Set<string> {
  if (!previous) return defaultSelection(files);
  const known = new Set(before.map((file) => file.path));
  const next = new Set<string>();
  for (const file of files) {
    if (file.area === "index") continue;
    if (previous.has(file.path) || !known.has(file.path)) next.add(file.path);
  }
  return next;
}

export function toggle(selection: Set<string>, paths: string[], on: boolean): Set<string> {
  const next = new Set(selection);
  for (const path of paths) {
    if (on) next.add(path);
    else next.delete(path);
  }
  return next;
}

/** Mismo slug que el backend (isot.wizard.slugify): minúsculas, sin acentos, guiones, 48 máx. */
export function slugify(text: string): string {
  const ascii = (text || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const slug = ascii.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return (slug || "cambio").slice(0, 48);
}

export function isWizardBranch(branch: string | null | undefined): boolean {
  return Boolean(branch && branch.startsWith("wizard/") && !PROTECTED_BRANCHES.has(branch));
}

/** Opciones de rama destino: la actual solo si es wizard/…; si no, siempre una nueva. */
export function commitTargets(status: Pick<WorktreeStatus, "branch">): { current: boolean; defaultTarget: "current" | "new" } {
  const current = isWizardBranch(status.branch);
  return { current, defaultTarget: current ? "current" : "new" };
}

/** Rama que se crearía (sin el sufijo -2, -3… que añade el backend si ya existe). */
export function newBranchName(branchName: string, message: string, count: number): string {
  return `wizard/${slugify(branchName.trim() || message.trim() || `isot: ${count} archivo(s)`)}`;
}

/** Motivo por el que «Commit» está deshabilitado (null = se puede). */
export function commitBlockReason(input: {
  edit: boolean;
  selected: number;
  pending: number;
  indexOnly: boolean;
  target: "current" | "new";
  branch: string;
}): string | null {
  if (!input.edit) return "Solo lectura: arranca ISoT con --edit (ISOT_EDIT=1).";
  if (!input.selected && !(input.indexOnly && input.pending === 0)) return "Elige al menos un archivo.";
  if (input.target === "current" && !isWizardBranch(input.branch)) return `Nunca se hace commit en ${input.branch}: usa una rama wizard/… nueva.`;
  return null;
}
