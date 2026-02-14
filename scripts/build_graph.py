"""
Script to build a graph from airport, route, and connection data.
"""

import pandas as pd
import ast
import json
from collections import defaultdict
import networkx as nx
from datetime import datetime
import pickle


def parse_legs_filtered(legs_str):
    """
    Parse the legs_filtered string into a list of tuples.
    Returns list of [(station_name, country), (departure_time, arrival_time)]
    """
    try:
        # The string is a JSON-like nested list
        legs = ast.literal_eval(legs_str)
        result = []
        for leg in legs:
            if len(leg) >= 2:
                station_info = leg[0]  # [station_name, country]
                time_info = leg[1]    # [departure_time, arrival_time]
                if len(station_info) >= 2 and len(time_info) >= 2:
                    station_name = station_info[0]
                    country = station_info[1]
                    dep_time = time_info[0]
                    arr_time = time_info[1]
                    result.append((station_name, country, dep_time, arr_time))
        return result
    except Exception as e:
        print(f"Error parsing legs_filtered: {e}")
        return []


def parse_legs_filtered_absolute_utc(legs_str):
    """
    Parse the legs_filtered_absolute_utc string into a list of tuples.
    Returns list of [(station_name, country), (departure_time_utc, arrival_time_utc)]
    """
    try:
        legs = ast.literal_eval(legs_str)
        result = []
        for leg in legs:
            if len(leg) >= 2:
                station_info = leg[0]  # [station_name, country]
                time_info = leg[1]     # [departure_time_utc, arrival_time_utc]
                if len(station_info) >= 2 and len(time_info) >= 2:
                    station_name = station_info[0]
                    country = station_info[1]
                    dep_time_utc = time_info[0]
                    arr_time_utc = time_info[1]
                    # Convert to int if they're strings
                    if isinstance(dep_time_utc, str):
                        dep_time_utc = int(dep_time_utc) if dep_time_utc.isdigit() else None
                    if isinstance(arr_time_utc, str):
                        arr_time_utc = int(arr_time_utc) if arr_time_utc.isdigit() else None
                    result.append((station_name, country, dep_time_utc, arr_time_utc))
        return result
    except Exception as e:
        print(f"Error parsing legs_filtered_absolute_utc: {e}")
        return []


