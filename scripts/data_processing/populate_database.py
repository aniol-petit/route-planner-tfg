"""
Populate database with airports, train routes, and flights data.

This script:
1. Creates the airports, train_routes, and flights tables if they don't exist
2. Populates the airports table from airports_filtered.csv
3. Populates the train_routes table from route_stops_with_countries_filtered.csv
4. Populates the flights table from JSON files in data/flight_data/

Usage:
    python scripts/populate_database.py [--force] [--skip-flights] [--flights-date DATE]
    
    --force: Clear existing data and repopulate tables
    --skip-flights: Skip importing flights data
    --flights-date: Import flights from a specific date only (YYYY-MM-DD format)
"""

import sqlite3
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, List, Dict

# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "flights.db"

AIRPORTS_CSV = DATA_DIR / "airports_filtered.csv"
TRAIN_ROUTES_CSV = DATA_DIR / "route_stops_with_countries_filtered.csv"
FLIGHT_DATA_DIR = DATA_DIR / "flight_data"

# ============================================================================
# DATABASE SCHEMA
# ============================================================================

CREATE_AIRPORTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS airports (
    id INTEGER PRIMARY KEY,
    ident TEXT,
    type TEXT,
    name TEXT,
    latitude_deg REAL,
    longitude_deg REAL,
    elevation_ft INTEGER,
    continent TEXT,
    iso_country TEXT,
    iso_region TEXT,
    municipality TEXT,
    scheduled_service TEXT,
    icao_code TEXT,
    iata_code TEXT,
    gps_code TEXT,
    local_code TEXT,
    home_link TEXT,
    wikipedia_link TEXT,
    keywords TEXT
);

CREATE INDEX IF NOT EXISTS idx_airports_iata ON airports(iata_code);
CREATE INDEX IF NOT EXISTS idx_airports_country ON airports(iso_country);
CREATE INDEX IF NOT EXISTS idx_airports_ident ON airports(ident);
"""

CREATE_TRAIN_ROUTES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS train_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_origin TEXT NOT NULL,
    route_destination TEXT NOT NULL,
    search_date TEXT NOT NULL,
    departure_time TEXT,
    arrival_time TEXT,
    legs_with_countries TEXT,
    legs_filtered TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_train_routes_origin ON train_routes(route_origin);
CREATE INDEX IF NOT EXISTS idx_train_routes_destination ON train_routes(route_destination);
CREATE INDEX IF NOT EXISTS idx_train_routes_date ON train_routes(search_date);
"""

CREATE_FLIGHTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number TEXT NOT NULL,
    departure_airport TEXT NOT NULL,
    arrival_airport TEXT NOT NULL,
    departure_scheduled TEXT NOT NULL,
    departure_estimated TEXT,
    departure_delay_minutes INTEGER,
    arrival_scheduled TEXT,
    status TEXT,
    date TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(flight_number, departure_airport, departure_scheduled)
);

CREATE INDEX IF NOT EXISTS idx_departure_airport ON flights(departure_airport);
CREATE INDEX IF NOT EXISTS idx_arrival_airport ON flights(arrival_airport);
CREATE INDEX IF NOT EXISTS idx_date ON flights(date);
CREATE INDEX IF NOT EXISTS idx_flight_number ON flights(flight_number);
"""

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def parse_number(value: str) -> Optional[float]:
    """Parse a number string, handling scientific notation and commas."""
    if not value or value.strip() == '':
        return None
    try:
        # Replace comma with dot for decimal separator
        value = value.replace(',', '.')
        # Handle scientific notation (e.g., "3,48648E+15")
        if 'E' in value.upper() or 'e' in value:
            return float(value)
        return float(value)
    except (ValueError, AttributeError):
        return None

def parse_integer(value: str) -> Optional[int]:
    """Parse an integer string."""
    if not value or value.strip() == '':
        return None
    try:
        return int(float(value))
    except (ValueError, AttributeError):
        return None

def clean_text(value: str) -> Optional[str]:
    """Clean and return text value, or None if empty."""
    if not value or value.strip() == '':
        return None
    return value.strip()

# ============================================================================
# DATABASE SETUP
# ============================================================================

def setup_tables(conn: sqlite3.Connection):
    """Create the airports, train_routes, and flights tables if they don't exist."""
    cursor = conn.cursor()
    cursor.executescript(CREATE_AIRPORTS_TABLE_SQL)
    cursor.executescript(CREATE_TRAIN_ROUTES_TABLE_SQL)
    cursor.executescript(CREATE_FLIGHTS_TABLE_SQL)
    conn.commit()
    print("✓ Tables created successfully")

