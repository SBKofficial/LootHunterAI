import os
import logging
import urllib.parse
import re
import time
from threading import Thread
from flask import Flask

# Third-party libraries
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from google import genai
from google.genai import types
from cachetools import TTLCache

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CUELINKS_ID = os.getenv("CUELINKS_ID")

# --- MEMORY CACHE ---
search_cache = TTLCache(maxsize=100, ttl=3600)

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- GEMINI CLIENT ---
client = genai.Client(api_key=GEMINI_API_KEY)

# --- FLASK KEEP-ALIVE SERVER (THE FIX) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running! 🤖"

def run_http_server():
    # Render assigns a PORT via environment variable. We MUST use it.
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http_server)
    t.start()

# --- CORE BOT FUNCTIONS ---

def monetize_links(text):
    if not CUELINKS_ID:
        return text 
    url_pattern = r'(https?://[^\s]+)'
    def convert_match(match):
        original_url = match.group(0)
        if "clnk.in" in original_url:
            return original_url
        encoded_url = urllib.parse.quote(original_url)
        return f"https://clnk.in/{CUELINKS_ID}/?url={encoded_url}"
    return re.sub(url_pattern, convert_match, text)

def get_shopping_advice(query):
    if query in search_cache:
        logger.info(f"Cache Hit for: {query}")
        return search_cache[query]

    logger.info(f"Searching Live for: {query}")
    
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

    # Retry Logic for Rate Limits
    models_to_try = ['gemini-2.0-flash-exp', 'gemini-1.5-flash']
    
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name, 
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[google_search_tool],
                    response_modalities=["TEXT"]
                )
            )
            result_text = response.text
            search_cache[query] = result_text
            return result_text
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                logger.warning(f"Rate Limit on {model_name}. Retrying...")
                time.sleep(2)
            else:
                logger.error(f"Error on {model_name}: {e}")

    return "⚠️ The market server is very busy. Please try again in 1 minute!"

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **I am Online!**\nAsk me for deals like 'Best 5G phone under 15k'.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 *Searching...*", parse_mode='Markdown')
    ai_response = get_shopping_advice(update.message.text)
    final_text = monetize_links(ai_response)
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=status_msg.message_id,
        text=final_text + "\n\n🛍️ *Support me by shopping via links!*"
    )

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # 1. Start the Fake Server first
    keep_alive()
    
    # 2. Start the Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print(f"✅ Bot is Live on Port {os.environ.get('PORT', 'Default')}")
    application.run_polling()
