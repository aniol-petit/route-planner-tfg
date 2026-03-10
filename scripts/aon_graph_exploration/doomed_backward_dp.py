"""
Backward dynamic programming on the AoN graph that is *intentionally* RAM-heavy.

This script is designed for thesis illustrations of the State Space Explosion
problem. It:

- Loads the baseline AoN graph and country mappings exactly like
  `baseline_dfs_search.py`.
- Sorts AoN nodes in descending order of departure time.
- For each node, stores *all* valid suffix paths in a memo table.
- Prints periodic logs that show how the total number of stored paths grows.

Suffix path definition (per-node DP state):
    (frozenset_of_countries, list_of_nodes, final_arrival_time)
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Dict, FrozenSet, List, Tuple, Hashable

from baseline_dfs_search import load_graphs, build_country_map, build_aon_country_map

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Types and constants
# ---------------------------------------------------------------------------

GUINNESS_WINDOW_MINUTES: int = 1440  # 24 hours

# A node in the AoN graph is a 4-tuple:
#   (origin_station_id, dest_station_id, dep_time_abs_minutes, arr_time_abs_minutes)
AonNode = Tuple[Hashable, Hashable, int, int]

# Suffix path:
#   (set of visited countries, explicit list of AoN nodes, final arrival time)
SuffixPath = Tuple[FrozenSet[str], List[AonNode], int]


# ---------------------------------------------------------------------------
# Core backward DP
# ---------------------------------------------------------------------------

def build_backward_dp_table() -> Tuple[Dict[AonNode, List[SuffixPath]], Dict[AonNode, str]]:
    """
    Build the backward DP table over the AoN graph.

    Returns
    -------
    dp_memo:
        Dictionary mapping each AoN node to a list of suffix paths:
        (frozenset_of_countries, list_of_nodes, final_arrival_time).
    aon_country_map:
        Mapping from AoN node to its destination-country string.
    """
    print("=" * 80)
    print("Doomed Backward DP on AoN Graph (State Space Explosion Demo)")
    print("=" * 80)

    # 1) Load graphs exactly like in baseline_dfs_search.py
    aon_graph, transport_graph = load_graphs()

    # 2) Build the same country mappings as the baseline search
    print("\nBuilding country mappings (same as baseline_dfs_search.py)...")
    station_country_map = build_country_map(transport_graph)
    aon_country_map = build_aon_country_map(aon_graph, station_country_map)
    print(f"  ✓ Mapped {len(aon_country_map):,} AoN nodes to countries")

    # 3) Topologically / temporally sort nodes by departure time, latest -> earliest
    print("\nSorting AoN nodes by departure time (descending)...")
    all_nodes: List[AonNode] = list(aon_graph.nodes())
    # Index 2 is dep_time in the AoN node tuple (origin, dest, dep_time, arr_time)
    all_nodes.sort(key=lambda n: int(n[2]), reverse=True)
    print(f"  ✓ Sorted {len(all_nodes):,} nodes")

    # 4) Backward DP table
    dp_memo: Dict[AonNode, List[SuffixPath]] = {}

    start_time = time.time()
    processed = 0

    for current_node in all_nodes:
        processed += 1

        # Initialize DP entry for this node (requirement 4a)
        dp_memo[current_node] = []

        origin, dest, dep_time, arr_time = current_node  # AoN tuple structure
        dep_time = int(dep_time)
        arr_time = int(arr_time)

        # Country of the *destination* station for this AoN node (same convention as baseline)
        current_country = aon_country_map.get(current_node, "Unknown")

        # Find successors in the AoN graph (requirement 4b)
        successors = list(aon_graph.successors(current_node))

        if not successors:
            # Leaf node: only the trivial suffix path that starts and ends here (requirement 4c)
            if current_country == "Unknown":
                country_set: FrozenSet[str] = frozenset()
            else:
                country_set = frozenset([str(current_country)])

            suffix_path: SuffixPath = (
                country_set,
                [current_node],
                arr_time,
            )
            dp_memo[current_node].append(suffix_path)
        else:
            # Internal node: extend *all* suffix paths from each successor (requirements 4d–4e)
            for succ in successors:
                # Suffix paths already computed for successor due to reverse-time ordering
                succ_suffixes = dp_memo.get(succ, [])

                for country_set, nodes_list, final_arrival_time in succ_suffixes:
                    # Enforce the 24-hour window relative to *current* node departure
                    if final_arrival_time - dep_time > GUINNESS_WINDOW_MINUTES:
                        continue

                    # Merge country sets (requirement 4e)
                    if current_country == "Unknown":
                        new_countries = country_set
                    else:
                        # Create a new frozenset to avoid accidental sharing
                        tmp = set(country_set)
                        tmp.add(str(current_country))
                        new_countries = frozenset(tmp)

                    # Prepend current node to the explicit node list (requirement 4e)
                    new_nodes = [current_node]
                    # Copy the rest of the path to avoid aliasing large lists between states
                    new_nodes.extend(nodes_list)

                    new_suffix: SuffixPath = (
                        new_countries,
                        new_nodes,
                        final_arrival_time,
                    )
                    dp_memo[current_node].append(new_suffix)

        # 5) Monitoring: log every 100 processed nodes
        if processed % 100 == 0:
            total_paths = sum(len(v) for v in dp_memo.values())
            elapsed = time.time() - start_time
            print(
                f"[{processed:>6} / {len(all_nodes):>6} nodes] "
                f"Total suffix paths stored: {total_paths:,} "
                f"(elapsed {elapsed:0.1f}s)",
                flush=True,
            )

    return dp_memo, aon_country_map


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    dp_memo, aon_country_map = build_backward_dp_table()

    total_nodes = len(dp_memo)
    total_paths = sum(len(v) for v in dp_memo.values())
    max_paths_at_node = max((len(v) for v in dp_memo.values()), default=0)

    print("\n" + "=" * 80)
    print("BACKWARD DP COMPLETED (Doomed State Space Explosion)")
    print("=" * 80)
    print(f"Nodes with memoized suffix paths: {total_nodes:,}")
    print(f"Total suffix paths stored in dp_memo: {total_paths:,}")
    print(f"Maximum suffix paths at any single node: {max_paths_at_node:,}")
    print("=" * 80)


if __name__ == "__main__":
    main()

