"""
Country Detection Script for Train Route Stops

This script processes all routes from train_routes.csv, detects the country
for each stop in each leg, and stores the results in a CSV file.

The output maintains the original route structure, with a new column
'legs_with_countries' where each stop is formatted as:
    [("station_name", "country"), [dep_time, arr_time]]

The country detection uses multiple strategies:
1. Station dataset lookup (authoritative)
2. GeoNames city lookup (Europe-focused)
3. Country code patterns in station names
4. Language-specific patterns
5. Known station corrections

All stops are assumed to be in Europe.
"""

import pandas as pd
import re
import json
import sys
from pathlib import Path
from typing import Optional, Dict, List, Tuple


# ============================================================================
# CONFIGURATION
# ============================================================================

# Paths (relative to project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ROUTES_FILE = DATA_DIR / "train_routes.csv"
STATIONS_FILE = DATA_DIR / "train_stations_europe.csv"
CITIES_FILE = DATA_DIR / "cities5000.txt"
OUTPUT_FILE = DATA_DIR / "route_stops_with_countries.csv"


# ============================================================================
# COUNTRY MAPPINGS AND CONSTANTS
# ============================================================================

# Country code to readable label mapping (Europe only)
COUNTRY_LABELS = {
    "FR": "France", "BE": "Belgium", "ES": "Spain", "CH": "Switzerland",
    "IT": "Italy", "AD": "Andorra", "GB": "England", "NL": "Netherlands",
    "DE": "Germany", "AT": "Austria", "LU": "Luxembourg", "PT": "Portugal",
    "PL": "Poland", "SE": "Sweden", "DK": "Denmark", "NO": "Norway",
    "FI": "Finland", "CZ": "Czechia", "SK": "Slovakia", "HU": "Hungary",
    "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "IE": "Ireland",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia", "HR": "Croatia",
    "SI": "Slovenia", "BA": "Bosnia and Herzegovina", "RS": "Serbia",
    "ME": "Montenegro", "MK": "North Macedonia", "AL": "Albania",
    "UA": "Ukraine", "BY": "Belarus", "RU": "Russia", "TR": "Turkey",
    "LI": "Liechtenstein", "MT": "Malta", "MD": "Moldova", "CY": "Cyprus",
}

EU_CODES = set(COUNTRY_LABELS.keys())

# Generic tokens to filter out from station names
GENERIC_TOKENS = {
    "station", "stn", "gare", "bahnhof", "hbf", "hl", "intl", "international",
    "aeroport", "aéroport", "airport", "tgv", "hb", "bf",
    "eurostar", "sncf", "sbb", "cfl", "ter", "rer",
}

# Country code patterns in parentheses (e.g., "(B)", "(CH)", "(LUX)", "(A)")
COUNTRY_CODE_PATTERNS = {
    "(B)": "BE", "(BE)": "BE", "(BEL)": "BE",
    "(CH)": "CH", "(SWI)": "CH", "(SUI)": "CH",
    "(LUX)": "LU", "(LU)": "LU",
    "(A)": "AT", "(AT)": "AT", "(AUT)": "AT",
    "(D)": "DE", "(DE)": "DE", "(GER)": "DE", "(DEU)": "DE",
    "(F)": "FR", "(FR)": "FR", "(FRA)": "FR",
    "(I)": "IT", "(IT)": "IT", "(ITA)": "IT",
    "(NL)": "NL", "(NED)": "NL", "(NLD)": "NL",
    "(PL)": "PL", "(POL)": "PL",
    "(CZ)": "CZ", "(CZE)": "CZ",
    "(SK)": "SK", "(SVK)": "SK",
    "(HU)": "HU", "(HUN)": "HU",
    "(RO)": "RO", "(ROU)": "RO",
    "(HR)": "HR", "(CRO)": "HR", "(HRV)": "HR",
    "(SI)": "SI", "(SVN)": "SI",
    "(DK)": "DK", "(DNK)": "DK", "(DEN)": "DK",
    "(SE)": "SE", "(SWE)": "SE",
    "(NO)": "NO", "(NOR)": "NO",
    "(ES)": "ES", "(ESP)": "ES",
    "(PT)": "PT", "(PRT)": "PT",
}

