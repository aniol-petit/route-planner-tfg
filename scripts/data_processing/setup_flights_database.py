"""
Setup script for flights database.

Creates a SQLite database with a minimal schema for storing flight data
from the Aviation Edge API.

Usage:
    python scripts/setup_flights_database.py
"""

import sqlite3
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

# Database location (in data folder)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "flights.db"

# ============================================================================
# DATABASE SCHEMA
# ============================================================================

CREATE_TABLE_SQL = """
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
# DATABASE SETUP
# ============================================================================

def setup_database():
    """
    Create the database and tables if they don't exist.
    """
    # Ensure data directory exists
    DATA_DIR.mkdir(exist_ok=True)
    
    print("=" * 70)
    print("SETTING UP FLIGHTS DATABASE")
    print("=" * 70)
    print(f"Database path: {DB_PATH}")
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Create table and indexes
        cursor.executescript(CREATE_TABLE_SQL)
        conn.commit()
        
        print("✓ Database created successfully!")
        print("✓ Table 'flights' created")
        print("✓ Indexes created")
        
        # Show table info
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"\nTables in database: {[t[0] for t in tables]}")
        
        # Show schema
        cursor.execute("PRAGMA table_info(flights)")
        columns = cursor.fetchall()
        print("\nTable schema:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
    except sqlite3.Error as e:
        print(f"✗ Error creating database: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()
    
    print("\n" + "=" * 70)
    print("DATABASE SETUP COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    setup_database()
