import requests
from bs4 import BeautifulSoup
import urllib.parse
import logging
import time
import random
import re

logger = logging.getLogger(__name__)

def fetch_jobs(search_config):
    """
    Fetches job listings from LinkedIn Guest API for a specific search configuration.
    """
    keywords = urllib.parse.quote(search_config.get("keywords", ""))
    geoId = search_config.get("geoId", "92000000")
    f_TPR = "r3600"  # Force past hour
    f_WT = search_config.get("f_WT", "2")
    sortBy = search_config.get("sortBy", "DD")
    
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={keywords}&geoId={geoId}&f_TPR={f_TPR}&f_WT={f_WT}&sortBy={sortBy}&start=0"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            logger.warning(f"LinkedIn rate limit hit (429) for keywords '{keywords}'.")
            return []
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching jobs from LinkedIn for '{keywords}': {e}")
        return []
        
    soup = BeautifulSoup(response.text, "html.parser")
    job_cards = soup.find_all("div", class_="base-card")
    
    jobs = []
    for card in job_cards:
        try:
            urn = card.get("data-entity-urn", "")
            job_id = urn.split(":")[-1] if urn else ""
            if not job_id:
                continue
                
            title_elem = card.find("h3", class_="base-search-card__title")
            title = title_elem.text.strip() if title_elem else "Unknown Title"
            
            company_elem = card.find("h4", class_="base-search-card__subtitle")
            company = company_elem.text.strip() if company_elem else "Unknown Company"
            
            location_elem = card.find("span", class_="job-search-card__location")
            location_text = location_elem.text.strip() if location_elem else "Unknown Location"
            
            link_elem = card.find("a", class_="base-card__full-link")
            link = link_elem.get("href", "") if link_elem else f"https://www.linkedin.com/jobs/view/{job_id}"
            
            time_elem = card.find("time")
            posted_time = time_elem.text.strip() if time_elem else ""
            
            time_lower = posted_time.lower()
            is_old = any(x in time_lower for x in ["day", "week", "month", "year", "hours"])
            if is_old:
                logger.info(f"Skipped old job: {title} ({posted_time})")
                continue
            
            
            if "?" in link:
                link = link.split("?")[0]
                
            jobs.append({
                "id": job_id,
                "title": title,
                "company": company,
                "location": location_text,
                "link": link,
                "posted_time": posted_time,
                "keywords": search_config.get("keywords", "")
            })
        except Exception as e:
            logger.error(f"Error parsing a job card: {e}")
            continue
            
    return jobs

def fetch_job_detail_to_get_company_url(job_id):
    """
    Fetches the specific job posting to extract the company's LinkedIn URL.
    """
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    try:
        # Avoid hitting API too rapidly
        time.sleep(random.uniform(0.5, 1.5))
        
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch job detail for {job_id}. Status: {response.status_code}")
            return {"company_url": None, "applicants": None}
            
        text = response.text
        soup = BeautifulSoup(text, "html.parser")
        
        # Extract applicants
        applicants = None
        app_match = re.search(r'(\d+)\s+applicants', text, re.IGNORECASE)
        if app_match:
            applicants = int(app_match.group(1))
        else:
            app_match = re.search(r'Over\s+(\d+)\s+applicants', text, re.IGNORECASE)
            if app_match:
                applicants = int(app_match.group(1)) + 1
            else:
                app_match = re.search(r'(\d+)\s+people clicked apply', text, re.IGNORECASE)
                if app_match:
                    applicants = int(app_match.group(1))
        
        # Search for any link that contains '/company/'
        company_url = None
        company_link_tag = soup.find("a", href=lambda href: href and "/company/" in href)
        if company_link_tag:
            company_url = company_link_tag.get("href")
            # Remove any trailing query parameters from the company URL
            if "?" in company_url:
                company_url = company_url.split("?")[0]
                
        return {"company_url": company_url, "applicants": applicants}
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching job detail {job_id}: {e}")
        return {"company_url": None, "applicants": None}
