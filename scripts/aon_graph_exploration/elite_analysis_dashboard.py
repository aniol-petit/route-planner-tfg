import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots
import plotly.graph_objects as go


TOTAL_BASELINE_STATIONS = 899


def extract_station_code(station_label: str) -> str:
    """
    Extract the 3-letter station code from strings like:
    - "SVO (Russian Federation)" -> "SVO"
    - "SVO - Moscow (Russian Federation)" -> "SVO"
    Fallback: first 3 consecutive uppercase letters.
    """
    if not station_label:
        return ""

    # Prefer the first token if it looks like a code
    first_token = station_label.split()[0]
    if len(first_token) == 3 and first_token.isalnum():
        return first_token

    # Fallback regex: first 3+ uppercase letters/digits
    match = re.search(r"\b([A-Z0-9]{3,4})\b", station_label)
    if match:
        return match.group(1)

    return station_label.strip()


def load_routes_from_checkpoint(json_path: Path) -> List[List[Dict]]:
    """
    Load all routes from the live checkpoint JSON.

    Expected structure:
    {
        "routes": {
            "15": [ [segment, segment, ...], ... ],
            "14": [ ... ]
        },
        ...
    }
    """
    if not json_path.exists():
        raise FileNotFoundError(f"Checkpoint file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

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
    Compute station and edge frequencies from a collection of routes.

    Station frequency:
        Count every time a station appears as origin or destination.

    Edge frequency:
        Count every time a directed edge (origin -> destination) occurs,
        ignoring specific departure/arrival times.
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
        # Cumulative Pareto math
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
        # Cumulative Pareto math for edges
        df["Cumulative Count"] = df["Count"].cumsum()
        total_occurrences = df["Count"].sum()
        if total_occurrences > 0:
            df["Cumulative %"] = df["Cumulative Count"] / total_occurrences * 100.0
        else:
            df["Cumulative %"] = 0.0
    return df


def inject_custom_css() -> None:
    """
    Inject CSS to mimic the look & feel of route_visualizer.html:
    - Dark blue/purple gradient background
    - Neon cyan accents
    - Glassmorphism cards
    """
    st.markdown(
        """
        <style>
        /* Global background & typography */
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
        }

        h1, h2, h3 {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }

        /* Gradient title, mimicking the original header */
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

        /* Stats container, similar to .stats in the HTML */
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

        /* Filter section styling */
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

        /* Dataframe tweaks */
        .stDataFrame, .stTable {
            border-radius: 10px;
            overflow: hidden;
        }

        /* Plotly background match */
        .js-plotly-plot .plotly .main-svg {
            background-color: rgba(0, 0, 0, 0) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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

        # Close the filters card wrapper
        st.markdown("</div>", unsafe_allow_html=True)

    return top_n_stations, top_n_edges


def render_station_section(stations_df: pd.DataFrame, top_n: int) -> None:
    st.markdown("### Top Stations by Usage (Pareto Analysis)")

    if stations_df.empty:
        st.info("No station data available from the current checkpoint.")
        return

    top_df = stations_df.head(top_n)

    # Dual-axis Pareto chart: bars for absolute count, line for cumulative %
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
    """
    Build a rank-annotated dataframe of all stations that appear in the
    checkpoint, sorted by frequency descending.
    """
    if stations_df.empty:
        return stations_df.copy()

    df = stations_df.copy()
    df = df.sort_values("Count", ascending=False).reset_index(drop=True)
    df["Rank"] = df.index + 1
    # Keep useful columns in a predictable order
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

    # Compute cutoff rank (first rank where frequency drops below X)
    below_mask = long_tail_df["Count"] < threshold
    if below_mask.any():
        first_below_rank = int(long_tail_df.loc[below_mask, "Rank"].min())
        cutoff_rank_text = (
            f"Stations with fewer than **{threshold}** appearances start at **Rank {first_below_rank}**."
        )
    else:
        first_below_rank = None
        cutoff_rank_text = (
            f"No stations fall below **{threshold}** appearances in the observed elite routes "
            f"(all {len(long_tail_df)} observed stations meet or exceed this threshold)."
        )

    # How many stations remain if we keep only those with >= X appearances?
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

    # Long-tail chart: Rank vs Frequency
    st.markdown("#### Long-Tail Frequency Curve")

    fig_lt = px.line(
        long_tail_df,
        x="Rank",
        y="Count",
        title="Station Frequency vs. Rank (Long Tail)",
    )
    fig_lt.update_traces(line=dict(color="#00d4ff"))

    # Horizontal reference line at the chosen cutoff X
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

    # Full station table for manual inspection
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
    """
    Helper to compute how many rows (stations/edges) are needed
    to reach a given cumulative coverage percentage.
    """
    if df.empty or "Cumulative %" not in df.columns:
        return 0

    # Ensure sorted by descending frequency / ascending cumulative
    cumulative = df["Cumulative %"]
    mask = cumulative >= coverage
    if not mask.any():
        return int(len(df))

    first_idx = mask.idxmax()
    # DataFrame is reset_index'ed so idx is positional
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
        st.metric(
            "Stations for 80% Coverage",
            f"{stations_80}",
        )
    with col3:
        st.metric(
            "Stations for 95% Coverage",
            f"{stations_95}",
        )

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


def main() -> None:
    st.set_page_config(
        page_title="Elite Network Frequency Analysis",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_custom_css()

    base_dir = Path(__file__).parent
    checkpoint_path = base_dir / "results" / "live_checkpoint.json"

    try:
        routes = load_routes_from_checkpoint(checkpoint_path)
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()
    except json.JSONDecodeError:
        st.error("Failed to parse JSON from results/live_checkpoint.json.")
        st.stop()

    station_counter, edge_counter = compute_frequencies(routes)
    stations_df = build_station_dataframe(station_counter)
    edges_df = build_edge_dataframe(edge_counter)

    total_unique_stations = len(station_counter)
    total_unique_edges = len(edge_counter)

    render_header(total_unique_stations, total_unique_edges)
    render_pruning_section(stations_df, total_unique_stations)

    # Sensible defaults
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

    # Long-tail analysis below the main Pareto view
    render_long_tail_section(stations_df)


if __name__ == "__main__":
    main()

