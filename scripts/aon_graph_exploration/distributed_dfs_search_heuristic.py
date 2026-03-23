"""
Distributed Branch and Bound Depth-First Search (DFS) on Activity-on-Node (AoN) Graph

This script is a distributed variant of `baseline_dfs_search.py` that supports:
- Data-level parallelism via SLURM job arrays (round-robin distribution of starting nodes)
- Local state dominance pruning (memoization) inside each worker

It aggressively explores the full AoN graph while preserving the original
highly-optimized Branch and Bound (MTT) logic.
"""

import argparse
import pickle
import json
import time
import os
import multiprocessing
import collections
from pathlib import Path
from typing import List, Tuple, Dict, FrozenSet
from concurrent.futures import ProcessPoolExecutor, as_completed
import networkx as nx
from tqdm import tqdm

# ============================================================================
# GLOBAL VARIABLES FOR WORKER PROCESSES
# ============================================================================

GLOBAL_GRAPH_DICT = None
GLOBAL_COUNTRY_MAP = None
GLOBAL_MTT = None


def init_worker(graph_dict, country_map, mtt):
    """Initialize worker process with graph data to avoid serialization overhead."""
    global GLOBAL_GRAPH_DICT, GLOBAL_COUNTRY_MAP, GLOBAL_MTT
    GLOBAL_GRAPH_DICT = graph_dict
    GLOBAL_COUNTRY_MAP = country_map
    GLOBAL_MTT = mtt


# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
GRAPH_DIR = PROJECT_ROOT / "graph"
AON_GRAPH_PATH = GRAPH_DIR / "aon_pruned_graph.gpickle"
TRANSPORT_GRAPH_PATH = GRAPH_DIR / "transportation_graph.gpickle"

# Constants
GUINNESS_WINDOW_MINUTES = 1440  # 24 hours
MAX_DATASET_TIME = 2880  # 48 hours (1440 * 2)
MTT_FALLBACK = 60  # Default MTT if missing or 0


# ============================================================================
# DATA LOADING
# ============================================================================

def load_graphs():
    """Load both the AoN graph and the original transportation graph."""
    print("Loading AoN graph...")
    with open(AON_GRAPH_PATH, "rb") as f:
        aon_graph = pickle.load(f)
    print(f"  ✓ Loaded {aon_graph.number_of_nodes():,} nodes, {aon_graph.number_of_edges():,} edges")

    print("Loading transportation graph for country mapping...")
    with open(TRANSPORT_GRAPH_PATH, "rb") as f:
        transport_graph = pickle.load(f)
    print(f"  ✓ Loaded {transport_graph.number_of_nodes():,} stations")

    return aon_graph, transport_graph


def build_country_map(transport_graph):
    """Build a mapping from station ID to country name."""
    country_map = {}
    for node, data in transport_graph.nodes(data=True):
        country = data.get("country", "Unknown")
        # Handle None, NaN, and non-string values
        if country is None:
            country_map[node] = "Unknown"
        elif isinstance(country, float) and country != country:  # NaN check
            country_map[node] = "Unknown"
        elif not isinstance(country, str):
            country_map[node] = str(country) if country is not None else "Unknown"
        else:
            country_map[node] = country
    return country_map


def build_aon_country_map(aon_graph, station_country_map):
    """Build a mapping from AoN node (tuple) to country of its destination station."""
    aon_country_map = {}
    for node in aon_graph.nodes():
        origin, dest, dep_time, arr_time = node
        # Country is determined by the destination station
        country = station_country_map.get(dest, "Unknown")
        # Normalize country value to string
        if country is None:
            aon_country_map[node] = "Unknown"
        elif isinstance(country, float) and country != country:  # NaN check
            aon_country_map[node] = "Unknown"
        elif not isinstance(country, str):
            aon_country_map[node] = str(country) if country is not None else "Unknown"
        else:
            aon_country_map[node] = country
    return aon_country_map


def compute_mtt_for_aon(aon_graph, aon_country_map):
    """
    Compute Minimum Travel Time (MTT) for countries.
    MTT[country] = minimum time required to reach ANY other country from that country.
    """
    unique_countries = {c for c in aon_country_map.values() if c not in [None, "Unknown"]}
    MTT = {c: float("inf") for c in unique_countries}

    for node in aon_graph.nodes():
        c_v = aon_country_map.get(node, "Unknown")
        if c_v == "Unknown" or c_v is None:
            continue

        # Check all outgoing edges to find minimum time to leave this country
        for successor in aon_graph.successors(node):
            c_u = aon_country_map.get(successor, "Unknown")
            if c_u != "Unknown" and c_u is not None and c_v != c_u:
                # Travel time from current node to successor
                # This is the time to reach a different country
                travel_time = successor[3] - node[3]  # arr_time(successor) - arr_time(current)
                if travel_time > 0:
                    # Update MTT for the source country (minimum time to leave it)
                    MTT[c_v] = min(MTT[c_v], travel_time)

    # Replace infinity with fallback for countries with no connections
    for country in MTT:
        if MTT[country] == float("inf"):
            MTT[country] = MTT_FALLBACK

    return MTT


