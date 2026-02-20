import argparse
import pickle
import time
from datetime import datetime
from common import UnifiedLabel

def bidirectional_stitcher(V, Labels_F, Labels_B, record=13, MinWait=10):
    print("\n[PHASE 3] Starting Bi-directional Stitching...")
    best_route_fwd = None
    best_route_bwd = None
    best_dur = float('inf')
    record_beaten = False

    for v in V:
        if not Labels_F.get(v) or not Labels_B.get(v): continue
        
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

    print("[STITCH] Reconstructing unified path...")
    path = []
    
    # Forward trace
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
        
    last_fwd = path[-1]
    
    # Backward trace
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
        prev_unified.pred = new_u 

    return path

def save_route_to_file(path, output_file="optimal_route.txt"):
    if not path: return
    hours, mins = (path[-1].t - path[0].start_time) // 60, (path[-1].t - path[0].start_time) % 60
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\nOPTIMAL BIDIRECTIONAL ROUTE\n" + "=" * 80 + "\n")
        f.write(f"Total steps: {len(path)}\n")
        f.write(f"Countries visited: {len(path[-1].C)}\n")
        f.write(f"Total time: {hours}h {mins}m\n\n")
        for i, lbl in enumerate(path):
            h, m = lbl.t // 60, lbl.t % 60
            f.write(f"Step {i+1}: Node {lbl.node} | Time {h%24:02d}:{m:02d} | Countries: {len(lbl.C)}\n")
    print(f"[INFO] Route saved to: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=int, default=13)
    args = parser.parse_args()

    t0 = time.time()
    print("[STITCH] Loading saved forward and backward labels...")
    with open("labels_forward.pkl", "rb") as f: Labels_F = pickle.load(f)
    with open("labels_backward.pkl", "rb") as f: Labels_B = pickle.load(f)
    
    # We just need V (keys of the dictionary)
    V = list(Labels_F.keys())
    
    best_path = bidirectional_stitcher(V, Labels_F, Labels_B, record=args.record)
    
    if best_path:
        save_route_to_file(best_path, f"optimal_route_bidirectional_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    print(f"[INFO] Stitching complete in {time.time() - t0:.2f} seconds.")