# ============================================================================
# AIRPORTS POPULATION
# ============================================================================

def populate_airports(conn: sqlite3.Connection, force: bool = False):
    """Populate the airports table from airports.csv."""
    if not AIRPORTS_CSV.exists():
        print(f"✗ Error: {AIRPORTS_CSV} not found")
        return 0
    
    cursor = conn.cursor()
    
    # Check if airports table already has data
    cursor.execute("SELECT COUNT(*) FROM airports")
    count_before = cursor.fetchone()[0]
    
    if count_before > 0:
        if force:
            print(f"⚠ Clearing {count_before} existing airport records...")
            cursor.execute("DELETE FROM airports")
            conn.commit()
            count_before = 0
        else:
            print(f"⚠ Airports table already has {count_before} records")
            print("  Skipping airports population (use --force to repopulate)")
            return count_before
    
    print(f"\nReading airports from {AIRPORTS_CSV}...")
    
    inserted = 0
    skipped = 0
    
    # Use UTF-8 with error handling - airports_filtered.csv may have special characters
    # errors='replace' will replace problematic bytes with replacement characters
    # This is safer than strict mode which would fail on any encoding issue
    with open(AIRPORTS_CSV, 'r', encoding='utf-8', errors='replace') as f:
        # Use comma as delimiter (airports_filtered.csv uses comma)
        reader = csv.DictReader(f, delimiter=',')
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 because row 1 is header
            try:
                # Parse all fields
                airport_id = parse_integer(row.get('id', ''))
                if airport_id is None:
                    skipped += 1
                    continue
                
                cursor.execute("""
                    INSERT OR REPLACE INTO airports (
                        id, ident, type, name, latitude_deg, longitude_deg,
                        elevation_ft, continent, iso_country, iso_region,
                        municipality, scheduled_service, icao_code, iata_code,
                        gps_code, local_code, home_link, wikipedia_link, keywords
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    airport_id,
                    clean_text(row.get('ident', '')),
                    clean_text(row.get('type', '')),
                    clean_text(row.get('name', '')),
                    parse_number(row.get('latitude_deg', '')),
                    parse_number(row.get('longitude_deg', '')),
                    parse_integer(row.get('elevation_ft', '')),
                    clean_text(row.get('continent', '')),
                    clean_text(row.get('iso_country', '')),
                    clean_text(row.get('iso_region', '')),
                    clean_text(row.get('municipality', '')),
                    clean_text(row.get('scheduled_service', '')),
                    clean_text(row.get('icao_code', '')),
                    clean_text(row.get('iata_code', '')),
                    clean_text(row.get('gps_code', '')),
                    clean_text(row.get('local_code', '')),
                    clean_text(row.get('home_link', '')),
                    clean_text(row.get('wikipedia_link', '')),
                    clean_text(row.get('keywords', ''))
                ))
                inserted += 1
                
                # Commit every 1000 rows for better performance
                if inserted % 1000 == 0:
                    conn.commit()
                    print(f"  Inserted {inserted} airports...")
                    
            except Exception as e:
                skipped += 1
                if row_num <= 10:  # Only show errors for first few rows
                    print(f"  ⚠ Error on row {row_num}: {e}")
                continue
    
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM airports")
    count_after = cursor.fetchone()[0]
    
    print(f"✓ Airports population complete:")
    print(f"  - Inserted: {inserted}")
    print(f"  - Skipped: {skipped}")
    print(f"  - Total in database: {count_after}")
    
    return inserted

# ============================================================================
# TRAIN ROUTES POPULATION
# ============================================================================

def populate_train_routes(conn: sqlite3.Connection, force: bool = False):
    """Populate the train_routes table from route_stops_with_countries_filtered.csv."""
    if not TRAIN_ROUTES_CSV.exists():
        print(f"✗ Error: {TRAIN_ROUTES_CSV} not found")
        return 0
    
    cursor = conn.cursor()
    
    # Check if train_routes table already has data
    cursor.execute("SELECT COUNT(*) FROM train_routes")
    count_before = cursor.fetchone()[0]
    
    if count_before > 0:
        if force:
            print(f"⚠ Clearing {count_before} existing train route records...")
            cursor.execute("DELETE FROM train_routes")
            conn.commit()
            count_before = 0
        else:
            print(f"⚠ Train routes table already has {count_before} records")
            print("  Skipping train routes population (use --force to repopulate)")
            return count_before
    
    print(f"\nReading train routes from {TRAIN_ROUTES_CSV}...")
    
    inserted = 0
    skipped = 0
    
    # Try different encodings for train routes CSV
    encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1250', 'cp1252']
    encoding_used = None
    
    for encoding in encodings:
        try:
            # Test if we can read the file with this encoding
            with open(TRAIN_ROUTES_CSV, 'r', encoding=encoding) as test_file:
                test_file.readline()
            encoding_used = encoding
            if encoding != 'utf-8':
                print(f"  Using encoding: {encoding}")
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    if encoding_used is None:
        print(f"✗ Error: Could not read {TRAIN_ROUTES_CSV} with any supported encoding")
        return 0
    
    with open(TRAIN_ROUTES_CSV, 'r', encoding=encoding_used) as f:
        # Use comma as delimiter
        reader = csv.DictReader(f, delimiter=',')
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 because row 1 is header
            try:
                route_origin = clean_text(row.get('route_origin', ''))
                route_destination = clean_text(row.get('route_destination', ''))
                search_date = clean_text(row.get('search_date', ''))
                
                if not route_origin or not route_destination or not search_date:
                    skipped += 1
                    continue
                
                # Store JSON strings as-is (they're already JSON strings in the CSV)
                legs_with_countries = clean_text(row.get('legs_with_countries', ''))
                legs_filtered = clean_text(row.get('legs_filtered', ''))
                
                cursor.execute("""
                    INSERT INTO train_routes (
                        route_origin, route_destination, search_date,
                        departure_time, arrival_time, legs_with_countries, legs_filtered
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    route_origin,
                    route_destination,
                    search_date,
                    clean_text(row.get('departure_time', '')),
                    clean_text(row.get('arrival_time', '')),
                    legs_with_countries,
                    legs_filtered
                ))
                inserted += 1
                
                # Commit every 1000 rows for better performance
                if inserted % 1000 == 0:
                    conn.commit()
                    print(f"  Inserted {inserted} train routes...")
                    
            except Exception as e:
                skipped += 1
                if row_num <= 10:  # Only show errors for first few rows
                    print(f"  ⚠ Error on row {row_num}: {e}")
                continue
    
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM train_routes")
    count_after = cursor.fetchone()[0]
    
    print(f"✓ Train routes population complete:")
    print(f"  - Inserted: {inserted}")
    print(f"  - Skipped: {skipped}")
    print(f"  - Total in database: {count_after}")
    
    return inserted

