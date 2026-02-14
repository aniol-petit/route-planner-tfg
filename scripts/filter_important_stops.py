"""
Filter Important Stops Script for Train Routes

This script processes routes from route_stops_with_countries.csv, scores the
importance of each stop, and filters them to keep only the most important ones.

Filtering rules:
- Always keep first and last leg of each route
- Keep at least one stop per country (to preserve country representation)
- Keep first and last stops in each country
- Keep other stops only if they are major hubs (score >= threshold)

The output adds a new column 'legs_filtered' with the filtered stops.
"""

import pandas as pd
import re
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


# ============================================================================
# CONFIGURATION
# ============================================================================

# Paths (relative to script location)
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
INPUT_FILE = DATA_DIR / "route_stops_with_countries.csv"
STATIONS_FILE = DATA_DIR / "train_stations_europe.csv"
OUTPUT_FILE = DATA_DIR / "route_stops_with_countries_filtered.csv"  # Add new column to same file


# ============================================================================
# CONSTANTS
# ============================================================================

# Generic tokens to filter out from station names
GENERIC_TOKENS = {
    "station", "stn", "gare", "bahnhof", "hbf", "hl", "intl", "international",
    "aeroport", "aéroport", "airport", "tgv", "hb", "bf",
    "eurostar", "sncf", "sbb", "cfl", "ter", "rer",
}

# Name-based heuristics
IMPORTANT_KEYWORDS = [
    "central", "centrale", "centraal", "hauptbahnhof", "main station",
    "termini", "stazione centrale", "centre-ville", "city", "intl", "international",
]
MINOR_KEYWORDS = [
    "halt", "halte", "haltestelle", "stop", "mairie", "eglise", "église", "village",
]

# Score threshold for keeping intermediate stops
HIGH_SCORE_THRESHOLD = 16


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _normalize_station(name: str) -> str:
    """Normalize station name: lowercase, remove punctuation, filter generic tokens."""
    s = str(name).lower()
    s = re.sub(r"[()\[\],.-]+", " ", s)
    tokens = [t for t in s.split() if t and t not in GENERIC_TOKENS]
    if not tokens:
        tokens = s.split()
    joined = "".join(tokens)
    return re.sub(r"[^a-z0-9]+", "", joined)


def _name_pattern_score(name: str) -> int:
    """Score station based on name patterns."""
    s = str(name).lower()
    score = 0
    for kw in IMPORTANT_KEYWORDS:
        if kw in s:
            score += 3
    for kw in MINOR_KEYWORDS:
        if kw in s:
            score -= 2
    return score


def parse_legs(legs_value) -> List:
    """Parse legs JSON string into list of stops."""
    if legs_value is None or (isinstance(legs_value, float) and pd.isna(legs_value)):
        return []
    
    if isinstance(legs_value, str):
        try:
            legs_list = json.loads(legs_value)
        except Exception:
            return []
    else:
        legs_list = legs_value
    
    return legs_list


# ============================================================================
# BUILD LOOKUPS
# ============================================================================

def build_station_meta_lookup(stations_df: pd.DataFrame) -> Dict:
    """Build lookup for station metadata (is_city, is_main_station, is_airport)."""
    station_meta_df = (
        stations_df[["name", "is_city", "is_main_station", "is_airport"]]
        .dropna(subset=["name"])
        .assign(norm_name=lambda d: d["name"].map(_normalize_station))
    )
    
    station_meta_agg = (
        station_meta_df
        .groupby("norm_name")[["is_city", "is_main_station", "is_airport"]]
        .any()
        .reset_index()
    )
    
    return station_meta_agg.set_index("norm_name").to_dict(orient="index")