# ============================================================================
# DFS SEARCH WITH BRANCH AND BOUND
# ============================================================================

def get_starting_nodes(aon_graph):
    """Get all valid starting nodes (arrival_time <= 1440 to ensure 24h window)."""
    starting_nodes = []
    for node in aon_graph.nodes():
        origin, dest, dep_time, arr_time = node
        if arr_time <= GUINNESS_WINDOW_MINUTES:
            starting_nodes.append(node)
    return starting_nodes


def graph_to_dict(aon_graph):
    """Convert NetworkX graph to dictionary and pre-sort for optimal DFS exploration."""
    graph_dict = {}
    for node in aon_graph.nodes():
        successors = list(aon_graph.successors(node))
        
        # HEURISTIC SORTING: Rig the DFS to explore the best layovers first!
        # Ideal flight/train layover is roughly 90 minutes.
        # We sort descending so the best options (closest to 90) are at the END of the list,
        # meaning the DFS stack.pop() will explore them FIRST.
        successors.sort(key=lambda succ: abs((succ[2] - node[3]) - 90), reverse=True)
        
        graph_dict[node] = successors
    return graph_dict


def calculate_upper_bound(
    current_countries: FrozenSet[str],
    current_arr_time: int,
    deadline: int,
    current_country: str,
    MTT: Dict[str, float],
) -> int:
    """Calculate the upper bound on number of countries reachable from current state."""
    mtt_value = MTT.get(current_country, MTT_FALLBACK)
    if mtt_value == 0:
        mtt_value = MTT_FALLBACK

    remaining_time = deadline - current_arr_time
    if remaining_time <= 0:
        return len(current_countries)

    max_future_countries = remaining_time // mtt_value
    upper_bound = len(current_countries) + max_future_countries
    return upper_bound


def format_path_details(path: List[Tuple], aon_country_map: Dict) -> str:
    """Format a path for detailed printing."""
    lines = []
    visited_countries = []
    start_time = None

    for i, node in enumerate(path):
        origin, dest, dep_time, arr_time = node
        country = aon_country_map.get(node, "Unknown")

        # Convert country to string and handle non-string values (None, NaN, etc.)
        if country is None:
            country_str = "Unknown"
        elif isinstance(country, float):
            # Check for NaN
            if country != country:  # NaN check
                country_str = "Unknown"
            else:
                country_str = str(country)
        elif not isinstance(country, str):
            country_str = str(country)
        else:
            country_str = country

        visited_countries.append(country_str)

        # Calculate elapsed time from path start
        if start_time is None:
            start_time = arr_time
            elapsed_minutes = 0
        else:
            elapsed_minutes = arr_time - start_time

        elapsed_hours = elapsed_minutes // 60
        elapsed_mins = elapsed_minutes % 60

        dep_hour = dep_time // 60
        dep_min = dep_time % 60
        arr_hour = arr_time // 60
        arr_min = arr_time % 60

        lines.append(
            f"  Step {i+1}: {origin} → {dest} ({country_str}) | "
            f"Dep: {dep_hour:02d}:{dep_min:02d} | "
            f"Arr: {arr_hour:02d}:{arr_min:02d} | "
            f"Elapsed: +{elapsed_hours:02d}:{elapsed_mins:02d}"
        )

    # Get unique countries in order of first appearance (filter out Unknown and invalid values)
    unique_countries = []
    seen = set()
    for country in visited_countries:
        if country and country != "Unknown" and country.lower() != "nan":
            if country not in seen:
                unique_countries.append(country)
                seen.add(country)

    countries_str = " → ".join(unique_countries)
    lines.append(f"\n  Visited Countries ({len(unique_countries)}): {countries_str}")
    return "\n".join(lines)


