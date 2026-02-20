import time
import networkx as nx

class ForwardLabel:
    def __init__(self, node, t, C, pred, start_time, score=0.0):
        self.node = node
        self.t = t
        self.C = frozenset(C)
        self.pred = pred
        self.start_time = start_time
        self.score = score
    def __lt__(self, other): return self.score > other.score

class BackwardLabel:
    def __init__(self, node, t, C, succ, end_time, score=0.0):
        self.node = node
        self.t = t
        self.C = frozenset(C)
        self.succ = succ
        self.end_time = end_time
        self.score = score
    def __lt__(self, other): return self.score > other.score

class UnifiedLabel:
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

    def update(self, q_size, stored_paths=0, max_c=0):
        self.processed_count += 1
        self.max_q_seen = max(self.max_q_seen, q_size)
        if self.processed_count % self.log_interval == 0:
            elapsed = time.time() - self.start_time
            rate = self.processed_count / elapsed if elapsed > 0 else 1
            # NEW: Tracking Max |C| for peace of mind
            print(f"[{self.name}] Iters: {self.processed_count:,} | Q-Size: {q_size:,} "
                  f"| Stored: {stored_paths:,} | Max |C|: {max_c} | Rate: {rate:,.0f} it/s")

def prepare_bidirectional_data(G):
    V = list(G.nodes())
    country_map = {node: data.get("country", "Unknown") for node, data in G.nodes(data=True)}
    Es_out, Et_out, Es_in, Et_in = {v: [] for v in V}, {v: [] for v in V}, {v: [] for v in V}, {v: [] for v in V}
    for u, v, key, data in G.edges(keys=True, data=True):
        edge_type = data.get("edge_type")
        if edge_type in ["flight", "train"]:
            for dep, arr in data.get("time_pairs", []):
                if dep is not None and arr is not None:
                    Es_out[u].append((v, int(dep), int(arr)))
                    Es_in[v].append((u, int(dep), int(arr)))
        elif edge_type == "connection":
            delta = data.get("connection_time")
            if delta is not None:
                Et_out[u].append((v, int(delta)))
                Et_in[v].append((u, int(delta)))
    return V, Es_out, Et_out, Es_in, Et_in, country_map

def compute_forward_reachability(V, Es_out, Et_out, country_map, bucket_size=60):
    all_buckets = {int(t // bucket_size) for edges in Es_out.values() for _, d, a in edges for t in (d, a)}
    if not all_buckets: return {}
    min_b, max_b = min(all_buckets), max(all_buckets)
    R = {v: {b: {country_map.get(v)} - {"Unknown", None} for b in range(min_b, max_b + 1)} for v in V}
    Adj = {v: {b: set() for b in range(min_b, max_b + 1)} for v in V}
    for v in V:
        for u, dep, arr in Es_out[v]:
            if min_b <= dep//bucket_size <= max_b and min_b <= arr//bucket_size <= max_b:
                Adj[v][int(dep//bucket_size)].add((u, int(arr//bucket_size)))
    for b in range(max_b, min_b - 1, -1):
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
    all_buckets = {int(t // bucket_size) for edges in Es_in.values() for _, d, a in edges for t in (d, a)}
    if not all_buckets: return {}
    min_b, max_b = min(all_buckets), max(all_buckets)
    R_back = {v: {b: {country_map.get(v)} - {"Unknown", None} for b in range(min_b, max_b + 1)} for v in V}
    Adj_in = {v: {b: set() for b in range(min_b, max_b + 1)} for v in V}
    for v in V:
        for u, dep, arr in Es_in[v]:
            if min_b <= dep//bucket_size <= max_b and min_b <= arr//bucket_size <= max_b:
                Adj_in[v][int(arr//bucket_size)].add((u, int(dep//bucket_size)))
    for b in range(min_b, max_b + 1):
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
