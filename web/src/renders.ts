/** Sección Renders: lógica pura (URL de sub-pestañas, árbol de archivos, edición de template.yaml). */

export const RENDER_SUBTABS = ["resumen", "plantillas", "diff", "guia"] as const;
export type RenderSubTab = (typeof RENDER_SUBTABS)[number];
export const TAB_PARAM = "tab";
export const SUB_PARAM = "sub";

export const RENDER_SUBTAB_LABELS: Record<RenderSubTab, string> = {
  resumen: "Resumen",
  plantillas: "Plantillas",
  diff: "Diff",
  guia: "Guía",
};

/** `?tab=renders&sub=diff` → { renders: true, sub: "diff" }. Una sub desconocida cae en «resumen». */
export function rendersFromUrl(href: string): { renders: boolean; sub: RenderSubTab } {
  const url = new URL(href, "http://localhost");
  const renders = url.searchParams.get(TAB_PARAM) === "renders";
  const raw = url.searchParams.get(SUB_PARAM) ?? "";
  const sub = (RENDER_SUBTABS as readonly string[]).includes(raw) ? (raw as RenderSubTab) : "resumen";
  return { renders, sub };
}

/** URL con (o sin) `tab=renders&sub=…`; conserva ?repo= y el resto de la query. */
export function withRendersParam(href: string, sub: RenderSubTab | null): string {
  const url = new URL(href, "http://localhost");
  if (sub) {
    url.searchParams.set(TAB_PARAM, "renders");
    url.searchParams.set(SUB_PARAM, sub);
  } else {
    if (url.searchParams.get(TAB_PARAM) === "renders") url.searchParams.delete(TAB_PARAM);
    url.searchParams.delete(SUB_PARAM);
  }
  const query = url.searchParams.toString();
  return `${url.pathname}${query ? `?${query}` : ""}${url.hash}`;
}

export type TreeNode<T> = {
  name: string;
  path: string;
  children: TreeNode<T>[];
  item?: T;
};

/** Árbol de carpetas a partir de rutas `a/b/c.yaml` (carpetas primero, orden alfabético). */
export function buildTree<T extends { path: string }>(items: T[]): TreeNode<T>[] {
  const root: TreeNode<T> = { name: "", path: "", children: [] };
  for (const item of items) {
    const parts = item.path.split("/").filter(Boolean);
    let node = root;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const path = parts.slice(0, index + 1).join("/");
      let dir = node.children.find((child) => !child.item && child.name === parts[index]);
      if (!dir) {
        dir = { name: parts[index], path, children: [] };
        node.children.push(dir);
      }
      node = dir;
    }
    node.children.push({ name: parts[parts.length - 1] ?? item.path, path: item.path, children: [], item });
  }
  const sort = (nodes: TreeNode<T>[]): TreeNode<T>[] =>
    nodes
      .map((entry) => ({ ...entry, children: sort(entry.children) }))
      .sort((a, b) => Number(Boolean(a.item)) - Number(Boolean(b.item)) || a.name.localeCompare(b.name));
  return sort(root.children);
}

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "ctx";

export function classifyDiffLine(line: string): DiffLineKind {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

export type SideRow = { left: string | null; right: string | null; kind: "same" | "add" | "del" | "mod" };

/** Filas lado a lado (antes | después) con un LCS por líneas; suficiente para archivos de config. */
export function sideBySide(before: string | null, after: string | null): SideRow[] {
  const a = before === null ? [] : before.replace(/\n$/, "").split("\n");
  const b = after === null ? [] : after.replace(/\n$/, "").split("\n");
  const n = a.length;
  const m = b.length;
  if (n * m > 4_000_000) {
    return Array.from({ length: Math.max(n, m) }, (_, i) => ({
      left: a[i] ?? null,
      right: b[i] ?? null,
      kind: a[i] === b[i] ? "same" : "mod",
    }));
  }
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const rows: SideRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      rows.push({ left: a[i], right: b[j], kind: "same" });
      i += 1;
      j += 1;
    } else if (j < m && (i >= n || lcs[i][j + 1] > lcs[i + 1][j])) {
      rows.push({ left: null, right: b[j], kind: "add" });
      j += 1;
    } else {
      rows.push({ left: a[i], right: null, kind: "del" });
      i += 1;
    }
  }
  // Une un «del» seguido de un «add» en una fila «mod».
  const merged: SideRow[] = [];
  for (const row of rows) {
    const last = merged[merged.length - 1];
    if (row.kind === "add" && last && last.kind === "del" && last.right === null) {
      last.right = row.right;
      last.kind = "mod";
    } else {
      merged.push({ ...row });
    }
  }
  return merged;
}

const TOP_KEY = /^([A-Za-z_][A-Za-z0-9_-]*):/;

/** Valor YAML seguro para una línea `clave: valor` (JSON es YAML válido). */
export function yamlScalar(value: string): string {
  if (/^[A-Za-z0-9_.-]+$/.test(value) && !/^(true|false|null|yes|no|on|off|~)$/i.test(value)) return value;
  return JSON.stringify(value);
}

/**
 * Pone (o quita, con value null/"") una clave escalar de primer nivel en template.yaml
 * sin tocar el resto del texto (comentarios incluidos).
 */
