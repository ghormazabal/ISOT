/** Lógica pura del detalle de conexiones (aristas del grafo). */
import type { EdgeSelection, Finding, GraphEdge, GraphPayload, LinkDetail, LinkItem, LinkSide, LinkVlan } from "./types";

export const DASH = "—";

export function dashValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  if (Array.isArray(value)) return value.length ? value.map((item) => dashValue(item)).join(", ") : DASH;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function edgeSelection(edge: GraphEdge): EdgeSelection {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    vlans: edge.vlans ?? [],
    ports: edge.ports ?? [],
    diff_status: edge.diff_status,
  };
}

/** Busca la arista seleccionada en el grafo actual (o null si ya no está). */
export function findEdge(graph: GraphPayload | null, id: string | null | undefined): GraphEdge | null {
  if (!graph || !id) return null;
  return graph.edges.find((edge) => edge.id === id) ?? null;
}

/** Ids de nodos que se mantienen resaltados al seleccionar una arista. */
export function edgeFocusIds(edge: Pick<EdgeSelection, "source" | "target"> | null): string[] {
  return edge ? [edge.source, edge.target] : [];
}

export type SideRow = { label: string; source: string; target: string };

const SIDE_FIELDS: Array<[keyof LinkSide, string]> = [
  ["interface", "Interfaz"],
  ["port", "Puerto"],
  ["bond", "Bond"],
  ["port_channel", "PortChannel / LAG"],
  ["lacp", "LACP (mode)"],
  ["members", "Miembros"],
  ["ip", "IP"],
  ["mtu", "MTU"],
  ["device_mtu", "MTU del equipo"],
];

/** Filas «campo | lado A | lado B». Campos vacíos en ambos lados se muestran igual con «—». */
export function sideRows(link: LinkItem): SideRow[] {
  const rows: SideRow[] = SIDE_FIELDS.map(([key, label]) => ({
    label,
    source: dashValue(link.source?.[key]),
    target: dashValue(link.target?.[key]),
  }));
  const extraKeys = new Set([...Object.keys(link.source?.extra ?? {}), ...Object.keys(link.target?.extra ?? {})]);
  for (const key of [...extraKeys].sort()) {
    rows.push({ label: key, source: dashValue(link.source?.extra?.[key]), target: dashValue(link.target?.extra?.[key]) });
  }
  return rows;
}

export function vlanLabel(vlan: LinkVlan): string {
  const parts = [String(vlan.id)];
  if (vlan.name) parts.push(vlan.name);
  return parts.join(" · ");
}

export function vlanNote(vlan: LinkVlan): string | null {
  if (vlan.implicit) return "implícita (OOB por defecto)";
  if (vlan.extra_host) return "extra_host_vlans";
  if (vlan.allowed === false) return "no está en allowed_vlans";
  return null;
}

export const LINK_TYPE_LABEL: Record<LinkItem["type"], string> = {
  bond: "Bond / LAG",
  interface: "Interfaz",
  oob: "OOB",
  hypervisor: "Hypervisor",
  reference: "Referencia",
};

function mentions(text: string, token: string): boolean {
  if (!token) return false;
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[^A-Za-z0-9_-])${escaped}([^A-Za-z0-9_-]|$)`, "i").test(text);
}

/**
 * Hallazgos relacionados con la conexión: los que nombran ambos extremos, o los
 * de un extremo que mencionan un puerto / bond del enlace (p.ej. INV-PORT).
 */
export function relatedFindings(detail: LinkDetail | null, findings: Finding[]): Finding[] {
  if (!detail) return [];
  const a = detail.source;
  const b = detail.target;
  const labels = [a.label, b.label];
  const ids = new Set([a.id, b.id]);
  const ports = detail.ports.filter((port) => port.length > 2);
  const owns = (finding: Finding, endpoint: typeof a) =>
    finding.graph_id === endpoint.id ||
    finding.entity_id === endpoint.id ||
    finding.entity_id === `${endpoint.site}/${endpoint.label}` ||
    mentions(finding.message, endpoint.label);
  return findings.filter((finding) => {
    if (finding.severity === "off") return false;
    const text = `${finding.message} ${finding.path ?? ""}`;
    const both = labels.every((label) => mentions(text, label)) || (owns(finding, a) && owns(finding, b));
    if (both) return true;
    const touches = ids.has(finding.graph_id ?? "") || owns(finding, a) || owns(finding, b);
    return touches && ports.some((port) => mentions(text, port));
  });
}

export type ChangeRow = { path: string; before: string; after: string; status: "added" | "removed" | "modified" };

export function changeRows(changes: Array<{ path: string; before: unknown; after: unknown; status: ChangeRow["status"] }>): ChangeRow[] {
  return changes.map((item) => ({
    path: item.path,
    before: dashValue(item.before),
    after: dashValue(item.after),
    status: item.status,
  }));
}

/** Detalle mínimo cuando el inventario no conoce la arista: solo lo que trae el grafo. */
export function fallbackDetail(edge: EdgeSelection, label: (id: string) => string): LinkDetail {
  const endpoint = (id: string) => {
    const [site, ...rest] = id.split(":");
    return { id, entity: "device" as const, label: label(id), site, hostname: rest.join(":") };
  };
  return {
    kind: "network",
    source: endpoint(edge.source),
    target: endpoint(edge.target),
    links: [],
    vlans: edge.vlans.map((id) => ({ id, name: null, cidr: null, gateway: null, vrf: null, description: null })),
    ports: edge.ports,
    paths: [],
  };
}
