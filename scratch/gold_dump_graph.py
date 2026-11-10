#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gold-standard toolkit, part 1: dump a measured graph as a HYPOTHESIS for scoring.

HANDOFF §9 G item 1 gates every quality arm behind a gold standard
(``docs/gold_abfix_slice8.json`` + ``docs/GOLD_STANDARD.md``). A hypothesis is
what an arm produced; this tool turns a frozen run's Kuzu graph into the JSON
``gold_score.py`` consumes.

**Open the database READ-ONLY.** The graph is evidence; the runbook's
SIGTERM-only rule exists because a stale writer corrupts the WAL. Opening
read-only never writes, and the runbook's "prove which model served" evidence
lives in the ``*_backend.log``, not here. No LLM call, no backend, no network.

What it emits (``scratch/gold_hypothesis_<graph_id>.json``):

* ``nodes`` — every ``Entity``: name, ontology label (``labels[1]``, None when the
  model left the node untyped), the raw ``labels`` list, ``summary``, the raw
  ``attributes`` string, its uuid, the episodes that reference it *through its
  edges* (authoritative — graphiti's ``RelatesToNode_.episodes``), and the chunks
  whose text contains the node name verbatim (heuristic, clearly labelled: it is
  what chunk-restricted recall uses, and it is not authoritative because graphiti
  does not store a node→episode pointer).
* ``edges`` — every ``(Entity)-[:RELATES_TO]->(RelatesToNode_)`` member: source
  name, relation name, target name, the fact text, ``valid_at``/``invalid_at``/
  ``expired_at``, the episode uuids and the episode names they map to.
* ``episodes`` — name, uuid, content length (name is ``episode-<slice index>``
  for the chunk-slice harness, so slice indexes recover).

Usage (from backend/):
    .venv/bin/python ../scratch/gold_dump_graph.py \
        --graph-id ab8before2_1794285607 \
        --out ../scratch/gold_hypothesis_ab8before2_1794285607.json
    .venv/bin/python ../scratch/gold_dump_graph.py --graph-id <id> --all-graphs
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
sys.path.insert(0, _BACKEND_DIR)

import kuzu  # noqa: E402
from app.config import Config  # noqa: E402

NODES_Q = """
MATCH (n:Entity)
RETURN n.uuid AS uuid, n.name AS name, n.labels AS labels,
       n.summary AS summary, n.attributes AS attributes
"""

EDGES_Q = """
MATCH (a:Entity)-[:RELATES_TO]->(e:RelatesToNode_)-[:RELATES_TO]->(b:Entity)
RETURN a.uuid AS src_uuid, a.name AS src_name,
       e.name AS rel, e.fact AS fact,
       e.episodes AS episodes, e.valid_at AS valid_at,
       e.invalid_at AS invalid_at, e.expired_at AS expired_at,
       b.uuid AS tgt_uuid, b.name AS tgt_name
"""

EPISODES_Q = "MATCH (p:Episodic) RETURN p.uuid AS uuid, p.name AS name, p.content AS content, p.created_at AS created_at"


def _ts(value) -> str | None:
    """Kuzu hands back NaT/None for an unset timestamp; both mean 'no date'."""

    if value is None:
        return None
    text = str(value)
    return None if text in ("NaT", "None", "") else text


def chunk_texts(slice_path: str) -> dict[int, str]:
    """The slice's chunk text, keyed by index (the heuristic attribution source)."""

    if not os.path.exists(slice_path):
        candidate = os.path.join(_HERE, os.path.basename(slice_path))
        slice_path = candidate if os.path.exists(candidate) else None
    if not slice_path:
        return {}
    with open(slice_path, encoding="utf-8") as f:
        payload = json.load(f)
    return {item["index"]: item["text"] for item in payload["slice"]}


def verbatim_chunks(name: str, texts: dict[int, str]) -> list[int]:
    """Chunk indexes whose text contains the node name verbatim (heuristic)."""

    if not name or not texts:
        return []
    return sorted(i for i, text in texts.items() if name in text)


def dump(graph_id: str, data_dir: str, slice_path: str) -> dict:
    path = os.path.join(data_dir, graph_id, "graph.kuzu")
    if not os.path.exists(path):
        raise SystemExit(f"[FAIL] no such graph database: {path}")
    texts = chunk_texts(slice_path)
    db = kuzu.Database(path, read_only=True)
    conn = kuzu.Connection(db)
    q = lambda sql: conn.execute(sql).get_as_df()  # noqa: E731

    episodes = {}
    for _, row in q(EPISODES_Q).iterrows():
        # episode-<slice index> for the chunk-slice harness
        index = None
        if isinstance(row["name"], str) and row["name"].startswith("episode-"):
            try:
                index = int(row["name"].split("-", 1)[1])
            except ValueError:
                index = None
        episodes[row["uuid"]] = {"name": row["name"], "index": index,
                                 "chars": len(row["content"] or ""),
                                 "created_at": _ts(row["created_at"])}

    nodes = []
    for _, row in q(NODES_Q).iterrows():
        labels = list(row["labels"] or [])
        typed = [label for label in labels if label != "Entity"]
        node = {
            "uuid": row["uuid"], "name": row["name"] or "",
            "type": typed[0] if typed else None,
            "labels": labels, "summary": row["summary"] or "",
            "attributes_raw": row["attributes"] or "",
            "episode_uuids": [], "attributed_chunks_verbatim": verbatim_chunks(
                row["name"], texts),
        }
        nodes.append(node)
    by_uuid = {n["uuid"]: n for n in nodes}

    edges = []
    for _, row in q(EDGES_Q).iterrows():
        ep_uuids = [u for u in (row["episodes"] or []) if u]
        names = [episodes[u]["name"] for u in ep_uuids if u in episodes]
        indexes = sorted({episodes[u]["index"] for u in ep_uuids
                          if u in episodes and episodes[u]["index"] is not None})
        for uuid in ep_uuids:
            if uuid in by_uuid:
                by_uuid[uuid]["episode_uuids"].append(uuid)
        edges.append({
            "src_uuid": row["src_uuid"], "src_name": row["src_name"] or "",
            "relation": row["rel"] or "", "tgt_uuid": row["tgt_uuid"],
            "tgt_name": row["tgt_name"] or "", "fact": row["fact"] or "",
            "episode_names": names, "attributed_chunks": indexes,
            "episode_created_at": sorted({episodes[u]["created_at"] for u in ep_uuids
                                         if u in episodes and episodes[u]["created_at"]}),
            "valid_at": _ts(row["valid_at"]),
            "invalid_at": _ts(row["invalid_at"]),
            "expired_at": _ts(row["expired_at"]),
        })
    for node in nodes:
        node["episode_uuids"] = sorted(set(node["episode_uuids"]))
    return {
        "graph_id": graph_id,
        "kuzu_path": path,
        "slice_file": slice_path if texts else None,
        "chunk_count_from_slice": len(texts),
        "episodes": list(episodes.values()),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--data-dir", default=None,
                        help="defaults to Config.GRAPHITI_DATA_DIR")
    parser.add_argument("--slice-file", default="scratch/abfix_slice8.json",
                        help="the frozen slice, for verbatim chunk attribution")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    data_dir = args.data_dir or Config.GRAPHITI_DATA_DIR
    payload = dump(args.graph_id, data_dir, args.slice_file)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    untyped = sum(1 for n in payload["nodes"] if not n["type"])
    print(f"[ok] {args.graph_id}: {payload['node_count']} nodes "
          f"({untyped} untyped) / {payload['edge_count']} edges -> {args.out}")
    print(f"[ok] {len(payload['episodes'])} episodes "
          f"({sorted(e['name'] for e in payload['episodes'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
