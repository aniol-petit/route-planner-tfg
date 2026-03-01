import networkx as nx
import pickle
import sys

# Increase recursion depth for deep 24-hour paths
sys.setrecursionlimit(5000)

def estimate_search_space(aon_path):
    print("Loading AoN graph...")
    with open(aon_path, "rb") as f:
        G = pickle.load(f)
        
    # 1. Find a starting node: Earliest flight out of Frankfurt (FRA)
    fra_starts = [n for n in G.nodes() if n[0] == 'FRA']
    
    if not fra_starts:
        print("Could not find any starting flights from FRA! Picking the node with highest out-degree.")
        fra_starts = sorted(G.nodes(), key=lambda n: G.out_degree(n), reverse=True)
        
    # Sort to get the absolute earliest departure
    fra_starts.sort(key=lambda x: x[2])
    start_node = fra_starts[0]
    
    print(f"\nSelected Starting Node: {start_node}")
    print(f"  Origin: {start_node[0]} -> Dest: {start_node[1]}")
    print(f"  Departure Time: {start_node[2]} (absolute mins)")
    
    # 2. Set the 24-hour (1440 mins) deadline
    deadline = start_node[2] + 1440
    print(f"  24-Hour Deadline: {deadline} (absolute mins)")
    
    # 3. Dynamic Programming / Memoized Path Counting
    memo = {}
    
    def count_valid_paths(current_node):
        # If we already calculated the future of this node, return the cached answer
        if current_node in memo:
            return memo[current_node]
            
        # Every valid node reached counts as 1 valid path sequence (a journey can end here)
        total_paths = 1
        
        for next_node in G.successors(current_node):
            # Only explore if the arrival time of the next flight is within the 24h window
            if next_node[3] <= deadline:
                total_paths += count_valid_paths(next_node)
                
        memo[current_node] = total_paths
        return total_paths

    print("\nCalculating total valid 24-hour sequences via Memoization...")
    total_sequences = count_valid_paths(start_node)
    
    print("="*80)
    print(f"Total Valid 24-Hour Paths from {start_node[0]}: {total_sequences:,}")
    print("="*80)

if __name__ == "__main__":
    AON = "../../graph/aon_pruned_graph.gpickle"
    estimate_search_space(AON)