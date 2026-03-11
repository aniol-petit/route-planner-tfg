from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Hashable, List, Optional, Tuple

from tqdm import tqdm

from baseline_dfs_search import (
    build_aon_country_map,
    build_country_map,
    load_graphs,
)
from scoring_engine import WARRIOR_MINIMUMS, extract_station_code, get_modality


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent

GUINNESS_WINDOW_MINUTES: int = 1440  # 24 hours


@dataclass(frozen=True)
class AoNNodeRecord:
    node_id: int
    origin: Hashable
    dest: Hashable
    dep_time: int
    arr_time: int
    dest_country_bit: int
    transport_type: str
    aon_tuple: Tuple[Hashable, Hashable, int, int]


def _normalize_country_name(value: Any) -> Optional[str]:
    """Normalize country values from graphs into clean strings."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value or value.lower() == "unknown" or value.lower() == "nan":
        return None
    return value


def build_country_bitmask_mapping(aon_country_map: Dict[Tuple, Any]) -> Dict[str, int]:
    """Create COUNTRY_TO_BIT mapping from AoN country labels."""
    countries = set()
    for country in aon_country_map.values():
        name = _normalize_country_name(country)
        if name:
            countries.add(name)

    sorted_countries = sorted(countries)
    country_to_bit: Dict[str, int] = {
        country: 1 << idx for idx, country in enumerate(sorted_countries)
    }

    # Pretty console table
    print("\n=== Country → Bitmask Mapping ===")
    print(f"{'Index':>5}  {'Country':<40}  {'Bit (int)':>10}  {'Bit (binary)':>20}")
    print("-" * 85)
    for idx, country in enumerate(sorted_countries):
        bit = country_to_bit[country]
        print(
            f"{idx:5d}  {country:<40}  {bit:10d}  {bin(bit):>20}",
            flush=False,
        )
    print("-" * 85)
    print(f"Total unique countries: {len(sorted_countries)}\n")

    # Persist to JSON in results/
    results_dir = SCRIPT_DIR / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = results_dir / "country_bit_mapping.json"
    with mapping_path.open("w", encoding="utf-8") as f:
        json.dump(country_to_bit, f, indent=2, ensure_ascii=False)

    return country_to_bit


def infer_transport_type(origin: Hashable, dest: Hashable) -> str:
    """
    Infer transport type (flight vs train) using station-code modalities.

    Heuristic:
    - If both origin and destination codes look like airports (modality 'A'), treat as 'flight'.
    - Otherwise, treat as 'train' (covers rail/bus and mixed transfers).
    """
    origin_code = extract_station_code(str(origin))
    dest_code = extract_station_code(str(dest))
    o_mod = get_modality(origin_code)
    d_mod = get_modality(dest_code)
    if o_mod == "A" and d_mod == "A":
        return "flight"
    return "train"


def compute_buffer_minutes(
    u_node: AoNNodeRecord,
    w_node: AoNNodeRecord,
) -> int:
    """
    Compute required buffer time between two AoN events using the same
    warrior-minimum logic as scoring_engine._compute_transfer_scores.
    """
    from_label = str(u_node.dest)
    to_label = str(w_node.origin)

    from_code = extract_station_code(from_label)
    to_code = extract_station_code(to_label)

    from_mod = get_modality(from_code)
    to_mod = get_modality(to_code)

    modality_key = f"{from_mod}->{to_mod}"
    warrior_min = WARRIOR_MINIMUMS.get(modality_key, 0)

    # Special case: same R->R station has 0 minimum connection time.
    if (
        from_mod == "R"
        and to_mod == "R"
        and from_code
        and from_code == to_code
    ):
        warrior_min = 0

    return int(warrior_min)


def pareto_prune_states(
    states: List[Tuple[int, Optional[int]]],
) -> List[Tuple[int, Optional[int]]]:
    """
    Pareto filter on bitmask coverage.

    State A dominates B if:
      (A.mask & B.mask) == B.mask AND A.mask != B.mask
    """
    if not states:
        return []

    # First deduplicate by mask (keep first pointer seen).
    by_mask: Dict[int, Optional[int]] = {}
    for mask, pointer in states:
        if mask not in by_mask:
            by_mask[mask] = pointer

    masks: List[int] = list(by_mask.keys())
    pointers: List[Optional[int]] = [by_mask[m] for m in masks]
    n = len(masks)
    keep = [True] * n

    for i in range(n):
        if not keep[i]:
            continue
        mi = masks[i]
        for j in range(n):
            if i == j or not keep[j]:
                continue
            mj = masks[j]
            if (mi & mj) == mj and mi != mj:
                # mi strictly dominates mj
                keep[j] = False

    pruned: List[Tuple[int, Optional[int]]] = []
    for i in range(n):
        if keep[i]:
            pruned.append((masks[i], pointers[i]))
    return pruned


def reconstruct_path(
    u_id: int,
    v_id: int,
    final_mask: int,
    first_next_id: Optional[int],
    M: Dict[int, Dict[int, List[Tuple[int, Optional[int]]]]],
    nodes_by_id: Dict[int, AoNNodeRecord],
) -> List[int]:
    """
    Reconstruct a path from u to v following bitwise pointers.

    Uses the rule:
      In cell M[curr][v], state (curr_mask, next_id) must satisfy
      (prev_dest_bit | next_mask) == curr_mask
    when moving from one cell to the next.
    """
    path_ids: List[int] = [u_id]
    curr_id = u_id
    curr_mask = final_mask
    next_id = first_next_id

    if next_id is None:
        return path_ids

    while True:
        path_ids.append(next_id)

        if next_id == v_id:
            # Final node should have base state (bitmask of its dest country) with pointer None.
            break

        curr_node = nodes_by_id[curr_id]
        next_row = M.get(next_id, {})
        cell_states = next_row.get(v_id, [])

        if not cell_states:
            break

        # Find the historical state that matches the current mask.
        chosen_mask: Optional[int] = None
        chosen_pointer: Optional[int] = None
        for next_mask, pointer in cell_states:
            if (curr_node.dest_country_bit | next_mask) == curr_mask:
                chosen_mask = next_mask
                chosen_pointer = pointer
                break

        if chosen_mask is None:
            break

        curr_id = next_id
        curr_mask = chosen_mask
        next_id = chosen_pointer

        if next_id is None:
            break

    return path_ids


def run_matrix_dp_solver() -> None:
    """
    2D Sparse Matrix Dynamic Programming over the AoN graph using bitwise Pareto dominance.

    Builds M[u][v] = list[(bitmask, next_node_id)] and reconstructs all routes
    that visit at least 14 countries within a 24-hour window.
    """
    print("=" * 80)
    print("Matrix DP Solver on AoN Graph (Bitwise Pareto Dominance)")
    print("=" * 80)

    overall_start = time.time()

    # ------------------------------------------------------------------
    # 1. Load graphs and build country maps
    # ------------------------------------------------------------------
    aon_graph, transport_graph = load_graphs()
    station_country_map = build_country_map(transport_graph)
    aon_country_map = build_aon_country_map(aon_graph, station_country_map)

    # Country bitmasking
    country_to_bit = build_country_bitmask_mapping(aon_country_map)

    # ------------------------------------------------------------------
    # 2. Node representation and sorting (backward flow)
    # ------------------------------------------------------------------
    print("Preparing AoN node records...")
    all_aon_nodes: List[Tuple[Hashable, Hashable, int, int]] = list(aon_graph.nodes())

    node_id_by_tuple: Dict[Tuple[Hashable, Hashable, int, int], int] = {}
    nodes_by_id: Dict[int, AoNNodeRecord] = {}

    for idx, node in enumerate(all_aon_nodes):
        origin, dest, dep_time, arr_time = node
        country_raw = aon_country_map.get(node, None)
        country_name = _normalize_country_name(country_raw)
        dest_bit = country_to_bit.get(country_name or "", 0)
        transport_type = infer_transport_type(origin, dest)

        record = AoNNodeRecord(
            node_id=idx,
            origin=origin,
            dest=dest,
            dep_time=int(dep_time),
            arr_time=int(arr_time),
            dest_country_bit=dest_bit,
            transport_type=transport_type,
            aon_tuple=node,
        )
        node_id_by_tuple[node] = idx
        nodes_by_id[idx] = record

    # Sort nodes strictly by descending dep_time
    nodes_sorted: List[AoNNodeRecord] = sorted(
        nodes_by_id.values(),
        key=lambda r: r.dep_time,
        reverse=True,
    )
    print(f"  ✓ Prepared {len(nodes_sorted):,} AoN nodes (sorted by dep_time descending)")

    # ------------------------------------------------------------------
    # 3. Connectivity matrix with strict buffer rules
    # ------------------------------------------------------------------
    print("\nPrecomputing valid successors with buffer rules...")
    successors_by_id: Dict[int, List[int]] = defaultdict(list)

    for record in tqdm(nodes_by_id.values(), desc="Building connectivity", unit="node"):
        u_id = record.node_id
        u_tuple = record.aon_tuple

        # Use the AoN graph's inherent successors and then filter by buffer rules.
        for succ_tuple in aon_graph.successors(u_tuple):
            w_id = node_id_by_tuple.get(succ_tuple)
            if w_id is None:
                continue
            w_record = nodes_by_id[w_id]

            # Station continuity constraint.
            if record.dest != w_record.origin:
                continue

            buffer_minutes = compute_buffer_minutes(record, w_record)
            if w_record.dep_time < record.arr_time + buffer_minutes:
                continue

            successors_by_id[u_id].append(w_id)

    # ------------------------------------------------------------------
    # 4. 2D Sparse Matrix DP core engine
    # ------------------------------------------------------------------
    print("\nRunning backward DP over AoN nodes...")
    M: Dict[int, Dict[int, List[Tuple[int, Optional[int]]]]] = {
        record.node_id: {} for record in nodes_by_id.values()
    }

    # Base case: M[u][u] = [(dest_country_bit_of_u, None)]
    for record in nodes_by_id.values():
        u = record.node_id
        M[u][u] = [(record.dest_country_bit, None)]

    # Backward loop from latest to earliest dep_time
    for record in tqdm(
        nodes_sorted,
        desc="Backward DP",
        unit="node",
    ):
        u = record.node_id
        u_dep = record.dep_time
        u_dest_bit = record.dest_country_bit

        # Temporary accumulator for this row M[u][v]
        new_states_by_v: Dict[int, List[Tuple[int, Optional[int]]]] = defaultdict(list)

        for w in successors_by_id.get(u, []):
            for v, states in M[w].items():
                v_arr = nodes_by_id[v].arr_time
                # 24-hour constraint relative to u's departure.
                if v_arr - u_dep > GUINNESS_WINDOW_MINUTES:
                    continue

                for w_mask, _w_pointer in states:
                    new_mask = u_dest_bit | w_mask
                    new_states_by_v[v].append((new_mask, w))

        # Merge candidates into M[u][v] with Pareto pruning
        for v, candidate_states in new_states_by_v.items():
            existing = M[u].get(v, [])
            combined = existing + candidate_states
            M[u][v] = pareto_prune_states(combined)

    dp_elapsed = time.time() - overall_start
    print(f"\nDP matrix construction completed in {dp_elapsed:0.1f} seconds.")

    # ------------------------------------------------------------------
    # 5. Extraction & route reconstruction
    # ------------------------------------------------------------------
    print("\nExtracting Pareto-optimal routes with ≥ 14 countries...")
    routes_by_score: Dict[str, List[List[Dict[str, Any]]]] = defaultdict(list)
    seen_paths: set[Tuple[int, ...]] = set()

    for u, row in tqdm(M.items(), desc="Scanning DP matrix", unit="row"):
        for v, states in row.items():
            for mask, first_next in states:
                country_count = int(mask.bit_count())
                if country_count < 14:
                    continue

                path_ids = reconstruct_path(
                    u_id=u,
                    v_id=v,
                    final_mask=mask,
                    first_next_id=first_next,
                    M=M,
                    nodes_by_id=nodes_by_id,
                )
                if not path_ids:
                    continue

                # Deduplicate by node-id sequence.
                path_key = tuple(path_ids)
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)

                # Convert to checkpoint-style segment list.
                segments: List[Dict[str, Any]] = []
                for node_id in path_ids:
                    n = nodes_by_id[node_id]
                    origin_country = station_country_map.get(n.origin, "Unknown")
                    dest_country = station_country_map.get(n.dest, "Unknown")
                    segments.append(
                        {
                            "origin": f"{n.origin} ({origin_country})",
                            "dest": f"{n.dest} ({dest_country})",
                            "dep_time": n.dep_time,
                            "arr_time": n.arr_time,
                        }
                    )

                routes_by_score[str(country_count)].append(segments)

    total_routes = sum(len(v) for v in routes_by_score.values())
    print(f"  ✓ Extracted {total_routes:,} routes with ≥ 14 countries.")

    # Prepare final checkpoint-style JSON structure.
    elapsed_total = time.time() - overall_start
    checkpoint_data: Dict[str, Any] = {
        "global_max": max(
            (int(score) for score in routes_by_score.keys()),
            default=0,
        ),
        "nodes_explored": len(nodes_by_id),
        "elapsed_seconds": elapsed_total,
        "routes": routes_by_score,
    }

    results_dir = SCRIPT_DIR / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = results_dir / "matrix_optimal_routes.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)

    print(f"\nSaved matrix-optimal routes to: {output_path}")
    print(f"Total elapsed time: {elapsed_total:0.1f} seconds")


def main() -> None:
    run_matrix_dp_solver()


if __name__ == "__main__":
    main()

