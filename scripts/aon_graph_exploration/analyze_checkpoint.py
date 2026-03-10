#!/usr/bin/env python3
"""
Analyze the live_checkpoint.json file to break down routes by number of countries.
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def extract_country(location: str) -> str:
    """Extract country name from location string.
    
    Format: "AIRPORT_CODE (Country Name)" or "Station Name (Country Name)"
    """
    match = re.search(r'\(([^)]+)\)', location)
    if match:
        return match.group(1)
    return location


def get_unique_countries(route: List[Dict[str, Any]]) -> Set[str]:
    """Extract unique countries from a route."""
    countries = set()
    for segment in route:
        origin_country = extract_country(segment['origin'])
        dest_country = extract_country(segment['dest'])
        countries.add(origin_country)
        countries.add(dest_country)
    return countries


def format_time(minutes: int) -> str:
    """Convert minutes since midnight to readable time format."""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def format_route(route: List[Dict[str, Any]], route_num: int) -> str:
    """Format a route in a human-readable way."""
    countries = get_unique_countries(route)
    lines = [
        f"\n  Route #{route_num} ({len(countries)} countries):",
        f"    Countries visited: {', '.join(sorted(countries))}",
        "    Path:"
    ]
    
    for i, segment in enumerate(route, 1):
        origin = segment['origin']
        dest = segment['dest']
        dep_time = format_time(segment['dep_time'])
        arr_time = format_time(segment['arr_time'])
        lines.append(f"      {i}. {origin} → {dest}")
        lines.append(f"         Departure: {dep_time}, Arrival: {arr_time}")
    
    return "\n".join(lines)


def main():
    """Main analysis function."""
    checkpoint_file = Path(__file__).resolve().parent / "results" / "live_checkpoint.json"
    
    print(f"Loading checkpoint file: {checkpoint_file}")
    with checkpoint_file.open('r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"\n=== Checkpoint Summary ===")
    print(f"Global max countries: {data.get('global_max', 'N/A')}")
    print(f"Nodes explored: {data.get('nodes_explored', 0):,}")
    print(f"Elapsed time: {data.get('elapsed_seconds', 0):.2f} seconds")
    
    routes_by_country_count = defaultdict(list)
    routes_by_country_and_start = defaultdict(lambda: defaultdict(int))
    
    # Process all routes
    for country_count_str, routes_list in data.get('routes', {}).items():
        country_count = int(country_count_str)
        for route in routes_list:
            unique_countries = get_unique_countries(route)
            actual_count = len(unique_countries)
            starting_node = route[0]['origin'] if route else "Unknown"
            routes_by_country_count[actual_count].append(route)
            routes_by_country_and_start[actual_count][starting_node] += 1
    
    # Sort by country count (descending)
    sorted_counts = sorted(routes_by_country_count.keys(), reverse=True)
    
    print(f"\n=== Routes by Number of Countries ===")
    print(f"Total unique routes: {sum(len(routes) for routes in routes_by_country_count.values())}")
    print()
    
    for country_count in sorted_counts:
        routes = routes_by_country_count[country_count]
        total_routes = len(routes)
        
        # Get starting node breakdown
        start_node_counts = routes_by_country_and_start[country_count]
        
        if len(start_node_counts) == 1:
            # Single starting node
            start_node = list(start_node_counts.keys())[0]
            print(f"{total_routes} route(s) with {country_count} countries, starting at {start_node}")
        else:
            # Multiple starting nodes
            parts = []
            for start_node, count in sorted(start_node_counts.items(), key=lambda x: x[1], reverse=True):
                parts.append(f"{count} start at {start_node}")
            print(f"{total_routes} route(s) with {country_count} countries, of which {', '.join(parts)}")
    
    print(f"\n=== Detailed Route Information ===")
    
    for country_count in sorted_counts:
        routes = routes_by_country_count[country_count]
        print(f"\n{'='*80}")
        print(f"ROUTES WITH {country_count} COUNTRIES ({len(routes)} total)")
        print(f"{'='*80}")
        
        #for idx, route in enumerate(routes, 1):
         #   print(format_route(route, idx))
        #  print()


if __name__ == "__main__":
    main()
