"""
Incremental scraper for Deutsche Bahn international train schedules.
Extracts train schedules by incrementally advancing time when "later connections" button appears.
Optimized for speed - minimal delays, no random waits.

Approach:
1. Start with initial time (default: 00:00)
2. Search and extract trains from the first page of results
3. When "later connections" button appears:
   - Get the last departure time from extracted trains
   - Restart search with that time (don't click the button)
4. Continue until reaching next day or no more results
5. Save results incrementally to CSV

Usage:
    python bahn_scraper_incremental.py <origin> <destination> [date] [time] [csv_filename]
    
Example:
    python bahn_scraper_incremental.py "London" "Paris" "23.01.2026" "00:00" "output.csv"
"""

import time
import csv
import sys
import re
import json
import os
from datetime import datetime
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
import undetected_chromedriver as uc


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Import necessary functions from the original scraper
# We'll copy the essential functions but optimize them

def setup_driver(headless=False):
    """
    Setup and return an undetected Chrome WebDriver instance.
    Uses undetected-chromedriver to avoid bot detection.
    """
    options = uc.ChromeOptions()
    
    if headless:
        options.add_argument('--headless=new')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-extensions')
    options.add_argument('--lang=es-ES')
    options.add_argument('--start-maximized')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
    
    driver = uc.Chrome(options=options, version_main=None, use_subprocess=True)
    time.sleep(1)  # Minimal wait for browser initialization
    
    try:
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['es-ES', 'es', 'en-US', 'en']
                });
                window.chrome = { runtime: {} };
            '''
        })
    except:
        pass
    
    try:
        if not headless:
            driver.set_window_size(1920, 1080)
    except:
        pass
    
    time.sleep(0.5)  # Minimal wait
    return driver


def fill_search_form(driver, origin, destination, date_str="23.01.2026", time_str="00:00"):
    """
    Fill in the search form on Deutsche Bahn website.
    Optimized version with minimal delays.
    """
    url = "https://int.bahn.de/es/"
    driver.get(url)
    wait = WebDriverWait(driver, 10)
    
    try:
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Handle cookies quickly
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except:
            pass
        
        try:
            cookie_button = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Aceptar')] | //button[@id='onetrust-accept-btn-handler']"))
            )
            driver.execute_script("arguments[0].click();", cookie_button)
        except:
            pass
        
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "input")))
        
        # Find origin input
        origin_input = None
        origin_selectors = [
            "input[placeholder*='Inicio' i]",
            "input[aria-label*='Inicio' i]",
            "input[name*='origin' i]",
            "input[name*='von' i]",
            "input[name*='quickFinder' i]",
        ]
        
        for selector in origin_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    if elem.is_displayed() and not elem.get_attribute('disabled'):
                        origin_input = elem
                        break
                if origin_input:
                    break
            except:
                continue
        
        if not origin_input:
            raise Exception("Could not find origin input field")
        
        # Fill origin
        try:
            if origin_input.get_attribute('disabled'):
                driver.execute_script("arguments[0].removeAttribute('disabled');", origin_input)
            
            driver.execute_script("""
                var elem = arguments[0];
                elem.scrollIntoView({behavior: 'instant', block: 'center'});
                elem.focus();
                elem.click();
                elem.value = '';
            """, origin_input)
            
            driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, origin_input, origin)
            
            time.sleep(0.6)  # Minimal wait for autocomplete
        except Exception as e:
            raise
        
        # Select first autocomplete suggestion
        origin_selected = False
        try:
            suggestion_selectors = [
                "ul[role='listbox'] li[role='option']:first-child",
                "ul[role='listbox'] > li:first-child",
                "[role='listbox'] [role='option']:first-child",
            ]
            
            suggestion = None
            for selector in suggestion_selectors:
                try:
                    suggestions = driver.find_elements(By.CSS_SELECTOR, selector)
                    for sug in suggestions:
                        if sug.is_displayed():
                            suggestion = sug
                            break
                    if suggestion:
                        break
                except:
                    continue
            
            if suggestion:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});", suggestion)
                time.sleep(0.1)
                driver.execute_script("arguments[0].click();", suggestion)
                time.sleep(0.3)
                
                driver.execute_script("""
                    arguments[0].blur();
                    arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                """, origin_input)
                time.sleep(0.5)
                
                current_value = origin_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", origin_input)
                if current_value and len(current_value) > 0:
                    origin_selected = True
        except:
            pass
        
        if not origin_selected:
            try:
                origin_input.send_keys(Keys.ENTER)
                time.sleep(0.3)
            except:
                pass
        
        # Find destination input
        destination_input = None
        quick_selectors = [
            "input[name*='nach' i]",
            "input[name*='quickFinder' i]",
            "input[placeholder*='Destino' i]",
        ]
        
        for selector in quick_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    if elem.is_displayed() and elem != origin_input and not elem.get_attribute('disabled'):
                        destination_input = elem
                        break
                if destination_input:
                    break
            except:
                continue
        
        if not destination_input:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                if inp.is_displayed() and inp != origin_input:
                    name = (inp.get_attribute('name') or '').lower()
                    if ('nach' in name or 'destination' in name) and not inp.get_attribute('disabled'):
                        destination_input = inp
                        break
        
        if not destination_input:
            raise Exception("Could not find destination input field")
        
        # Fill destination
        try:
            if destination_input.get_attribute('disabled'):
                driver.execute_script("arguments[0].removeAttribute('disabled');", destination_input)
            
            driver.execute_script("""
                var elem = arguments[0];
                elem.scrollIntoView({behavior: 'instant', block: 'center'});
                elem.focus();
                elem.click();
                elem.value = '';
            """, destination_input)
            
            driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, destination_input, destination)
            
            time.sleep(0.6)  # Minimal wait for autocomplete
        except Exception as e:
            raise
        
        # Select first autocomplete suggestion
        destination_selected = False
        try:
            suggestion = None
            for selector in suggestion_selectors:
                try:
                    suggestions = driver.find_elements(By.CSS_SELECTOR, selector)
                    for sug in suggestions:
                        if sug.is_displayed():
                            suggestion = sug
                            break
                    if suggestion:
                        break
                except:
                    continue
            
            if suggestion:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});", suggestion)
                time.sleep(0.1)
                driver.execute_script("arguments[0].click();", suggestion)
                time.sleep(0.3)
                
                driver.execute_script("""
                    arguments[0].blur();
                    arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                """, destination_input)
                time.sleep(0.5)
                
                current_value = destination_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", destination_input)
                if current_value and len(current_value) > 0:
                    destination_selected = True
        except:
            pass
        
        if not destination_selected:
            try:
                destination_input.send_keys(Keys.ENTER)
                time.sleep(0.3)
            except:
                pass
        
        time.sleep(0.3)  # Wait for form to process
        
        # Set date and time
        try:
            date_time_selectors = [
                "//a[contains(text(), 'Cambiar ruta de ida')]",
                "//*[contains(text(), 'desde') and contains(text(), ':')]",
                "//a[contains(text(), 'Cambiar')]",
            ]
            
            date_link = None
            for selector in date_time_selectors:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    for elem in elements:
                        if elem.is_displayed():
                            date_link = elem
                            break
                    if date_link:
                        break
                except:
                    continue
            
            if date_link:
                driver.execute_script("arguments[0].click();", date_link)
                # Faster wait - just check for day elements instead of waiting for specific text
                try:
                    WebDriverWait(driver, 2).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='calendar-day'], [class*='day']"))
                    )
                except:
                    pass
                
                # Extract target day (skip month/year navigation since calendar opens on correct month/year)
                date_parts = date_str.split('.')
                target_day = int(date_parts[0])
                
                # Click on the day - optimized: find and click in one pass
                try:
                    day_clicked = False
                    day_elements = driver.find_elements(By.CSS_SELECTOR, 
                        ".db-web-date-picker-calendar-day, [class*='calendar-day'], [class*='day']")
                    
                    for elem in day_elements:
                        try:
                            if not elem.is_displayed():
                                continue
                            text = elem.text.strip()
                            if text.isdigit() and int(text) == target_day:  # Direct match, no need to collect all
                                classes = (elem.get_attribute('class') or '').lower()
                                aria_disabled = elem.get_attribute('aria-disabled')
                                aria_hidden = elem.get_attribute('aria-hidden')
                                
                                if (aria_disabled != 'true' and 
                                    aria_hidden != 'true' and
                                    'disabled' not in classes and 
                                    'other' not in classes):
                                    
                                    driver.execute_script("arguments[0].click();", elem)
                                    day_clicked = True
                                    break
                        except:
                            continue
                    
                    if day_clicked:
                        time.sleep(0.1)  # Minimal wait for selection to register
                except:
                    pass
                
                # Set time
                try:
                    # Wait for time inputs to be present (reduced timeout)
                    try:
                        WebDriverWait(driver, 1.5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "input[aria-label*='Hora' i], input.test-hours-input, input[class*='hours']"))
                        )
                    except:
                        pass
                    
                    time_parts = time_str.split(':')
                    target_hours = time_parts[0].zfill(2)
                    target_minutes = time_parts[1].zfill(2) if len(time_parts) > 1 else "00"
                    
                    # Find hour input - more comprehensive selectors
                    hour_input = None
                    hour_selectors = [
                        "input.test-hours-input",
                        "input[aria-label='Horas']",
                        "input[aria-label*='Hora' i]",
                        "input[class*='hours-input' i]",
                        "input[class*='hours']",
                        "//div[contains(@class, 'time-picker')]//input[contains(@class, 'hours')]",
                        "//div[contains(@class, 'db-web-time-picker')]//input[1]",
                        "//div[contains(@class, 'time-picker')]//input[1]",
                    ]
                    
                    for selector in hour_selectors:
                        try:
                            if selector.startswith("//"):
                                elements = driver.find_elements(By.XPATH, selector)
                            else:
                                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                            
                            for elem in elements:
                                if elem.is_displayed():
                                    # Double check it's actually an input
                                    tag = elem.tag_name.lower()
                                    if tag == 'input':
                                        hour_input = elem
                                        break
                            if hour_input:
                                break
                        except:
                            continue
                    
                    # Find minute input
                    minute_input = None
                    minute_selectors = [
                        "input.test-minutes-input",
                        "input[aria-label='Minutos']",
                        "input[aria-label*='Minuto' i]",
                        "input[class*='minutes-input' i]",
                        "input[class*='minutes']",
                        "//div[contains(@class, 'time-picker')]//input[contains(@class, 'minutes')]",
                        "//div[contains(@class, 'db-web-time-picker')]//input[2]",
                        "//div[contains(@class, 'time-picker')]//input[2]",
                    ]
                    
                    for selector in minute_selectors:
                        try:
                            if selector.startswith("//"):
                                elements = driver.find_elements(By.XPATH, selector)
                            else:
                                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                            
                            for elem in elements:
                                if elem.is_displayed() and elem != hour_input:
                                    # Double check it's actually an input
                                    tag = elem.tag_name.lower()
                                    if tag == 'input':
                                        minute_input = elem
                                        break
                            if minute_input:
                                break
                        except:
                            continue
                    
                    # Set hour value
                    if hour_input:
                        try:
                            # Remove readonly attribute if present
                            driver.execute_script("arguments[0].removeAttribute('readonly');", hour_input)
                            
                            # Method 1: Try JavaScript first
                            driver.execute_script("""
                                arguments[0].focus();
                                arguments[0].click();
                                arguments[0].select();
                                arguments[0].value = '';
                                arguments[0].value = arguments[1];
                                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                            """, hour_input, target_hours)
                            
                            time.sleep(0.2)
                            
                            # Verify
                            actual_hours = hour_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", hour_input)
                            if actual_hours != target_hours:
                                # Method 2: Try Selenium send_keys as fallback
                                try:
                                    hour_input.clear()
                                    hour_input.send_keys(target_hours)
                                    hour_input.send_keys(Keys.TAB)  # Trigger change event
                                    time.sleep(0.2)
                                    actual_hours = hour_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", hour_input)
                                except:
                                    pass
                            
                            if actual_hours != target_hours:
                                print(f"Warning: Could not set hours to {target_hours}, got {actual_hours}")
                        except Exception as e:
                            print(f"Error setting hours: {e}")
                    else:
                        print("Warning: Could not find hour input")
                    
                    # Set minute value
                    if minute_input:
                        try:
                            # Remove readonly attribute if present
                            driver.execute_script("arguments[0].removeAttribute('readonly');", minute_input)
                            
                            # Method 1: Try JavaScript first
                            driver.execute_script("""
                                arguments[0].focus();
                                arguments[0].click();
                                arguments[0].select();
                                arguments[0].value = '';
                                arguments[0].value = arguments[1];
                                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                            """, minute_input, target_minutes)
                            
                            time.sleep(0.2)
                            
                            # Verify
                            actual_minutes = minute_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", minute_input)
                            if actual_minutes != target_minutes:
                                # Method 2: Try Selenium send_keys as fallback
                                try:
                                    minute_input.clear()
                                    minute_input.send_keys(target_minutes)
                                    minute_input.send_keys(Keys.TAB)  # Trigger change event
                                    time.sleep(0.2)
                                    actual_minutes = minute_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", minute_input)
                                except:
                                    pass
                            
                            if actual_minutes != target_minutes:
                                print(f"Warning: Could not set minutes to {target_minutes}, got {actual_minutes}")
                        except Exception as e:
                            print(f"Error setting minutes: {e}")
                    else:
                        print("Warning: Could not find minute input")
                    
                    # Final verification
                    if hour_input and minute_input:
                        final_hours = hour_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", hour_input)
                        final_minutes = minute_input.get_attribute('value') or driver.execute_script("return arguments[0].value;", minute_input)
                        if final_hours != target_hours or final_minutes != target_minutes:
                            print(f"Warning: Time verification failed. Expected {target_hours}:{target_minutes}, got {final_hours}:{final_minutes}")
                except Exception as e:
                    print(f"Error setting time: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Click "Adoptar" button
                try:
                    time.sleep(0.1)
                    adoptar_button = None
                    
                    try:
                        adoptar_span = driver.find_element(By.XPATH, "//span[contains(text(), 'Adoptar')]")
                        adoptar_button = adoptar_span.find_element(By.XPATH, "./ancestor::button")
                    except:
                        pass
                    
                    if not adoptar_button:
                        adoptar_selectors = [
                            "button[class*='_button--commit']",
                            "//button[contains(@class, '_button--commit')]",
                        ]
                        
                        for selector in adoptar_selectors:
                            try:
                                if selector.startswith("//"):
                                    buttons = driver.find_elements(By.XPATH, selector)
                                else:
                                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                                
                                for btn in buttons:
                                    if btn.is_displayed():
                                        btn_text = btn.text.strip()
                                        if 'adoptar' in btn_text.lower():
                                            adoptar_button = btn
                                            break
                                if adoptar_button:
                                    break
                            except:
                                continue
                    
                    if adoptar_button:
                        WebDriverWait(driver, 2).until(EC.element_to_be_clickable(adoptar_button))
                        driver.execute_script("arguments[0].click();", adoptar_button)
                        time.sleep(0.3)
                except:
                    pass
        except:
            pass
        
        time.sleep(0.2)
        
        # Click search button
        search_button = None
        search_selectors = [
            "button.quick-finder_suche-button",
            "button.test-db-web-button",
            "//button[contains(@class, 'quick-finder_suche-button')]",
            "//button[contains(text(), 'Buscar')]",
        ]
        
        for selector in search_selectors:
            try:
                if selector.startswith("//"):
                    buttons = driver.find_elements(By.XPATH, selector)
                else:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                
                for btn in buttons:
                    if btn.is_displayed() and btn.is_enabled():
                        btn_text = btn.text.strip().lower()
                        if len(btn_text) == 0 or 'buscar' in btn_text or len(btn_text) < 3:
                            search_button = btn
                            break
                if search_button:
                    break
            except:
                continue
        
        if not search_button:
            raise Exception("Could not find search button")
        
        WebDriverWait(driver, 2).until(EC.element_to_be_clickable(search_button))
        driver.execute_script("arguments[0].click();", search_button)
        
        # Wait for results page
        try:
            WebDriverWait(driver, 10).until(
                lambda d: 'buchung/fahrplan/suche' in d.current_url or 'fahrplan' in d.current_url
            )
        except:
            pass
        
        time.sleep(0.5)  # Minimal wait for page load
        
    except Exception as e:
        print(f"Error filling search form: {e}")
        raise


# Import extract functions from the original scraper module
# These functions handle the complex extraction logic
try:
    import bahn_scraper
    extract_train_data = bahn_scraper.extract_train_data
    extract_stops_from_details = bahn_scraper.extract_stops_from_details
    extract_intermediate_stops = bahn_scraper.extract_intermediate_stops
except ImportError:
    # Fallback: if import fails, we'll need to define minimal versions
    def extract_train_data(driver, target_date="23.01.2026"):
        """Minimal extract - should import from bahn_scraper"""
        raise ImportError("Could not import extract functions from bahn_scraper")
    
    def extract_stops_from_details(driver, connection_element, train_arrival_time=None):
        """Minimal extract - should import from bahn_scraper"""
        raise ImportError("Could not import extract functions from bahn_scraper")
    
    def extract_intermediate_stops(driver, connection_element):
        """Minimal extract - should import from bahn_scraper"""
        raise ImportError("Could not import extract functions from bahn_scraper")


def has_more_results(driver):
    """Check if there's a 'conexiones posteriores' button."""
    try:
        more_button = driver.find_element(By.XPATH, "//*[contains(text(), 'conexiones posteriores')] | //*[contains(text(), 'Conexiones posteriores')]")
        return more_button.is_displayed()
    except:
        return False


def has_next_date_results(driver, target_date="23.01.2026"):
    """Check if results for the next date have appeared."""
    try:
        import re
        from datetime import datetime
        
        target_parts = target_date.split('.')
        target_day = int(target_parts[0])
        target_month = int(target_parts[1])
        target_year = int(target_parts[2])
        
        month_map = {
            'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12
        }
        
        page_text = driver.page_source
        date_pattern = r'(\d{1,2})\.\s*(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\s*(\d{4})'
        matches = re.findall(date_pattern, page_text, re.IGNORECASE)
        
        for match in matches:
            day = int(match[0])
            month_abbrev = match[1].lower()
            year = int(match[2])
            
            if month_abbrev in month_map:
                month = month_map[month_abbrev]
                found_date = datetime(year, month, day)
                target_datetime = datetime(target_year, target_month, target_day)
                
                if found_date > target_datetime:
                    return True
        
        return False
    except:
        return False


def get_last_departure_time(trains):
    """
    Get the last departure time from a list of trains.
    Returns time in HH:MM format, or None if no trains.
    """
    if not trains:
        return None
    
    def time_to_minutes(time_str):
        """Convert HH:MM to minutes for comparison"""
        if not time_str:
            return 0
        try:
            parts = time_str.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except:
            pass
        return 0
    
    last_time = None
    last_minutes = -1
    
    for train in trains:
        dep_time = train.get('departure_time', '')
        if dep_time:
            dep_minutes = time_to_minutes(dep_time)
            if dep_minutes > last_minutes:
                last_minutes = dep_minutes
                last_time = dep_time
    
    return last_time


def scrape_route_incremental(origin, destination, date_str="23.01.2026", initial_time_str="00:00", headless=False, csv_filename="train_schedules.csv"):
    """
    Scrape train schedules incrementally by time.
    When "later connections" button appears, stop and restart with last departure time.
    
    Args:
        origin: Origin station name
        destination: Destination station name
        date_str: Date in format DD.MM.YYYY
        initial_time_str: Initial time in format HH:MM (default: "00:00")
        headless: Whether to run browser in headless mode
        csv_filename: Output CSV filename
    
    Returns:
        int: Number of trains scraped
    """
    driver = setup_driver(headless=headless)
    all_trains = []
    seen_train_keys = set()
    current_time = initial_time_str
    iteration = 0
    max_iterations = 100  # Safety limit
    
    try:
        while iteration < max_iterations:
            iteration += 1
            print(f"Iteration {iteration}: Scraping from {current_time}...", end=" ", flush=True)
            
            # Fill search form with current time
            fill_search_form(driver, origin, destination, date_str, current_time)
            
            # Wait for results (minimal)
            time.sleep(0.5)
            
            # Extract trains from current page
            trains = extract_train_data(driver, target_date=date_str)
            
            if not trains:
                print("No trains found.")
                break
            
            # Add route information and deduplicate
            new_trains = []
            for train in trains:
                train['route_origin'] = origin
                train['route_destination'] = destination
                train['search_date'] = date_str
                
                unique_key = (
                    train.get('departure_time', ''),
                    train.get('arrival_time', '')
                )
                
                if unique_key not in seen_train_keys and unique_key[0]:
                    seen_train_keys.add(unique_key)
                    new_trains.append(train)
            
            if not new_trains:
                print("No new trains (all duplicates).")
                # Check if we should continue
                if has_more_results(driver):
                    last_time = get_last_departure_time(trains)
                    if last_time:
                        # Compare times properly
                        def time_to_minutes(t):
                            if not t: return 0
                            try:
                                p = t.split(':')
                                if len(p) == 2:
                                    return int(p[0]) * 60 + int(p[1])
                            except:
                                pass
                            return 0
                        
                        if time_to_minutes(last_time) > time_to_minutes(current_time):
                            current_time = last_time
                            print(f"  Continuing with time: {current_time}")
                            continue
                break
            
            all_trains.extend(new_trains)
            print(f"Found {len(new_trains)} new trains (total: {len(all_trains)})")
            
            # Save to CSV immediately (append mode)
            save_to_csv(new_trains, csv_filename, append=True)
            
            # Check if we've reached next date
            if has_next_date_results(driver, date_str):
                print("  Reached next day - finished.")
                break
            
            # Check if "later connections" button exists
            if has_more_results(driver):
                # Get last departure time and continue with that time
                last_time = get_last_departure_time(trains)
                if last_time:
                    # Compare times properly
                    def time_to_minutes(t):
                        if not t: return 0
                        try:
                            p = t.split(':')
                            if len(p) == 2:
                                return int(p[0]) * 60 + int(p[1])
                        except:
                            pass
                        return 0
                    
                    if time_to_minutes(last_time) > time_to_minutes(current_time):
                        current_time = last_time
                        print(f"  Found 'later connections' button. Continuing with time: {current_time}")
                        # Don't click the button - just restart the search with new time
                        continue
                    else:
                        # No new time available, we're done
                        print("  No new departure times available.")
                        break
                else:
                    # No departure time found, we're done
                    print("  No departure times found.")
                    break
            else:
                # No more results button - we're done
                print("  No more results available.")
                break
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error during scraping: {e}")
        raise
    finally:
        time.sleep(0.2)
        try:
            driver.quit()
        except:
            try:
                driver.close()
            except:
                pass
    
    return len(all_trains)


def save_to_csv(trains, filename="train_schedules.csv", append=False):
    """
    Save train data to CSV file.
    """
    if not trains:
        return
    
    fieldnames = [
        'route_origin',
        'route_destination',
        'search_date',
        'departure_time',
        'arrival_time',
        'legs'
    ]
    
    file_exists = os.path.exists(filename)
    if append:
        write_header = not file_exists or (file_exists and os.path.getsize(filename) == 0)
    else:
        write_header = True
    
    with open(filename, 'a' if append else 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        if write_header:
            writer.writeheader()
        
        for train in trains:
            row = {
                'route_origin': train.get('route_origin', ''),
                'route_destination': train.get('route_destination', ''),
                'search_date': train.get('search_date', ''),
                'departure_time': train.get('departure_time', ''),
                'arrival_time': train.get('arrival_time', ''),
                'legs': ''
            }
            
            if 'legs' in train and isinstance(train['legs'], list):
                row['legs'] = json.dumps(train['legs'], ensure_ascii=False)
            
            writer.writerow(row)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python bahn_scraper_incremental.py <origin> <destination> [date] [time] [csv_filename]")
        print("Example: python bahn_scraper_incremental.py 'London' 'Paris' '23.01.2026' '00:00' 'output.csv'")
        sys.exit(1)
    
    origin = sys.argv[1]
    destination = sys.argv[2]
    date_str = sys.argv[3] if len(sys.argv) > 3 else "23.01.2026"
    time_str = sys.argv[4] if len(sys.argv) > 4 else "00:00"
    csv_filename = sys.argv[5] if len(sys.argv) > 5 else "train_schedules.csv"
    
    print(f"Scraping route: {origin} → {destination}")
    print(f"Date: {date_str}, Initial time: {time_str}")
    print(f"Output file: {csv_filename}\n")
    
    try:
        num_trains = scrape_route_incremental(
            origin, 
            destination, 
            date_str=date_str, 
            initial_time_str=time_str,
            headless=False,  # Set to True for production
            csv_filename=csv_filename
        )
        print(f"\nScraping completed! Total trains: {num_trains}")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
