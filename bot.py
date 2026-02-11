"""
Group Expenses Telegram Bot
A bot for tracking shared expenses in Telegram groups.
"""

import os
import csv
import json
import logging
import io
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt

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
EXPENSES_TMP_DIR = DATA_DIR / "expenses_tmp"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
CATEGORIES_DIR.mkdir(exist_ok=True)
EXPENSES_DIR.mkdir(exist_ok=True)
EXPENSES_TMP_DIR.mkdir(exist_ok=True)

# Conversation states
WAITING_FOR_CATEGORIES = 0
SELECTING_CATEGORY = 1
ENTERING_PRICE = 2
ENTERING_COMMENT = 3
SELECTING_PERIOD = 4
SELECTING_PERIOD_FOR_FILE = 5
SETTLEMENT_WAITING_FOR_USERS = 6


def get_categories_file(chat_id: int) -> Path:
    """Get path to categories file for a specific chat."""
    return CATEGORIES_DIR / f"categories_{chat_id}.json"


def get_expenses_file(chat_id: int) -> Path:
    """Get path to expenses CSV file for a specific chat."""
    return EXPENSES_DIR / f"expenses_{chat_id}.csv"


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


def get_stat_for_period(csv_file: str | Path, start: str, end: str) -> dict:
    """
    Get statistics for expenses in a given period.
    
    Args:
        csv_file: Path to CSV file with columns Date,User,Category,Price,Comment
        start: Start date in format YYYY-MM-DD
        end: End date in format YYYY-MM-DD
    
    Returns:
        Dictionary with:
        - total: Total spend for the period
        - by_category: Dict of category -> spend
        - by_user: Dict of user -> spend
    """
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()
    
    total = 0.0
    by_category = {}
    by_user = {}
    
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse date (only date part, ignore time)
            date_str = row["Date"].split()[0]
            row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            
            # Check if within period
            if start_date <= row_date <= end_date:
                price = float(row["Price"])
                category = row["Category"]
                user = row["User"]
                
                # Update totals
                total += price
                by_category[category] = by_category.get(category, 0.0) + price
                by_user[user] = by_user.get(user, 0.0) + price
    
    return {
        "total": total,
        "by_category": by_category,
        "by_user": by_user,
    }


def get_settlement(csv_file: str | Path, usernames: list[str]) -> dict:
    """
    Calculate settlement between selected users so that everyone spends the same amount.
    
    Args:
        csv_file: Path to CSV file with columns Date,User,Category,Price,Comment
        usernames: List of users who want to settle
    
    Returns:
        Dictionary with:
        - spent: Dict of user -> total spent
        - average: Target equal spend per user
        - balances: Dict of user -> balance ( >0 means should receive, <0 means should pay )
        - transactions: List of transfers:
            [{"from": user_who_pays, "to": user_who_receives, "amount": float}, ...]
    """
    usernames_set = set(usernames)
    spent: dict[str, float] = {u: 0.0 for u in usernames}

    # Sum spending for each selected user
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            user = row.get("User")
            if user in usernames_set:
                price_str = (row.get("Price") or "").strip()
                if not price_str:
                    continue
                try:
                    amount = float(price_str)
                except ValueError:
                    continue
                spent[user] += amount

    # Compute average and balances
    n = len(usernames)
    total_spent = sum(spent.values())
    average = total_spent / n if n > 0 else 0.0

    balances: dict[str, float] = {
        u: round(spent[u] - average, 2) for u in usernames
    }

    # Split into debtors (<0) and creditors (>0)
    creditors: list[list[object]] = []
    debtors: list[list[object]] = []
    for user, bal in balances.items():
        if bal > 0:
            creditors.append([user, bal])  # [name, positive_balance]
        elif bal < 0:
            debtors.append([user, bal])    # [name, negative_balance]

    # Greedy settlement: match biggest creditor with biggest debtor
    transactions: list[dict] = []

    creditors.sort(key=lambda x: x[1], reverse=True)  # largest positive first
    debtors.sort(key=lambda x: x[1])                  # most negative first

    i, j = 0, 0
    while i < len(creditors) and j < len(debtors):
        cred_user, cred_amount = creditors[i]
        debt_user, debt_amount = debtors[j]  # negative

        transfer = min(cred_amount, -debt_amount)
        transfer = round(transfer, 2)

        if transfer > 0:
            transactions.append({
                "from": debt_user,
                "to": cred_user,
                "amount": transfer,
            })

        # Update balances for this step
        creditors[i][1] = round(cred_amount - transfer, 2)
        debtors[j][1] = round(debt_amount + transfer, 2)

        # Move pointers when someone is settled (close enough to zero)
        if abs(creditors[i][1]) < 0.01:
            i += 1
        if abs(debtors[j][1]) < 0.01:
            j += 1

    return {
        "spent": spent,
        "average": round(average, 2),
        "balances": balances,
        "transactions": transactions,
    }


