"""Assertions for the connector registry. NO LLM CALLS, NO NETWORK — and that is the point.

    python scripts/check_connectors.py

The registry is the access ask stated as a table: which real endpoints exist, what PSP does
with each today, and what going live costs. Its single most important property is that it is
DECLARATIVE — a screen that probes 46 production endpoints to render itself would be a
liability, and the difference between "declarative" and "accidentally probing" is one careless
import away.

Exit code 0 = clean.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py
from scripts._harness import FAILED, check   # noqa: E402



def main() -> int:
    from app.substrate import connectors as C

    print(f"\n{'-' * 78}\n[1] it cannot call anything\n{'-' * 78}")
    mod_path = Path(C.__file__)
    tree = ast.parse(mod_path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    NETWORK = {"requests", "httpx", "urllib", "urllib3", "socket", "aiohttp", "http"}
    # AST, not a substring scan: `.get(` appears in this module as a dict lookup, so a text
    # search reports a false positive and teaches whoever reads it to ignore the check.
    check("imports no network module", not (imported & NETWORK),
          f"imports: {', '.join(sorted(imported)) or 'nothing'}")
    check("declares itself declarative", C.registry().get("declarative") is True)

    r = C.registry()
    print(f"\n{'-' * 78}\n[2] the table itself\n{'-' * 78}")
    paths = [rt["path"] for g in r["groups"] for rt in g["routes"]] + \
            [o["path"] for o in r["other_services"]]
    check(f"{len(paths)} endpoints catalogued", len(paths) == r["total"],
          f"total says {r['total']}")
    check("no duplicate paths", len(set(paths)) == len(paths),
          str([p for p in set(paths) if paths.count(p) > 1]))
    check("every group carries queue / adapter / status / env / note",
          all({"queue", "adapter", "status", "env", "note", "count"} <= set(g)
              for g in r["groups"]))
    check("every status is one of the three legend values",
          {g["status"] for g in r["groups"]} | {o["status"] for o in r["other_services"]}
          <= set(r["legend"]),
          ", ".join(sorted(r["legend"])))
    check("counts add up per group",
          all(g["count"] == len(g["routes"]) for g in r["groups"]))
    check("by_status sums to the total", sum(r["by_status"].values()) == r["total"],
          f"{r['by_status']} vs {r['total']}")

    print(f"\n{'-' * 78}\n[3] the facts a viewer will ask about\n{'-' * 78}")
    # The two growth endpoints this build actually serves.
    growth = [g for g in r["groups"] if g["group"] == "GROWTH_DASHBOARD_API_ROUTES"]
    check("the growth group is present with both endpoints",
          bool(growth) and growth[0]["count"] == 2)
    # Was: assert every path contains ":hubID", "matching the panel verbatim". That assertion
    # locked the registry to the panel's CLIENT-SIDE route table, which is exactly what turned out
    # to be wrong — the client omits the `/api` prefix because its HTTP layer prepends a base, and
    # the controllers template `{hubId}`. Asserting fidelity to the client table would have kept
    # the registry permanently wrong and called it verified. It now asserts fidelity to the
    # SERVICE, which is what an access request has to be correct against.
    check("growth paths carry the /api prefix the controllers declare",
          all(rt["path"].startswith("/api/v1/captain/growth-dashboard/")
              for rt in growth[0]["routes"]) if growth else False,
          growth[0]["routes"][0]["path"] if growth else "")
    check("and they are hub-templated as the controller declares it",
          all("{hubId}" in rt["path"] for rt in growth[0]["routes"]) if growth else False,
          growth[0]["routes"][0]["path"] if growth else "")
    # The claim from item 1, on the record here too.
    kafka = [o for o in r["other_services"] if "Kafka" in o["path"]]
    check("the missing loss-reversal write path is stated explicitly", bool(kafka),
          kafka[0]["note"][:70] if kafka else "absent — the WRITE_MODE story has no anchor")
    check("   and it is marked as having no adapter",
          bool(kafka) and kafka[0]["status"] == "none")
    # The two access asks that matter most, both visible as gaps.
    joined = " ".join(rt["path"] for g in r["groups"] for rt in g["routes"])
    check("the payments endpoints are catalogued", "/v1/entity/payments/pending" in joined,
          "payment_not_received is ~26% of labelled tickets")
    check("the COD-pendency endpoint is catalogued",
          "/v1/cod-pendency/get-cod-pendency-buckets" in joined, "~16% of tickets")
    check("the scan endpoint is catalogued", "/v1/shipment/tracking-details" in joined,
          "what the Hardstop date rules need")
    check("every 'none' row names why it is not wired",
          all(o["note"] for o in r["other_services"] if o["status"] == "none"))

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"CONNECTOR REGISTRY VERIFIED — {r['total']} endpoints catalogued, nothing called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
