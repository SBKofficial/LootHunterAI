import os
import logging
import urllib.parse
import re
from datetime import datetime, timedelta
import time
from google.api_core import exceptions as google_exceptions
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from google import genai
from google.genai import types
from cachetools import TTLCache

# --- CONFIGURATION (Load from Environment Variables) ---
# Get these from Render Dashboard > Environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CUELINKS_ID = os.getenv("CUELINKS_ID")  # Your Numeric ID (e.g., 55882)

# --- UPGRADE 1: MEMORY CACHE ---
# Stores search results for 1 hour (3600 seconds) to save API calls
# Max 100 queries stored in memory
search_cache = TTLCache(maxsize=100, ttl=3600)

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- GEMINI AI CLIENT ---
client = genai.Client(api_key=GEMINI_API_KEY)

# --- CORE FUNCTIONS ---

def monetize_links(text):
    """
    Finds normal Amazon/Flipkart links and wraps them in Cuelinks deep links.
    """
    if not CUELINKS_ID:
        return text # Return normal text if no ID set

    url_pattern = r'(https?://[^\s]+)'
    
    def convert_match(match):
        original_url = match.group(0)
        # Skip if already a cuelinks link
        if "clnk.in" in original_url:
            return original_url
            
        encoded_url = urllib.parse.quote(original_url)
        # The Magic Deep Link Formula
        return f"https://clnk.in/{CUELINKS_ID}/?url={encoded_url}"

    return re.sub(url_pattern, convert_match, text)

def get_shopping_advice(query):
    """
    Searches via Gemini. Includes automatic Retry logic and Fallback to stable models.
    """
    # 1. Check Cache
    if query in search_cache:
        logger.info(f"Cache Hit for: {query}")
        return search_cache[query]

    logger.info(f"Searching Live for: {query}")
    
    # Define the tool once
    google_search_tool = types.Tool(
        google_search=types.GoogleSearch()
    )

    prompt = f"""
    Act as a friendly Indian shopping assistant. User wants: "{query}"
    
    1. Search Google for the 3 BEST options available in India right now.
    2. Format specific model names, REAL prices in Rupees (₹), and a 1-sentence "Why this?"
    3. Do NOT invent prices. Use real data.
    4. Keep it concise.
    """

    # --- RETRY LOGIC (The Fix) ---
    # We try up to 3 times if the API is busy
    max_retries = 3
    
    # Priority list: Try 2.0 first, if it fails, fallback to 1.5 (Stable)
    models_to_try = ['gemini-2.0-flash-exp', 'gemini-1.5-flash']

    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                logger.info(f"Attempting with model: {model_name} (Try {attempt+1})")
                
                response = client.models.generate_content(
                    model=model_name, 
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[google_search_tool],
                        response_modalities=["TEXT"]
                    )
                )
                
                # Success! Save to cache and return
                result_text = response.text
                search_cache[query] = result_text
                return result_text

            except Exception as e:
                # Check if it's a "Too Many Requests" (429) error
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    logger.warning(f"Rate Limit Hit on {model_name}. Waiting 5s...")
                    time.sleep(5) # Wait 5 seconds before retrying
                else:
                    logger.error(f"Gemini Error ({model_name}): {e}")
                    break # If it's a different error, stop trying this model

    # If all attempts fail
    return "⚠️ The market server is very busy right now. Please try again in 1 minute!"
# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 **Hi {user_name}!**\n\nI am your AI Deal Finder.\n"
        "Tell me what you need, and I'll compare prices for you.\n\n"
        "Try saying:\n"
        "👉 *'Best gaming phone under 25k'*\n"
        "👉 *'Nike running shoes for men'*\n"
        "👉 *'Sony noise cancelling headphones price'*",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_query = update.message.text
    
    # --- UPGRADE 2: SIMPLE RATE LIMIT ---
    # (Prevents one user from spamming)
    # You can add logic here to check user_id vs last_message_time if needed.
    
    # Send "Typing" status
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Send temporary message
    status_msg = await update.message.reply_text("🔍 *Searching top stores...*", parse_mode='Markdown')
    
    # Get Intelligence
    ai_response = get_shopping_advice(user_query)
    
    # Make Money (Convert Links)
    final_text = monetize_links(ai_response)
    
    # Add Footer
    footer = "\n\n🛍️ *Shop via these links to support me!*"
    
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=status_msg.message_id,
        text=final_text + footer
    )

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Check for keys
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("❌ ERROR: Keys missing! Set TELEGRAM_TOKEN and GEMINI_API_KEY in Environment.")
        exit(1)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("✅ Bot is Live & Listening...")
    application.run_polling()