def explore_start_node(args):
    """
    Worker function to explore a single starting node.
    Uses global variables (GLOBAL_GRAPH_DICT, GLOBAL_COUNTRY_MAP, GLOBAL_MTT)
    set by init_worker to avoid serialization overhead.
    """
    start_node, shared_global_max, shared_lock = args

    # Use global variables instead of arguments
    aon_graph_dict = GLOBAL_GRAPH_DICT
    aon_country_map = GLOBAL_COUNTRY_MAP
    MTT = GLOBAL_MTT

    nodes_explored = 0
    pruned_count = 0
    saved_paths = []

    origin, dest, dep_time, arr_time = start_node
    deadline = arr_time + GUINNESS_WINDOW_MINUTES

    start_country = aon_country_map.get(start_node, "Unknown")
    if start_country == "Unknown":
        return (0, 0, [])

    visited_countries = frozenset([start_country])
    initial_path = [start_node]
    stack = [(start_node, visited_countries, initial_path)]

    # --- LAZY SYNC VARIABLES ---
    local_global_max = 0
    sync_counter = 0

    # Local state dominance map: (node, visited_countries_frozenset) -> best_arrival_time
    local_visited_states: Dict[Tuple[Tuple, FrozenSet[str]], int] = {}

    while stack:
        current_node, visited_countries, current_path = stack.pop()
        nodes_explored += 1
        sync_counter += 1

        # Sync with the shared Manager only once every 10,000 nodes to avoid IPC overhead
        if sync_counter >= 10000:
            local_global_max = shared_global_max.value  # Read without locking (safe for simple ints)
            sync_counter = 0

        has_valid_successor = False
        successors = aon_graph_dict.get(current_node, [])

        for successor in successors:
            succ_origin, succ_dest, succ_dep, succ_arr = successor

            if succ_arr > deadline:
                continue

            has_valid_successor = True

            succ_country = aon_country_map.get(successor, "Unknown")
            if succ_country == "Unknown":
                continue

            future_visited = visited_countries
            if succ_country not in visited_countries:
                future_visited = visited_countries | frozenset([succ_country])

            # STATE DOMINANCE PRUNING (Strict & Fast)
            state_key = (successor, future_visited)
            best_known_arr = local_visited_states.get(state_key, float("inf"))

            # Strictly prune if we've already reached this state at an earlier or equal time
            if succ_arr >= best_known_arr:
                pruned_count += 1
                continue

            local_visited_states[state_key] = succ_arr

            ub = calculate_upper_bound(
                future_visited,
                succ_arr,
                deadline,
                succ_country,
                MTT,
            )

            # Prune using the lightning-fast local copy
            if ub < local_global_max:
                pruned_count += 1
                continue

            new_path = current_path + [successor]
            stack.append((successor, future_visited, new_path))

        if not has_valid_successor:
            num_countries = len(visited_countries)

            # If we beat our local record OR we hit the 14-country human threshold
            if num_countries > local_global_max or num_countries >= 14:
                # NOW we pay the IPC overhead to lock and talk to the Manager
                with shared_lock:
                    if num_countries > shared_global_max.value:
                        shared_global_max.value = num_countries
                    local_global_max = shared_global_max.value  # Update our local copy

                if num_countries >= 14 or num_countries >= local_global_max:
                    saved_paths.append((num_countries, current_path.copy()))

    return (nodes_explored, pruned_count, saved_paths)


# ============================================================================
# CHECKPOINTING
# ============================================================================

