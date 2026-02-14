"""
Batch scraper for multiple routes.
Calls bahn_scraper_incremental.py for each route in both directions.

Usage:
    python batch_scraper.py

Routes are defined in the routes list below.
Each route is a string with format: "Origin - stops ↔ Destination - stops"
The script extracts the first stop before ↔ as origin and last stop after ↔ as destination.
The script will scrape both directions (A->B and B->A) for each route.
"""

import sys
from bahn_scraper_incremental import scrape_route_incremental

# Configuration
DATE = "22.01.2026"
TIME = "00:00"
HEADLESS = False  # Set to True for production

# Routes list - each route is a string with "↔" separator
# Format: "Origin - stops ↔ Destination - stops"
# Origin is the first stop before ↔, destination is the last stop after ↔
# Example: "London ↔ Paris" or "London - Calais ↔ Paris - Lyon"
routes = [
        "Lille ↔ Gent - Antwerpen",
        "Lille ↔ Namur - Liege",
        "Bruxelles - Namur ↔ Luxembourg",
        "Bruxelles ↔ Lille - Lyon - Marseille",
        "Bruxelles ↔ Koln - Frankfurt (Main)",
        "Bruxelles - Antwerpen ↔ Rotterdam - Amsterdam Zuid",
        "Bruxelles - Mechelen - Antwerpen ↔ Breda - Rotterdam",
        "Paris ↔ Bruxelles - Antwerpen - Rotterdam - Amsterdam Centraal",
        "Paris ↔ Bruxelles - Liege - Koln (- Dusseldorf - Essen - Dortmund)",
        "Paris ↔ Luxembourg",
        "Paris ↔ Frankfurt (Main)",
        "Paris - Strasbourg - Karlsruhe ↔ Stuttgart",
        "Paris - (Dijon) ↔ Basel - Zurich",
        "Paris ↔ Geneve",
        "Paris - (Lyon) ↔ Torino - Milano",
        "Amsterdam Centraal - Utrecht - Arnhem ↔ Dusseldorf - Koln - Frankfurt (Main)",
        "Amsterdam Centraal - Amersfoort ↔ Osnabruck - Hannover - Berlin",
        "Amsterdam Centraal - Rotterdam ↔ Bruxelles - LIlle - London",
        "Amsterdam Centraal - Rotterdam ↔ Antwerp - Bruxelles - Paris",
        "Amsterdam Zuid - Rotterdam ↔ Antwerp - Bruxelles",
        "Rotterdam - Breda ↔ Antwerp - Mechelen - Bruxelles",
        "Hamburg ↔ Odense - Kobenhavn",
        "Kobenhavn ↔ Malmo - Goteborg",
        "Oslo ↔ Goteborg",
        "Oslo ↔ Stockholm",
        "Praha - Brno ↔ Bratsislava - Budapest",
        "Praha - Brno ↔ Wien - Graz - Klagenfurt - Villach",
        "Praha - Plzen ↔ Regensburg - Munchen",
        "Praha - Ceske Budejovice ↔ Linz",
        "Praha - Pardubice - Lichkov ↔ Wroclaw - Poznan - Bydgoszcz - Gdansk - Sopot - Gdniya",
        "Berlin ↔ Poznan - Waszawa",
        "Hamburg - Berlin - Dresden ↔ Praha (- Bratislava - Budapest)",
        "Munchen - Salzburg - Linz - Wien ↔ Budapest",
        "Munchen - Innsbruck ↔ Bolzano - Verona ( - Bologna and Venezia)",
        "Munchen - Lindau ↔ St Gallen - Zurich",
        "Koln - Bonn - Koblenz - Mainz - Frankfurt (Main) - Nurnberg - Regensburg ↔ Linz - Wien",
        "(Frankfurt (Main) / Koln - Stuttgart) - Munchen ↔ Salzburg - Villach - Klagenfurt - Graz",
        "Stuttgart ↔ Zurich",
        "Berlin - Frankfurt (Main) - Freiburg ↔ Basel",
        "Hamburg - Bremen - Dortmund - Dusseldorf - Koln - Freiburg ↔ Basel",
        "Hamburg - Hannover - Frankfurt (Main) - Freiburg ↔ Basel",
        "Zurich ↔ St Anton - Innsbruck - Salzburg - Linz - Wien",
        "Zurich - Lugano ↔ Como - Milano",
        "Basel - Bern - Brig ↔ Stresa - Milano",
        "Geneve - Lausanne - Brig ↔ Stresa - Milano ( - Verona - Padova - Venezia)",
        "Stuttgart - Munich ↔ Udine – Venice",
        "Stuttgart - Munich ↔ Ljubljana",
        "Stuttgart - Munich ↔ Zagreb",
        "Stuttgart - Munich ↔ Vienna – Budapest",
        "Munich ↔ Hannover – Hamburg",
        "Munich and Innsbruck ↔ Amsterdam",
        "Munchen ↔ Florence - Roma*",
        "Munich - Salzburg - Linz - Vienna ↔ Warsaw",
        "Munich - Salzburg - Linz - Vienna ↔ Krakow",
        "Prague ↔ Warsaw",
        "Prague ↔ Kracow",
        "Zurich ↔ Vienna – Budapest",
        "Zurich - Basel ↔ Amsterdam",
        "Zurich ↔ Graz",
        "Zurich ↔ Ljubljana – Zagreb",
        "Zurich – Basel ↔ Berlin",
        "Zurich - Basel ↔ Hamburg",
        "Zurich - Basel - Freiburg - Karlsruhe ↔ Dresden - Decin - Prague",
        "Vienna – Linz ↔ Koln/Cologne - Amsterdam",
        "Vienna – Dresden - Berlin",
        "Vienna – Linz ↔ Hannover – Hamburg",
        "Vienna ↔ Firenze/Florence - Roma",
        "Vienna ↔ Udine – Venice",
        "Vienna ↔ Verona - Milano – Genova - La Spezia",
        "Vienna - Budapest ↔ Kiev",
        "Vienna - Budapest ↔ Bucharest",
        "Sofia ↔ Istanbul",
        "Stockholm ↔ Hamburg",
        "Budapest ↔ Warsaw",
        "Budapest ↔ Berlin",
    
]

