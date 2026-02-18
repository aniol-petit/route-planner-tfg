import time
import heapq
import random
import pickle
import json
from datetime import datetime
from pathlib import Path

import networkx as nx


# ==========================================
# --- 1. Algorithm Classes & Core Functions
# ==========================================

class Label:
    def __init__(self, node, t, C, pred, start_time, score=0.0):
        self.node = node
        self.t = t
        self.C = frozenset(C)
        self.pred = pred
        self.start_time = start_time
        self.score = score

    def __lt__(self, other):
        # Invert so heapq pops the MAXIMUM score first
        return self.score > other.score


class ProgressTracker:
    def __init__(self, log_interval=1000):
        self.start_time = time.time()
        self.processed_count = 0
        self.log_interval = log_interval
        self.max_q_seen = 0

    def update(self, q_size, best_c_len):
        self.processed_count += 1
        self.max_q_seen = max(self.max_q_seen, q_size)

        if self.processed_count % self.log_interval == 0:
            elapsed_sec = time.time() - self.start_time
            rate = self.processed_count / elapsed_sec if elapsed_sec > 0 else 1
            eta_minutes = (q_size / rate) / 60.0
            print(
                f"[DEBUG] Iters: {self.processed_count:,} | "
                f"Q-Size: {q_size:,} (Max: {self.max_q_seen:,}) | "
                f"Best |C|: {best_c_len} | "
                f"Rate: {rate:,.0f} iters/sec | ETA (Drain): ~{eta_minutes:.2f} mins"
            )


def weighted_random_choice(steps):
    """
    steps: list of tuples (node, tnew, cnew, weight)
    returns one chosen step tuple (node, tnew, cnew, weight)
    """
    if not steps:
        raise ValueError("weighted_random_choice called with empty steps")
    total = sum(w for *_, w in steps)
    if total <= 0:
        return random.choice(steps)
    r = random.random() * total
    upto = 0.0
    for step in steps:
        w = step[3]
        upto += w
        if upto >= r:
            return step
    return steps[-1]


def greedy_stochastic_rollout(Lstart, Es, Et, country_map):
    """
    Algorithm 7: Greedy-stochastic rollout probe
    Input:
      - Lstart: Label(node=v, t, C, pred, start_time)
      - Es: dict[v] -> list of (u, dep, arr)
      - Et: dict[v] -> list of (w, delta)
      - country_map: dict[node] -> country string
    Output:
      - Lfinal: final Label reached when no more moves are possible within 24 hours
    """
    L = Lstart
    while True:
        valid_steps = []

        for u, dep, arr in Es.get(L.node, []):
            if dep >= L.t and (arr - L.start_time) <= 1440:
                cnew = country_map.get(u, "Unknown") or "Unknown"
                valid_steps.append((u, arr, cnew))

        for w, delta in Et.get(L.node, []):
            tnew = L.t + delta
            if (tnew - L.start_time) <= 1440:
                cnew = country_map.get(w, "Unknown") or "Unknown"
                valid_steps.append((w, tnew, cnew))

        if not valid_steps:
            break

        weighted_steps = []
        for u, tnew, cnew in valid_steps:
            dt = max(1, tnew - L.t)
            if cnew not in L.C:
                weight = 10000.0 / dt
            else:
                weight = 10.0 / dt
            weighted_steps.append((u, tnew, cnew, weight))

        chosen_u, chosen_tnew, chosen_cnew, _ = weighted_random_choice(weighted_steps)
        C_prime = set(L.C)
        C_prime.add(chosen_cnew)
        L = Label(node=chosen_u, t=chosen_tnew, C=C_prime, pred=L, start_time=L.start_time)

    return L


