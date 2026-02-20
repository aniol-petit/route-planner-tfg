import argparse
import pickle
import heapq
import time
from common import prepare_bidirectional_data, compute_backward_reachability, compute_mtt, BackwardLabel, ProgressTracker

def backward_half_search(V, Es_in, Et_in, country_map, R_back_func, MTT, MaxDur=840, MinC=5, GlobalMin=25):
    print(f"\n[PHASE 2] Starting Backward Half-Search (MaxDur={MaxDur}m, MinC={MinC})...")
    Labels_B = {v: [] for v in V}
    Q = []
    
    for v in V:
        for u, dep, arr in Es_in[v]:
            c = country_map.get(v, "Unknown")
            L = BackwardLabel(node=v, t=arr, C={c} if c!="Unknown" else set(), succ=None, end_time=arr)
            L.score = (len(L.C) * 10000) + L.t
            Labels_B[v].append(L)
            heapq.heappush(Q, L)

    tracker = ProgressTracker("BWD", 10000)
    max_c_seen = 0 # NEW: Track best countries seen
    
    while Q:
        L = heapq.heappop(Q)
        v, t, C, end_time = L.node, L.t, L.C, L.end_time
        
        max_c_seen = max(max_c_seen, len(C))
        tracker.update(len(Q), sum(len(lst) for lst in Labels_B.values()), max_c_seen)

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

        for u, dep, arr in Es_in[v]:
            if arr <= t and (end_time - dep) <= MaxDur:
                c = country_map.get(u, "Unknown")
                C_prime = C | {c} if c != "Unknown" else C
                L_prime = BackwardLabel(u, dep, C_prime, L, end_time)
                
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

    for v in V: Labels_B[v] = [L for L in Labels_B[v] if len(L.C) >= MinC]
    print(f"[BWD] Finished. Stored optimal valid paths: {sum(len(l) for l in Labels_B.values()):,}")
    return Labels_B

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=str, default="../graph/transportation_graph.gpickle")
    parser.add_argument("--max-dur", type=int, default=840)
    parser.add_argument("--min-countries", type=int, default=6)
    args = parser.parse_args()

    print("[BWD] Loading Graph...")
    with open(args.graph, "rb") as f: G = pickle.load(f)
    V, _, _, Es_in, Et_in, cmap = prepare_bidirectional_data(G)
    
    R_back = compute_backward_reachability(V, Es_in, Et_in, cmap)
    MTT = compute_mtt(V, Es_in, Et_in, cmap) # Approximation fine here
    def R_back_func(v, b): return R_back.get(v, {}).get(b, {cmap.get(v, "Unknown")} - {"Unknown", None})

    t0 = time.time()
    Labels_B = backward_half_search(V, Es_in, Et_in, cmap, R_back_func, MTT, args.max_dur, args.min_countries)
    
    # Save output
    with open("labels_backward.pkl", "wb") as f:
        pickle.dump(Labels_B, f)
    print(f"[BWD] Complete. File saved in {time.time() - t0:.2f}s.")
