# Slack Shopping Bot (LangChain Version)

This project implements a Slack bot that acts as a conversational agent to manage a shared weekly shopping list, primarily focusing on products from Target.com. It uses LangChain with an OpenAI model (GPT-4 Turbo recommended) to understand natural language requests and interact with various tools for scraping, searching, and database management.

## Features

* **Conversational Interaction:** Add, search, view, and delete items using natural language by mentioning the bot in Slack (e.g., `@ShopBot add https://...`, `@ShopBot find milk`).
* **Add Item via URL:** Provide a Target.com product URL, and the bot will scrape details (title, price) and ask for quantity before adding.
* **Search Items:** Ask the bot to find items on Target (e.g., "find cheap snacks"). It will present options, and you can choose one to add after specifying quantity.
* **View List:** Ask the bot "what's on the list?" to see all currently active items.
* **Delete Item:** Ask the bot to remove an item you added using its description or ID (e.g., "delete the cookies", "remove item id 5").
* **Weekly Reminder:** Automatically posts a reminder message to a designated channel every Friday at 5 PM (configurable timezone).
* **Order Placement:** An admin can use the `/order-placed` slash command to notify the channel that the order has been made and clear the active list.
* **Context-Aware:** Uses conversation memory to handle multi-step interactions like asking for quantity after finding/scraping an item.

## Technology Stack

* **Python:** Core programming language (3.10+).
* **LangChain:** Framework for building language model applications, handling the agent logic, tool usage, and memory.
* **OpenAI API:** Powers the language model (GPT-4 Turbo recommended) for understanding and responding.
* **FastAPI:** Asynchronous web framework for handling incoming Slack requests.
* **Slack Bolt for Python:** SDK for simplifying Slack API interactions and event handling.
* **Playwright:** For robust browser automation to scrape JavaScript-heavy Target product pages.
* **BeautifulSoup4:** For parsing HTML content obtained via Playwright.
* **APScheduler:** For scheduling the weekly reminder message.
* **SQLite:** Simple file-based database for storing the shopping list.
* **Docker:** For containerizing the application, ensuring consistent deployment and managing dependencies like Playwright.
* **Uvicorn:** ASGI server to run the FastAPI application.

## Setup & Installation

**Prerequisites:**

