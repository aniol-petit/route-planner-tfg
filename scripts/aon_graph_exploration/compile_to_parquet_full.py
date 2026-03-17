import json
import hashlib
import copy
import pickle
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import tqdm
import scoring_engine


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def build_schema() -> pa.schema:
    return pa.schema(
        [
            pa.field("start_node", pa.string()),
            pa.field("end_node", pa.string()),
            pa.field("total_countries", pa.int64()),
            pa.field("total_legs", pa.int64()),
            pa.field("route_length_key", pa.int64()),
            pa.field("duration_mins", pa.int64()),
            pa.field("route_sequence_json", pa.string()),
            pa.field("rqi_score", pa.float64()),
        ]
    )


def load_train_schedules():
    graph_path = PROJECT_ROOT / "graph" / "transportation_graph.gpickle"
    train_schedules = {}
    try:
        with open(graph_path, "rb") as f:
            G = pickle.load(f)
        for u, v, key, data in G.edges(keys=True, data=True):
            if data.get("edge_type") == "train":
                u_code = scoring_engine.extract_station_code(u)
                v_code = scoring_engine.extract_station_code(v)
                if (u_code, v_code) not in train_schedules:
                    train_schedules[(u_code, v_code)] = []
                for dep, arr in data.get("time_pairs", []):
                    train_schedules[(u_code, v_code)].append((int(dep), int(arr)))
    except Exception as e:  # pragma: no cover - best-effort loader
        print(f"Warning: Could not load train schedules: {e}")
    return train_schedules


def optimize_and_score_route(route_sequence, train_schedules, transfer_times):
    current_route = copy.deepcopy(route_sequence)

    # First, get the baseline score
    best_score_result = scoring_engine.score_route(current_route)
    best_rqi = best_score_result.get("RQI", -999.0)
    if best_rqi is None:
        best_rqi = -999.0

    # Iterate through segments to find trains we can slide
    for i in range(1, len(current_route) - 1):
        raw_u = current_route[i].get("origin", "")
        raw_v = current_route[i].get("dest", "")
        u = scoring_engine.extract_station_code(raw_u)
        v = scoring_engine.extract_station_code(raw_v)

        # If it's a train route we have schedules for
        if (u, v) in train_schedules:
            # Constrained by PREVIOUS arrival
            prev_v = scoring_engine.extract_station_code(
                current_route[i - 1].get("dest", "")
            )
            transit_in = (
                transfer_times.get(prev_v, {}).get(u, 60) if prev_v != u else 0
            )
            min_dep = int(current_route[i - 1].get("arr_time")) + transit_in

            # Constrained by NEXT departure
            next_u = scoring_engine.extract_station_code(
                current_route[i + 1].get("origin", "")
            )
            transit_out = (
                transfer_times.get(v, {}).get(next_u, 60) if v != next_u else 0
            )
            max_arr = int(current_route[i + 1].get("dep_time")) - transit_out

            # Find valid trains
            valid_trains = [
                t
                for t in train_schedules[(u, v)]
                if t[0] >= min_dep and t[1] <= max_arr
            ]

            # Test valid trains to find the highest RQI
            for train_dep, train_arr in valid_trains:
                test_route = copy.deepcopy(current_route)
                test_route[i]["dep_time"] = train_dep
                test_route[i]["arr_time"] = train_arr

                test_score_result = scoring_engine.score_route(test_route)
                test_rqi = test_score_result.get("RQI", -999.0)
                if test_rqi is None:
                    test_rqi = -999.0

                if test_rqi > best_rqi:
                    best_rqi = test_rqi
                    best_score_result = test_score_result
                    current_route = test_route

    return current_route, best_score_result, best_rqi


