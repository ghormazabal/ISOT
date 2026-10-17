import { repoUrl } from "./api";
import type {
  RenderInstance,
  RenderPreview,
  RendersDashboard,
  RendersDiff,
  RenderTemplateDetail,
  RenderTemplateSummary,
  WritePreview,
  WriteResult,
} from "./rendersTypes";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(repoUrl(url), init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* respuesta no JSON */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function query(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : "";
}

export type PreviewBody = {
  id?: string;
  spec_text?: string;
  files?: Record<string, string>;
  site: string;
  cluster?: string | null;
  params?: Record<string, unknown>;
  instance?: string;
  ref?: string | null;
};

export type ConfigBody = {
  text?: string;
  settings?: { target_dir?: string };
  upsert_instance?: Partial<RenderInstance> & { id: string; template: string; site: string };
  remove_instance?: string;
  branch?: string | null;
  description?: string;
};

export type TemplateSaveBody = {
  id: string;
  spec_text?: string;
  files?: Record<string, string>;
  delete_template?: boolean;
  branch?: string | null;
};

export type ApplyBody = { instances?: string[]; prune?: boolean; branch?: string | null };

export const rendersApi = {
  dashboard: (ref?: string | null) => request<RendersDashboard>(`/api/renders${query({ ref })}`),
  templates: (ref?: string | null) => request<RenderTemplateSummary[]>(`/api/renders/templates${query({ ref })}`),
  template: (id: string, source?: "repo" | "bundled" | null, ref?: string | null) =>
    request<RenderTemplateDetail>(`/api/renders/templates/${encodeURIComponent(id)}${query({ source, ref })}`),
  clone: (id: string, newId: string) =>
    request<{ id: string; from: string; spec_text: string; file_contents: Record<string, string>; exists: boolean }>(
      `/api/renders/templates/${encodeURIComponent(id)}/clone${query({ new_id: newId })}`,
    ),
  context: (site: string, cluster?: string | null, ref?: string | null) =>
    request<Record<string, unknown>>(`/api/renders/context${query({ site, cluster, ref })}`),
  preview: (body: PreviewBody) => post<RenderPreview>("/api/renders/preview", body),
  diff: (params: { instance?: string | null; target_ref?: string | null; ref?: string | null }) =>
    request<RendersDiff>(`/api/renders/diff${query(params)}`),
  guide: (doc: "human" | "llm" = "human") => request<{ path: string; markdown: string }>(`/api/renders/guide${query({ doc })}`),
  config: (ref?: string | null) =>
    request<{ path: string; exists: boolean; text: string; errors: string[] }>(`/api/renders/config${query({ ref })}`),
  templateSavePreview: (body: TemplateSaveBody) => post<WritePreview>("/api/renders/templates/save/preview", body),
  templateSaveCommit: (body: TemplateSaveBody) => post<WriteResult>("/api/renders/templates/save/commit", body),
  configPreview: (body: ConfigBody) => post<WritePreview>("/api/renders/config/preview", body),
  configCommit: (body: ConfigBody) => post<WriteResult>("/api/renders/config/commit", body),
  applyPreview: (body: ApplyBody) => post<WritePreview>("/api/renders/apply/preview", body),
  applyCommit: (body: ApplyBody) => post<WriteResult>("/api/renders/apply/commit", body),
};