# Common short tokens that should be avoided (too ambiguous)
AMBIGUOUS_SHORT_TOKENS = {
    "st", "stn", "h", "n", "s", "e", "w", "nord", "sud", "est", "west",
    "mont", "blanc", "marco", "calci", "gardena", "laion", "waidbruck", "lajen"
}

# Language-specific patterns that indicate countries
LANGUAGE_PATTERNS = {
    # Czech patterns (high confidence)
    " nad ": "CZ",  # "nad" = "on" in Czech
    "nad Ohri": "CZ",
    " u ": "CZ",   # "u" = "at" in Czech
    " hl.n.": "CZ",  # "hlavní nádraží" = main station
    " hl.st.": "CZ",
    # Austrian patterns (check before German patterns)
    " b.Wien": "AT",  # "bei Wien" = near Vienna
    " b. ": "AT",     # "bei" = "near" in German/Austrian
    " bei ": "AT",
    "an der Pyhrnbahn": "AT",  # Austrian railway line
    "an der March": "AT",  # March river in Austria
    "Klopeiner See": "AT",  # Austrian lake
    "NÖ": "AT",  # Niederösterreich
    # German patterns
    "(Bay)": "DE",   # Bayern = Bavaria
    "(Main)": "DE",
    "(Saar)": "DE",
    "(Westf)": "DE",
    "(Oder)": "DE",
    "(Isar)": "DE",
    "(bei Berlin)": "DE",
    "(Breisgau)": "DE",
    "(Allgäu)": "DE",
    # Italian patterns
    "di ": "IT",     # "of" in Italian
    "del ": "IT",    # "of the" in Italian
    "S. ": "IT",     # "San/Santo" abbreviation
    "Porta ": "IT",  # "Porta" = gate/station in Italian
    "Ponte ": "IT",  # "Ponte" = bridge in Italian
    # French patterns
    "St-": "FR",     # "Saint" abbreviation
    "St ": "FR",
    "Ste-": "FR",
    "Ste ": "FR",
    "Gare ": "FR",   # "Gare" = station in French
    "TGV": "FR",     # French high-speed train
    "Rhône": "FR",   # Rhône river/region
    "Alpes": "FR",   # Alps (French side)
    # Polish patterns
    "Glowny": "PL",  # "Główny" = main
    "Port Lotniczy": "PL",  # airport
    # Romanian patterns
    "Turnu": "RO",   # Romanian place name pattern
    # Spanish patterns (to avoid false matches)
    "Centrale": "IT",  # Italian, not Spanish
    # Additional specific patterns
    "Rhône-Alpes": "FR",  # French region
    "Gardena": "IT",  # Italian valley
    "Laion": "IT",  # Italian town
    "Waidbruck": "IT",  # Italian town (German name)
    "Lajen": "IT",  # Italian town
    "Mont-Blanc": "FR",  # French/Italian border, but "Genève" context suggests France
}

