import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  GLOBAL_GROUP,
  annotateGraph,
  groupFindingsBySite,
  scopeFindings,
  selectionFromFinding,
  wizardCanCommit,
} from "./findings.ts";
import type { Finding, FindingsPayload, GraphPayload, WizardPreview } from "./types.ts";

function preview(added: Array<{ severity: "error" | "warning" | "info" }>): WizardPreview {
  return {
    findings: {
      added: added.map((item, index) => ({
        id: "K8S-CP-001",
        severity: item.severity,
        entity_kind: "cluster",
        entity_id: "site2/site2-edge",
        site: "site2",
        cluster: "site2-edge",
        message: `n${index}`,
        recommendation: "x",
        path: null,
        graph_id: "cluster:site2:site2-edge",
      })),
      resolved: [],
      current: [],
      blocked: added.some((item) => item.severity === "error"),
    },
  };
}

describe("wizardCanCommit", () => {
  it("bloquea si hay errores nuevos", () => {
    const result = wizardCanCommit(preview([{ severity: "error" }]), true);
    assert.equal(result.ok, false);
    assert.equal(result.reason, "error");
  });

  it("exige checkbox si hay warnings nuevos", () => {
    const pending = wizardCanCommit(preview([{ severity: "warning" }]), false);
    assert.equal(pending.ok, false);
    assert.equal(pending.reason, "warning");
    const accepted = wizardCanCommit(preview([{ severity: "warning" }]), true);
    assert.equal(accepted.ok, true);
  });

  it("permite confirmar sin hallazgos nuevos", () => {
    assert.equal(wizardCanCommit(preview([]), false).ok, true);
  });
});

function finding(partial: Partial<Finding>): Finding {
  return {
    id: "K8S-CP-001",
    severity: "warning",
    entity_kind: "cluster",
    entity_id: "site2/site2-edge",
    site: "site2",
    cluster: "site2-edge",
    message: "m",
    recommendation: "r",
    path: null,
    graph_id: "cluster:site2:site2-edge",
    ...partial,
  };
}

const TEMPLATE_SITE1 = finding({
  id: "K8S-VER-003",
  entity_kind: "template",
  entity_id: "global/rke2-x",
  site: "site1",
  cluster: "site1-apps",
  graph_id: "cluster:site1:site1-apps",
  source_template: "global/rke2-x",
});
const ORPHAN = finding({
  id: "INV-HELM-001",
  severity: "error",
  entity_kind: "template",
  entity_id: "global/orphan",
  site: null,
  cluster: null,
  graph_id: "template:global:orphan",
});

function globalPayload(): FindingsPayload {
  const findings = [finding({}), finding({ severity: "info", site: "site1", cluster: "site1-mgr" }), TEMPLATE_SITE1, ORPHAN];
  return { scope: "all", counts: { error: 1, warning: 2, info: 1, total: 4 }, findings };
}

describe("hallazgos por sitio", () => {
  it("scopeFindings deja solo el sitio (sin globales)", () => {
    const scoped = scopeFindings(globalPayload(), "site1");
    assert.ok(scoped);
    assert.equal(scoped.scope, "site1");
    assert.ok(scoped.findings.every((item) => item.site === "site1"));
    assert.deepEqual(scoped.counts, { error: 0, warning: 1, info: 1, total: 2 });
    assert.equal(scopeFindings(globalPayload(), "site2")?.counts.total, 1);
    assert.equal(scopeFindings(globalPayload(), "all")?.counts.total, 4);
  });

  it("groupFindingsBySite agrupa por sitio y deja Global / plantillas al final", () => {
    const groups = groupFindingsBySite(globalPayload().findings, ["site1", "site2", "site3"]);
    assert.deepEqual(
      groups.map((item) => [item.key, item.counts.total]),
      [
        ["site1", 2],
        ["site2", 1],
        ["site3", 0],
        [GLOBAL_GROUP, 1],
      ],
    );
  });

  it("el nodo de sitio del mapa global suma todos los hallazgos del sitio", () => {
    const graph: GraphPayload = {
      view: "global",
      edges: [],
      nodes: [
        { id: "site:site1", hostname: "SITE1", site: "site1", entity: "site", kind: "site" },
        { id: "site:site2", hostname: "SITE2", site: "site2", entity: "site", kind: "site" },
      ],
    } as unknown as GraphPayload;
    const out = annotateGraph(graph, globalPayload().findings);
    const site1 = out?.nodes.find((item) => item.id === "site:site1");
    const site2 = out?.nodes.find((item) => item.id === "site:site2");
    assert.deepEqual(site1?.finding_counts, { error: 0, warning: 1, info: 1 });
    assert.deepEqual(site2?.finding_counts, { error: 0, warning: 1, info: 0 });
  });

  it("un hallazgo de template atribuido abre el clúster que lo usa", () => {
    assert.deepEqual(selectionFromFinding(TEMPLATE_SITE1), { type: "cluster", site: "site1", name: "site1-apps" });
    assert.deepEqual(selectionFromFinding(ORPHAN), { type: "template", site: "global", name: "orphan" });
  });
});
