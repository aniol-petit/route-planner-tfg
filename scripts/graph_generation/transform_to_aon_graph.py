"""
Transform a transportation graph into an Activity-on-Node (AoN) Directed Graph
with aggressive pruning using the Earliest Arrival rule.

This script:
1. Loads the transportation graph from transportation_graph.gpickle
2. Creates event nodes from scheduled transport edges (flights/trains)
3. Connects event nodes with edges following time constraints
4. Prunes edges to keep only the earliest arriving option per destination
5. Saves the pruned AoN graph to aon_pruned_graph.gpickle
"""

import pickle
import networkx as nx
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_transportation_graph(graph_path):
    """Load the transportation graph from a pickle file."""
    print(f"Loading graph from {graph_path}...")
    with open(graph_path, 'rb') as f:
        G = pickle.load(f)
    print(f"Loaded graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G


def build_aon_graph(G):
    """
    Transform the transportation graph into an Activity-on-Node graph.
    
    Args:
        G: NetworkX MultiDiGraph with:
            - Nodes: Station/Airport IDs
            - Edges with edge_type='flight' or 'train' containing time_pairs
            - Edges with edge_type='connection' containing connection_time
    
    Returns:
        nx.DiGraph: New graph where nodes are transport events (tuples)
    """
    # Create the new AoN graph
    aon_graph = nx.DiGraph()
    
    # Step 1: Create event nodes from scheduled transport edges
    print("\nStep 1: Creating event nodes from scheduled transports...")
    event_nodes = []
    
    for u, v, key, data in G.edges(keys=True, data=True):
        edge_type = data.get('edge_type')
        
        if edge_type in ['flight', 'train']:
            time_pairs = data.get('time_pairs', [])
            for dep_time, arr_time in time_pairs:
                if dep_time is not None and arr_time is not None:
                    # Create event node: (origin, destination, departure_time, arrival_time)
                    event_node = (u, v, int(dep_time), int(arr_time))
                    event_nodes.append(event_node)
                    # Add node to graph (no attributes needed for now)
                    aon_graph.add_node(event_node)
    
    print(f"Created {len(event_nodes)} event nodes")
    
    # Step 2: Build transfer edge lookup for fast access
    # Note: The graph uses edge_type='connection' with 'connection_time' attribute,
    # which corresponds to transfer edges with duration_min in the requirements
    print("\nStep 2: Building transfer edge lookup...")
    transfer_edges = defaultdict(dict)  # transfer_edges[origin][destination] = duration_min
    
    for u, v, key, data in G.edges(keys=True, data=True):
        edge_type = data.get('edge_type')
        if edge_type == 'connection':
            connection_time = data.get('connection_time')
            if connection_time is not None:
                transfer_edges[u][v] = int(connection_time)
    
    print(f"Found {sum(len(dests) for dests in transfer_edges.values())} transfer edges")
    
    # Step 3: Create edges between event nodes with earliest arrival pruning
    print("\nStep 3: Creating edges with earliest arrival pruning...")
    
    # Group event nodes by origin station for efficient lookup
    events_by_origin = defaultdict(list)
    for event_node in event_nodes:
        origin, dest, dep, arr = event_node
        events_by_origin[origin].append(event_node)
    
    edges_created = 0
    edges_pruned = 0
    
    # For each event node A, find valid subsequent event nodes B
    for node_a in event_nodes:
        origin_a, dest_a, dep_a, arr_a = node_a
        
        # Find all valid subsequent event nodes
        valid_subsequent = []
        
        # Check events that start from dest_a (same station)
        for node_b in events_by_origin.get(dest_a, []):
            origin_b, dest_b, dep_b, arr_b = node_b
            # Valid if departure time is after arrival time
            if dep_b >= arr_a:
                valid_subsequent.append(node_b)
        
        # Check events that require a transfer
        # We need to find all stations reachable from dest_a via transfer
        for transfer_dest, transfer_duration in transfer_edges.get(dest_a, {}).items():
            # Check events starting from transfer_dest
            for node_b in events_by_origin.get(transfer_dest, []):
                origin_b, dest_b, dep_b, arr_b = node_b
                # Valid if departure time is after arrival + transfer duration
                if dep_b >= arr_a + transfer_duration:
                    valid_subsequent.append(node_b)
        
        # Step 4: Apply earliest arrival pruning
        # Group valid subsequent nodes by their destination station
        nodes_by_destination = defaultdict(list)
        for node_b in valid_subsequent:
            origin_b, dest_b, dep_b, arr_b = node_b
            nodes_by_destination[dest_b].append(node_b)
        
        # For each destination, keep only the node with minimum arrival time
        for dest_b, candidate_nodes in nodes_by_destination.items():
            if candidate_nodes:
                # Find the node with minimum arrival time
                earliest_node = min(candidate_nodes, key=lambda n: n[3])  # n[3] is arrival_time
                aon_graph.add_edge(node_a, earliest_node)
                edges_created += 1
                
                # Count how many edges were pruned (not created)
                edges_pruned += len(candidate_nodes) - 1
    
    print(f"Created {edges_created} edges")
    print(f"Pruned {edges_pruned} edges (kept only earliest arrival per destination)")
    
    return aon_graph


def main():
    """Main execution function."""
    # Set up paths
    graph_dir = PROJECT_ROOT / "graph"
    input_graph_path = graph_dir / "transportation_graph_freq100.gpickle"
    output_graph_path = graph_dir / "aon_pruned_graph_freq100.gpickle"
    
    # Check if input graph exists
    if not input_graph_path.exists():
        print(f"ERROR: Graph file not found: {input_graph_path}")
        print("Please run 'python scripts/build_graph.py' first to generate the graph.")
        return
    
    # Load the transportation graph
    G = load_transportation_graph(input_graph_path)
    
    # Build the AoN graph
    aon_graph = build_aon_graph(G)
    
    # Print summary
    print("\n" + "="*50)
    print("AoN Graph Summary:")
    print(f"Total Event Nodes: {aon_graph.number_of_nodes()}")
    print(f"Total Pruned Edges: {aon_graph.number_of_edges()}")
    print("="*50)
    
    # Save the new graph
    print(f"\nSaving AoN graph to {output_graph_path}...")
    with open(output_graph_path, 'wb') as f:
        pickle.dump(aon_graph, f)
    print("Graph saved successfully!")


if __name__ == "__main__":
    main()