def label_setting_algorithm_with_rollouts(
    V,
    Es,
    Et,
    country_map,
    R_func,
    bucket_func,
    MTT,
    record,
    compute_score,
    rollout_freq=1000,
    rollout_seed=None,
):
    """
    Algorithm 8: Label-setting algorithm with time-budgeted UB and Monte-Carlo rollouts.
    """
    if rollout_seed is not None:
        random.seed(rollout_seed)

    Labels = {v: [] for v in V}
    Q = []
    best_label = None
    iters = 0

    print("[INFO] Initializing labels at departures...")
    for v in V:
        for u, dep, arr in Es.get(v, []):
            country = country_map.get(u, "Unknown")
            if country is None:
                country = "Unknown"
            C = {country}
            L = Label(node=u, t=arr, C=C, pred=None, start_time=dep)
            L.score = compute_score(L)
            Labels[u].append(L)
            heapq.heappush(Q, L)

    print(
        f"[INFO] Initialization complete. Queue size: {len(Q)}. "
        f"Target record: {record}. Rollout freq: {rollout_freq}"
    )
    tracker = ProgressTracker(log_interval=500)

    def update_pareto_front(node, new_label):
        existing_labels = Labels[node]
        labels_to_keep = []

        # If an existing label dominates the new one, discard new_label
        for L_ext in existing_labels:
            if L_ext.t <= new_label.t and L_ext.C.issuperset(new_label.C):
                if L_ext.t < new_label.t or L_ext.C != new_label.C:
                    return False

        # Remove labels dominated by the new label
        for L_ext in existing_labels:
            if new_label.t <= L_ext.t and new_label.C.issuperset(L_ext.C):
                if new_label.t < L_ext.t or new_label.C != L_ext.C:
                    continue
            labels_to_keep.append(L_ext)

        labels_to_keep.append(new_label)
        Labels[node] = labels_to_keep
        return True

    while Q:
        L = heapq.heappop(Q)
        v, t, C, start_time = L.node, L.t, L.C, L.start_time

        best_c_len = len(best_label.C) if best_label else 0
        tracker.update(len(Q), best_c_len)

        # --- NEW: MONTE CARLO ROLLOUT TRIGGER ---
        iters += 1
        if rollout_freq and (iters % rollout_freq == 0):
            Lrollout = greedy_stochastic_rollout(L, Es, Et, country_map)
            print(
                f"[ROLLOUT] iters={iters:,} | pre t={t} "
                f"| pre |C|={len(C)} | post |C|={len(Lrollout.C)}"
            )
            if best_label is None or len(Lrollout.C) > len(best_label.C):
                best_label = Lrollout
                print(
                    f"\n[ROLLOUT] New best via rollout! Visited: {len(best_label.C)} "
                    f"countries. Time elapsed: {best_label.t - best_label.start_time} mins."
                )
                if len(best_label.C) > record:
                    print("[ROLLOUT] Record beaten! Early stopping triggered.")
                    break

        # Enforce 24-hour journey limit (1440 mins)
        if t - start_time > 1440:
            continue

        # --- TIME-BUDGETED UPPER BOUND (Algorithm 8 UB) ---
        U = R_func(v, bucket_func(t)) - C
        sorted_U = sorted(U, key=lambda c: MTT.get(c, float("inf")))

        time_spent = 0
        UB = 0
        remaining_time = 1440 - (t - start_time)

        for c in sorted_U:
            time_spent += MTT.get(c, float("inf"))
            if time_spent <= remaining_time:
                UB += 1
            else:
                break

        # Record-based pruning
        if len(C) + UB <= record:
            continue

        # Update best label from normal queue expansion
        if best_label is None or len(C) > len(best_label.C):
            best_label = L
            print(
                f"\n[SUCCESS] New best! Visited: {len(C)} countries. "
                f"Time elapsed: {t - start_time} mins."
            )
            if len(C) > record:
                print("[SUCCESS] Record beaten! Early stopping triggered.")
                break

        # Extend via scheduled edges
        for u, dep, arr in Es.get(v, []):
            if dep >= t and (dep - start_time) <= 1440:
                tnew = arr
                if tnew - start_time > 1440:
                    continue

                country = country_map.get(u, "Unknown")
                if country is None:
                    country = "Unknown"
                C_prime = set(C)
                C_prime.add(country)

                L_prime = Label(node=u, t=tnew, C=C_prime, pred=L, start_time=start_time)
                if update_pareto_front(u, L_prime):
                    L_prime.score = compute_score(L_prime)
                    heapq.heappush(Q, L_prime)

        # Extend via transfers
        for w, delta in Et.get(v, []):
            tnew = t + delta
            if tnew - start_time > 1440:
                continue

            # As in Algorithm 8 pseudocode: transfers do not change C here
            L_prime = Label(node=w, t=tnew, C=C, pred=L, start_time=start_time)
            if update_pareto_front(w, L_prime):
                L_prime.score = compute_score(L_prime)
                heapq.heappush(Q, L_prime)

    print("\n[INFO] Reconstructing path...")
    path, current = [], best_label
    while current is not None:
        path.append(current)
        current = current.pred
    path.reverse()

    print(f"[INFO] Finished in {(time.time() - tracker.start_time):.2f} seconds.")
    return path


# ==========================================
# --- 2. NetworkX Data Preparation
# ==========================================

