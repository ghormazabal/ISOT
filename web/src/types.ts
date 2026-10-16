export type DeviceLink = {
  interface: string | null;
  device: string;
  port: string | null;
  vlan: number | string | null;
  site?: string | null;
};

export type DeviceIndexEntry = {
  hostname: string;
  site: string;
  role: string | null;
  ip: string | null;
  path: string;
  kind: string;
  cluster_ref: string | null;
  cluster_ref_site?: string | null;
  links: DeviceLink[];
};

export type ClusterIndexEntry = {
  name: string;
  site: string;
  kind: string | null;
  template_ref: string | null;
  distro: string | null;
  version: string | null;
  path: string;
  node_count: number;
  override_count?: number;
};

export type ChartMeta = {
  name: string;
  repo: string | null;
  chart: string;
  version: string | null;
  namespace: string | null;
  order: number | null;
  depends_on: string[];
  values_ref: string | null;
  values_source: "ref" | "inline" | "missing";
  has_values: boolean;
  note: string | null;
};

export type TemplateIndexEntry = {
  name: string;
  site: string;
  scope: "global" | "site";
  version: string | null;
  distro: string | null;
  cni: string | null;
  path: string;
  chart_count?: number;
  charts?: string[];
  used_by: string[];
};

export type SiteIndexEntry = {
  id: string;
  name: string;
  path: string;
  description: string | null;
  device_count: number;
  cluster_count: number;
  address_spaces: string[];
  inter_site_links: Array<{
    device: string;
    peer_site: string;
    peer_device: string;
    vlan: number | string | null;
    port: string | null;
  }>;
};

export type DiffStatus = "added" | "removed" | "modified" | "unchanged";

export type GraphNode = {
  id: string;
  hostname: string;
  site?: string | null;
  role: string | null;
  ip: string | null;
  kind: string | null;
  cluster_ref: string | null;
  path: string | null;
  entity?: "device" | "cluster" | "template" | "site" | "network" | "release";
  template_ref?: string | null;
  remote?: boolean;
  description?: string | null;
  diff_status?: DiffStatus;
  diff_counts?: { added: number; removed: number; modified: number };
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  vlans: Array<number | string>;
  ports: string[];
  diff_status?: DiffStatus;
};

export type GraphPayload = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  view?: string;
};

export type Topology = {
  site?: { id?: string; operator?: string; description?: string };
  subnets?: Array<{
    name: string;
    cidr: string;
    vlan: number | null;
    gateway?: string;
    description?: string;
  }>;
  vlans?: Array<{ id: number; name: string; subnet?: string }>;
  gaps?: string[];
};

export type DeviceDetail = {
  index: DeviceIndexEntry;
  data: {
    hostname?: string;
    role?: string;
    cluster_ref?: string;
    os?: string;
    networking?: Array<{
      interface?: string;
      name?: string;
      mac?: string;
      ip?: string;
      vlan?: number | string;
      connected_to?: { device?: string; port?: string; site?: string };
    }>;
    [key: string]: unknown;
  };
};

export type ClusterDetail = {
  name: string;
  site: string;
  path: string;
  members: string[];
  data: Record<string, unknown>;
  template_site?: string | null;
  template_path?: string | null;
};

export type TemplateDetail = {
  name: string;
  site: string;
  path: string;
  used_by: string[];
  data: Record<string, unknown>;
  charts: ChartMeta[];
};

export type ChartValues = {
  release: string;
  chart: string;
  version: string | null;
  source: "ref" | "inline" | "missing";
  path: string | null;
  values: unknown;
  missing: boolean;
  error: string | null;
};

export type ValuesDiffEntry = {
  path: string;
  template: unknown;
  override: unknown;
  effective: unknown;
  status: "inherited" | "overridden" | "added";
};

export type ValuesDiffRelease = {
  release: string;
  chart: string;
  version: string | null;
  values_source: string;
  values_path: string | null;
  has_override: boolean;
  error: string | null;
  entries: ValuesDiffEntry[];
};

export type ValuesDiff = {
  cluster: string;
  cluster_site: string;
  template: { site: string; name: string; path: string };
  unknown_releases: string[];
  releases: ValuesDiffRelease[];
};

export type DiffChange = {
  path: string;
  before: unknown;
  after: unknown;
  status: "added" | "removed" | "modified";
};

export type DiffKind = "site" | "device" | "network" | "cluster" | "template" | "release" | "values";

export type DiffChild = {
  id: string;
  kind: DiffKind;
  label: string;
  status: DiffStatus;
  site: string | null;
};

export type DiffEntity = {
  kind: DiffKind;
  id: string;
  label: string;
  site: string | null;
  status: DiffStatus;
  graph_ids: string[];
  changes: DiffChange[];
  before: unknown;
  after: unknown;
  inherited?: boolean;
  child_counts?: { added: number; removed: number; modified: number };
  children?: DiffChild[];
};

export type GitRef = {
  name: string;
  sha: string;
  kind: "branch" | "tag" | "commit";
  remote?: boolean;
  short?: string;
  subject?: string;
  author?: string;
  date?: string;
};

export type RefsPayload = {
  branches: GitRef[];
  tags: GitRef[];
  commits: GitRef[];
  head?: { ref: string; sha: string; short: string };
  rev?: { ref: string; sha: string; short: string };
};

export type CommitsPayload = {
  ref: string;
  sha: string;
  short: string;
  commits: GitRef[];
};

export type SemanticDiff = {
  base: { ref: string; sha: string; short?: string };
  head: { ref: string; sha: string; short?: string };
  site: string | null;
  summary: { added: number; removed: number; modified: number; unchanged: number };
  entities: DiffEntity[];
  graphs: {
    network?: GraphPayload;
    kubernetes?: GraphPayload;
    global?: GraphPayload;
  };
};

export type CanvasMode = "graph" | "explorer" | "diff";

export type Selection =
  | { type: "device"; site: string; hostname: string }
  | { type: "cluster"; site: string; name: string }
  | { type: "template"; site: string; name: string }
  | { type: "site"; id: string };

export type GroupBy = "cluster" | "kind" | "vlan";
export type AppView = "network" | "kubernetes";
export type SiteFilter = "all" | string;
