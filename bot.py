"""
Group Expenses Telegram Bot
A bot for tracking shared expenses in Telegram groups.
"""

import os
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Data directories
DATA_DIR = Path("data")
CATEGORIES_DIR = DATA_DIR / "categories"
EXPENSES_DIR = DATA_DIR / "expenses"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
CATEGORIES_DIR.mkdir(exist_ok=True)
EXPENSES_DIR.mkdir(exist_ok=True)

# Conversation states
WAITING_FOR_CATEGORIES = 0
SELECTING_CATEGORY = 1
ENTERING_PRICE = 2
ENTERING_COMMENT = 3


def get_categories_file(chat_id: int) -> Path:
    """Get path to categories file for a specific chat."""
    return CATEGORIES_DIR / f"{chat_id}.json"


def get_expenses_file(chat_id: int) -> Path:
    """Get path to expenses CSV file for a specific chat."""
    return EXPENSES_DIR / f"{chat_id}.csv"


def load_categories(chat_id: int) -> list[str] | None:
    """Load categories for a chat. Returns None if not set."""
    categories_file = get_categories_file(chat_id)
    if categories_file.exists():
        with open(categories_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_categories(chat_id: int, categories: list[str]) -> None:
    """Save categories for a chat."""
    categories_file = get_categories_file(chat_id)
    with open(categories_file, "w", encoding="utf-8") as f:
        json.dump(categories, f, ensure_ascii=False, indent=2)


def save_expense(chat_id: int, user: str, category: str, price: float, comment: str) -> None:
    """Save expense to CSV file."""
    expenses_file = get_expenses_file(chat_id)
    file_exists = expenses_file.exists()
    
    with open(expenses_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Date", "User", "Category", "Price", "Comment"])
        
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([date, user, category, price, comment])


def get_periods(csv_file: str | Path) -> list[tuple[str, str, str]]:
    """
    Get period information from a CSV file with a Date column.
    
    Returns a list of tuples containing:
    - 3 last months: (month_name, first_date, last_date)
    - Last year: (year_number, first_date, last_date)
    - All period: ("All period", earliest_date, latest_date)
    """
    import calendar
    
    # Read dates from CSV
    dates = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row["Date"].split()[0]  # Get only date part (YYYY-MM-DD)
            dates.append(datetime.strptime(date_str, "%Y-%m-%d").date())
    
    if not dates:
        return []
    
    # Sort dates
    dates.sort()
    
    result = []
    
    # Get unique months (year, month) sorted descending
    unique_months = sorted(set((d.year, d.month) for d in dates), reverse=True)
    
    # 3 last months
    for year, month in unique_months[:3]:
        month_name = calendar.month_name[month]
        first_day = f"{year}-{month:02d}-01"
        last_day_num = calendar.monthrange(year, month)[1]
        last_day = f"{year}-{month:02d}-{last_day_num:02d}"
        result.append((month_name, first_day, last_day))
    
    # Last year
    unique_years = sorted(set(d.year for d in dates), reverse=True)
    if unique_years:
        last_year = unique_years[0]
        first_day_year = f"{last_year}-01-01"
        last_day_year = f"{last_year}-12-31"
        result.append((str(last_year), first_day_year, last_day_year))
    
    # All period
    earliest = min(dates)
    latest = max(dates)
    result.append(("All period", earliest.strftime("%Y-%m-%d"), latest.strftime("%Y-%m-%d")))
    
    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start command - ask for categories if not set."""
    chat_id = update.effective_chat.id
    categories = load_categories(chat_id)
    
    if categories:
        await update.message.reply_text(
            f"👋 Welcome back!\n\n"
            f"Your categories are already set up:\n"
            f"📋 {', '.join(categories)}\n\n"
            f"Use /expense to add a new expense.\n"
            f"Use /setcategories to change categories."
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "👋 Hello! I'm your Group Expenses Bot.\n\n"
            "Let's set up your expense categories.\n\n"
            "Enter the most popular categories of your group expenses, "
            "separated by commas.\n\n"
            "For example:\n"
            "🍽️ Restaurants, 🏠 Apartment, 👶 Kids, 🎁 Gifts, 🛒 Supermarket"
        )
        return WAITING_FOR_CATEGORIES


async def set_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /setcategories command - allow changing categories."""
    await update.message.reply_text(
        "📝 Let's update your expense categories.\n\n"
        "Enter the categories separated by commas.\n\n"
        "For example:\n"
        "🍽️ Restaurants, 🏠 Apartment, 👶 Kids, 🎁 Gifts, 🛒 Supermarket"
    )
    return WAITING_FOR_CATEGORIES


async def receive_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the categories sent by user."""
    chat_id = update.effective_chat.id
    text = update.message.text
    
    # Parse categories from comma-separated text
    categories = [cat.strip() for cat in text.split(",") if cat.strip()]
    
    if not categories:
        await update.message.reply_text(
            "❌ No valid categories found. Please enter at least one category, "
            "separated by commas."
        )
        return WAITING_FOR_CATEGORIES
    
    # Add "Other" category if not present
    if "Other" not in categories and "other" not in [c.lower() for c in categories]:
        categories.append("Other")
    
    # Save categories
    save_categories(chat_id, categories)
    
    await update.message.reply_text(
        f"✅ Categories saved!\n\n"
        f"📋 Your categories:\n{', '.join(categories)}\n\n"
        f"Now you can use /expense to add expenses."
    )
    return ConversationHandler.END


async def expense_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /expense command - start expense entry flow."""
    chat_id = update.effective_chat.id
    categories = load_categories(chat_id)
    
    if not categories:
        await update.message.reply_text(
            "⚠️ Categories are not set up yet.\n\n"
            "Please use /start to set up expense categories first."
        )
        return ConversationHandler.END
    
    # Create inline keyboard with categories
    keyboard = []
    row = []
    for i, category in enumerate(categories):
        row.append(InlineKeyboardButton(category, callback_data=f"cat_{category}"))
        if len(row) == 2:  # 2 buttons per row
            keyboard.append(row)
            row = []
    if row:  # Add remaining buttons
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📊 Choose expense category:",
        reply_markup=reply_markup
    )
    return SELECTING_CATEGORY


async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle category selection from inline keyboard."""
    query = update.callback_query
    await query.answer()
    
    # Extract category from callback data
    category = query.data.replace("cat_", "")
    context.user_data["expense_category"] = category
    
    await query.edit_message_text(
        f"📁 Category: {category}\n\n"
        f"💰 Enter the price:"
    )
    return ENTERING_PRICE


async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the price entered by user."""
    text = update.message.text.strip()
    
    # Try to parse the price
    try:
        # Replace comma with dot for decimal numbers
        price = float(text.replace(",", "."))
        if price <= 0:
            raise ValueError("Price must be positive")
        
        context.user_data["expense_price"] = price
        
        await update.message.reply_text(
            f"💰 Price: {price}\n\n"
            f"📝 Enter a comment (or send /skip to skip):"
        )
        return ENTERING_COMMENT
    
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid price. Please enter a valid number.\n\n"
            "For example: 25.50 or 100"
        )
        return ENTERING_PRICE