# Known corrections for stations that are frequently mis-mapped
STATION_CORRECTIONS = {
    # French stations
    "Genève, Mont-Blanc": "CH",  # Geneva is in Switzerland
    "Orcier, Stade": "FR",
    "Valence TGV Rhône-Alpes Sud": "FR",
    "Fillière, P+R": "FR",
    "Bons-en-Chablais, Gare Didier": "FR",
    "Bons-en-Chablais(An)": "FR",
    "Bartenheim Pharmacie": "FR",
    "St-Léonard": "CH",  # Switzerland, Canton Valais
    "Delémont": "CH",  # Switzerland (not France)
    "Capolago-Riva S. Vitale": "CH",  # Switzerland (not Italy)
    # Belgian stations
    "Dave-St.-Martin": "BE",
    "Bruxelles-Midi Eurostar": "BE",
    "Gent St Pieters": "BE",  # Ghent, Belgium (not France)
    "Rochefort-Jemelle": "BE",  # Belgium (not France)
    # Austrian stations
    "Baden b.Wien": "AT",
    "Klaus an der Pyhrnbahn": "AT",
    "Sierndorf an der March": "AT",
    "Kühnsdorf-Klopeiner See": "AT",
    "Salzburg Liefering": "AT",  # Austria (not Germany)
    "Hall in Tirol-Thaur": "AT",  # Austria (not Germany)
    "Leithen b.Seefeld": "AT",  # Austria (Seefeld is in Tyrol, Austria, not Germany)
    "Reith b.Seefeld": "AT",  # Austria (Seefeld is in Tyrol, Austria, not Germany)
    # Italian stations
    "Ponte Gardena-Laion/Waidbruck-Lajen": "IT",
    "Ponte S. Marco-Calci": "IT",
    "Domodossola": "IT",  # Border station, but administratively Italian
    "Ala": "IT",  # Trentino-Alto Adige
    # Czech stations
    "Bohusovice nad Ohri": "CZ",
    "Straz nad Ohri": "CZ",
    "Stare Mesto u Uherského Hradiste": "CZ",
    "Zelezna Ruda mesto": "CZ",
    "Zelezna Ruda centrum": "CZ",
    # Slovak stations
    "Bratislava hl.st.": "SK",  # Main station of Bratislava, Slovakia (not Czechia)
    # Polish stations
    "Rybnik(CZ)": "PL",  # Rybnik is in Silesia, Poland (not Czechia, despite CZ suffix)
    "Stare Kurowo": "PL",  # Lubusz Voivodeship, Poland (not Slovenia)
    "Bardo Slaskie": "PL",
    "Bardo Przylek": "PL",
    "Stare Bielice": "PL",
    "Wilkowo Swiebodzinskie": "PL",
    "Trzciana": "PL",
    "Gorki Noteckie": "PL",
    "Jablonowo Pomorskie": "PL",
    # German stations
    "Am Hart, München": "DE",
    "Eschenlohe": "DE",  # Eschenlohe, Bavaria, Germany (not Liechtenstein)
    "St Goarshausen": "DE",
    "St Ilgen-Sandhausen": "DE",
    "St Ingbert": "DE",
    "Flughafen BER": "DE",
    "Flughafen BER (S-Bahn)": "DE",
    "Neuhaus, Oberteuringen": "DE",  # Baden-Württemberg
    "Augustin-Bea-Straße/Schule, Oberteuringen": "DE",  # Baden-Württemberg
    # UK stations
    "Beaulieu Park": "GB",  # Near Chelmsford, Essex, UK
    # Danish stations
    "Koebenhavns Lufthavn st": "DK",  # Copenhagen Airport
    # Swedish stations
    "Visby st": "SE",  # Visby on Gotland, Sweden (not Denmark)
    # Hungarian stations
    "Komarom": "HU",  # Hungarian side (Slovak side is Komárno)
    "Acs": "HU",
    # Croatian stations
    "Novi Dvori": "HR",
    "Veliko Trgovisce": "HR",
    "Zabok": "HR",
    "Gradec stajaliste": "HR",  # Croatian spelling "stajalište", near Zagreb (not Austria)
    "Velika Ves": "HR",  # Border settlement near Krapina, Croatia (not Poland)
    # Ukrainian stations
    "Svyatoshino": "UA",
    # Romanian stations
    "Carbunesti": "RO",
    "Campina": "RO",
    "Ilia": "RO",  # Romania (not Bulgaria)
    # Slovenian stations
    "Plave": "SI",
    "Grahovo": "SI",  # Grahovo ob Bači, Slovenia (not Bosnia and Herzegovina)
    # Bulgarian stations
    # Liechtenstein stations
    "Schaanwald, Zollamt": "LI",  # Liechtenstein (not Switzerland)
    "Schaanwald, Zuschg": "LI",  # Liechtenstein (not Switzerland)
    "Schaanwald, Waldstrasse": "LI",  # Liechtenstein (not Switzerland)
    "Schaanwald, Industrie": "LI",  # Liechtenstein (not Switzerland)
    "Forst Hilti": "LI",  # Liechtenstein (not Switzerland)
    "Eschen, Kohlplatz": "LI",  # Eschen, Liechtenstein (not Switzerland)
    "Bendern, Widagass": "LI",  # Bendern, Liechtenstein (not Switzerland)
    "Schaan, Rosengarten": "LI",  # Schaan, Liechtenstein (not Switzerland)
    "Vaduz, Mühleholz": "LI",  # Vaduz, Liechtenstein (not Switzerland)
    "Triesen, Messina": "LI",  # Triesen, Liechtenstein (not Switzerland)
    "Mauren FL, Freihof": "LI",  # Mauren, Liechtenstein (FL = Fürstentum Liechtenstein, not Switzerland)
    "Mauren FL, Post": "LI",  # Mauren, Liechtenstein (FL = Fürstentum Liechtenstein, not Switzerland)
    "Mauren FL, Freiendorf": "LI",  # Mauren, Liechtenstein (FL = Fürstentum Liechtenstein, not Switzerland)
    "Mauren FL, Wegacker": "LI",  # Mauren, Liechtenstein (FL = Fürstentum Liechtenstein, not Switzerland)
    "Mauren FL, Fallsgass": "LI",  # Mauren, Liechtenstein (FL = Fürstentum Liechtenstein, not Switzerland)
}


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def normalize_station(name: str) -> str:
    """Normalize station name: lowercase, remove punctuation, filter generic tokens."""
    s = str(name).lower()
    s = re.sub(r"[()\[\],.-]+", " ", s)
    tokens = [t for t in s.split() if t and t not in GENERIC_TOKENS]
    if not tokens:
        tokens = s.split()
    joined = "".join(tokens)
    return re.sub(r"[^a-z0-9]+", "", joined)


