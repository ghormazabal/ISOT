import type { Finding, GraphPayload, WizardPreview } from "./types";

const RANK: Record<string, number> = { error: 3, warning: 2, info: 1 };

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
      const hit = findings.filter((item) => {
        if (item.graph_id === node.id) return true;
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