async def receive_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the comment entered by user and save expense."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_name = user.full_name or user.username or str(user.id)
    
    comment = update.message.text.strip()
    
    # Get saved data
    category = context.user_data.get("expense_category", "Unknown")
    price = context.user_data.get("expense_price", 0)
    
    # Save expense
    save_expense(chat_id, user_name, category, price, comment)
    
    # Clear user data
    context.user_data.clear()
    
    await update.message.reply_text(
        f"✅ Expense saved!\n\n"
        f"📁 Category: {category}\n"
        f"💰 Price: {price}\n"
        f"📝 Comment: {comment}\n"
        f"👤 User: {user_name}\n\n"
        f"Use /expense to add another expense."
    )
    return ConversationHandler.END


async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip comment and save expense."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_name = user.full_name or user.username or str(user.id)
    
    # Get saved data
    category = context.user_data.get("expense_category", "Unknown")
    price = context.user_data.get("expense_price", 0)
    
    # Save expense with empty comment
    save_expense(chat_id, user_name, category, price, "")
    
    # Clear user data
    context.user_data.clear()
    
    await update.message.reply_text(
        f"✅ Expense saved!\n\n"
        f"📁 Category: {category}\n"
        f"💰 Price: {price}\n"
        f"👤 User: {user_name}\n\n"
        f"Use /expense to add another expense."
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel current operation."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Operation cancelled.\n\n"
        "Use /expense to start again."
    )
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help message."""
    await update.message.reply_text(
        "🤖 *Group Expenses Bot Help*\n\n"
        "*Commands:*\n"
        "/start - Set up categories for the group\n"
        "/expense - Add a new expense\n"
        "/setcategories - Change expense categories\n"
        "/cancel - Cancel current operation\n"
        "/help - Show this help message\n\n"
        "*How to use:*\n"
        "1. Add the bot to your group\n"
        "2. Use /start to set up expense categories\n"
        "3. Use /expense to add expenses\n"
        "4. Each group has its own categories and expense file",
        parse_mode="Markdown"
    )


def main() -> None:
    """Run the bot."""
    # Get bot token from environment
    token = os.getenv("BOT_TOKEN")
    if not token or token == "your_bot_token_here":
        logger.error("BOT_TOKEN not set in .env file!")
        print("\n❌ Error: BOT_TOKEN not set!")
        print("Please edit the .env file and add your Telegram bot token.")
        print("Get your token from @BotFather on Telegram.\n")
        return
    
    # Create application
    application = Application.builder().token(token).build()
    
    # Setup conversation handler
    setup_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("setcategories", set_categories_command),
        ],
        states={
            WAITING_FOR_CATEGORIES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_categories),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=False,  # Allow any user in chat to respond
    )
    
    # Expense conversation handler
    expense_handler = ConversationHandler(
        entry_points=[CommandHandler("expense", expense_command)],
        states={
            SELECTING_CATEGORY: [
                CallbackQueryHandler(category_selected, pattern="^cat_"),
            ],
            ENTERING_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price),
            ],
            ENTERING_COMMENT: [
                CommandHandler("skip", skip_comment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_comment),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,  # Track each user's expense separately
    )
    
    # Add handlers
    application.add_handler(setup_handler)
    application.add_handler(expense_handler)
    application.add_handler(CommandHandler("help", help_command))
    
    # Start the bot
    logger.info("Starting bot...")
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