def build_station_frequency_stats(routes_df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """Build frequency statistics for all stations across routes."""
    print("Computing station frequencies across all routes...")
    station_counts = defaultdict(int)
    station_route_counts = defaultdict(set)
    station_examples = {}
    
    for route_idx, row in routes_df.iterrows():
        legs = parse_legs(row["legs_with_countries"])
        for stop in legs:
            if not isinstance(stop, list) or len(stop) != 2:
                continue
            station_info, times = stop
            if not isinstance(station_info, list) or len(station_info) != 2:
                continue
            station_name, country = station_info
            
            norm = _normalize_station(station_name)
            if not norm:
                continue
            
            station_counts[norm] += 1
            station_route_counts[norm].add(route_idx)
            if norm not in station_examples:
                station_examples[norm] = station_name
    
    station_freq_df = pd.DataFrame(
        [
            {
                "norm_name": norm,
                "total_occurrences": count,
                "n_routes": len(station_route_counts[norm]),
            }
            for norm, count in station_counts.items()
        ]
    )
    
    if not station_freq_df.empty:
        q90 = station_freq_df["total_occurrences"].quantile(0.9)
        q95 = station_freq_df["total_occurrences"].quantile(0.95)
    else:
        q90 = q95 = 0
    
    print(f"Frequency quantiles: q90={q90:.1f}, q95={q95:.1f}")
    
    return station_freq_df, station_examples


def score_station(
    norm_name: str,
    example_name: str,
    station_meta_lookup: Dict,
    station_freq_df: pd.DataFrame
) -> float:
    """Score a station based on metadata, name patterns, and frequency."""
    meta = station_meta_lookup.get(norm_name)
    
    # 1) Metadata score
    meta_score = 0
    if meta is not None:
        if bool(meta.get("is_main_station")):
            meta_score += 7
        if bool(meta.get("is_city")):
            meta_score += 3
        if bool(meta.get("is_airport")):
            meta_score += 3
    
    # 2) Name pattern score
    pattern_score = _name_pattern_score(example_name)
    
    # 3) Frequency score
    freq_score = 0
    if not station_freq_df.empty:
        freq_row = station_freq_df.loc[station_freq_df["norm_name"] == norm_name]
        if not freq_row.empty:
            occ = float(freq_row["total_occurrences"].iloc[0])
            q95 = station_freq_df["total_occurrences"].quantile(0.95)
            q90 = station_freq_df["total_occurrences"].quantile(0.9)
            if occ >= q95:
                freq_score += 3
            elif occ >= q90:
                freq_score += 2
    
    total = max(0, meta_score + pattern_score + freq_score)
    return total


def build_station_score_lookup(
    routes_df: pd.DataFrame,
    station_meta_lookup: Dict,
    station_freq_df: pd.DataFrame
) -> Dict[str, float]:
    """Build lookup for station importance scores."""
    print("Scoring stations...")
    
    # Get station examples
    station_examples = {}
    for _, row in routes_df.iterrows():
        legs = parse_legs(row["legs_with_countries"])
        for stop in legs:
            if not isinstance(stop, list) or len(stop) != 2:
                continue
            station_info, times = stop
            if not isinstance(station_info, list) or len(station_info) != 2:
                continue
            station_name, country = station_info
            
            norm = _normalize_station(station_name)
            if norm and norm not in station_examples:
                station_examples[norm] = station_name
    
    # Score all stations
    station_scores = {}
    for norm, example_name in station_examples.items():
        score = score_station(norm, example_name, station_meta_lookup, station_freq_df)
        station_scores[norm] = score
    
    print(f"Built station_score_lookup with {len(station_scores)} entries")
    if station_scores:
        scores_list = list(station_scores.values())
        print(f"Score stats: min={min(scores_list):.1f}, max={max(scores_list):.1f}, "
              f"mean={sum(scores_list)/len(scores_list):.1f}")
    
    return station_scores


# ============================================================================
# FILTERING LOGIC
# ============================================================================

def filter_legs(
    legs_value: List,
    station_score_lookup: Dict[str, float]
) -> List:
    """
    Filter legs to keep only important stops.
    
    Rules:
    - Always keep first and last stop
    - Keep at least one stop per country (highest score)
    - Keep first and last stops in each country
    - Keep other stops only if score >= HIGH_SCORE_THRESHOLD
    """
    legs = parse_legs(legs_value)
    if not legs or len(legs) <= 2:
        return legs
    
    # Build per-stop info list with scores
    stop_infos = []
    for idx, stop in enumerate(legs):
        if not isinstance(stop, list) or len(stop) != 2:
            continue
        station_info, times = stop
        if not isinstance(station_info, list) or len(station_info) != 2:
            continue
        station_name, country = station_info
        
        norm = _normalize_station(station_name)
        score = station_score_lookup.get(norm, 0.0)
        
        stop_infos.append({
            "idx": idx,
            "name": station_name,
            "country": country,
            "score": score,
        })
    
    n = len(stop_infos)
    if n == 0:
        return legs
    
    selected_indices = {0, n - 1}  # Always keep first and last
    
    # Group stops by country
    by_country = defaultdict(list)
    for info in stop_infos:
        country = info["country"]
        if country:  # Only process stops with a country
            by_country[country].append(info)
    
    # For each country:
    # 1. Keep the highest-scoring stop (ensures at least one per country)
    # 2. Keep first and last stops in that country
    for country, country_stops in by_country.items():
        if not country_stops:
            continue
        
        # Keep highest-scoring stop
        best_stop = max(country_stops, key=lambda x: x["score"])
        selected_indices.add(best_stop["idx"])
        
        # Keep first and last stops in this country
        if len(country_stops) > 1:
            # Find first and last indices for this country
            country_indices = sorted([s["idx"] for s in country_stops])
            selected_indices.add(country_indices[0])  # First in country
            selected_indices.add(country_indices[-1])  # Last in country
    
    # Keep any intermediate stops with sufficiently high score
    for info in stop_infos[1:-1]:  # Exclude first/last, already kept
        if info["score"] >= HIGH_SCORE_THRESHOLD:
            selected_indices.add(info["idx"])
    
    # Rebuild legs with selected stops only, preserving original order
    selected_sorted = sorted(selected_indices)
    filtered = [legs[i] for i in selected_sorted]
    return filtered


# ============================================================================
# MAIN PROCESSING
# ============================================================================

def main():
    """Main execution function."""
    print("=" * 70)
    print("Filter Important Stops for Train Routes")
    print("=" * 70)
    print()
    
    # Check input files exist
    if not INPUT_FILE.exists():
        print(f"ERROR: Input file not found: {INPUT_FILE}")
        sys.exit(1)
    if not STATIONS_FILE.exists():
        print(f"ERROR: Stations file not found: {STATIONS_FILE}")
        sys.exit(1)
    
    # Load data
    print("Loading data files...")
    print(f"  - Routes: {INPUT_FILE}")
    routes_df = pd.read_csv(INPUT_FILE)
    print(f"    Loaded {len(routes_df)} routes")
    
    print(f"  - Stations: {STATIONS_FILE}")
    stations_df = pd.read_csv(STATIONS_FILE, low_memory=False)
    print(f"    Loaded {len(stations_df)} stations")
    
    # Build lookups
    print("\nBuilding lookup tables...")
    print("  - Building station metadata lookup...")
    station_meta_lookup = build_station_meta_lookup(stations_df)
    print(f"    Built lookup with {len(station_meta_lookup)} entries")
    
    print("  - Building station frequency statistics...")
    station_freq_df, station_examples = build_station_frequency_stats(routes_df)
    print(f"    Found {len(station_freq_df)} distinct stations")
    
    print("  - Building station score lookup...")
    station_score_lookup = build_station_score_lookup(
        routes_df, station_meta_lookup, station_freq_df
    )
    
    # Filter legs
    print("\nFiltering stops...")
    routes_df["legs_filtered"] = routes_df["legs_with_countries"].apply(
        lambda x: filter_legs(x, station_score_lookup)
    )
    
    # Convert filtered legs to JSON strings
    routes_df["legs_filtered"] = routes_df["legs_filtered"].apply(json.dumps)
    
    # Statistics
    print("\nStatistics:")
    total_stops_before = sum(
        len(parse_legs(row["legs_with_countries"]))
        for _, row in routes_df.iterrows()
    )
    total_stops_after = sum(
        len(parse_legs(row["legs_filtered"]))
        for _, row in routes_df.iterrows()
    )
    
    avg_before = total_stops_before / len(routes_df) if len(routes_df) > 0 else 0
    avg_after = total_stops_after / len(routes_df) if len(routes_df) > 0 else 0
    
    print(f"  Total stops before: {total_stops_before}")
    print(f"  Total stops after:  {total_stops_after}")
    print(f"  Reduction: {100 * (1 - total_stops_after / total_stops_before):.1f}%")
    print(f"  Average stops per route before: {avg_before:.2f}")
    print(f"  Average stops per route after:  {avg_after:.2f}")
    
    # Save results
    print(f"\nSaving results to {OUTPUT_FILE}...")
    routes_df.to_csv(OUTPUT_FILE, index=False)
    print("Done!")
    
    print("\n" + "=" * 70)
    print("Filtering complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