def route_to_record(route, length_key: int) -> dict:
    if not route:
        raise ValueError("Encountered empty route.")

    first_leg = route[0]
    last_leg = route[-1]

    start_node = first_leg.get("origin")
    end_node = last_leg.get("dest")

    dep_time = first_leg.get("dep_time")
    arr_time = last_leg.get("arr_time")

    duration_mins = None
    if dep_time is not None and arr_time is not None:
        try:
            duration_mins = int(arr_time) - int(dep_time)
        except (TypeError, ValueError):
            duration_mins = None

    total_legs = len(route)
    total_countries = int(length_key)

    return {
        "start_node": start_node,
        "end_node": end_node,
        "total_countries": total_countries,
        "total_legs": total_legs,
        "route_length_key": int(length_key),
        "duration_mins": duration_mins,
        "route_sequence_json": json.dumps(route, separators=(",", ":")),
    }


def build_route_hash(route) -> str:
    if not route:
        return ""
    
    path_elements = []
    for leg in route:
        origin = leg.get("origin", "")
        dest = leg.get("dest", "")
        dep = leg.get("dep_time", "")
        arr = leg.get("arr_time", "")
        path_elements.append(f"{origin}_{dep}_{dest}_{arr}")
        
    path_string = "|".join(path_elements)
    return hashlib.md5(path_string.encode("utf-8")).hexdigest()


def compile_to_parquet() -> None:
    script_dir = PROJECT_ROOT / "scripts" / "aon_graph_exploration"
    input_dir = script_dir / "results_distributed_full"
    output_dir = script_dir / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "master_elite_routes_full.parquet"

    json_files = sorted(input_dir.glob("routes_part_*.jsonl"))
    if not json_files:
        print(f"No checkpoint files found in {input_dir}")
        return

    schema = build_schema()
    seen_route_hashes = set()
    total_files_processed = 0
    total_duplicates_skipped = 0
    total_unique_routes_written = 0

    batch_records = []
    batch_size = 50_000

    train_schedules = load_train_schedules()
    transfer_times = scoring_engine.load_transfer_durations()

    with pq.ParquetWriter(
        str(output_path), schema=schema, compression="snappy"
    ) as writer:
        for json_file in tqdm.tqdm(json_files, desc="Processing checkpoint files"):
            total_files_processed += 1
            with json_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    data = json.loads(line)
                    length_key = int(data.get("score", 0))
                    route = data.get("route", [])
                    
                    if length_key < 14 or not route:
                        continue

                    # 1. Optimize the route first
                    optimized_route, score_result, rqi = optimize_and_score_route(
                        route, train_schedules, transfer_times
                    )

                    # 2. Hash the OPTIMIZED route to ensure unique final schedules are kept
                    route_hash = build_route_hash(optimized_route)
                    if not route_hash:
                        continue

                    if route_hash in seen_route_hashes:
                        total_duplicates_skipped += 1
                        continue

                    seen_route_hashes.add(route_hash)

                    # 3. Build the record using the optimized route
                    record = route_to_record(optimized_route, length_key)
                    record["rqi_score"] = float(rqi)
                    
                    batch_records.append(record)
                    total_unique_routes_written += 1

                    if len(batch_records) >= batch_size:
                        df = pd.DataFrame(batch_records)
                        if "rqi_score" in df.columns:
                            df["rqi_score"] = df["rqi_score"].astype(float)
                        table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
                        writer.write_table(table)
                        batch_records.clear()

        if batch_records:
            df = pd.DataFrame(batch_records)
            if "rqi_score" in df.columns:
                df["rqi_score"] = df["rqi_score"].astype(float)
            table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
            writer.write_table(table)
            batch_records.clear()

    file_size_mb = output_path.stat().st_size / (1024 * 1024)

    print("Finished compiling routes to Parquet.")
    print(f"Total files processed: {total_files_processed}")
    print(f"Total duplicate routes skipped: {total_duplicates_skipped}")
    print(f"Total unique 14+ country routes written: {total_unique_routes_written}")
    print(f"Output Parquet file: {output_path}")
    print(f"Final Parquet file size: {file_size_mb:.2f} MB")


if __name__ == "__main__":
    compile_to_parquet()