# ============================================================================
# FLIGHTS POPULATION
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
    except (KeyError, AttributeError, ValueError):
        return None


def find_flight_data_files(date_filter: Optional[str] = None) -> List[Path]:
    """
    Find all flight data JSON files in the flight_data folder structure.
    
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


def populate_flights(conn: sqlite3.Connection, force: bool = False, date_filter: Optional[str] = None):
    """Populate the flights table from JSON files in flight_data directory."""
    cursor = conn.cursor()
    
    # Check if flights table already has data
    cursor.execute("SELECT COUNT(*) FROM flights")
    count_before = cursor.fetchone()[0]
    
    if count_before > 0:
        if force:
            print(f"⚠ Clearing {count_before} existing flight records...")
            cursor.execute("DELETE FROM flights")
            conn.commit()
            count_before = 0
        else:
            print(f"⚠ Flights table already has {count_before} records")
            print("  Skipping flights population (use --force to repopulate)")
            return count_before
    
    # Find JSON files to process
    files_to_process = find_flight_data_files(date_filter)
    
    if not files_to_process:
        if date_filter:
            print(f"✗ No JSON files found in date folder: {date_filter}")
            print(f"  Looking in: {FLIGHT_DATA_DIR / date_filter}")
        else:
            print(f"✗ No JSON files found in {FLIGHT_DATA_DIR}")
        return 0
    
    print(f"\nReading flights from {len(files_to_process)} JSON files...")
    if date_filter:
        print(f"  Date filter: {date_filter}")
    
    total_imported = 0
    total_skipped = 0
    total_errors = 0
    
    insert_sql = """
    INSERT OR IGNORE INTO flights (
        flight_number, departure_airport, arrival_airport,
        departure_scheduled, departure_estimated, departure_delay_minutes,
        arrival_scheduled, status, date
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    for file_num, json_file in enumerate(files_to_process, 1):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                flights_data = json.load(f)
        except json.JSONDecodeError:
            total_errors += 1
            continue
        except FileNotFoundError:
            total_errors += 1
            continue
        
        if not isinstance(flights_data, list):
            total_errors += 1
            continue
        
        file_imported = 0
        file_skipped = 0
        
        for flight in flights_data:
            flight_data = extract_flight_data(flight)
            
            if not flight_data:
                file_skipped += 1
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
                file_imported += 1
                total_imported += 1
            except sqlite3.Error:
                file_skipped += 1
                total_skipped += 1
        
        # Commit every 10 files for better performance
        if file_num % 10 == 0:
            conn.commit()
            print(f"  Processed {file_num}/{len(files_to_process)} files, imported {total_imported} flights...")
    
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM flights")
    count_after = cursor.fetchone()[0]
    
    print(f"✓ Flights population complete:")
    print(f"  - Files processed: {len(files_to_process)}")
    print(f"  - Flights imported: {total_imported}")
    print(f"  - Skipped: {total_skipped}")
    print(f"  - Errors: {total_errors}")
    print(f"  - Total in database: {count_after}")
    
    return total_imported

# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    """Main function to populate the database."""
    parser = argparse.ArgumentParser(
        description="Populate database with airports, train routes, and flights data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Populate all tables:
  python scripts/populate_database.py
  
  # Force repopulation of all tables:
  python scripts/populate_database.py --force
  
  # Skip flights import:
  python scripts/populate_database.py --skip-flights
  
  # Import flights from a specific date only:
  python scripts/populate_database.py --flights-date 2026-01-22
        """
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Clear existing data and repopulate tables'
    )
    parser.add_argument(
        '--skip-flights',
        action='store_true',
        help='Skip importing flights data'
    )
    parser.add_argument(
        '--flights-date',
        type=str,
        help='Import flights from a specific date only (YYYY-MM-DD format)'
    )
    args = parser.parse_args()
    
    print("=" * 70)
    print("POPULATING DATABASE")
    print("=" * 70)
    print(f"Database path: {DB_PATH}")
    if args.force:
        print("⚠ Force mode: Will clear existing data")
    if args.skip_flights:
        print("⚠ Skipping flights import")
    if args.flights_date:
        print(f"⚠ Flights date filter: {args.flights_date}")
    
    # Ensure data directory exists
    DATA_DIR.mkdir(exist_ok=True)
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # Setup tables
        print("\n[1/4] Setting up database tables...")
        print("  Creating airports, train_routes, and flights tables...")
        setup_tables(conn)
        
        # Populate airports
        print("\n[2/4] Populating airports table...")
        airports_count = populate_airports(conn, force=args.force)
        
        # Populate train routes
        print("\n[3/4] Populating train routes table...")
        train_routes_count = populate_train_routes(conn, force=args.force)
        
        # Populate flights
        flights_count = 0
        if not args.skip_flights:
            print("\n[4/4] Populating flights table...")
            flights_count = populate_flights(conn, force=args.force, date_filter=args.flights_date)
        else:
            print("\n[4/4] Skipping flights population...")
        
        # Show summary
        print("\n" + "=" * 70)
        print("DATABASE POPULATION SUMMARY")
        print("=" * 70)
        print(f"Airports inserted: {airports_count}")
        print(f"Train routes inserted: {train_routes_count}")
        print(f"Flights inserted: {flights_count}")
        
        # Show table info
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"\nTables in database: {[t[0] for t in tables]}")
        
        # Show record counts
        for table_name in ['airports', 'train_routes', 'flights']:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"  - {table_name}: {count} records")
            except sqlite3.OperationalError:
                pass
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        raise
    finally:
        conn.close()
    
    print("\n" + "=" * 70)
    print("DATABASE POPULATION COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
