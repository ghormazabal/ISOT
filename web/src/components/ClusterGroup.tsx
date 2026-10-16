import type { Node, NodeProps } from "@xyflow/react";

type GroupData = { label: string };
type GroupNode = Node<GroupData, "clusterGroup">;

export function ClusterGroup({ data }: NodeProps<GroupNode>) {
  return <div className="cluster-group-label">{data.label}</div>;
}
