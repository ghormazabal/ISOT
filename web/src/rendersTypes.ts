/** Tipos de la API /api/renders (ver isot/renders.py e isot/web_renders.py). */

export type RenderError = { file: string | null; line: number | null; kind: string; message: string; item?: string };
export type RenderWarning = { file: string; line: number; kind: string; message: string };

export type RenderOutputSummary = { template: string; mode: "single" | "per_item"; path: string };

export type RenderTemplateSummary = {
  id: string;
  source: "repo" | "bundled" | "bundled-overridden" | "draft";
  path: string;
  version: string;
  description: string | null;
  scope: "site" | "cluster" | "device";
  tags: string[];
  outputs: RenderOutputSummary[];
  files: string[];
  errors: RenderError[];
  params: Record<string, unknown>;
  instances?: number;
};

export type RenderInstance = {
  id: string;
  template: string;
  site: string;
  cluster: string | null;
  /** Carpeta destino propia (mismo repo de datos); null = la global. */
  target_dir: string | null;
  dir: string;
  dir_explicit: boolean;
  params: Record<string, unknown>;
  enabled: boolean;
  description: string | null;
  source: string;
};

export type RenderTemplateDetail = RenderTemplateSummary & {
  spec_text: string;
  file_contents: Record<string, string>;
  used_by: RenderInstance[];
};

export type DashboardRow = RenderInstance & {
  /** Carpeta completa de la salida dentro del repo de datos. */
  folder: string | null;
  folder_error: string | null;
  template_source: string | null;
  version: string;
  files: number;
  errors: RenderError[];
  error_count: number;
  warning_count: number;
  status: "ok" | "error" | "disabled";
};

export type StatusCounts = { new: number; modified: number; unchanged: number; orphaned: number };

export type RendersDashboard = {
  context_version: number;
  templates: { total: number; repo: number; bundled: number; invalid: number };
  instances: { total: number; enabled: number; ok: number; error: number };
  files: number;
  errors: number;
  warnings: number;
  per_site: Record<string, { instances: number; files: number; errors: number }>;
  per_cluster: Record<string, { instances: number; files: number; errors: number }>;
  drift: {
    in_sync: number;
    drift: number;
    stale: number;
    counts: StatusCounts;
    error: string | null;
  };
  rows: DashboardRow[];
  config: { path: string; exists: boolean; target_dir: string; errors: string[] };
  edit: boolean;
  ref: string | null;
};

export type RenderedFile = { path: string; content: string; output: number; source: string; mode: "single" | "per_item" };

export type RenderPreview = {
  instance: string;
  template: string;
  version: string;
  ok: boolean;
  count: number;
  files: RenderedFile[];
  errors: RenderError[];
  warnings: RenderWarning[];
  site: string;
  cluster: string | null;
  scope: string;
  template_errors: RenderError[];
};

export type DiffFile = {
  path: string;
  status: "new" | "modified" | "unchanged" | "orphaned";
  before: string | null;
  after: string | null;
  diff: string;
  target_path: string;
};

export type DiffInstance = {
  id: string;
  template: string;
  version: string;
  site: string;
  cluster: string | null;
  dir: string | null;
  errors: RenderError[];
  warnings: RenderWarning[];
  files: DiffFile[];
  counts: StatusCounts;
  skipped: string[];
  sidecar: Record<string, unknown> | null;
  in_sync?: boolean;
  stale?: boolean;
  target_error?: string;
};

export type RendersDiff = {
  target_dir: string;
  error: string | null;
  ref: string | null;
  instances: DiffInstance[];
  counts: StatusCounts;
  in_sync: number;
  drift: number;
  edit: boolean;
};

export type ProposedFile = { path: string; status: string; diff: string; content: string | null };

export type WritePreview = { files: ProposedFile[]; errors?: Array<RenderError | string>; text?: string; skipped?: string[]; folders?: string[] };

export type WriteResult = {
  ok: boolean;
  branch: string;
  created: boolean;
  sha: string;
  short: string;
  base: string;
  message: string;
  files: string[];
  push?: { ok: boolean; message: string };
};
