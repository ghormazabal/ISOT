import { useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { api } from "../api";
import type { ChartMeta, ChartValues, TemplateDetail } from "../types";
import { ValuesTree, valuesToTree } from "./ValuesTree";

type Props = {
  template: TemplateDetail;
  onOpenCluster: (site: string, name: string) => void;
};

type SectionId = "distro" | "cni" | "pools" | "addons" | "charts";

const SECTION_LABEL: Record<SectionId, string> = {
  distro: "Distribución",
  cni: "CNI",
  pools: "Node pools",
  addons: "Add-ons",
  charts: "Charts / Helm",
};

export function TemplateExplorer({ template, onOpenCluster }: Props) {
  const data = template.data;
  const charts = template.charts ?? [];
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    distro: true,
    cni: true,
    pools: false,
    addons: false,
    charts: true,
  });
  const [openCharts, setOpenCharts] = useState<Record<string, boolean>>({});
  const [values, setValues] = useState<Record<string, ChartValues>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [activeChart, setActiveChart] = useState<string | null>(charts[0]?.name ?? null);

  const cni = (data.cni as Record<string, unknown> | undefined) ?? {};
  const pools = (data.node_pools as Record<string, unknown> | undefined) ?? {};
  const addons = (data.addons_default as Record<string, unknown> | undefined) ?? {};

  const loadChart = async (release: string) => {
    setLoading((prev) => ({ ...prev, [release]: true }));
    try {
      const payload = await api.templateValues(template.site, template.name, release);
      setValues((prev) => ({ ...prev, [release]: payload }));
    } catch (err) {
      setErrors((prev) => ({
        ...prev,
        [release]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setLoading((prev) => ({ ...prev, [release]: false }));
    }
  };

  useEffect(() => {
    const first = template.charts[0]?.name ?? null;
    setValues({});
    setErrors({});
    setOpenCharts(first ? { [first]: true } : {});
    setQuery("");
    setActiveChart(first);
    if (!first) return;
    let cancelled = false;
    setLoading({ [first]: true });
    api
      .templateValues(template.site, template.name, first)
      .then((payload) => {
        if (!cancelled) setValues({ [first]: payload });
      })
      .catch((err: Error) => {
        if (!cancelled) setErrors({ [first]: err.message });
      })
      .finally(() => {
        if (!cancelled) setLoading({});
      });
    return () => {
      cancelled = true;
    };
  }, [template.site, template.name]);

  const toggleSection = (id: SectionId) => {
    setOpenSections((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleChart = (name: string) => {
    setOpenCharts((prev) => ({ ...prev, [name]: true }));
    setActiveChart(name);
    if (!values[name]) void loadChart(name);
  };

  const graph = useMemo(() => buildExplorerGraph(template.name, charts, activeChart), [template.name, charts, activeChart]);

  const onNodeClick: NodeMouseHandler = (_event, node) => {
    const id = String(node.id);
    if (id === "chart-others") {
      setOpenSections((prev) => ({ ...prev, charts: true }));
      return;
    }
    if (id.startsWith("chart:")) {
      const name = id.slice("chart:".length);
      setActiveChart(name);
      setOpenCharts((prev) => ({ ...prev, [name]: true }));
      setOpenSections((prev) => ({ ...prev, charts: true }));
      if (!values[name]) void loadChart(name);
      return;
    }
    if ((["distro", "cni", "pools", "addons", "charts"] as SectionId[]).includes(id as SectionId)) {
      toggleSection(id as SectionId);
    }
  };

  const active = charts.find((item) => item.name === activeChart) ?? null;
  const activeValues = activeChart ? values[activeChart] : undefined;
  const valueNodes = activeValues ? valuesToTree(activeValues.values) : [];

  return (
    <section className="explorer-pane">
      <header className="graph-toolbar">
        <div>
          <h2>Explorador del template</h2>
          <p className="mono">
            {template.path} · {charts.length} charts
          </p>
        </div>
        <label className="search path-search">
          <span>Ruta de values</span>
          <input
            id="values-path-search"
            name="values-path-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="p.ej. cniConfig.podCIDR"
            autoComplete="off"
          />
        </label>
      </header>
      <div className="explorer-graph">
        <ReactFlow
          nodes={graph.nodes}
          edges={graph.edges}
          onNodeClick={onNodeClick}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={18} color="#24303a" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="explorer-body">
        <div className="explorer-tree-pane">
          <ul className="explorer-tree">
            <li>
              <span className="tree-root">{template.name}</span>
              <ul>
                <Section
                  id="distro"
                  open={openSections.distro}
                  onToggle={toggleSection}
                  facts={[
                    ["distro", str(data.distro)],
                    ["version", str(data.version)],
                    ["kubernetes", str(data.kubernetes_version)],
                  ]}
                />
                <Section
                  id="cni"
                  open={openSections.cni}
                  onToggle={toggleSection}
                  facts={[
                    ["primary", str(cni.primary)],
                    ...objectFacts((cni.defaults as Record<string, unknown> | undefined) ?? {}, "defaults"),
                  ]}
                />
                <Section
                  id="pools"
                  open={openSections.pools}
                  onToggle={toggleSection}
                  facts={objectFacts(pools)}
                />
                <Section
                  id="addons"
                  open={openSections.addons}
                  onToggle={toggleSection}
                  facts={flattenAddonFacts(addons)}
                />
                <li>
                  <button type="button" className="tree-section" onClick={() => toggleSection("charts")}>
                    {openSections.charts ? "▾" : "▸"} {SECTION_LABEL.charts}
                    <span className="muted"> {charts.length}</span>
                  </button>
                  {openSections.charts ? (
                    <ul>
                      {charts.map((chart) => (
                        <li key={chart.name}>
                          <button
                            type="button"
                            className={`tree-chart${activeChart === chart.name ? " is-active" : ""}`}
                            onClick={() => toggleChart(chart.name)}
                          >
                            {openCharts[chart.name] ? "▾" : "▸"} {chart.name}
                            <span className="muted">
                              {" "}
                              {chart.version || "versión faltante"} · {chart.values_source}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              </ul>
            </li>
          </ul>
          {template.used_by.length > 0 ? (
            <p className="explorer-used">
              Usado por{" "}
              {template.used_by.map((item, index) => {
                const idx = item.indexOf("/");
                const site = idx === -1 ? template.site : item.slice(0, idx);
                const name = idx === -1 ? item : item.slice(idx + 1);
                return (
                  <span key={item}>
                    {index > 0 ? ", " : null}
                    <button type="button" className="text-link" onClick={() => onOpenCluster(site, name)}>
                      {item}
                    </button>
                  </span>
                );
              })}
            </p>
          ) : (
            <p className="muted">Ningún clúster referencia este template.</p>
          )}
        </div>
        <div className="explorer-values">
          {active ? (
            <>
              <h3>
                Chart <span className="mono">{active.name}</span>
              </h3>
              <dl className="facts">
                <div>
                  <dt>chart</dt>
                  <dd className="mono">{active.chart}</dd>
                </div>
                <div>
                  <dt>repo</dt>
                  <dd className="mono">{active.repo || "faltante"}</dd>
                </div>
                <div>
                  <dt>versión</dt>
                  <dd className="mono">{active.version || "faltante"}</dd>
                </div>
                <div>
                  <dt>namespace</dt>
                  <dd className="mono">{active.namespace || "faltante"}</dd>
                </div>
                <div>
                  <dt>orden</dt>
                  <dd>{active.order ?? "—"}</dd>
                </div>
                <div>
                  <dt>depends_on</dt>
                  <dd className="mono">{active.depends_on.length ? active.depends_on.join(", ") : "—"}</dd>
                </div>
                <div>
                  <dt>values</dt>
                  <dd className="mono">
                    {active.values_source === "ref"
                      ? active.values_ref
                      : active.values_source === "inline"
                        ? "inline"
                        : "faltante"}
                  </dd>
                </div>
              </dl>
              {active.note ? <p className="muted">{active.note}</p> : null}
              {loading[active.name] ? <p className="muted">Cargando values…</p> : null}
              {errors[active.name] ? <p className="error">{errors[active.name]}</p> : null}
              {activeValues?.missing ? (
                <p className="missing-flag">values faltantes (ni values_ref ni values_inline)</p>
              ) : (
                <ValuesTree
                  nodes={valueNodes}
                  query={query}
                  defaultOpen
                  emptyLabel={activeValues ? "values vacíos" : "expande el chart para cargar values"}
                />
              )}
            </>
          ) : (
            <p className="placeholder">Selecciona un chart en el grafo o el árbol.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function Section({
  id,
  open,
  onToggle,
  facts,
}: {
  id: SectionId;
  open: boolean;
  onToggle: (id: SectionId) => void;
  facts: Array<[string, string]>;
}) {
  return (
    <li>
      <button type="button" className="tree-section" onClick={() => onToggle(id)}>
        {open ? "▾" : "▸"} {SECTION_LABEL[id]}
      </button>
      {open ? (
        <ul className="tree-facts">
          {facts.length === 0 ? (
            <li className="muted">faltante</li>
          ) : (
            facts.map(([key, value]) => (
              <li key={key}>
                <span className="mono">{key}</span> {value || "faltante"}
              </li>
            ))
          )}
        </ul>
      ) : null}
    </li>
  );
}

function str(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function objectFacts(obj: Record<string, unknown>, prefix = ""): Array<[string, string]> {
  return Object.entries(obj).map(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return [path, JSON.stringify(value)];
    }
    return [path, Array.isArray(value) ? value.map(String).join(", ") : str(value)];
  });
}

function flattenAddonFacts(addons: Record<string, unknown>): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(addons)) {
    if (key === "others_keys" && Array.isArray(value)) {
      rows.push(["others_keys", value.map(String).join(", ")]);
      continue;
    }
    if (value && typeof value === "object") {
      rows.push([key, JSON.stringify(value)]);
    } else {
      rows.push([key, str(value)]);
    }
  }
  return rows;
}

function buildExplorerGraph(templateName: string, charts: ChartMeta[], active: string | null) {
  const nodes: Node[] = [
    {
      id: "template",
      position: { x: 360, y: 0 },
      data: { label: templateName, kind: "template" },
      style: nodeStyle("#c46b9a"),
    },
    {
      id: "distro",
      position: { x: 20, y: 110 },
      data: { label: "Distribución" },
      style: nodeStyle("#6fbfa8"),
    },
    {
      id: "cni",
      position: { x: 180, y: 110 },
      data: { label: "CNI" },
      style: nodeStyle("#6fbfa8"),
    },
    {
      id: "pools",
      position: { x: 320, y: 110 },
      data: { label: "Node pools" },
      style: nodeStyle("#6fbfa8"),
    },
    {
      id: "addons",
      position: { x: 480, y: 110 },
      data: { label: "Add-ons" },
      style: nodeStyle("#6fbfa8"),
    },
    {
      id: "charts",
      position: { x: 640, y: 110 },
      data: { label: `Charts (${charts.length})` },
      style: nodeStyle("#5b9fd4"),
    },
  ];
  const edges: Edge[] = [
    { id: "t-d", source: "template", target: "distro" },
    { id: "t-c", source: "template", target: "cni" },
    { id: "t-p", source: "template", target: "pools" },
    { id: "t-a", source: "template", target: "addons" },
    { id: "t-ch", source: "template", target: "charts" },
  ];
  const documented = charts.filter((item) => item.version);
  const others = charts.filter((item) => !item.version);
  documented.forEach((chart, index) => {
    const id = `chart:${chart.name}`;
    nodes.push({
      id,
      position: { x: 20 + (index % 4) * 190, y: 220 + Math.floor(index / 4) * 72 },
      data: { label: `${chart.name}\n${chart.version}` },
      style: {
        ...nodeStyle(active === chart.name ? "#6fbfa8" : "#5b9fd4"),
        whiteSpace: "pre-wrap",
        width: 170,
        fontSize: 11,
        textAlign: "center",
      },
    });
    edges.push({ id: `ch-${chart.name}`, source: "charts", target: id });
  });
  if (others.length) {
    nodes.push({
      id: "chart-others",
      position: { x: 20 + (documented.length % 4) * 190, y: 220 + Math.floor(documented.length / 4) * 72 },
      data: { label: `otros (${others.length})\nversión faltante` },
      style: {
        ...nodeStyle("#8a969e"),
        whiteSpace: "pre-wrap",
        width: 170,
        fontSize: 11,
        textAlign: "center",
      },
    });
    edges.push({ id: "ch-others", source: "charts", target: "chart-others" });
  }
  return { nodes, edges };
}

function nodeStyle(color: string): CSSProperties {
  return {
    background: "#1d242c",
    color: "#e4eaee",
    border: `1px solid ${color}`,
    borderRadius: 6,
    padding: 8,
    fontSize: 12,
    width: 130,
  };
}
