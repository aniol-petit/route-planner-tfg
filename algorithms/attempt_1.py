import time

import heapq

import networkx as nx

import pickle

from pathlib import Path

# Forgot to define the UB pruning in here

# --- 1. Algorithm Classes & Core Function ---



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

            print(f"[DEBUG] Iters: {self.processed_count:,} | Q-Size: {q_size:,} (Max: {self.max_q_seen:,}) | Best |C|: {best_c_len} | Rate: {rate:,.0f} iters/sec | ETA (Drain): ~{eta_minutes:.2f} mins")



def label_setting_algorithm(V, Es, Et, country_map, R_func, bucket_func, record, compute_score):

    Labels = {v: [] for v in V}

    Q = []

    best_label = None

   

    print("[INFO] Initializing labels at departures...")

    for v in V:

        for u, dep, arr in Es.get(v, []):

            country = country_map.get(u, 'Unknown')

            if country is None:

                country = 'Unknown'

            C = {country}

            # start_time should be the departure time, not arrival time

            L = Label(node=u, t=arr, C=C, pred=None, start_time=dep)

            L.score = compute_score(L)

            Labels[u].append(L)

            heapq.heappush(Q, L)



    print(f"[INFO] Initialization complete. Queue size: {len(Q)}. Target record: {record}")

    tracker = ProgressTracker(log_interval=100) # Lowered for testing



    def update_pareto_front(node, new_label):

        existing_labels = Labels[node]

        labels_to_keep = []

        for L_ext in existing_labels:

            if L_ext.t <= new_label.t and L_ext.C.issuperset(new_label.C):

                if L_ext.t < new_label.t or L_ext.C != new_label.C:

                    return False

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

       

        if t - start_time > 1440:

            continue

           

        UB = len(R_func(v, bucket_func(t)) - C)

       

        if len(C) + UB <= record:

            continue

           

        if best_label is None or len(C) > len(best_label.C):

            best_label = L

            print(f"\n[SUCCESS] New best! Visited: {len(C)} countries. Time elapsed: {t - start_time} mins.")

            if len(C) > record:

                print(f"[SUCCESS] Record beaten! Early stopping triggered.")

                break



        for u, dep, arr in Es.get(v, []):

            if dep >= t and (dep - start_time) <= 1440:

                if arr - start_time > 1440: continue

                country = country_map.get(u, 'Unknown')

                if country is None:

                    country = 'Unknown'

                C_prime = C | {country}

                L_prime = Label(node=u, t=arr, C=C_prime, pred=L, start_time=start_time)

                if update_pareto_front(u, L_prime):

                    L_prime.score = compute_score(L_prime)

                    heapq.heappush(Q, L_prime)



        for w, delta in Et.get(v, []):

            t_new = t + delta

            if t_new - start_time > 1440: continue

            L_prime = Label(node=w, t=t_new, C=C, pred=L, start_time=start_time)

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



# --- 2. NetworkX Data Preparation ---



def prepare_data_from_networkx(G):

    """

    Converts a NetworkX MultiDiGraph into the dictionaries needed by the algorithm.

   

    The graph structure from build_graph.py has:

    - Flight edges: edge_type='flight' with time_pairs=[(dep1, arr1), (dep2, arr2), ...]

    - Train edges: edge_type='train' with time_pairs=[(dep1, arr1), (dep2, arr2), ...]

    - Connection edges: edge_type='connection' with connection_time=delta (in minutes)

   

    Returns:

    - V: List of all nodes

    - Es: Dict mapping node v to list of (u, dep, arr) tuples for scheduled edges (flights/trains)

    - Et: Dict mapping node v to list of (w, delta) tuples for transfer edges (connections)

    - country_map: Dict mapping node v to its country name

    """

    V = list(G.nodes())

   

    # Extract country mapping from node attributes

    # Fallback to 'Unknown' if the attribute is missing

    country_map = {node: data.get('country', 'Unknown') for node, data in G.nodes(data=True)}

   

    Es = {v: [] for v in V}

    Et = {v: [] for v in V}

   

    # Iterate through all edges (MultiDiGraph allows multiple edges between same nodes)

    for u, v, key, data in G.edges(keys=True, data=True):

        edge_type = data.get('edge_type')  # Note: it's 'edge_type', not 'type'

       

        if edge_type in ['flight', 'train']:

            # Scheduled edges: flights and trains have time_pairs list

            time_pairs = data.get('time_pairs', [])

            for dep, arr in time_pairs:

                if dep is not None and arr is not None:

                    Es[u].append((v, int(dep), int(arr)))

       

        elif edge_type == 'connection':

            # Transfer edges: connections have connection_time

            connection_time = data.get('connection_time')

            if connection_time is not None:

                Et[u].append((v, int(connection_time)))

   

    return V, Es, Et, country_map



