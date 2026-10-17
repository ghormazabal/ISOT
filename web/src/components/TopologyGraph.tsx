import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { ClusterGroup } from "./ClusterGroup";
import { RefitOnResize } from "./RefitOnResize";
import {
  ClusterGraphNode,
  DeviceNode,
  NetworkGraphNode,
  ReleaseGraphNode,
  SiteGraphNode,
  TemplateGraphNode,
} from "./DeviceNode";
import { DIFF_STROKE, buildFlow, vlanColor } from "../layout";
import { annotateGraph } from "../findings";
import type { AppView, DiffStatus, Finding, GraphEdge, GraphNode, GraphPayload, GroupBy, SiteFilter, Topology } from "../types";

const nodeTypes = {
  device: DeviceNode,
  clusterGroup: ClusterGroup,
  clusterNode: ClusterGraphNode,
  templateNode: TemplateGraphNode,
  siteNode: SiteGraphNode,
  networkNode: NetworkGraphNode,
  releaseNode: ReleaseGraphNode,
};

type Props = {
  graph: GraphPayload | null;
  topology: Topology | null;
  groupBy: GroupBy;
  view: AppView;
  siteFilter: SiteFilter;
  selectedId: string | null;
  onSelectNode: (node: GraphNode) => void;
  onGroupBy: (value: GroupBy) => void;
  compare?: boolean;
  onlyChanges?: boolean;
  onOnlyChanges?: (value: boolean) => void;
  focusIds?: string[];
  neighborhood?: boolean;
  onNeighborhood?: (value: boolean) => void;
  findings?: Finding[];
  /** Arista seleccionada: se resalta con sus extremos y se atenúa el resto. */
  selectedEdgeId?: string | null;
  onSelectEdge?: (edge: GraphEdge) => void;
  onClearEdge?: () => void;
};

