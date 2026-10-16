import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { ClusterGroup } from "./ClusterGroup";
import {
  ClusterGraphNode,
  DeviceNode,
  NetworkGraphNode,
  ReleaseGraphNode,
  SiteGraphNode,
  TemplateGraphNode,
} from "./DeviceNode";
import { DIFF_STROKE, buildFlow, vlanColor } from "../layout";
import type { AppView, DiffStatus, GraphNode, GraphPayload, GroupBy, SiteFilter, Topology } from "../types";

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
}: Props) {
  const effectiveGroup: GroupBy = siteFilter === "all" && view === "network" ? "kind" : groupBy;
  const visibleGraph = useMemo(() => (graph ? filterGraph(graph, onlyChanges && compare) : null), [graph, onlyChanges, compare]);
  const laid = useMemo(
    () => (visibleGraph ? buildFlow(visibleGraph, effectiveGroup) : { nodes: [], edges: [] }),
    [visibleGraph, effectiveGroup],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(laid.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(laid.edges);

  useEffect(() => {
    setNodes(laid.nodes.map((node) => ({ ...node, selected: node.id === selectedId })));
    setEdges(laid.edges);
  }, [laid, selectedId, setEdges, setNodes]);

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
    <section className="graph-pane">
      <header className="graph-toolbar">
        <div>
          <h2>{isGlobal ? "Sitios" : view === "kubernetes" ? "Kubernetes" : "Topología lógica"}</h2>
          <p>
            {compare
              ? isGlobal
                ? "Diff gráfico · clic en un sitio modificado para abrir su topología"
                : "Diff gráfico entre refs de git (sin checkout)"
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
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#2c3640" gap={18} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </section>
  );
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