def prepare_data_from_networkx(G):
    V = list(G.nodes())
    country_map = {node: data.get("country", "Unknown") for node, data in G.nodes(data=True)}
    Es = {v: [] for v in V}
    Et = {v: [] for v in V}

    for u, v, key, data in G.edges(keys=True, data=True):
        edge_type = data.get("edge_type")
        if edge_type in ["flight", "train"]:
            time_pairs = data.get("time_pairs", [])
            for dep, arr in time_pairs:
                if dep is not None and arr is not None:
                    Es[u].append((v, int(dep), int(arr)))
        elif edge_type == "connection":
            connection_time = data.get("connection_time")
            if connection_time is not None:
                Et[u].append((v, int(connection_time)))

    return V, Es, Et, country_map


# ==========================================
# --- 3. Precomputation Algorithms (3 & 5)
# ==========================================

def compute_relaxed_reachability(V, Es, Et, country_map, bucket_func, bucket_size_mins=60):
    """Algorithm 3: Relaxed reachability backpropagation."""
    print("\n[INFO] Precomputing Relaxed Reachability (Algorithm 3)...")
    start_time = time.time()

    all_buckets = set()
    for v in V:
        for u, dep, arr in Es.get(v, []):
            all_buckets.add(bucket_func(dep))
            all_buckets.add(bucket_func(arr))

    if not all_buckets:
        print("[WARNING] No scheduled edges found to compute bounds.")
        min_b, max_b = 0, 24
    else:
        min_b, max_b = min(all_buckets), max(all_buckets)

    print(f"[INFO] Time span covers buckets {min_b} to {max_b} (Total: {max_b - min_b + 1} buckets).")

    R = {v: {b: set() for b in range(min_b, max_b + 1)} for v in V}
    for v in V:
        c = country_map.get(v, "Unknown")
        if c != "Unknown":
            for b in range(min_b, max_b + 1):
                R[v][b].add(c)

    Adj = {v: {b: set() for b in range(min_b, max_b + 1)} for v in V}

    for v in V:
        for u, dep, arr in Es.get(v, []):
            b, b_prime = bucket_func(dep), bucket_func(arr)
            if min_b <= b <= max_b and min_b <= b_prime <= max_b:
                Adj[v][b].add((u, b_prime))

    for v in V:
        for w, delta in Et.get(v, []):
            for b in range(min_b, max_b + 1):
                t = b * bucket_size_mins
                t_prime = t + delta
                b_prime = bucket_func(t_prime)
                if min_b <= b_prime <= max_b:
                    Adj[v][b].add((w, b_prime))

    print("[INFO] Propagating backwards. This may take a moment...")
    for b in range(max_b, min_b - 1, -1):
        while True:
            changed = False
            for v in V:
                for u, b_prime in Adj[v][b]:
                    diff = R[u][b_prime] - R[v][b]
                    if diff:
                        R[v][b].update(diff)
                        changed = True
            if not changed:
                break

    print(f"[INFO] Reachability computed in {time.time() - start_time:.2f} seconds.")
    return R


def compute_mtt(V, Es, Et, country_map, unique_countries):
    """Algorithm 5: Minimum Transit Time precomputation."""
    print("\n[INFO] Precomputing Minimum Transit Times (Algorithm 5)...")
    start_time = time.time()

    MTT = {c: float("inf") for c in unique_countries}

    for v in V:
        c_v = country_map.get(v, "Unknown")
        for u, dep, arr in Es.get(v, []):
            c_u = country_map.get(u, "Unknown")
            if c_v != "Unknown" and c_u != "Unknown" and c_v != c_u:
                transit_time = arr - dep
                if transit_time < MTT[c_u]:
                    MTT[c_u] = transit_time

    for v in V:
        c_v = country_map.get(v, "Unknown")
        for w, delta in Et.get(v, []):
            c_w = country_map.get(w, "Unknown")
            if c_v != "Unknown" and c_w != "Unknown" and c_v != c_w:
                if delta < MTT[c_w]:
                    MTT[c_w] = delta

    print(f"[INFO] MTT computed in {time.time() - start_time:.2f} seconds.")
    return MTT


# ==========================================
# --- 4. Route Saving Functions (same shape as attempt3)
# ==========================================

def format_time_minutes(minutes):
    hours = minutes // 60
    mins = minutes % 60
    day_offset = hours // 24
    hour_24 = hours % 24
    day_str = f"Day {day_offset + 1}, " if day_offset > 0 else ""
    return f"{day_str}{hour_24:02d}:{mins:02d}"


