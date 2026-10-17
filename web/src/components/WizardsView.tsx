import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import type {
  ClusterIndexEntry,
  ConnectionPolicy,
  ConnectionRule,
  GraphNode,
  GroupBy,
  SiteIndexEntry,
  SiteTemplateOption,
  TemplateIndexEntry,
  WizardCommit,
  WizardField,
  WizardPreview,
} from "../types";
import {
  DEFAULT_DOCK_WIDTH,
  clampDockWidth,
  MIN_DOCK_WIDTH,
  MIN_GRAPH_WIDTH,
  emptyWizardDraft,
  isDraftInProgress,
  type DeviceDraftState,
  type SiteDraftState,
  type WizardDraft,
  type WizardMode,
} from "../wizardDraft";
import { wizardCanCommit, wizardStatus } from "../findings";
import { LinkDetailView } from "./LinkDetail";
import { Splitter } from "./Splitter";
import { edgeSelection, fallbackDetail, relatedFindings } from "../links";
import { TopologyGraph } from "./TopologyGraph";
import type { PanelSpec } from "../resize";
import type { EdgeSelection, Finding, FixContext, LinkResponse } from "../types";

type Props = {
  sites: SiteIndexEntry[];
  clustersBySite: Record<string, ClusterIndexEntry[]>;
  templates: TemplateIndexEntry[];
  /** Plantillas de sitio (site-templates/ del repo de datos y templates/sites de ISoT). */
  siteTemplates?: SiteTemplateOption[];
  /** Tras un commit del wizard (p.ej. para refrescar rama/HEAD del repo de datos). */
  onAfterCommit?: (commit: WizardCommit) => void;
  onOpenCompare: (base: string, head: string) => void;
  initialDraft: WizardDraft;
  onDraftChange: (draft: WizardDraft) => void;
  onClearDraft: () => void;
  hidden?: boolean;
  /** Borrador a cargar (p. ej. desde «Corregir» en un hallazgo); cambia `seedKey` para aplicarlo. */
  seedDraft?: WizardDraft | null;
  seedKey?: number;
};

type Mode = WizardMode;
type DockTab = "fields" | "yaml";

const EDITABLE = /^(hostname|cluster_ref|oob\.(ip|port)|bonds\.[^.]+\.leaf_pc|networking\.[^.]+\.(ip|connected_to\.port))$/;

export function WizardsView({
  sites,
  clustersBySite,
  templates,
  siteTemplates = [],
  onAfterCommit,
  onOpenCompare,
  initialDraft,
  onDraftChange,
  onClearDraft,
  hidden = false,
  seedDraft = null,
  seedKey = 0,
}: Props) {
  const [epoch, setEpoch] = useState(0);
  const [lastCommit, setLastCommit] = useState<WizardCommit | null>(null);
  const [seed, setSeed] = useState<WizardDraft | null>(null);
  const appliedSeed = useRef(0);

  useEffect(() => {
    if (!seedKey || seedKey === appliedSeed.current || !seedDraft) return;
    appliedSeed.current = seedKey;
    setSeed(seedDraft);
    setLastCommit(null);
    setEpoch((value) => value + 1);
  }, [seedKey, seedDraft]);
  const blank = emptyWizardDraft({
    site: sites[0]?.id,
    cluster: clustersBySite[sites[0]?.id ?? ""]?.[0]?.name,
    templateSite: sites.find((item) => item.id === "site2")?.id ?? sites[0]?.id,
  });
  const initial = epoch === 0 ? initialDraft : (seed ?? blank);

  const discard = () => {
    if (
      !window.confirm(
        "¿Descartar el borrador del wizard? Se perderán el paso actual, los campos ingresados, las reglas y la vista previa.",
      )
    ) {
      return;
    }
    setLastCommit(null);
    setSeed(null);
    setEpoch((value) => value + 1);
    onClearDraft();
  };

  return (
    <section className="wizard-pane" hidden={hidden} aria-hidden={hidden}>
      <WizardForms
        key={epoch}
        sites={sites}
        clustersBySite={clustersBySite}
        templates={templates}
        siteTemplates={siteTemplates}
        onOpenCompare={onOpenCompare}
        initial={initial}
        onDraftChange={onDraftChange}
        onCommitted={(commit) => {
          setLastCommit(commit);
          setSeed(null);
          setEpoch((value) => value + 1);
          onClearDraft();
          onAfterCommit?.(commit);
        }}
        onDiscard={discard}
        lastCommit={lastCommit}
      />
    </section>
  );
}

