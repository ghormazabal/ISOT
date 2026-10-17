import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parseInline, parseMarkdown, safeHref } from "./markdown.ts";
import {
  appendOutput,
  filterByPath,
  SAVE_STATE_LABEL,
  saveBlockReason,
  splitPath,
  buildTree,
  classifyDiffLine,
  driftLabel,
  getTopLevelKey,
  rendersFromUrl,
  setTopLevelKey,
  sideBySide,
  withRendersParam,
} from "./renders.ts";

describe("renders URL", () => {
  it("lee y escribe tab=renders&sub=… conservando ?repo=", () => {
    assert.deepEqual(rendersFromUrl("http://x/?repo=demo&tab=renders&sub=diff"), { renders: true, sub: "diff" });
    assert.deepEqual(rendersFromUrl("http://x/?tab=renders&sub=nope"), { renders: true, sub: "resumen" });
    assert.equal(rendersFromUrl("http://x/?repo=a").renders, false);
    assert.equal(withRendersParam("http://x/?repo=a", "plantillas"), "/?repo=a&tab=renders&sub=plantillas");
    assert.equal(withRendersParam("http://x/?repo=a&tab=renders&sub=diff#h", null), "/?repo=a#h");
  });
});

describe("árbol y diff", () => {
  it("agrupa rutas en carpetas, carpetas primero", () => {
    const tree = buildTree([{ path: "b.yaml" }, { path: "nodes/n2.yaml" }, { path: "nodes/n1.yaml" }]);
    assert.deepEqual(
      tree.map((n) => n.name),
      ["nodes", "b.yaml"],
    );
    assert.deepEqual(
      tree[0].children.map((n) => n.path),
      ["nodes/n1.yaml", "nodes/n2.yaml"],
    );
  });
  it("clasifica líneas de diff unificado", () => {
    assert.equal(classifyDiffLine("+++ b/x"), "meta");
    assert.equal(classifyDiffLine("@@ -1 +1 @@"), "hunk");
    assert.equal(classifyDiffLine("+a"), "add");
    assert.equal(classifyDiffLine("-a"), "del");
    assert.equal(classifyDiffLine(" a"), "ctx");
  });
  it("lado a lado: iguales, modificadas, añadidas", () => {
    const rows = sideBySide("a\nb\nc\n", "a\nB\nc\nd\n");
    assert.deepEqual(
      rows.map((r) => r.kind),
      ["same", "mod", "same", "add"],
    );
    assert.equal(sideBySide(null, "x\n")[0].kind, "add");
  });
  it("etiqueta de drift", () => {
    assert.equal(driftLabel({ new: 1, modified: 2, unchanged: 3, orphaned: 0 }), "1 nuevo · 2 modificados");
    assert.equal(driftLabel({ new: 0, modified: 0, unchanged: 3, orphaned: 0 }), "al día");
  });
});

describe("edición de template.yaml", () => {
  const spec = "# comentario\nid: t\nscope: site\noutputs:\n  - template: a.j2\n    path: a.yaml\n";
  it("cambia, inserta y quita claves de primer nivel sin tocar comentarios", () => {
    const changed = setTopLevelKey(spec, "scope", "cluster");
    assert.match(changed, /^# comentario\nid: t\nscope: cluster\n/);
    const inserted = setTopLevelKey(spec, "for_each", "devices | list");
    assert.match(inserted, /for_each: "devices \| list"\noutputs:/);
    assert.equal(getTopLevelKey(inserted, "for_each"), "devices | list");
    assert.doesNotMatch(setTopLevelKey(spec, "scope", null), /scope:/);
  });
  it("añade una salida al bloque outputs", () => {
    const next = appendOutput(spec, { template: "b.j2", mode: "per_item", path: "{{ item.name }}.yaml" });
    assert.match(next, /path: a.yaml\n {2}- template: b.j2\n {4}mode: per_item\n {4}path: "\{\{ item.name \}\}.yaml"\n$/);
  });
});

describe("markdown de la guía", () => {
  it("títulos, listas, tablas y código", () => {
    const blocks = parseMarkdown("# Guía\n\nTexto `x` **y**.\n\n- uno\n- dos\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```yaml\nk: v\n```\n");
    assert.deepEqual(
      blocks.map((b) => b.kind),
      ["heading", "paragraph", "list", "table", "code"],
    );
    assert.deepEqual(
      parseInline("a `b` **c** [d](https://e)").map((i) => i.kind),
      ["text", "code", "text", "strong", "text", "link"],
    );
    assert.equal(safeHref("javascript:alert(1)"), null);
    assert.equal(safeHref("#seccion"), "#seccion");
  });
});

describe("lista de archivos de salida", () => {
  it("separa carpeta y nombre", () => {
    assert.deepEqual(splitPath("site3/edge/nads.yaml"), { dir: "site3/edge/", name: "nads.yaml" });
    assert.deepEqual(splitPath("solo.yaml"), { dir: "", name: "solo.yaml" });
  });
  it("filtra por todos los términos de la ruta", () => {
    const items = [{ path: "nodes/n1/netplan.yaml" }, { path: "nodes/n2/netplan.yaml" }, { path: "all-nodes.yaml" }];
    assert.equal(filterByPath(items, "").length, 3);
    assert.deepEqual(filterByPath(items, "N2").map((i) => i.path), ["nodes/n2/netplan.yaml"]);
    assert.deepEqual(filterByPath(items, "nodes yaml").length, 3);
    assert.deepEqual(filterByPath(items, "all nodes").map((i) => i.path), ["all-nodes.yaml"]);
    assert.equal(filterByPath(items, "zzz").length, 0);
  });
});

describe("guardar la salida en el repo", () => {
  const ok = { canWrite: true, writeReason: "", docSource: "repo" as const, dirty: false, renderErrors: 0, hasPreview: true };
  it("se puede con edición, template guardado y preview sin errores", () => {
    assert.equal(saveBlockReason(ok), null);
    assert.equal(saveBlockReason({ ...ok, docSource: "bundled" }), null);
  });
  it("explica por qué no", () => {
    assert.equal(saveBlockReason({ ...ok, canWrite: false, writeReason: "Solo lectura" }), "Solo lectura");
    assert.match(saveBlockReason({ ...ok, docSource: "draft" }) ?? "", /sin guardar/);
    assert.match(saveBlockReason({ ...ok, dirty: true }) ?? "", /cambios sin guardar/);
    assert.match(saveBlockReason({ ...ok, renderErrors: 2 }) ?? "", /2 error/);
    assert.match(saveBlockReason({ ...ok, hasPreview: false }) ?? "", /preview/);
    assert.equal(saveBlockReason({ ...ok, statusError: "carpeta inválida" }), "carpeta inválida");
  });
  it("etiquetas del estado en el repo", () => {
    assert.equal(SAVE_STATE_LABEL.al_dia, "al día");
    assert.equal(SAVE_STATE_LABEL.no_existe, "no existe");
  });
});