def parse_route(route_string):
    """
    Parse a route string to extract origin and destination.
    
    Args:
        route_string: Route string in format "Origin - stops ↔ Destination - stops"
    
    Returns:
        tuple: (origin, destination) - extracted origin and destination stops
    """
    if "↔" not in route_string:
        raise ValueError(f"Route string must contain '↔' separator: {route_string}")
    
    parts = route_string.split("↔")
    if len(parts) != 2:
        raise ValueError(f"Route string must contain exactly one '↔' separator: {route_string}")
    
    origin_part = parts[0].strip()
    destination_part = parts[1].strip()
    
    # Extract first stop from origin part (before " - ")
    origin_stops = [s.strip() for s in origin_part.split(" - ")]
    origin = origin_stops[0] if origin_stops else origin_part
    
    # Extract last stop from destination part (after " - ")
    destination_stops = [s.strip() for s in destination_part.split(" - ")]
    destination = destination_stops[-1] if destination_stops else destination_part
    
    return (origin, destination)


def get_csv_filename(origin, destination, date_str):
    """
    Generate a CSV filename for a route.
    Format: {origin}_{destination}_{date}.csv
    """
    # Sanitize names for filename
    origin_clean = origin.replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")
    dest_clean = destination.replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")
    date_clean = date_str.replace(".", "_")
    return f"{origin_clean}_{dest_clean}_{date_clean}.csv"


def scrape_route_both_directions(route_string, date_str, time_str, headless=False):
    """
    Scrape a route in both directions.
    
    Args:
        route_string: Route string in format "Origin - stops ↔ Destination - stops"
        date_str: Date in format DD.MM.YYYY
        time_str: Time in format HH:MM
        headless: Whether to run browser in headless mode
    
    Returns:
        tuple: (forward_count, reverse_count) - number of trains scraped in each direction
    """
    try:
        origin, destination = parse_route(route_string)
    except ValueError as e:
        print(f"Warning: {e}, skipping.")
        return (0, 0)
    
    print(f"\n{'='*60}")
    print(f"Processing route: {route_string}")
    print(f"  Origin: {origin}")
    print(f"  Destination: {destination}")
    print(f"{'='*60}\n")
    
    # Forward direction: origin -> destination
    print(f"Direction 1: {origin} -> {destination}")
    csv_filename_forward = get_csv_filename(origin, destination, date_str)
    
    try:
        forward_count = scrape_route_incremental(
            origin=origin,
            destination=destination,
            date_str=date_str,
            initial_time_str=time_str,
            headless=headless,
            csv_filename=csv_filename_forward
        )
        print(f"✓ Forward direction completed: {forward_count} trains scraped")
        print(f"  Saved to: {csv_filename_forward}\n")
    except Exception as e:
        print(f"✗ Error in forward direction: {e}\n")
        forward_count = 0
    
    # Reverse direction: destination -> origin
    print(f"Direction 2: {destination} -> {origin}")
    csv_filename_reverse = get_csv_filename(destination, origin, date_str)
    
    try:
        reverse_count = scrape_route_incremental(
            origin=destination,
            destination=origin,
            date_str=date_str,
            initial_time_str=time_str,
            headless=headless,
            csv_filename=csv_filename_reverse
        )
        print(f"✓ Reverse direction completed: {reverse_count} trains scraped")
        print(f"  Saved to: {csv_filename_reverse}\n")
    except Exception as e:
        print(f"✗ Error in reverse direction: {e}\n")
        reverse_count = 0
    
    return (forward_count, reverse_count)


def main():
    """
    Main function to process all routes.
    """
    if not routes:
        print("No routes defined. Please add routes to the 'routes' list in batch_scraper.py")
        sys.exit(1)
    
    print(f"Batch Scraper Configuration:")
    print(f"  Date: {DATE}")
    print(f"  Time: {TIME}")
    print(f"  Headless: {HEADLESS}")
    print(f"  Total routes: {len(routes)}")
    print(f"  Total directions to scrape: {len(routes) * 2}\n")
    
    total_forward = 0
    total_reverse = 0
    successful_routes = 0
    failed_routes = 0
    
    for i, route in enumerate(routes, 1):
        print(f"\n[{i}/{len(routes)}] Processing route...")
        
        try:
            forward_count, reverse_count = scrape_route_both_directions(
                route_string=route,
                date_str=DATE,
                time_str=TIME,
                headless=HEADLESS
            )
            
            total_forward += forward_count
            total_reverse += reverse_count
            
            if forward_count > 0 or reverse_count > 0:
                successful_routes += 1
            else:
                failed_routes += 1
                
        except Exception as e:
            print(f"✗ Fatal error processing route {route}: {e}\n")
            failed_routes += 1
            import traceback
            traceback.print_exc()
    
    # Summary
    print(f"\n{'='*60}")
    print(f"BATCH SCRAPING SUMMARY")
    print(f"{'='*60}")
    print(f"Total routes processed: {len(routes)}")
    print(f"Successful routes: {successful_routes}")
    print(f"Failed routes: {failed_routes}")
    print(f"Total trains scraped (forward): {total_forward}")
    print(f"Total trains scraped (reverse): {total_reverse}")
    print(f"Total trains scraped (all): {total_forward + total_reverse}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