# --- 3. Main Execution Block ---



if __name__ == "__main__":

    import argparse

   

    parser = argparse.ArgumentParser(description="Run label-setting algorithm for route planning")

    parser.add_argument('--graph', type=str, default='../graph/transportation_graph.gpickle',

                       help='Path to the graph pickle file')

    parser.add_argument('--record', type=int, default=5,

                       help='Current record to beat (number of countries)')

    args = parser.parse_args()

   

    # A. Load the graph from pickle file

    graph_path = Path(args.graph)

    if not graph_path.exists():

        print(f"[ERROR] Graph file not found: {graph_path}")

        print("[INFO] Please run 'python scripts/build_graph.py' first to generate the graph.")

        exit(1)

   

    print(f"[SETUP] Loading graph from {graph_path}...")

    with open(graph_path, 'rb') as f:

        G = pickle.load(f)

   

    print(f"[INFO] Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

   

    # B. Extract data from NetworkX graph

    print("[SETUP] Converting graph to algorithm format...")

    V, Es, Et, country_map = prepare_data_from_networkx(G)

   

    # Count edges for reporting

    total_scheduled = sum(len(edges) for edges in Es.values())

    total_transfers = sum(len(edges) for edges in Et.values())

    print(f"[INFO] Converted: {total_scheduled} scheduled edges, {total_transfers} transfer edges")

   

    # Get unique countries for reachability function

    unique_countries = set(country_map.values())

    unique_countries.discard('Unknown')

    print(f"[INFO] Found {len(unique_countries)} unique countries in graph")



    # C. Define required heuristic functions

    def my_bucket_func(t):

        """

        Map absolute minute time 't' into a time bucket.

        Using 1-hour buckets (60 minutes).

        """

        return int(t // 60)



    def my_R_func(v, b):

        """

        Reachability function: Returns set of countries reachable from node 'v'

        starting at time bucket 'b'.

       

        This is a simplified version. In production, this should be pre-computed

        based on the graph structure to provide tighter upper bounds.

        For now, we return all countries in the graph (optimistic bound).

        """

        # TODO: Pre-compute actual reachability for better pruning

        return unique_countries



    def my_compute_score(label):

        """

        Scoring heuristic for the priority queue.

        Prioritize labels that visit MORE countries in LESS time.

        Higher score = higher priority.

        """

        # Weight countries heavily, subtract time to prefer faster routes

        return (len(label.C) * 10000) - label.t



    # D. Run the algorithm

    current_record_to_beat = args.record

   

    print(f"\n[SETUP] Starting algorithm with target record: {current_record_to_beat} countries...")

    print("[INFO] Algorithm will search for routes visiting maximum countries within 24 hours")

   

    best_path = label_setting_algorithm(

        V=V,

        Es=Es,

        Et=Et,

        country_map=country_map,

        R_func=my_R_func,

        bucket_func=my_bucket_func,

        record=current_record_to_beat,

        compute_score=my_compute_score

    )



    # E. Display the results

    print("\n" + "="*70)

    print("FINAL ROUTE")

    print("="*70)

    if best_path:

        print(f"Total steps: {len(best_path)}")

        print(f"Countries visited: {len(best_path[-1].C) if best_path else 0}")

        if best_path:

            total_time = best_path[-1].t - best_path[0].start_time

            print(f"Total journey time: {total_time:.1f} minutes ({total_time/60:.2f} hours)")

        print("\nRoute details:")

        for i, step in enumerate(best_path):

            countries = sorted(list(step.C))

            time_elapsed = step.t - step.start_time if step.start_time else 0

            print(f"  Step {i+1}: Node={step.node} | Time={step.t} mins | "

                  f"Elapsed={time_elapsed:.1f} mins | Countries={len(countries)}: {countries[:5]}{'...' if len(countries) > 5 else ''}")

    else:

        print("No valid route found within 24 hours.")

    print("="*70)