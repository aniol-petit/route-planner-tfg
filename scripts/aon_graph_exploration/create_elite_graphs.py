import json
import pickle
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import networkx as nx


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def extract_station_code(station_label: str) -> str:
    """
    Extract the 3-letter station code from strings like:
    - "SVO (Russian Federation)" -> "SVO"
    - "SVO - Moscow (Russian Federation)" -> "SVO"
    Fallback: first 3 consecutive uppercase letters.
    """
    if not station_label:
        return ""

    first_token = station_label.split()[0]
    if len(first_token) == 3 and first_token.isalnum():
        return first_token

    import re

    match = re.search(r"\b([A-Z0-9]{3,4})\b", station_label)
    if match:
        return match.group(1)

    return station_label.strip()


def load_routes_from_checkpoint(json_path: Path) -> List[List[Dict]]:
    """
    Load all routes from the live checkpoint JSON.

    Expected structure:
    {
        "routes": {
            "15": [ [segment, segment, ...], ... ],
            "14": [ ... ]
        },
        ...
    }
    """
    if not json_path.exists():
        raise FileNotFoundError(f"Checkpoint file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    routes_by_score = data.get("routes", {}) or {}

    all_routes: List[List[Dict]] = []
    for _score, routes in routes_by_score.items():
        if not isinstance(routes, Iterable):
            continue
        for route in routes:
            if isinstance(route, Iterable) and route:
                all_routes.append(route)

    return all_routes


def compute_station_frequencies(
    routes: Iterable[Iterable[Dict]],
) -> Counter:
    """
    Compute station frequency from a collection of routes.

    Station frequency:
        Count every time a station appears as origin or destination.
    """
    station_counter: Counter = Counter()

    for route in routes:
        for segment in route:
            origin_label = segment.get("origin", "")
            dest_label = segment.get("dest", "")

            origin_code = extract_station_code(origin_label)
            dest_code = extract_station_code(dest_label)

            if origin_code:
                station_counter[origin_code] += 1
            if dest_code:
                station_counter[dest_code] += 1

    return station_counter


def summarize_graph(name: str, graph: nx.Graph) -> Tuple[int, int]:
    return graph.number_of_nodes(), graph.number_of_edges()


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    project_root = PROJECT_ROOT
    
    checkpoint_path = base_dir / "results" / "live_checkpoint.json"
    graph_dir = project_root / "graph"
    original_graph_path = graph_dir / "transportation_graph.gpickle"

    print("=== Elite Graph Creation ===")
    print(f"Checkpoint file: {checkpoint_path}")
    print(f"Original graph: {original_graph_path}")

    try:
        routes = load_routes_from_checkpoint(checkpoint_path)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return
    except json.JSONDecodeError:
        print("[ERROR] Failed to parse JSON from results/live_checkpoint.json.")
        return

    if not routes:
        print("[WARN] No routes found in checkpoint. Aborting graph pruning.")
        return

    station_counter = compute_station_frequencies(routes)

    freq_gt_1000 = {code for code, count in station_counter.items() if count > 1000}
    freq_gt_100 = {code for code, count in station_counter.items() if count > 100}

    print(f"Stations with freq > 1000: {len(freq_gt_1000)}")
    print(f"Stations with freq > 100:  {len(freq_gt_100)}")

    if not original_graph_path.exists():
        print(f"[ERROR] Original graph file not found: {original_graph_path}")
        return

    with original_graph_path.open("rb") as f:
        G: nx.Graph = pickle.load(f)

    G_freq1000 = G.subgraph(freq_gt_1000).copy()
    G_freq100 = G.subgraph(freq_gt_100).copy()

    graph_dir.mkdir(parents=True, exist_ok=True)

    out_path_1000 = graph_dir / "transportation_graph_freq1000.gpickle"
    out_path_100 = graph_dir / "transportation_graph_freq100.gpickle"

    with out_path_1000.open("wb") as f:
        pickle.dump(G_freq1000, f, protocol=pickle.HIGHEST_PROTOCOL)
    with out_path_100.open("wb") as f:
        pickle.dump(G_freq100, f, protocol=pickle.HIGHEST_PROTOCOL)

    orig_nodes, orig_edges = summarize_graph("Original", G)
    n1000_nodes, n1000_edges = summarize_graph("Freq>1000", G_freq1000)
    n100_nodes, n100_edges = summarize_graph("Freq>100", G_freq100)

    print("\n=== Graph Summary ===")
    print(f"Original graph:         {orig_nodes:6d} nodes | {orig_edges:6d} edges")
    print(f"Aggressive (freq>1000): {n1000_nodes:6d} nodes | {n1000_edges:6d} edges")
    print(f"Conservative (freq>100):{n100_nodes:6d} nodes | {n100_edges:6d} edges")

    print("\nSaved pruned graphs:")
    print(f"- {out_path_1000}")
    print(f"- {out_path_100}")


if __name__ == "__main__":
    main()