export function setTopLevelKey(text: string, key: string, value: string | null): string {
  const lines = text.split("\n");
  const index = lines.findIndex((line) => TOP_KEY.exec(line)?.[1] === key);
  const next = value === null || value === "" ? null : `${key}: ${yamlScalar(value)}`;
  if (index >= 0) {
    if (next === null) lines.splice(index, 1);
    else lines[index] = next;
    return lines.join("\n");
  }
  if (next === null) return text;
  const outputs = lines.findIndex((line) => TOP_KEY.exec(line)?.[1] === "outputs");
  if (outputs >= 0) {
    lines.splice(outputs, 0, next);
    return lines.join("\n");
  }
  return `${text.replace(/\n*$/, "")}\n${next}\n`;
}

/** Lee una clave escalar de primer nivel (sin comillas). */
export function getTopLevelKey(text: string, key: string): string {
  for (const line of text.split("\n")) {
    const match = TOP_KEY.exec(line);
    if (match?.[1] !== key) continue;
    let raw = line.slice(match[0].length).trim();
    if (raw.startsWith('"')) {
      try {
        return JSON.parse(raw) as string;
      } catch {
        return raw.slice(1, -1);
      }
    }
    if (raw.startsWith("'") && raw.endsWith("'")) raw = raw.slice(1, -1).replace(/''/g, "'");
    return raw.replace(/\s+#.*$/, "");
  }
  return "";
}

export type OutputDraft = { template: string; mode: "single" | "per_item"; path: string };

/** Añade una salida al final del bloque `outputs:` (lo crea si no existe). */
export function appendOutput(text: string, output: OutputDraft): string {
  const block = [
    `  - template: ${yamlScalar(output.template)}`,
    `    mode: ${output.mode}`,
    `    path: ${yamlScalar(output.path)}`,
  ];
  const lines = text.replace(/\n*$/, "").split("\n");
  const start = lines.findIndex((line) => TOP_KEY.exec(line)?.[1] === "outputs");
  if (start < 0) return `${lines.join("\n")}\noutputs:\n${block.join("\n")}\n`;
  let end = start + 1;
  while (end < lines.length && (lines[end].startsWith(" ") || lines[end].startsWith("-") || lines[end].trim() === "")) end += 1;
  while (end > start + 1 && lines[end - 1].trim() === "") end -= 1;
  lines.splice(end, 0, ...block);
  return `${lines.join("\n")}\n`;
}

/** Esqueleto de un template nuevo (lo que crea «Nuevo template»). */
export function newTemplateSkeleton(id: string): { spec: string; files: Record<string, string> } {
  return {
    spec: [
      `id: ${id}`,
      "version: 1",
      "description: Describe qué genera este template",
      "scope: site",
      "# for_each / where: qué elementos recorre una salida per_item (y `items` en single).",
      "for_each: \"devices | selectattr('kind', 'equalto', 'baremetal') | list\"",
      "params: {}",
      "outputs:",
      "  - template: main.yaml.j2",
      "    mode: per_item",
      "    path: \"{{ item.name }}.yaml\"",
      "",
    ].join("\n"),
    files: {
      "main.yaml.j2": [
        "# {{ item.hostname }} · sitio {{ site.name }}",
        "hostname: {{ item.hostname }}",
        "interfaces:",
        "{% for iface in item.interfaces %}",
        "  - name: {{ iface.name }}",
        "    vlan: {{ iface.vlan }}",
        "    address: {{ iface.ip }}",
        "{% endfor %}",
        "",
      ].join("\n"),
    },
  };
}

export const SAFE_TEMPLATE_ID = /^[a-z0-9][a-z0-9._-]{0,62}$/;
export const SAFE_FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$/;

export type StatusCounts = { new: number; modified: number; unchanged: number; orphaned: number };

export function driftLabel(counts: StatusCounts): string {
  const parts: string[] = [];
  if (counts.new) parts.push(`${counts.new} nuevo${counts.new === 1 ? "" : "s"}`);
  if (counts.modified) parts.push(`${counts.modified} modificado${counts.modified === 1 ? "" : "s"}`);
  if (counts.orphaned) parts.push(`${counts.orphaned} huérfano${counts.orphaned === 1 ? "" : "s"}`);
  if (!parts.length) return counts.unchanged ? "al día" : "sin archivos";
  return parts.join(" · ");
}

/** Con más archivos que esto, la lista de salida muestra un filtro. */
export const FILE_FILTER_MIN = 6;

/** `a/b/c.yaml` → { dir: "a/b/", name: "c.yaml" } (para listas compactas con la ruta relativa). */
export function splitPath(path: string): { dir: string; name: string } {
  const index = path.lastIndexOf("/");
  return index < 0 ? { dir: "", name: path } : { dir: path.slice(0, index + 1), name: path.slice(index + 1) };
}

/** Filtra por ruta: todos los términos (separados por espacios) deben aparecer, sin distinguir mayúsculas. */
export function filterByPath<T extends { path: string }>(items: T[], query: string): T[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return items;
  return items.filter((item) => {
    const path = item.path.toLowerCase();
    return terms.every((term) => path.includes(term));
  });
}
