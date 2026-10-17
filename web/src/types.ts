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
  leaf_pair?: string | null;
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
  leaf_pair?: string | null;
  finding_severity?: "error" | "warning" | "info";
  finding_counts?: { error: number; warning: number; info: number };
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

export type ConnectionRule = {
  id?: string;
  roles?: string[];
  attach?: string;
  leafs_per_node?: number;
  bond?: string;
  bond_name?: string;
  purpose?: string;
  via?: string;
  description?: string;
};

export type ConnectionPolicy = {
  architecture?: string;
  pair_policy?: string;
  ports_per_leaf?: number;
  fabric?: {
    spines?: number;
    leaf_pairs?: number;
    unpaired_leafs?: number;
    mgmt_switches?: number;
    dcgw?: number;
  };
  rules?: ConnectionRule[];
};

export type Topology = {
  site?: { id?: string; operator?: string; description?: string };
  architecture?: string;
  connection_rules?: ConnectionPolicy;
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

export type GroupBy = "cluster" | "kind" | "vlan" | "pair";
export type AppView = "network" | "kubernetes" | "wizards";

export type WizardField = {
  path: string;
  value: unknown;
  auto: boolean;
  origin: string;
  hostname?: string;
};

export type WizardFile = {
  path: string;
  action: "create" | "modify";
  content: string;
  diff: string;
};

export type WizardPreview = {
  kind?: string;
  site?: string;
  count?: number;
  sibling?: string | null;
  pattern_source?: string | null;
  pattern_note?: string | null;
  warnings?: string[];
  role?: string | null;
  hostnames?: string[];
  fields?: WizardField[];
  files?: WizardFile[];
  name?: string;
  graphs?: {
    network?: GraphPayload;
    kubernetes?: GraphPayload;
    global?: GraphPayload;
  };
  topology?: Topology;
  focus_ids?: string[];
  summary?: { added: number; removed: number; modified: number; unchanged: number };
  architecture?: string;
  connection_rules?: ConnectionPolicy;
  rules_source?: "declared" | "inferred" | null;
  inferred_rules?: ConnectionPolicy | null;
  findings?: WizardFindings;
};

export type Finding = {
  id: string;
  severity: "error" | "warning" | "info" | "off";
  default_severity?: string;
  entity_kind: string;
  entity_id: string;
  site: string | null;
  cluster: string | null;
  message: string;
  recommendation: string;
  path: string | null;
  graph_id: string | null;
  silenced?: boolean;
  override_reason?: string | null;
};

export type FindingsPayload = {
  counts: { error: number; warning: number; info: number; total: number };
  findings: Finding[];
  rules?: Array<{
    id: string;
    severity: string;
    description: string;
    entity: string;
    recommendation: string;
  }>;
};

export type FindingsDiffPayload = {
  base: FindingsPayload;
  head: FindingsPayload;
  added: Finding[];
  resolved: Finding[];
};

export type WizardFindings = {
  added: Finding[];
  resolved: Finding[];
  current: Finding[];
  blocked: boolean;
};

export type WizardCommit = {
  ok: boolean;
  branch: string;
  sha: string;
  short: string;
  base: string;
  message: string;
  files: string[];
  preview?: WizardPreview;
};
export type SiteFilter = "all" | string;
