"""
Automated script to fetch flights data from Aviation Edge API for all airports.

This script reads IATA codes from data/iata_codes.csv and fetches flight data
for each airport, storing responses in data/flight_data/{date}/{airport_code}.json

Usage:
    # Fetch for all airports:
    python scripts/fetch_flights_data.py
    
    # Test with a single airport:
    python scripts/fetch_flights_data.py --airport BCN
    
    # Specify a date:
    python scripts/fetch_flights_data.py --date 2026-01-22
    
    # Fetch for a date range:
    python scripts/fetch_flights_data.py --date 2026-01-22 --date-to 2026-01-25
    
    # Combine options:
    python scripts/fetch_flights_data.py --airport BCN --date 2026-01-22 --date-to 2026-01-25
"""

import os
import sys
import requests
import json
import csv
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, List

# Try to load python-dotenv if available
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================

# API endpoint
BASE_URL = "https://aviation-edge.com/v2/public/flightsHistory"

# Default date (today)
DEFAULT_DATE = datetime.now().strftime("%Y-%m-%d")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ============================================================================
# API KEY MANAGEMENT
# ============================================================================

def load_env_file():
    """
    Load environment variables from .env file if it exists.
    Looks for .env file in the project root (parent of scripts directory).
    """
    if not DOTENV_AVAILABLE:
        return
    
    env_file = PROJECT_ROOT / ".env"
    
    if env_file.exists():
        load_dotenv(env_file)
        print(f"✓ Loaded .env file from: {env_file}")
    else:
        print(f"⚠ .env file not found at: {env_file}")
        print("  You can create one with: AVIATION_EDGE_API_KEY=your_api_key_here")


def get_api_key():
    """
    Retrieve API key from .env file or environment variable.
    
    Returns:
        str: API key
        
    Raises:
        SystemExit: If API key is not found
    """
    # First, try to load from .env file
    load_env_file()
    
    # Then get the API key from environment (either from .env or system env)
    api_key = os.getenv("AVIATION_EDGE_API_KEY")
    
    if not api_key:
        print("\nERROR: API key not found!")
        print("\nPlease create a .env file in the project root with:")
        print("  AVIATION_EDGE_API_KEY=your_api_key_here")
        print(f"\nOr set it as an environment variable:")
        print("  Windows (PowerShell): $env:AVIATION_EDGE_API_KEY='your_api_key_here'")
        print("  Windows (CMD): set AVIATION_EDGE_API_KEY=your_api_key_here")
        print("  Linux/Mac: export AVIATION_EDGE_API_KEY='your_api_key_here'")
        
        if not DOTENV_AVAILABLE:
            print("\nNote: python-dotenv is not installed. Install it with:")
            print("  pip install python-dotenv")
            print("\nOr use environment variables directly.")
        
        sys.exit(1)
    
    return api_key


# ============================================================================
# IATA CODES LOADING
# ============================================================================

def load_iata_codes(csv_path: Path) -> List[str]:
    """
    Load IATA codes from CSV file.
    
    Args:
        csv_path: Path to CSV file with IATA codes
        
    Returns:
        List of IATA codes
    """
    iata_codes = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('iata_code', '').strip().upper()
                if code:
                    iata_codes.append(code)
    except FileNotFoundError:
        print(f"✗ Error: IATA codes file not found at {csv_path}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error reading IATA codes file: {e}")
        sys.exit(1)
    
    return iata_codes


# ============================================================================
# API CALL
# ============================================================================

