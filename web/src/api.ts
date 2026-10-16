import type {
  ChartValues,
  ClusterDetail,
  ClusterIndexEntry,
  DeviceDetail,
  DeviceIndexEntry,
  GraphPayload,
  CommitsPayload,
  RefsPayload,
  SemanticDiff,
  SiteIndexEntry,
  TemplateDetail,
  TemplateIndexEntry,
  Topology,
  ValuesDiff,
} from "./types";

async function fetchJSON<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* respuesta no JSON */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  sites: () => fetchJSON<SiteIndexEntry[]>("/api/sites"),
  templates: () => fetchJSON<TemplateIndexEntry[]>("/api/templates"),
  globalGraph: () => fetchJSON<GraphPayload>("/api/graph?view=global"),
  allNetworkGraph: () => fetchJSON<GraphPayload>("/api/graph?view=network"),
  allKubernetesGraph: () => fetchJSON<GraphPayload>("/api/graph?view=kubernetes"),
  devices: (site: string) => fetchJSON<DeviceIndexEntry[]>(`/api/sites/${encodeURIComponent(site)}/devices`),
  clusters: (site: string) => fetchJSON<ClusterIndexEntry[]>(`/api/sites/${encodeURIComponent(site)}/clusters`),
  siteTemplates: (site: string) =>
    fetchJSON<TemplateIndexEntry[]>(`/api/sites/${encodeURIComponent(site)}/templates`),
  graph: (site: string, view: "network" | "kubernetes" = "network") =>
    fetchJSON<GraphPayload>(`/api/sites/${encodeURIComponent(site)}/graph?view=${view}`),
  topology: (site: string) => fetchJSON<Topology>(`/api/sites/${encodeURIComponent(site)}/topology`),
  device: (site: string, hostname: string) =>
    fetchJSON<DeviceDetail>(
      `/api/sites/${encodeURIComponent(site)}/devices/${encodeURIComponent(hostname)}`,
    ),
  cluster: (site: string, name: string) =>
    fetchJSON<ClusterDetail>(`/api/sites/${encodeURIComponent(site)}/clusters/${encodeURIComponent(name)}`),
  template: (site: string, name: string) =>
    site === "global"
      ? fetchJSON<TemplateDetail>(`/api/templates/${encodeURIComponent(name)}`)
      : fetchJSON<TemplateDetail>(
          `/api/sites/${encodeURIComponent(site)}/templates/${encodeURIComponent(name)}`,
        ),
  templateValues: (site: string, name: string, release: string) => {
    const encoded = `${encodeURIComponent(name)}/values/${encodeURIComponent(release)}`;
    return site === "global"
      ? fetchJSON<ChartValues>(`/api/templates/${encoded}`)
      : fetchJSON<ChartValues>(`/api/sites/${encodeURIComponent(site)}/templates/${encoded}`);
  },
  clusterValuesDiff: (site: string, name: string) =>
    fetchJSON<ValuesDiff>(
      `/api/sites/${encodeURIComponent(site)}/clusters/${encodeURIComponent(name)}/values-diff`,
    ),
  refs: (rev?: string) => {
    const params = rev ? `?rev=${encodeURIComponent(rev)}` : "";
    return fetchJSON<RefsPayload>(`/api/refs${params}`);
  },
  commits: (rev: string, limit = 80) => {
    const params = new URLSearchParams({ rev, limit: String(limit) });
    return fetchJSON<CommitsPayload>(`/api/commits?${params.toString()}`);
  },
  diff: (base: string, head: string, site?: string) => {
    const params = new URLSearchParams({ base, head });
    if (site && site !== "all") params.set("site", site);
    return fetchJSON<SemanticDiff>(`/api/diff?${params.toString()}`);
  },
};