def save_checkpoint(
    paths_by_score,
    current_global_max,
    total_nodes,
    elapsed_time,
    aon_country_map,
    station_country_map,
    job_idx: int,
):
    """
    Save a checkpoint of current progress to an append-only JSONL file.
    Only saves paths with score >= 14 or score == current_global_max.
    """
    output_dir = SCRIPT_DIR / "results_distributed_full_heuristic"
    output_dir.mkdir(exist_ok=True)
    checkpoint_file = output_dir / f"routes_part_{job_idx}.jsonl"

    try:
        # Use 'a' for append mode to write line-by-line (JSONL format)
        with open(checkpoint_file, "a", encoding="utf-8") as f:
            for score, paths in paths_by_score.items():
                if score >= 14 or score == current_global_max:
                    for path in paths:
                        path_steps = []
                        for node in path:
                            origin, dest, dep_time, arr_time = node
                            path_steps.append(
                                {
                                    "origin": f"{origin} ({station_country_map.get(origin, 'Unknown')})",
                                    "dest": f"{dest} ({station_country_map.get(dest, 'Unknown')})",
                                    "dep_time": dep_time,
                                    "arr_time": arr_time,
                                }
                            )

                        # Write exactly one JSON object per line
                        line_data = {"score": int(score), "route": path_steps}
                        f.write(json.dumps(line_data, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Warning: Failed to save checkpoint: {e}", flush=True)


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Distributed Branch and Bound DFS search on AoN graph (job-array aware)"
    )
    parser.add_argument("--job_idx", type=int, default=0, help="Index of this job in the job array")
    parser.add_argument(
        "--total_jobs", type=int, default=1, help="Total number of jobs in the job array"
    )
    args = parser.parse_args()

    if args.job_idx < 0 or args.job_idx >= args.total_jobs:
        raise ValueError(f"job_idx must be in [0, total_jobs-1], got {args.job_idx} of {args.total_jobs}")

    print("=" * 80)
    print("Distributed Branch and Bound DFS Search on AoN Graph")
    print("=" * 80)
    print(f"Job index: {args.job_idx} / {args.total_jobs}")

    # Load graphs
    aon_graph, transport_graph = load_graphs()

    # Build country mappings
    print("\nBuilding country mappings...")
    station_country_map = build_country_map(transport_graph)
    aon_country_map = build_aon_country_map(aon_graph, station_country_map)
    print(f"  ✓ Mapped {len(aon_country_map):,} AoN nodes to countries")

    # Compute MTT
    print("\nComputing Minimum Travel Time (MTT)...")
    MTT = compute_mtt_for_aon(aon_graph, aon_country_map)
    print(f"  ✓ Computed MTT for {len(MTT)} countries")

    # Get starting nodes
    print("\nIdentifying starting nodes...")
    starting_nodes = get_starting_nodes(aon_graph)
    print(f"  ✓ Found {len(starting_nodes):,} valid starting nodes")

    # Deterministic ordering, then round-robin distribution across jobs
    starting_nodes.sort()
    assigned_nodes = [
        node for i, node in enumerate(starting_nodes) if i % args.total_jobs == args.job_idx
    ]
    print(
        f"Job {args.job_idx}/{args.total_jobs} processing {len(assigned_nodes)} starting nodes "
        f"out of {len(starting_nodes)}."
    )

    if not assigned_nodes:
        print("No starting nodes assigned to this job. Exiting.")
        return

    # Convert graph to dictionary for multiprocessing (NetworkX graphs aren't easily picklable)
    print("\nPreparing graph for multiprocessing...")
    aon_graph_dict = graph_to_dict(aon_graph)
    print("  ✓ Graph converted to dictionary format")

    # Perform DFS search with multiprocessing
    print("\n" + "=" * 80)
    print(f"Starting multiprocessed DFS search from {len(assigned_nodes):,} assigned starting nodes")
    print(f"{'=' * 80}\n")

    overall_start = time.time()
    total_nodes_explored = 0
    total_pruned = 0

    # Create shared memory for global max and lock
    manager = multiprocessing.Manager()
    shared_global_max = manager.Value("i", 0)
    shared_lock = manager.Lock()

    # Dictionary to store paths by score: {score: [list of paths]}
    paths_by_score = collections.defaultdict(list)

    # Read exactly what SLURM gave us, fallback to os.cpu_count() if not in SLURM
    slurm_cpus = int(os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count()))
    num_workers = max(1, slurm_cpus - 2)
    print(f"Using {num_workers} worker processes (SLURM allocated: {slurm_cpus} CPUs)\n", flush=True)

    # Prepare arguments for each worker (only lightweight data now)
    worker_args = [
        (start_node, shared_global_max, shared_lock) for start_node in assigned_nodes
    ]

    # Track previous global max for alert printing
    previous_global_max = 0

    # Use ProcessPoolExecutor with initializer to avoid serialization overhead
    with ProcessPoolExecutor(
        max_workers=num_workers,
        initializer=init_worker,
        initargs=(aon_graph_dict, aon_country_map, MTT),
    ) as executor:
        # Submit all tasks
        future_to_node = {
            executor.submit(explore_start_node, args_tuple): args_tuple[0]
            for args_tuple in worker_args
        }

        # Process results as they complete with progress bar
        with tqdm(
            total=len(assigned_nodes),
            desc="Processing assigned starting nodes",
            unit="node",
        ) as pbar:
            for future in as_completed(future_to_node):
                try:
                    nodes_explored, pruned_count, saved_paths = future.result()
                except Exception as exc:
                    start_node = future_to_node[future]
                    print(f"\nStarting node {start_node} generated an exception: {exc}")
                    pbar.update(1)
                    continue

                # Update statistics
                total_nodes_explored += nodes_explored
                total_pruned += pruned_count

                # Process saved paths from this worker
                for num_countries, path in saved_paths:
                    paths_by_score[num_countries].append(path)
                    # Print immediately if this worker delivered a new high-water mark
                    if num_countries > previous_global_max:
                        previous_global_max = num_countries
                        print(f"\n{'=' * 80}")
                        print(f"🎉 NEW GLOBAL MAXIMUM (job-local): {num_countries} COUNTRIES!")
                        print(f"{'=' * 80}")
                        print("Route details:")
                        print(format_path_details(path, aon_country_map))
                        print(f"{'=' * 80}\n", flush=True)
                        # Save checkpoint immediately after finding new maximum
                        elapsed_time = time.time() - overall_start
                        with shared_lock:
                            current_global_max = shared_global_max.value
                        save_checkpoint(
                            paths_by_score,
                            current_global_max,
                            total_nodes_explored,
                            elapsed_time,
                            aon_country_map,
                            station_country_map,
                            args.job_idx,
                        )

                # Periodic checkpoint every 100 completed nodes
                if pbar.n % 100 == 0 and pbar.n > 0:
                    elapsed_time = time.time() - overall_start
                    with shared_lock:
                        current_global_max = shared_global_max.value
                    save_checkpoint(
                        paths_by_score,
                        current_global_max,
                        total_nodes_explored,
                        elapsed_time,
                        aon_country_map,
                        station_country_map,
                        args.job_idx,
                    )

                pbar.update(1)

    # Get final global max (job-local)
    with shared_lock:
        global_max_countries = shared_global_max.value

    overall_elapsed = time.time() - overall_start

    # Clean up paths_by_score based on rules
    if paths_by_score:
        absolute_max = max(paths_by_score.keys())

        if absolute_max < 14:
            # If absolute max < 14, keep only paths with absolute max
            paths_to_keep = {absolute_max: paths_by_score[absolute_max]}
            paths_by_score = paths_to_keep
        else:
            # If absolute max >= 14, keep all paths with score >= 14
            paths_to_keep = {
                score: paths for score, paths in paths_by_score.items() if score >= 14
            }
            paths_by_score = paths_to_keep

    # Calculate total paths
    total_paths = sum(len(paths) for paths in paths_by_score.values())

    # Final summary
    print("\n" + "=" * 80)
    print("SEARCH COMPLETE (Distributed Job)")
    print("=" * 80)
    print(f"Job Index: {args.job_idx} / {args.total_jobs}")
    print(f"Total Time: {overall_elapsed:.2f} seconds ({overall_elapsed/60:.2f} minutes)")
    print(f"Total Nodes Explored: {total_nodes_explored:,}")
    print(f"Total Branches Pruned: {total_pruned:,}")
    print(f"Job-Local Maximum Countries: {global_max_countries}")
    print(f"Total Paths Saved: {total_paths}")
    if paths_by_score:
        print(
            f"Paths by Score: "
            f"{dict((score, len(paths)) for score, paths in sorted(paths_by_score.items()))}"
        )
    print("=" * 80)

    # Save results
    output_dir = SCRIPT_DIR / "results_distributed_full_heuristic"
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / f"final_results_part_{args.job_idx}.pickle"
    print(f"\nSaving results to {output_file}...")
    results = {
        "job_idx": args.job_idx,
        "total_jobs": args.total_jobs,
        "global_max_countries": global_max_countries,
        "paths_by_score": dict(paths_by_score),  # Convert defaultdict to regular dict for pickling
        "total_nodes_explored": total_nodes_explored,
        "total_pruned": total_pruned,
        "execution_time": overall_elapsed,
        "num_starting_nodes": len(assigned_nodes),
        "num_all_starting_nodes": len(starting_nodes),
    }

    with open(output_file, "wb") as f:
        pickle.dump(results, f)
    print("  ✓ Results saved successfully!")

    # Print all saved paths, grouped by score
    if paths_by_score:
        print(f"\n{'=' * 80}")
        print(f"ALL SAVED PATHS (Total: {total_paths} paths):")
        print(f"{'=' * 80}")

        # Sort by score (descending)
        for score in sorted(paths_by_score.keys(), reverse=True):
            paths = paths_by_score[score]
            print(f"\n{'=' * 80}")
            print(f"PATHS WITH {score} COUNTRIES ({len(paths)} paths):")
            print(f"{'=' * 80}")
            for i, path in enumerate(paths, 1):
                print(f"\nPath {i}/{len(paths)} ({score} countries):")
                print(format_path_details(path, aon_country_map))
                print("-" * 80)


if __name__ == "__main__":
    main()

