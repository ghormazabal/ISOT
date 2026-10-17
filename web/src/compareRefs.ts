import type { RefsPayload } from "./types";

const BASE_CANDIDATES = ["origin/main", "main", "origin/master", "master"];

/**
 * Refs por defecto de «Comparar», sin nombres propios de la demo.
 *
 * Base: main/master (local o remoto) o, si no hay, la primera rama.
 * Head: la rama más reciente (el backend las ordena por fecha de commit) que no
 * sea la base ni apunte al commit que está en uso. Si no hay ninguna, queda
 * vacía y la persona la elige.
 */
export function pickDefaultRefs(payload: RefsPayload): { base: string; head: string } {
  const branches = payload.branches ?? [];
  const names = branches.map((item) => item.name);
  const base = BASE_CANDIDATES.find((name) => names.includes(name)) || names[0] || "";
  const baseSha = branches.find((item) => item.name === base)?.sha;
  const current = payload.head?.sha;
  const sameAsBase = (name: string) => name === base || name.replace(/^origin\//, "") === base.replace(/^origin\//, "");
  const candidates = branches.filter((item) => !sameAsBase(item.name) && item.sha !== baseSha);
  const head = (candidates.find((item) => item.sha !== current) ?? candidates[0])?.name ?? "";
  return { base, head };
}
