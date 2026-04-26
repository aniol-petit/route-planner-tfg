import json
import re
import ast
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import duckdb
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots
import plotly.graph_objects as go

import scoring_engine


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PARQUET_PATH = str(PROJECT_ROOT / "scripts" / "aon_graph_exploration" / "results" / "master_elite_routes_full.parquet")

TOTAL_BASELINE_STATIONS = 899

# Default pagination sizes
ROUTE_EXPLORER_INITIAL = 20
ROUTE_EXPLORER_STEP = 20
ROUTE_RANKING_INITIAL = 10
ROUTE_RANKING_STEP = 10


# ----------------------------
# Shared helpers
# ----------------------------

def extract_station_code(station_label: str) -> str:
    """
    Extract the 3-letter station code from strings like:
    - "SVO (Russian Federation)" -> "SVO"
    - "SVO - Moscow (Russian Federation)" -> "SVO"
    Fallback: first 3 consecutive uppercase letters.
    """
    if not station_label:
        return ""

    first_token = station_label.split()[0]
    if len(first_token) == 3 and first_token.isalnum():
        return first_token

    match = re.search(r"\b([A-Z0-9]{3,4})\b", station_label)
    if match:
        return match.group(1)

    return station_label.strip()


def clean_station_name(label: str) -> str:
    """Removes the '(Country)' suffix to perfectly match CSV station names."""
    if not label: return ""
    return re.sub(r"\s*\(.*?\)\s*$", "", label).strip()

@st.cache_data
def load_schedule_lookup() -> dict:
    """Builds an Edge-Based lookup: (Origin, Dest, Dep%1440, Arr%1440) -> (Local_Dep, Local_Arr)"""
    lookup = {}
    
    # 1. Parse Flights
    try:
        flights = pd.read_csv(PROJECT_ROOT / "data" / "final_data" / "flights_df.csv")
        for _, row in flights.iterrows():
            orig = str(row['departure_airport']).strip()
            dest = str(row['arrival_airport']).strip()
            
            # Extract pure HH:MM from strings like "2026-01-23t07:30:00.000"
            dep_raw = str(row['departure_scheduled'])
            arr_raw = str(row['arrival_scheduled'])
            dep_m = re.search(r'(\d{2}:\d{2})', dep_raw)
            arr_m = re.search(r'(\d{2}:\d{2})', arr_raw)
            dep_hhmm = dep_m.group(1) if dep_m else dep_raw[:5]
            arr_hhmm = arr_m.group(1) if arr_m else arr_raw[:5]
            
            # STRICTLY use UTC absolute minutes to avoid dictionary collisions
            if pd.notna(row.get('departure_scheduled_absolute_utc')) and pd.notna(row.get('arrival_scheduled_absolute_utc')):
                d_utc = int(float(row['departure_scheduled_absolute_utc'])) % 1440
                a_utc = int(float(row['arrival_scheduled_absolute_utc'])) % 1440
                lookup[(orig, dest, d_utc, a_utc)] = (dep_hhmm, arr_hhmm)
    except Exception as e:
        print(f"Error parsing flights: {e}")

    # 2. Parse Trains
    try:
        routes = pd.read_csv(PROJECT_ROOT / "data" / "final_data" / "routes_df.csv")
        for _, row in routes.iterrows():
            if pd.isna(row.get('legs_filtered')): continue
            try:
                legs_str = json.loads(row['legs_filtered']) if isinstance(row['legs_filtered'], str) else ast.literal_eval(row['legs_filtered'])
                
                legs_utc = None
                if pd.notna(row.get('legs_filtered_absolute_utc')):
                    legs_utc = json.loads(row['legs_filtered_absolute_utc']) if isinstance(row['legs_filtered_absolute_utc'], str) else ast.literal_eval(row['legs_filtered_absolute_utc'])
                    
                for i in range(len(legs_str) - 1):
                    orig = legs_str[i][0][0].strip()
                    dest = legs_str[i+1][0][0].strip()
                    dep_hhmm = legs_str[i][1][1]
                    arr_hhmm = legs_str[i+1][1][0]
                    
                    if legs_utc and i+1 < len(legs_utc):
                        d_utc = int(float(legs_utc[i][1][1])) % 1440
                        a_utc = int(float(legs_utc[i+1][1][0])) % 1440
                        lookup[(orig, dest, d_utc, a_utc)] = (dep_hhmm, arr_hhmm)
            except Exception:
                continue
    except Exception as e:
        print(f"Error parsing trains: {e}")

    return lookup


@st.cache_data
def prepare_parquet_page(where_clause: str, limit: int, offset: int) -> pd.DataFrame:
    query = f"SELECT * FROM '{PARQUET_PATH}' WHERE {where_clause} LIMIT {limit} OFFSET {offset}"
    return duckdb.query(query).df()


@st.cache_data
def load_parquet_filtered(where_clause: str) -> pd.DataFrame:
    """
    Load the full set of routes matching the current filters from the Parquet file.

    This is used to feed the UI-level pagination and downstream analyses, so the
    app always knows the true filtered population instead of just a small page.
    """
    query = f"SELECT * FROM '{PARQUET_PATH}' WHERE {where_clause}"
    return duckdb.query(query).df()


@st.cache_data
def parquet_route_metrics(where_clause: str) -> Tuple[int, int]:
    total_routes = duckdb.query(
        f"SELECT COUNT(*) FROM '{PARQUET_PATH}' WHERE {where_clause}"
    ).fetchone()[0]
    max_countries = duckdb.query(
        f"SELECT MAX(total_countries) FROM '{PARQUET_PATH}' WHERE {where_clause}"
    ).fetchone()[0]
    return int(total_routes or 0), int(max_countries or 0)


@st.cache_data
def get_parquet_metadata(parquet_path: str) -> Dict:
    """
    Return global stats and the full list of unique starting origins from the Parquet file.
    """
    global_stats = duckdb.query(
        f"SELECT COUNT(*), MAX(total_countries) FROM '{parquet_path}'"
    ).fetchone()

    origins_df = duckdb.query(
        f"SELECT DISTINCT start_node FROM '{parquet_path}' ORDER BY start_node"
    ).df()
    unique_origins = origins_df["start_node"].tolist()

    return {
        "global_total_routes": global_stats[0] if global_stats else 0,
        "global_max_countries": global_stats[1] if global_stats else 0,
        "unique_origins": unique_origins,
    }