def extract_tokens(name: str) -> List[str]:
    """Extract meaningful tokens from station name (without generic tokens)."""
    s = str(name).lower()
    s = re.sub(r"[()\[\],.-]+", " ", s)
    return [t for t in s.split() if t and t not in GENERIC_TOKENS]


# ============================================================================
# LOOKUP BUILDERS
# ============================================================================

def build_station_country_lookup(stations_df: pd.DataFrame) -> Dict[str, str]:
    """Build station-name → country mapping from stations dataset."""
    station_country_df = (
        stations_df[["name", "country"]]
        .dropna(subset=["name", "country"])
        .assign(norm_name=lambda d: d["name"].map(normalize_station))
    )
    
    station_country_df = station_country_df[station_country_df["country"].isin(EU_CODES)]
    
    station_country_lookup = (
        station_country_df
        .drop_duplicates("norm_name")
        .set_index("norm_name")["country"]
        .to_dict()
    )
    
    return station_country_lookup


def build_city_geonames_lookup(cities_file: Path) -> Dict[str, str]:
    """Build Europe-focused GeoNames city index for country mapping."""
    city_candidates = {}
    
    with open(cities_file, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 15:
                continue

            name = parts[1]
            ascii_name = parts[2]
            alt_names = parts[3] if parts[3] else ""
            country_code = parts[8]
            try:
                population = int(parts[14]) if parts[14] else 0
            except ValueError:
                population = 0

            # Keep only European countries
            if country_code not in EU_CODES:
                continue

            names_to_add = {name}
            if ascii_name and ascii_name != name:
                names_to_add.add(ascii_name)
            if alt_names:
                for alt in alt_names.split(","):
                    alt = alt.strip()
                    if alt:
                        names_to_add.add(alt)

            for nm in names_to_add:
                norm = normalize_station(nm)
                if not norm:
                    continue
                city_candidates.setdefault(norm, []).append((country_code, population))

    city_geonames_lookup = {}
    for norm, candidates in city_candidates.items():
        best_cc, _ = max(candidates, key=lambda x: x[1])  # highest population European city
        city_geonames_lookup[norm] = best_cc

    return city_geonames_lookup


# ============================================================================
# COUNTRY DETECTION
# ============================================================================

def correct_station_country(station_name: str, country_code: Optional[str]) -> Optional[str]:
    """Apply known corrections for frequently mis-mapped stations."""
    station_name_clean = station_name.strip() if station_name else station_name
    
    # Check exact match first
    if station_name in STATION_CORRECTIONS:
        return STATION_CORRECTIONS[station_name]
    if station_name_clean in STATION_CORRECTIONS:
        return STATION_CORRECTIONS[station_name_clean]
    
    # Check partial matches for common patterns
    if "Genève" in station_name or "Geneva" in station_name:
        return "CH"
    if "TGV" in station_name and "Rhône" in station_name:
        return "FR"
    if " b.Wien" in station_name or " bei Wien" in station_name:
        return "AT"
    if "nad Ohri" in station_name:
        return "CZ"
    if "Zelezna Ruda" in station_name:
        return "CZ"
    if " u " in station_name and "Hradiste" in station_name:
        return "CZ"
    if "Ponte" in station_name and ("Gardena" in station_name or "Marco" in station_name):
        return "IT"
    if "Domodossola" in station_name:
        return "IT"
    if station_name == "Ala" or (station_name.startswith("Ala") and len(station_name) <= 5):
        return "IT"  # Trentino-Alto Adige
    if "Bardo" in station_name and ("Slaskie" in station_name or "Przylek" in station_name):
        return "PL"
    if "Wilkowo Swiebodzinskie" in station_name:
        return "PL"
    if "Trzciana" in station_name:
        return "PL"
    if "Gorki Noteckie" in station_name:
        return "PL"
    if "Jablonowo Pomorskie" in station_name:
        return "PL"
    if "Flughafen BER" in station_name:
        return "DE"
    if "Koebenhavns Lufthavn" in station_name:
        return "DK"
    if "Zabok" in station_name:
        return "HR"
    if "Svyatoshino" in station_name:
        return "UA"
    if "Carbunesti" in station_name:
        return "RO"
    if "München" in station_name:
        return "DE"
    if "St Goarshausen" in station_name:
        return "DE"
    if "St Ilgen-Sandhausen" in station_name:
        return "DE"
    if "St Ingbert" in station_name:
        return "DE"
    if "Oberteuringen" in station_name:
        return "DE"  # Baden-Württemberg
    if "Beaulieu Park" in station_name:
        return "GB"
    if "Komarom" in station_name:
        return "HU"
    if station_name == "Acs":
        return "HU"
    if "Novi Dvori" in station_name:
        return "HR"
    if "Veliko Trgovisce" in station_name:
        return "HR"
    if "Plave" in station_name:
        return "SI"
    if station_name == "Ilia":
        return "BG"
    if "Bruxelles-Midi" in station_name or "Bruxelles Midi" in station_name:
        return "BE"
    if "Bons-en-Chablais" in station_name:
        return "FR"
    if "Bartenheim" in station_name:
        return "FR"
    if "St-Léonard" in station_name and "Switzerland" not in station_name:
        return "CH"
    # Catch all Mauren FL variants (FL = Fürstentum Liechtenstein)
    if "Mauren FL" in station_name:
        return "LI"  # All Mauren FL stations are in Liechtenstein, not Switzerland
    # Catch Eschen variants (but not Eschenlohe which is in Germany)
    if "Eschen" in station_name and "Eschenlohe" not in station_name and "Liechtenstein" not in station_name:
        return "LI"  # Eschen is in Liechtenstein, not Switzerland (but Eschenlohe is in Germany)
    # Catch Bendern variants (Bendern is in Liechtenstein)
    if "Bendern" in station_name:
        return "LI"  # Bendern is in Liechtenstein, not Switzerland
    # Catch Schaan variants (Schaan is in Liechtenstein)
    if "Schaan" in station_name and "Schaanwald" not in station_name and "Liechtenstein" not in station_name:
        return "LI"  # Schaan is in Liechtenstein, not Switzerland (but Schaanwald is handled separately)
    # Catch Vaduz variants (Vaduz is the capital of Liechtenstein)
    if "Vaduz" in station_name and "Liechtenstein" not in station_name:
        return "LI"  # Vaduz is in Liechtenstein, not Switzerland
    # Catch Triesen variants (Triesen is in Liechtenstein)
    if "Triesen" in station_name and "Liechtenstein" not in station_name:
        return "LI"  # Triesen is in Liechtenstein, not Switzerland
    
    return country_code


def map_station_to_country_europe(
    station_name: str,
    station_country_lookup: Dict[str, str],
    city_geonames_lookup: Dict[str, str]
) -> Optional[str]:
    """
    Map a station name to a European country code, using multiple strategies.
    
    Args:
        station_name: The station name to map
        station_country_lookup: Lookup from normalized station names to country codes
        city_geonames_lookup: Lookup from normalized city names to country codes
        
    Returns:
        Country code (2-letter ISO) or None if not found
    """
    norm = normalize_station(station_name)
    if not norm:
        return None

    # 0) Check for explicit country codes in parentheses (highest priority)
    for pattern, country_code in COUNTRY_CODE_PATTERNS.items():
        if pattern in station_name:
            if country_code in EU_CODES:
                return country_code

    # 0.5) Check for language-specific patterns
    for pattern, country_code in LANGUAGE_PATTERNS.items():
        if pattern in station_name:
            if country_code in EU_CODES:
                return country_code

    # 1) Try stations dataset (authoritative)
    cc = station_country_lookup.get(norm)
    if cc:
        return cc

    # 2) Try full normalized name first (most specific)
    if norm and len(norm) >= 4 and norm in city_geonames_lookup:
        return city_geonames_lookup[norm]

    # 3) Fall back to GeoNames Europe-only lookup (using improved token matching)
    tokens = extract_tokens(station_name)
    if not tokens:
        return None
    
    # Prioritize the first/main token (usually the city name) - most reliable
    if len(tokens) > 0:
        first_token = tokens[0]
        if len(first_token) >= 3 and first_token not in AMBIGUOUS_SHORT_TOKENS:
            norm_first = re.sub(r"[^a-z0-9]+", "", first_token)
            if norm_first and len(norm_first) >= 3 and norm_first in city_geonames_lookup:
                return city_geonames_lookup[norm_first]
    
    # Then try longer token combinations (prioritize longer matches)
    max_tokens = min(4, len(tokens))
    for token_count in range(max_tokens, 0, -1):
        for i in range(len(tokens) - token_count + 1):
            token_group = tokens[i:i+token_count]
            # Skip if any token is too short/ambiguous
            if any(len(t) < 3 or t in AMBIGUOUS_SHORT_TOKENS for t in token_group):
                continue
            
            combined = "".join(token_group)
            norm_combined = re.sub(r"[^a-z0-9]+", "", combined)
            if norm_combined and len(norm_combined) >= 4 and norm_combined in city_geonames_lookup:
                return city_geonames_lookup[norm_combined]
    
    # As last resort, try individual tokens (but avoid ambiguous short ones)
    for token in tokens:
        if len(token) < 4 or token in AMBIGUOUS_SHORT_TOKENS:
            continue
        norm_token = re.sub(r"[^a-z0-9]+", "", token)
        if norm_token and len(norm_token) >= 4 and norm_token in city_geonames_lookup:
            return city_geonames_lookup[norm_token]

    return None


# ============================================================================
# DATA PROCESSING
# ============================================================================

def parse_legs(legs_value) -> List[Tuple[str, List]]:
    """Parse raw legs JSON string into [(station_name, [dep_time, arr_time])] list."""
    if legs_value is None or (isinstance(legs_value, float) and pd.isna(legs_value)):
        return []

    if isinstance(legs_value, str):
        try:
            legs_list = json.loads(legs_value)
        except Exception:
            return []
    else:
        legs_list = legs_value

    # Expect structure [[station_name, [dep, arr]], ...]
    parsed = []
    for item in legs_list:
        if not isinstance(item, list) or len(item) != 2:
            continue
        station_name, times = item
        parsed.append((station_name, times))
    return parsed


def process_routes(
    routes_df: pd.DataFrame,
    station_country_lookup: Dict[str, str],
    city_geonames_lookup: Dict[str, str]
) -> pd.DataFrame:
    """
    Process all routes and detect countries for all stops.
    Maintains the original route structure with legs array containing countries.
    Automatically filters out stops that couldn't be mapped (unmapped stops are
    likely unimportant minor stations).
    
    Returns:
        DataFrame with same columns as input, plus legs_with_countries column
        where each stop is: [("station_name", "country"), [dep_time, arr_time]]
        Only stops with successfully detected countries are included.
    """
    results = []
    
    for route_idx, row in routes_df.iterrows():
        # Parse legs
        legs = parse_legs(row["legs"])
        
        # Process each stop and add country
        # Filter out stops that couldn't be mapped (they're likely unimportant minor stations)
        legs_with_countries = []
        for station_name, times in legs:
            # Detect country
            country_code = map_station_to_country_europe(
                station_name,
                station_country_lookup,
                city_geonames_lookup
            )
            
            # Apply corrections
            if country_code:
                country_code = correct_station_country(station_name, country_code)
            
            country_label = COUNTRY_LABELS.get(country_code, country_code) if country_code else None
            
            # Only include stops that have a country mapped (filter out unmapped minor stations)
            if country_label is not None:
                # Create stop with country: [("station_name", "country"), [dep_time, arr_time]]
                stop_with_country = [
                    (station_name, country_label),
                    times
                ]
                legs_with_countries.append(stop_with_country)
        
        # Create result row with all original columns plus legs_with_countries
        result_row = {
            "route_origin": row.get("route_origin", ""),
            "route_destination": row.get("route_destination", ""),
            "search_date": row.get("search_date", ""),
            "departure_time": row.get("departure_time", ""),
            "arrival_time": row.get("arrival_time", ""),
            "legs_with_countries": json.dumps(legs_with_countries),
        }
        results.append(result_row)
    
    return pd.DataFrame(results)


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution function."""
    print("=" * 70)
    print("Country Detection for Train Route Stops")
    print("=" * 70)
    print()
    
    # Check input files exist
    if not ROUTES_FILE.exists():
        print(f"ERROR: Routes file not found: {ROUTES_FILE}")
        sys.exit(1)
    if not STATIONS_FILE.exists():
        print(f"ERROR: Stations file not found: {STATIONS_FILE}")
        sys.exit(1)
    if not CITIES_FILE.exists():
        print(f"ERROR: Cities file not found: {CITIES_FILE}")
        sys.exit(1)
    
    # Load data
    print("Loading data files...")
    print(f"  - Routes: {ROUTES_FILE}")
    routes_df = pd.read_csv(ROUTES_FILE)
    print(f"    Loaded {len(routes_df)} routes")
    
    print(f"  - Stations: {STATIONS_FILE}")
    stations_df = pd.read_csv(STATIONS_FILE, low_memory=False)
    print(f"    Loaded {len(stations_df)} stations")
    
    print(f"  - Cities: {CITIES_FILE}")
    
    # Build lookups
    print("\nBuilding lookup tables...")
    print("  - Building station country lookup...")
    station_country_lookup = build_station_country_lookup(stations_df)
    print(f"    Built lookup with {len(station_country_lookup)} entries")
    
    print("  - Building GeoNames city lookup...")
    city_geonames_lookup = build_city_geonames_lookup(CITIES_FILE)
    print(f"    Built lookup with {len(city_geonames_lookup)} entries")
    
    # Process routes
    print("\nProcessing routes and detecting countries...")
    results_df = process_routes(routes_df, station_country_lookup, city_geonames_lookup)
    
    # Statistics - count from original data and filtered results
    total_stops_original = 0
    total_stops_filtered = 0
    
    # Count original stops
    for _, row in routes_df.iterrows():
        legs = parse_legs(row["legs"])
        total_stops_original += len(legs)
    
    # Count filtered stops (kept stops)
    for legs_json in results_df["legs_with_countries"]:
        legs = json.loads(legs_json)
        total_stops_filtered += len(legs)
    
    unmapped = total_stops_original - total_stops_filtered
    mapped = total_stops_filtered
    
    print(f"\nProcessing complete!")
    print(f"  Total routes processed: {len(results_df)}")
    print(f"  Total stops in original data: {total_stops_original}")
    print(f"  Stops with country detected (kept): {mapped} ({100 * mapped / total_stops_original:.2f}%)")
    print(f"  Stops without country (filtered out): {unmapped} ({100 * unmapped / total_stops_original:.2f}%)")
    
    # Save results
    print(f"\nSaving results to: {OUTPUT_FILE}")
    results_df.to_csv(OUTPUT_FILE, index=False)
    print("Done!")
    
    # Show sample of filtered stops (if any)
    if unmapped > 0:
        print(f"\nSample of filtered stops (first 10):")
        print("  (These stops were removed because they couldn't be mapped to a country)")
        count = 0
        # We need to check the original data to see what was filtered
        for route_idx, row in routes_df.iterrows():
            legs = parse_legs(row["legs"])
            for station_name, times in legs:
                country_code = map_station_to_country_europe(
                    station_name,
                    station_country_lookup,
                    city_geonames_lookup
                )
                if country_code:
                    country_code = correct_station_country(station_name, country_code)
                country_label = COUNTRY_LABELS.get(country_code, country_code) if country_code else None
                if country_label is None:
                    print(f"  - {station_name} (Route: {row['route_origin']} -> {row['route_destination']})")
                    count += 1
                    if count >= 10:
                        break
            if count >= 10:
                break
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
