import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { repoUrl } from "./api.ts";
import { filterRepos, loadStoredRepo, pickRepo, repoFromUrl, repoState, storeRepo, withRepoParam } from "./repos.ts";
import { hasSavedDraft, loadSession, memoryStorage, saveSession, sessionKey, emptyWizardDraft } from "./wizardDraft.ts";

const repos = [
  { id: "norte", name: "Demo norte", path: "demo-norte", branch: "main" },
  { id: "sur", name: "Demo sur", path: "demo-sur", branch: "lab" },
];

describe("selección de repositorio", () => {
  it("URL > localStorage > default_repo > primero", () => {
    assert.equal(pickRepo(repos, "norte", "sur", null), "sur");
    assert.equal(pickRepo(repos, "norte", null, "sur"), "sur");
    assert.equal(pickRepo(repos, "sur", null, null), "sur");
    assert.equal(pickRepo(repos, null, "nada", "otro"), "norte");
    assert.equal(pickRepo([], null, "x", null), null);
  });

  it("filtra por id, nombre, carpeta y rama", () => {
    assert.deepEqual(filterRepos(repos, "SUR").map((r) => r.id), ["sur"]);
    assert.deepEqual(filterRepos(repos, "lab").map((r) => r.id), ["sur"]);
    assert.deepEqual(filterRepos(repos, "demo-").map((r) => r.id), ["norte", "sur"]);
    assert.equal(filterRepos(repos, "  ").length, 2);
  });

  it("estado corto", () => {
    assert.equal(repoState({ available: false, git: false, dirty: false, error: "x" }).kind, "unavailable");
    assert.equal(repoState({ available: true, git: false, dirty: false, error: null }).kind, "nogit");
    assert.equal(repoState({ available: true, git: true, dirty: true, error: null }).label, "● sin commit");
    assert.equal(repoState({ available: true, git: true, dirty: false, error: null }).kind, "clean");
  });

  it("?repo= en la URL conserva el resto", () => {
    assert.equal(withRepoParam("/?a=1#x", "sur"), "/?a=1&repo=sur#x");
    assert.equal(withRepoParam("/?repo=sur&a=1", null), "/?a=1");
    assert.equal(repoFromUrl("/?repo=norte"), "norte");
    assert.equal(repoFromUrl("/"), null);
  });

  it("recuerda el último repositorio", () => {
    const storage = memoryStorage();
    assert.equal(loadStoredRepo(storage), null);
    storeRepo(storage, "sur");
    assert.equal(loadStoredRepo(storage), "sur");
    assert.equal(loadStoredRepo(null), null);
  });

  it("prefijo /api/repos/{id}/ en las llamadas de datos", () => {
    assert.equal(repoUrl("/api/sites", "sur"), "/api/repos/sur/sites");
    assert.equal(repoUrl("/api/diff?base=a&head=b", "sur"), "/api/repos/sur/diff?base=a&head=b");
    assert.equal(repoUrl("/api/repos", "sur"), "/api/repos");
    assert.equal(repoUrl("/api/repos/norte", "sur"), "/api/repos/norte");
    assert.equal(repoUrl("/api/sites", null), "/api/sites");
    assert.equal(repoUrl("/assets/x.js", "sur"), "/assets/x.js");
  });
});

describe("borradores por repositorio", () => {
  it("cada repo guarda su sesión del wizard", () => {
    const storage = memoryStorage();
    const draft = emptyWizardDraft();
    draft.mode = "device";
    draft.device.role = "otro-rol";
    draft.device.count = 3;
    const session = { v: 1 as const, draft, lastBrowse: { view: "network" as const, compare: false, siteFilter: "site3" }, editing: true };
    assert.ok(saveSession(session, storage, "sur"));
    assert.equal(loadSession(storage, "norte"), null);
    assert.equal(loadSession(storage, "sur")?.lastBrowse.siteFilter, "site3");
    assert.equal(sessionKey("sur"), "isot.wizard.session:sur");
    assert.equal(hasSavedDraft("sur", storage), true);
    assert.equal(hasSavedDraft("norte", storage), false);
  });
});