async def settle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle /settle command - ask which users will take part in settlement.
    """
    chat_id = update.effective_chat.id
    expenses_file = get_expenses_file(chat_id)

    if not expenses_file.exists():
        await update.message.reply_text(
            "🤝 No expenses recorded yet.\n\n"
            "Use /expense to add your first expense before settling."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🤝 Let's settle up!\n\n"
        "Send the usernames that will take part in the settlement, separated by commas or spaces.\n"
        "You can include or omit the @, for example:\n"
        "@alice, @bob charlie"
    )
    return SETTLEMENT_WAITING_FOR_USERS


async def receive_settlement_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Receive list of usernames for settlement and send optimal settlement algorithm.
    """
    chat_id = update.effective_chat.id
    expenses_file = get_expenses_file(chat_id)

    if not expenses_file.exists():
        await update.message.reply_text(
            "🤝 No expenses recorded yet.\n\n"
            "Use /expense to add your first expense before settling."
        )
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    # Split by commas and whitespace, strip optional leading '@'
    raw_tokens = text.replace(",", " ").split()
    usernames: list[str] = []
    for token in raw_tokens:
        name = token.strip()
        if not name:
            continue
        if name.startswith("@"):
            name = name[1:]
        if not name:
            continue
        if name not in usernames:
            usernames.append(name)

    if not usernames:
        await update.message.reply_text(
            "❌ I didn't find any usernames.\n\n"
            "Please send usernames separated by commas or spaces, for example:\n"
            "@alice, @bob charlie"
        )
        return SETTLEMENT_WAITING_FOR_USERS

    try:
        result = get_settlement(expenses_file, usernames)
    except Exception as e:
        logger.exception("Error while calculating settlement: %s", e)
        await update.message.reply_text(
            "⚠️ Something went wrong while calculating the settlement. "
            "Please try again later."
        )
        return ConversationHandler.END

    spent: dict[str, float] = result.get("spent", {})
    average: float = result.get("average", 0.0)
    balances: dict[str, float] = result.get("balances", {})
    transactions: list[dict] = result.get("transactions", [])

    total_spent = sum(spent.values())
    if total_spent == 0 or not transactions:
        await update.message.reply_text(
            "🤝 No settlement needed for the selected users.\n\n"
            "Either there are no expenses for these users or everyone already spent equally."
        )
        return ConversationHandler.END

    lines: list[str] = []
    lines.append("🤝 *Settlement summary*")
    lines.append("")
    lines.append(f"👥 Users: {', '.join(usernames)}")
    lines.append(f"💰 Total spent: {total_spent:.2f}")
    lines.append(f"🎯 Target per person: {average:.2f}")
    lines.append("")
    lines.append("📊 *Balances:*")
    for user in usernames:
        user_spent = spent.get(user, 0.0)
        bal = balances.get(user, 0.0)
        if bal > 0.01:
            status = "should receive"
        elif bal < -0.01:
            status = "should pay"
        else:
            status = "is settled"
        lines.append(f"  • {user}: spent {user_spent:.2f}, balance {bal:+.2f} ({status})")

    lines.append("")
    lines.append("📎 *Optimal settlement transactions:*")
    for tx in transactions:
        from_user = tx.get("from")
        to_user = tx.get("to")
        amount = tx.get("amount", 0.0)
        lines.append(f"  • {from_user} → {to_user}: {amount:.2f}")

    message_text = "\n".join(lines)
    # Use plain text to avoid markdown parsing issues with usernames
    await update.message.reply_text(message_text)

    return ConversationHandler.END


def get_csv_for_period(csv_file: str | Path, start: str, end: str) -> Path:
    """
    Create a filtered CSV file containing only rows within the specified period.
    
    Args:
        csv_file: Path to CSV file with columns Date,User,Category,Price,Comment
        start: Start date in format YYYY-MM-DD
        end: End date in format YYYY-MM-DD
    
    Returns:
        Path to the created CSV file in data/expenses_tmp/
    """
    csv_file = Path(csv_file)
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()
    
    # Create output filename: <original_name>_tmp<start><end>.csv
    original_name = csv_file.stem  # filename without extension
    output_filename = f"{original_name}_tmp{start}{end}.csv"
    output_path = EXPENSES_TMP_DIR / output_filename
    
    # Read and filter rows
    filtered_rows = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            # Parse date (only date part, ignore time)
            date_str = row["Date"].split()[0]
            row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            
            # Check if within period
            if start_date <= row_date <= end_date:
                filtered_rows.append(row)
    
    # Write filtered rows to new CSV file
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered_rows)
    
    return output_path


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


