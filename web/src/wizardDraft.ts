/**Borrador del wizard: estado de la app + sessionStorage (sobrevive recarga).*/

import type { ConnectionRule, Finding, FixContext, WizardPreview } from "./types";

export type WizardMode = "pick" | "device" | "site";
export type DockTab = "fields" | "yaml";

export type DeviceDraftState = {
  site: string;
  kind: string;
  cluster: string;
  role: string;
  count: number;
  edits: Record<string, string>;
  saveRules: boolean;
  preview: WizardPreview | null;
  neighborhood: boolean;
  tab: DockTab;
  /** Hallazgo que se está corrigiendo (abierto con «Corregir»), o null. */
  fix?: FixContext | null;
};

export type SiteDraftState = {
  step: number;
  architecture: string;
  spines: number;
  leafPairs: number;
  unpairedLeafs: number;
  mgmtSwitches: number;
  dcgwCount: number;
  portsPerLeaf: number;
  pairPolicy: string;
  rules: ConnectionRule[];
  id: string;
  name: string;
  description: string;
  templateSite: string;
  infraRange: string;
  vlanOffset: number;
  templateRef: string;
  clusterName: string;
  workers: number;
  dci: boolean;
  preview: WizardPreview | null;
  neighborhood: boolean;
  tab: DockTab;
  graphView: "network" | "global";
};

export type WizardDraft = {
  mode: WizardMode;
  device: DeviceDraftState;
  site: SiteDraftState;
};

export type BrowseSnapshot = {
  view: "network" | "kubernetes";
  compare: boolean;
  siteFilter: string;
};

export type WizardSession = {
  v: 1;
  draft: WizardDraft;
  lastBrowse: BrowseSnapshot;
  editing: boolean;
};

export const STORAGE_KEY = "isot.wizard.session";

export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export function emptyDeviceDraft(site = "site1", cluster = "site1-apps"): DeviceDraftState {
  return {
    site,
    kind: "baremetal",
    cluster,
    role: "apps-worker",
    count: 1,
    edits: {},
    saveRules: false,
    preview: null,
    neighborhood: false,
    tab: "fields",
    fix: null,
  };
}

/**
 * Borrador del wizard «Agregar device» precargado desde un hallazgo con
 * remediación add_device: sitio, clúster, rol, tipo y cantidad.
 */
export function draftFromFinding(finding: Finding, base: WizardDraft): WizardDraft | null {
  const action = finding.remediation;
  if (!action || action.kind !== "add_device") return null;
  const device: DeviceDraftState = {
    ...emptyDeviceDraft(action.site, action.cluster ?? ""),
    kind: action.device_kind,
    role: action.role,
    count: Math.max(1, Math.min(action.count || 1, 32)),
    fix: {
      finding: {
        id: finding.id,
        severity: finding.severity,
        site: finding.site,
        cluster: finding.cluster,
        entity_kind: finding.entity_kind,
        entity_id: finding.entity_id,
        message: finding.message,
      },
      remediation: action,
    },
  };
  return { ...base, mode: "device", device };
}

export function emptySiteDraft(templateSite = "site2"): SiteDraftState {
  return {
    step: 1,
    architecture: "spine-leaf",
    spines: 2,
    leafPairs: 2,
    unpairedLeafs: 0,
    mgmtSwitches: 1,
    dcgwCount: 1,
    portsPerLeaf: 48,
    pairPolicy: "fill-first",
    rules: [],
    id: "site3",
    name: "SITE3",
    description: "Sitio de ejemplo generado por wizard",
    templateSite,
    infraRange: "10.90.0.0/20",
    vlanOffset: 0,
    templateRef: "rke2-rancher-hybrid-calico",
    clusterName: "site3-edge",
    workers: 2,
    dci: true,
    preview: null,
    neighborhood: false,
    tab: "fields",
    graphView: "network",
  };
}

export function emptyWizardDraft(opts?: { site?: string; cluster?: string; templateSite?: string }): WizardDraft {
  return {
    mode: "pick",
    device: emptyDeviceDraft(opts?.site, opts?.cluster),
    site: emptySiteDraft(opts?.templateSite),
  };
}

export function defaultBrowse(): BrowseSnapshot {
  return { view: "network", compare: false, siteFilter: "all" };
}

