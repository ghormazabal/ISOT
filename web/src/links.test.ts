import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  DASH,
  changeRows,
  dashValue,
  edgeFocusIds,
  edgeSelection,
  fallbackDetail,
  findEdge,
  relatedFindings,
  sideRows,
  vlanLabel,
  vlanNote,
} from "./links.ts";
import type { Finding, GraphEdge, GraphPayload, LinkDetail, LinkItem } from "./types.ts";

const edge = {
  id: "site1:LEAF003--site1:NODE007",
  source: "site1:NODE007",
  target: "site1:LEAF003",
  vlans: [101, 102],
  ports: ["Eth1/7", "PortChannel07"],
} as unknown as GraphEdge;

const bondLink = {
  type: "bond",
  label: "bond0",
  declared_by: "source",
  source: { interface: "bond0", bond: "bond0", lacp: "802.3ad", members: ["ens1f0", "ens1f1"], mtu: 9000, extra: { note: "uplink" } },
  target: { port: "Eth1/7", port_channel: "PortChannel07", device_mtu: 9216 },
  vlans: [],
  attributes: {},
  sources: [],
} as unknown as LinkItem;

const detail = {
  kind: "network",
  source: { id: "site1:NODE007", entity: "device", label: "SITE1-NODE007", site: "site1", hostname: "SITE1-NODE007" },
  target: { id: "site1:LEAF003", entity: "device", label: "SITE1-LEAF003", site: "site1", hostname: "SITE1-LEAF003" },
  links: [bondLink],
  vlans: [],
  ports: ["Eth1/7", "PortChannel07"],
  paths: [],
} as unknown as LinkDetail;

function finding(partial: Partial<Finding>): Finding {
  return { id: "X", severity: "warning", message: "", entity_id: "", ...partial } as Finding;
}

describe("dashValue", () => {
  it("usa «—» para valores ausentes", () => {
    assert.equal(dashValue(null), DASH);
    assert.equal(dashValue(undefined), DASH);
    assert.equal(dashValue(""), DASH);
    assert.equal(dashValue([]), DASH);
    assert.equal(dashValue(0), "0");
    assert.equal(dashValue(["a", null]), `a, ${DASH}`);
  });
});

describe("sideRows", () => {
  it("lista los campos por lado sin inventar valores", () => {
    const rows = sideRows(bondLink);
    const byLabel = Object.fromEntries(rows.map((row) => [row.label, row]));
    assert.equal(byLabel["LACP (mode)"].source, "802.3ad");
    assert.equal(byLabel["LACP (mode)"].target, DASH);
    assert.equal(byLabel["PortChannel / LAG"].target, "PortChannel07");
    assert.equal(byLabel["Miembros"].source, "ens1f0, ens1f1");
    assert.equal(byLabel["MTU del equipo"].target, "9216");
    assert.equal(byLabel["IP"].source, DASH);
    assert.equal(byLabel["note"].source, "uplink");
  });
});

describe("selección de aristas", () => {
  it("edgeSelection copia vlans/puertos y findEdge la recupera", () => {
    const selection = edgeSelection(edge);
    assert.deepEqual(selection.vlans, [101, 102]);
    assert.deepEqual(selection.ports, ["Eth1/7", "PortChannel07"]);
    const graph = { nodes: [], edges: [edge] } as unknown as GraphPayload;
    assert.equal(findEdge(graph, edge.id), edge);
    assert.equal(findEdge(graph, "otra"), null);
    assert.equal(findEdge(null, edge.id), null);
    assert.deepEqual(edgeFocusIds(selection), ["site1:NODE007", "site1:LEAF003"]);
    assert.deepEqual(edgeFocusIds(null), []);
  });

  it("fallbackDetail usa solo los datos del grafo", () => {
    const fallback = fallbackDetail(edgeSelection(edge), (id) => id.split(":")[1]);
    assert.equal(fallback.source.label, "NODE007");
    assert.equal(fallback.target.hostname, "LEAF003");
    assert.deepEqual(fallback.links, []);
    assert.deepEqual(
      fallback.vlans.map((vlan) => [vlan.id, vlan.name]),
      [
        [101, null],
        [102, null],
      ],
    );
  });
});

describe("relatedFindings", () => {
  it("incluye hallazgos que nombran ambos extremos o un puerto del enlace", () => {
    const both = finding({ id: "K8S-CP-002", message: "SITE1-NODE007 sin uplink a SITE1-LEAF003" });
    const port = finding({ id: "INV-PORT", graph_id: "site1:LEAF003", message: "Puerto Eth1/7 asignado dos veces" });
    const unrelated = finding({ id: "INV-PORT", graph_id: "site1:LEAF003", message: "Puerto Eth1/70 asignado dos veces" });
    const other = finding({ id: "INV-X", message: "SITE1-NODE009 sin rol" });
    const off = finding({ id: "OFF", severity: "off" as Finding["severity"], message: "SITE1-NODE007 SITE1-LEAF003" });
    assert.deepEqual(
      relatedFindings(detail, [both, port, unrelated, other, off]).map((item) => item.id),
      ["K8S-CP-002", "INV-PORT"],
    );
    assert.deepEqual(relatedFindings(null, [both]), []);
  });
});

describe("formato", () => {
  it("vlanLabel / vlanNote / changeRows", () => {
    assert.equal(vlanLabel({ id: 101, name: "mgmt" } as never), "101 · mgmt");
    assert.equal(vlanLabel({ id: 7 } as never), "7");
    assert.equal(vlanNote({ id: 2002, implicit: true } as never), "implícita (OOB por defecto)");
    assert.equal(vlanNote({ id: 5, extra_host: true } as never), "extra_host_vlans");
    assert.equal(vlanNote({ id: 5, allowed: false } as never), "no está en allowed_vlans");
    assert.equal(vlanNote({ id: 5 } as never), null);
    assert.deepEqual(changeRows([{ path: "target.port", before: "Eth1/5", after: null, status: "removed" }]), [
      { path: "target.port", before: "Eth1/5", after: DASH, status: "removed" },
    ]);
  });
});
