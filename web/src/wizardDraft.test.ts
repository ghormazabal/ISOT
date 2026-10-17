import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applyEditToggle,
  clearSession,
  draftsEqual,
  emptyWizardDraft,
  isDraftInProgress,
  loadSession,
  saveSession,
  type StorageLike,
} from "./wizardDraft.ts";

function memory(): StorageLike {
  const data = new Map<string, string>();
  return {
    getItem: (key) => (data.has(key) ? data.get(key)! : null),
    setItem: (key, value) => {
      data.set(key, value);
    },
    removeItem: (key) => {
      data.delete(key);
    },
  };
}

describe("wizardDraft", () => {
  it("empty draft is not in progress", () => {
    assert.equal(isDraftInProgress(emptyWizardDraft()), false);
  });

  it("site wizard or preview counts as in progress", () => {
    const draft = emptyWizardDraft();
    draft.mode = "site";
    assert.equal(isDraftInProgress(draft), true);
    draft.mode = "pick";
    draft.site.step = 4;
    assert.equal(isDraftInProgress(draft), true);
    draft.site.step = 1;
    draft.device.preview = { kind: "device", hostnames: ["SITE1-NODE003"] };
    assert.equal(isDraftInProgress(draft), true);
  });

  it("round-trips session through storage", () => {
    const storage = memory();
    const draft = emptyWizardDraft();
    draft.mode = "site";
    draft.site.step = 4;
    draft.site.id = "site9";
    draft.site.architecture = "spine-leaf";
    draft.site.rules = [{ id: "compute-mlag", attach: "leaf-pair", leafs_per_node: 2 }];
    const lastBrowse = { view: "kubernetes" as const, compare: false, siteFilter: "site1" };
    assert.equal(saveSession({ v: 1, draft, lastBrowse, editing: true }, storage), true);
    const loaded = loadSession(storage);
    assert.ok(loaded);
    assert.equal(loaded.editing, true);
    assert.equal(loaded.lastBrowse.view, "kubernetes");
    assert.equal(loaded.lastBrowse.siteFilter, "site1");
    assert.equal(loaded.draft.mode, "site");
    assert.equal(loaded.draft.site.step, 4);
    assert.equal(loaded.draft.site.id, "site9");
    assert.equal(loaded.draft.site.rules[0]?.id, "compute-mlag");
    assert.equal(draftsEqual(loaded.draft, draft), true);
  });

  it("clearSession drops the key", () => {
    const storage = memory();
    saveSession(
      { v: 1, draft: emptyWizardDraft(), lastBrowse: { view: "network", compare: false, siteFilter: "all" }, editing: false },
      storage,
    );
    clearSession(storage);
    assert.equal(loadSession(storage), null);
  });

  it("loadSession returns null on garbage", () => {
    const storage = memory();
    storage.setItem("isot.wizard.session", "{not json");
    assert.equal(loadSession(storage), null);
  });

  it("device field edits count as in progress", () => {
    const draft = emptyWizardDraft();
    draft.device.edits = { "SITE1-NODE003.oob.ip": "10.1.1.9" };
    assert.equal(isDraftInProgress(draft), true);
  });

  it("Editar toggles back to the previous browse view and site", () => {
    const lastBrowse = { view: "kubernetes" as const, compare: true, siteFilter: "site1" };
    const entering = applyEditToggle("network", lastBrowse);
    assert.equal(entering.view, "wizards");
    assert.equal(entering.compare, false);
    const leaving = applyEditToggle("wizards", lastBrowse);
    assert.equal(leaving.view, "kubernetes");
    assert.equal(leaving.compare, true);
    assert.equal(leaving.siteFilter, "site1");
  });

  it("reload while browsing keeps an in-progress draft", () => {
    const storage = memory();
    const draft = emptyWizardDraft();
    draft.mode = "device";
    draft.device.role = "apps-worker";
    draft.device.count = 3;
    saveSession(
      { v: 1, draft, lastBrowse: { view: "network", compare: false, siteFilter: "site2" }, editing: false },
      storage,
    );
    const loaded = loadSession(storage);
    assert.ok(loaded);
    assert.equal(loaded.editing, false);
    assert.equal(loaded.lastBrowse.siteFilter, "site2");
    assert.equal(isDraftInProgress(loaded.draft), true);
    assert.equal(loaded.draft.device.count, 3);
  });
});
