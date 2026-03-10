"""
Test script for Aviation Edge API - Flights History endpoint.

This script makes a single API call to test the flightsHistory endpoint.
It uses a .env file for secure API key storage.

Usage:
    1. Create a .env file in the project root with:
       AVIATION_EDGE_API_KEY=your_api_key_here
    
    2. Run the script:
       python scripts/test_aviation_edge_api.py

Configuration:
    Modify the AIRPORT_CODE and DATE_FROM variables below to test different airports/dates.
"""

import os
import sys
import requests
import json
from datetime import datetime
from pathlib import Path

# Try to load python-dotenv if available
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# ============================================================================
# CONFIGURATION - Modify these parameters as needed
# ============================================================================

# Airport code (e.g., 'JFK' for New York, 'BCN' for Barcelona)
AIRPORT_CODE = "BCN"

# Start date in YYYY-MM-DD format (e.g., '2024-12-25')
# Defaults to today's date if not specified
DATE_FROM = "2026-01-22"

# End date in YYYY-MM-DD format (optional - leave as None to not include date_to parameter)
# DATE_TO = "2024-12-31"
DATE_TO = None

# API endpoint
BASE_URL = "https://aviation-edge.com/v2/public/flightsHistory"

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
# API CALL
# ============================================================================

def test_flights_history_api(api_key, airport_code, date_from, flight_type="departure", date_to=None):
    """
    Make a single API call to the flightsHistory endpoint.
    
    Args:
        api_key (str): API key for authentication
        airport_code (str): Airport code (e.g., 'JFK')
        date_from (str): Start date in YYYY-MM-DD format
        flight_type (str): Type of flight - 'departure' or 'arrival' (default: 'departure')
        date_to (str, optional): End date in YYYY-MM-DD format. If None, not included in request.
    
    Returns:
        dict: API response as JSON, or None if request failed
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
    
    print("=" * 70)
    print("AVIATION EDGE API TEST - Flights History Endpoint")
    print("=" * 70)
    print(f"Endpoint: {BASE_URL}")
    print(f"Parameters:")
    print(f"  - Code: {airport_code}")
    print(f"  - Type: {flight_type}")
    print(f"  - Date From: {date_from}")
    if date_to:
        print(f"  - Date To: {date_to}")
    print(f"  - API Key: {'*' * (len(api_key) - 4) + api_key[-4:]}")  # Show last 4 chars only
    print("=" * 70)
    print("\nMaking API call...")
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        
        data = response.json()
        
        print("✓ API call successful!")
        print(f"  Status Code: {response.status_code}")
        print(f"  Response Type: {type(data)}")
        
        if isinstance(data, list):
            print(f"  Number of flights: {len(data)}")
        elif isinstance(data, dict):
            print(f"  Response keys: {list(data.keys())}")
        
        return data
        
    except requests.exceptions.RequestException as e:
        print(f"✗ API call failed!")
        print(f"  Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"  Status Code: {e.response.status_code}")
            try:
                error_data = e.response.json()
                print(f"  Error Response: {json.dumps(error_data, indent=2)}")
            except:
                print(f"  Response Text: {e.response.text[:500]}")
        return None


# ============================================================================
# RESPONSE DISPLAY
# ============================================================================

def display_response(data):
    """
    Display the API response in a readable format.
    
    Args:
        data: API response data (dict or list)
    """
    if data is None:
        print("\nNo data to display (API call failed).")
        return
    
    print("\n" + "=" * 70)
    print("API RESPONSE")
    print("=" * 70)
    
    # Pretty print JSON
    print(json.dumps(data, indent=2, ensure_ascii=False))
    
    # If it's a list of flights, show summary
    if isinstance(data, list) and len(data) > 0:
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total flights found: {len(data)}")
        
        # Show first few flights as examples
        print(f"\nFirst {min(3, len(data))} flight(s):")
        for i, flight in enumerate(data[:3], 1):
            print(f"\n  Flight {i}:")
            if isinstance(flight, dict):
                for key, value in list(flight.items())[:5]:  # Show first 5 fields
                    print(f"    {key}: {value}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """
    Main function to test the API.
    """
    # Get API key from environment
    api_key = get_api_key()
    
    # Find data directory (project root / data)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    data_dir = project_root / "data"
    
    # Create data directory if it doesn't exist
    data_dir.mkdir(exist_ok=True)
    
    # Make single API call
    response_data = test_flights_history_api(
        api_key=api_key,
        airport_code=AIRPORT_CODE,
        date_from=DATE_FROM,
        flight_type="departure",
        date_to=DATE_TO
    )
    
    # Display response
    display_response(response_data)
    
    # Save response to data folder
    if response_data is not None:
        # Create filename with airport code and date
        date_str = DATE_FROM.replace("-", "")
        output_file = data_dir / f"aviation_edge_history_{AIRPORT_CODE}_{date_str}.json"
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(response_data, f, indent=2, ensure_ascii=False)
            print(f"\n✓ Response saved to: {output_file}")
        except Exception as e:
            print(f"\n⚠ Could not save response to file: {e}")
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