function WizardForms({
  sites,
  clustersBySite,
  templates,
  siteTemplates,
  onOpenCompare,
  initial,
  onDraftChange,
  onCommitted,
  onDiscard,
  lastCommit,
}: {
  sites: SiteIndexEntry[];
  clustersBySite: Record<string, ClusterIndexEntry[]>;
  templates: TemplateIndexEntry[];
  siteTemplates: SiteTemplateOption[];
  onOpenCompare: (base: string, head: string) => void;
  initial: WizardDraft;
  onDraftChange: (draft: WizardDraft) => void;
  onCommitted: (commit: WizardCommit) => void;
  onDiscard: () => void;
  lastCommit: WizardCommit | null;
}) {
  const [mode, setMode] = useState<Mode>(initial.mode);
  const [deviceDraft, setDeviceDraft] = useState<DeviceDraftState>(initial.device);
  const [siteDraft, setSiteDraft] = useState<SiteDraftState>(initial.site);

  useEffect(() => {
    onDraftChange({ mode, device: deviceDraft, site: siteDraft });
  }, [mode, deviceDraft, siteDraft, onDraftChange]);

  const inProgress = isDraftInProgress({ mode, device: deviceDraft, site: siteDraft });

  return (
    <>
      <header className="graph-toolbar">
        <div>
          <h2>Wizards</h2>
          <p>
            Alta guiada. El commit vive en una rama <span className="mono">wizard/…</span>, nunca en main ni en la rama
            actual. El avance se conserva al salir de Editar y al recargar.
          </p>
        </div>
        <div className="wizard-header-actions">
          {inProgress ? (
            <button type="button" className="text-link" id="wizard-discard-draft" onClick={onDiscard}>
              Descartar borrador
            </button>
          ) : null}
          {mode !== "pick" ? (
            <button type="button" className="text-link" onClick={() => setMode("pick")}>
              ← elegir wizard
            </button>
          ) : null}
        </div>
      </header>
      {lastCommit ? <CommitBanner commit={lastCommit} onOpenCompare={onOpenCompare} /> : null}
      {mode === "pick" ? (
        <div className="wizard-cards">
          <button type="button" className="wizard-card" onClick={() => setMode("site")}>
            <strong>Nuevo sitio</strong>
            <span>Arquitectura, reglas de cableado, VLANs a partir de un sitio plantilla y DCI opcional.</span>
          </button>
          <button type="button" className="wizard-card" onClick={() => setMode("device")}>
            <strong>Agregar device</strong>
            <span>Hostname, IPs y puertos autoasignados a partir de los hermanos del mismo rol y tipo. Si no hay, patrón nuevo por convenio (NODE/CTRL/…).</span>
          </button>
        </div>
      ) : null}
      <div className="wizard-mode" hidden={mode !== "device"}>
        <DeviceWizard
          sites={sites}
          clustersBySite={clustersBySite}
          onOpenCompare={onOpenCompare}
          initial={initial.device}
          onChange={setDeviceDraft}
          onCommitted={onCommitted}
        />
      </div>
      <div className="wizard-mode" hidden={mode !== "site"}>
        <SiteWizard
          sites={sites}
          templates={templates}
          siteTemplates={siteTemplates}
          onOpenCompare={onOpenCompare}
          initial={initial.site}
          onChange={setSiteDraft}
          onCommitted={onCommitted}
        />
      </div>
    </>
  );
}

