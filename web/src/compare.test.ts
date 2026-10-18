import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { compareFromUrl, defaultCompare, headIsWritable, isLocalRef, LOCAL_REF, normalizeRef, refLabel, swapRefs, withCompareParams } from "./compare.ts";
import type { RefsPayload } from "./types.ts";

describe("refs de Comparar", () => {
  it("acepta local y sus alias, ramas, tags, SHA cortos y HEAD~N", () => {
    for (const value of ["local", "LOCAL", "working", "worktree"]) assert.equal(normalizeRef(value), LOCAL_REF);
    for (const value of ["main", "wizard/x-2", "v1.2", "abc1234", "HEAD~3", "origin/main"]) assert.equal(normalizeRef(value), value);
  });

  it("rechaza inyección y sintaxis rara", () => {
    for (const value of ["", "  ", "a..b", "$(id)", "-x", "main; rm", "x y", "`id`", "HEAD@{1}"]) assert.equal(normalizeRef(value), null, value);
  });

  it("etiquetas compactas", () => {
    assert.equal(refLabel("local"), "Local");
    assert.equal(refLabel("0123456789abcdef0123"), "0123456");
    assert.equal(refLabel("main"), "main");
    assert.ok(isLocalRef("working-tree"));
  });
});

describe("estado de Comparar en la URL", () => {
  it("lee y escribe base/head conservando el resto", () => {
    const href = "http://h/?repo=demo&tab=renders&sub=diff";
    const next = withCompareParams(href, { base: "main", head: "local" });
    assert.equal(next, "/?repo=demo&tab=renders&sub=diff&base=main&head=local");
    assert.deepEqual(compareFromUrl(`http://h${next}`), { base: "main", head: "local" });
    assert.equal(withCompareParams(`http://h${next}`, null), "/?repo=demo&tab=renders&sub=diff");
  });

  it("ignora la URL si falta un lado o es inválido", () => {
    assert.equal(compareFromUrl("http://h/?base=main"), null);
    assert.equal(compareFromUrl("http://h/?base=main&head=a..b"), null);
  });
});

describe("refs por defecto y escritura", () => {
  const payload = (names: string[]): RefsPayload => ({
    branches: names.map((name, i) => ({ name, sha: `c${i}`, kind: "branch" })),
    tags: [],
    commits: [],
    head: { ref: "HEAD", sha: "c0", short: "c0" },
  });

  it("base main y head local", () => {
    assert.deepEqual(defaultCompare(payload(["feature/x", "main"])), { base: "main", head: "local" });
    assert.deepEqual(defaultCompare(null), { base: "HEAD", head: "local" });
  });

  it("swap y head escribible", () => {
    assert.deepEqual(swapRefs({ base: "main", head: "local" }), { base: "local", head: "main" });
    assert.ok(headIsWritable(null));
    assert.ok(headIsWritable({ base: "main", head: "local" }));
    assert.ok(!headIsWritable({ base: "main", head: "wizard/a" }));
    assert.ok(!headIsWritable({ base: "local", head: "main" }));
  });
});