export function isDraftInProgress(draft: WizardDraft | null | undefined): boolean {
  if (!draft) return false;
  if (draft.mode !== "pick") return true;
  if (draft.device.preview || draft.site.preview) return true;
  if (draft.site.step > 1) return true;
  if (Object.keys(draft.device.edits || {}).length > 0) return true;
  if (draft.device.fix) return true;
  return false;
}

/** Editar es un toggle: si ya estás en wizards, vuelves a Sitios/Kubernetes/Comparar. */
export function applyEditToggle(
  view: "network" | "kubernetes" | "wizards",
  lastBrowse: BrowseSnapshot,
): { view: "network" | "kubernetes" | "wizards"; compare: boolean; siteFilter: string } {
  if (view === "wizards") {
    return { view: lastBrowse.view, compare: lastBrowse.compare, siteFilter: lastBrowse.siteFilter };
  }
  return { view: "wizards", compare: false, siteFilter: lastBrowse.siteFilter };
}

function memoryStorage(): StorageLike {
  const data = new Map<string, string>();
  return {
    getItem: (key) => (data.has(key) ? data.get(key)! : null),
    setItem: (key, value) => {
      data.set(key, value);
    },
    removeItem: (key) => {
      data.delete(key);
    },
  };
}

export function getDefaultStorage(): StorageLike {
  try {
    if (typeof sessionStorage !== "undefined") return sessionStorage;
  } catch {
    /* private mode */
  }
  return memoryStorage();
}

export function loadSession(storage: StorageLike = getDefaultStorage()): WizardSession | null {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as WizardSession;
    if (!parsed || parsed.v !== 1 || !parsed.draft) return null;
    const base = emptyWizardDraft();
    return {
      v: 1,
      editing: Boolean(parsed.editing),
      lastBrowse: {
        view: parsed.lastBrowse?.view === "kubernetes" ? "kubernetes" : "network",
        compare: Boolean(parsed.lastBrowse?.compare),
        siteFilter: String(parsed.lastBrowse?.siteFilter || "all"),
      },
      draft: {
        mode: parsed.draft.mode === "device" || parsed.draft.mode === "site" ? parsed.draft.mode : "pick",
        device: { ...base.device, ...(parsed.draft.device || {}) },
        site: { ...base.site, ...(parsed.draft.site || {}) },
      },
    };
  } catch {
    return null;
  }
}

function slimPreview(preview: WizardPreview | null): WizardPreview | null {
  if (!preview) return null;
  const graphs = preview.graphs
    ? {
        network: preview.graphs.network
          ? {
              ...preview.graphs.network,
              nodes: (preview.graphs.network.nodes || []).map((node) => ({
                ...node,
              })),
              edges: preview.graphs.network.edges || [],
            }
          : undefined,
        global: preview.graphs.global
          ? {
              ...preview.graphs.global,
              nodes: (preview.graphs.global.nodes || []).map((node) => ({ ...node })),
              edges: preview.graphs.global.edges || [],
            }
          : undefined,
        kubernetes: preview.graphs.kubernetes,
      }
    : undefined;
  return { ...preview, graphs };
}

function withoutHeavyGraphs(draft: WizardDraft): WizardDraft {
  return {
    ...draft,
    device: { ...draft.device, preview: slimPreview(draft.device.preview) },
    site: { ...draft.site, preview: slimPreview(draft.site.preview) },
  };
}

function dropGraphs(draft: WizardDraft): WizardDraft {
  const strip = (preview: WizardPreview | null): WizardPreview | null => {
    if (!preview) return null;
    const { graphs: _graphs, ...rest } = preview;
    return rest;
  };
  return {
    ...draft,
    device: { ...draft.device, preview: strip(draft.device.preview) },
    site: { ...draft.site, preview: strip(draft.site.preview) },
  };
}

export function saveSession(session: WizardSession, storage: StorageLike = getDefaultStorage()): boolean {
  const payload: WizardSession = { v: 1, draft: session.draft, lastBrowse: session.lastBrowse, editing: session.editing };
  const attempts = [payload, { ...payload, draft: withoutHeavyGraphs(payload.draft) }, { ...payload, draft: dropGraphs(payload.draft) }];
  for (const item of attempts) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(item));
      return true;
    } catch {
      /* quota */
    }
  }
  return false;
}

export function clearSession(storage: StorageLike = getDefaultStorage()): void {
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function draftsEqual(left: WizardDraft, right: WizardDraft): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
