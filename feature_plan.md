# Slack Shopping Agent - Feature Plan

This document outlines the planned features and workflow for the Slack Shopping Agent.

## Core Functionality

-   **Add Items:** Users can mention the bot (`@ShoppingAgent`) in the target channel or a thread initiated by the bot to add items to the shopping list. The agent uses LangChain and OpenAI to parse the request and add the item(s) to the SQLite database (`shopping_list.db`).
    -   Includes quantity and attempts to fetch price using Playwright/BeautifulSoup.
    -   Recognizes user requests to remove items or clear their own items.
    -   Stores the Slack `user_id` and fetches the user's display name.
-   **View List:** Users can ask the agent to show the current shopping list.
-   **Clear List (Admin):** An admin user can run the `/order-placed` command.
    -   The agent fetches the active shopping list from the database.
    -   The agent calls the Target Automation Agent's API (`/trigger-shopping-run`) with the list payload and API key (from environment variables).
    -   **On successful API trigger (e.g., 202 Accepted):**
        -   Items in the database are marked as `ordered`.
        -   A confirmation message is posted to the channel, summarizing the triggered items and total cost, grouped by user.
        -   The list is effectively cleared for the next cycle.
    -   **On API failure:**
        -   Items are **not** marked as `ordered`.
        -   An ephemeral error message is sent to the admin, indicating the failure.
-   **Reminder Scheduling (Admin):**
    -   `/schedule-reminder [once HH:MM | weekly day HH:MM] message`: Admins can schedule one-time or recurring weekly reminders to be posted in the target channel.
    -   `/list-reminders`: Admins can view all currently scheduled reminders.
    -   `/delete-reminder [job_id]`: Admins can delete a scheduled reminder by its ID.
-   **Database:** Uses SQLite (`shopping_list.db`) to store items (product title, quantity, price, added timestamp, user ID, user name, ordered status).
-   **Error Handling:** Provides informative error messages (often ephemeral) for command failures, API issues, or permission errors.
-   **Configuration:** Uses `.env` for sensitive information (API keys, tokens) and configuration (channel ID, database path).

## Technical Stack

-   **Language:** Python 3.11+
-   **Framework:** FastAPI (for potential future webhooks, though currently primarily script/Bolt-based)
-   **Slack Integration:** `slack_bolt` SDK
-   **LLM Orchestration:** LangChain
-   **LLM:** OpenAI (GPT-4 Turbo or similar)
-   **Web Scraping (Price):** Playwright + BeautifulSoup4
-   **Scheduling:** APScheduler
-   **Database:** SQLite (via `sqlite3` standard library)
-   **Dependencies:** `python-dotenv`, `requests`

## Setup and Running

1.  Clone the repository.
2.  Create a virtual environment: `python -m venv venv`
3.  Activate the environment: `source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
4.  Install dependencies: `pip install -r requirements.txt`
5.  Install Playwright browsers: `playwright install`
6.  Copy `.env.example` to `.env`.
7.  Fill in the required values in `.env`:
    *   `SLACK_AGENT_TOKEN` (Bot Token)
    *   `SLACK_SIGNING_SECRET`
    *   `OPENAI_API_KEY`
    *   `TARGET_CHANNEL_ID` (Channel where the bot operates)
    *   `STAGEHAND_API_ENDPOINT` (URL of your running Target Automation Agent)
    *   `STAGEHAND_API_KEY` (Shared API key for the Target Automation Agent)
    *   `DATABASE_PATH` (Defaults to `./shopping_list.db`)
8.  Run the agent: `python main.py`

## Next Steps / Future Ideas

-   Improve price fetching reliability (handle more site structures, CAPTCHAs).
-   Add command to view/manage scheduled reminders (`/list-reminders`, `/delete-reminder`). - **DONE**
-   Allow users to clear *only their own* items.
-   More robust error handling and reporting.
-   Explore using Slack Modals for a more interactive UI (e.g., for scheduling reminders).
-   Containerization (Docker).