function DeviceWizard({
  sites,
  clustersBySite,
  onOpenCompare,
  initial,
  onChange,
  onCommitted,
}: {
  sites: SiteIndexEntry[];
  clustersBySite: Record<string, ClusterIndexEntry[]>;
  onOpenCompare: (base: string, head: string) => void;
  initial: DeviceDraftState;
  onChange: (draft: DeviceDraftState) => void;
  onCommitted: (commit: WizardCommit) => void;
}) {
  const [site, setSite] = useState(initial.site);
  const [kind, setKind] = useState(initial.kind);
  const [cluster, setCluster] = useState(initial.cluster);
  useEffect(() => {
    // El borrador puede venir de otro repositorio de datos: si el sitio no existe, se usa el primero.
    if (sites.length && !sites.some((item) => item.id === site)) {
      const first = sites[0].id;
      setSite(first);
      setCluster(clustersBySite[first]?.[0]?.name ?? "");
    }
  }, [sites, site, clustersBySite]);
  const [role, setRole] = useState(initial.role);
  const [count, setCount] = useState(initial.count);
  const [preview, setPreview] = useState<WizardPreview | null>(initial.preview);
  const [commit, setCommit] = useState<WizardCommit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>(initial.edits);
  const [neighborhood, setNeighborhood] = useState(initial.neighborhood);
  const [tab, setTab] = useState<DockTab>(initial.tab);
  const [saveRules, setSaveRules] = useState(initial.saveRules);
  const [acceptWarnings, setAcceptWarnings] = useState(false);
  const [fix, setFix] = useState<FixContext | null>(initial.fix ?? null);
  const [graphHidden, setGraphHidden] = useState(Boolean(initial.graphHidden));
  const [dockWidth, setDockWidth] = useState(initial.dockWidth ?? DEFAULT_DOCK_WIDTH);
  const skipPreview = useRef(Boolean(initial.preview));
  const clusters = clustersBySite[site] ?? [];

  useEffect(() => {
    onChange({
      site,
      kind,
      cluster,
      role,
      count,
      edits,
      saveRules,
      preview,
      neighborhood,
      tab,
      fix,
      graphHidden,
      dockWidth,
    });
  }, [site, kind, cluster, role, count, edits, saveRules, preview, neighborhood, tab, fix, graphHidden, dockWidth, onChange]);

  const body = useMemo(
    () => ({
      site,
      kind,
      cluster: cluster || null,
      role: role || null,
      count,
      fields: edits,
      save_inferred_rules: saveRules,
      accept_warnings: acceptWarnings,
      fix: fix?.finding ?? null,
    }),
    [site, kind, cluster, role, count, edits, saveRules, acceptWarnings, fix],
  );

  const run = async (action: "preview" | "commit") => {
    setError(null);
    setLoading(true);
    setCommit(null);
    try {
      const payload = action === "preview" ? await api.wizardDevicePreview(body) : await api.wizardDeviceCommit(body);
      if (action === "preview") setPreview(payload as WizardPreview);
      else {
        const done = payload as WizardCommit;
        setCommit(done);
        if (done.preview) setPreview(done.preview);
        onCommitted(done);
        return;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (skipPreview.current) {
      skipPreview.current = false;
      return;
    }
    if (!preview) return;
    const handle = window.setTimeout(() => {
      void run("preview");
    }, 400);
    return () => window.clearTimeout(handle);
    // Re-preview when the user edits auto fields or count/cluster after the first preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edits, count, cluster, site, kind, role, saveRules]);

  useEffect(() => {
    // Abierto desde «Corregir»: la vista previa sale sola (direccionamiento automático).
    if (initial.fix && !initial.preview) void run("preview");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={preview ? "wizard-workspace" : "wizard-form"}>
      <div className="wizard-form">
        <h3>Agregar device</h3>
        <WizardStatusBar preview={preview} fix={fix} onClearFix={() => setFix(null)} />
        <div className="wizard-grid">
          <label>
            Sitio
            <select
              value={site}
              onChange={(event) => {
                const next = event.target.value;
                setSite(next);
                setCluster(clustersBySite[next]?.[0]?.name ?? "");
                setPreview(null);
                setEdits({});
              }}
              id="wizard-device-site"
            >
              {sites.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Tipo
            <select value={kind} onChange={(event) => setKind(event.target.value)} id="wizard-device-kind">
              <option value="baremetal">baremetal</option>
              <option value="vm">vm</option>
              <option value="network">red</option>
            </select>
          </label>
          <label>
            Clúster
            <select value={cluster} onChange={(event) => setCluster(event.target.value)} id="wizard-device-cluster">
              <option value="">—</option>
              {clusters.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Rol
            <input id="wizard-device-role" value={role} onChange={(event) => setRole(event.target.value)} />
          </label>
          <label>
            Agregar N
            <input
              id="wizard-device-count"
              type="number"
              min={1}
              max={32}
              value={count}
              onChange={(event) => setCount(Number(event.target.value) || 1)}
            />
          </label>
        </div>
        {preview?.rules_source === "inferred" ? (
          <label className="check-row">
            <input
              id="wizard-save-inferred"
              type="checkbox"
              checked={saveRules}
              onChange={(event) => setSaveRules(event.target.checked)}
            />{" "}
            Guardar reglas inferidas en <span className="mono">topology/index.yaml</span>
          </label>
        ) : null}
        {preview?.rules_source === "declared" ? (
          <p className="muted">Este sitio ya declara <span className="mono">connection_rules</span>; el alta respeta la política.</p>
        ) : null}
        <div className="wizard-actions">
          <button type="button" onClick={() => void run("preview")} disabled={loading} id="wizard-device-preview">
            Vista previa
          </button>
          <button
            type="button"
            className="primary"
            id="wizard-device-commit"
            onClick={() => void run("commit")}
            disabled={loading || !preview || !wizardCanCommit(preview, acceptWarnings).ok}
          >
            Confirmar en rama wizard/…
          </button>
          <AcceptWarnings preview={preview} checked={acceptWarnings} onChange={setAcceptWarnings} />
        </div>
        {error ? <p className="boot-error">{error}</p> : null}
        <CommitBanner commit={commit} onOpenCompare={onOpenCompare} />
      </div>
      {preview ? (
        <PreviewStage
          preview={preview}
          neighborhood={neighborhood}
          onNeighborhood={setNeighborhood}
          tab={tab}
          onTab={setTab}
          edits={edits}
          onEdit={(key, value) => {
            setEdits((current) => {
              const next = { ...current };
              if (value === "") delete next[key];
              else next[key] = value;
              return next;
            });
          }}
          graphView="network"
          graphHidden={graphHidden}
          onGraphHidden={setGraphHidden}
          dockWidth={dockWidth}
          onDockWidth={setDockWidth}
        />
      ) : null}
    </div>
  );
}

function emptyRule(): ConnectionRule {
  return {
    id: "custom",
    roles: ["worker"],
    attach: "leaf-pair",
    leafs_per_node: 2,
    bond: "lacp",
    bond_name: "bondC",
    description: "",
  };
}

function SiteWizard({
  sites,
  templates,
  siteTemplates,
  onOpenCompare,
  initial,
  onChange,
  onCommitted,
}: {
  sites: SiteIndexEntry[];
  templates: TemplateIndexEntry[];
  siteTemplates: SiteTemplateOption[];
  onOpenCompare: (base: string, head: string) => void;
  initial: SiteDraftState;
  onChange: (draft: SiteDraftState) => void;
  onCommitted: (commit: WizardCommit) => void;
}) {
  const [step, setStep] = useState(initial.step);
  const [architecture, setArchitecture] = useState(initial.architecture);
  const [spines, setSpines] = useState(initial.spines);
  const [leafPairs, setLeafPairs] = useState(initial.leafPairs);
  const [unpairedLeafs, setUnpairedLeafs] = useState(initial.unpairedLeafs);
  const [mgmtSwitches, setMgmtSwitches] = useState(initial.mgmtSwitches);
  const [dcgwCount, setDcgwCount] = useState(initial.dcgwCount);
  const [portsPerLeaf, setPortsPerLeaf] = useState(initial.portsPerLeaf);
  const [pairPolicy, setPairPolicy] = useState(initial.pairPolicy);
  const [rules, setRules] = useState<ConnectionRule[]>(initial.rules);
  const [id, setId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [templateSite, setTemplateSite] = useState(
    initial.templateSite || sites.find((item) => item.id === "site2")?.id || sites[0]?.id || siteTemplates[0]?.id || "",
  );
  useEffect(() => {
    // Repo de datos vacío: en cuanto llegan las plantillas, se elige la primera.
    // También si el borrador apunta a un sitio que no existe en este repositorio.
    const known = sites.some((item) => item.id === templateSite) || siteTemplates.some((item) => item.id === templateSite);
    if (known || (!sites.length && !siteTemplates.length)) return;
    const fallback = sites.find((item) => item.id === "site2")?.id || sites[0]?.id || siteTemplates[0]?.id;
    if (fallback && fallback !== templateSite) setTemplateSite(fallback);
  }, [templateSite, sites, siteTemplates]);
  const [infraRange, setInfraRange] = useState(initial.infraRange);
  const [vlanOffset, setVlanOffset] = useState(initial.vlanOffset);
  const [templateRef, setTemplateRef] = useState(initial.templateRef);
  const [clusterName, setClusterName] = useState(initial.clusterName);
  const [workers, setWorkers] = useState(initial.workers);
  const [dciWanted, setDci] = useState(initial.dci);
  // Sin otros sitios en el repo de datos no hay contra quién levantar el DCI.
  const dciAvailable = sites.length > 0;
  const dci = dciWanted && dciAvailable;
  const [preview, setPreview] = useState<WizardPreview | null>(initial.preview);
  const [commit, setCommit] = useState<WizardCommit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [neighborhood, setNeighborhood] = useState(initial.neighborhood);
  const [tab, setTab] = useState<DockTab>(initial.tab);
  const [graphView, setGraphView] = useState<"network" | "global">(initial.graphView);
  const [graphHidden, setGraphHidden] = useState(Boolean(initial.graphHidden));
  const [dockWidth, setDockWidth] = useState(initial.dockWidth ?? DEFAULT_DOCK_WIDTH);
  const [acceptWarnings, setAcceptWarnings] = useState(false);
  const skipDefaults = useRef((initial.rules?.length ?? 0) > 0);

  const availableTemplates = useMemo(
    () =>
      templates.filter(
        (item) => item.scope === "global" || item.site === "global" || item.site === templateSite,
      ),
    [templates, templateSite],
  );

  useEffect(() => {
    if (skipDefaults.current) {
      skipDefaults.current = false;
      return;
    }
    let cancelled = false;
    api
      .wizardCablingDefaults(architecture)
      .then((policy) => {
        if (cancelled) return;
        const fabric = policy.fabric ?? {};
        setSpines(fabric.spines ?? 0);
        setLeafPairs(fabric.leaf_pairs ?? 0);
        setUnpairedLeafs(fabric.unpaired_leafs ?? 0);
        setMgmtSwitches(fabric.mgmt_switches ?? 0);
        setDcgwCount(fabric.dcgw ?? 0);
        setPortsPerLeaf(policy.ports_per_leaf ?? 48);
        setPairPolicy(policy.pair_policy ?? "fill-first");
        setRules(policy.rules ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [architecture]);

  useEffect(() => {
    onChange({
      step,
      architecture,
      spines,
      leafPairs,
      unpairedLeafs,
      mgmtSwitches,
      dcgwCount,
      portsPerLeaf,
      pairPolicy,
      rules,
      id,
      name,
      description,
      templateSite,
      infraRange,
      vlanOffset,
      templateRef,
      clusterName,
      workers,
      dci,
      preview,
      neighborhood,
      tab,
      graphView,
      graphHidden,
      dockWidth,
    });
  }, [
    step,
    architecture,
    spines,
    leafPairs,
    unpairedLeafs,
    mgmtSwitches,
    dcgwCount,
    portsPerLeaf,
    pairPolicy,
    rules,
    id,
    name,
    description,
    templateSite,
    infraRange,
    vlanOffset,
    templateRef,
    clusterName,
    workers,
    dci,
    preview,
    neighborhood,
    tab,
    graphView,
    graphHidden,
    dockWidth,
    onChange,
  ]);

  const connectionRules: ConnectionPolicy = {
    architecture,
    pair_policy: pairPolicy,
    ports_per_leaf: portsPerLeaf,
    fabric: {
      spines,
      leaf_pairs: leafPairs,
      unpaired_leafs: unpairedLeafs,
      mgmt_switches: mgmtSwitches,
      dcgw: dcgwCount,
    },
    rules,
  };

  const body = {
    id,
    name,
    description,
    architecture,
    spines,
    leaf_pairs: leafPairs,
    unpaired_leafs: unpairedLeafs,
    mgmt_switches: mgmtSwitches,
    dcgw: dcgwCount,
    ports_per_leaf: portsPerLeaf,
    pair_policy: pairPolicy,
    connection_rules: connectionRules,
    template_site: templateSite,
    infra_range: infraRange,
    vlan_offset: vlanOffset,
    dci,
    dci_peer: sites.find((item) => item.id === "site1")?.id ?? sites[0]?.id ?? "",
    clusters: [{ name: clusterName, template_ref: templateRef, workers, role: "worker" }],
    accept_warnings: acceptWarnings,
  };

  const run = async (action: "preview" | "commit") => {
    setError(null);
    setLoading(true);
    setCommit(null);
    try {
      const payload = action === "preview" ? await api.wizardSitePreview(body) : await api.wizardSiteCommit(body);
      if (action === "preview") setPreview(payload as WizardPreview);
      else {
        const done = payload as WizardCommit;
        setCommit(done);
        if (done.preview) setPreview(done.preview);
        onCommitted(done);
        return;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const collapsed = architecture === "collapsed-core";
  const custom = architecture === "custom";

  return (
    <div className={preview ? "wizard-workspace" : "wizard-form"}>
      <div className="wizard-form">
        <h3>Nuevo sitio · paso {step} / 5</h3>
        <WizardStatusBar preview={preview} fix={null} />
        {step === 1 ? (
          <div id="wizard-site-architecture">
            <p className="muted">Arquitectura de red del sitio. Las cantidades alimentan el fabric y los nodos.</p>
            <div className="arch-cards">
              {[
                { id: "spine-leaf", title: "Spine-Leaf", hint: "Spines + pares de leafs (MLAG/ESI). Por defecto." },
                { id: "collapsed-core", title: "Collapsed core", hint: "Un único par de leafs, sin spines." },
                { id: "custom", title: "Otra / Personalizada", hint: "Sin reglas predefinidas; se editan en el paso 4." },
              ].map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={architecture === item.id ? "arch-card is-active" : "arch-card"}
                  onClick={() => setArchitecture(item.id)}
                  id={`wizard-arch-${item.id}`}
                >
                  <strong>{item.title}</strong>
                  <span>{item.hint}</span>
                </button>
              ))}
            </div>
            <div className="wizard-grid">
              {collapsed ? null : (
                <label>
                  Spines
                  <input
                    id="wizard-arch-spines"
                    type="number"
                    min={0}
                    max={8}
                    value={spines}
                    onChange={(event) => setSpines(Number(event.target.value) || 0)}
                  />
                </label>
              )}
              <label>
                Pares de leafs
                <input
                  id="wizard-arch-pairs"
                  type="number"
                  min={0}
                  max={16}
                  value={leafPairs}
                  onChange={(event) => setLeafPairs(Number(event.target.value) || 0)}
                />
              </label>
              {custom ? (
                <label>
                  Leafs sueltos
                  <input
                    type="number"
                    min={0}
                    max={16}
                    value={unpairedLeafs}
                    onChange={(event) => setUnpairedLeafs(Number(event.target.value) || 0)}
                  />
                </label>
              ) : null}
              <label>
                Switch MGMT / OOB
                <input
                  type="number"
                  min={0}
                  max={4}
                  value={mgmtSwitches}
                  onChange={(event) => setMgmtSwitches(Number(event.target.value) || 0)}
                />
              </label>
              <label>
                DCGW / border
                <input
                  type="number"
                  min={0}
                  max={4}
                  value={dcgwCount}
                  onChange={(event) => setDcgwCount(Number(event.target.value) || 0)}
                />
              </label>
              <label>
                Cluster-template
                <select value={templateRef} onChange={(event) => setTemplateRef(event.target.value)}>
                  {availableTemplates.map((item) => (
                    <option key={`${item.site}:${item.name}`} value={item.name}>
                      {item.scope === "site" ? `${item.site}/` : "global/"}
                      {item.name}
                      {item.library ? " (biblioteca ISoT: se copia al repo)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Nombre del clúster
                <input value={clusterName} onChange={(event) => setClusterName(event.target.value)} />
              </label>
              <label>
                Nodos (rol worker)
                <input
                  id="wizard-arch-workers"
                  type="number"
                  min={1}
                  max={16}
                  value={workers}
                  onChange={(event) => setWorkers(Number(event.target.value) || 1)}
                />
              </label>
            </div>
          </div>
        ) : null}
        {step === 2 ? (
          <div className="wizard-grid">
            <label>
              id
              <input id="wizard-site-id" value={id} onChange={(event) => setId(event.target.value)} />
            </label>
            <label>
              Nombre
              <input id="wizard-site-name" value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="wide">
              Descripción
              <input value={description} onChange={(event) => setDescription(event.target.value)} />
            </label>
          </div>
        ) : null}
        {step === 3 ? (
          <div className="wizard-grid">
            <label>
              Sitio plantilla
              <select value={templateSite} onChange={(event) => setTemplateSite(event.target.value)} id="wizard-site-template">
                {sites.length ? (
                  <optgroup label="Sitios del repositorio">
                    {sites.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {siteTemplates.length ? (
                  <optgroup label="Plantillas">
                    {siteTemplates.map((item) => (
                      <option key={item.id} value={item.id} title={item.description ?? undefined}>
                        {item.name} ({item.source === "repo" ? "site-templates/ del repo" : "ISoT"})
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
            </label>
            <label>
              infra_range
              <input id="wizard-site-range" value={infraRange} onChange={(event) => setInfraRange(event.target.value)} />
            </label>
            <label>
              Offset de VLAN
              <input
                type="number"
                value={vlanOffset}
                onChange={(event) => setVlanOffset(Number(event.target.value) || 0)}
              />
            </label>
          </div>
        ) : null}
        {step === 4 ? (
          <div id="wizard-site-rules">
            <p className="muted">
              Política persistida en <span className="mono">sites/&lt;id&gt;/topology/index.yaml</span> como{" "}
              <span className="mono">connection_rules</span>. Se puede editar, quitar o agregar reglas por rol.
            </p>
            <div className="wizard-grid">
              <label>
                Asignación de pares
                <select
                  id="wizard-pair-policy"
                  value={pairPolicy}
                  onChange={(event) => setPairPolicy(event.target.value)}
                >
                  <option value="fill-first">llenar un par antes de pasar al siguiente</option>
                  <option value="round-robin">round-robin entre pares</option>
                </select>
              </label>
              <label>
                Puertos por leaf (modelo genérico)
                <input
                  type="number"
                  min={8}
                  max={128}
                  value={portsPerLeaf}
                  onChange={(event) => setPortsPerLeaf(Number(event.target.value) || 48)}
                />
              </label>
            </div>
            <div className="rule-list">
              {rules.map((rule, index) => (
                <div key={`${rule.id ?? "r"}-${index}`} className="rule-card">
                  <div className="wizard-grid">
                    <label>
                      id
                      <input
                        value={rule.id ?? ""}
                        onChange={(event) =>
                          setRules((current) =>
                            current.map((item, idx) => (idx === index ? { ...item, id: event.target.value } : item)),
                          )
                        }
                      />
                    </label>
                    <label>
                      roles
                      <input
                        value={(rule.roles ?? []).join(", ")}
                        onChange={(event) =>
                          setRules((current) =>
                            current.map((item, idx) =>
                              idx === index
                                ? {
                                    ...item,
                                    roles: event.target.value
                                      .split(",")
                                      .map((part) => part.trim())
                                      .filter(Boolean),
                                  }
                                : item,
                            ),
                          )
                        }
                      />
                    </label>
                    <label>
                      attach
                      <select
                        value={rule.attach ?? "leaf-pair"}
                        onChange={(event) =>
                          setRules((current) =>
                            current.map((item, idx) => (idx === index ? { ...item, attach: event.target.value } : item)),
                          )
                        }
                      >
                        <option value="leaf-pair">leaf-pair (MLAG/ESI)</option>
                        <option value="all-spines">all-spines</option>
                        <option value="mgmt">mgmt / OOB</option>
                      </select>
                    </label>
                    {rule.attach === "leaf-pair" ? (
                      <>
                        <label>
                          leafs por nodo
                          <input
                            type="number"
                            min={1}
                            max={4}
                            value={rule.leafs_per_node ?? 2}
                            onChange={(event) =>
                              setRules((current) =>
                                current.map((item, idx) =>
                                  idx === index ? { ...item, leafs_per_node: Number(event.target.value) || 2 } : item,
                                ),
                              )
                            }
                          />
                        </label>
                        <label>
                          bond
                          <input
                            value={rule.bond_name ?? ""}
                            onChange={(event) =>
                              setRules((current) =>
                                current.map((item, idx) =>
                                  idx === index ? { ...item, bond_name: event.target.value, bond: "lacp" } : item,
                                ),
                              )
                            }
                          />
                        </label>
                      </>
                    ) : null}
                    <label className="wide">
                      descripción
                      <input
                        value={rule.description ?? ""}
                        onChange={(event) =>
                          setRules((current) =>
                            current.map((item, idx) =>
                              idx === index ? { ...item, description: event.target.value } : item,
                            ),
                          )
                        }
                      />
                    </label>
                  </div>
                  <button
                    type="button"
                    className="text-link"
                    onClick={() => setRules((current) => current.filter((_, idx) => idx !== index))}
                  >
                    Quitar regla
                  </button>
                </div>
              ))}
            </div>
            <button type="button" onClick={() => setRules((current) => [...current, emptyRule()])} id="wizard-add-rule">
              Agregar regla
            </button>
          </div>
        ) : null}
        {step === 5 ? (
          <label className="check-row">
            <input
              type="checkbox"
              id="wizard-site-dci"
              checked={dci}
              disabled={!dciAvailable}
              onChange={(event) => setDci(event.target.checked)}
            />{" "}
            Enlace DCI (address_space dci-overlay)
            {dciAvailable ? null : <span className="muted"> · no hay otros sitios en el repositorio</span>}
          </label>
        ) : null}
        <div className="wizard-actions">
          <button type="button" disabled={step === 1} onClick={() => setStep((value) => value - 1)}>
            Atrás
          </button>
          {step < 5 ? (
            <button type="button" onClick={() => setStep((value) => value + 1)} id="wizard-site-next">
              Siguiente
            </button>
          ) : (
            <>
              <button type="button" onClick={() => void run("preview")} disabled={loading} id="wizard-site-preview">
                Vista previa
              </button>
              <button
                type="button"
                className="primary"
                id="wizard-site-commit"
                onClick={() => void run("commit")}
                disabled={loading || !preview || !wizardCanCommit(preview, acceptWarnings).ok}
              >
                Confirmar en rama wizard/…
              </button>
              <AcceptWarnings preview={preview} checked={acceptWarnings} onChange={setAcceptWarnings} />
            </>
          )}
        </div>
        {error ? <p className="boot-error">{error}</p> : null}
        <CommitBanner commit={commit} onOpenCompare={onOpenCompare} />
      </div>
      {preview ? (
        <PreviewStage
          preview={preview}
          neighborhood={neighborhood}
          onNeighborhood={setNeighborhood}
          tab={tab}
          onTab={setTab}
          graphView={graphView}
          onGraphView={setGraphView}
          defaultGroupBy="pair"
          graphHidden={graphHidden}
          onGraphHidden={setGraphHidden}
          dockWidth={dockWidth}
          onDockWidth={setDockWidth}
        />
      ) : null}
    </div>
  );
}

function PreviewStage({
  preview,
  neighborhood,
  onNeighborhood,
  tab,
  onTab,
  edits,
  onEdit,
  graphView,
  onGraphView,
  defaultGroupBy = "cluster",
  graphHidden = false,
  onGraphHidden,
  dockWidth = DEFAULT_DOCK_WIDTH,
  onDockWidth,
}: {
  preview: WizardPreview;
  neighborhood: boolean;
  onNeighborhood: (value: boolean) => void;
  tab: DockTab;
  onTab: (value: DockTab) => void;
  edits?: Record<string, string>;
  onEdit?: (key: string, value: string) => void;
  graphView: "network" | "global";
  onGraphView?: (value: "network" | "global") => void;
  defaultGroupBy?: GroupBy;
  graphHidden?: boolean;
  onGraphHidden?: (value: boolean) => void;
  dockWidth?: number;
  onDockWidth?: (value: number) => void;
}) {
  const [groupBy, setGroupBy] = useState<GroupBy>(defaultGroupBy);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [stageWidth, setStageWidth] = useState(0);
  useEffect(() => {
    const node = stageRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setStageWidth(node.getBoundingClientRect().width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const dockSpec: PanelSpec = {
    defaultSize: DEFAULT_DOCK_WIDTH,
    min: MIN_DOCK_WIDTH,
    max: stageWidth ? Math.max(MIN_DOCK_WIDTH, stageWidth - MIN_GRAPH_WIDTH) : 2000,
  };
  const [edge, setEdge] = useState<EdgeSelection | null>(null);
  const [edgeResponse, setEdgeResponse] = useState<LinkResponse | null>(null);
  const [edgeLoading, setEdgeLoading] = useState(false);
  const [edgeError, setEdgeError] = useState<string | null>(null);
  useEffect(() => setEdge(null), [preview, graphView]);
  useEffect(() => {
    if (!edge) {
      setEdgeResponse(null);
      setEdgeError(null);
      return;
    }
    let cancelled = false;
    setEdgeLoading(true);
    setEdgeError(null);
    setEdgeResponse(null);
    const files = (preview.files ?? []).map((file) => ({ path: file.path, content: file.content }));
    api
      .linkPreview(edge.source, edge.target, files)
      .then((payload) => {
        if (!cancelled) setEdgeResponse(payload);
      })
      .catch((err: Error) => {
        if (!cancelled) setEdgeError(err.message);
      })
      .finally(() => {
        if (!cancelled) setEdgeLoading(false);
      });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setEdge(null);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKey);
    };
  }, [edge, preview.files]);
  const graph = graphView === "global" ? preview.graphs?.global ?? null : preview.graphs?.network ?? null;
  const siteFilter = graphView === "global" ? "all" : (preview.site ?? "all");
  const focusIds = graphView === "global" && preview.site ? [`site:${preview.site}`] : (preview.focus_ids ?? []);

  return (
    <div
      ref={stageRef}
      className={`wizard-stage${graphHidden ? " graph-hidden" : ""}`}
      id="wizard-graph"
      style={graphHidden ? undefined : { gridTemplateColumns: `minmax(0, 1fr) 10px min(${dockWidth}px, 70%)` }}
    >
      {graphHidden ? null : (
      <div className="wizard-graph-wrap">
        {preview.kind === "site" && onGraphView ? (
          <nav className="view-tabs">
            <button type="button" className={graphView === "network" ? "is-active" : ""} onClick={() => onGraphView("network")}>
              Sitio
            </button>
            <button
              type="button"
              className={graphView === "global" ? "is-active" : ""}
              id="wizard-site-graph-global"
              onClick={() => onGraphView("global")}
            >
              Global
            </button>
          </nav>
        ) : null}
        <TopologyGraph
          graph={graph}
          topology={preview.topology ?? null}
          groupBy={groupBy}
          view="network"
          siteFilter={siteFilter}
          selectedId={focusIds[0] ?? null}
          onSelectNode={(_node: GraphNode) => undefined}
          onGroupBy={setGroupBy}
          compare
          onlyChanges={false}
          focusIds={focusIds}
          neighborhood={neighborhood}
          onNeighborhood={onNeighborhood}
          findings={preview.findings?.added ?? preview.findings?.current ?? []}
          selectedEdgeId={edge?.id ?? null}
          onSelectEdge={(item) => setEdge(edgeSelection(item))}
          onClearEdge={() => setEdge(null)}
        />
      </div>
      )}
      {graphHidden ? null : (
        <Splitter
          id="wizard-divider"
          className="wizard-divider"
          orientation="vertical"
          label="Ancho del panel Campos / YAML"
          size={stageWidth ? clampDockWidth(dockWidth, stageWidth) : dockWidth}
          spec={dockSpec}
          sign={-1}
          onSize={(value) => onDockWidth?.(stageWidth ? clampDockWidth(value, stageWidth) : value)}
        />
      )}
      <aside className="wizard-dock">
        <nav className="view-tabs dock-tabs">
          <button
            type="button"
            id="wizard-preview-tab-fields"
            className={tab === "fields" && !edge ? "is-active" : ""}
            onClick={() => {
              setEdge(null);
              onTab("fields");
            }}
          >
            Campos
          </button>
          <button
            type="button"
            id="wizard-preview-tab-yaml"
            className={tab === "yaml" && !edge ? "is-active" : ""}
            onClick={() => {
              setEdge(null);
              onTab("yaml");
            }}
          >
            YAML
          </button>
          {edge ? (
            <button type="button" id="wizard-preview-tab-link" className="is-active">
              Conexión
            </button>
          ) : null}
          {onGraphHidden ? (
            <button
              type="button"
              id="wizard-toggle-graph"
              className="graph-toggle"
              aria-pressed={graphHidden}
              onClick={() => onGraphHidden(!graphHidden)}
            >
              {graphHidden ? "Mostrar grafo" : "Ocultar grafo"}
            </button>
          ) : null}
        </nav>
        {edge ? (
          <LinkDetailView
            edge={edge}
            response={edgeResponse}
            fallback={
              edgeError
                ? fallbackDetail(edge, (id) => graph?.nodes.find((node) => node.id === id)?.hostname ?? id.split(":").pop() ?? id)
                : null
            }
            loading={edgeLoading}
            error={edgeError}
            findings={relatedFindings(edgeResponse?.detail ?? null, preview.findings?.added ?? preview.findings?.current ?? [])}
            onClose={() => setEdge(null)}
          />
        ) : tab === "fields" ? (
          <FieldsTable preview={preview} edits={edits} onEdit={onEdit} />
        ) : (
          <YamlDiff preview={preview} />
        )}
      </aside>
    </div>
  );
}

function FieldsTable({
  preview,
  edits,
  onEdit,
}: {
  preview: WizardPreview;
  edits?: Record<string, string>;
  onEdit?: (key: string, value: string) => void;
}) {
  return (
    <div className="wizard-preview">
      {preview.hostnames?.length ? <p className="mono">hostnames: {preview.hostnames.join(", ")}</p> : null}
      {preview.pattern_note ? (
        <p className="wizard-pattern-note" id="wizard-pattern-note">
          {preview.pattern_note}
        </p>
      ) : null}
      {preview.sibling ? <p className="muted">Hermano: {preview.sibling}</p> : null}
      {(preview.warnings ?? []).map((item) => (
        <p key={item} className="wizard-warn">
          {item}
        </p>
      ))}
      {preview.rules_source === "inferred" ? (
        <p className="muted">Reglas inferidas de hermanos (aún no guardadas en topology).</p>
      ) : null}
      <table className="wizard-fields">
        <thead>
          <tr>
            <th>Campo</th>
            <th>Valor</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(preview.fields ?? []).map((field) => (
            <FieldRow key={fieldKey(field)} field={field} edits={edits} onEdit={onEdit} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fieldKey(field: WizardField): string {
  return field.hostname ? `${field.hostname}::${field.path}` : field.path;
}

function FieldRow({
  field,
  edits,
  onEdit,
}: {
  field: WizardField;
  edits?: Record<string, string>;
  onEdit?: (key: string, value: string) => void;
}) {
  const key = fieldKey(field);
  const editable = Boolean(onEdit) && EDITABLE.test(field.path);
  const display = edits && key in edits ? edits[key] : String(field.value ?? "");
  return (
    <tr>
      <td className="mono">
        {field.hostname && previewNeedsHost(field) ? `${field.hostname}.` : ""}
        {field.path}
      </td>
      <td className="mono">
        {editable ? (
          <input
            value={display}
            onChange={(event) => onEdit?.(key, event.target.value)}
            aria-label={key}
          />
        ) : (
          display
        )}
      </td>
      <td>
        {field.auto && !(edits && key in edits) ? (
          <span className="auto-tag" title={field.origin}>
            auto
          </span>
        ) : (
          <span className="muted">{edits && key in edits ? "manual" : field.auto ? "auto" : "manual"}</span>
        )}
      </td>
    </tr>
  );
}

function previewNeedsHost(field: WizardField): boolean {
  return Boolean(field.hostname) && field.path !== "hostname";
}

function YamlDiff({ preview }: { preview: WizardPreview }) {
  return (
    <div className="wizard-preview">
      {(preview.files ?? []).map((file) => (
        <details key={file.path} open={file.action === "create"}>
          <summary>
            <span className="mono">{file.path}</span> · {file.action}
          </summary>
          <pre className="wizard-diff">{file.diff || file.content}</pre>
        </details>
      ))}
    </div>
  );
}

function AcceptWarnings({
  preview,
  checked,
  onChange,
}: {
  preview: WizardPreview | null;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  const warnings = (preview?.findings?.added ?? []).filter((item) => item.severity === "warning").length;
  if (!warnings) return null;
  return (
    <label className="check-row accept-warnings" htmlFor="wizard-accept-warnings">
      <input
        id="wizard-accept-warnings"
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      Acepto {warnings} {warnings === 1 ? "aviso nuevo" : "avisos nuevos"}
    </label>
  );
}

/** Una línea: corrección + resultado + avisos nuevos; los detalles se despliegan con un clic. */
function WizardStatusBar({
  preview,
  fix,
  onClearFix,
}: {
  preview: WizardPreview | null;
  fix: FixContext | null;
  onClearFix?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const status = wizardStatus(preview, fix);
  if (!status) return null;
  const errors = status.added.filter((item) => item.severity === "error");
  const warnings = status.added.filter((item) => item.severity === "warning");
  const infos = status.added.filter((item) => item.severity === "info");
  return (
    <div className="wizard-status" id="wizard-status">
      <button
        type="button"
        className="wizard-status-line"
        id="wizard-status-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title={open ? "Ocultar detalles" : "Ver detalles"}
      >
        {status.fix && fix ? (
          <span className="ws-part ws-fix" id="wizard-fix-banner" title={status.fix}>
            <span className={`sev-pill sev-${fix.finding.severity}`}>{fix.finding.severity}</span>
            <span className="ws-text">{status.fix}</span>
          </span>
        ) : null}
        {status.resolve ? (
          <span className={`ws-part ${status.resolve.ok ? "ws-ok" : "ws-pending"}`} id="wizard-fix-result">
            {status.resolve.text}
          </span>
        ) : null}
        {status.errors ? (
          <span className="ws-part ws-error" id="wizard-findings-block">
            ✖ {status.errors} {status.errors === 1 ? "error bloquea" : "errores bloquean"} Confirmar
          </span>
        ) : null}
        {status.warnings ? (
          <span className="ws-part ws-warn">
            ⚠ {status.warnings} {status.warnings === 1 ? "aviso nuevo" : "avisos nuevos"}
          </span>
        ) : null}
        <span className="ws-more">{open ? "▴" : "▾ detalles"}</span>
      </button>
      {open ? (
        <div className="wizard-status-details" id="wizard-status-details">
          {fix ? (
            <div className="ws-detail-fix">
              <p>
                <strong>{fix.finding.id}</strong> <span className="mono">{fix.finding.entity_id}</span> — {fix.finding.message}
              </p>
              <p className="muted">
                Propuesta: {fix.remediation.label} ({fix.remediation.reason})
                {fix.remediation.count > 1 ? ` · se agregan los ${fix.remediation.count} en este mismo alta` : ""}
              </p>
              {fix.remediation.followup ? <p className="muted">{fix.remediation.followup}</p> : null}
              {onClearFix ? (
                <button type="button" className="text-link" onClick={onClearFix}>
                  Quitar corrección
                </button>
              ) : null}
            </div>
          ) : null}
          {status.added.length ? (
            <ul className="findings-list compact" id="wizard-findings">
              {[...errors, ...warnings, ...infos].map((item: Finding, index) => (
                <li key={`${item.id}:${item.entity_id}:${index}`}>
                  <span className={`sev-pill sev-${item.severity}`}>{item.severity}</span> <strong>{item.id}</strong>{" "}
                  {item.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function CommitBanner({
  commit,
  onOpenCompare,
}: {
  commit: WizardCommit | null;
  onOpenCompare: (base: string, head: string) => void;
}) {
  if (!commit) return null;
  return (
    <div className="wizard-success">
      Commit {commit.short} en <span className="mono">{commit.branch}</span> (base {commit.base}).
      <button type="button" className="text-link" onClick={() => onOpenCompare(commit.base, commit.branch)}>
        Abrir Comparar
      </button>
      {commit.push ? (
        <span className={commit.push.ok ? "wizard-push ok" : "wizard-push error"} id="wizard-push-result">
          {commit.push.ok
            ? ` Publicada en ${commit.push.remote}.`
            : ` No se pudo publicar en ${commit.push.remote}: ${commit.push.message || "git push falló"}`}
        </span>
      ) : null}
    </div>
  );
}
