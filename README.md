# Group Expenses Bot 💰

A Telegram bot for tracking shared expenses in group chats. Each group has its own expense categories and CSV file for recording expenses.

## Features

- **Custom Categories**: Set up expense categories specific to your group
- **Easy Expense Entry**: Add expenses with category, price, and optional comment
- **Per-Group Tracking**: Each group has its own categories and expense file
- **CSV Export**: All expenses are saved in CSV format for easy analysis

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` command
3. Follow the instructions to create your bot
4. Copy the bot token you receive

### 2. Configure the Bot

1. Open the `.env` file in the project directory
2. Replace `your_bot_token_here` with your actual bot token:
   ```
   BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Bot

```bash
python bot.py
```

## Usage

### Setting Up Categories

1. Add the bot to your Telegram group
2. Send `/start` in the group
3. Enter your expense categories separated by commas:
   ```
   Restaurants, Apartment, Kids, Gifts, Supermarket
   ```
4. The bot automatically adds an "Other" category

### Adding Expenses

1. Send `/expense` command
2. Select a category from the inline keyboard
3. Enter the price (numbers only, e.g., `25.50` or `100`)
4. Enter a comment or send `/skip` to skip

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Initialize the bot and set up categories |
| `/expense` | Add a new expense |
| `/setcategories` | Change expense categories |
| `/cancel` | Cancel current operation |
| `/help` | Show help message |

## Data Storage

- **Categories**: Stored in `data/categories/{chat_id}.json`
- **Expenses**: Stored in `data/expenses/{chat_id}.csv`

### CSV Format

| Column | Description |
|--------|-------------|
| Date | Timestamp of the expense |
| User | Name of the user who added the expense |
| Category | Selected expense category |
| Price | Amount of the expense |
| Comment | Optional comment |

## Project Structure

```
GroupExpensesBot/
├── bot.py              # Main bot code
├── requirements.txt    # Python dependencies
├── .env               # Bot token (not committed to git)
├── README.md          # This file
└── data/              # Data directory (created automatically)
    ├── categories/    # Category files per chat
    └── expenses/      # Expense CSV files per chat
```

## License

MIT License