def build_route_dataframe(route_segments: list, lookup: dict) -> pd.DataFrame:
    rows = []
    if not route_segments: return pd.DataFrame()
    
    # Clock strictly starts at the ARRIVAL time of the first flight/leg
    start_time = int(route_segments[0].get("arr_time", 0))
    
    for i, seg in enumerate(route_segments):
        raw_orig = seg.get("origin", "")
        raw_dest = seg.get("dest", "")
        clean_orig = clean_station_name(raw_orig)
        clean_dest = clean_station_name(raw_dest)
        
        dep_abs = int(seg.get("dep_time", 0))
        arr_abs = int(seg.get("arr_time", 0))
        
        # Elapsed time calculation (100% timezone insensible, purely physical time)
        if i == 0:
            elapsed_str = "00:00"
        else:
            elapsed_mins = max(0, arr_abs - start_time)
            elapsed_str = f"{elapsed_mins // 60:02d}:{elapsed_mins % 60:02d}"
        
        # Edge-Based Lookup matching with modulo 1440
        key = (clean_orig, clean_dest, dep_abs % 1440, arr_abs % 1440)
        
        if key in lookup:
            dep_str, arr_str = lookup[key]
        else:
            # Fallback format for artificial transfers (like changing stations)
            dep_str = f"~ {(dep_abs // 60) % 24:02d}:{dep_abs % 60:02d}"
            arr_str = f"~ {(arr_abs // 60) % 24:02d}:{arr_abs % 60:02d}"
            
        rows.append({
            "Origin": raw_orig,
            "Destination": raw_dest,
            "Real Dep": dep_str,
            "Real Arr": arr_str,
            "Elapsed": elapsed_str
        })
    return pd.DataFrame(rows)


def extract_routes_from_data(data: Dict) -> List[List[Dict]]:
    """
    Extract all routes from a checkpoint JSON dict.

    Expected structure:
        {
            "routes": {
                "15": [ [segment, segment, ...], ... ],
                "14": [ ... ]
            },
            ...
        }
    """
    routes_by_score = data.get("routes", {}) or {}
    all_routes: List[List[Dict]] = []

    for _score, routes in routes_by_score.items():
        if not isinstance(routes, Iterable):
            continue
        for route in routes:
            if isinstance(route, Iterable) and route:
                all_routes.append(route)

    return all_routes


def compute_frequencies(
    routes: Iterable[Iterable[Dict]],
) -> Tuple[Counter, Counter]:
    """
    Station and edge frequencies.
    """
    station_counter: Counter = Counter()
    edge_counter: Counter = Counter()

    for route in routes:
        for segment in route:
            origin_label = segment.get("origin", "")
            dest_label = segment.get("dest", "")

            origin_code = extract_station_code(origin_label)
            dest_code = extract_station_code(dest_label)

            if origin_code:
                station_counter[origin_code] += 1
            if dest_code:
                station_counter[dest_code] += 1

            if origin_code and dest_code:
                edge_key = f"{origin_code} -> {dest_code}"
                edge_counter[edge_key] += 1

    return station_counter, edge_counter


def build_station_dataframe(station_counter: Counter) -> pd.DataFrame:
    rows = [{"Station": code, "Count": count} for code, count in station_counter.items()]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Count", ascending=False).reset_index(drop=True)
        df["Cumulative Count"] = df["Count"].cumsum()
        total_occurrences = df["Count"].sum()
        if total_occurrences > 0:
            df["Cumulative %"] = df["Cumulative Count"] / total_occurrences * 100.0
        else:
            df["Cumulative %"] = 0.0
    return df