def save_route_to_file(path, G, country_map, Es, Et, output_file="optimal_route.txt"):
    if not path:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("No valid route found within 24 hours.\n")
        return

    output_path = Path(output_file)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("OPTIMAL ROUTE - MAXIMUM COUNTRY COVERAGE\n")
        f.write("=" * 80 + "\n\n")

        total_steps = len(path)
        countries_visited = len(path[-1].C) if path else 0
        total_time = path[-1].t - path[0].start_time if path else 0

        f.write("SUMMARY\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total steps: {total_steps}\n")
        f.write(f"Countries visited: {countries_visited}\n")
        f.write(f"Total journey time: {total_time:.1f} minutes ({total_time / 60:.2f} hours)\n")
        f.write(f"Start time: {format_time_minutes(path[0].start_time)}\n")
        f.write(f"End time: {format_time_minutes(path[-1].t)}\n")
        f.write("\n")

        all_countries = sorted(list(path[-1].C))
        f.write(f"COUNTRIES VISITED ({len(all_countries)}):\n")
        f.write("-" * 80 + "\n")
        for i, country in enumerate(all_countries, 1):
            f.write(f"  {i}. {country}\n")
        f.write("\n")

        f.write("DETAILED ROUTE\n")
        f.write("-" * 80 + "\n\n")

        for i, label in enumerate(path):
            node = label.node
            node_data = G.nodes[node]
            node_type = node_data.get("node_type", "unknown")
            country = country_map.get(node, "Unknown")
            time_elapsed = label.t - label.start_time if label.start_time else 0
            countries_at_step = sorted(list(label.C))

            f.write(f"Step {i + 1}: {node} ({node_type.upper()})\n")
            f.write(f"  Location: {node}\n")
            f.write(f"  Country: {country}\n")
            f.write(
                f"  Time: {format_time_minutes(label.t)} "
                f"(Elapsed: {time_elapsed:.1f} mins / {time_elapsed / 60:.2f} hrs)\n"
            )
            f.write(f"  Countries visited so far: {len(countries_at_step)}\n")
            if len(countries_at_step) <= 10:
                f.write(f"  Countries: {', '.join(countries_at_step)}\n")
            else:
                f.write(
                    f"  Countries: {', '.join(countries_at_step[:10])} "
                    f"... (+{len(countries_at_step) - 10} more)\n"
                )

            if i > 0:
                prev_label = path[i - 1]
                prev_node = prev_label.node
                prev_time = prev_label.t
                current_time = label.t

                found_transport = False
                for dest, dep, arr in Es.get(prev_node, []):
                    if dest == node and dep == prev_time and arr == current_time:
                        edge_type = None
                        for u2, v2, key, data in G.edges(prev_node, keys=True, data=True):
                            if v2 == node:
                                edge_type = data.get("edge_type", "unknown")
                                time_pairs = data.get("time_pairs", [])
                                if (dep, arr) in time_pairs:
                                    break
                        transport_type = (
                            "FLIGHT"
                            if edge_type == "flight"
                            else "TRAIN"
                            if edge_type == "train"
                            else "SCHEDULED"
                        )
                        duration = arr - dep
                        f.write(f"  → Transport: {transport_type} from {prev_node} to {node}\n")
                        f.write(
                            f"    Departure: {format_time_minutes(dep)}, "
                            f"Arrival: {format_time_minutes(arr)}\n"
                        )
                        f.write(f"    Duration: {duration:.1f} minutes ({duration / 60:.2f} hours)\n")
                        found_transport = True
                        break

                if not found_transport:
                    for dest, delta in Et.get(prev_node, []):
                        if dest == node and (prev_time + delta) == current_time:
                            f.write(f"  → Transport: CONNECTION/TRANSFER from {prev_node} to {node}\n")
                            f.write(f"    Transfer time: {delta:.1f} minutes ({delta / 60:.2f} hours)\n")
                            found_transport = True
                            break

                if not found_transport:
                    f.write(f"  → Transport: Unknown (from {prev_node})\n")

            f.write("\n")

        f.write("=" * 80 + "\n")
        f.write(f"Route saved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n")

    json_file = output_path.with_suffix(".json")
    route_data = {
        "summary": {
            "total_steps": total_steps,
            "countries_visited": countries_visited,
            "total_time_minutes": total_time,
            "total_time_hours": total_time / 60,
            "start_time": format_time_minutes(path[0].start_time),
            "end_time": format_time_minutes(path[-1].t),
            "all_countries": all_countries,
        },
        "route": [],
    }

    for i, label in enumerate(path):
        node = label.node
        node_data = G.nodes[node]
        step_data = {
            "step": i + 1,
            "node": node,
            "node_type": node_data.get("node_type", "unknown"),
            "country": country_map.get(node, "Unknown"),
            "time_minutes": label.t,
            "time_formatted": format_time_minutes(label.t),
            "time_elapsed_minutes": label.t - label.start_time if label.start_time else 0,
            "countries_visited": sorted(list(label.C)),
            "countries_count": len(label.C),
        }

        if i > 0:
            prev_label = path[i - 1]
            step_data["from_node"] = prev_label.node
            step_data["from_time"] = prev_label.t

        route_data["route"].append(step_data)

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(route_data, f, indent=2, ensure_ascii=False)

    print(f"[INFO] Route saved to: {output_path}")
    print(f"[INFO] JSON version saved to: {json_file}")


