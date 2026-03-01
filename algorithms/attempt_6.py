import time
import heapq
import pickle
import json
from datetime import datetime
from pathlib import Path

# ==========================================
# --- 1. Algorithm Classes & Core Functions
# ==========================================

class ForwardLabel:
    def __init__(self, node, t, C, pred, start_time, score=0.0):
        self.node = node
        self.t = t  # Arrival time at this node
        self.C = frozenset(C)
        self.pred = pred
        self.start_time = start_time
        self.score = score

    def __lt__(self, other):
        return self.score > other.score  # Max heap

class BackwardLabel:
    def __init__(self, node, t, C, succ, end_time, score=0.0):
        self.node = node
        self.t = t  # Departure time from this node (looking backwards)
        self.C = frozenset(C)
        self.succ = succ
        self.end_time = end_time
        self.score = score

    def __lt__(self, other):
        return self.score > other.score  # Max heap

class UnifiedLabel:
    """Used for final reconstructed path compatibility with the saver."""
    def __init__(self, node, t, C, pred, start_time):
        self.node = node
        self.t = t
        self.C = frozenset(C)
        self.pred = pred
        self.start_time = start_time

class ProgressTracker:
    def __init__(self, name, log_interval=5000):
        self.name = name
        self.start_time = time.time()
        self.processed_count = 0
        self.log_interval = log_interval
        self.max_q_seen = 0

    def update(self, q_size, stored_paths=0):
        self.processed_count += 1
        self.max_q_seen = max(self.max_q_seen, q_size)
        if self.processed_count % self.log_interval == 0:
            elapsed = time.time() - self.start_time
            rate = self.processed_count / elapsed if elapsed > 0 else 1
            print(f"[{self.name}] Iters: {self.processed_count:,} | Q-Size: {q_size:,} "
                  f"| Stored: {stored_paths:,} | Rate: {rate:,.0f} it/s")

# ==========================================
# --- 2. NetworkX Data Preparation
# ==========================================

def prepare_bidirectional_data(G):
    V = list(G.nodes())
    country_map = {node: data.get("country", "Unknown") for node, data in G.nodes(data=True)}
    
    # Forward edges (Outbound)
    Es_out = {v: [] for v in V}
    Et_out = {v: [] for v in V}
    # Backward edges (Inbound)
    Es_in = {v: [] for v in V}
    Et_in = {v: [] for v in V}

    for u, v, key, data in G.edges(keys=True, data=True):
        edge_type = data.get("edge_type")
        if edge_type in ["flight", "train"]:
            for dep, arr in data.get("time_pairs", []):
                if dep is not None and arr is not None:
                    Es_out[u].append((v, int(dep), int(arr)))
                    Es_in[v].append((u, int(dep), int(arr))) # w=u, v=v, dep, arr
        elif edge_type == "connection":
            delta = data.get("connection_time")
            if delta is not None:
                Et_out[u].append((v, int(delta)))
                Et_in[v].append((u, int(delta)))

    return V, Es_out, Et_out, Es_in, Et_in, country_map

# ==========================================
# --- 3. Precomputation (Algorithms 3, 3-Rev, 5)
# ==========================================

