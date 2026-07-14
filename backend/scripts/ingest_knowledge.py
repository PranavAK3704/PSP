#!/usr/bin/env python3
"""Ingest knowledge from the source repos into the platform's own corpus.

Reads config/sources.yaml (the repo-swap seam), normalizes every source into a
flat list of knowledge chunks with a common shape, and writes a single snapshot
to data/knowledge/corpus.json. The running engine reads only that snapshot, so
the platform is standalone at runtime and the source repos can be swapped by
editing sources.yaml and re-running this script.

Normalized chunk shape:
    { id, kind, theme, queue, title, text, tags[], source_repo }
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"
OUT_DIR = ROOT / "data" / "knowledge"


def _resolve(home: str, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    return Path(home).expanduser() / rel


def _clean_doc(t: str) -> str:
    """Strip Google-Docs export cruft + embedded Metabase SQL/base64 query blobs,
    but PRESERVE useful links (Google Forms, template sheets) so the engine can
    surface a form/template the SOP tells the partner to use."""
    t = t.replace("&#10;", " ").replace("\\*", "*").replace("\\_", "_").replace("\\\\", "")
    t = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\1 \2", t)   # keep link TEXT and URL
    # drop only a URL's query/fragment (that's where the base64 Metabase blobs live);
    # keep the base path so forms.gle / forms/.../viewform / template links survive.
    t = re.sub(r"(https?://[^\s?#]+)[^\s]*", r"\1", t)
    t = re.sub(r"\S{120,}", "", t)                        # any residual giant token
    t = re.sub(r"[*_]{2,}", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _split_long(heading: str, body: str, limit: int = 1600) -> list[tuple[str, str]]:
    """Keep chunks retrievable — split an over-long section into windows."""
    if len(body) <= limit:
        return [(heading, body)]
    out, i, n = [], 0, 1
    while i < len(body):
        out.append((f"{heading} ({n})", body[i:i + limit]))
        i += limit
        n += 1
    return out


def _chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) blocks at ## / ### boundaries."""
    blocks, heading, body = [], "Overview", []
    for line in text.splitlines():
        if line.startswith("#"):
            if body:
                blocks.append((heading, "\n".join(body).strip()))
                body = []
            heading = line.lstrip("#").strip()
        else:
            body.append(line)
    if body:
        blocks.append((heading, "\n".join(body).strip()))
    return [(h, b) for h, b in blocks if b]


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"  ! skip {path.name}: {e}")
        return None