def build_graph():
    """
    Build the graph from the data files.
    """
    # Initialize the graph
    G = nx.MultiDiGraph()
    
    # 1. Add airport nodes from airports_df.csv
    print("Loading airports...")
    airports_df = pd.read_csv('../data/final_data/airports_df.csv')
    airport_countries = {}
    for _, row in airports_df.iterrows():
        iata_code = row['iata_code']
        country = row['country_name']
        if pd.notna(iata_code):
            G.add_node(iata_code, country=country, node_type='airport')
            airport_countries[iata_code] = country
    
    print(f"Added {len(airport_countries)} airport nodes")
    
    # 2. Add train/bus station nodes from routes_df.csv
    print("Loading train/bus stations...")
    routes_df = pd.read_csv('../data/final_data/routes_df.csv')
    station_countries = {}
    
    for _, row in routes_df.iterrows():
        legs_filtered = row['legs_filtered']
        if pd.notna(legs_filtered):
            legs = parse_legs_filtered(legs_filtered)
            for station_name, country, _, _ in legs:
                if station_name not in G.nodes():
                    G.add_node(station_name, country=country, node_type='station')
                    station_countries[station_name] = country
    
    print(f"Added {len(station_countries)} station nodes")
    
    # 3. Add flight edges from flights_df.csv
    print("Loading flight edges...")
    flights_df = pd.read_csv('../data/final_data/flights_df.csv')
    
    # Filter flights for 2026-01-21 and 2026-01-22
    flights_df['departure_scheduled'] = pd.to_datetime(flights_df['departure_scheduled'], errors='coerce')
    flights_df = flights_df[
        (flights_df['departure_scheduled'].dt.date == pd.Timestamp('2026-01-21').date()) |
        (flights_df['departure_scheduled'].dt.date == pd.Timestamp('2026-01-22').date())
    ]
    
    # Group by route and collect all time pairs
    flight_edges = defaultdict(list)
    for _, row in flights_df.iterrows():
        dep_airport = row['departure_airport']
        arr_airport = row['arrival_airport']
        dep_time_utc = row['departure_scheduled_absolute_utc']
        arr_time_utc = row['arrival_scheduled_absolute_utc']
        
        if pd.notna(dep_airport) and pd.notna(arr_airport) and pd.notna(dep_time_utc) and pd.notna(arr_time_utc):
            route_key = (dep_airport, arr_airport)
            flight_edges[route_key].append((int(dep_time_utc), int(arr_time_utc)))
    
    # Add edges to graph (one edge per unique route with all time pairs as attribute)
    for (dep_airport, arr_airport), time_pairs in flight_edges.items():
        if dep_airport in G.nodes() and arr_airport in G.nodes():
            G.add_edge(dep_airport, arr_airport, 
                      edge_type='flight',
                      time_pairs=time_pairs)
    
    print(f"Added {len(flight_edges)} flight edges")
    
    # 4. Add train route edges from routes_df.csv
    print("Loading train route edges...")
    train_edges = defaultdict(list)
    
    for _, row in routes_df.iterrows():
        legs_utc = row['legs_filtered_absolute_utc']
        if pd.notna(legs_utc):
            legs = parse_legs_filtered_absolute_utc(legs_utc)
            
            # Create edges between consecutive stops
            for i in range(len(legs) - 1):
                station1, country1, dep_time1, arr_time1 = legs[i]
                station2, country2, dep_time2, arr_time2 = legs[i + 1]
                
                # According to the spec: 
                # For pair [361, 361] at first stop: use second position (361)
                # For pair [499, 499] at second stop: use first position (499)
                # Store as (361, 499) = (arr_time1, dep_time2)
                if arr_time1 is not None and dep_time2 is not None:
                    route_key = (station1, station2)
                    # Store (departure_time, arrival_time) where:
                    # departure_time = arr_time1 (second position of first pair)
                    # arrival_time = dep_time2 (first position of second pair)
                    train_edges[route_key].append((int(arr_time1), int(dep_time2)))
    
    # Add edges to graph (one edge per unique route with all time pairs as attribute)
    for (station1, station2), time_pairs in train_edges.items():
        if station1 in G.nodes() and station2 in G.nodes():
            G.add_edge(station1, station2,
                      edge_type='train',
                      time_pairs=time_pairs)
    
    print(f"Added {len(train_edges)} train route edges")
    
    # 5. Add connection edges from connections.csv (bidirectional)
    print("Loading connection edges...")
    connections_df = pd.read_csv('../data/final_data/connections.csv')
    
    connection_count = 0
    for _, row in connections_df.iterrows():
        origin = row['Origin']
        destination = row['Destination']
        connection_time = row['connection_time']
        
        if pd.notna(origin) and pd.notna(destination) and pd.notna(connection_time):
            # Add edge from origin to destination
            if origin in G.nodes() and destination in G.nodes():
                G.add_edge(origin, destination,
                          edge_type='connection',
                          connection_time=int(connection_time))
                connection_count += 1
            
            # Add edge from destination to origin (bidirectional)
            if destination in G.nodes() and origin in G.nodes():
                G.add_edge(destination, origin,
                          edge_type='connection',
                          connection_time=int(connection_time))
                connection_count += 1
    
    print(f"Added {connection_count} connection edges")
    
    # Print summary
    print("\n" + "="*50)
    print("Graph Summary:")
    print(f"Total nodes: {G.number_of_nodes()}")
    print(f"Total edges: {G.number_of_edges()}")
    print(f"Airport nodes: {sum(1 for n, d in G.nodes(data=True) if d.get('node_type') == 'airport')}")
    print(f"Station nodes: {sum(1 for n, d in G.nodes(data=True) if d.get('node_type') == 'station')}")
    print(f"Flight edges: {sum(1 for u, v, d in G.edges(data=True) if d.get('edge_type') == 'flight')}")
    print(f"Train edges: {sum(1 for u, v, d in G.edges(data=True) if d.get('edge_type') == 'train')}")
    print(f"Connection edges: {sum(1 for u, v, d in G.edges(data=True) if d.get('edge_type') == 'connection')}")
    print("="*50)
    
    return G


if __name__ == "__main__":
    graph = build_graph()
    
    # Save the graph using pickle
    with open('../data/final_data/transportation_graph.gpickle', 'wb') as f:
        pickle.dump(graph, f)
    print("Graph saved as transportation_graph.gpickle")
    
    # Alternative: Save as GraphML (more portable, but may not preserve all attributes)
    # nx.write_graphml(graph, '../data/final_data/transportation_graph.graphml')
    
    print("\nGraph built successfully!")
    print("You can now use the graph object for your analysis.")
