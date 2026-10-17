import type { Node, NodeProps } from "@xyflow/react";

type GroupData = { label: string; finding_severity?: string };
type GroupNode = Node<GroupData, "clusterGroup">;

export function ClusterGroup({ data }: NodeProps<GroupNode>) {
  return (
    <div className="cluster-group-label">
      {data.label}
      {data.finding_severity ? (
        <span className={`finding-badge sev-${data.finding_severity}`}>{data.finding_severity}</span>
      ) : null}
    </div>
  );
}
