import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { WorktreeFile } from "./types.ts";
import { commitBlockReason, commitTargets, defaultSelection, groupByArea, isWizardBranch, mergeSelection, newBranchName, slugify, toggle } from "./worktree.ts";

const files: WorktreeFile[] = [
  { path: "_index_manifest.json", status: "modified", area: "index", staged: false },
  { path: "rendered/site1/site/chrony-conf/chrony.conf", status: "untracked", area: "output", staged: false },
  { path: "renders.yaml", status: "modified", area: "renders", staged: false },
  { path: "manifest-templates/chrony-conf/chrony.conf.j2", status: "modified", area: "templates", staged: false },
  { path: "sites/site1/devices/a.yaml", status: "deleted", area: "sites", staged: false },
];

describe("Cambios: agrupar y seleccionar", () => {
  it("agrupa por área en orden fijo", () => {
    assert.deepEqual(
      groupByArea(files).map((group) => [group.id, group.files.length]),
      [["output", 1], ["templates", 1], ["renders", 1], ["sites", 1], ["index", 1]],
    );
  });
  it("por defecto entra todo menos el índice (que se regenera solo)", () => {
    const selected = defaultSelection(files);
    assert.equal(selected.size, 4);
    assert.ok(!selected.has("_index_manifest.json"));
  });
  it("al recargar conserva lo desmarcado y marca lo nuevo", () => {
    const before = files.slice(0, 3);
    const previous = new Set(["rendered/site1/site/chrony-conf/chrony.conf"]);
    const next = mergeSelection(previous, before, files);
    assert.ok(next.has("rendered/site1/site/chrony-conf/chrony.conf"));
    assert.ok(!next.has("renders.yaml")); // desmarcado antes
    assert.ok(next.has("sites/site1/devices/a.yaml")); // nuevo
    assert.deepEqual([...toggle(next, ["renders.yaml"], true)].includes("renders.yaml"), true);
  });
});

describe("Cambios: rama destino del commit", () => {
  it("nunca en main/master; la actual solo si es wizard/…", () => {
    assert.deepEqual(commitTargets({ branch: "main" }), { current: false, defaultTarget: "new" });
    assert.deepEqual(commitTargets({ branch: "wizard/renders-site1" }), { current: true, defaultTarget: "current" });
    assert.equal(isWizardBranch("feature/x"), false);
    assert.equal(isWizardBranch("master"), false);
  });
  it("nombre de la rama nueva como el backend", () => {
    assert.equal(slugify("Renders de Ñuñoa: chrony"), "renders-de-nunoa-chrony");
    assert.equal(newBranchName("", "dos renders y un template", 3), "wizard/dos-renders-y-un-template");
    assert.equal(newBranchName("mi rama", "x", 3), "wizard/mi-rama");
    assert.equal(newBranchName("", "", 2), "wizard/isot-2-archivo-s");
  });
  it("explica por qué no se puede hacer commit", () => {
    const ok = { edit: true, selected: 2, pending: 2, indexOnly: false, target: "new" as const, branch: "main" };
    assert.equal(commitBlockReason(ok), null);
    assert.match(commitBlockReason({ ...ok, target: "current" }) ?? "", /Nunca se hace commit en main/);
    assert.match(commitBlockReason({ ...ok, selected: 0 }) ?? "", /al menos un archivo/);
    assert.equal(commitBlockReason({ ...ok, selected: 0, pending: 0, indexOnly: true }), null); // solo índice
    assert.match(commitBlockReason({ ...ok, edit: false }) ?? "", /Solo lectura/);
  });
});
