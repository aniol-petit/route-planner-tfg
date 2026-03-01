import argparse
import pickle
import heapq
import time
import os
from common import prepare_bidirectional_data, compute_forward_reachability, compute_mtt, ForwardLabel, ProgressTracker

def forward_half_search(V, Es, Et, country_map, R_func, MTT, MaxDur=840, MinC=5, GlobalMin=25):
    print(f"\n[PHASE 1] Starting Forward Half-Search (MaxDur={MaxDur}m, MinC={MinC})...")
    Labels_F = {v: [] for v in V}
    Q = []
    
    # --- GUINNESS RULE INITIALIZATION ---
    for v in V:
        for u, dep, arr in Es[v]:
            c_arr = country_map.get(u, "Unknown")
            C_arr = {c_arr} if c_arr != "Unknown" else set()
            
            # Step 0: Boarding origin (No countries counted, clock set to arrival time)
            L_origin = ForwardLabel(node=v, t=dep, C=set(), pred=None, start_time=arr)
            
            # Step 1: Official Start (Arrival at first destination)
            L_arrival = ForwardLabel(node=u, t=arr, C=C_arr, pred=L_origin, start_time=arr)
            L_arrival.score = (len(C_arr) * 10000) - arr
            
            Labels_F[u].append(L_arrival)
            heapq.heappush(Q, L_arrival)
    # ------------------------------------

    tracker = ProgressTracker("FWD", 10000)
    max_c_seen = 0  
    
    while Q:
        L = heapq.heappop(Q)
        v, t, C, start_time = L.node, L.t, L.C, L.start_time
        
        max_c_seen = max(max_c_seen, len(C))
        tracker.update(len(Q), sum(len(lst) for lst in Labels_F.values()), max_c_seen)

        if tracker.processed_count % 50000 == 0:
            with open("temp_fwd.pkl", "wb") as f: pickle.dump(Labels_F, f)
            os.replace("temp_fwd.pkl", "live_labels_forward.pkl")

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
                
                dominated = False
                to_keep = []
                for ext in Labels_F[u]:
                    if ext.t <= arr and ext.C.issuperset(C_prime):
                        if ext.t < arr or ext.C != C_prime: dominated = True; break
                    if not (arr <= ext.t and C_prime.issuperset(ext.C) and (arr < ext.t or C_prime != ext.C)):
                        to_keep.append(ext)
                if not dominated:
                    to_keep.append(L_prime)
                    Labels_F[u] = to_keep
                    L_prime.score = (len(C_prime) * 10000) - arr
                    heapq.heappush(Q, L_prime)

    for v in V: Labels_F[v] = [L for L in Labels_F[v] if len(L.C) >= MinC]
    print(f"[FWD] Finished. Stored optimal valid paths: {sum(len(l) for l in Labels_F.values()):,}")
    return Labels_F

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=str, default="../../graph/transportation_graph.gpickle")
    parser.add_argument("--max-dur", type=int, default=840)
    parser.add_argument("--min-countries", type=int, default=6)
    args = parser.parse_args()

    with open(args.graph, "rb") as f: G = pickle.load(f)
    V, Es_out, Et_out, _, _, cmap = prepare_bidirectional_data(G)
    R_fwd = compute_forward_reachability(V, Es_out, Et_out, cmap)
    MTT = compute_mtt(V, Es_out, Et_out, cmap)
    def R_func(v, b): return R_fwd.get(v, {}).get(b, {cmap.get(v, "Unknown")} - {"Unknown", None})

    Labels_F = forward_half_search(V, Es_out, Et_out, cmap, R_func, MTT, args.max_dur, args.min_countries)
    
    with open("temp_fwd.pkl", "wb") as f: pickle.dump(Labels_F, f)
    os.replace("temp_fwd.pkl", "live_labels_forward.pkl")
    with open("fwd_done.flag", "w") as f: f.write("done")