def compute_forward_reachability(V, Es_out, Et_out, country_map, bucket_size=60):
    print("\n[INFO] Precomputing Forward Reachability (Alg 3)...")
    all_buckets = {int(t // bucket_size) for edges in Es_out.values() for _, d, a in edges for t in (d, a)}
    if not all_buckets: return {}
    min_b, max_b = min(all_buckets), max(all_buckets)
    
    R = {v: {b: {country_map.get(v)} - {"Unknown", None} for b in range(min_b, max_b + 1)} for v in V}
    Adj = {v: {b: set() for b in range(min_b, max_b + 1)} for v in V}

    for v in V:
        for u, dep, arr in Es_out[v]:
            if min_b <= dep//bucket_size <= max_b and min_b <= arr//bucket_size <= max_b:
                Adj[v][int(dep//bucket_size)].add((u, int(arr//bucket_size)))

    for b in range(max_b, min_b - 1, -1): # Backward in time
        while True:
            changed = False
            for v in V:
                for u, b_prime in Adj[v][b]:
                    diff = R[u][b_prime] - R[v][b]
                    if diff:
                        R[v][b].update(diff)
                        changed = True
            if not changed: break
    return R

def compute_backward_reachability(V, Es_in, Et_in, country_map, bucket_size=60):
    print("[INFO] Precomputing Backward Reachability (Alg 3 Reversed)...")
    all_buckets = {int(t // bucket_size) for edges in Es_in.values() for _, d, a in edges for t in (d, a)}
    if not all_buckets: return {}
    min_b, max_b = min(all_buckets), max(all_buckets)
    
    R_back = {v: {b: {country_map.get(v)} - {"Unknown", None} for b in range(min_b, max_b + 1)} for v in V}
    Adj_in = {v: {b: set() for b in range(min_b, max_b + 1)} for v in V}

    for v in V:
        for u, dep, arr in Es_in[v]:
            if min_b <= dep//bucket_size <= max_b and min_b <= arr//bucket_size <= max_b:
                Adj_in[v][int(arr//bucket_size)].add((u, int(dep//bucket_size)))

    for b in range(min_b, max_b + 1): # Forward in time
        while True:
            changed = False
            for v in V:
                for u, b_prime in Adj_in[v][b]:
                    diff = R_back[u][b_prime] - R_back[v][b]
                    if diff:
                        R_back[v][b].update(diff)
                        changed = True
            if not changed: break
    return R_back

def compute_mtt(V, Es_out, Et_out, country_map):
    print("[INFO] Precomputing Minimum Transit Times (Alg 5)...")
    unique_countries = {c for c in country_map.values() if c not in [None, "Unknown"]}
    MTT = {c: float("inf") for c in unique_countries}

    for v in V:
        c_v = country_map.get(v)
        for u, dep, arr in Es_out[v]:
            c_u = country_map.get(u)
            if c_v and c_u and c_v != c_u and c_v != "Unknown" and c_u != "Unknown":
                MTT[c_u] = min(MTT[c_u], arr - dep)
        for w, delta in Et_out[v]:
            c_w = country_map.get(w)
            if c_v and c_w and c_v != c_w and c_v != "Unknown" and c_w != "Unknown":
                MTT[c_w] = min(MTT[c_w], delta)
    return MTT

# ==========================================
# --- 4. Main Search Algorithms (10, 11, 12)
# ==========================================

def forward_half_search(V, Es, Et, country_map, R_func, MTT, MaxDur=840, MinC=5, GlobalMin=25):
    print(f"\n[PHASE 1] Starting Forward Half-Search (MaxDur={MaxDur}m, MinC={MinC})...")
    Labels_F = {v: [] for v in V}
    Q = []
    
    for v in V:
        for u, dep, arr in Es[v]:
            c = country_map.get(u, "Unknown")
            L = ForwardLabel(node=u, t=arr, C={c} if c!="Unknown" else set(), pred=None, start_time=dep)
            L.score = (len(L.C) * 10000) - L.t
            Labels_F[u].append(L)
            heapq.heappush(Q, L)

    tracker = ProgressTracker("FWD", 10000)
    
    while Q:
        L = heapq.heappop(Q)
        v, t, C, start_time = L.node, L.t, L.C, L.start_time
        tracker.update(len(Q), sum(len(lst) for lst in Labels_F.values()))

        elapsed = t - start_time
        rem_half = MaxDur - elapsed
        needed = MinC - len(C)
        
        if needed > 0 and rem_half < (needed * GlobalMin): continue
        if needed > 0:
            U = R_func(v, int(t // 60)) - C
            sorted_U = sorted(U, key=lambda x: MTT.get(x, float('inf')))
            time_spent, UB = 0, 0
            for c in sorted_U:
                time_spent += MTT.get(c, float('inf'))
                if time_spent <= rem_half: UB += 1
                else: break
            if len(C) + UB < MinC: continue

        for u, dep, arr in Es[v]:
            if dep >= t and (arr - start_time) <= MaxDur:
                c = country_map.get(u, "Unknown")
                C_prime = C | {c} if c != "Unknown" else C
                L_prime = ForwardLabel(u, arr, C_prime, L, start_time)
                
                # Pareto
                dominated = False
                to_keep = []
                for ext in Labels_F[u]:
                    if ext.t <= arr and ext.C.issuperset(C_prime):
                        if ext.t < arr or ext.C != C_prime:
                            dominated = True; break
                    if not (arr <= ext.t and C_prime.issuperset(ext.C) and (arr < ext.t or C_prime != ext.C)):
                        to_keep.append(ext)
                if not dominated:
                    to_keep.append(L_prime)
                    Labels_F[u] = to_keep
                    L_prime.score = (len(C_prime) * 10000) - arr
                    heapq.heappush(Q, L_prime)

    # Cleanup
    for v in V: Labels_F[v] = [L for L in Labels_F[v] if len(L.C) >= MinC]
    print(f"[FWD] Finished. Stored optimal valid paths: {sum(len(l) for l in Labels_F.values()):,}")
    return Labels_F

def backward_half_search(V, Es_in, Et_in, country_map, R_back_func, MTT, MaxDur=840, MinC=5, GlobalMin=25):
    print(f"\n[PHASE 2] Starting Backward Half-Search (MaxDur={MaxDur}m, MinC={MinC})...")
    Labels_B = {v: [] for v in V}
    Q = []
    
    for v in V:
        for u, dep, arr in Es_in[v]: # u -> v
            c = country_map.get(v, "Unknown")
            L = BackwardLabel(node=v, t=arr, C={c} if c!="Unknown" else set(), succ=None, end_time=arr)
            L.score = (len(L.C) * 10000) + L.t # Later t is better (less elapsed)
            Labels_B[v].append(L)
            heapq.heappush(Q, L)

    tracker = ProgressTracker("BWD", 10000)
    
    while Q:
        L = heapq.heappop(Q)
        v, t, C, end_time = L.node, L.t, L.C, L.end_time
        tracker.update(len(Q), sum(len(lst) for lst in Labels_B.values()))

        elapsed = end_time - t
        rem_half = MaxDur - elapsed
        needed = MinC - len(C)
        
        if needed > 0 and rem_half < (needed * GlobalMin): continue
        if needed > 0:
            U = R_back_func(v, int(t // 60)) - C
            sorted_U = sorted(U, key=lambda x: MTT.get(x, float('inf')))
            time_spent, UB = 0, 0
            for c in sorted_U:
                time_spent += MTT.get(c, float('inf'))
                if time_spent <= rem_half: UB += 1
                else: break
            if len(C) + UB < MinC: continue

        for u, dep, arr in Es_in[v]: # u is the previous station
            if arr <= t and (end_time - dep) <= MaxDur:
                c = country_map.get(u, "Unknown")
                C_prime = C | {c} if c != "Unknown" else C
                L_prime = BackwardLabel(u, dep, C_prime, L, end_time)
                
                # Backward Pareto (t >= is better)
                dominated = False
                to_keep = []
                for ext in Labels_B[u]:
                    if ext.t >= dep and ext.C.issuperset(C_prime):
                        if ext.t > dep or ext.C != C_prime:
                            dominated = True; break
                    if not (dep >= ext.t and C_prime.issuperset(ext.C) and (dep > ext.t or C_prime != ext.C)):
                        to_keep.append(ext)
                if not dominated:
                    to_keep.append(L_prime)
                    Labels_B[u] = to_keep
                    L_prime.score = (len(C_prime) * 10000) + dep
                    heapq.heappush(Q, L_prime)

    # Cleanup
    for v in V: Labels_B[v] = [L for L in Labels_B[v] if len(L.C) >= MinC]
    print(f"[BWD] Finished. Stored optimal valid paths: {sum(len(l) for l in Labels_B.values()):,}")
    return Labels_B

def bidirectional_stitcher(V, Labels_F, Labels_B, record=13, MinWait=10):
    print("\n[PHASE 3] Starting Bi-directional Stitching...")
    best_route_fwd = None
    best_route_bwd = None
    best_dur = float('inf')
    record_beaten = False

    for v in V:
        if not Labels_F[v] or not Labels_B[v]: continue
        
        for L_f in Labels_F[v]:
            for L_b in Labels_B[v]:
                wait_time = L_b.t - L_f.t
                if wait_time < MinWait: continue
                
                total_dur = L_b.end_time - L_f.start_time
                if total_dur > 1440: continue
                
                C_combo = L_f.C | L_b.C
                
                if len(C_combo) > record or (len(C_combo) == record and total_dur < best_dur):
                    record = len(C_combo)
                    best_dur = total_dur
                    best_route_fwd = L_f
                    best_route_bwd = L_b
                    record_beaten = True
                    print(f"  [STITCH] New best! Countries: {record} | Station: {v} | Total Time: {total_dur}m")

    if not record_beaten:
        print("[STITCH] No combination beat the required record.")
        return None

    # Reconstruct into Unified labels for the saver
    print("[STITCH] Reconstructing unified path...")
    path = []
    
    # 1. Forward trace
    curr = best_route_fwd
    fwd_path = []
    while curr:
        fwd_path.append(curr)
        curr = curr.pred
    fwd_path.reverse()
    
    start_time = fwd_path[0].start_time
    cum_C = set()
    for lbl in fwd_path:
        cum_C.update(lbl.C)
        path.append(UnifiedLabel(lbl.node, lbl.t, cum_C.copy(), None, start_time))
        
    # Link the lists
    last_fwd = path[-1]
    
    # 2. Backward trace (skip the first element because it's the exact same node 'v' where they waited)
    curr = best_route_bwd.succ
    bwd_path = []
    while curr:
        bwd_path.append(curr)
        curr = curr.succ
        
    prev_unified = last_fwd
    for lbl in bwd_path:
        cum_C.update(lbl.C)
        new_u = UnifiedLabel(lbl.node, lbl.t, cum_C.copy(), prev_unified, start_time)
        path.append(new_u)
        prev_unified.pred = new_u # Just linking them purely for the saver's formatting

    return path

# ==========================================
# --- 5. Route Saving & Main
# ==========================================

def format_time_minutes(minutes):
    hours, mins = minutes // 60, minutes % 60
    return f"{hours % 24:02d}:{mins:02d}"

def save_route_to_file(path, output_file="optimal_route.txt"):
    if not path: return
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\nOPTIMAL BIDIRECTIONAL ROUTE\n" + "=" * 80 + "\n")
        f.write(f"Total steps: {len(path)}\n")
        f.write(f"Countries visited: {len(path[-1].C)}\n")
        f.write(f"Total time: {path[-1].t - path[0].start_time} mins\n\n")
        for i, lbl in enumerate(path):
            f.write(f"Step {i+1}: Node {lbl.node} | Time {format_time_minutes(lbl.t)} | Countries: {len(lbl.C)}\n")
    print(f"[INFO] Route saved to: {output_file}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=str, default="../graph/transportation_graph.gpickle")
    parser.add_argument("--record", type=int, default=13)
    parser.add_argument("--max-dur", type=int, default=840, help="Max mins per half-search (default 840/14h)")
    parser.add_argument("--min-countries", type=int, default=6, help="Min countries per half-search")
    args = parser.parse_args()

    with open(args.graph, "rb") as f: G = pickle.load(f)
    print(f"[SETUP] Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    V, Es_out, Et_out, Es_in, Et_in, cmap = prepare_bidirectional_data(G)
    
    R_fwd = compute_forward_reachability(V, Es_out, Et_out, cmap)
    R_back = compute_backward_reachability(V, Es_in, Et_in, cmap)
    MTT = compute_mtt(V, Es_out, Et_out, cmap)

    def R_func(v, b): return R_fwd.get(v, {}).get(b, {cmap.get(v, "Unknown")} - {"Unknown", None})
    def R_back_func(v, b): return R_back.get(v, {}).get(b, {cmap.get(v, "Unknown")} - {"Unknown", None})

    t0 = time.time()
    Labels_F = forward_half_search(V, Es_out, Et_out, cmap, R_func, MTT, args.max_dur, args.min_countries)
    Labels_B = backward_half_search(V, Es_in, Et_in, cmap, R_back_func, MTT, args.max_dur, args.min_countries)
    
    best_path = bidirectional_stitcher(V, Labels_F, Labels_B, record=args.record)
    print(f"\n[INFO] Total Execution Time: {time.time() - t0:.2f} seconds.")

    if best_path:
        save_route_to_file(best_path, f"optimal_route_bidirectional_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")