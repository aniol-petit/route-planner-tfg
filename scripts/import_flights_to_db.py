"""
Import flights data from JSON files into the SQLite database.

This script reads JSON files from the new folder structure:
    data/flight_data/{date}/{airport_code}.json

And imports the flight data into the database.

Usage:
    python scripts/import_flights_to_db.py [json_file_path]
    
    If no file path is provided, it will look for all JSON files
    in the data/flight_data/{date}/ folders.
    
    You can also specify a specific date folder or file:
    python scripts/import_flights_to_db.py --date 2026-01-22
    python scripts/import_flights_to_db.py data/flight_data/2026-01-22/BCN.json
"""

import json
import sqlite3
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
FLIGHT_DATA_DIR = DATA_DIR / "flight_data"
DB_PATH = DATA_DIR / "flights.db"

# ============================================================================
# DATA PROCESSING
# ============================================================================

def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """
    Parse datetime string from API format (e.g., "2026-01-22t01:30:00.000").
    
    Args:
        dt_str: Datetime string in API format
        
    Returns:
        datetime object or None if parsing fails
    """
    if not dt_str:
        return None
    
    try:
        # Remove milliseconds and 't' separator, normalize format
        dt_str_clean = dt_str.replace('t', ' ').replace('T', ' ')
        if '.' in dt_str_clean:
            dt_str_clean = dt_str_clean.split('.')[0]
        
        return datetime.strptime(dt_str_clean, "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return None


def calculate_delay_minutes(scheduled: Optional[datetime], estimated: Optional[datetime]) -> Optional[int]:
    """
    Calculate delay in minutes between scheduled and estimated times.
    
    Args:
        scheduled: Scheduled departure time
        estimated: Estimated departure time
        
    Returns:
        Delay in minutes, or None if either time is missing
    """
    if not scheduled or not estimated:
        return None
    
    delta = estimated - scheduled
    return int(delta.total_seconds() / 60)


def extract_flight_data(flight: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract essential flight data from API response format.
    
    Args:
        flight: Flight data from API response
        
    Returns:
        Dictionary with extracted fields, or None if required data is missing
    """
    try:
        # Extract flight number
        flight_number = None
        if "flight" in flight:
            flight_number = flight["flight"].get("iataNumber") or flight["flight"].get("number")
        
        if not flight_number:
            return None
        
        # Extract airports
        departure_airport = flight.get("departure", {}).get("iataCode", "").upper()
        arrival_airport = flight.get("arrival", {}).get("iataCode", "").upper()
        
        if not departure_airport or not arrival_airport:
            return None
        
        # Extract times
        dep_scheduled_str = flight.get("departure", {}).get("scheduledTime")
        dep_estimated_str = flight.get("departure", {}).get("estimatedTime")
        arr_scheduled_str = flight.get("arrival", {}).get("scheduledTime")
        
        dep_scheduled = parse_datetime(dep_scheduled_str)
        dep_estimated = parse_datetime(dep_estimated_str)
        arr_scheduled = parse_datetime(arr_scheduled_str)
        
        if not dep_scheduled:
            return None
        
        # Calculate delay
        delay_minutes = calculate_delay_minutes(dep_scheduled, dep_estimated)
        
        # Extract date (YYYY-MM-DD)
        date = dep_scheduled.strftime("%Y-%m-%d")
        
        # Extract status
        status = flight.get("status", "")
        
        return {
            "flight_number": flight_number.upper(),
            "departure_airport": departure_airport,
            "arrival_airport": arrival_airport,
            "departure_scheduled": dep_scheduled_str,
            "departure_estimated": dep_estimated_str,
            "departure_delay_minutes": delay_minutes,
            "arrival_scheduled": arr_scheduled_str,
            "status": status,
            "date": date
        }
    except (KeyError, AttributeError, ValueError) as e:
        print(f"  ⚠ Error extracting flight data: {e}")
        return None


def import_flights_from_json(json_file: Path, conn: sqlite3.Connection) -> int:
    """
    Import flights from a JSON file into the database.
    
    Args:
        json_file: Path to JSON file
        conn: Database connection
        
    Returns:
        Number of flights imported
    """
    print(f"\nProcessing: {json_file.name}")
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            flights_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"  ✗ Error reading JSON file: {e}")
        return 0
    except FileNotFoundError:
        print(f"  ✗ File not found: {json_file}")
        return 0
    
    if not isinstance(flights_data, list):
        print(f"  ✗ Expected list of flights, got {type(flights_data)}")
        return 0
    
    cursor = conn.cursor()
    imported = 0
    skipped = 0
    errors = 0
    
    insert_sql = """
    INSERT OR IGNORE INTO flights (
        flight_number, departure_airport, arrival_airport,
        departure_scheduled, departure_estimated, departure_delay_minutes,
        arrival_scheduled, status, date
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    for flight in flights_data:
        flight_data = extract_flight_data(flight)
        
        if not flight_data:
            skipped += 1
            continue
        
        try:
            cursor.execute(insert_sql, (
                flight_data["flight_number"],
                flight_data["departure_airport"],
                flight_data["arrival_airport"],
                flight_data["departure_scheduled"],
                flight_data["departure_estimated"],
                flight_data["departure_delay_minutes"],
                flight_data["arrival_scheduled"],
                flight_data["status"],
                flight_data["date"]
            ))
            imported += 1
        except sqlite3.Error as e:
            errors += 1
            if errors <= 5:  # Only show first 5 errors
                print(f"  ⚠ Database error: {e}")
    
    conn.commit()
    
    print(f"  ✓ Imported: {imported}")
    if skipped > 0:
        print(f"  ⚠ Skipped (missing data): {skipped}")
    if errors > 0:
        print(f"  ✗ Errors: {errors}")
    
    return imported


# ============================================================================
# MAIN
# ============================================================================

def find_flight_data_files(date_filter: Optional[str] = None) -> List[Path]:
    """
    Find all flight data JSON files in the new folder structure.
    
    Args:
        date_filter: Optional date string (YYYY-MM-DD) to filter by specific date folder
        
    Returns:
        List of paths to JSON files
    """
    files = []
    
    if not FLIGHT_DATA_DIR.exists():
        return files
    
    if date_filter:
        # Look in specific date folder
        date_dir = FLIGHT_DATA_DIR / date_filter
        if date_dir.exists() and date_dir.is_dir():
            files.extend(date_dir.glob("*.json"))
    else:
        # Look in all date folders
        for date_dir in FLIGHT_DATA_DIR.iterdir():
            if date_dir.is_dir():
                files.extend(date_dir.glob("*.json"))
    
    return sorted(files)


def main():
    """
    Main function to import flights data.
    """
    parser = argparse.ArgumentParser(
        description="Import flights data from JSON files into the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import all files from all dates:
  python scripts/import_flights_to_db.py
  
  # Import files from a specific date:
  python scripts/import_flights_to_db.py --date 2026-01-22
  
  # Import a specific file:
  python scripts/import_flights_to_db.py data/flight_data/2026-01-22/BCN.json
        """
    )
    
    parser.add_argument(
        'file_path',
        nargs='?',
        help='Specific JSON file to import (optional)'
    )
    
    parser.add_argument(
        '--date',
        type=str,
        help='Import files from a specific date folder (YYYY-MM-DD format)'
    )
    
    args = parser.parse_args()
    
    # Check if database exists
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Please run setup_flights_database.py first")
        sys.exit(1)
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # Get files to process
        if args.file_path:
            # Process specific file
            json_file = Path(args.file_path)
            if not json_file.is_absolute():
                # Try relative to project root first
                json_file = PROJECT_ROOT / json_file
                if not json_file.exists():
                    # Try relative to data directory
                    json_file = DATA_DIR / args.file_path
            
            if not json_file.exists():
                print(f"✗ Error: File not found: {args.file_path}")
                sys.exit(1)
            
            files_to_process = [json_file]
        else:
            # Find all JSON files in flight_data structure
            files_to_process = find_flight_data_files(args.date)
            
            if not files_to_process:
                if args.date:
                    print(f"No JSON files found in date folder: {args.date}")
                    print(f"Looking in: {FLIGHT_DATA_DIR / args.date}")
                else:
                    print("No JSON files found to import.")
                    print(f"Looking in: {FLIGHT_DATA_DIR}/*/")
                    print("\nExpected structure: data/flight_data/{date}/{airport_code}.json")
                sys.exit(0)
        
        print("=" * 70)
        print("IMPORTING FLIGHTS TO DATABASE")
        print("=" * 70)
        print(f"Database: {DB_PATH}")
        print(f"Files to process: {len(files_to_process)}")
        
        if args.date:
            print(f"Date filter: {args.date}")
        
        # Get current count
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM flights")
        count_before = cursor.fetchone()[0]
        print(f"Current flights in database: {count_before}")
        print()
        
        # Import each file
        total_imported = 0
        for json_file in files_to_process:
            imported = import_flights_from_json(json_file, conn)
            total_imported += imported
        
        # Get final count
        cursor.execute("SELECT COUNT(*) FROM flights")
        count_after = cursor.fetchone()[0]
        
        print("\n" + "=" * 70)
        print("IMPORT SUMMARY")
        print("=" * 70)
        print(f"Files processed: {len(files_to_process)}")
        print(f"Flights imported: {total_imported}")
        print(f"Total flights in database: {count_after}")
        print(f"New flights added: {count_after - count_before}")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