def ingest() -> dict:
    cfg = yaml.safe_load(SOURCES.read_text())
    home = cfg.get("home", "~")
    src = cfg["sources"]
    chunks: list[dict] = []

    # POLICY vs PROCEDURE (product-owner distinction):
    #   policy    = rigid partner-support rules we own
    #   procedure = functional-team / supply-chain process
    # Heuristic typing at ingest; the KT engine lets a human set it explicitly.
    _POLICY_HINTS = ("waiver", "reversal", "partner right", "presumption", "cap ",
                     "constitution", "appeal", "proportional")

    def _ktype(kind: str, text: str) -> str:
        t = (text or "").lower()
        if kind in ("faq",):
            return "procedure"
        if any(h in t for h in _POLICY_HINTS):
            return "policy"
        return "procedure"

    def add(**kw):
        kw.setdefault("tags", [])
        kw.setdefault("knowledge_type", _ktype(kw.get("kind", ""), kw.get("text", "")))
        chunks.append(kw)

    # ── input-bot: sop_knowledge.json (91 chunks) ──
    p = _resolve(home, src.get("sop_knowledge", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        for c in d.get("chunks", []):
            add(id=c["id"], kind="sop", theme=c.get("theme", ""), queue=c.get("queue_code", ""),
                title=c.get("title", ""), text=c.get("text", ""), tags=c.get("tags", []),
                source_repo="input-bot/sop_knowledge")
        print(f"  + sop_knowledge: {len(d.get('chunks', []))} chunks")

    # ── input-bot: kt_knowledge.json (83 entries) ──
    p = _resolve(home, src.get("kt_knowledge", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        for e in d.get("entries", []):
            add(id=e["id"], kind="kt", theme=e.get("queue", ""), queue=e.get("queue", ""),
                title=e.get("title", ""), text=e.get("knowledge", "") or e.get("raw_text", ""),
                tags=(e.get("tags", []) or []) + (e.get("triggers", []) or []),
                source_repo="input-bot/kt_knowledge")
        print(f"  + kt_knowledge: {len(d.get('entries', []))} entries")

    # ── input-bot: portal_faq.json (47 issues) ──
    p = _resolve(home, src.get("portal_faq", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        n = 0
        for cat, issues in d.get("categories", {}).items():
            for iss in issues:
                add(id=f"faq_{iss.get('issue_id', n)}", kind="faq", theme=cat, queue=cat,
                    title=iss.get("title", ""), text=iss.get("answer_text", ""),
                    tags=[cat, "answerable" if iss.get("answerable") else "escalate"],
                    source_repo="input-bot/portal_faq")
                n += 1
        print(f"  + portal_faq: {n} issues")

    # ── input-bot: kt_ingest_lm_ops.json (44 ops entries) ──
    p = _resolve(home, src.get("kt_ingest_ops", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        for e in d.get("entries", []):
            add(id=e["id"], kind="kt", theme=e.get("queue", ""), queue=e.get("queue", ""),
                title=e.get("title", ""), text=e.get("knowledge", ""),
                tags=(e.get("tags", []) or []) + (e.get("triggers", []) or []),
                source_repo="input-bot/kt_ingest_lm_ops")
        print(f"  + kt_ingest_lm_ops: {len(d.get('entries', []))} entries")

    # ── valmo-l1-agent: sop_structured.json (scenario decision trees) ──
    p = _resolve(home, src.get("sop_structured", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        trees = d if isinstance(d, list) else d.get("sops", [])
        n = 0
        for tree in trees:
            theme = tree.get("problem_theme", "")
            for sc in tree.get("scenarios", []):
                add(id=f"scn_{sc.get('scenario_id', n)}", kind="scenario", theme=theme,
                    queue=tree.get("queue", ""), title=sc.get("label", ""),
                    text=f"Conditions: {'; '.join(sc.get('conditions', []))}. "
                         f"Action: {sc.get('action', '')}. {sc.get('response_to_captain', '')}",
                    tags=(tree.get("trigger_keywords", []) or []) + [sc.get("scenario_id", "")],
                    source_repo="valmo-l1-agent/sop_structured")
                n += 1
        print(f"  + sop_structured: {n} scenarios")

    # ── valmo-platform: seedSops.js (11 authored SOPs — broadest coverage) ──
    import shutil
    import subprocess
    p = _resolve(home, src.get("seed_sops_js", ""))
    node = shutil.which("node")
    if p.exists() and node:
        try:
            raw = subprocess.check_output(
                [node, str(Path(__file__).parent / "extract_seed_sops.mjs"), str(p)],
                text=True, timeout=30)
            for sop in json.loads(raw):
                scenarios = "; ".join(f"{s.get('label', '')} → {s.get('decision', '')}"
                                      for s in sop.get("scenarios", []))
                text = " | ".join(filter(None, [
                    sop.get("summary", ""), f"Intent: {sop.get('intent', '')}",
                    f"Scenarios: {scenarios}",
                    f"Guardrails: {'; '.join(sop.get('guardrails', []))}"]))
                add(id=sop["id"], kind="sop", theme=sop.get("problem_theme", ""),
                    queue=sop.get("queue", "") or sop.get("category", ""),
                    title=sop.get("problem_theme", ""), text=text,
                    tags=(sop.get("trigger_keywords", []) or []) + [sop.get("category", "")],
                    source_repo="valmo-platform/seedSops")
            print(f"  + seedSops (authored): parsed {len(json.loads(raw))} SOPs")
        except Exception as e:  # noqa: BLE001
            print(f"  ! seedSops skip: {e}")
    elif p.exists():
        print("  ! seedSops skip: node not found (needed to parse the JS module)")

    # ── valmo-l1-agent: valmo_kt.md (core domain KT — chunk by heading) ──
    p = _resolve(home, src.get("valmo_kt_md", ""))
    if p.exists():
        blocks = _chunk_markdown(p.read_text())
        for i, (heading, body) in enumerate(blocks):
            add(id=f"valmokt_{i}", kind="kt", theme="domain", queue="general",
                title=heading, text=body, tags=["valmo", "domain", "kt"],
                source_repo="valmo-l1-agent/valmo_kt")
        print(f"  + valmo_kt.md: {len(blocks)} sections")

    # ── valmo-l1-agent: stage0_domain.json (per-queue taxonomy) ──
    p = _resolve(home, src.get("stage0_domain", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        n = 0
        for qname, q in (d.get("queues", {}) or {}).items():
            for lt, lt_def in (q.get("loss_type_taxonomy", {}) or {}).items():
                add(id=f"domain_{qname}_{lt}", kind="kt", theme=qname, queue=qname,
                    title=f"{qname}: {lt}",
                    text=f"{lt_def.get('physical_event', '')} Triggers: {lt_def.get('trigger_scans', '')}",
                    tags=(lt_def.get("captain_signals", []) or []) + [qname, lt],
                    source_repo="valmo-l1-agent/stage0_domain")
                n += 1
        if n:
            print(f"  + stage0_domain: {n} taxonomy entries")

    # ══ CANONICAL sources (source of truth) ═════════════════════════════════
    can = cfg.get("canonical", {})

    # Live SOP Redressal Tracker (published CSV) — replaces the stale agent_sops.
    import io
    rows = None
    url = can.get("sop_tracker_csv_url", "")
    if url:
        try:
            import requests
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            rows = list(csv.reader(io.StringIO(resp.text)))
            print("  + sop_tracker: fetched LIVE")
        except Exception as e:  # noqa: BLE001
            print(f"  ! sop_tracker live fetch failed ({e}); using snapshot")
    if rows is None:
        snap = _resolve(home, can.get("sop_tracker_snapshot", ""))
        if snap.exists():
            rows = list(csv.reader(snap.read_text().splitlines()))
            print("  + sop_tracker: snapshot")
    if rows:
        hi = next((i for i, r in enumerate(rows) if r and r[0].strip() == "L3 category"), None)
        n, cur = 0, ""
        for r in (rows[hi + 1:] if hi is not None else []):
            if not any(c.strip() for c in r):
                continue
            l3 = r[0].strip() or cur
            cur = l3
            scen = r[2].strip() if len(r) > 2 else ""
            if not scen:
                continue
            g = lambda i: (r[i].strip() if len(r) > i else "")  # noqa: E731
            text = " | ".join(filter(None, [
                f"Scenario: {scen}", f"L1 SOP: {g(3)}", f"Escalation: {g(5)}",
                f"L2 SOP: {g(6)}", f"Functional dependency: {g(7)}", f"TAT: {g(8)}",
                f"POCs: {g(9)}", f"Template: {g(11)}"]))
            add(id=f"tracker_{n}", kind="playbook", theme=l3, queue=l3, title=scen[:90],
                text=text, tags=[l3, g(4)], source_repo="canonical/sop_tracker_sheet")
            n += 1
        print(f"  + sop_tracker (canonical): {n} scenarios")

    # Collated SOPs doc (Drive snapshot) — cleaned + heading-chunked.
    p = _resolve(home, can.get("collated_sops_doc", ""))
    if p.exists():
        blocks = _chunk_markdown(_clean_doc(p.read_text()))
        n = 0
        for heading, body in blocks:
            for h, b in _split_long(heading, body):
                if len(b) < 40:
                    continue
                add(id=f"collated_{n}", kind="sop", theme=heading[:40], queue=heading[:40],
                    title=heading[:90], text=b, tags=["collated_sops", heading[:30]],
                    source_repo="canonical/collated_sops_doc")
                n += 1
        print(f"  + collated_sops doc (canonical): {n} chunks")

    # Supplemental (hand-authored gap fixes).
    p = _resolve(home, can.get("supplemental", ""))
    d = _load_json(p) if p.exists() else None
    if d:
        for e in d.get("entries", []):
            add(id=e["id"], kind=e.get("kind", "sop"), theme=e.get("theme", ""),
                queue=e.get("queue", ""), title=e.get("title", ""), text=e.get("text", ""),
                tags=e.get("tags", []), source_repo="canonical/supplemental")
        print(f"  + supplemental (canonical): {len(d.get('entries', []))} entries")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "_about": "Ingested knowledge snapshot for the Valmo Partner Support Platform demo.",
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    (OUT_DIR / "corpus.json").write_text(json.dumps(out, indent=1))

    # Copy the SLA matrix verbatim (used by the policy engine for TAT checks).
    sla = _resolve(home, src.get("losses_sla", ""))
    if sla.exists():
        rows = list(csv.reader(sla.read_text().splitlines()))
        (OUT_DIR / "losses_sla.json").write_text(json.dumps(rows, indent=1))
        print(f"  + losses_sla: {len(rows)} rows")

    print(f"\n  = wrote {len(chunks)} chunks -> {OUT_DIR / 'corpus.json'}")
    return out


if __name__ == "__main__":
    print("Ingesting knowledge from source repos (config/sources.yaml)...")
    result = ingest()
    if result["chunk_count"] == 0:
        print("\n  WARNING: 0 chunks ingested. Check paths in config/sources.yaml.")
        sys.exit(1)
