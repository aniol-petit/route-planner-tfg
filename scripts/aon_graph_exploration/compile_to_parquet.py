import json
import hashlib
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

    # Deterministic path string based on sequence of nodes visited.
    nodes = []
    first_leg = route[0]
    origin = first_leg.get("origin")
    if origin is not None:
        nodes.append(str(origin))

    for leg in route:
        dest = leg.get("dest")
        if dest is not None:
            nodes.append(str(dest))

    path_string = " -> ".join(nodes)
    return hashlib.md5(path_string.encode("utf-8")).hexdigest()


def compile_to_parquet() -> None:
    script_dir = PROJECT_ROOT / "scripts" / "aon_graph_exploration"
    input_dir = script_dir / "results_distributed"
    output_dir = script_dir / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "master_elite_routes.parquet"

    json_files = sorted(input_dir.glob("checkpoint_part_*.json"))
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

    with pq.ParquetWriter(str(output_path), schema=schema, compression="snappy") as writer:
        for json_file in tqdm.tqdm(json_files, desc="Processing checkpoint files"):
            total_files_processed += 1
            with json_file.open("r", encoding="utf-8") as f:
                data = json.load(f)

            routes_by_length = data.get("routes", {})
            for length_key_str, routes_list in routes_by_length.items():
                try:
                    length_key = int(length_key_str)
                except (TypeError, ValueError):
                    continue

                if length_key < 14:
                    continue

                for route in routes_list:
                    route_hash = build_route_hash(route)
                    if not route_hash:
                        continue

                    if route_hash in seen_route_hashes:
                        total_duplicates_skipped += 1
                        continue

                    seen_route_hashes.add(route_hash)

                    # Compute RQI score for this route.
                    route_sequence = route
                    score_result = scoring_engine.score_route(route_sequence)
                    rqi = float(score_result.get("RQI", -999.0))

                    record = route_to_record(route, length_key)
                    record["rqi_score"] = rqi
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