def build_edge_dataframe(edge_counter: Counter) -> pd.DataFrame:
    rows = []
    for edge, count in edge_counter.items():
        if "->" in edge:
            origin, dest = [p.strip() for p in edge.split("->", 1)]
        else:
            origin, dest = "", ""
        rows.append(
            {
                "Origin": origin,
                "Destination": dest,
                "Edge": edge,
                "Count": count,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Count", ascending=False).reset_index(drop=True)
        df["Cumulative Count"] = df["Count"].cumsum()
        total_occurrences = df["Count"].sum()
        if total_occurrences > 0:
            df["Cumulative %"] = df["Cumulative Count"] / total_occurrences * 100.0
        else:
            df["Cumulative %"] = 0.0
    return df


def inject_custom_css() -> None:
    """
    Dark gradient + glassmorphism similar to the HTML visualizer.
    """
    st.markdown(
        """
        <style>
        body {
            background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
            color: #e0e0e0;
        }

        .stApp {
            background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
            color: #e0e0e0;
        }

        .main .block-container {
            padding-top: 1.5rem;
            padding-bottom: 3rem;
            max-width: 1400px;
            font-size: 0.95rem;
        }

        h1, h2, h3 {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }

        .elite-title {
            text-align: center;
            margin-bottom: 0.5rem;
            font-size: 2.3rem;
            font-weight: 700;
            background: linear-gradient(90deg, #00d4ff, #5b86e5);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .elite-subtitle {
            text-align: center;
            color: #b0b0b0;
            margin-bottom: 1.8rem;
        }

        .elite-stats-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 20px 24px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            margin-bottom: 1.5rem;
        }

        .elite-stats-row {
            display: flex;
            justify-content: center;
            gap: 30px;
            flex-wrap: wrap;
        }

        .elite-stat-item {
            text-align: center;
            padding: 15px 25px;
            background: rgba(0, 212, 255, 0.08);
            border-radius: 10px;
            border: 1px solid rgba(0, 212, 255, 0.3);
            min-width: 180px;
        }

        .elite-stat-value {
            font-size: 1.8em;
            font-weight: bold;
            color: #00d4ff;
        }

        .elite-stat-label {
            font-size: 0.9em;
            color: #b0b0b0;
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .elite-filters-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 18px 22px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            margin-bottom: 1.2rem;
        }

        .elite-filters-title {
            color: #00d4ff;
            font-size: 0.95em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 0.5rem;
        }

        .stDataFrame, .stTable {
            border-radius: 10px;
            overflow: hidden;
        }

        .js-plotly-plot .plotly .main-svg {
            background-color: rgba(0, 0, 0, 0) !important;
        }

        /* Custom Route Itinerary Styles */
        .route-container {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 10px;
            margin-top: 10px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .route-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            transition: background 0.2s;
        }
        .route-row:last-child {
            border-bottom: none;
        }
        .route-row:hover {
            background: rgba(0, 212, 255, 0.05);
        }
        .route-stations {
            flex: 2;
            display: flex;
            align-items: center;
        }
        .station-box {
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-width: 120px;
        }
        .station-code {
            color: #ffffff;
            font-weight: 600;
            font-size: 1.05em;
        }
        .station-country {
            color: #888888;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .route-arrow {
            color: #5b86e5;
            margin: 0 15px;
            font-weight: bold;
        }
        .route-times {
            flex: 1;
            text-align: center;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }
        .time-badge {
            background: rgba(0, 212, 255, 0.1);
            border: 1px solid rgba(0, 212, 255, 0.3);
            color: #00d4ff;
            padding: 4px 10px;
            border-radius: 20px;
            font-family: monospace;
            font-size: 0.9em;
            font-weight: bold;
        }
        .route-elapsed {
            flex: 1;
            text-align: right;
        }
        .elapsed-badge {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #b0b0b0;
            padding: 4px 10px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.85em;
        }

        /* Sidebar contrast and legibility */
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #141e30 0%, #243b55 100%);
            color: #f0f0f0;
        }

        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] p {
            color: #f0f4ff !important;
        }

        /* Brighter labels for radios/sliders */
        label[data-baseweb="radio"],
        div[data-baseweb="radio"] label,
        div[data-baseweb="slider"] label {
            color: #e6e9ff !important;
            font-weight: 500;
        }

        /* Improve expanders in Route Explorer */
        div[data-testid="stExpander"] {
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.12);
            background: rgba(255, 255, 255, 0.03);
        }

        div[data-testid="stExpander"] > div {
            color: #e0e6ff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------
# Elite analysis (existing dashboard)
# ----------------------------

def render_beautiful_route(seg_df: pd.DataFrame):
    """Generates and renders a custom HTML timeline, strictly minified to prevent Markdown code-block rendering."""
    if seg_df.empty:
        st.info("No route segments to display.")
        return

    html_parts = ["<div class='route-container'>"]
    
    for _, row in seg_df.iterrows():
        orig_raw = str(row['Origin'])
        dest_raw = str(row['Destination'])
        
        import re
        orig_m = re.match(r"(.*?)\s*\((.*?)\)", orig_raw)
        o_name, o_country = orig_m.groups() if orig_m else (orig_raw, "")
            
        dest_m = re.match(r"(.*?)\s*\((.*?)\)", dest_raw)
        d_name, d_country = dest_m.groups() if dest_m else (dest_raw, "")

        dep = str(row['Real Dep']).strip()
        arr = str(row['Real Arr']).strip()
        elapsed = str(row['Elapsed']).strip()
        
        # Packed strictly into a single line with NO line breaks
        row_html = f"<div class='route-row'><div class='route-stations'><div class='station-box'><div class='station-code'>{o_name}</div><div class='station-country'>{o_country}</div></div><div class='route-arrow'>➔</div><div class='station-box'><div class='station-code'>{d_name}</div><div class='station-country'>{d_country}</div></div></div><div class='route-times'><span class='time-badge'>{dep}</span> <span class='route-arrow'>➔</span> <span class='time-badge'>{arr}</span></div><div class='route-elapsed'><span class='elapsed-badge'>⏱ {elapsed}</span></div></div>"
        
        html_parts.append(row_html)
        
    html_parts.append("</div>")
    
    # Force strip ANY remaining hidden newlines that might trigger Streamlit
    final_html = "".join(html_parts).replace("\n", "").replace("\r", "")
    
    st.markdown(final_html, unsafe_allow_html=True)

def render_header(total_stations: int, total_edges: int) -> None:
    st.markdown(
        "<div class='elite-title'>Elite Network Frequency Analysis</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='elite-subtitle'>Aggregated usage of stations and connections across all winning routes</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="elite-stats-card">
          <div class="elite-stats-row">
            <div class="elite-stat-item">
              <div class="elite-stat-value">{total_stations:,}</div>
              <div class="elite-stat-label">Total Unique Stations</div>
            </div>
            <div class="elite-stat-item">
              <div class="elite-stat-value">{total_edges:,}</div>
              <div class="elite-stat-label">Total Unique Edges</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_filters(default_top_stations: int, default_top_edges: int) -> Tuple[int, int]:
    with st.container():
        st.markdown(
            "<div class='elite-filters-card'><div class='elite-filters-title'>Display Controls</div>",
            unsafe_allow_html=True,
        )

        col1, col2 = st.columns(2)

        with col1:
            top_n_stations = st.slider(
                "Top stations to display",
                min_value=10,
                max_value=200,
                value=default_top_stations,
                step=10,
            )

        with col2:
            top_n_edges = st.slider(
                "Top edges to display",
                min_value=20,
                max_value=300,
                value=default_top_edges,
                step=20,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    return top_n_stations, top_n_edges


def render_station_section(stations_df: pd.DataFrame, top_n: int) -> None:
    st.markdown("### Top Stations by Usage (Pareto Analysis)")

    if stations_df.empty:
        st.info("No station data available from the current checkpoint.")
        return

    top_df = stations_df.head(top_n)

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Bar(
            x=top_df["Station"],
            y=top_df["Count"],
            name="Frequency",
            marker=dict(color="#00d4ff"),
        ),
        secondary_y=False,
    )

    fig.add_trace(
        go.Scatter(
            x=top_df["Station"],
            y=top_df["Cumulative %"],
            name="Cumulative %",
            mode="lines+markers",
            line=dict(color="#5b86e5", width=3),
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title_text="Station Usage Pareto (Frequency vs. Cumulative %)",
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    fig.update_xaxes(title_text="Station")
    fig.update_yaxes(title_text="Usage Count", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative %", range=[0, 105], secondary_y=True)

    st.plotly_chart(fig, use_container_width=True)
    st.markdown("**Station Frequency Table (with Cumulative %)**")
    st.dataframe(
        top_df[["Station", "Count", "Cumulative %"]],
        use_container_width=True,
        hide_index=True,
    )


def render_edge_section(edges_df: pd.DataFrame, top_n: int) -> None:
    st.markdown("### Top Edges by Usage")

    if edges_df.empty:
        st.info("No edge data available from the current checkpoint.")
        return

    top_df = edges_df.head(top_n)[["Origin", "Destination", "Count", "Cumulative %"]]
    st.markdown("**Edge Frequency Table (with Cumulative %)**")
    st.dataframe(top_df, use_container_width=True, hide_index=True)


def _build_long_tail_dataframe(stations_df: pd.DataFrame) -> pd.DataFrame:
    if stations_df.empty:
        return stations_df.copy()

    df = stations_df.copy()
    df = df.sort_values("Count", ascending=False).reset_index(drop=True)
    df["Rank"] = df.index + 1
    cols = ["Rank", "Station", "Count"]
    for extra in ["Cumulative Count", "Cumulative %"]:
        if extra in df.columns:
            cols.append(extra)
    df = df[cols]
    return df


def render_long_tail_section(stations_df: pd.DataFrame) -> None:
    st.markdown("### Long-Tail & Cutoff Analysis")

    if stations_df.empty:
        st.info("No station data available for long-tail analysis.")
        return

    long_tail_df = _build_long_tail_dataframe(stations_df)
    if long_tail_df.empty:
        st.info("No station data available for long-tail analysis.")
        return

    max_count = int(long_tail_df["Count"].max())
    if max_count <= 0:
        st.info("All stations have zero recorded appearances; long-tail analysis is not meaningful.")
        return

    st.markdown(
        "This section explores the **poor-performing stations** and helps you pick a "
        "cutoff based on a minimum required number of appearances."
    )

    baseline = TOTAL_BASELINE_STATIONS

    col_thresh, col_metrics = st.columns([1, 2])
    with col_thresh:
        threshold = st.slider(
            "Minimum required appearances (X)",
            min_value=1,
            max_value=max_count,
            value=min(2, max_count),
            step=1,
            help="Stations with fewer than X recorded appearances are considered poor performers.",
        )

    below_mask = long_tail_df["Count"] < threshold
    if below_mask.any():
        first_below_rank = int(long_tail_df.loc[below_mask, "Rank"].min())
        cutoff_rank_text = (
            f"Stations with fewer than **{threshold}** appearances start at **Rank {first_below_rank}**."
        )
    else:
        cutoff_rank_text = (
            f"No stations fall below **{threshold}** appearances in the observed elite routes "
            f"(all {len(long_tail_df)} observed stations meet or exceed this threshold)."
        )

    kept_df = long_tail_df[long_tail_df["Count"] >= threshold]
    kept_stations = int(len(kept_df))

    if baseline > 0:
        eliminated_pct = max(0.0, (1.0 - kept_stations / baseline) * 100.0)
    else:
        eliminated_pct = 0.0

    with col_metrics:
        st.markdown(
            cutoff_rank_text
            + "<br/>"
            + (
                f"Keeping only stations with **≥ {threshold}** appearances leaves a graph of "
                f"**{kept_stations} stations**, eliminating approximately "
                f"**{eliminated_pct:0.1f}%** of the original {baseline}-node graph."
            ),
            unsafe_allow_html=True,
        )

    st.markdown("#### Long-Tail Frequency Curve")

    fig_lt = px.line(
        long_tail_df,
        x="Rank",
        y="Count",
        title="Station Frequency vs. Rank (Long Tail)",
    )
    fig_lt.update_traces(line=dict(color="#00d4ff"))
    fig_lt.add_hline(
        y=threshold,
        line_dash="dash",
        line_color="#ff4b4b",
        annotation_text=f"Threshold = {threshold}",
        annotation_position="top left",
    )
    fig_lt.update_layout(
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=60, b=40),
        xaxis_title="Rank (1 = most frequent)",
        yaxis_title="Frequency (appearances)",
    )

    st.plotly_chart(fig_lt, use_container_width=True)

    st.markdown("#### Full Station Frequency Table")
    st.markdown(
        "Search and scroll to inspect stations all the way down to the poorest performers."
    )
    display_df = long_tail_df.rename(columns={"Count": "Frequency"})
    st.dataframe(
        display_df[["Rank", "Station", "Frequency"]],
        use_container_width=True,
        hide_index=True,
    )


def _required_items_for_coverage(df: pd.DataFrame, coverage: float) -> int:
    if df.empty or "Cumulative %" not in df.columns:
        return 0

    cumulative = df["Cumulative %"]
    mask = cumulative >= coverage
    if not mask.any():
        return int(len(df))

    first_idx = mask.idxmax()
    return int(first_idx) + 1


def render_pruning_section(stations_df: pd.DataFrame, total_unique_stations: int) -> None:
    st.markdown("### Graph Pruning Potential")

    if stations_df.empty or total_unique_stations == 0:
        st.info("No station data available to estimate pruning potential.")
        return

    baseline = TOTAL_BASELINE_STATIONS
    percent_original_used = (
        (total_unique_stations / baseline) * 100.0 if baseline > 0 else 0.0
    )

    stations_80 = _required_items_for_coverage(stations_df, 80.0)
    stations_95 = _required_items_for_coverage(stations_df, 95.0)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            "% of Original Graph Used",
            f"{percent_original_used:0.1f}%",
            help="Unique elite stations relative to the baseline graph size.",
        )
    with col2:
        st.metric("Stations for 80% Coverage", f"{stations_80}")
    with col3:
        st.metric("Stations for 95% Coverage", f"{stations_95}")

    st.markdown("#### Interactive Cut-off Simulator")
    target_coverage = st.slider(
        "Target Route Coverage (%)",
        min_value=50,
        max_value=100,
        value=95,
        step=1,
    )

    required_stations = _required_items_for_coverage(stations_df, float(target_coverage))
    if baseline > 0:
        eliminated_pct = max(0.0, (1.0 - required_stations / baseline) * 100.0)
    else:
        eliminated_pct = 0.0

    st.markdown(
        f"To maintain **{target_coverage}%** of the elite routing structure, "
        f"you only need to keep **{required_stations}** stations. "
        f"This eliminates approximately **{eliminated_pct:0.1f}%** of the original graph."
    )


def render_elite_analysis(
    data_source: str,
    loaded_routes_data: Optional[Dict],
    where_clause: Optional[str],
    total_matching_routes: Optional[int],
) -> None:
    if data_source == "Live Checkpoints (JSON)":
        if not loaded_routes_data:
            st.info("No routes found in the selected checkpoint.")
            return

        routes = extract_routes_from_data(loaded_routes_data)
        if not routes:
            st.info("No routes found in the selected checkpoint.")
            return

        station_counter, edge_counter = compute_frequencies(routes)
        stations_df = build_station_dataframe(station_counter)
        edges_df = build_edge_dataframe(edge_counter)

        total_unique_stations = len(station_counter)
        total_unique_edges = len(edge_counter)

        render_header(total_unique_stations, total_unique_edges)
        render_pruning_section(stations_df, total_unique_stations)

        default_top_stations = min(50, len(stations_df) if not stations_df.empty else 50)
        default_top_edges = min(100, len(edges_df) if not edges_df.empty else 100)

        top_n_stations, top_n_edges = render_filters(
            default_top_stations=default_top_stations or 50,
            default_top_edges=default_top_edges or 100,
        )

        col_left, col_right = st.columns([2, 1])
        with col_left:
            render_station_section(stations_df, top_n_stations)
        with col_right:
            render_edge_section(edges_df, top_n_edges)

        render_long_tail_section(stations_df)
        return

    # Parquet-backed safe sampling for elite analysis
    if not where_clause:
        st.warning("No filters are defined for the Parquet-backed elite analysis.")
        return

    st.info(
        "Note: Frequency analysis is based on a sample of the top 10,000 "
        "filtered routes for performance."
    )

    sample_df = duckdb.query(
        f"SELECT * FROM '{PARQUET_PATH}' WHERE {where_clause} LIMIT 10000"
    ).df()

    if sample_df.empty:
        st.info("No routes available in the sampled subset for elite analysis.")
        return

    sampled_routes: List[List[Dict]] = []
    for _, row in sample_df.iterrows():
        route_raw = row.get("route_sequence_json")
        if isinstance(route_raw, str):
            try:
                route = json.loads(route_raw)
            except json.JSONDecodeError:
                continue
        else:
            route = route_raw

        if isinstance(route, list) and route:
            sampled_routes.append(route)

    if not sampled_routes:
        st.info("No valid routes could be parsed from the sampled subset.")
        return

    station_counter, edge_counter = compute_frequencies(sampled_routes)
    stations_df = build_station_dataframe(station_counter)
    edges_df = build_edge_dataframe(edge_counter)

    total_unique_stations = len(station_counter)
    total_unique_edges = len(edge_counter)

    render_header(total_unique_stations, total_unique_edges)
    render_pruning_section(stations_df, total_unique_stations)

    default_top_stations = min(50, len(stations_df) if not stations_df.empty else 50)
    default_top_edges = min(100, len(edges_df) if not edges_df.empty else 100)

    top_n_stations, top_n_edges = render_filters(
        default_top_stations=default_top_stations or 50,
        default_top_edges=default_top_edges or 100,
    )

    col_left, col_right = st.columns([2, 1])
    with col_left:
        render_station_section(stations_df, top_n_stations)
    with col_right:
        render_edge_section(edges_df, top_n_edges)

    render_long_tail_section(stations_df)


# ----------------------------
# Route Explorer (HTML visualizer ported to Streamlit)
# ----------------------------

def _get_unique_countries_from_route(route: list) -> list:
    """
    Extracts unique countries visited in a route, in order of visit.
    Strictly uses the destination of each segment, meaning the origin country
    of the very first leg is correctly excluded from the World Record count.
    """
    import re
    ordered_countries: list = []
    seen = set()
    country_regex = re.compile(r"\(([^)]+)\)")

    for segment in route:
        dest = segment.get("dest", "")
        dest_match = country_regex.search(dest)
        if dest_match:
            country = dest_match.group(1)
            if country not in seen:
                seen.add(country)
                ordered_countries.append(country)

    return ordered_countries


def _format_time(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _parse_hhmm_to_minutes(value: str) -> Optional[int]:
    """
    Parse a time string in HH:MM format into minutes since midnight.
    Returns None for invalid inputs.
    """
    if not value:
        return None

    raw = value.strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", raw)
    if not match:
        return None

    hours = int(match.group(1))
    mins = int(match.group(2))
    return hours * 60 + mins


def _build_route_path(route: Iterable[Dict]) -> str:
    """
    Build a compact path string like "VIE -> BRU -> CDG" from a route.
    """
    segments = list(route)
    if not segments:
        return ""

    codes: List[str] = []
    first_origin = extract_station_code(segments[0].get("origin", ""))
    if first_origin:
        codes.append(first_origin)

    for seg in segments:
        dest_code = extract_station_code(seg.get("dest", ""))
        if dest_code:
            codes.append(dest_code)

    # Deduplicate consecutive duplicates to keep the path clean.
    deduped: List[str] = []
    for code in codes:
        if not deduped or deduped[-1] != code:
            deduped.append(code)

    return " -> ".join(deduped)


def render_route_ranking(
    data_source: str,
    loaded_routes_data: Optional[Dict],
    where_clause: Optional[str],
    total_matching_routes: Optional[int],
) -> None:
    st.markdown(
        "<div class='elite-title'>📊 Route Ranking & Scoring</div>"
        "<div class='elite-subtitle'>Rank routes by robustness of transfers using the Route Quality Index (RQI)</div>",
        unsafe_allow_html=True,
    )

    lookup = load_schedule_lookup()

    if data_source == "Live Checkpoints (JSON)":
        if not loaded_routes_data:
            st.info("No routes found in the selected checkpoint.")
            return

        routes = extract_routes_from_data(loaded_routes_data)
        if not routes:
            st.info("No routes found in the selected checkpoint.")
            return

        ranked_rows: List[Dict] = []
        route_meta: List[Dict] = []

        for idx, route in enumerate(routes):
            score_result = scoring_engine.score_route(route)
            rqi = score_result.get("RQI")
            bottleneck = score_result.get("bottleneck") or {}

            countries = _get_unique_countries_from_route(route)
            country_count = len(countries)

            route_path = _build_route_path(route)
            bottleneck_label = bottleneck.get("label") or ""

            ranked_rows.append(
                {
                    "route_idx": idx,
                    "RQI Score": rqi,
                    "Country Count": country_count,
                    "Bottleneck Info": bottleneck_label,
                    "Route Path": route_path,
                }
            )

            route_meta.append(
                {
                    "route": route,
                    "countries": countries,
                    "country_count": country_count,
                    "segments": len(route),
                    "score_result": score_result,
                }
            )

        df = pd.DataFrame(ranked_rows)

        if df.empty or df["RQI Score"].isna().all():
            st.info("Unable to compute RQI scores for the available routes (missing timing data).")
            return

        df = df.sort_values("RQI Score", ascending=False, na_position="last").reset_index(drop=True)
        df.insert(0, "Rank", df.index + 1)

        any_station_filter = st.text_input(
            "Must Include Stations (comma-separated)",
            value="",
        ).strip()

        hide_miracles = st.checkbox(
            "Hide Miracle Routes (RQI < 0)",
            value=True,
            help="When checked, routes with negative RQI (heavily penalized connections) are hidden.",
        )

        if hide_miracles:
            filtered_df = df[(df["RQI Score"].notna()) & (df["RQI Score"] >= 0)]
        else:
            filtered_df = df[df["RQI Score"].notna()]

        if any_station_filter:
            stations_to_find = [s.strip().lower() for s in any_station_filter.split(",") if s.strip()]
            filtered_df = filtered_df[
                filtered_df["Route Path"].apply(
                    lambda path: all(s in str(path).lower() for s in stations_to_find)
                )
            ]

        if filtered_df.empty:
            st.info("No routes remain after applying the current filters.")
            return

        display_df = filtered_df[
            ["Rank", "RQI Score", "Country Count", "Bottleneck Info", "Route Path", "route_idx"]
        ].copy()

        display_df["RQI Score"] = display_df["RQI Score"].astype(float)

        # Keep route_idx for lookup but don't show it in the main table.
        pretty_df = display_df.drop(columns=["route_idx"])

        st.markdown("### Ranked Routes")
        st.dataframe(
            pretty_df.style.format({"RQI Score": "{:.1f}"}),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("### Route Details")

        # Start by showing the top 10 routes, allow loading more via a button.
        details_key = "route_ranking_details_count"
        if details_key not in st.session_state:
            st.session_state[details_key] = ROUTE_RANKING_INITIAL
        current_count = st.session_state[details_key]
        current_count = max(1, min(current_count, len(filtered_df)))
        st.session_state[details_key] = current_count

        st.write(f"Showing detailed view for top {current_count} of {len(filtered_df)} ranked routes.")

        detailed_df = filtered_df.head(current_count)

        for _, row in detailed_df.iterrows():
            route_idx = int(row["route_idx"])
            meta = route_meta[route_idx]
            route = meta["route"]
            countries = meta["countries"]
            segments_count = meta["segments"]
            rqi_value = row["RQI Score"]
            bottleneck_info = row["Bottleneck Info"]

            header = (
                f"Rank {int(row['Rank'])} · RQI {rqi_value:.1f} · "
                f"{meta['country_count']} countries · {segments_count} segments"
            )

            with st.expander(header):
                seg_df = build_route_dataframe(route, lookup)
                render_beautiful_route(seg_df)

                if countries:
                    st.markdown("**Countries visited:** " + ", ".join(countries))
                if bottleneck_info:
                    st.markdown(f"**Bottleneck:** {bottleneck_info}")

        # "Load more" button to reveal additional ranked routes, until exhausted.
        if current_count < len(filtered_df):
            if st.button("Load more ranked routes", key="route_ranking_load_more"):
                st.session_state[details_key] = min(
                    current_count + ROUTE_RANKING_STEP,
                    len(filtered_df),
                )
        return

    # Parquet-backed RQI ranking with DuckDB-native pagination
    if not where_clause:
        st.warning("No filters are defined for the Parquet-backed ranking.")
        return

    total = int(total_matching_routes or 0)
    if total == 0:
        st.info("No routes found in the master elite database for the selected filters.")
        return

    page_size = ROUTE_RANKING_STEP
    page_state_key = "parquet_route_ranking_page"
    if page_state_key not in st.session_state:
        st.session_state[page_state_key] = 0
    current_page = st.session_state[page_state_key]
    max_page = max(0, (total - 1) // page_size)
    current_page = max(0, min(current_page, max_page))
    st.session_state[page_state_key] = current_page

    offset = current_page * page_size

    query = (
        f"SELECT * FROM '{PARQUET_PATH}' "
        f"WHERE {where_clause} "
        f"ORDER BY rqi_score DESC, total_countries DESC "
        f"LIMIT {page_size} OFFSET {offset}"
    )
    page_df = duckdb.query(query).df()

    if page_df.empty:
        st.info("No routes available for the current ranking page.")
        return

    ranked_rows: List[Dict] = []
    routes_for_page: List[Dict] = []

    for _, row in page_df.iterrows():
        route_raw = row.get("route_sequence_json")
        if isinstance(route_raw, str):
            try:
                route = json.loads(route_raw)
            except json.JSONDecodeError:
                continue
        else:
            route = route_raw

        if not isinstance(route, list) or not route:
            continue

        countries = _get_unique_countries_from_route(route)
        country_count = len(countries)

        score_result = scoring_engine.score_route(route)
        bottleneck = score_result.get("bottleneck") or {}

        route_path = _build_route_path(route)
        bottleneck_label = bottleneck.get("label") or ""

        rqi = float(row.get("rqi_score", -999.0))

        routes_for_page.append(
            {
                "route": route,
                "countries": countries,
                "country_count": country_count,
                "segments": len(route),
                "score_result": score_result,
            }
        )

        ranked_rows.append(
            {
                "route_idx": len(routes_for_page) - 1,
                "RQI Score": rqi,
                "Country Count": country_count,
                "Bottleneck Info": bottleneck_label,
                "Route Path": route_path,
            }
        )

    if not ranked_rows:
        st.info("No valid routes could be scored on this page.")
        return

    df = pd.DataFrame(ranked_rows)
    if df.empty or df["RQI Score"].isna().all():
        st.info("Unable to compute RQI scores for the current page (missing timing data).")
        return

    global_rank_start = offset + 1
    df.insert(0, "Rank", range(global_rank_start, global_rank_start + len(df)))

    hide_miracles = st.checkbox(
        "Hide Miracle Routes (RQI < 0)",
        value=True,
        help="When checked, routes with negative RQI (heavily penalized connections) are hidden.",
        key="parquet_hide_miracles",
    )

    if hide_miracles:
        filtered_df = df[(df["RQI Score"].notna()) & (df["RQI Score"] >= 0)]
    else:
        filtered_df = df[df["RQI Score"].notna()]

    if filtered_df.empty:
        st.info("No routes remain after applying the current filters to this page.")
        return

    display_df = filtered_df[
        ["Rank", "RQI Score", "Country Count", "Bottleneck Info", "Route Path", "route_idx"]
    ].copy()

    display_df["RQI Score"] = display_df["RQI Score"].astype(float)
    pretty_df = display_df.drop(columns=["route_idx"])

    st.markdown(
        f"Showing page {current_page + 1} of {max_page + 1} "
        f"({len(filtered_df)} ranked routes on this page, {total:,} total matching routes)."
    )

    st.markdown("### Ranked Routes (Current Page)")
    st.dataframe(
        pretty_df.style.format({"RQI Score": "{:.1f}"}),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### Route Details (Current Page)")
    for _, row in filtered_df.iterrows():
        route_idx = int(row["route_idx"])
        if route_idx < 0 or route_idx >= len(routes_for_page):
            continue
        route_meta = routes_for_page[route_idx]
        route = route_meta["route"]
        countries = route_meta["countries"]
        segments_count = route_meta["segments"]
        rqi_value = row["RQI Score"]
        bottleneck_info = row["Bottleneck Info"]

        header = (
            f"Rank {int(row['Rank'])} · RQI {rqi_value:.1f} · "
            f"{route_meta['country_count']} countries · {segments_count} segments"
        )

        with st.expander(header):
            seg_df = build_route_dataframe(route, lookup)
            render_beautiful_route(seg_df)

            if countries:
                st.markdown("**Countries visited:** " + ", ".join(countries))
            if bottleneck_info:
                st.markdown(f"**Bottleneck:** {bottleneck_info}")

    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.button("Previous page", disabled=current_page == 0, key="parquet_ranking_prev"):
            st.session_state[page_state_key] = max(0, current_page - 1)
    with col_next:
        if st.button("Next page", disabled=current_page >= max_page, key="parquet_ranking_next"):
            st.session_state[page_state_key] = min(max_page, current_page + 1)


def render_route_explorer(
    data_source: str,
    loaded_routes_data: Optional[Dict],
    where_clause: Optional[str],
    total_matching_routes: Optional[int],
) -> None:
    st.markdown(
        "<div class='elite-title'>🚀 Route Planner Explorer</div>"
        "<div class='elite-subtitle'>Browse the best routes and filter by countries visited and starting node</div>",
        unsafe_allow_html=True,
    )

    # Deterministic schedule lookup for display times
    lookup = load_schedule_lookup()

    if data_source == "Live Checkpoints (JSON)":
        if not loaded_routes_data:
            st.info("No routes found in the selected checkpoint.")
            return

        routes_by_score = loaded_routes_data.get("routes", {}) or {}
        all_routes: List[Dict] = []
        country_counts = set()
        start_nodes = set()
        start_times = set()

        for _score, routes in routes_by_score.items():
            if not isinstance(routes, Iterable):
                continue
            for route in routes:
                if route and isinstance(route, Iterable):
                    countries = _get_unique_countries_from_route(route)
                    start_node = route[0].get("origin", "") if route else ""
                    start_dep = route[0].get("dep_time") if route else None
                    start_time_mins: Optional[int] = None
                    start_time_label = ""
                    if start_dep is not None:
                        try:
                            start_time_mins = int(start_dep) % 1440
                            start_time_label = _format_time(start_time_mins)
                        except (TypeError, ValueError):
                            start_time_mins = None
                            start_time_label = ""
                    country_count = len(countries)
                    if country_count:
                        country_counts.add(country_count)
                    if start_node:
                        start_nodes.add(start_node)
                    if start_time_label:
                        start_times.add(start_time_label)
                    all_routes.append(
                        {
                            "route": route,
                            "country_count": country_count,
                            "countries": countries,
                            "start_node": start_node,
                            "start_time_mins": start_time_mins,
                            "start_time_label": start_time_label,
                            "segments": len(route),
                        }
                    )

        if not all_routes:
            st.info("No routes found in the selected checkpoint.")
            return

        all_routes.sort(key=lambda r: (-r["country_count"], -r["segments"]))

        nodes_explored = loaded_routes_data.get("nodes_explored")
        max_countries = max(r["country_count"] for r in all_routes)

        # Blue stats boxes for clear, high-contrast display
        total_routes_text = f"{len(all_routes):,}"
        max_countries_text = f"{max_countries}"
        nodes_explored_text = (
            f"{int(nodes_explored):,}" if isinstance(nodes_explored, (int, float)) else "N/A"
        )

        st.markdown(
            f"""
            <div class="elite-stats-card">
              <div class="elite-stats-row">
                <div class="elite-stat-item">
                  <div class="elite-stat-value">{total_routes_text}</div>
                  <div class="elite-stat-label">Total Routes</div>
                </div>
                <div class="elite-stat-item">
                  <div class="elite-stat-value">{max_countries_text}</div>
                  <div class="elite-stat-label">Max Countries in a Route</div>
                </div>
                <div class="elite-stat-item">
                  <div class="elite-stat-value">{nodes_explored_text}</div>
                  <div class="elite-stat-label">Nodes Explored</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")

        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1:
            country_options = sorted(country_counts, reverse=True)
            country_filter = st.selectbox(
                "Filter by number of countries",
                options=["All"] + [str(c) for c in country_options],
                index=0,
            )
        with col_f2:
            start_node_options = sorted(start_nodes)
            start_filter = st.selectbox(
                "Filter by starting node",
                options=["All"] + start_node_options,
                index=0,
            )
        with col_f3:
            start_time_options = sorted(start_times)
            start_time_filter = st.selectbox(
                "Filter by starting time",
                options=["All"] + start_time_options,
                index=0,
            )
        with col_f4:
            any_station_filter = st.text_input(
                "Must Include Stations (comma-separated, e.g., VIE, WAW)",
                value="",
            ).strip()

        filtered = all_routes
        if country_filter != "All":
            target = int(country_filter)
            filtered = [r for r in filtered if r["country_count"] == target]
        if start_filter != "All":
            filtered = [r for r in filtered if r["start_node"] == start_filter]
        if start_time_filter != "All":
            filtered = [r for r in filtered if r["start_time_label"] == start_time_filter]
        if any_station_filter:
            stations_to_find = [s.strip().lower() for s in any_station_filter.split(",") if s.strip()]
            filtered = [
                r
                for r in filtered 
                if all(s in str(r["route"]).lower() for s in stations_to_find)
            ]

        if not filtered:
            st.info("No routes match the selected filters.")
            return

        # Paginate explorer results: start at 20 and allow loading more
        explorer_filters_key = "route_explorer_filters"
        explorer_count_key = "route_explorer_display_count"

        current_filters = {
            "country_filter": country_filter,
            "start_filter": start_filter,
            "start_time_filter": start_time_filter,
        }
        previous_filters = st.session_state.get(explorer_filters_key)

        # Reset pagination when filters change
        if previous_filters != current_filters:
            st.session_state[explorer_filters_key] = current_filters
            st.session_state[explorer_count_key] = ROUTE_EXPLORER_INITIAL

        max_display = st.session_state.get(explorer_count_key, ROUTE_EXPLORER_INITIAL)
        max_display = max(1, min(max_display, len(filtered)))
        st.session_state[explorer_count_key] = max_display

        st.write(f"Showing {max_display} of {len(filtered)} matching routes.")

        for idx, route_info in enumerate(filtered[:max_display], start=1):
            with st.expander(
                f"Route #{idx} · {route_info['country_count']} countries · {route_info['segments']} segments"
            ):
                segments = route_info["route"]
                seg_df = build_route_dataframe(segments, lookup)
                render_beautiful_route(seg_df)

                st.markdown("**Countries visited:** " + ", ".join(route_info["countries"]))

        # "Load more" button to show additional routes until all are visible.
        if max_display < len(filtered):
            if st.button("Load more routes", key="route_explorer_load_more"):
                st.session_state[explorer_count_key] = min(
                    max_display + ROUTE_EXPLORER_STEP,
                    len(filtered),
                )
        return

    # Parquet-backed lazy page rendering
    if not where_clause:
        st.warning("No filters are defined for the Parquet-backed explorer.")
        return

    total = int(total_matching_routes or 0)
    if total == 0:
        st.info("No routes found in the master elite database for the selected filters.")
        return

    page_size = ROUTE_EXPLORER_STEP
    page_state_key = "parquet_route_explorer_page"
    if page_state_key not in st.session_state:
        st.session_state[page_state_key] = 0
    current_page = st.session_state[page_state_key]
    max_page = max(0, (total - 1) // page_size)
    current_page = max(0, min(current_page, max_page))
    st.session_state[page_state_key] = current_page

    offset = current_page * page_size

    # Stats: use total_matching_routes and cached parquet_route_metrics for max_countries
    _, max_countries = parquet_route_metrics(where_clause)
    total_routes_text = f"{total:,}"
    max_countries_text = f"{max_countries}"
    nodes_explored_text = "N/A"

    st.markdown(
        f"""
        <div class="elite-stats-card">
          <div class="elite-stats-row">
            <div class="elite-stat-item">
              <div class="elite-stat-value">{total_routes_text}</div>
              <div class="elite-stat-label">Total Routes (Filtered)</div>
            </div>
            <div class="elite-stat-item">
              <div class="elite-stat-value">{max_countries_text}</div>
              <div class="elite-stat-label">Max Countries in a Route</div>
            </div>
            <div class="elite-stat-item">
              <div class="elite-stat-value">{nodes_explored_text}</div>
              <div class="elite-stat-label">Nodes Explored</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    query = (
        f"SELECT * FROM '{PARQUET_PATH}' "
        f"WHERE {where_clause} "
        f"ORDER BY total_countries DESC "
        f"LIMIT {page_size} OFFSET {offset}"
    )
    page_df = duckdb.query(query).df()

    if page_df.empty:
        st.info("No routes available for the current explorer page.")
        return

    routes_for_page: List[Dict] = []

    for _, row in page_df.iterrows():
        route_raw = row.get("route_sequence_json")
        if isinstance(route_raw, str):
            try:
                route = json.loads(route_raw)
            except json.JSONDecodeError:
                continue
        else:
            route = route_raw

        if not isinstance(route, list) or not route:
            continue

        countries = _get_unique_countries_from_route(route)
        country_count = len(countries)
        start_node = route[0].get("origin", "") if route else ""

        routes_for_page.append(
            {
                "route": route,
                "country_count": country_count,
                "countries": countries,
                "start_node": start_node,
                "segments": len(route),
            }
        )

    if not routes_for_page:
        st.info("No valid routes could be parsed on this page.")
        return

    st.write(
        f"Showing page {current_page + 1} of {max_page + 1} "
        f"({len(routes_for_page)} routes on this page, {total:,} total matching routes)."
    )

    for idx, route_info in enumerate(routes_for_page, start=1):
        with st.expander(
            f"Route (page) #{idx} · {route_info['country_count']} countries · {route_info['segments']} segments"
        ):
            segments = route_info["route"]
            seg_df = build_route_dataframe(segments, lookup)
            render_beautiful_route(seg_df)

            st.markdown("**Countries visited:** " + ", ".join(route_info["countries"]))

    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.button("Previous page", disabled=current_page == 0, key="parquet_explorer_prev"):
            st.session_state[page_state_key] = max(0, current_page - 1)
    with col_next:
        if st.button("Next page", disabled=current_page >= max_page, key="parquet_explorer_next"):
            st.session_state[page_state_key] = min(max_page, current_page + 1)


# ----------------------------
# App entrypoint: file selection + pages
# ----------------------------

def main() -> None:
    st.set_page_config(
        page_title="Route Planner Analysis",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_custom_css()

    data_source = st.sidebar.radio(
        "Select Data Source",
        options=["Live Checkpoints (JSON)", "Master Elite Database (Parquet)"],
        index=0,
    )

    loaded_routes_data: Optional[Dict] = None
    current_label: str = ""
    where_clause: Optional[str] = None
    total_matching_routes: Optional[int] = None

    if data_source == "Live Checkpoints (JSON)":
        base_dir = Path(__file__).parent
        results_dir = base_dir / "results"

        st.sidebar.markdown("### Checkpoint source")

        available_files = sorted(results_dir.glob("*.json")) if results_dir.exists() else []
        source_mode = st.sidebar.radio(
            "Select source",
            options=["From results/ folder", "Upload JSON file"],
            index=0 if available_files else 1,
        )

        raw_data: Optional[Dict] = None

        if source_mode == "From results/ folder":
            if not available_files:
                st.sidebar.info("No JSON files found in results/. Please upload a file instead.")
            else:
                labels = [p.name for p in available_files]
                selected_label = st.sidebar.selectbox("Checkpoint file", labels, index=0)
                checkpoint_path = available_files[labels.index(selected_label)]
                current_label = checkpoint_path.name
                try:
                    with checkpoint_path.open("r", encoding="utf-8") as f:
                        raw_data = json.load(f)
                except FileNotFoundError as e:
                    st.error(str(e))
                    st.stop()
                except json.JSONDecodeError:
                    st.error(f"Failed to parse JSON from {checkpoint_path}.")
                    st.stop()
        else:
            uploaded = st.sidebar.file_uploader("Upload checkpoint JSON", type=["json"])
            if uploaded is not None:
                current_label = uploaded.name
                try:
                    file_bytes = uploaded.read()
                    raw_data = json.loads(file_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    st.error("Failed to parse uploaded JSON file.")
                    st.stop()

        if raw_data is None:
            st.warning("Please select or upload a checkpoint JSON file to begin.")
            st.stop()

        loaded_routes_data = raw_data

    elif data_source == "Master Elite Database (Parquet)":
        # Build WHERE clause based on global metadata-driven dropdown filters
        metadata = get_parquet_metadata(PARQUET_PATH)

        origin_filter = st.sidebar.selectbox(
            "Filter by starting node",
            options=["All"] + metadata.get("unique_origins", []),
        )

        start_time_filter_raw = st.sidebar.text_input(
            "Filter by starting time (HH:MM, 24h)",
            value="",
            help="Example: 07:30. Applied to the first segment departure time.",
        ).strip()
        start_time_filter_mins = _parse_hhmm_to_minutes(start_time_filter_raw)
        if start_time_filter_raw and start_time_filter_mins is None:
            st.sidebar.warning("Invalid start time format. Use HH:MM (24h), e.g. 07:30.")

        country_options = list(range(metadata.get("global_max_countries", 0), 13, -1))
        country_filter = st.sidebar.selectbox(
            "Filter by number of countries",
            options=["All"] + [str(c) for c in country_options],
        )

        any_station_filter = st.sidebar.text_input(
            "Must Include Stations (comma-separated, e.g., VIE, WAW)",
            value="",
        ).strip()

        where_clauses = []
        if country_filter != "All":
            where_clauses.append(f"total_countries = {int(country_filter)}")
        if origin_filter != "All":
            safe_origin = origin_filter.replace("'", "''")
            where_clauses.append(f"start_node = '{safe_origin}'")
        if start_time_filter_mins is not None:
            where_clauses.append(
                "TRY_CAST(json_extract_string(route_sequence_json, '$[0].dep_time') AS BIGINT) % 1440 "
                f"= {start_time_filter_mins}"
            )
        if any_station_filter:
            stations_to_find = [s.strip() for s in any_station_filter.split(",") if s.strip()]
            for s in stations_to_find:
                safe_station = s.replace("'", "''")
                # Because route_sequence_json is a string in the DB, we can use a fast ILIKE query (case-insensitive)
                where_clauses.append(f"route_sequence_json ILIKE '%{safe_station}%'")

        if where_clauses:
            where_clause = " AND ".join(where_clauses)
        else:
            where_clause = "1=1"

        total_matching_routes, max_countries = parquet_route_metrics(where_clause)
        if total_matching_routes == 0:
            st.warning("No routes found in the master elite database for the selected filters.")
            st.stop()

        # Do not eagerly materialize routes; rely on lazy DuckDB-backed queries per view.
        loaded_routes_data = None
        current_label = (
            f"Master Elite Database (Parquet) · {total_matching_routes:,} routes, "
            f"max {max_countries} countries"
        )

    page = st.sidebar.radio(
        "View",
        options=["Route Explorer", "Elite Analysis", "Route Ranking"],
        index=0,
    )

    if current_label:
        st.sidebar.markdown(f"**Active checkpoint:** `{current_label}`")

    if page == "Route Explorer":
        render_route_explorer(data_source, loaded_routes_data, where_clause, total_matching_routes)
    elif page == "Elite Analysis":
        render_elite_analysis(data_source, loaded_routes_data, where_clause, total_matching_routes)
    else:
        render_route_ranking(data_source, loaded_routes_data, where_clause, total_matching_routes)


if __name__ == "__main__":
    main()