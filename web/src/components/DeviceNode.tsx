import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { GraphNode } from "../types";

type DeviceFlowNode = Node<GraphNode, "device">;

function diffClass(status: GraphNode["diff_status"]): string {
  return status ? ` diff-${status}` : "";
}

function DiffBadge({ counts }: { counts: GraphNode["diff_counts"] }) {
  if (!counts) return null;
  const added = counts.added || 0;
  const removed = counts.removed || 0;
  const modified = counts.modified || 0;
  if (!added && !removed && !modified) return null;
  return (
    <span className="diff-badge">
      {added ? <span className="add">+{added}</span> : null}
      {removed ? <span className="del">−{removed}</span> : null}
      {modified ? <span className="mod">~{modified}</span> : null}
    </span>
  );
}

export function DeviceNode({ data, selected }: NodeProps<DeviceFlowNode>) {
  return (
    <div className={`device-node kind-${data.kind ?? "otros"}${selected ? " is-selected" : ""}${diffClass(data.diff_status)}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.hostname}</strong>
      <span className="muted">{data.role ?? "sin rol"}</span>
      <span className="mono">{data.ip ?? "sin-ip"}</span>
      <DiffBadge counts={data.diff_counts} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

type ClusterFlowNode = Node<GraphNode, "clusterNode">;
type TemplateFlowNode = Node<GraphNode, "templateNode">;
type NetworkFlowNode = Node<GraphNode, "networkNode">;
type ReleaseFlowNode = Node<GraphNode, "releaseNode">;

export function ClusterGraphNode({ data, selected }: NodeProps<ClusterFlowNode>) {
  return (
    <div className={`device-node kind-cluster${selected ? " is-selected" : ""}${diffClass(data.diff_status)}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.hostname}</strong>
      <span className="muted">clúster · {data.role ?? "—"}</span>
      <span className="mono">{data.ip ?? ""}</span>
      <DiffBadge counts={data.diff_counts} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export function TemplateGraphNode({ data, selected }: NodeProps<TemplateFlowNode>) {
  return (
    <div className={`device-node kind-template${selected ? " is-selected" : ""}${diffClass(data.diff_status)}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.hostname}</strong>
      <span className="muted">template{data.site ? ` · ${data.site}` : ""}</span>
      <span className="mono">{data.ip ?? ""}</span>
      <DiffBadge counts={data.diff_counts} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export function NetworkGraphNode({ data, selected }: NodeProps<NetworkFlowNode>) {
  return (
    <div className={`device-node kind-network${selected ? " is-selected" : ""}${diffClass(data.diff_status)}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.hostname}</strong>
      <span className="muted">VLAN / red</span>
      <span className="mono">{data.site ?? ""}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export function ReleaseGraphNode({ data, selected }: NodeProps<ReleaseFlowNode>) {
  return (
    <div className={`device-node kind-release${selected ? " is-selected" : ""}${diffClass(data.diff_status)}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.hostname}</strong>
      <span className="muted">Helm release</span>
      <span className="mono">{data.site ?? "global"}</span>
      <DiffBadge counts={data.diff_counts} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

type SiteFlowNode = Node<GraphNode, "siteNode">;

export function SiteGraphNode({ data, selected }: NodeProps<SiteFlowNode>) {
  return (
    <div className={`device-node kind-site${selected ? " is-selected" : ""}${diffClass(data.diff_status)}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.hostname}</strong>
      <span className="muted">sitio</span>
      <span className="mono">{data.ip ?? ""}</span>
      <DiffBadge counts={data.diff_counts} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
