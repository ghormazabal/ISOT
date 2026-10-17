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
  /** Capa de la biblioteca (`@isot`, `@templates1`…) si no está en el repo de datos. */
  library?: string | null;
};

export type DataRepoStatus = {
  id: string;
  name: string;
  path: string;
  git: boolean;
  branch: string | null;
  head: string | null;
  short: string | null;
  dirty: boolean;
  remote: string | null;
  remote_url: string | null;
  expected_branch: string | null;
  /** false: ruta inexistente o carpeta que no es un repositorio de datos. */
  available?: boolean;
  error?: string | null;
  default?: boolean;
  /** Edición efectiva en este repositorio (global salvo repos[].edit: false). */
  edit?: boolean;
};

/** Entrada de GET /api/repos (path = solo el nombre de la carpeta). */
export type RepoStatus = DataRepoStatus & {
  available: boolean;
  error: string | null;
  default: boolean;
  edit: boolean;
};

export type ReposPayload =
  | { configured: false; repos: []; default_repo: null; error: string | null; message: string; hints: string[]; search: string[]; edit: boolean }
  | { configured: true; origin: string; config_file: string | null; default_repo: string; repos: RepoStatus[] };

export type ConfigInfo =
  | {
      configured: false;
      error: string | null;
      message: string;
      hints: string[];
      search: string[];
      edit: boolean;
    }
  | {
      configured: true;
      origin: string;
      config_file: string | null;
      data_repo: DataRepoStatus;
      /** Repositorios configurados (estado de cada uno) y cuál es el de esta vista. */
      repos: (RepoStatus & { active: boolean })[];
      active_repo: string;
      default_repo: string;
      templates: string[];
      edit: boolean;
      push: boolean;
      push_remote: string;
      ui: { title: string | null };
    };

export type SiteTemplateOption = {
  id: string;
  name: string;
  description: string | null;
  source: "repo" | "isot";
};

export type LibraryTemplate = {
  name: string;
  site: string;
  scope: "global";
  library: string;
  distro: string | null;
  version: string | null;
  path: string;
  in_repo: boolean;
};

export type WizardStatus = {
  edit: boolean;
  push?: boolean;
  site_templates?: SiteTemplateOption[];
  template_library?: LibraryTemplate[];
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
  /** Archivo del repo de datos del que sale la entidad (para abrir su diff YAML). */
  path?: string | null;
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

/** Meta de una ref resuelta por el backend; `local` = working tree. */
export type RefMeta = {
  ref: string;
  sha: string;
  short?: string;
  local?: boolean;
  label?: string;
  subject?: string;
  author?: string;
  date?: string;
  dirty?: number;
};

export type CompareFileEntry = { path: string; status: "added" | "removed" | "modified"; kind: string };

export type CompareFilesPayload = {
  base: RefMeta;
  head: RefMeta;
  prefix: string[];
  files: CompareFileEntry[];
  truncated: boolean;
  counts: { added: number; removed: number; modified: number };
};

export type CompareFilePayload = {
  path: string;
  status: "added" | "removed" | "modified" | "unchanged";
  binary: boolean;
  before: string | null;
  after: string | null;
  diff: string;
  base: RefMeta;
  head: RefMeta;
};

export type IndexStatus = {
  fresh: boolean;
  live: boolean;
  manifest: boolean;
  missing: string[];
  stale: string[];
  files: number;
  can_reindex: boolean;
};

export type SemanticDiff = {
  base: RefMeta;
  head: RefMeta;
  site: string | null;
  summary: { added: number; removed: number; modified: number; unchanged: number };
  entities: DiffEntity[];
  graphs: {
    network?: GraphPayload;
    kubernetes?: GraphPayload;
    global?: GraphPayload;
  };
};

export type CanvasMode = "graph" | "explorer" | "diff" | "files";

export type Selection =
  | { type: "device"; site: string; hostname: string }
  | { type: "cluster"; site: string; name: string }
  | { type: "template"; site: string; name: string }
  | { type: "site"; id: string };

export type GroupBy = "cluster" | "kind" | "vlan" | "pair";
export type AppView = "network" | "kubernetes" | "wizards" | "renders";

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
  /** Hallazgo de un template atribuido al clúster que lo usa ("global/<name>"). */
  source_template?: string | null;
  /** Acción de remediación (ver docs/rules.md). */
  remediation?: Remediation | null;
};

export type AddDeviceRemediation = {
  kind: "add_device";
  site: string;
  cluster: string | null;
  role: string;
  device_kind: "baremetal" | "vm" | "network";
  count: number;
  reason: string;
  label: string;
  followup?: string;
};

