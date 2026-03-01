import argparse
import pickle
import time
import os
from datetime import datetime
from common import UnifiedLabel, prepare_bidirectional_data

def get_sig_f(L): return (L.t, L.start_time, tuple(sorted([str(c) for c in L.C])))
def get_sig_b(L): return (L.t, L.end_time, tuple(sorted([str(c) for c in L.C])))

def get_detailed_route_string(path, G, country_map, Es_out, Et_out, title):
    # 1. Chronological Countries (Skipping dummy origin node)
    chrono_countries = []
    seen = set()
    for lbl in path[1:]:
        c = country_map.get(lbl.node, "Unknown")
        if c != "Unknown" and c not in seen:
            seen.add(c)
            chrono_countries.append(c)

    # 2. Exact Times
    total_time = path[-1].t - path[0].start_time
    hours, mins = total_time // 60, total_time % 60

    lines = [
        "="*80,
        f"🌟 {title} 🌟 | {len(chrono_countries)} Countries",
        "="*80,
        f"Chronological Order: {', '.join(chrono_countries)}",
        f"Total Time (From First Arrival): {total_time} mins ({hours}h {mins}m)",
        "-"*80
    ]

    for i, lbl in enumerate(path):
        c = country_map.get(lbl.node, "Unknown")
        h, m = lbl.t // 60, lbl.t % 60
        
        if i == 0:
            lines.append(f"Step {i+1}: Node {lbl.node} ({c})")
            lines.append(f"  [Boarding Only] Departs: {(lbl.t//60)%24:02d}:{lbl.t%60:02d} | CLOCK NOT STARTED YET")
        else:
            elapsed = lbl.t - path[0].start_time
            lines.append(f"Step {i+1}: Node {lbl.node} ({c})")
            lines.append(f"  [Arrival] Time: {h%24:02d}:{m:02d} (Elapsed: {elapsed} mins)")
            
            # Transport Details
            prev = path[i-1]
            found = False
            for u, dep, arr in Es_out.get(prev.node, []):
                if u == lbl.node and dep == prev.t and arr == lbl.t:
                    dur = arr - dep
                    lines.append(f"  → Transport: SCHEDULED from {prev.node} (Dep: {(dep//60)%24:02d}:{dep%60:02d} | Dur: {dur}m)")
                    found = True; break
            if not found:
                for u, delta in Et_out.get(prev.node, []):
                    if u == lbl.node and prev.t + delta == lbl.t:
                        lines.append(f"  → Transport: TRANSFER from {prev.node} (Dur: {delta}m)")
                        break
        lines.append("")
    lines.append("="*80)
    return "\n".join(lines)

def reconstruct_path(best_route_fwd, best_route_bwd):
    path, curr, fwd_path = [], best_route_fwd, []
    while curr:
        fwd_path.append(curr)
        curr = curr.pred
    fwd_path.reverse()
    
    start_time = fwd_path[0].start_time
    cum_C = set()
    for lbl in fwd_path:
        cum_C.update(lbl.C)
        path.append(UnifiedLabel(lbl.node, lbl.t, cum_C.copy(), None, start_time))
        
    last_fwd, curr, bwd_path = path[-1], best_route_bwd.succ, []
    while curr:
        bwd_path.append(curr)
        curr = curr.succ
        
    prev_unified = last_fwd
    for lbl in bwd_path:
        cum_C.update(lbl.C)
        new_u = UnifiedLabel(lbl.node, lbl.t, cum_C.copy(), prev_unified, start_time)
        path.append(new_u)
        prev_unified.pred = new_u 

    return path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=int, default=13)
    parser.add_argument("--min-wait", type=int, default=10)
    parser.add_argument("--graph", type=str, default="../../graph/transportation_graph.gpickle")
    args = parser.parse_args()

    print("[STITCHER] Loading Graph Data for Detailed Logs...")
    with open(args.graph, "rb") as f: G = pickle.load(f)
    _, Es_out, Et_out, _, _, cmap = prepare_bidirectional_data(G)

    print("[STITCHER] Initializing Live Watchdog...")
    checked_pairs = set()
    target_world_record = args.record
    best_overall_countries = 0
    best_overall_time = float('inf')
    best_overall_path = None
    
    iteration = 0
    while True:
        iteration += 1
        
        if not os.path.exists("live_labels_forward.pkl") or not os.path.exists("live_labels_backward.pkl"):
            print("[STITCHER] Waiting for first checkpoints...")
            time.sleep(30); continue
            
        try:
            with open("live_labels_forward.pkl", "rb") as f: Labels_F = pickle.load(f)
            with open("live_labels_backward.pkl", "rb") as f: Labels_B = pickle.load(f)
        except Exception:
            time.sleep(5); continue
            
        new_pairs_checked = 0
        scan_improved = False
        
        for v in Labels_F.keys():
            if not Labels_F.get(v) or not Labels_B.get(v): continue
            
            for L_f in Labels_F[v]:
                for L_b in Labels_B[v]:
                    pair_sig = (v, get_sig_f(L_f), get_sig_b(L_b))
                    if pair_sig in checked_pairs: continue
                        
                    checked_pairs.add(pair_sig)
                    new_pairs_checked += 1
                    
                    wait_time = L_b.t - L_f.t
                    # Fix for dummy end nodes: only require wait time if taking another transport
                    if L_b.succ is not None and wait_time < args.min_wait: continue
                    if L_b.succ is None and wait_time < 0: continue
                    
                    total_dur = L_b.end_time - L_f.start_time
                    if total_dur > 1440: continue
                    
                    # Ignore origin country in combo calculation
                    C_fwd = set(c for c in L_f.C if c != "Unknown")
                    C_bwd = set(c for c in L_b.C if c != "Unknown")
                    C_combo = C_fwd | C_bwd
                    num_countries = len(C_combo)
                    
                    if num_countries > best_overall_countries or (num_countries == best_overall_countries and total_dur < best_overall_time):
                        best_overall_countries = num_countries
                        best_overall_time = total_dur
                        best_overall_path = reconstruct_path(L_f, L_b)
                        scan_improved = True

        if scan_improved:
            title = "NEW WORLD RECORD BEATEN!" if best_overall_countries > target_world_record else "NEW BEST FEASIBLE ROUTE"
            print(get_detailed_route_string(best_overall_path, G, cmap, Es_out, Et_out, title))
            
            # Simple file save for safety
            with open(f"BEST_ROUTE_{best_overall_countries}C.txt", "w", encoding="utf-8") as f:
                f.write(get_detailed_route_string(best_overall_path, G, cmap, Es_out, Et_out, title))

        print(f"[STITCHER] Scan {iteration} Summary:")
        print(f"  → Checked {new_pairs_checked:,} new pairs (Total: {len(checked_pairs):,})")
        if best_overall_countries > 0:
            print(f"  → Current Best Held: {best_overall_countries} Countries in {best_overall_time} mins.")
            
        if os.path.exists("fwd_done.flag") and os.path.exists("bwd_done.flag"):
            print("\n[STITCHER] Detected completion flags. Shutting down gracefully.")
            break
            
        time.sleep(30)