# ==========================================
# --- 5. Main Execution Block
# ==========================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run label-setting algorithm (Algorithm 8) with time-budgeted UB + Monte-Carlo rollouts"
    )
    parser.add_argument(
        "--graph",
        type=str,
        default="../graph/transportation_graph.gpickle",
        help="Path to the graph pickle file",
    )
    parser.add_argument(
        "--record",
        type=int,
        default=13,
        help="Current record to beat (number of countries)",
    )
    parser.add_argument(
        "--rollout-freq",
        type=int,
        default=1000,
        help="Number of iterations between rollouts",
    )
    parser.add_argument(
        "--rollout-seed",
        type=int,
        default=None,
        help="Random seed for rollouts (optional)",
    )
    args = parser.parse_args()

    graph_path = Path(args.graph)
    if not graph_path.exists():
        print(f"[ERROR] Graph file not found: {graph_path}")
        print("[INFO] Please run 'python scripts/build_graph.py' first to generate the graph.")
        raise SystemExit(1)

    print(f"[SETUP] Loading graph from {graph_path}...")
    with open(graph_path, "rb") as f:
        G = pickle.load(f)
    print(f"[INFO] Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    print("[SETUP] Converting graph to algorithm format...")
    V, Es, Et, country_map = prepare_data_from_networkx(G)

    total_scheduled = sum(len(edges) for edges in Es.values())
    total_transfers = sum(len(edges) for edges in Et.values())
    print(f"[INFO] Converted: {total_scheduled} scheduled edges, {total_transfers} transfer edges")

    unique_countries = set(country_map.values())
    unique_countries.discard("Unknown")
    print(f"[INFO] Found {len(unique_countries)} unique countries in graph")

    def my_bucket_func(t):
        return int(t // 60)

    precomputed_R = compute_relaxed_reachability(V, Es, Et, country_map, my_bucket_func, bucket_size_mins=60)
    precomputed_MTT = compute_mtt(V, Es, Et, country_map, unique_countries)

    def my_R_func(v, b):
        if b in precomputed_R.get(v, {}):
            return precomputed_R[v][b]
        c = country_map.get(v, "Unknown")
        return {c} if c != "Unknown" else set()

    def my_compute_score(label):
        return (len(label.C) * 10000) - label.t

    current_record_to_beat = args.record
    print(f"\n[SETUP] Starting Algorithm 8 with target record: {current_record_to_beat} countries...")

    best_path = label_setting_algorithm_with_rollouts(
        V=V,
        Es=Es,
        Et=Et,
        country_map=country_map,
        R_func=my_R_func,
        bucket_func=my_bucket_func,
        MTT=precomputed_MTT,
        record=current_record_to_beat,
        compute_score=my_compute_score,
        rollout_freq=args.rollout_freq,
        rollout_seed=args.rollout_seed,
    )

    print("\n" + "=" * 70)
    print("FINAL ROUTE")
    print("=" * 70)
    if best_path:
        print(f"Total steps: {len(best_path)}")
        print(f"Countries visited: {len(best_path[-1].C) if best_path else 0}")
        total_time = best_path[-1].t - best_path[0].start_time
        print(f"Total journey time: {total_time:.1f} minutes ({total_time / 60:.2f} hours)")
        print("\nRoute details:")
        for i, step in enumerate(best_path):
            countries = sorted(list(step.C))
            time_elapsed = step.t - step.start_time if step.start_time else 0
            print(
                f"  Step {i + 1}: Node={step.node} | Time={step.t} mins | "
                f"Elapsed={time_elapsed:.1f} mins | Countries={len(countries)}: "
                f"{countries[:5]}{'...' if len(countries) > 5 else ''}"
            )
    else:
        print("No valid route found within 24 hours.")
    print("=" * 70)

    if best_path:
        output_filename = f"optimal_route_attempt4_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        save_route_to_file(best_path, G, country_map, Es, Et, output_filename)