def generate_pie_chart(by_category: dict) -> io.BytesIO:
    """
    Generate a pie chart of spendings by categories.
    
    Args:
        by_category: Dict of category -> spend amount
    
    Returns:
        BytesIO buffer containing the PNG image
    """
    if not by_category:
        return None
    
    # Prepare data
    categories = list(by_category.keys())
    amounts = list(by_category.values())
    total = sum(amounts)
    
    # Create labels with amounts
    labels = [f"{cat}\n{amt:.0f}" for cat, amt in zip(categories, amounts)]
    
    # Color palette - vibrant and distinct colors
    colors = [
        '#FF6B6B',  # Coral Red
        '#4ECDC4',  # Turquoise
        '#45B7D1',  # Sky Blue
        '#96CEB4',  # Sage Green
        '#FFEAA7',  # Cream Yellow
        '#DDA0DD',  # Plum
        '#98D8C8',  # Mint
        '#F7DC6F',  # Mustard
        '#BB8FCE',  # Light Purple
        '#85C1E9',  # Light Blue
        '#F8B500',  # Gold
        '#FF8C00',  # Dark Orange
    ]
    
    # Create figure with transparent background
    fig, ax = plt.subplots(figsize=(10, 8), facecolor='white')
    
    # Create pie chart
    wedges, texts, autotexts = ax.pie(
        amounts,
        labels=labels,
        autopct=lambda pct: f'{pct:.1f}%',
        colors=colors[:len(categories)],
        startangle=90,
        explode=[0.02] * len(categories),  # Slight separation
        textprops={'fontsize': 11, 'fontweight': 'bold'},
        pctdistance=0.75,
        labeldistance=1.15,
    )
    
    # Style the percentage labels
    for autotext in autotexts:
        autotext.set_fontsize(10)
        autotext.set_fontweight('bold')
        autotext.set_color('white')
    
    # Equal aspect ratio ensures circular pie
    ax.axis('equal')
    
    # Title
    ax.set_title(f'Expenses by Category\nTotal: {total:.0f}', fontsize=14, fontweight='bold', pad=20)
    
    # Save to BytesIO buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    
    return buf


async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /stat command - show statistics for a period."""
    chat_id = update.effective_chat.id
    expenses_file = get_expenses_file(chat_id)
    
    if not expenses_file.exists():
        await update.message.reply_text(
            "📊 No expenses recorded yet.\n\n"
            "Use /expense to add your first expense!"
        )
        return ConversationHandler.END
    
    # Get available periods
    periods = get_periods(expenses_file)
    
    if not periods:
        await update.message.reply_text(
            "📊 No expenses found in the records.\n\n"
            "Use /expense to add expenses."
        )
        return ConversationHandler.END
    
    # Store periods in context for later use
    context.user_data["stat_periods"] = periods
    
    # Create inline keyboard with periods
    keyboard = []
    for i, (name, start, end) in enumerate(periods):
        button_text = f"📅 {name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"period_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📊 Choose a period to view statistics:",
        reply_markup=reply_markup
    )
    return SELECTING_PERIOD


async def period_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle period selection and show statistics."""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    
    # Extract period index from callback data
    period_idx = int(query.data.replace("period_", ""))
    periods = context.user_data.get("stat_periods", [])
    
    if period_idx >= len(periods):
        await query.edit_message_text("❌ Invalid period selection.")
        return ConversationHandler.END
    
    period_name, start_date, end_date = periods[period_idx]
    expenses_file = get_expenses_file(chat_id)
    
    # Get statistics for the period
    stats = get_stat_for_period(expenses_file, start_date, end_date)
    
    # Check if there are any expenses
    if stats["total"] == 0:
        await query.edit_message_text(
            f"📊 Statistics for {period_name}\n\n"
            f"No expenses found in this period."
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    # Format the text message
    message_lines = [
        f"📊 *Statistics for {period_name}*",
        f"📅 Period: {start_date} — {end_date}",
        "",
        f"💰 *Total spend: {stats['total']:.2f}*",
        "",
        "👥 *Spend by user:*",
    ]
    
    # Add user breakdown
    for user, amount in sorted(stats["by_user"].items(), key=lambda x: -x[1]):
        percentage = (amount / stats["total"]) * 100
        message_lines.append(f"  • {user}: {amount:.2f} ({percentage:.1f}%)")
    
    message_text = "\n".join(message_lines)
    
    # Edit the original message with text statistics
    await query.edit_message_text(message_text, parse_mode="Markdown")
    
    # Generate and send pie chart
    if stats["by_category"]:
        pie_chart = generate_pie_chart(stats["by_category"])
        if pie_chart:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=pie_chart,
                caption=f"📊 Expenses by category for {period_name}"
            )
    
    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END


