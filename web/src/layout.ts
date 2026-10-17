import type { Edge, Node } from "@xyflow/react";
import type { DiffStatus, GraphNode, GraphPayload, GroupBy } from "./types";

const COL_WIDTH = 280;
const NODE_HEIGHT = 72;
const GROUP_PAD_X = 16;
const GROUP_PAD_Y = 48;

export const KIND_LABELS: Record<string, string> = {
  baremetal: "Baremetal",
  "virtual-machines": "Máquinas virtuales",
  network: "Red",
  template: "Templates",
  cluster: "Clústeres",
  site: "Sitios",
  remote: "Remoto",
  release: "Releases Helm",
  otros: "Otros",
};

const PALETTE = ["#5b9fd4", "#3dba8c", "#d4a017", "#c46b9a", "#8aa0b3", "#e07a5f"];

export const DIFF_STROKE: Record<DiffStatus, string> = {
  added: "#3dd68c",
  removed: "#f87171",
  modified: "#e4b44c",
  unchanged: "#3a4550",
};

export function vlanColor(vlan: number | string | null | undefined): string {
  const id = Number(vlan);
  if (!Number.isFinite(id)) return "#8a969e";
  return PALETTE[Math.abs(id) % PALETTE.length];
}

function groupKey(node: GraphNode, groupBy: GroupBy): string {
  if (node.entity === "network") return "network";
  if (node.entity === "release") return "release";
  if (groupBy === "kind") return node.kind || "otros";
  if (groupBy === "pair") return node.leaf_pair || "sin-par";
  return node.cluster_ref || "sin-cluster";
}

function vlanGroup(nodeId: string, graph: GraphPayload): string {
  const fromEdge = graph.edges.find((edge) => edge.source === nodeId || edge.target === nodeId);
  const vlan = fromEdge?.vlans[0];
  if (vlan !== undefined && vlan !== null) return `vlan-${vlan}`;
  return "sin-vlan";
}

function groupLabel(key: string, groupBy: GroupBy): string {
  if (key === "network") return "VLANs / redes";
  if (key === "release") return "Releases Helm";
  if (groupBy === "kind") return KIND_LABELS[key] || key;
  if (groupBy === "cluster") return key === "sin-cluster" ? "Sin clúster" : key;
  if (groupBy === "pair") {
    if (key === "sin-par") return "Sin par";
    if (key === "spines") return "Spines";
    if (key === "mgmt") return "MGMT / OOB";
    if (key === "border") return "Border / DCGW";
    if (key === "unpaired") return "Leafs sueltos";
    return key.startsWith("PAIR") ? `Par ${key.replace("PAIR", "")}` : key;
  }
  if (key === "sin-vlan") return "Sin VLAN";
  return key.replace("vlan-", "VLAN ");
}

function pairRank(key: string): number {
  if (key === "spines") return 0;
  if (key.startsWith("PAIR")) {
    const number = Number(key.replace("PAIR", ""));
    return Number.isFinite(number) ? number : 10;
  }
  if (key === "unpaired") return 40;
  if (key === "mgmt") return 50;
  if (key === "border") return 51;
  if (key === "sin-par") return 99;
  return 20;
}

function flowNodeType(entity: GraphNode["entity"]): string {
  if (entity === "cluster") return "clusterNode";
  if (entity === "template") return "templateNode";
  if (entity === "site") return "siteNode";
  if (entity === "network") return "networkNode";
  if (entity === "release") return "releaseNode";
  return "device";
}

export function buildFlow(graph: GraphPayload, groupBy: GroupBy): { nodes: Node[]; edges: Edge[] } {
  const buckets = new Map<string, GraphNode[]>();
  for (const node of graph.nodes) {
    const key =
      graph.view === "global"
        ? "sitios"
        : graph.view === "kubernetes"
          ? node.kind === "template" || node.entity === "template"
            ? "templates"
            : node.kind === "release" || node.entity === "release"
              ? "releases"
              : `${node.site ?? ""}:${node.cluster_ref || node.hostname}`
          : groupBy === "vlan"
            ? vlanGroup(node.id, graph)
            : groupKey(node, groupBy);
    const list = buckets.get(key) ?? [];
    list.push(node);
    buckets.set(key, list);
  }

  const nodes: Node[] = [];
  const ordered = [...buckets.entries()];
  if (groupBy === "pair") {
    ordered.sort((left, right) => pairRank(left[0]) - pairRank(right[0]) || left[0].localeCompare(right[0]));
  }
  ordered.forEach(([key, members], col) => {
    const height = GROUP_PAD_Y + members.length * (NODE_HEIGHT + 10) + 16;
    const label =
      graph.view === "global"
        ? "Sitios"
        : graph.view === "kubernetes"
          ? key === "templates"
            ? "Templates"
            : key === "releases"
              ? "Releases Helm"
              : `clúster ${key}`
          : groupLabel(key, groupBy);
    const rank: Record<string, number> = { error: 3, warning: 2, info: 1 };
    let groupSev: string | undefined;
    let best = 0;
    for (const member of members) {
      const sev = member.finding_severity;
      const value = sev ? rank[sev] || 0 : 0;
      if (value > best) {
        best = value;
        groupSev = sev;
      }
    }
    const border = groupSev === "error" ? "#d46a6a" : groupSev === "warning" ? "#d4a017" : groupSev === "info" ? "#5b9fd4" : "#2c3640";
    nodes.push({
      id: `group:${key}`,
      type: "clusterGroup",
      position: { x: 24 + col * (COL_WIDTH + 40), y: 16 },
      data: { label, finding_severity: groupSev },
      style: {
        width: COL_WIDTH,
        height,
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${border}`,
        borderRadius: 8,
        pointerEvents: "none",
      },
      selectable: false,
    });
    members.forEach((member, index) => {
      nodes.push({
        id: member.id,
        type: flowNodeType(member.entity),
        parentId: `group:${key}`,
        extent: "parent",
        position: { x: GROUP_PAD_X, y: GROUP_PAD_Y + index * (NODE_HEIGHT + 10) },
        data: member,
        draggable: false,
        style: { pointerEvents: "all", cursor: member.entity === "site" ? "pointer" : undefined },
      });
    });
  });

  const edges: Edge[] = graph.edges.map((edge) => {
    const primary = edge.vlans[0] ?? null;
    const label = edge.vlans.length ? edge.vlans.map((vlan) => String(vlan)).join(" · ") : undefined;
    const stroke = edge.diff_status ? DIFF_STROKE[edge.diff_status] : vlanColor(primary);
    const width = edge.diff_status && edge.diff_status !== "unchanged" ? 2.2 : 1.6;
    const removed = edge.diff_status === "removed";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label,
      type: "smoothstep",
      style: {
        stroke,
        strokeWidth: width,
        opacity: edge.diff_status === "unchanged" ? 0.45 : removed ? 0.75 : 1,
        strokeDasharray: removed ? "6 4" : undefined,
      },
      labelStyle: { fill: stroke, fontSize: 10, fontFamily: "IBM Plex Mono, monospace" },
      animated: false,
    };
  });

  return { nodes, edges };
}