def fetch_flights_history(api_key: str, airport_code: str, date_from: str, 
                         flight_type: str = "departure", date_to: Optional[str] = None) -> Optional[dict]:
    """
    Make an API call to the flightsHistory endpoint.
    
    Args:
        api_key: API key for authentication
        airport_code: Airport code (e.g., 'JFK')
        date_from: Start date in YYYY-MM-DD format
        flight_type: Type of flight - 'departure' or 'arrival' (default: 'departure')
        date_to: End date in YYYY-MM-DD format. If None, not included in request.
    
    Returns:
        API response as JSON (dict or list), or None if request failed
    """
    params = {
        "key": api_key,
        "code": airport_code,
        "type": flight_type,
        "date_from": date_from
    }
    
    # Only add date_to if provided
    if date_to is not None:
        params["date_to"] = date_to
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        
        data = response.json()
        return data
        
    except requests.exceptions.RequestException as e:
        print(f"  ✗ API call failed for {airport_code}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"    Status Code: {e.response.status_code}")
            try:
                error_data = e.response.json()
                if isinstance(error_data, dict) and 'error' in error_data:
                    print(f"    Error: {error_data['error']}")
            except:
                pass
        return None


# ============================================================================
# DATE PARTITIONING
# ============================================================================

def extract_flight_date(flight: dict, flight_type: str = "departure") -> Optional[str]:
    """
    Extract date (YYYY-MM-DD) from a flight's scheduled time.
    
    Args:
        flight: Flight data dictionary
        flight_type: 'departure' or 'arrival' to determine which time to use
        
    Returns:
        Date string in YYYY-MM-DD format, or None if not found
    """
    try:
        if flight_type == "departure":
            time_str = flight.get("departure", {}).get("scheduledTime")
        else:
            time_str = flight.get("arrival", {}).get("scheduledTime")
        
        if not time_str:
            return None
        
        # Extract date part (format: "2026-01-22t01:30:00.000")
        date_part = time_str.split('t')[0] if 't' in time_str.lower() else time_str.split('T')[0]
        date_part = date_part.split(' ')[0]  # In case there's a space instead
        
        # Validate date format
        datetime.strptime(date_part, "%Y-%m-%d")
        return date_part
    except (ValueError, AttributeError, KeyError, IndexError):
        return None


def partition_flights_by_date(flights: list, flight_type: str = "departure") -> dict:
    """
    Partition a list of flights by their date.
    
    Args:
        flights: List of flight dictionaries
        flight_type: 'departure' or 'arrival' to determine which time to use
        
    Returns:
        Dictionary mapping date strings (YYYY-MM-DD) to lists of flights
    """
    partitioned = {}
    
    for flight in flights:
        date = extract_flight_date(flight, flight_type)
        if date:
            if date not in partitioned:
                partitioned[date] = []
            partitioned[date].append(flight)
    
    return partitioned


# ============================================================================
# FILE MANAGEMENT
# ============================================================================

def save_flights_data(data: list | dict, output_path: Path) -> bool:
    """
    Save flights data to JSON file.
    
    Args:
        data: Flight data to save (list or dict)
        output_path: Path where to save the file
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"  ✗ Error saving file: {e}")
        return False


# ============================================================================
# MAIN PROCESSING
# ============================================================================

def fetch_flights_for_airport(api_key: str, airport_code: str, date: str, 
                              flight_data_dir: Path, flight_type: str = "departure",
                              date_to: Optional[str] = None) -> tuple[int, int]:
    """
    Fetch flights data for a single airport and save to file(s).
    
    If date_to is provided, partitions flights by date and saves separate files
    for each date in the range.
    
    Args:
        api_key: API key
        airport_code: Airport IATA code
        date: Start date in YYYY-MM-DD format
        flight_data_dir: Base directory for flight data (data/flight_data)
        flight_type: Type of flight - 'departure' or 'arrival'
        date_to: End date in YYYY-MM-DD format (optional)
        
    Returns:
        Tuple of (successful_files, failed_files)
    """
    # Fetch data from API
    print(f"  Fetching {airport_code}...", end=" ", flush=True)
    data = fetch_flights_history(api_key, airport_code, date, flight_type, date_to)
    
    if data is None:
        print("✗ Failed")
        return (0, 1)
    
    # Handle non-list responses gracefully
    if not isinstance(data, list):
        if isinstance(data, dict):
            # Check for error messages
            error_msg = data.get('error', '')
            if error_msg:
                print(f"⚠ No data: {error_msg}")
            elif len(data) == 0:
                print("⚠ No flights found (empty response)")
            else:
                # Unknown dict structure
                print(f"⚠ Unexpected response format: {list(data.keys())[:3]}")
        else:
            # Other non-list, non-dict types (shouldn't happen, but handle gracefully)
            print(f"⚠ Unexpected data type: {type(data).__name__}")
        
        # Return (0, 0) to indicate no files saved but not a failure
        # This is expected for airports with no flights
        return (0, 0)
    
    # If date_to is provided, partition by date
    if date_to:
        print(f"✓ Received {len(data)} flights, partitioning by date...", end=" ", flush=True)
        partitioned = partition_flights_by_date(data, flight_type)
        
        if not partitioned:
            print("✗ No valid dates found in flights")
            return (0, 1)
        
        successful = 0
        failed = 0
        skipped = 0
        
        for flight_date, flights in sorted(partitioned.items()):
            date_dir = flight_data_dir / flight_date
            output_file = date_dir / f"{airport_code}.json"
            
            # Check if file already exists
            if output_file.exists():
                skipped += 1
                continue
            
            # Save partitioned flights
            if save_flights_data(flights, output_file):
                successful += 1
            else:
                failed += 1
        
        print(f"✓ Saved {successful} file(s), skipped {skipped} existing, {failed} failed")
        return (successful, failed)
    else:
        # Single date - save to one file
        date_dir = flight_data_dir / date
        output_file = date_dir / f"{airport_code}.json"
        
        # Check if file already exists
        if output_file.exists():
            print(f"Already exists (skipping)")
            return (0, 0)
        
        # Save to file
        if save_flights_data(data, output_file):
            print(f"✓ Saved {len(data)} flights")
            return (1, 0)
        else:
            print("✗ Failed to save")
            return (0, 1)


def main():
    """
    Main function to fetch flights data.
    """
    parser = argparse.ArgumentParser(
        description="Fetch flights data from Aviation Edge API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch for all airports for today:
  python scripts/fetch_flights_data.py
  
  # Test with a single airport:
  python scripts/fetch_flights_data.py --airport BCN
  
  # Specify a date:
  python scripts/fetch_flights_data.py --date 2026-01-22
  
  # Fetch for a date range:
  python scripts/fetch_flights_data.py --date 2026-01-22 --date-to 2026-01-25
  
  # Combine options:
  python scripts/fetch_flights_data.py --airport BCN --date 2026-01-22 --date-to 2026-01-25
        """
    )
    
    parser.add_argument(
        '--airport',
        type=str,
        help='Single airport IATA code to fetch (for testing). If not provided, fetches for all airports.'
    )
    
    parser.add_argument(
        '--date',
        type=str,
        default=DEFAULT_DATE,
        help=f'Start date (date_from) in YYYY-MM-DD format (default: {DEFAULT_DATE})'
    )
    
    parser.add_argument(
        '--date-to',
        type=str,
        default=None,
        help='End date (date_to) in YYYY-MM-DD format. If not provided, only fetches for the start date.'
    )
    
    parser.add_argument(
        '--type',
        type=str,
        choices=['departure', 'arrival'],
        default='departure',
        help='Flight type: departure or arrival (default: departure)'
    )
    
    args = parser.parse_args()
    
    # Validate date format
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"✗ Error: Invalid date format '{args.date}'. Use YYYY-MM-DD format.")
        sys.exit(1)
    
    # Validate date_to format if provided
    if args.date_to:
        try:
            datetime.strptime(args.date_to, "%Y-%m-%d")
        except ValueError:
            print(f"✗ Error: Invalid date_to format '{args.date_to}'. Use YYYY-MM-DD format.")
            sys.exit(1)
        
        # Validate that date_to is after date
        date_from = datetime.strptime(args.date, "%Y-%m-%d")
        date_to = datetime.strptime(args.date_to, "%Y-%m-%d")
        if date_to < date_from:
            print(f"✗ Error: date_to ({args.date_to}) must be after or equal to date ({args.date})")
            sys.exit(1)
    
    # Get API key
    api_key = get_api_key()
    
    # Setup paths
    data_dir = PROJECT_ROOT / "data"
    flight_data_dir = data_dir / "flight_data"
    iata_codes_file = data_dir / "iata_codes.csv"
    
    # Ensure directories exist
    flight_data_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 70)
    print("FETCHING FLIGHTS DATA")
    print("=" * 70)
    if args.date_to:
        print(f"Date range: {args.date} to {args.date_to}")
    else:
        print(f"Date: {args.date}")
    print(f"Flight type: {args.type}")
    print(f"Output directory: {flight_data_dir}")
    
    # Get list of airports to process
    if args.airport:
        # Single airport mode (testing)
        airports = [args.airport.upper()]
        print(f"Mode: Single airport test ({args.airport.upper()})")
    else:
        # All airports mode
        print(f"Mode: All airports")
        airports = load_iata_codes(iata_codes_file)
        print(f"Loaded {len(airports)} airports from {iata_codes_file.name}")
    
    print("=" * 70)
    print()
    
    # Process each airport
    successful_files = 0
    failed_files = 0
    skipped_files = 0
    
    for i, airport_code in enumerate(airports, 1):
        print(f"[{i}/{len(airports)}] {airport_code}:", end=" ")
        
        # If no date_to, check if file already exists (for single date)
        if not args.date_to:
            date_dir = flight_data_dir / args.date
            output_file = date_dir / f"{airport_code}.json"
            
            if output_file.exists():
                print(f"Already exists (skipping)")
                skipped_files += 1
                continue
        
        # Fetch and save
        files_saved, files_failed = fetch_flights_for_airport(
            api_key, airport_code, args.date, 
            flight_data_dir, args.type, args.date_to
        )
        
        successful_files += files_saved
        failed_files += files_failed
        
        # Small delay to avoid rate limiting (if processing many airports)
        if not args.airport and i < len(airports):
            import time
            time.sleep(0.5)  # 500ms delay between requests
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total airports: {len(airports)}")
    print(f"Files saved: {successful_files}")
    print(f"Files failed: {failed_files}")
    print(f"Files skipped (already exist): {skipped_files}")
    if args.date_to:
        print(f"\nNote: When using date ranges, flights are partitioned by date")
        print(f"      into separate files in their respective date folders.")
    print("=" * 70)


if __name__ == "__main__":
    main()
