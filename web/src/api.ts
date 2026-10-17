import type {
  ChartValues,
  ClusterDetail,
  ClusterIndexEntry,
  ConnectionPolicy,
  DeviceDetail,
  DeviceIndexEntry,
  GraphPayload,
  LinkResponse,
  CommitsPayload,
  RefsPayload,
  SemanticDiff,
  SiteIndexEntry,
  TemplateDetail,
  TemplateIndexEntry,
  Topology,
  ValuesDiff,
  FindingsDiffPayload,
  FindingsPayload,
  WizardCommit,
  WizardPreview,
} from "./types";

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
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

function postJSON<T>(url: string, body: unknown): Promise<T> {
  return fetchJSON<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  link: (source: string, target: string, refs?: { base: string; head: string } | null) => {
    const params = new URLSearchParams({ source, target });
    if (refs) {
      params.set("base", refs.base);
      params.set("head", refs.head);
    }
    return fetchJSON<LinkResponse>(`/api/link?${params.toString()}`);
  },
  linkPreview: (source: string, target: string, files: Array<{ path: string; content: string }>) =>
    postJSON<LinkResponse>("/api/link/preview", { source, target, files }),
  wizardStatus: () => fetchJSON<{ edit: boolean }>("/api/wizard/status"),
  wizardCablingDefaults: (architecture: string, extra?: Record<string, string | number>) => {
    const params = new URLSearchParams({ architecture });
    if (extra) {
      for (const [key, value] of Object.entries(extra)) {
        params.set(key, String(value));
      }
    }
    return fetchJSON<ConnectionPolicy>(`/api/wizard/cabling-defaults?${params.toString()}`);
  },
  wizardDevicePreview: (body: unknown) => postJSON<WizardPreview>("/api/wizard/device/preview", body),
  wizardDeviceCommit: (body: unknown) => postJSON<WizardCommit>("/api/wizard/device/commit", body),
  wizardSitePreview: (body: unknown) => postJSON<WizardPreview>("/api/wizard/site/preview", body),
  wizardSiteCommit: (body: unknown) => postJSON<WizardCommit>("/api/wizard/site/commit", body),
  findings: (params?: { site?: string; ref?: string }) => {
    const search = new URLSearchParams();
    if (params?.site && params.site !== "all") search.set("site", params.site);
    if (params?.ref) search.set("ref", params.ref);
    const q = search.toString();
    return fetchJSON<FindingsPayload>(`/api/findings${q ? `?${q}` : ""}`);
  },
  findingsDiff: (base: string, head: string, site?: string) => {
    const search = new URLSearchParams({ base, head });
    if (site && site !== "all") search.set("site", site);
    return fetchJSON<FindingsDiffPayload>(`/api/findings?${search.toString()}`);
  },
};
