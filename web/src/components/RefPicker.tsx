import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { GitRef, RefsPayload } from "../types";

type Props = {
  id: string;
  label: string;
  refs: RefsPayload | null;
  branch: string;
  commit: string;
  custom: string;
  resolvedSha?: string | null;
  onBranch: (value: string) => void;
  onCommit: (value: string) => void;
  onCustom: (value: string) => void;
};

export function effectiveRef(branch: string, commit: string, custom: string): string {
  const pasted = custom.trim();
  if (pasted) return pasted;
  return commit || branch;
}

export function RefPicker({
  id,
  label,
  refs,
  branch,
  commit,
  custom,
  resolvedSha,
  onBranch,
  onCommit,
  onCustom,
}: Props) {
  const [commits, setCommits] = useState<GitRef[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!branch) {
      setCommits([]);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .commits(branch)
      .then((payload) => {
        if (cancelled) return;
        setCommits(payload.commits);
        const tip = payload.commits[0]?.sha || payload.sha;
        if (tip && !payload.commits.some((item) => item.sha === commit) && !custom.trim()) {
          onCommit(tip);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setCommits([]);
          setError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [branch]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return commits;
    return commits.filter((item) =>
      [item.short, item.sha, item.subject, item.author, item.date, item.name]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [commits, search]);

  const usingCustom = Boolean(custom.trim());

  return (
    <fieldset className="ref-picker">
      <legend>{label}</legend>
      <label>
        <span>Rama / tag</span>
        <select
          id={`${id}-branch`}
          name={`${id}-branch`}
          value={branch}
          onChange={(event) => {
            onCustom("");
            onCommit("");
            onBranch(event.target.value);
          }}
        >
          {refOptions(refs)}
        </select>
      </label>
      <label>
        <span>Buscar commit</span>
        <input
          id={`${id}-search`}
          name={`${id}-search`}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="SHA, mensaje, autor, fecha"
          autoComplete="off"
          disabled={usingCustom}
        />
      </label>
      <label>
        <span>Commit de la rama</span>
        <select
          id={`${id}-commit`}
          name={`${id}-commit`}
          value={commit}
          onChange={(event) => onCommit(event.target.value)}
          disabled={usingCustom || filtered.length === 0}
        >
          {filtered.length === 0 ? <option value="">sin commits</option> : null}
          {filtered.map((item) => (
            <option key={item.sha} value={item.sha}>
              {formatCommit(item)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>SHA o expresión</span>
        <input
          id={`${id}-custom`}
          name={`${id}-custom`}
          value={custom}
          onChange={(event) => onCustom(event.target.value)}
          placeholder="abc1234 o HEAD~3"
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <p className="ref-resolved mono">
        {usingCustom ? `ref ${custom.trim()}` : null}
        {resolvedSha ? ` → ${resolvedSha.slice(0, 12)}` : null}
        {error ? <span className="error"> {error}</span> : null}
      </p>
    </fieldset>
  );
}

function formatCommit(item: GitRef): string {
  const when = item.date ? item.date.replace("T", " ").slice(0, 16) : "";
  const author = item.author ? ` · ${item.author}` : "";
  return `${item.short || item.name} ${item.subject || ""}${author} ${when}`.trim();
}

function refOptions(payload: RefsPayload | null) {
  if (!payload) return <option value="">sin refs</option>;
  return (
    <>
      <optgroup label="Ramas">
        {payload.branches.map((item) => (
          <option key={`b-${item.name}`} value={item.name}>
            {item.name}
            {item.remote ? " (remote)" : ""}
          </option>
        ))}
      </optgroup>
      {payload.tags.length > 0 ? (
        <optgroup label="Tags">
          {payload.tags.map((item) => (
            <option key={`t-${item.name}`} value={item.name}>
              {item.name}
            </option>
          ))}
        </optgroup>
      ) : null}
    </>
  );
}