export function TopologyGraph({
  graph,
  topology,
  groupBy,
  view,
  siteFilter,
  selectedId,
  onSelectNode,
  onGroupBy,
  compare = false,
  onlyChanges = false,
  onOnlyChanges,
  focusIds,
  neighborhood = false,
  onNeighborhood,
  findings = [],
  selectedEdgeId = null,
  onSelectEdge,
  onClearEdge,
}: Props) {
  const effectiveGroup: GroupBy = siteFilter === "all" && view === "network" ? "kind" : groupBy;
  const decorated = useMemo(() => annotateGraph(graph, findings), [graph, findings]);
  const visibleGraph = useMemo(() => {
    if (!decorated) return null;
    const seeds = focusIds?.length ? focusIds : addedIds(decorated);
    const narrowed = neighborhood ? neighborhoodGraph(decorated, seeds) : decorated;
    return filterGraph(narrowed, onlyChanges && compare && !neighborhood);
  }, [decorated, onlyChanges, compare, neighborhood, focusIds]);
  const laid = useMemo(
    () => (visibleGraph ? buildFlow(visibleGraph, effectiveGroup) : { nodes: [], edges: [] }),
    [visibleGraph, effectiveGroup],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(laid.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(laid.edges);

  const selectedEdge = useMemo(
    () => (selectedEdgeId ? (visibleGraph?.edges.find((edge) => edge.id === selectedEdgeId) ?? null) : null),
    [selectedEdgeId, visibleGraph],
  );

  useEffect(() => {
    const endpoints = new Set(selectedEdge ? [selectedEdge.source, selectedEdge.target] : []);
    setNodes(
      laid.nodes.map((node) => ({
        ...node,
        selected: node.id === selectedId,
        className: selectedEdge
          ? endpoints.has(node.id)
            ? "edge-endpoint"
            : node.type === "clusterGroup"
              ? "is-dimmed-group"
              : "is-dimmed"
          : undefined,
      })),
    );
    setEdges(
      laid.edges.map((edge) => ({
        ...edge,
        selected: edge.id === selectedEdge?.id,
        className: selectedEdge ? (edge.id === selectedEdge.id ? "is-edge-selected" : "is-dimmed") : undefined,
        zIndex: edge.id === selectedEdge?.id ? 10 : undefined,
      })),
    );
  }, [laid, selectedId, selectedEdge, setEdges, setNodes]);

  const onEdgeClick: EdgeMouseHandler = (event, edge) => {
    event.stopPropagation();
    const found = visibleGraph?.edges.find((item) => item.id === edge.id);
    if (found) onSelectEdge?.(found);
  };

  const onNodeClick: NodeMouseHandler = (_event, node) => {
    if (node.type === "clusterGroup") return;
    onSelectNode(node.data as GraphNode);
  };
  const onNodeDoubleClick: NodeMouseHandler = (event, node) => {
    onNodeClick(event, node);
  };

  const vlans = topology?.vlans ?? [];
  const isGlobal = siteFilter === "all" && view === "network";

  return (
    <section className={`graph-pane${onSelectEdge ? " edges-clickable" : ""}${selectedEdge ? " has-edge-selection" : ""}`}>
      <header className="graph-toolbar">
        <div>
          <h2>{isGlobal ? "Sitios" : view === "kubernetes" ? "Kubernetes" : "Topología lógica"}</h2>
          <p>
            {compare
              ? isGlobal
                ? "Diff gráfico · clic en un sitio modificado para abrir su topología"
                : "Diff gráfico base → head (Local, ramas o commits; sin checkout)"
              : isGlobal
                ? "Vista global: sitios y enlaces DCI (índice)"
                : view === "kubernetes"
                  ? "Templates, clústeres y nodos (índices)"
                  : "Grafo armado desde el índice (nodos y connected_to)"}
          </p>
        </div>
        {view === "network" && !isGlobal ? (
          <label htmlFor="group-by">
            Agrupar por
            <select
              id="group-by"
              name="group-by"
              value={groupBy}
              onChange={(event) => onGroupBy(event.target.value as GroupBy)}
            >
              <option value="cluster">clúster</option>
              <option value="kind">tipo</option>
              <option value="vlan">VLAN</option>
              <option value="pair">par de leafs</option>
            </select>
          </label>
        ) : null}
      </header>
      {compare ? (
        <ul className="vlan-legend diff-legend">
          {(["added", "removed", "modified", "unchanged"] as DiffStatus[]).map((status) => (
            <li key={status}>
              <i style={{ background: DIFF_STROKE[status] }} />
              {statusLabel(status)}
            </li>
          ))}
          <li>
            <label className="diff-filter">
              <input
                type="checkbox"
                checked={onlyChanges}
                onChange={(event) => onOnlyChanges?.(event.target.checked)}
              />{" "}
              solo cambios
            </label>
          </li>
          {onNeighborhood ? (
            <li>
              <label className="diff-filter">
                <input
                  id="wizard-neighborhood"
                  type="checkbox"
                  checked={neighborhood}
                  onChange={(event) => onNeighborhood(event.target.checked)}
                />{" "}
                solo vecindario
              </label>
            </li>
          ) : null}
        </ul>
      ) : view === "network" && !isGlobal && vlans.length > 0 ? (
        <ul className="vlan-legend">
          {vlans.map((vlan) => (
            <li key={vlan.id}>
              <i style={{ background: vlanColor(vlan.id) }} />
              VLAN {vlan.id} {vlan.name}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="graph-canvas">
        <ReactFlow
          key={`${siteFilter}:${view}:${compare}:${visibleGraph?.nodes.length ?? 0}:${visibleGraph?.view ?? ""}`}
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          onEdgeClick={onSelectEdge ? onEdgeClick : undefined}
          onPaneClick={onClearEdge}
          elementsSelectable
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#2c3640" gap={18} />
          <Controls showInteractive={false} />
          <FitFocus ids={neighborhood && visibleGraph ? visibleGraph.nodes.map((node) => node.id) : (focusIds ?? [])} />
          <RefitOnResize ids={neighborhood && visibleGraph ? visibleGraph.nodes.map((node) => node.id) : (focusIds ?? [])} />
        </ReactFlow>
      </div>
    </section>
  );
}

function FitFocus({ ids }: { ids: string[] }) {
  const { fitView } = useReactFlow();
  const key = ids.join("|");
  useEffect(() => {
    if (!key) return;
    const list = key.split("|").filter(Boolean);
    const handle = window.setTimeout(() => {
      void fitView({ nodes: list.map((id) => ({ id })), padding: 0.45, duration: 280 });
    }, 80);
    return () => window.clearTimeout(handle);
  }, [fitView, key]);
  return null;
}

function addedIds(graph: GraphPayload): string[] {
  return graph.nodes.filter((node) => node.diff_status === "added").map((node) => node.id);
}

function neighborhoodGraph(graph: GraphPayload, seeds: string[]): GraphPayload {
  const keep = new Set(seeds);
  if (!keep.size) return graph;
  for (const edge of graph.edges) {
    if (keep.has(edge.source) || keep.has(edge.target)) {
      keep.add(edge.source);
      keep.add(edge.target);
    }
  }
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => keep.has(node.id)),
    edges: graph.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
  };
}

function statusLabel(status: DiffStatus): string {
  if (status === "added") return "agregado";
  if (status === "removed") return "quitado";
  if (status === "modified") return "modificado";
  return "sin cambios";
}

function filterGraph(graph: GraphPayload, onlyChanges: boolean): GraphPayload {
  if (!onlyChanges) return graph;
  const changedEdges = graph.edges.filter((edge) => edge.diff_status && edge.diff_status !== "unchanged");
  const keep = new Set<string>();
  for (const node of graph.nodes) {
    if (node.diff_status && node.diff_status !== "unchanged") keep.add(node.id);
  }
  for (const edge of changedEdges) {
    keep.add(edge.source);
    keep.add(edge.target);
  }
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => keep.has(node.id)),
    edges: graph.edges.filter(
      (edge) => keep.has(edge.source) && keep.has(edge.target) && (edge.diff_status !== "unchanged" || keep.has(edge.source)),
    ),
  };
}
