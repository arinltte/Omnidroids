#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gold-standard toolkit, part 2: the scorer (HANDOFF §9 G item 1).

``docs/GOLD_STANDARD.md`` defines the metrics; this file implements them and
refuses to hide anything. Three modes:

* ``--check``  — validate the gold annotation against the frozen slice before it
  is trusted: every entity name/alias must occur verbatim in the chunk the
  annotation says it does, every gold type must exist in the ontology, every gold
  relation must exist, every gold edge's endpoints must point at gold entities.
  A gold standard that is not grounded is not a standard.
* ``--score``  — score one hypothesis (``gold_dump_graph.py`` output, or any JSON
  with ``nodes``/``edges``) against the gold and write the metrics + the
  adjudication queue. Nothing is auto-accepted: hypothesis items that match
  neither a gold nor a junk entry land in ``review_queue`` and are counted as
  false positives until a human rules on them (two precision numbers are
  emitted, pre- and post-adjudication, so no one reads the flattering one).
* ``--agree``  — inter-annotator agreement between two gold files (the frozen
  gold and an independent second pass), on gold/borderline/junk tiers and on the
  gold edge set.

Determinism: matching is exact-normalised string equality against manual
``match_keys`` (plus the annotation's own name/aliases). Fuzzy containment
matches are reported as *candidates* and never counted as hits unless
``--match fuzzy`` is given, so the reported precision cannot be inflated by a
substring accident.

Usage (from backend/):
    .venv/bin/python ../scratch/gold_score.py --check \\
        --gold ../docs/gold_abfix_slice8.json --slice ../scratch/abfix_slice8.json
    .venv/bin/python ../scratch/gold_score.py --score \\
        --gold ../docs/gold_abfix_slice8.json \\
        --hypothesis ../scratch/gold_hypothesis_ab8before2_1794285607.json \\
        --run ../scratch/chunkslice_ab8before2_1794285607.json \\
        --out ../scratch/gold_score_ab8before2.json
    .venv/bin/python ../scratch/gold_score.py --agree \\
        --gold ../docs/gold_abfix_slice8.json --other ../scratch/gold_second_annotator.json
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

PUNCT = "“”‘’\"'`。，、；：,;:!！?？（）()【】[]{}《》〈>-—…·|/\\\t\r\n "
_WS = re.compile(r"\s+")


def norm(text) -> str:
    """Normalisation used by every match in this scorer (see docs §3.1)."""

    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = _WS.sub("", text)
    return text.strip(PUNCT).strip().lower()


def norm_rel(text) -> str:
    return re.sub(r"[\s_\-]+", "", norm(text)).upper()


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if text in ("NaT", "None", ""):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(text[: len(fmt) + 8], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_gold(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        gold = json.load(f)
    for key in ("entities", "edges", "ontology"):
        if key not in gold:
            raise SystemExit(f"[FAIL] {path}: missing '{key}' — not a gold annotation")
    return gold


def load_slice(path: str) -> dict[int, str]:
    if not os.path.exists(path):
        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 os.path.basename(path))
        path = candidate if os.path.exists(candidate) else None
    if not path:
        raise SystemExit(f"[FAIL] slice not found: {path}")
    with open(path, encoding="utf-8") as f:
        return {item["index"]: item["text"] for item in json.load(f)["slice"]}


def tiers(gold: dict) -> dict:
    out = {"gold": [], "borderline": [], "junk": []}
    for entry in gold["entities"]:
        out.setdefault(entry["tier"], []).append(entry)
    return out


# ---------------------------------------------------------------------------
# --check : is the gold grounded?
# ---------------------------------------------------------------------------
def check_gold(gold: dict, texts: dict[int, str]) -> int:
    problems: list[str] = []
    entity_types = set(gold["ontology"]["entity_types"])
    edge_types = set(gold["ontology"]["edge_types"])
    by_id = {e["id"]: e for e in gold["entities"]}
    seen_names: dict[str, str] = {}

    for entry in gold["entities"]:
        eid, name = entry["id"], norm(entry["name"])
        if entry.get("type") and entry["type"] not in entity_types:
            problems.append(f"{eid}: type '{entry['type']}' is not in the ontology")
        if entry.get("tier") not in ("gold", "borderline", "junk"):
            problems.append(f"{eid}: unknown tier '{entry.get('tier')}'")
        if not name:
            problems.append(f"{eid}: empty name")
        if name in seen_names:
            problems.append(f"{eid}: duplicate normalised name of {seen_names[name]}")
        else:
            seen_names[name] = eid
        # grounding: the name, and every alias, must appear verbatim in a chunk
        # the annotation claims (or, for pure match_keys, in at least one chunk)
        surfaces = [("name", entry["name"])] + [
            ("alias", a) for a in entry.get("aliases", [])]
        chunks = set(entry.get("chunks", []) or [])
        if entry.get("mentions"):
            chunks |= {m["chunk"] for m in entry["mentions"]}
        if not chunks:
            problems.append(f"{eid}: no chunk attributed")
        for kind, surface in surfaces:
            # compared whitespace-insensitively: the PDF text extractor wraps mid-word,
            # and the scorer normalises the same way, so a line break inside a term
            # must not read as "the gold is not grounded in the document".
            where = [i for i, text in texts.items()
                     if norm(surface) in norm(text)]
            if not where:
                problems.append(f"{eid}: {kind} '{surface}' does not occur verbatim in the slice")
                continue
            if chunks and not (set(where) & chunks):
                problems.append(
                    f"{eid}: {kind} '{surface}' occurs in chunks {where} but is attributed to {sorted(chunks)}")

    for edge in gold["edges"]:
        for field, key in (("source", "source"), ("target", "target")):
            ref = edge.get(key)
            if ref not in by_id:
                problems.append(f"{edge['id']}: {field} '{ref}' is not a known entity id")
        if by_id.get(edge.get("source"), {}).get("tier") != "gold":
            problems.append(f"{edge['id']}: source is not gold-tier")
        if by_id.get(edge.get("target"), {}).get("tier") != "gold":
            problems.append(f"{edge['id']}: target is not gold-tier")
        if edge.get("relation") and edge["relation"] not in edge_types:
            problems.append(f"{edge['id']}: relation '{edge['relation']}' is not in the ontology")
        if edge.get("temporal", {}).get("valid_at", "unstated") not in ("unstated", None):
            parse_date(edge["temporal"]["valid_at"])
            if not parse_date(edge["temporal"]["valid_at"]):
                problems.append(f"{edge['id']}: unparseable valid_at {edge['temporal']['valid_at']}")

    gold_n = sum(1 for e in gold["entities"] if e["tier"] == "gold")
    border_n = sum(1 for e in gold["entities"] if e["tier"] == "borderline")
    junk_n = sum(1 for e in gold["entities"] if e["tier"] == "junk")
    print(f"[check] {gold_n} gold / {border_n} borderline / {junk_n} junk entities, "
          f"{len(gold['edges'])} gold edges over {len(texts)} chunks")
    if problems:
        print(f"[check] {len(problems)} PROBLEM(S):")
        for problem in problems:
            print(f"[check]   - {problem}")
        return 1
    print("[check] PASS — every surface form is grounded in the slice text, every "
          "gold type/relation exists in the ontology, every edge endpoint is gold-tier")
    return 0


# ---------------------------------------------------------------------------
# --score : hypothesis vs gold
# ---------------------------------------------------------------------------
def match_keys(entry: dict, policy: str) -> list[str]:
    keys = set(entry.get("match_keys", []))
    keys.add(entry["name"])
    if policy in ("alias", "fuzzy"):
        keys |= set(entry.get("aliases", []))
    return [norm(k) for k in keys if norm(k)]


def index_gold(gold: dict, policy: str) -> dict:
    """normalised surface -> the entry it resolves to (first entry wins)."""

    index: dict[str, dict] = {}
    for tier in ("gold", "borderline", "junk"):
        for entry in tiers(gold)[tier]:
            for key in match_keys(entry, policy):
                index.setdefault(key, dict(entry, _tier=tier))
    return index


def entry_chunks(entry: dict) -> set[int]:
    """The chunks a gold entity is attributable to (its mentions)."""

    chunks = set(entry.get("chunks", []) or [])
    chunks |= {m["chunk"] for m in entry.get("mentions", []) or []}
    return chunks


def score(gold: dict, hypothesis: dict, policy: str, run: dict | None,
          texts: dict[int, str] | None = None) -> dict:
    index = index_gold(gold, policy)
    ontology_edge_types = set(gold["ontology"]["edge_types"])
    gold_edges = gold["edges"]
    nodes = hypothesis.get("nodes", [])
    edges = hypothesis.get("edges", [])

    # ---- entities -------------------------------------------------------
    node_rows = []
    by_gold_id: dict[str, list[str]] = {}
    junk_hits: list[dict] = []
    review: list[dict] = []
    for node in nodes:
        key = norm(node.get("name"))
        entry = index.get(key)
        row = {"name": node.get("name"), "type": node.get("type"),
               "labels": node.get("labels"), "summary": node.get("summary"),
               "chunks_verbatim": node.get("attributed_chunks_verbatim"),
               "verdict": None, "gold_id": None, "gold_type": None,
               "gold_tier": None, "reason": None, "candidate": None}
        if entry is not None:
            row.update(verdict=entry["tier"], gold_id=entry["id"],
                       gold_type=entry.get("type"), gold_tier=entry["_tier"],
                       reason=entry.get("reason"))
            if entry["_tier"] == "gold":
                by_gold_id.setdefault(entry["id"], []).append(node["name"])
            elif entry["_tier"] == "junk":
                junk_hits.append(row)
        elif policy == "fuzzy":
            candidate = fuzzy_candidate(key, index)
            if candidate:
                row.update(verdict="fuzzy-candidate", candidate=candidate["id"],
                           gold_tier=candidate["_tier"])
        if row["verdict"] is None:
            row["verdict"] = "unknown"
            review.append({"kind": "node", "name": node.get("name"),
                           "type": node.get("type"),
                           "summary": node.get("summary")[:200],
                           "question": "no gold or junk surface matches this node: "
                                       "is it a true positive the gold missed, or a false positive?",
                           "adjudication": None})
        node_rows.append(row)

    gold_entries = tiers(gold)["gold"]
    borderline_entries = tiers(gold)["borderline"]
    # Which chunks did the run actually attempt? A skipped chunk cannot produce its
    # entities, so recall over the whole slice conflates "the model missed it" with
    # "the chunk failed before extraction ran". Both numbers are reported.
    processed = {ep["index"] for ep in hypothesis.get("episodes", [])
                 if ep.get("index") is not None}
    attempted_entries = [e for e in gold_entries if entry_chunks(e) & processed]
    unattempted_entries = [e for e in gold_entries if not (entry_chunks(e) & processed)]
    found_ids = set(by_gold_id)
    resolved_borderline = [r for r in node_rows if r["gold_tier"] == "borderline"]
    unknown = [r for r in node_rows if r["verdict"] == "unknown"]
    fuzzy = [r for r in node_rows if r["verdict"] == "fuzzy-candidate"]

    duplicate_ids = {gid: names for gid, names in by_gold_id.items() if len(names) > 1}
    type_checked = [r for r in node_rows if r["gold_tier"] == "gold"]
    type_ok = [r for r in type_checked if norm(r["type"]) == norm(r["gold_type"])]
    untyped = sum(1 for n in nodes if not n.get("type"))
    off_ontology_labels = sum(1 for n in nodes if n.get("type") and n["type"] not in
                              set(gold["ontology"]["entity_types"]))

    # attribute support: does the text-supported value survive in the node?
    attr_total = attr_hit = 0
    attr_detail = []
    node_by_name = {norm(n.get("name")): n for n in nodes}
    for entry in gold_entries:
        source = next((r for r in type_checked if r["gold_id"] == entry["id"]), None)
        if source is None:
            continue
        hypothesis_node = node_by_name.get(norm(entry["name"]))
        blob = norm(" ".join(filter(None, [
            hypothesis_node.get("name"), hypothesis_node.get("summary"),
            hypothesis_node.get("attributes_raw")])))
        for attr, value in (entry.get("attributes") or {}).items():
            if value is None:
                continue
            attr_total += 1
            hit = norm(value) in blob
            attr_hit += 1 if hit else 0
            attr_detail.append({"gold_id": entry["id"], "attribute": attr,
                                "value": value, "survived": hit})
        for fact in entry.get("extra_facts", []):
            attr_total += 1
            hit = norm(fact) in blob
            attr_hit += 1 if hit else 0
            attr_detail.append({"gold_id": entry["id"], "attribute": "extra_fact",
                                "value": fact, "survived": hit})

    # ---- edges ----------------------------------------------------------
    def resolve(name):
        entry = index.get(norm(name))
        return entry, (entry["_tier"] if entry else None)

    gold_edge_triples = {}
    for edge in gold_edges:
        gold_edge_triples[(edge["source"], norm_rel(edge["relation"]), edge["target"])] = edge
    gold_pairs = {}
    for (src, rel, tgt) in gold_edge_triples:
        gold_pairs.setdefault((src, tgt), []).append(rel)

    edge_rows = []
    endpoint_classes = {"gold": 0, "borderline": 0, "junk": 0, "unknown": 0}
    off_ontology_rels = 0
    for edge in edges:
        rel = norm_rel(edge.get("relation"))
        s_entry, s_tier = resolve(edge.get("src_name"))
        t_entry, t_tier = resolve(edge.get("tgt_name"))
        endpoint_classes[s_tier or "unknown"] += 1
        endpoint_classes[t_tier or "unknown"] += 1
        if rel not in {norm_rel(r) for r in ontology_edge_types}:
            off_ontology_rels += 1
        row = {"src": edge.get("src_name"), "relation": edge.get("relation"),
               "tgt": edge.get("tgt_name"), "fact": (edge.get("fact") or "")[:160],
               "valid_at": edge.get("valid_at"), "invalid_at": edge.get("invalid_at"),
               "chunks": edge.get("attributed_chunks"), "verdict": None,
               "gold_edge_id": None}
        if s_tier and t_tier and s_tier != "junk" and t_tier != "junk":
            triple = (s_entry["id"], rel, t_entry["id"])
            if triple in gold_edge_triples:
                row["verdict"] = "match"
                row["gold_edge_id"] = gold_edge_triples[triple]["id"]
            else:
                swapped = (t_entry["id"], rel, s_entry["id"])
                pair = (s_entry["id"], t_entry["id"])
                if swapped in gold_edge_triples:
                    row["verdict"] = "direction-error"
                    row["gold_edge_id"] = gold_edge_triples[swapped]["id"]
                elif pair in gold_pairs:
                    row["verdict"] = "wrong-relation"
                else:
                    row["verdict"] = "extra"
        else:
            row["verdict"] = "junk-edge"
            # Only an edge with an *unresolved* (not junk-classified) endpoint asks a
            # human anything: has the gold missed an entity? A junk/junk edge is already
            # a counted false positive and needs no adjudication.
            if s_tier == "unknown" or t_tier == "unknown":
                review.append({"kind": "edge", "src": edge.get("src_name"),
                               "relation": edge.get("relation"),
                               "tgt": edge.get("tgt_name"),
                               "fact": (edge.get("fact") or "")[:160],
                               "question": "an endpoint resolved to neither gold nor junk — "
                                           "is the gold missing an entity?",
                               "adjudication": None})
        edge_rows.append(row)

    matched = [r for r in edge_rows if r["verdict"] == "match"]
    matched_gold_ids = {r["gold_edge_id"] for r in matched}

    # ---- temporal -------------------------------------------------------
    temporal_rows = []
    for edge in gold_edges:
        expected = edge.get("temporal", {})
        if not expected.get("valid_at") or expected.get("valid_at") == "unstated":
            continue
        found = next((r for r in matched if r["gold_edge_id"] == edge["id"]), None)
        got_in = parse_date(found.get("valid_at")) if found else None
        got_out = parse_date(found.get("invalid_at")) if found else None
        want_in = parse_date(expected["valid_at"])
        want_out = parse_date(expected.get("invalid_at"))
        ok = bool(got_in and got_out and want_in and want_out
                  and got_in.date() == want_in.date() and got_out.date() == want_out.date())
        temporal_rows.append({
            "gold_edge_id": edge["id"], "expected": f"{expected['valid_at']}→{expected.get('invalid_at')}",
            "got": f"{found.get('valid_at') if found else None}→{found.get('invalid_at') if found else None}" if found else "edge not matched",
            "ok": ok})

    # temporal provenance: is valid_at a date the TEXT stated, or graphiti's default
    # (the episode reference time it was handed, which for this harness is 'now')?
    # A date that appears nowhere in the slice cannot have come from the slice.
    slice_dates = slice_date_tokens(texts)
    provenance = {"text-derived": 0, "not-in-slice": 0, "unset": 0}
    for edge in edges:
        got = parse_date(edge.get("valid_at"))
        if got is None:
            provenance["unset"] += 1
            continue
        provenance["text-derived" if _date_in_slice(got, slice_dates) else "not-in-slice"] += 1
    not_in_slice = [{"src": e.get("src_name"), "relation": e.get("relation"),
                     "tgt": e.get("tgt_name"), "valid_at": e.get("valid_at")}
                    for e in edges
                    if parse_date(e.get("valid_at"))
                    and not _date_in_slice(parse_date(e.get("valid_at")), slice_dates)]

    # ---- cost -----------------------------------------------------------
    cost = {}
    if run:
        by_model = run.get("calls_by_response_model") or {}
        cost = {
            "source": run.get("graph_id") or run.get("slice_file"),
            "llm_calls": run.get("llm_calls"), "retries": run.get("retries"),
            "wall_seconds": run.get("wall_seconds"),
            "mean_seconds_per_call": run.get("mean_seconds_per_call"),
            "ok": run.get("ok"), "skipped": run.get("skipped"),
            "skipped_indices": run.get("skipped_indices"),
            "error_classes": run.get("error_classes"),
            "calls_by_response_model": by_model,
            "derived": {
                "llm_calls_per_gold_entity": safe_div(run.get("llm_calls"), len(found_ids)),
                "llm_calls_per_gold_edge": safe_div(run.get("llm_calls"), len(matched_gold_ids)),
                "edge_dedupe_calls_per_matched_edge": safe_div(
                    (by_model.get("EdgeDuplicate") or {}).get("calls"), len(matched)),
                "source": "derived: run totals ÷ gold-standard hits",
            },
        }

    metrics = {
        "entity": {
            "hypothesis_nodes": len(nodes),
            "gold_entities": len(gold_entries),
            "gold_found": len(found_ids),
            "recall": ratio(len(found_ids), len(gold_entries)),
            "recall_on_attempted_chunks": ratio(
                len([e for e in attempted_entries if e["id"] in found_ids]),
                len(attempted_entries)),
            "gold_entities_on_attempted_chunks": len(attempted_entries),
            "gold_entities_on_skipped_chunks": [e["name"] for e in unattempted_entries],
            "processed_chunks": sorted(processed),
            "precision_unadjudicated": ratio(len(found_ids), len(nodes)),
            "precision_adjudicated": ratio(
                len(found_ids) + sum(1 for r in review if r.get("kind") == "node"
                                     and r.get("adjudication") == "accept"),
                len(nodes) - sum(1 for r in review if r.get("kind") == "node"
                                 and r.get("adjudication") == "reject")),
            "duplicate_over_split": {
                "gold_ids_with_more_than_one_node": len(duplicate_ids),
                "examples": {k: v for k, v in list(duplicate_ids.items())[:10]},
                "hypothesis_nodes_resolving_to_gold": sum(len(v) for v in by_gold_id.values()),
                "distinct_gold_entities_hit": len(by_gold_id),
                "over_split_rate": ratio(sum(len(v) for v in by_gold_id.values())
                                         - len(by_gold_id),
                                         max(sum(len(v) for v in by_gold_id.values()), 1)),
                "source": "derived: nodes resolving to a gold entity minus the distinct"
                          " gold entities they resolve to",
            },
            "type_accuracy_on_matched": ratio(len(type_ok), len(type_checked)),
            "untyped_nodes": untyped,
            "off_ontology_type_labels": off_ontology_labels,
            "borderline_matched": len(resolved_borderline),
            "junk_hits": len(junk_hits),
            "junk_hit_rate": ratio(len(junk_hits), len(nodes)),
            "junk_reasons": tally(junk_hits, key="reason"),
            "unknown_nodes": len(unknown),
            "fuzzy_candidates": len(fuzzy),
        },
        "edge": {
            "hypothesis_edges": len(edges),
            "gold_edges": len(gold_edges),
            "matched": len(matched),
            "recall": ratio(len(matched), len(gold_edges)),
            "precision_unadjudicated": ratio(len(matched), len(edges)),
            "extra": sum(1 for r in edge_rows if r["verdict"] == "extra"),
            "direction_errors": sum(1 for r in edge_rows if r["verdict"] == "direction-error"),
            "wrong_relation": sum(1 for r in edge_rows if r["verdict"] == "wrong-relation"),
            "junk_edges": sum(1 for r in edge_rows if r["verdict"] == "junk-edge"),
            "off_ontology_relation_rate": ratio(off_ontology_rels, len(edges)),
            "endpoint_resolution": {
                "gold_endpoints": endpoint_classes["gold"],
                "borderline_endpoints": endpoint_classes["borderline"],
                "junk_endpoints": endpoint_classes["junk"],
                "unknown_endpoints": endpoint_classes["unknown"],
                "endpoints_total": sum(endpoint_classes.values()),
                "both_endpoints_gold": ratio(
                    sum(1 for r in edge_rows if r["verdict"] != "junk-edge"), len(edges)),
            },
        },
        "temporal": {
            "scoreable_gold_edges": len(temporal_rows),
            "correct": sum(1 for r in temporal_rows if r["ok"]),
            "completeness": ratio(sum(1 for r in temporal_rows if r["ok"]), len(temporal_rows)),
            "valid_at_provenance": provenance,
            "valid_at_not_in_slice_examples": not_in_slice[:8],
            "detail": temporal_rows,
        },
        "attributes": {
            "supported_values": attr_total, "survived": attr_hit,
            "support_rate": ratio(attr_hit, attr_total), "detail": attr_detail,
        },
        "cost": cost,
    }
    return {
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "gold": {"file": gold.get("gold_standard"), "version": gold.get("version"),
                 "entities": len(gold_entries)},
        "hypothesis": hypothesis.get("graph_id"),
        "match_policy": policy,
        "gold_found_names": [(gid, by_gold_id[gid][0]) for gid in sorted(found_ids)],
        "gold_missing_names": [(e["id"], e["name"]) for e in gold_entries if e["id"] not in found_ids],
        "metrics": metrics,
        "nodes": node_rows, "edges": edge_rows, "review_queue": review,
    }


def safe_div(a, b):
    if not a or not b:
        return None
    return round(a / b, 2)


def ratio(a, b):
    return round(a / b, 4) if b else None


def tally(rows, key=None):
    out = {}
    for row in rows:
        label = row.get(key) if key else "n/a"
        out[label] = out.get(label, 0) + 1
    return out


def slice_date_tokens(texts: dict[int, str]) -> set[str]:
    """Every date the slice text actually states, at three granularities.

    The PDF extractor leaves dates like 2023年9月, 2025/11/28 and 2023至2025年 in
    the text. A hypothesis valid_at whose date is not in this set cannot have been
    read off the text, whatever the model meant to do.
    """

    tokens: set[str] = set()
    for text in texts.values():
        for year, month in re.findall(r"(\d{4})\s*年\s*(\d{1,2})?\s*月?", text):
            tokens.add(year)
            if month:
                tokens.add(f"{year}-{int(month):02d}")
        for year, month, day in re.findall(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", text):
            tokens.add(f"{year}-{int(month):02d}")
            tokens.add(f"{year}-{int(month):02d}-{int(day):02d}")
        tokens.update(re.findall(r"(\d{4})\s*年?\s*至\s*(\d{4})", text))
    return tokens


def _date_in_slice(moment, slice_dates: set[str]) -> bool:
    return (moment.strftime("%Y-%m") in slice_dates
            or moment.strftime("%Y-%m-%d") in slice_dates
            or moment.strftime("%Y") in slice_dates)


def fuzzy_candidate(key, index):
    if len(key) < 2:
        return None
    best = None
    for candidate_key, entry in index.items():
        if key in candidate_key or candidate_key in key:
            best = entry
            break
    return best


# ---------------------------------------------------------------------------
# --agree : two gold annotations
# ---------------------------------------------------------------------------
# A bounded second adjudication answers "which entities/edges are gold, and how do
# you call these six contested items?" -- it is NOT an exhaustive junk list. So its
# tier sets are reconstructed from those answers, and the junk comparison is
# declared not-comparable instead of reporting a meaningless Jaccard between
# "144 grounded junk terms" and "6 adjudicated verdicts".
ADJUDICATOR_VERDICT_EXPANSION = {
    # contested key -> the surface forms the question enumerated, so a stated
    # verdict can be expanded over the same set the second adjudicator saw.
    "c": [("查询引擎", "Organization"), ("媒体引擎", "Organization"),
          ("洞察引擎", "Organization"), ("分析论坛", "Organization")],
    "d": [("武大宿舍改造模板", "CrowdAction"), ("高考志愿避坑指南", "CrowdAction")],
    "e": [("图书馆诬告案", None), ("甲醛宿舍", None)],
}


def _verdict_tier(text) -> str:
    lowered = str(text).lower()
    if lowered.startswith("gold"):
        return "gold"
    if lowered.startswith("border"):
        return "borderline"
    return "junk"


def normalise_adjudicator(doc: dict) -> dict:
    """Lift a bounded adjudication into the annotation shape `agree` compares."""

    entities, edges = [], []
    for entry in doc.get("gold_entities", []):
        entities.append({"name": entry["name"], "tier": "gold",
                         "type": entry.get("type")})
    for key, surfaces in ADJUDICATOR_VERDICT_EXPANSION.items():
        verdict = (doc.get("contested") or {}).get(key)
        if not verdict:
            continue
        tier = _verdict_tier(verdict)
        for name, entity_type in surfaces:
            if name in {e["name"] for e in entities}:
                continue
            entities.append({"name": name, "tier": tier, "type": entity_type})
    for entry in doc.get("gold_edges", []):
        edges.append({"source_name": entry["source"], "relation": entry["relation"],
                      "target_name": entry["target"],
                      "temporal": {"valid_at": entry.get("time_window")}})
    return {"entities": entities, "edges": edges, "_bounded": True,
            "_annotator": doc.get("annotator") or doc.get("model")}


def agree(gold: dict, other: dict) -> dict:
    annotator = other.get("annotator") or other.get("model")
    bounded = "entities" not in other
    if bounded:
        other = normalise_adjudicator(other)
    def surface_set(doc, tier):
        out = set()
        for entry in doc["entities"]:
            if entry.get("tier") == tier:
                out.add(norm(entry.get("name")))
        return out

    def edge_set(doc):
        return {(norm(e.get("source_name")), norm_rel(e.get("relation")),
                 norm(e.get("target_name"))) for e in doc.get("edges", [])}

    report = {"other_annotator": other.get("annotator") or other.get("model")}
    for tier in ("gold", "borderline", "junk"):
        a, b = surface_set(gold, tier), surface_set(other, tier)
        union = a | b
        entry = {
            "primary": len(a), "second": len(b),
            "agreement_jaccard": round(len(a & b) / len(union), 3) if union else None,
            "only_in_primary": sorted(a - b), "only_in_second": sorted(b - a)}
        if bounded and tier == "junk":
            # The bounded pass was never asked for an exhaustive junk list, so this
            # number would measure question scope, not annotator agreement.
            entry["comparable"] = False
            entry["why_not"] = ("the second pass adjudicated contested calls only; it was "
                                "not asked to enumerate junk")
            entry["agreement_jaccard"] = None
        report[f"{tier}_entities"] = entry
    ea, eb = edge_set(gold), edge_set(other)
    report["gold_edges"] = {
        "primary": len(ea), "second": len(eb),
        "agreement_jaccard": round(len(ea & eb) / len(ea | eb), 3) if (ea | eb) else None,
        "only_in_primary": sorted(ea - eb), "only_in_second": sorted(eb - ea)}
    report["annotator"] = annotator
    report["scope"] = {
        "primary": "full annotation: every tier enumerated and grounded",
        "second": ("bounded adjudication: gold set + gold edge set + six contested calls"
                   if bounded else "full annotation"),
    }
    return report


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def print_summary(result: dict) -> None:
    m = result["metrics"]
    e, g, t = m["entity"], m["edge"], m["temporal"]
    print(f"[score] hypothesis: {result['hypothesis']}  match policy: {result['match_policy']}")
    print(f"[score] ENTITY  recall={e['recall']} ({e['gold_found']}/{e['gold_entities']})  "
          f"precision(unadjudicated)={e['precision_unadjudicated']}  "
          f"type_acc={e['type_accuracy_on_matched']}  untyped={e['untyped_nodes']}")
    print(f"[score]        attempted-chunk recall={e['recall_on_attempted_chunks']} "
          f"({e['gold_entities_on_attempted_chunks']} gold entities on chunks "
          f"{e['processed_chunks']})")
    print(f"[score]        junk_hits={e['junk_hits']} ({e['junk_hit_rate']} of {e['hypothesis_nodes']} nodes) "
          f"unknown={e['unknown_nodes']} fuzzy={e['fuzzy_candidates']} "
          f"duplicates={e['duplicate_over_split']['gold_ids_with_more_than_one_node']}")
    print(f"[score] EDGE    recall={g['recall']} ({g['matched']}/{g['gold_edges']})  "
          f"precision={g['precision_unadjudicated']}  extra={g['extra']}  "
          f"direction_errors={g['direction_errors']}  wrong_relation={g['wrong_relation']}  "
          f"junk_edges={g['junk_edges']}")
    print(f"[score]        endpoints gold={g['endpoint_resolution']['gold_endpoints']} "
          f"junk={g['endpoint_resolution']['junk_endpoints']} "
          f"unknown={g['endpoint_resolution']['unknown_endpoints']} "
          f"off_ontology_relation_rate={g['off_ontology_relation_rate']}")
    print(f"[score] TEMPORAL completeness={t['completeness']} ({t['correct']}/{t['scoreable_gold_edges']}) "
          f"valid_at provenance={t['valid_at_provenance']}")
    a = m["attributes"]
    print(f"[score] ATTRS   support={a['support_rate']} ({a['survived']}/{a['supported_values']})")
    if m["cost"]:
        c = m["cost"]
        print(f"[score] COST    calls={c['llm_calls']} retries={c['retries']} "
              f"wall={c['wall_seconds']}s calls/gold_entity="
              f"{c['derived']['llm_calls_per_gold_entity']} calls/gold_edge="
              f"{c['derived']['llm_calls_per_gold_edge']}")
    if result["review_queue"]:
        print(f"[score] REVIEW QUEUE: {len(result['review_queue'])} item(s) need adjudication "
              f"(they count as false positives until ruled on)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", required=True)
    parser.add_argument("--slice", default="scratch/abfix_slice8.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--agree", action="store_true")
    parser.add_argument("--hypothesis")
    parser.add_argument("--other", help="the second annotation, for --agree")
    parser.add_argument("--run", help="a chunk_slice_measure.py summary, for cost")
    parser.add_argument("--match", choices=("strict", "alias", "fuzzy"), default="alias")
    parser.add_argument("--out")
    args = parser.parse_args()

    gold = load_gold(args.gold)
    texts = load_slice(args.slice)
    rc = 0

    if args.check:
        rc |= check_gold(gold, texts)

    if args.agree:
        if not args.other:
            raise SystemExit("[FAIL] --agree needs --other (the second annotation)")
        with open(args.other, encoding="utf-8") as f:
            other = json.load(f)
        report = agree(gold, other)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

    if args.score:
        if not args.hypothesis:
            raise SystemExit("[FAIL] --score needs --hypothesis (gold_dump_graph.py output)")
        with open(args.hypothesis, encoding="utf-8") as f:
            hypothesis = json.load(f)
        run = None
        if args.run:
            with open(args.run, encoding="utf-8") as f:
                run = json.load(f)
        result = score(gold, hypothesis, args.match, run, texts)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        print_summary(result)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
