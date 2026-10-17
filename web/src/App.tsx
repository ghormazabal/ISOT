import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { CompareSidebar } from "./components/CompareSidebar";
import { CompareValues } from "./components/CompareValues";
import { DetailPanel } from "./components/DetailPanel";
import { FindingsBell, FindingsPanel } from "./components/FindingsPanel";
import { RefPicker, effectiveRef } from "./components/RefPicker";
import { Splitter, useMediaQuery, usePanelSize, useViewport } from "./components/Splitter";
import { Sidebar } from "./components/Sidebar";
import { TemplateExplorer } from "./components/TemplateExplorer";
import { TopologyGraph } from "./components/TopologyGraph";
import { ValuesDiffView } from "./components/ValuesDiff";
import { WizardsView } from "./components/WizardsView";
import { api } from "./api";
import { countFindings, scopeFindings, selectionFromFinding } from "./findings";
import { appPanelSpecs, clampSize, layoutKey } from "./resize";
import {
  applyEditToggle,
  defaultBrowse,
  draftFromFinding,
  draftsEqual,
  emptyWizardDraft,
  isDraftInProgress,
  loadSession,
  saveSession,
  type BrowseSnapshot,
  type WizardDraft,
} from "./wizardDraft";
import type {
  AppView,
  CanvasMode,
  ClusterDetail,
  ClusterIndexEntry,
  DeviceDetail,
  DeviceIndexEntry,
  DiffEntity,
  Finding,
  OpenEntityRemediation,
  FindingsDiffPayload,
  FindingsPayload,
  GraphNode,
  GraphPayload,
  GroupBy,
  RefsPayload,
  Selection,
  SemanticDiff,
  SiteFilter,
  SiteIndexEntry,
  TemplateDetail,
  TemplateIndexEntry,
  Topology,
  ValuesDiff,
} from "./types";

const savedSession = loadSession();

