import { useMemo, useState } from "react";

export type ValueKind = "inherited" | "overridden" | "added" | "plain" | "missing";

export type ValueNode = {
  key: string;
  path: string;
  value?: unknown;
  kind?: ValueKind;
  children?: ValueNode[];
  leaf?: boolean;
};

type Props = {
  nodes: ValueNode[];
  query?: string;
  emptyLabel?: string;
  defaultOpen?: boolean;
};

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value === "" ? "(vacío)" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.length === 0) return "[]";
  if (value && typeof value === "object" && Object.keys(value as object).length === 0) return "{}";
  return JSON.stringify(value);
}

export function valuesToTree(obj: unknown, prefix = ""): ValueNode[] {
  if (obj === null || obj === undefined) return [];
  if (Array.isArray(obj)) {
    return obj.map((item, index) => {
      const path = prefix ? `${prefix}[${index}]` : `[${index}]`;
      const children = isNested(item) ? valuesToTree(item, path) : undefined;
      return {
        key: String(index),
        path,
        value: item,
        leaf: !children,
        children,
      };
    });
  }
  if (typeof obj === "object") {
    return Object.entries(obj as Record<string, unknown>).map(([key, value]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      const children = isNested(value) ? valuesToTree(value, path) : undefined;
      return { key, path, value, leaf: !children, children };
    });
  }
  return prefix ? [{ key: prefix, path: prefix, value: obj, leaf: true }] : [];
}

function isNested(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value) && typeof value === "object" && Object.keys(value as object).length > 0;
}

function matchesQuery(node: ValueNode, needle: string): boolean {
  if (!needle) return true;
  if (node.path.toLowerCase().includes(needle) || String(node.key).toLowerCase().includes(needle)) {
    return true;
  }
  return (node.children ?? []).some((child) => matchesQuery(child, needle));
}

export function ValuesTree({ nodes, query = "", emptyLabel = "sin values", defaultOpen = false }: Props) {
  const needle = query.trim().toLowerCase();
  const visible = useMemo(
    () => (needle ? nodes.filter((node) => matchesQuery(node, needle)) : nodes),
    [nodes, needle],
  );
  if (visible.length === 0) {
    return <p className="muted">{emptyLabel}</p>;
  }
  return (
    <ul className="value-tree">
      {visible.map((node) => (
        <TreeItem key={node.path} node={node} needle={needle} defaultOpen={defaultOpen || Boolean(needle)} />
      ))}
    </ul>
  );
}

function TreeItem({ node, needle, defaultOpen }: { node: ValueNode; needle: string; defaultOpen: boolean }) {
  const hasChildren = Boolean(node.children && node.children.length);
  const [open, setOpen] = useState(defaultOpen || Boolean(needle));
  const kids = (node.children ?? []).filter((child) => matchesQuery(child, needle));
  const kind = node.kind ?? "plain";
  return (
    <li className={`value-node kind-${kind}`}>
      <button
        type="button"
        className="value-toggle"
        onClick={() => hasChildren && setOpen((value) => !value)}
        aria-expanded={hasChildren ? open : undefined}
      >
        <span className="value-twist">{hasChildren ? (open ? "▾" : "▸") : "·"}</span>
        <span className="mono value-key">{node.key}</span>
        {!hasChildren ? <span className="mono value-leaf">{formatValue(node.value)}</span> : null}
        {kind !== "plain" ? <span className={`value-tag ${kind}`}>{kindLabel(kind)}</span> : null}
      </button>
      {hasChildren && open ? (
        <ul className="value-tree">
          {kids.map((child) => (
            <TreeItem key={child.path} node={child} needle={needle} defaultOpen={defaultOpen || Boolean(needle)} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function kindLabel(kind: ValueKind): string {
  if (kind === "inherited") return "heredado";
  if (kind === "overridden") return "sobrescrito";
  if (kind === "added") return "efectivo (añadido)";
  if (kind === "missing") return "faltante";
  return "";
}