async def getfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /getfile command - export expenses CSV for a period."""
    chat_id = update.effective_chat.id
    expenses_file = get_expenses_file(chat_id)
    
    if not expenses_file.exists():
        await update.message.reply_text(
            "📁 No expenses recorded yet.\n\n"
            "Use /expense to add your first expense!"
        )
        return ConversationHandler.END
    
    # Get available periods
    periods = get_periods(expenses_file)
    
    if not periods:
        await update.message.reply_text(
            "📁 No expenses found in the records.\n\n"
            "Use /expense to add expenses."
        )
        return ConversationHandler.END
    
    # Store periods in context for later use
    context.user_data["file_periods"] = periods
    
    # Create inline keyboard with periods
    keyboard = []
    for i, (name, start, end) in enumerate(periods):
        button_text = f"📅 {name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"fileperiod_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📁 Choose a period to export expenses:",
        reply_markup=reply_markup
    )
    return SELECTING_PERIOD_FOR_FILE


async def file_period_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle period selection and send CSV file."""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    
    # Extract period index from callback data
    period_idx = int(query.data.replace("fileperiod_", ""))
    periods = context.user_data.get("file_periods", [])
    
    if period_idx >= len(periods):
        await query.edit_message_text("❌ Invalid period selection.")
        return ConversationHandler.END
    
    period_name, start_date, end_date = periods[period_idx]
    expenses_file = get_expenses_file(chat_id)
    
    # Create CSV file for the period
    csv_file_path = get_csv_for_period(expenses_file, start_date, end_date)
    
    # Edit the original message to show selection
    await query.edit_message_text(f"📅 Selected period: {period_name}")
    
    # Send the CSV file
    try:
        with open(csv_file_path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=f"expenses_{period_name.replace(' ', '_')}_{start_date}_{end_date}.csv",
                caption=f"📁 Here is the file with all of your expenses for the period {period_name} ({start_date} — {end_date})"
            )
    finally:
        # Delete the temporary CSV file after sending
        if csv_file_path.exists():
            csv_file_path.unlink()
    
    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END


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
    #user_name = user.full_name or user.username or str(user.id)
    user_name = user.username or user.name or str(user.id)
    
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
    #user_name = user.full_name or user.username or str(user.id)
    user_name = user.username or user.name or str(user.id)
    
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
        "/stat - View spending statistics and charts\n"
        "/getfile - Export expenses as CSV file\n"
        "/settle - Calculate optimal settlement between users\n"
        "/setcategories - Change expense categories\n"
        "/cancel - Cancel current operation\n"
        "/help - Show this help message\n\n"
        "*How to use:*\n"
        "1. Add the bot to your group\n"
        "2. Use /start to set up expense categories\n"
        "3. Use /expense to add expenses\n"
        "4. Use /stat to view statistics by period\n"
        "5. Use /getfile to export expenses as CSV\n"
        "6. Each group has its own categories and expense file",
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
    
    # Statistics conversation handler
    stat_handler = ConversationHandler(
        entry_points=[CommandHandler("stat", stat_command)],
        states={
            SELECTING_PERIOD: [
                CallbackQueryHandler(period_selected, pattern="^period_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,
    )
    
    # Get file conversation handler
    getfile_handler = ConversationHandler(
        entry_points=[CommandHandler("getfile", getfile_command)],
        states={
            SELECTING_PERIOD_FOR_FILE: [
                CallbackQueryHandler(file_period_selected, pattern="^fileperiod_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,
    )

    # Settlement conversation handler
    settle_handler = ConversationHandler(
        entry_points=[CommandHandler("settle", settle_command)],
        states={
            SETTLEMENT_WAITING_FOR_USERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_settlement_users),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,
    )
    
    # Add handlers
    application.add_handler(setup_handler)
    application.add_handler(expense_handler)
    application.add_handler(stat_handler)
    application.add_handler(getfile_handler)
    application.add_handler(settle_handler)
    application.add_handler(CommandHandler("help", help_command))
    
    # Start the bot
    logger.info("Starting bot...")
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

