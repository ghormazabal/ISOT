import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { pickDefaultRefs } from "./compareRefs.ts";
import type { RefsPayload } from "./types.ts";

const ref = (name: string, sha: string, remote = false) => ({ name, sha, kind: "branch", remote }) as RefsPayload["branches"][number];

function payload(branches: RefsPayload["branches"], head = "c1"): RefsPayload {
  return { branches, tags: [], commits: [], head: { ref: "HEAD", sha: head, short: head } };
}

describe("refs por defecto de Comparar", () => {
  it("base main y head la rama más reciente que no es la actual", () => {
    const refs = payload([ref("ingest/2026-01-02", "c3"), ref("feature/x", "c2"), ref("main", "c1")], "c3");
    assert.deepEqual(pickDefaultRefs(refs), { base: "main", head: "feature/x" });
  });

  it("si solo queda la rama actual, la usa", () => {
    const refs = payload([ref("ingest/2026-01-02", "c3"), ref("main", "c1")], "c3");
    assert.deepEqual(pickDefaultRefs(refs), { base: "main", head: "ingest/2026-01-02" });
  });

  it("no prefiere ninguna rama de la demo por nombre", () => {
    const refs = payload([ref("otra", "c4"), ref("demo/diff-example", "c2"), ref("main", "c1")]);
    assert.equal(pickDefaultRefs(refs).head, "otra");
  });

  it("ignora main remoto y ramas en el mismo commit que la base", () => {
    const refs = payload([ref("origin/main", "c1", true), ref("copia", "c1"), ref("main", "c1")]);
    assert.deepEqual(pickDefaultRefs(refs), { base: "origin/main", head: "" });
  });

  it("sin ramas, todo vacío", () => {
    assert.deepEqual(pickDefaultRefs(payload([])), { base: "", head: "" });
  });
});
