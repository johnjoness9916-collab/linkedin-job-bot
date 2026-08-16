import json
import sqlite3
import logging
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {}

def save_config(config_data):
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return False

def add_subscriber(chat_id):
    import os
    db_path = os.getenv("DB_PATH", "seen_jobs.db")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)
    config = load_config()
    searches = config.get("searches", [])
    
    search_text = ""
    for idx, s in enumerate(searches, 1):
        search_text += f"{idx}. Keywords: `{s.get('keywords')}`, GeoId: `{s.get('geoId')}`\n"
        
    welcome_msg = (
        "🤖 *Welcome to LinkedIn Job Scraper Bot!*\n\n"
        "I will send you real-time job alerts based on your filters.\n\n"
        f"🔍 *Current Searches:*\n{search_text}\n"
        f"🏢 *Company Filter:* Max {config.get('max_company_followers', 1000)} followers.\n"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs_sent = context.bot_data.get('jobs_sent_today', 0)
    await update.message.reply_text(f"📊 *Status*\n\nJobs sent today: `{jobs_sent}`", parse_mode=ParseMode.MARKDOWN)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛑 Bot command received. (To fully stop the background scraping, you must stop the Python script).")

async def setfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ This command is disabled in the new multi-search version. "
        "Please edit `config.json` manually to modify searches and restart the bot."
    )

async def send_job_to_telegram(bot, chat_id, job):
    """
    Sends a single job to the specified Telegram chat.
    """
    title = job.get('title', 'Unknown')
    company = job.get('company', 'Unknown')
    followers = job.get('followers', 0)
    size = job.get('size')
    location = job.get('location', 'Unknown')
    keywords = job.get('keywords', 'N/A')
    link = job.get('link', '')

    applicants = job.get('applicants')
    
    if applicants is not None:
        applicants_str = f"📨 {applicants} applicants (Low competition!)"
    else:
        applicants_str = "📨 Unknown applicants"

    if not followers and not size:
        company_details = "Size: Unknown, Followers: Unknown - likely small startup!"
    else:
        f_str = followers if followers else "Unknown"
        s_str = size if size else "Unknown"
        company_details = f"👥 {f_str} followers | 📏 Size: {s_str} (Small Startup!)"

    text = (
        f"🚀 **{title}**\n"
        f"🏢 {company} | {company_details}\n"
        f"🌍 {location} | 🌐 Worldwide Remote\n"
        f"{applicants_str}\n"
        f"🔍 Matched: {keywords}"
    )
    
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Apply", url=link)]
    ])
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=None,
            reply_markup=reply_markup
        )
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return True
    except Exception as e:
        logger.error(f"Failed to send job to Telegram: {e}")
        return False
