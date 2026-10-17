import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { isOffBranch, mergeLibraryTemplates, repoSummary } from "./dataRepo.ts";
import type { DataRepoStatus, LibraryTemplate, TemplateIndexEntry } from "./types.ts";

const repo: DataRepoStatus = {
  id: "inv",
  name: "inv",
  path: "/tmp/inv",
  git: true,
  branch: "main",
  head: "0123456789abcdef",
  short: "0123456",
  dirty: false,
  remote: "origin",
  remote_url: null,
  expected_branch: "main",
};

describe("repositorio de datos", () => {
  it("resume nombre, rama, HEAD y cambios sin commit", () => {
    assert.equal(repoSummary(repo), "inv · main · 0123456");
    assert.equal(repoSummary({ ...repo, dirty: true }), "inv · main · 0123456 ● sin commit");
    assert.equal(repoSummary({ ...repo, git: false }), "inv · sin git");
    assert.equal(repoSummary({ ...repo, branch: null }), "inv · detached · 0123456");
  });

  it("detecta rama distinta a la esperada", () => {
    assert.equal(isOffBranch(repo), false);
    assert.equal(isOffBranch({ branch: "wizard/x", expected_branch: "main" }), true);
    assert.equal(isOffBranch({ branch: "wizard/x", expected_branch: null }), false);
  });
});

describe("mergeLibraryTemplates", () => {
  it("agrega solo los de la biblioteca que el repo no tiene", () => {
    const local = [{ name: "rke2-lab", site: "global", scope: "global", path: "cluster-templates/rke2-lab/template.yaml" }] as TemplateIndexEntry[];
    const library = [
      { name: "rke2-lab", library: "@isot", path: "@isot/rke2-lab", distro: "rke2", version: null },
      { name: "rke2-openstack-cilium", library: "@isot", path: "@isot/rke2-openstack-cilium", distro: "rke2", version: null },
    ] as LibraryTemplate[];
    const merged = mergeLibraryTemplates(local, library);
    assert.deepEqual(
      merged.map((item) => [item.name, item.library ?? null]),
      [
        ["rke2-lab", null],
        ["rke2-openstack-cilium", "@isot"],
      ],
    );
  });
});
