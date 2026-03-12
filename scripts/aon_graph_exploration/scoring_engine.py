import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import pickle
from functools import lru_cache


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


WARRIOR_MINIMUMS: Dict[str, int] = {
    "A->A": 20,
    "A->R": 20,
    "R->A": 25,
    "R->R": 5,
}

@lru_cache(maxsize=1)
def load_transfer_durations() -> Dict[str, Dict[str, int]]:
    graph_path = PROJECT_ROOT / "graph" / "transportation_graph.gpickle"
    transfer_edges: Dict[str, Dict[str, int]] = {}
    try:
        with open(graph_path, "rb") as f:
            G = pickle.load(f)
        for u, v, key, data in G.edges(keys=True, data=True):
            if data.get("edge_type") == "connection":
                conn_time = data.get("connection_time")
                if conn_time is not None:
                    if u not in transfer_edges:
                        transfer_edges[u] = {}
                    transfer_edges[u][v] = int(conn_time)
    except Exception as e:
        print(f"Warning: Could not load transfer edges for scoring: {e}")
    return transfer_edges

def extract_station_code(station_label: str) -> str:
    """
    Extract a station code from a human-readable label.

    Mirrors the behavior in the main analysis app, so we can reuse
    the same logic when scoring routes.
    """
    if not station_label:
        return ""

    first_token = station_label.split()[0]
    if len(first_token) == 3 and first_token.isalnum():
        return first_token

    match = re.search(r"\b([A-Z0-9]{3,4})\b", station_label)
    if match:
        return match.group(1)

    return station_label.strip()


def get_modality(station_code: str) -> str:
    """
    Simple modality heuristic.

    - If the code is exactly 3 uppercase letters, treat it as an Airport ("A").
    - Otherwise, treat it as a Rail/Bus station ("R").
    """
    if station_code and len(station_code) == 3 and station_code.isalpha() and station_code.isupper():
        return "A"
    return "R"


def _to_minutes(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compute_transfer_scores(route: Iterable[Dict]) -> List[Dict[str, Any]]:
    """
    Compute per-transfer scores for a single route.

    The route is expected to be an iterable of segments, each with:
    - "origin"
    - "dest"
    - "dep_time" (minutes since some reference)
    - "arr_time" (minutes since some reference)
    """
    segments = list(route)
    transfer_scores: List[Dict[str, Any]] = []

    transfer_times = load_transfer_durations()

    for i in range(len(segments) - 1):
        arrival_seg = segments[i]
        departure_seg = segments[i + 1]

        arr_minutes = _to_minutes(arrival_seg.get("arr_time"))
        dep_minutes = _to_minutes(departure_seg.get("dep_time"))

        if arr_minutes is None or dep_minutes is None:
            # Skip transfers we cannot time.
            continue

        raw_buffer = dep_minutes - arr_minutes

        from_label = arrival_seg.get("dest", "") or arrival_seg.get("origin", "")
        to_label = departure_seg.get("origin", "") or departure_seg.get("dest", "")

        from_code = extract_station_code(from_label)
        to_code = extract_station_code(to_label)

        from_mod = get_modality(from_code)
        to_mod = get_modality(to_code)

        modality_key = f"{from_mod}->{to_mod}"
        warrior_min = WARRIOR_MINIMUMS.get(modality_key, 0)

        # Special case: same R->R station has 0 minimum connection time.
        if from_mod == "R" and to_mod == "R" and from_code and from_code == to_code:
            warrior_min = 0

        transit_time = 0
        if from_label != to_label:
            # Try to find the exact transit time from the graph
            transit_time = transfer_times.get(from_label, {}).get(to_label, 0)
            # If it's a station change but we have no data, apply a heavy default penalty
            if transit_time == 0:
                transit_time = 60

        margin = raw_buffer - transit_time - warrior_min

        if margin >= 0:
            score = 100.0 * (1.0 - math.exp(-0.05 * margin))
        else:
            score = float(margin) * 1000.0

        transfer_scores.append(
            {
                "index": i,
                "from_label": from_label,
                "to_label": to_label,
                "from_code": from_code,
                "to_code": to_code,
                "from_modality": from_mod,
                "to_modality": to_mod,
                "raw_buffer": raw_buffer,
                "warrior_minimum": warrior_min,
                "margin": margin,
                "score": score,
            }
        )

    return transfer_scores


def score_route(route: Iterable[Dict]) -> Dict[str, Any]:
    """
    Score a single route based on its transfers.

    Returns a dictionary with:
    - "RQI": Route Quality Index
    - "bottleneck": details about the worst transfer
    - "transfer_scores": list of per-transfer scoring details
    """
    transfer_scores = _compute_transfer_scores(route)

    if not transfer_scores:
        return {
            "RQI": None,
            "bottleneck": None,
            "transfer_scores": [],
        }

    scores = [t["score"] for t in transfer_scores]
    bottleneck = min(transfer_scores, key=lambda t: t["score"])

    bottleneck_score = bottleneck["score"]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    rqi = (0.7 * bottleneck_score) + (0.3 * avg_score)

    margin = bottleneck["margin"]
    from_code = bottleneck["from_code"] or ""
    to_code = bottleneck["to_code"] or ""
    margin_str = f"{margin:+.0f}m"
    label = f"{from_code}->{to_code} ({margin_str} margin)" if from_code or to_code else f"{margin_str} margin"

    bottleneck_details = {
        "from_code": from_code,
        "to_code": to_code,
        "margin": margin,
        "score": bottleneck_score,
        "label": label,
        "raw_buffer": bottleneck["raw_buffer"],
        "warrior_minimum": bottleneck["warrior_minimum"],
        "index": bottleneck["index"],
    }

    return {
        "RQI": rqi,
        "bottleneck": bottleneck_details,
        "transfer_scores": transfer_scores,
    }