export default function App() {
  const [view, setView] = useState<AppView>(() => (savedSession?.editing ? "wizards" : savedSession?.lastBrowse.view ?? "network"));
  const [siteFilter, setSiteFilter] = useState<SiteFilter>(() => savedSession?.lastBrowse.siteFilter ?? "all");
  const [lastBrowse, setLastBrowse] = useState<BrowseSnapshot>(() => savedSession?.lastBrowse ?? defaultBrowse());
  const [wizardDraft, setWizardDraft] = useState<WizardDraft>(() => savedSession?.draft ?? emptyWizardDraft());
  const [sites, setSites] = useState<SiteIndexEntry[]>([]);
  const [devicesBySite, setDevicesBySite] = useState<Record<string, DeviceIndexEntry[]>>({});
  const [clustersBySite, setClustersBySite] = useState<Record<string, ClusterIndexEntry[]>>({});
  const [templates, setTemplates] = useState<TemplateIndexEntry[]>([]);
  const [graphs, setGraphs] = useState<Record<string, GraphPayload>>({});
  const [topologies, setTopologies] = useState<Record<string, Topology>>({});
  const [query, setQuery] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("cluster");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [deviceDetail, setDeviceDetail] = useState<DeviceDetail | null>(null);
  const [clusterDetail, setClusterDetail] = useState<ClusterDetail | null>(null);
  const [templateDetail, setTemplateDetail] = useState<TemplateDetail | null>(null);
  const [valuesDiff, setValuesDiff] = useState<ValuesDiff | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [canvas, setCanvas] = useState<CanvasMode>("graph");
  const [compare, setCompare] = useState(() => Boolean(savedSession && !savedSession.editing && savedSession.lastBrowse.compare));
  const [refs, setRefs] = useState<RefsPayload | null>(null);
  const [baseBranch, setBaseBranch] = useState("");
  const [baseCommit, setBaseCommit] = useState("");
  const [baseCustom, setBaseCustom] = useState("");
  const [headBranch, setHeadBranch] = useState("");
  const [headCommit, setHeadCommit] = useState("");
  const [headCustom, setHeadCustom] = useState("");
  const [diff, setDiff] = useState<SemanticDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [onlyChanges, setOnlyChanges] = useState(true);
  const [diffEntity, setDiffEntity] = useState<DiffEntity | null>(null);
  const [editEnabled, setEditEnabled] = useState(false);
  const [findings, setFindings] = useState<FindingsPayload | null>(null);
  const [findingsDiff, setFindingsDiff] = useState<FindingsDiffPayload | null>(null);
  const [findingsOpen, setFindingsOpen] = useState(false);
  const [wizardSeed, setWizardSeed] = useState<{ key: number; draft: WizardDraft } | null>(null);

  const persistDraft = useCallback((draft: WizardDraft) => {
    setWizardDraft((current) => (draftsEqual(current, draft) ? current : draft));
  }, []);

  const clearDraft = useCallback(() => {
    setWizardDraft(emptyWizardDraft());
  }, []);

  useEffect(() => {
    if (view === "wizards") return;
    setLastBrowse({
      view: view === "kubernetes" ? "kubernetes" : "network",
      compare,
      siteFilter,
    });
  }, [view, compare, siteFilter]);

  useEffect(() => {
    saveSession({
      v: 1,
      draft: wizardDraft,
      lastBrowse,
      editing: view === "wizards",
    });
  }, [wizardDraft, lastBrowse, view]);

  const toggleEdit = () => {
    const next = applyEditToggle(view, lastBrowse);
    setView(next.view);
    setCompare(next.compare);
    if (view === "wizards") setSiteFilter(next.siteFilter);
  };

  const draftPending = isDraftInProgress(wizardDraft);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [siteIdx, templateIdx, globalGraph, gitRefs, wizard, findingsPayload] = await Promise.all([
          api.sites(),
          api.templates(),
          api.globalGraph(),
          api.refs().catch(() => null),
          api.wizardStatus().catch(() => ({ edit: false })),
          api.findings().catch(() => null),
        ]);
        if (cancelled) return;
        const devices: Record<string, DeviceIndexEntry[]> = {};
        const clusters: Record<string, ClusterIndexEntry[]> = {};
        const graphMap: Record<string, GraphPayload> = { "all:global": globalGraph };
        const topoMap: Record<string, Topology> = {};
        await Promise.all(
          siteIdx.map(async (site) => {
            const [devs, clus, net, k8s, topo] = await Promise.all([
              api.devices(site.id),
              api.clusters(site.id),
              api.graph(site.id, "network"),
              api.graph(site.id, "kubernetes"),
              api.topology(site.id),
            ]);
            devices[site.id] = devs;
            clusters[site.id] = clus;
            graphMap[`${site.id}:network`] = net;
            graphMap[`${site.id}:kubernetes`] = k8s;
            topoMap[site.id] = topo;
          }),
        );
        if (cancelled) return;
        const allK8s = await api.allKubernetesGraph();
        if (cancelled) return;
        graphMap["all:kubernetes"] = allK8s;
        setSites(siteIdx);
        setTemplates(templateIdx);
        setDevicesBySite(devices);
        setClustersBySite(clusters);
        setGraphs(graphMap);
        setTopologies(topoMap);
        if (gitRefs) {
          setRefs(gitRefs);
          const pick = pickDefaultRefs(gitRefs);
          setBaseBranch(pick.base);
          setHeadBranch(pick.head);
        }
        if (findingsPayload) setFindings(findingsPayload);
        const canEdit = Boolean(wizard?.edit);
        setEditEnabled(canEdit);
        if (!canEdit && view === "wizards") {
          setView(lastBrowse.view);
          setCompare(lastBrowse.compare);
        }
      } catch (err) {
        if (!cancelled) setBootError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (compare) {
      setDeviceDetail(null);
      setClusterDetail(null);
      setTemplateDetail(null);
      setValuesDiff(null);
      setLoadingDetail(false);
      return;
    }
    if (!selection || selection.type === "site") {
      setDeviceDetail(null);
      setClusterDetail(null);
      setTemplateDetail(null);
      setValuesDiff(null);
      setError(null);
      if (!selection || selection.type === "site") setLoadingDetail(false);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    setError(null);
    setValuesDiff(null);
    const selected = selection;
    const request =
      selected.type === "device"
        ? api.device(selected.site, selected.hostname)
        : selected.type === "cluster"
          ? api.cluster(selected.site, selected.name)
          : api.template(selected.site, selected.name);
    request
      .then(async (payload) => {
        if (cancelled) return;
        setDeviceDetail(selected.type === "device" ? (payload as DeviceDetail) : null);
        const cluster = selected.type === "cluster" ? (payload as ClusterDetail) : null;
        setClusterDetail(cluster);
        setTemplateDetail(selected.type === "template" ? (payload as TemplateDetail) : null);
        if (selected.type === "template") setCanvas("explorer");
        if (selected.type === "cluster" && cluster?.data?.template_ref) {
          try {
            const diff = await api.clusterValuesDiff(selected.site, selected.name);
            if (!cancelled) {
              setValuesDiff(diff);
              setCanvas("diff");
            }
          } catch (err) {
            if (!cancelled) setError(err instanceof Error ? err.message : String(err));
          }
        } else if (selected.type === "cluster") {
          setCanvas("graph");
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selection, compare]);

  const baseRef = effectiveRef(baseBranch, baseCommit, baseCustom);
  const headRef = effectiveRef(headBranch, headCommit, headCustom);

  useEffect(() => {
    if (!compare || !baseRef || !headRef) return;
    let cancelled = false;
    setDiffLoading(true);
    setDiffError(null);
    api
      .diff(baseRef, headRef, siteFilter)
      .then((payload) => {
        if (!cancelled) {
          setDiff(payload);
          const changed = payload.summary.added + payload.summary.removed + payload.summary.modified;
          setOnlyChanges(changed > 0);
          setDiffEntity((prev) => {
            if (!prev) return null;
            return payload.entities.find((item) => item.id === prev.id) ?? null;
          });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setDiff(null);
          setDiffError(err.message);
        }
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [compare, baseRef, headRef, siteFilter]);

  useEffect(() => {
    if (!compare || !baseRef || !headRef) {
      setFindingsDiff(null);
      return;
    }
    let cancelled = false;
    api
      .findingsDiff(baseRef, headRef, siteFilter)
      .then((payload) => {
        if (!cancelled) setFindingsDiff(payload);
      })
      .catch(() => {
        if (!cancelled) setFindingsDiff(null);
      });
    return () => {
      cancelled = true;
    };
  }, [compare, baseRef, headRef, siteFilter]);

  const devices = useMemo(() => {
    if (siteFilter === "all") return Object.values(devicesBySite).flat();
    return devicesBySite[siteFilter] ?? [];
  }, [devicesBySite, siteFilter]);

  const clusters = useMemo(() => {
    if (siteFilter === "all") return Object.values(clustersBySite).flat();
    return clustersBySite[siteFilter] ?? [];
  }, [clustersBySite, siteFilter]);

  const visibleTemplates = useMemo(() => {
    if (siteFilter === "all") return templates;
    const used = new Set(
      (clustersBySite[siteFilter] ?? [])
        .map((item) => item.template_ref)
        .filter((item): item is string => Boolean(item)),
    );
    return templates.filter(
      (item) => item.site === siteFilter || (item.scope === "global" && used.has(item.name)),
    );
  }, [templates, siteFilter, clustersBySite]);

  const graphKey =
    siteFilter === "all"
      ? view === "kubernetes"
        ? "all:kubernetes"
        : "all:global"
      : `${siteFilter}:${view}`;
  const liveGraph = graphs[graphKey] ?? null;
  const compareGraph =
    compare && diff
      ? siteFilter === "all" && view === "network"
        ? (diff.graphs.global ?? diff.graphs.network ?? null)
        : view === "kubernetes"
          ? (diff.graphs.kubernetes ?? null)
          : (diff.graphs.network ?? null)
      : null;
  const graph = compare ? compareGraph : liveGraph;
  const topology = siteFilter === "all" ? null : (topologies[siteFilter] ?? null);

  const selectedId = (() => {
    if (!selection) return null;
    if (selection.type === "device") return `${selection.site}:${selection.hostname}`;
    if (selection.type === "cluster") return `cluster:${selection.site}:${selection.name}`;
    if (selection.type === "template") return `template:${selection.site}:${selection.name}`;
    return `site:${selection.id}`;
  })();

  const entityByGraphId = (nodeId: string | null): DiffEntity | null => {
    if (!diff || !nodeId) return null;
    return (
      diff.entities.find((item) => item.graph_ids.includes(nodeId) || item.id === nodeId) ?? null
    );
  };

  const onGraphNode = (node: GraphNode) => {
    const siteIdFromNode =
      node.entity === "site" ? node.site || node.id.replace(/^site:/, "") : null;
    if (compare && diff) {
      const found = entityByGraphId(node.id);
      if (found) {
        setDiffEntity(found);
        setSelection(selectionFromEntity(found, siteFilter));
        if (found.kind === "site" || siteIdFromNode) {
          const siteId = found.kind === "site" ? found.site || found.id : siteIdFromNode;
          if (siteId && siteId !== "all") setSiteFilter(siteId);
        }
        return;
      }
    }
    setDiffEntity(null);
    if (siteIdFromNode) {
      setSiteFilter(siteIdFromNode);
      setSelection({ type: "site", id: siteIdFromNode });
      return;
    }
    if (node.entity === "template") {
      setSelection({ type: "template", site: node.site || "global", name: node.hostname });
      setView("kubernetes");
    } else if (node.entity === "cluster") {
      setSelection({ type: "cluster", site: node.site || siteFilter, name: node.hostname });
    } else if (node.hostname && node.site) {
      setSelection({ type: "device", site: node.site, hostname: node.hostname });
    }
  };

  const onDiffEntity = (entity: DiffEntity) => {
    setDiffEntity(entity);
    setSelection(selectionFromEntity(entity, siteFilter));
    if (entity.kind === "site") {
      const siteId = entity.site || entity.id;
      if (siteId && siteId !== "all") setSiteFilter(siteId);
    } else if (entity.site && entity.site !== "global" && siteFilter === "all") {
      setSiteFilter(entity.site);
    }
    if (entity.kind === "values" || entity.kind === "release" || entity.kind === "template" || entity.kind === "cluster") {
      setView("kubernetes");
      if (entity.kind === "values") setCanvas("diff");
    }
  };

  const onSelectDiffChild = (id: string) => {
    if (!diff) return;
    const found = diff.entities.find((item) => item.id === id);
    if (found) onDiffEntity(found);
  };

  const activeSite = sites.find((item) => item.id === siteFilter);
  const siteNames = useMemo(() => Object.fromEntries(sites.map((item) => [item.id, item.name])), [sites]);
  const siteIds = useMemo(() => sites.map((item) => item.id), [sites]);

  // Sitio al que pertenecen la campana y el panel: el filtro de sitio o, en Editar,
  // el sitio del wizard activo. "all" = vista global agrupada por sitio.
  const findingsScope: string = (() => {
    if (view !== "wizards") return siteFilter;
    if (wizardDraft.mode === "device") return wizardDraft.device.site || "all";
    if (wizardDraft.mode === "site") return wizardDraft.site.id || "all";
    return "all";
  })();
  const scopedFindings: FindingsPayload | null = (() => {
    if (compare && findingsDiff && view !== "wizards") return scopeFindings(findingsDiff.head, findingsScope);
    const current = view === "wizards" && wizardDraft.mode === "site" ? wizardDraft.site.preview?.findings?.current : null;
    if (current && findingsScope !== "all") {
      // Sitio nuevo aún sin commit: lo que reporta la vista previa del wizard.
      return { scope: findingsScope, counts: countFindings(current), findings: current };
    }
    return scopeFindings(findings, findingsScope);
  })();
  // «Corregir» en un hallazgo: entra en Editar con el wizard «Agregar device» precargado.
  const fixFinding = (finding: Finding) => {
    if (!editEnabled) return;
    const next = draftFromFinding(finding, wizardDraft);
    if (!next) return;
    if (
      isDraftInProgress(wizardDraft) &&
      !wizardDraft.device.fix &&
      !window.confirm("Hay un borrador del wizard en curso. ¿Reemplazarlo por la corrección de este hallazgo?")
    ) {
      return;
    }
    setWizardDraft(next);
    setWizardSeed((current) => ({ key: (current?.key ?? 0) + 1, draft: next }));
    setCompare(false);
    setView("wizards");
    setFindingsOpen(false);
  };

  const openEntityFromFinding = (action: OpenEntityRemediation) => {
    if (view === "wizards") {
      setView(lastBrowse.view);
    }
    setCompare(false);
    if (action.target === "site") {
      if (action.site) setSiteFilter(action.site);
      setSelection({ type: "site", id: action.name });
      setView("network");
      setCanvas("graph");
      return;
    }
    if (action.target === "device" && action.site) {
      setSiteFilter(action.site);
      setSelection({ type: "device", site: action.site, hostname: action.name });
      setView("network");
      setCanvas("graph");
      return;
    }
    if (action.target === "template") {
      setSelection({ type: "template", site: action.site || "global", name: action.name });
      setView("kubernetes");
      return;
    }
    if (action.site) {
      // cluster o values: la ficha del clúster abre el diff de values si tiene template.
      setSiteFilter(action.site);
      setSelection({ type: "cluster", site: action.site, name: action.name });
      setView("kubernetes");
      setCanvas(action.target === "values" ? "diff" : "graph");
    }
  };

  const openSiteFromFindings = (site: string) => {
    if (view === "wizards") {
      setView(lastBrowse.view);
      setCompare(lastBrowse.compare);
    }
    setSiteFilter(site);
    setSelection({ type: "site", id: site });
    setCanvas("graph");
  };

  const narrow = useMediaQuery("(max-width: 1100px)");
  const viewport = useViewport();
  const topbarRef = useRef<HTMLElement | null>(null);
  const [topbarHeight, setTopbarHeight] = useState(56);
  useEffect(() => {
    const node = topbarRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setTopbarHeight(Math.round(node.getBoundingClientRect().height)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const panelKey = (panel: string) => layoutKey(view, compare, narrow ? `narrow.${panel}` : panel);
  const sidebarStore = usePanelSize(panelKey("sidebar"), appPanelSpecs(viewport, narrow).sidebar);
  const detailStore = usePanelSize(panelKey("detail"), appPanelSpecs(viewport, narrow).detail);
  const specs = appPanelSpecs(viewport, narrow, { sidebar: sidebarStore.size, detail: detailStore.size, topbar: topbarHeight });
  const sidebar = { ...sidebarStore, size: clampSize(sidebarStore.size, specs.sidebar) };
  const detail = { ...detailStore, size: clampSize(detailStore.size, specs.detail) };
  const mainHeight = usePanelSize(panelKey("main-height"), specs.mainHeight);
  const findingsWidth = usePanelSize(panelKey("findings"), specs.findings);
  const appStyle = {
    "--sidebar-w": `${sidebar.size}px`,
    "--detail-w": `${detail.size}px`,
    "--main-h": `${mainHeight.size}px`,
    "--findings-w": `${findingsWidth.size}px`,
    "--topbar-h": `${topbarHeight}px`,
  } as CSSProperties;

  return (
    <div
      className={[
        "app",
        view === "wizards" ? "app-wizards" : "",
        sidebar.size === 0 ? "sidebar-collapsed" : "",
        !narrow && detail.size === 0 ? "detail-collapsed" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={appStyle}
    >
      <header className="topbar" ref={topbarRef}>
        <div>
          <p className="brand">ISoT</p>
          <p className="tagline">
            {siteFilter === "all"
              ? `${sites.length} sitios · vista global`
              : `${activeSite?.name ?? siteFilter} · ${activeSite?.description ?? ""}`}
          </p>
        </div>
        <label className="site-picker">
          <span>Sitio</span>
          <select
            id="site-filter"
            name="site-filter"
            value={siteFilter}
            onChange={(event) => {
              const next = event.target.value as SiteFilter;
              setSiteFilter(next);
              setSelection(null);
              setQuery("");
            }}
          >
            <option value="all">Todos (global)</option>
            {sites.map((site) => (
              <option key={site.id} value={site.id}>
                {site.name}
              </option>
            ))}
          </select>
        </label>
        <nav className="view-tabs">
          <button type="button" className={view === "network" ? "is-active" : ""} onClick={() => setView("network")}>
            {siteFilter === "all" ? "Sitios" : "Red"}
          </button>
          <button
            type="button"
            className={view === "kubernetes" ? "is-active" : ""}
            onClick={() => setView("kubernetes")}
          >
            Kubernetes
          </button>
        </nav>
        <nav className="view-tabs">
          <button
            type="button"
            className={compare ? "is-active" : ""}
            onClick={() => {
              setCanvas("graph");
              setDiffEntity(null);
              if (view === "wizards") {
                setView(lastBrowse.view);
                setCompare(true);
                setSiteFilter(lastBrowse.siteFilter);
                return;
              }
              setCompare((value) => !value);
            }}
          >
            Comparar
          </button>
        </nav>
        <FindingsBell
          counts={scopedFindings?.counts ?? { error: 0, warning: 0, info: 0 }}
          scopeLabel={findingsScope === "all" ? null : (siteNames[findingsScope] ?? findingsScope)}
          onClick={() => setFindingsOpen((value) => !value)}
          active={findingsOpen}
        />
        {editEnabled ? (
          <nav className="view-tabs">
            <button
              type="button"
              id="edit-toggle"
              className={view === "wizards" ? "is-active" : ""}
              aria-pressed={view === "wizards"}
              onClick={toggleEdit}
            >
              Editar
              {draftPending ? <span className="draft-pill">borrador</span> : null}
            </button>
          </nav>
        ) : null}
        {compare ? (
          <div className="ref-pickers">
            <RefPicker
              id="base-ref"
              label="Base"
              refs={refs}
              branch={baseBranch}
              commit={baseCommit}
              custom={baseCustom}
              resolvedSha={diff?.base.sha}
              onBranch={setBaseBranch}
              onCommit={setBaseCommit}
              onCustom={setBaseCustom}
            />
            <RefPicker
              id="head-ref"
              label="Head"
              refs={refs}
              branch={headBranch}
              commit={headCommit}
              custom={headCustom}
              resolvedSha={diff?.head.sha}
              onBranch={setHeadBranch}
              onCommit={setHeadCommit}
              onCustom={setHeadCustom}
            />
          </div>
        ) : null}
        {view === "kubernetes" || compare ? (
          <nav className="view-tabs canvas-tabs">
            <button
              type="button"
              className={canvas === "graph" ? "is-active" : ""}
              onClick={() => setCanvas("graph")}
            >
              Grafo
            </button>
            {!compare ? (
              <button
                type="button"
                className={canvas === "explorer" ? "is-active" : ""}
                disabled={!templateDetail}
                onClick={() => templateDetail && setCanvas("explorer")}
              >
                Explorador
              </button>
            ) : null}
            <button
              type="button"
              className={canvas === "diff" ? "is-active" : ""}
              disabled={compare ? !diff : !valuesDiff}
              onClick={() => (compare ? diff : valuesDiff) && setCanvas("diff")}
            >
              Diff values
            </button>
          </nav>
        ) : null}
        <p className="badge">{editEnabled ? "edición · wizards" : "solo lectura · Git-as-a-Database"}</p>
      </header>
      {bootError ? <p className="boot-error">No se pudieron leer los índices: {bootError}</p> : null}
      {editEnabled ? (
        <WizardsView
          hidden={view !== "wizards"}
          sites={sites}
          clustersBySite={clustersBySite}
          templates={templates}
          initialDraft={wizardDraft}
          onDraftChange={persistDraft}
          onClearDraft={clearDraft}
          seedDraft={wizardSeed?.draft ?? null}
          seedKey={wizardSeed?.key ?? 0}
          onOpenCompare={(base, head) => {
            setView("network");
            setSiteFilter("all");
            setCompare(true);
            setCanvas("graph");
            setBaseBranch(base);
            setBaseCommit("");
            setBaseCustom("");
            setHeadBranch(head);
            setHeadCommit("");
            setHeadCustom("");
          }}
        />
      ) : null}
      {view !== "wizards" ? (
        <>
      {compare && diffError ? <p className="boot-error">Diff: {diffError}</p> : null}
      {compare && diffLoading ? <p className="boot-error">Calculando diff…</p> : null}
      {compare && diff ? (
        <CompareSidebar
          diff={diff}
          view={view}
          siteFilter={siteFilter}
          query={query}
          selectedId={diffEntity?.id ?? selectedId}
          onlyChanges={onlyChanges}
          onQuery={setQuery}
          onSelect={onDiffEntity}
          onOnlyChanges={setOnlyChanges}
        />
      ) : (
      <Sidebar
        view={view}
        siteFilter={siteFilter}
        sites={sites}
        devices={devices}
        clusters={clusters}
        templates={visibleTemplates}
        query={query}
        selectedId={selectedId}
        onQuery={setQuery}
        onSelectSite={(id) => {
          setSiteFilter(id);
          setSelection({ type: "site", id });
        }}
        onSelectDevice={(site, hostname) => setSelection({ type: "device", site, hostname })}
        onSelectCluster={(site, name) => {
          setSelection({ type: "cluster", site, name });
          setView("kubernetes");
        }}
        onSelectTemplate={(site, name) => {
          setSelection({ type: "template", site, name });
          setView("kubernetes");
        }}
      />
      )}
      {compare && canvas === "diff" && diff ? (
        <CompareValues diff={diff} onSelectEntity={onDiffEntity} />
      ) : view === "kubernetes" && canvas === "explorer" && templateDetail ? (
        <TemplateExplorer
          template={templateDetail}
          onOpenCluster={(site, name) => {
            setSelection({ type: "cluster", site, name });
            setView("kubernetes");
          }}
        />
      ) : view === "kubernetes" && canvas === "diff" && valuesDiff ? (
        <ValuesDiffView
          diff={valuesDiff}
          onOpenTemplate={(site, name) => {
            setSelection({ type: "template", site, name });
            setView("kubernetes");
          }}
          onOpenCluster={(site, name) => setSelection({ type: "cluster", site, name })}
        />
      ) : (
        <TopologyGraph
          graph={graph}
          topology={topology}
          groupBy={groupBy}
          view={view}
          siteFilter={siteFilter}
          selectedId={selectedId}
          onSelectNode={onGraphNode}
          onGroupBy={setGroupBy}
          compare={compare}
          onlyChanges={onlyChanges}
          onOnlyChanges={setOnlyChanges}
          findings={scopedFindings?.findings ?? []}
        />
      )}
      <DetailPanel
        selection={selection}
        sites={sites}
        device={deviceDetail}
        cluster={clusterDetail}
        template={templateDetail}
        loading={loadingDetail}
        error={error}
        onOpenDevice={(site, hostname) => setSelection({ type: "device", site, hostname })}
        onOpenCluster={(site, name) => setSelection({ type: "cluster", site, name })}
        onOpenTemplate={(site, name) => setSelection({ type: "template", site, name })}
        onOpenSite={(id) => {
          setSiteFilter(id);
          setSelection({ type: "site", id });
        }}
        onShowExplorer={() => setCanvas("explorer")}
        onShowDiff={() => setCanvas("diff")}
        diffEntity={compare ? diffEntity : null}
        onSelectDiffEntity={onSelectDiffChild}
      />
      <Splitter
        id="split-sidebar"
        className="app-split app-split-sidebar"
        orientation="vertical"
        label="Ancho del menú lateral"
        size={sidebar.size}
        spec={specs.sidebar}
        sign={1}
        onSize={sidebar.setSize}
        onReset={sidebar.reset}
      />
      {narrow ? (
        <Splitter
          id="split-detail"
          className="app-split app-split-main-height"
          orientation="horizontal"
          label="Alto del grafo (resto: detalle)"
          size={mainHeight.size}
          spec={specs.mainHeight}
          sign={1}
          onSize={mainHeight.setSize}
          onReset={mainHeight.reset}
        />
      ) : (
        <Splitter
          id="split-detail"
          className="app-split app-split-detail"
          orientation="vertical"
          label="Ancho del panel de detalle"
          size={detail.size}
          spec={specs.detail}
          sign={-1}
          onSize={detail.setSize}
          onReset={detail.reset}
        />
      )}
        </>
      ) : null}
      {findingsOpen ? (
        <Splitter
          id="split-findings"
          className="app-split app-split-findings"
          orientation="vertical"
          label="Ancho del panel de hallazgos"
          size={findingsWidth.size}
          spec={specs.findings}
          sign={-1}
          onSize={findingsWidth.setSize}
          onReset={findingsWidth.reset}
          controls="findings-panel"
        />
      ) : null}
      <FindingsPanel
        payload={scopedFindings}
        scope={findingsScope}
        siteNames={siteNames}
        siteIds={siteIds}
        added={findingsDiff?.added}
        resolved={findingsDiff?.resolved}
        compare={compare && view !== "wizards"}
        onOpenSite={openSiteFromFindings}
        editEnabled={editEnabled}
        onFix={fixFinding}
        onOpenEntity={openEntityFromFinding}
        open={findingsOpen}
        onClose={() => setFindingsOpen(false)}
        onSelect={(finding: Finding) => {
          const next = selectionFromFinding(finding);
          if (!next) return;
          if (view === "wizards") setCompare(lastBrowse.compare);
          if (next.type === "cluster" && next.site && next.name) {
            setSelection({ type: "cluster", site: next.site, name: next.name });
            setView("kubernetes");
            if (next.site !== "all") setSiteFilter(next.site);
          } else if (next.type === "device" && next.site && next.hostname) {
            setSelection({ type: "device", site: next.site, hostname: next.hostname });
            setView("network");
            setSiteFilter(next.site);
          } else if (next.type === "template" && next.site && next.name) {
            setSelection({ type: "template", site: next.site, name: next.name });
            setView("kubernetes");
          } else if (next.type === "site" && next.id) {
            setSelection({ type: "site", id: next.id });
            setSiteFilter(next.id);
            setView("network");
          }
          setCanvas("graph");
        }}
      />
    </div>
  );
}

function pickDefaultRefs(payload: RefsPayload): { base: string; head: string } {
  const branches = payload.branches.map((item) => item.name);
  const prefer = (names: string[]): string =>
    names.find((name) => branches.includes(name)) || branches[0] || payload.commits[0]?.sha || "";
  const demo = prefer(["demo/diff-example", "origin/demo/diff-example", "cursor/demo-diff-example-9fbc"]);
  const main = prefer(["origin/main", "main", "master"]);
  const head = demo && demo !== main ? demo : branches.find((name) => name !== main) || main;
  return { base: main, head };
}

function selectionFromEntity(entity: DiffEntity, siteFilter: string): Selection {
  if (entity.kind === "site") return { type: "site", id: entity.site || entity.id };
  if (entity.kind === "cluster") {
    const [site, name] = entity.id.split(":");
    return { type: "cluster", site: site || siteFilter, name: name || entity.label };
  }
  if (entity.kind === "template") {
    const [site, name] = entity.id.split(":");
    return { type: "template", site: site || "global", name: name || entity.label };
  }
  if (entity.kind === "device") {
    const idx = entity.id.indexOf(":");
    return {
      type: "device",
      site: entity.site || entity.id.slice(0, idx),
      hostname: entity.id.slice(idx + 1),
    };
  }
  return { type: "site", id: entity.site || siteFilter || "site1" };
}
