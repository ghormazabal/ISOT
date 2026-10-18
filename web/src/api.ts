import type {
  ChartValues,
  ClusterDetail,
  ClusterIndexEntry,
  ConfigInfo,
  ReposPayload,
  ConnectionPolicy,
  DeviceDetail,
  DeviceIndexEntry,
  GraphPayload,
  LinkResponse,
  CommitsPayload,
  CompareFilePayload,
  CompareFilesPayload,
  IndexStatus,
  RefMeta,
  RefsPayload,
  SemanticDiff,
  SiteIndexEntry,
  TemplateDetail,
  TemplateIndexEntry,
  Topology,
  ValuesDiff,
  FindingsDiffPayload,
  FindingsPayload,
  WizardSave,
  WorktreeCommitBody,
  WorktreeCommitResult,
  WorktreeDiff,
  WorktreeStatus,
  WizardPreview,
  WizardStatus,
} from "./types";

let activeRepo: string | null = null;

/** Repositorio de datos de las llamadas siguientes (prefijo /api/repos/{id}/). */
export function setApiRepo(repoId: string | null): void {
  activeRepo = repoId;
}

export function getApiRepo(): string | null {
  return activeRepo;
}

/** `/api/x` → `/api/repos/{id}/x` con un repositorio activo; `/api/repos…` no se toca. */
export function repoUrl(url: string, repoId: string | null = activeRepo): string {
  if (!repoId || !url.startsWith("/api/") || url === "/api/repos" || url.startsWith("/api/repos/") || url.startsWith("/api/repos?")) {
    return url;
  }
  return `/api/repos/${encodeURIComponent(repoId)}/${url.slice("/api/".length)}`;
}

/** Error HTTP con su código (409 = el working tree cambió desde la vista previa). */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function isConflict(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409;
}

/** Avisa a la barra superior («Cambios») de que el working tree cambió. */
export const WORKTREE_EVENT = "isot:worktree-changed";
export function notifyWorktreeChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(WORKTREE_EVENT));
}

/** Abre el panel «Cambios» de la barra superior (p. ej. desde el resultado de un guardado). */
export const OPEN_CHANGES_EVENT = "isot:open-changes";
export function openChanges(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(OPEN_CHANGES_EVENT));
}

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(repoUrl(url), init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* respuesta no JSON */
    }
    throw new ApiError(detail, response.status);
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
  resolveRef: (ref: string) => fetchJSON<RefMeta>(`/api/refs/resolve?${new URLSearchParams({ ref }).toString()}`),
  compareFiles: (base: string, head: string, prefix?: string[]) => {
    const params = new URLSearchParams({ base, head });
    for (const item of prefix ?? []) params.append("prefix", item);
    return fetchJSON<CompareFilesPayload>(`/api/compare/files?${params.toString()}`);
  },
  compareFile: (base: string, head: string, path: string) =>
    fetchJSON<CompareFilePayload>(`/api/compare/file?${new URLSearchParams({ base, head, path }).toString()}`),
  indexStatus: () => fetchJSON<IndexStatus>("/api/index/status"),
  reindex: () => postJSON<{ ok: boolean; saved: boolean; files?: string[]; message?: string }>("/api/index/reindex", {}),
  worktree: () => fetchJSON<WorktreeStatus>("/api/worktree"),
  worktreeDiff: (path: string) => fetchJSON<WorktreeDiff>(`/api/worktree/diff?path=${encodeURIComponent(path)}`),
  worktreeDiscard: (paths: string[], tokens?: Record<string, string>) =>
    postJSON<WorktreeStatus & { discarded: string[] }>("/api/worktree/discard", { paths, tokens }),
  worktreeCommit: (body: WorktreeCommitBody) => postJSON<WorktreeCommitResult>("/api/worktree/commit", body),
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
  wizardStatus: () => fetchJSON<WizardStatus>("/api/wizard/status"),
  config: () => fetchJSON<ConfigInfo>("/api/config"),
  repos: () => fetchJSON<ReposPayload>("/api/repos"),
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
  wizardDeviceSave: (body: unknown) => postJSON<WizardSave>("/api/wizard/device/save", body),
  wizardSitePreview: (body: unknown) => postJSON<WizardPreview>("/api/wizard/site/preview", body),
  wizardSiteSave: (body: unknown) => postJSON<WizardSave>("/api/wizard/site/save", body),
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
