# Slack Shopping Agent with LangChain and Target Automation Integration

This project implements a Slack bot that acts as a shared shopping list manager for a team or channel. It uses LangChain and OpenAI to understand user requests for adding, removing, or viewing items. A key feature is the integration with a separate Target Automation Agent (a TypeScript service) to trigger automated shopping runs.

## Table of Contents

- [Features](#features)
- [Workflow](#workflow)
- [Technical Stack](#technical-stack)
- [Setup](#setup)
- [Usage](#usage)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)
- [Future Enhancements](#future-enhancements)

## Features

-   **Natural Language Item Management:** Add items to the shopping list by mentioning the bot (e.g., "@ShoppingAgent add 2 apples and a loaf of bread").
-   **Quantity Recognition:** Understands quantities mentioned in requests.
-   **Price Fetching (Experimental):** Attempts to fetch product prices from Target.com using Playwright/BeautifulSoup.
-   **List Viewing:** Ask the bot to show the current list.
-   **Item Removal:** Ask the bot to remove specific items.
-   **User Association:** Tracks which user added each item.
-   **Order Placement & Automation Trigger:**
    -   Admins use `/order-placed`.
    -   The agent calls the Target Automation Agent API to trigger a shopping run.
    -   Marks items as ordered in the database *only* upon successful API trigger.
    -   Provides clear confirmation or error messages in Slack.
-   **Reminder Scheduling (Admin):**
    -   `/schedule-reminder`: Schedule one-time or weekly reminders (e.g., "add items before Friday").
    -   `/list-reminders`: View scheduled reminders.
    -   `/delete-reminder`: Remove a scheduled reminder.
-   **Persistent Storage:** Uses SQLite for the shopping list.
-   **Configurable:** Uses environment variables for secrets and settings.

## Workflow

1.  **Add Items:** Users `@mention` the `@ShoppingAgent` in the designated channel or a thread initiated by the bot. Example: `@ShoppingAgent Please add 3 bananas and 1 gallon of milk.`
2.  **Agent Processing:** The bot uses LangChain/OpenAI to parse the message, identify items and quantities, potentially look up prices, and adds them to the `shopping_list.db` database, associating them with the user.
3.  **View List:** Any user can ask `@ShoppingAgent show me the list`.
4.  **Order Placement (Admin):** An admin user runs the `/order-placed` command.
5.  **API Call:** The Slack agent retrieves the active list, formats it, and sends a POST request to the `/trigger-shopping-run` endpoint of the configured Target Automation Agent (using `STAGEHAND_API_ENDPOINT` and `STAGEHAND_API_KEY` from `.env`).
6.  **Outcome:**
    *   **Success (API returns 202):** The agent marks the items as `ordered` in the database and posts a confirmation message to the channel summarizing the triggered order.
    *   **Failure (API returns other status or error):** The agent sends an ephemeral error message to the admin, and the items remain `active` in the database.
7.  **Reminders:** Admins can use `/schedule-reminder`, `/list-reminders`, and `/delete-reminder` to manage automated messages posted to the channel.

## Technical Stack

-   Python 3.11+
-   `slack_bolt`: Slack SDK for Python
-   `langchain` & `langchain-openai`: LLM orchestration and OpenAI integration
-   `openai`: OpenAI API client
-   `requests`: For making HTTP calls to the Target Automation Agent
-   `playwright` & `beautifulsoup4`: For web scraping product prices (experimental)
-   `apscheduler`: For scheduling reminders
-   `python-dotenv`: For managing environment variables
-   SQLite: For database storage
-   FastAPI/Uvicorn: Web framework (primarily for running the bot process)

## Setup

1.  **Prerequisites:** Python 3.11+, pip.
2.  **Clone:** `git clone <repository-url>`
3.  **Navigate:** `cd slack_shopping_agent`
4.  **Virtual Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/macOS
    # venv\Scripts\activate    # Windows
    ```
5.  **Install Dependencies:** `pip install -r requirements.txt`
6.  **Install Playwright Browsers:** `playwright install` (Needed for price fetching)
7.  **Configure Environment:**
    *   Copy `.env.example` to `.env`.
    *   Edit `.env` and fill in your actual secrets and configuration (see [Environment Variables](#environment-variables)). **Crucially, set `STAGEHAND_API_ENDPOINT` and `STAGEHAND_API_KEY` to point to your running Target Automation Agent.**
8.  **Run the Agent:** `python main.py`

## Usage

-   **Adding Items:** `@ShoppingAgent add [quantity] [item name]` (e.g., `@ShoppingAgent add 2 boxes of cereal`)
-   **Viewing List:** `@ShoppingAgent show list` or `@ShoppingAgent what's on the list?`
-   **Removing Items:** `@ShoppingAgent remove apples`
-   **Placing Order (Admin Only):** `/order-placed`
-   **Scheduling Reminder (Admin Only):** `/schedule-reminder [once HH:MM | weekly day HH:MM] message`
-   **Listing Reminders (Admin Only):** `/list-reminders`
-   **Deleting Reminder (Admin Only):** `/delete-reminder [job_id]`

## Environment Variables

Create a `.env` file in the project root with the following variables:

```dotenv
# Slack Bot Credentials
SLACK_AGENT_TOKEN="xoxb-your-slack-bot-token" # Bot User OAuth Token
SLACK_SIGNING_SECRET="your-slack-signing-secret" # App Credentials -> Signing Secret

# OpenAI API Key
OPENAI_API_KEY="sk-your-openai-api-key"

# Slack Channel Configuration
TARGET_CHANNEL_ID="C123ABC456" # ID of the channel where the bot operates

# Database Configuration
DATABASE_PATH="./shopping_list.db" # Path to the SQLite database file

# Target Automation Agent Configuration
STAGEHAND_API_ENDPOINT="https://your-target-automation-agent-url.com" # URL of the Target Automation Agent (TypeScript service)
STAGEHAND_API_KEY="your-shared-api-key-for-automation-agent" # API key for the Target Automation Agent

# --- Deprecated Variables (No longer used) ---
# EXPORT_DIR="./exports"
# EXPORT_FORMAT="json"
# TARGET_AUTOMATION_PATH="./target_automation.py"
```

## Project Structure

```
slack_shopping_agent/
├── .env                # Local environment variables (DO NOT COMMIT)
├── .env.example        # Example environment variables
├── .gitignore          # Git ignore rules
├── main.py             # Main application entry point, initializes FastAPI & Bolt
├── agent_executor.py   # Handles LangChain agent setup and invocation
├── database.py         # SQLite database interactions (add, get, mark ordered)
├── product_service.py  # Fetches product details (price) using web scraping
├── scheduler.py        # Manages scheduled reminders using APScheduler
├── slack_handler.py    # Handles Slack events (mentions, commands) via Bolt
├── utils.py            # Utility functions (e.g., price formatting)
├── requirements.txt    # Python dependencies
├── shopping_list.db    # SQLite database file (created on first run)
├── feature_plan.md     # Outline of features and technical stack
└── README.md           # This file
```

## Future Enhancements

-   Improve price fetching accuracy and reliability.
-   Allow users to clear only *their own* items from the list.
-   More detailed error reporting (e.g., specific API errors).
-   Use Slack Modals for UI improvements (e.g., scheduling, list management).
-   Containerize the application using Docker.
-   Add unit and integration tests.