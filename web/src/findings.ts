import type { Finding, FindingCounts, FindingsPayload, GraphPayload, WizardPreview } from "./types";

const RANK: Record<string, number> = { error: 3, warning: 2, info: 1 };

/** Clave del grupo de hallazgos que no pertenecen a ningún sitio. */
export const GLOBAL_GROUP = "__global__";

export function countFindings(items: Finding[]): FindingCounts {
  const counts = { error: 0, warning: 0, info: 0, total: 0 };
  for (const item of items) {
    if (item.severity === "error") counts.error += 1;
    else if (item.severity === "warning") counts.warning += 1;
    else if (item.severity === "info") counts.info += 1;
  }
  counts.total = counts.error + counts.warning + counts.info;
  return counts;
}

/**
 * Payload acotado a un sitio: solo hallazgos con `site === siteId`.
 * Los de templates ya vienen atribuidos por la API al clúster que los usa.
 * `siteId` "all" (o vacío) devuelve el payload global tal cual.
 */
export function scopeFindings(payload: FindingsPayload | null, siteId: string | null | undefined): FindingsPayload | null {
  if (!payload || !siteId || siteId === "all") return payload;
  if (payload.scope === siteId) return payload;
  const findings = payload.findings.filter((item) => item.site === siteId);
  const counts = countFindings(findings);
  return {
    ...payload,
    scope: siteId,
    counts,
    by_site: { [siteId]: counts },
    global: { error: 0, warning: 0, info: 0, total: 0 },
    findings,
  };
}

export type FindingGroup = {
  key: string;
  site: string | null;
  counts: FindingCounts;
  findings: Finding[];
};

/**
 * Agrupa por sitio para la vista global. Incluye sitios sin hallazgos (si se
 * pasan en `siteIds`) y al final el grupo "Global / plantillas" si tiene algo.
 */
export function groupFindingsBySite(findings: Finding[], siteIds: string[] = []): FindingGroup[] {
  const buckets = new Map<string, Finding[]>();
  for (const id of siteIds) buckets.set(id, []);
  const globals: Finding[] = [];
  for (const item of findings) {
    if (!item.site) {
      globals.push(item);
      continue;
    }
    const list = buckets.get(item.site) ?? [];
    list.push(item);
    buckets.set(item.site, list);
  }
  const groups: FindingGroup[] = [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([site, items]) => ({ key: site, site, counts: countFindings(items), findings: items }));
  if (globals.length) {
    groups.push({ key: GLOBAL_GROUP, site: null, counts: countFindings(globals), findings: globals });
  }
  return groups;
}

/** "rke2-x" de "global/rke2-x" (o null si el hallazgo no viene de un template). */
export function templateName(finding: Finding): string | null {
  if (!finding.source_template) return null;
  const parts = finding.source_template.split("/");
  return parts[parts.length - 1] || finding.source_template;
}

export function worstSeverity(items: Finding[]): "error" | "warning" | "info" | undefined {
  let best = 0;
  let sev: "error" | "warning" | "info" | undefined;
  for (const item of items) {
    if (item.severity === "off") continue;
    const rank = RANK[item.severity] || 0;
    if (rank > best) {
      best = rank;
      sev = item.severity;
    }
  }
  return sev;
}

export function findingsForNode(findings: Finding[], nodeId: string, clusterRef?: string | null, site?: string | null): Finding[] {
  return findings.filter((item) => {
    if (item.graph_id && item.graph_id === nodeId) return true;
    if (clusterRef && item.cluster === clusterRef && item.site === site) return true;
    return false;
  });
}

export function annotateGraph(graph: GraphPayload | null, findings: Finding[]): GraphPayload | null {
  if (!graph) return graph;
  return {
    ...graph,
    nodes: graph.nodes.map((node) => {
      const globalView = graph.view === "global";
      const hit = findings.filter((item) => {
        // Mapa de sitios: cada sitio suma todos sus hallazgos (incluidos los de templates atribuidos).
        if (globalView && node.entity === "site") return Boolean(item.site) && item.site === node.site;
        if (item.graph_id === node.id) return true;
        if (
          node.entity === "template" &&
          item.source_template &&
          node.id === `template:${item.source_template.replace("/", ":")}`
        ) {
          return true;
        }
        if (node.entity === "cluster" && item.cluster === node.hostname && item.site === node.site) return true;
        if (node.entity === "device" && item.entity_kind === "device" && item.graph_id === node.id) return true;
        if (node.entity === "site" && item.site === node.site && item.entity_kind === "site") return true;
        if (node.cluster_ref && item.cluster === node.cluster_ref && item.site === node.site) return true;
        return false;
      });
      if (!hit.length) return node;
      const counts = { error: 0, warning: 0, info: 0 };
      for (const item of hit) {
        if (item.severity === "error") counts.error += 1;
        else if (item.severity === "warning") counts.warning += 1;
        else if (item.severity === "info") counts.info += 1;
      }
      return { ...node, finding_severity: worstSeverity(hit), finding_counts: counts };
    }),
  };
}

export function wizardCanCommit(preview: WizardPreview | null, acceptedWarnings: boolean): { ok: boolean; reason: string | null } {
  const added = preview?.findings?.added ?? [];
  if (added.some((item) => item.severity === "error")) {
    return { ok: false, reason: "error" };
  }
  if (added.some((item) => item.severity === "warning") && !acceptedWarnings) {
    return { ok: false, reason: "warning" };
  }
  return { ok: true, reason: null };
}

export function selectionFromFinding(finding: Finding): {
  type: "site" | "device" | "cluster" | "template";
  site?: string;
  hostname?: string;
  name?: string;
  id?: string;
} | null {
  if (finding.source_template && finding.site && finding.cluster) {
    // Hallazgo heredado de un template: se abre el clúster del sitio que lo usa.
    return { type: "cluster", site: finding.site, name: finding.cluster };
  }
  if (finding.entity_kind === "cluster" && finding.site && finding.cluster) {
    return { type: "cluster", site: finding.site, name: finding.cluster };
  }
  if (finding.entity_kind === "device" && finding.site) {
    const host = finding.entity_id.split("/")[1] || finding.entity_id;
    return { type: "device", site: finding.site, hostname: host };
  }
  if (finding.entity_kind === "template") {
    const [site, name] = finding.entity_id.split("/");
    return { type: "template", site: site || "global", name: name || finding.entity_id };
  }
  if (finding.site) return { type: "site", id: finding.site, site: finding.site };
  return null;
}

/** ¿El hallazgo tiene una acción que abre un wizard? */
export function canFixWithWizard(finding: Finding): boolean {
  return finding.remediation?.kind === "add_device";
}

/** Texto del resultado del preview frente al hallazgo que se corrige. */
export function fixSummary(preview: WizardPreview | null): { ok: boolean; text: string } | null {
  const fix = preview?.findings?.fix;
  if (!fix) return null;
  const also = fix.also_resolves.length ? ` · también resuelve ${fix.also_resolves.join(", ")}` : "";
  if (fix.resolved) return { ok: true, text: `Resuelve ${fix.id}${also}` };
  return { ok: false, text: `Aún no resuelve ${fix.id}${also}` };
}
