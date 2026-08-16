import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler
from scraper import fetch_jobs, fetch_job_detail_to_get_company_url
from telegram_sender import (
    start_command, status_command, stop_command, setfilter_command, 
    send_job_to_telegram, load_config
)
from company_checker import init_company_db, get_company_info

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = os.getenv("DB_PATH", "seen_jobs.db")

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def init_db():
    """Initializes the SQLite database and seen table."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            timestamp DATETIME
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def is_job_seen(job_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM seen WHERE id = ?", (job_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_job_seen(job_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO seen (id, timestamp) VALUES (?, ?)", (job_id, datetime.now()))
    conn.commit()
    conn.close()

def get_all_subscribers():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT chat_id FROM subscribers")
        subs = [row[0] for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        subs = []
    conn.close()
    return subs

def is_blacklisted(title, blacklist_words):
    title_lower = title.lower()
    for word in blacklist_words:
        if word.lower() in title_lower:
            return True
    return False

async def scrape_job_task(context):
    """The background task that runs periodically."""
    config = load_config()
    logger.info("Running job scraper task...")
    
    searches = config.get("searches", [])
    blacklist = config.get("blacklist_words", [])
    max_followers = config.get("max_company_followers", 1000)
    cache_days = config.get("company_cache_days", 7)
    
    all_jobs = []
    
    # 1. Fetch from all parallel searches
    for search_config in searches:
        logger.info(f"Fetching jobs for keywords: {search_config.get('keywords')}")
        jobs = await asyncio.to_thread(fetch_jobs, search_config)
        all_jobs.extend(jobs)
        
    # 2. Dedup by job ID within the fetched batch
    unique_jobs = {}
    for job in all_jobs:
        if job['id'] not in unique_jobs:
            unique_jobs[job['id']] = job
            
    jobs_found = len(unique_jobs)
    jobs_skipped = 0
    jobs_sent = 0
    
    for job_id, job in unique_jobs.items():
        if is_blacklisted(job['title'], blacklist):
            continue
            
        if not is_job_seen(job['id']):
            # It's a new job. Let's check company size.
            logger.info(f"Processing new job: {job['title']} at {job['company']}")
            
            job_detail = await asyncio.to_thread(fetch_job_detail_to_get_company_url, job['id'])
            
            if not job_detail:
                company_url = None
                applicants = None
            else:
                company_url = job_detail.get('company_url')
                applicants = job_detail.get('applicants')
                
            job['applicants'] = applicants
            max_applicants = config.get("max_applicants", 10)
            
            if applicants is not None and applicants > max_applicants:
                logger.info(f"Skipped {job['id']} - {applicants} applicants (>{max_applicants})")
                mark_job_seen(job['id'])
                continue

            if not company_url:
                logger.info(f"Could not find company URL for job {job['id']}. Skipping.")
                mark_job_seen(job['id'])
                continue
                
            info = await asyncio.to_thread(get_company_info, company_url, cache_days)
            followers = info.get('followers')
            size = info.get('size')
            
            # Small by followers - only if we KNOW it's small
            is_small_by_followers = (followers is not None and followers <= max_followers)
            # Small by size - only 0-1, 1-10, 2-10
            is_small_by_size = (size in ["0-1", "1-10", "2-10", "1-2", "2", "1"])
            
            should_send = False
            # If followers unknown, we ONLY send if size is proven 2-10. Otherwise SKIP.
            if followers is None:
                if is_small_by_size:
                    should_send = True
                else:
                    logger.info(f"Skipped large/unknown company: {job['company']}")
            else:
                if is_small_by_followers or is_small_by_size:
                    should_send = True
                else:
                    logger.info(f"Skipped {job['company']} with {followers} followers, size {size}")

            if should_send:
                job['followers'] = followers
                job['size'] = size
                
                subscribers = set(get_all_subscribers())
                if CHAT_ID:
                    try:
                        subscribers.add(int(CHAT_ID))
                    except ValueError:
                        pass
                
                any_success = False
                for sub_chat_id in subscribers:
                    success = await send_job_to_telegram(context.bot, sub_chat_id, job)
                    if success:
                        any_success = True
                
                if any_success:
                    mark_job_seen(job['id'])
                    jobs_sent += 1
                    
                    if 'jobs_sent_today' not in context.bot_data:
                        context.bot_data['jobs_sent_today'] = 0
                    context.bot_data['jobs_sent_today'] += 1
            else:
                jobs_skipped += 1
                mark_job_seen(job['id'])
                
    logger.info(f"Scraper task finished. Found {jobs_found} jobs, {jobs_skipped} skipped due to >{max_followers} followers, {jobs_sent} sent.")

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN must be set in .env")
        return
        
    init_db()
    init_company_db()
    
    config = load_config()
    interval = config.get("check_interval_seconds", 300)
    
    proxy_url = os.getenv("PROXY_URL")
    
    app_builder = ApplicationBuilder().token(BOT_TOKEN)
    if proxy_url:
        app_builder = app_builder.proxy_url(proxy_url).get_updates_proxy_url(proxy_url)
        
    application = app_builder.build()
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("setfilter", setfilter_command))
    
    # Setup background polling
    job_queue = application.job_queue
    job_queue.run_repeating(scrape_job_task, interval=interval, first=1)
    
    logger.info(f"Bot started. Polling LinkedIn every {interval} seconds.")
    application.run_polling()

if __name__ == '__main__':
    main()