export type OpenEntityRemediation = {
  kind: "open_entity";
  target: "cluster" | "template" | "values" | "device" | "site";
  site: string | null;
  name: string;
  label: string;
};

export type Remediation = AddDeviceRemediation | OpenEntityRemediation;

/** Hallazgo que un wizard intenta corregir (viaja en el borrador y en el body del preview). */
export type FixContext = {
  finding: Pick<Finding, "id" | "severity" | "site" | "cluster" | "entity_kind" | "entity_id" | "message">;
  remediation: AddDeviceRemediation;
};

export type FixStatus = {
  id: string;
  site?: string | null;
  cluster?: string | null;
  resolved: boolean;
  remaining: Finding[];
  also_resolves: string[];
};

export type FindingCounts = { error: number; warning: number; info: number; total: number };

export type FindingsPayload = {
  /** "all" (vista global) o el id del sitio. */
  scope?: string;
  counts: FindingCounts;
  /** Contadores por sitio (todos los sitios en la vista global). */
  by_site?: Record<string, FindingCounts>;
  /** Hallazgos no atribuibles a un sitio (templates sin uso, inventario). */
  global?: FindingCounts;
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
  scope?: string;
  refs?: { base: RefMeta; head: RefMeta };
  base: FindingsPayload;
  head: FindingsPayload;
  added: Finding[];
  resolved: Finding[];
  added_counts?: FindingCounts;
  resolved_counts?: FindingCounts;
  added_by_site?: Record<string, FindingCounts>;
  resolved_by_site?: Record<string, FindingCounts>;
};

export type WizardFindings = {
  site?: string | null;
  counts?: FindingCounts;
  added: Finding[];
  resolved: Finding[];
  current: Finding[];
  blocked: boolean;
  fix?: FixStatus | null;
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
  /** Solo con edit.push: resultado de `git push -u` con las credenciales del usuario. */
  push?: { ok: boolean; remote: string; branch: string; message: string };
};
export type SiteFilter = "all" | string;

/** Un lado de un enlace lógico (lo que no está en el modelo llega como null). */
export type LinkSide = {
  interface: string | null;
  port: string | null;
  bond: string | null;
  port_channel: string | null;
  lacp: string | null;
  members: string[] | null;
  ip: string | null;
  mtu: number | string | null;
  device_mtu: number | string | null;
  extra: Record<string, unknown>;
};

export type LinkVlan = {
  id: number | string;
  name: string | null;
  subnet?: string | null;
  cidr: string | null;
  gateway: string | null;
  vrf: string | null;
  description: string | null;
  interface?: string | null;
  ip?: string | null;
  network?: string | null;
  allowed?: boolean;
  extra_host?: boolean;
  implicit?: boolean;
};

export type LinkEndpoint = {
  id: string;
  entity: "device" | "site" | "cluster" | "template";
  label: string;
  site: string;
  hostname?: string;
  role?: string | null;
  kind?: string | null;
  cluster_ref?: string | null;
  path?: string | null;
  remote?: boolean;
  proposed?: boolean;
};

export type LinkSource = { path: string | null; key: string; yaml: string };

export type LinkItem = {
  type: "bond" | "interface" | "oob" | "hypervisor" | "reference";
  label: string;
  declared_by: "source" | "target";
  source: LinkSide;
  target: LinkSide;
  vlans: LinkVlan[];
  attributes: Record<string, unknown>;
  sources: LinkSource[];
  pair?: { source: LinkEndpoint; target: LinkEndpoint };
};

export type LinkDetail = {
  kind: "network" | "dci" | "kubernetes";
  source: LinkEndpoint;
  target: LinkEndpoint;
  links: LinkItem[];
  vlans: LinkVlan[];
  ports: string[];
  paths: string[];
};

export type LinkChange = { path: string; before: unknown; after: unknown; status: "added" | "removed" | "modified" };

export type LinkResponse = {
  detail: LinkDetail;
  base: LinkDetail | null;
  head: LinkDetail | null;
  diff: { status: "added" | "removed" | "modified" | "unchanged" | "missing"; changes: LinkChange[] } | null;
  refs: { base: { ref: string; sha: string }; head: { ref: string; sha: string } } | null;
};

/** Arista seleccionada en un grafo. */
export type EdgeSelection = {
  id: string;
  source: string;
  target: string;
  vlans: Array<number | string>;
  ports: string[];
  diff_status?: DiffStatus;
};