* Git installed.
* Python 3.10 or higher installed.
* Docker installed and running.
* A GitHub account (or GitLab/Bitbucket).
* A Slack workspace where you can install apps.
* An OpenAI API key ([platform.openai.com](https://platform.openai.com/)).
* A Render account ([render.com](https://render.com/)).

**Steps:**

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/YourUsername/slack-shopping-bot-langchain.git](https://github.com/YourUsername/slack-shopping-bot-langchain.git) # Replace with your repo URL
    cd slack-shopping-bot-langchain
    ```

2.  **Environment Variables (Local Development):**
    * Create a file named `.env` in the project root.
    * **IMPORTANT:** Add `.env` to your `.gitignore` file. **Do NOT commit your `.env` file.**
    * Add the following keys, replacing the placeholder values:
        ```dotenv
        # .env - For local development ONLY
        SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
        SLACK_SIGNING_SECRET=your-slack-signing-secret
        OPENAI_API_KEY=sk-your-openai-api-key
        TARGET_CHANNEL_ID=C123ABC456 # ID of the Slack channel for reminders/notifications
        DATABASE_PATH=./shopping_list.db # Path for local SQLite file
        TZ=Asia/Jerusalem # Or your local timezone e.g., America/New_York, Europe/London
        ```

3.  **Install Python Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Install Playwright Browsers:**
    * This downloads the necessary browser binaries for Playwright.
    ```bash
    playwright install --with-deps chromium
    # Or: playwright install --with-deps # Installs browsers listed in playwright config (if any)
    ```

5.  **Initialize Database (Local):**
    * The application will automatically create the `shopping_list.db` file (using the `DATABASE_PATH` from `.env`) and run the schema setup (`db_schema.sql`) the first time it starts if the file doesn't exist.

## Running Locally (Optional)

Running locally is useful for testing and debugging but requires exposing your local server to the internet for Slack API calls.

1.  **Ensure `.env` file is configured** with your test tokens/keys.
2.  **Use `ngrok` or a similar tool:**
    * Download and install ngrok ([ngrok.com](https://ngrok.com/)).
    * Expose your local port (default is 8000):
        ```bash
        ngrok http 8000
        ```
    * ngrok will provide a public HTTPS URL (e.g., `https://randomstring.ngrok.io`).
3.  **Update Slack App URLs:** Go to your Slack app settings and temporarily update the Request URLs for Event Subscriptions, Interactivity, and Slash Commands to use your `ngrok` HTTPS URL (e.g., `https://randomstring.ngrok.io/slack/events`).
4.  **Run the application:**
    ```bash
    uvicorn main:api --host 0.0.0.0 --port 8000 --reload
    ```
5.  Test interactions by mentioning the bot in your test Slack channel. Remember to revert Slack URLs when deploying.

## Deployment (Render)

This application is designed to be deployed easily on Render using Docker.

1.  **Push Code:** Ensure your latest code (including `Dockerfile`, `.gitignore`, etc., but **NOT** `.env`) is pushed to your GitHub repository.
2.  **Create Render Web Service:**
    * Log in to Render -> New + -> Web Service.
    * Connect your GitHub repository.
    * Configure:
        * **Name:** Choose a unique name (e.g., `slack-shop-bot-lc`).
        * **Region:** Select a suitable region.
        * **Branch:** `main` (or your deployment branch).
        * **Runtime:** **Docker**. Render should detect `Dockerfile`.
        * **Instance Type:** **Free** (or a paid tier if needed).
3.  **Add Persistent Disk:**
    * Scroll to "Disks" -> "Add Disk".
    * **Name:** e.g., `database-disk`.
    * **Mount Path:** `/data` (Crucial!).
    * **Size (GB):** `1` (Minimum).
4.  **Configure Environment Variables (Render UI):**
    * Go to the "Environment" section for your new service.
    * Add the following environment variables (do NOT use a `.env` file here):
        * `SLACK_BOT_TOKEN`: Your production Slack bot token.
        * `SLACK_SIGNING_SECRET`: Your production Slack signing secret.
        * `OPENAI_API_KEY`: Your OpenAI API key.
        * `TARGET_CHANNEL_ID`: The production Slack channel ID.
        * `DATABASE_PATH`: `/data/shopping_list.db` (Points to the persistent disk).
        * `PYTHONUNBUFFERED`: `1`
        * `TZ`: `Asia/Jerusalem` (Or your desired production timezone).
        * `PORT`: `8000` (Render sets this, but good to include).
5.  **Deploy:** Click "Create Web Service". Wait for the build and deployment process to complete. Monitor the logs for errors.
6.  **Update Slack App Request URLs:**
    * Copy your service's URL from Render (e.g., `https://your-app-name.onrender.com`).
    * Go back to your Slack App configuration page ([api.slack.com/apps](https://api.slack.com/apps)).
    * Update the Request URLs for:
        * **Event Subscriptions:** `https://your-app-name.onrender.com/slack/events`
        * **Interactivity & Shortcuts:** `https://your-app-name.onrender.com/slack/interactive`
        * **Slash Commands (`/order-placed`):** `https://your-app-name.onrender.com/slack/commands`
    * Save changes in Slack. You might need to reinstall the app to your workspace.

## Slack App Configuration Summary

Ensure your Slack App is configured with:

* **Bot User:** Added.
* **Scopes (Bot Token):** `app_mentions:read`, `chat:write`, `commands`, `users:read`, `channels:history`.
* **Event Subscriptions:** Enabled, Request URL set, Subscribed to `app_mention` bot event.
* **Interactivity & Shortcuts:** Enabled, Request URL set (even if no interactive components are currently used).
* **Slash Commands:** `/order-placed` command created, Request URL set.
* **Credentials:** Bot Token (`SLACK_BOT_TOKEN`) and Signing Secret (`SLACK_SIGNING_SECRET`) copied securely.
* **Installation:** App installed to your workspace, Bot invited to the `TARGET_CHANNEL_ID`.

## Usage

* **Primary Interaction:** Mention the bot in the designated Slack channel: `@YourBotName <your request>`.
* **Examples:**
    * `@YourBotName add https://www.target.com/p/tide-pods...` (Follow prompts for quantity)
    * `@YourBotName can you find laundry detergent?` (Review options, confirm selection, provide quantity)
    * `@YourBotName what is on the shopping list?`
    * `@YourBotName please delete the Tide Pods`
    * `@YourBotName remove item 3` (If you know the Item ID from viewing the list)
    * `@YourBotName hello` (Test basic interaction)
* **Admin Command:** Use `/order-placed` to mark the list as complete and notify the channel.

## Important Notes & Caveats

* 🚨 **Target Scraping:** The CSS selectors used in `product_service.py` to scrape Target.com are **highly likely to break** when Target updates their website. This is the most fragile part of the bot and will require manual updates to the selectors periodically. Consider using professional scraping APIs for more robustness if needed.
* **LLM Reliability:** The bot's understanding of natural language depends on the OpenAI model and the quality of the system prompt (`agent_executor.py`). Complex or ambiguous requests might be misunderstood. The prompt may need tuning based on observed behavior.
* **API Costs:** Using the OpenAI API (especially GPT-4) for processing messages incurs costs. Monitor your OpenAI usage. Consider using GPT-3.5 Turbo for lower costs if acceptable.
* **Security:** Keep your Slack tokens, signing secret, and OpenAI API key secure. Do not commit them directly into your code or `.env` file in Git history. Use Render's environment variable management.
* **Error Handling:** Basic error handling is included, but can be improved for more specific user feedback. Check Render logs for detailed error information.

## Future Improvements

* Implement more robust scraping (e.g., using scraping APIs, fallback methods).
* Add support for more stores.
* Refine error handling and provide more informative feedback to users.
* Implement item editing (e.g., changing quantity).
* Use a more persistent memory backend (e.g., Redis, database) instead of in-memory for better scalability.
* Add more sophisticated user permission checks (e.g., admin roles).
* Improve fuzzy matching for deleting items by description.
