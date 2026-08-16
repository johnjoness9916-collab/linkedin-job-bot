import sqlite3
import logging
import requests
from bs4 import BeautifulSoup
import time
import random
import re
from datetime import datetime, timedelta

import os

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "seen_jobs.db")

def init_company_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS company_cache (
            company_url TEXT PRIMARY KEY,
            company_name TEXT,
            followers INT,
            size TEXT,
            last_checked TIMESTAMP
        )
    ''')
    try:
        cursor.execute("ALTER TABLE company_cache ADD COLUMN size TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def parse_kmb(text):
    if not text:
        return None
    text = text.upper().replace(",", "").strip()
    multiplier = 1
    if "K" in text:
        multiplier = 1000
        text = text.replace("K", "")
    elif "M" in text:
        multiplier = 1000000
        text = text.replace("M", "")
    elif "B" in text:
        multiplier = 1000000000
        text = text.replace("B", "")
    try:
        return int(float(text) * multiplier)
    except ValueError:
        logger.error(f"Failed to parse follower count from text: '{text}'")
        return None

def get_cached_company(company_url, cache_days):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT followers, size, last_checked FROM company_cache WHERE company_url = ?", (company_url,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        followers, size, last_checked_str = result
        last_checked = datetime.fromisoformat(last_checked_str)
        if datetime.now() - last_checked <= timedelta(days=cache_days):
            return {"followers": followers, "size": size}
    return None

def update_company_cache(company_url, company_name, followers, size):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute('''
        INSERT INTO company_cache (company_url, company_name, followers, size, last_checked)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(company_url) DO UPDATE SET 
            company_name=excluded.company_name,
            followers=excluded.followers,
            size=excluded.size,
            last_checked=excluded.last_checked
    ''', (company_url, company_name, followers, size, now))
    conn.commit()
    conn.close()

def get_company_info(company_url, cache_days=7):
    if not company_url or not company_url.startswith("https://www.linkedin.com/company/"):
        return {"followers": 0, "size": None}
        
    # Check cache first
    cached_info = get_cached_company(company_url, cache_days)
    if cached_info is not None:
        return cached_info
        
    base_url = company_url.split("?")[0].rstrip("/")
    variants = [
        base_url,
        base_url + "/about/",
        base_url + "/posts/?feedView=all"
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    followers = None
    size = None
    company_name = ""
    
    for url in variants:
        time.sleep(1.0)
        try:
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch company page {url}. Status: {response.status_code}")
                continue
                
            text = response.text
            soup = BeautifulSoup(text, "html.parser")
            page_text = soup.get_text(separator=' ')
            
            # Extract followers
            if followers is None:
                match1 = re.search(r'"followerCount":\s*(\d+)', text)
                if match1:
                    followers = int(match1.group(1))
                else:
                    match2 = re.search(r'([\d,\.]+[KkMmBb]?)\s+followers', page_text, re.IGNORECASE)
                    if match2:
                        followers = parse_kmb(match2.group(1))
            
            # Extract size
            if size is None:
                smatch1 = re.search(r'Company size.*?(\d+\s*-\s*\d+|\d+\+?)\s*employees', page_text, re.IGNORECASE)
                smatch2 = re.search(r'"staffCountRange".*?"(\d+-\d+)"', text, re.IGNORECASE)
                smatch3 = re.search(r'(\d+\s*-\s*\d+)\s*employees', page_text, re.IGNORECASE)
                
                if smatch1:
                    size = smatch1.group(1).replace(" ", "")
                elif smatch2:
                    size = smatch2.group(1).replace(" ", "")
                elif smatch3:
                    size = smatch3.group(1).replace(" ", "")
                    
            if followers is not None and size is not None:
                # Try to get company name from page
                company_name_elem = soup.find("h1")
                company_name = company_name_elem.text.strip() if company_name_elem else ""
                break
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching company page {url}: {e}")
            continue

    if followers is not None or size is not None:
        update_company_cache(company_url, company_name, followers, size)
        return {"followers": followers, "size": size}
    else:
        logger.info(f"Followers and size unknown for {company_url}")
        update_company_cache(company_url, "", None, None)
        return {"followers": None, "size": None}
