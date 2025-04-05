import os
import logging
import re
from typing import Optional, Dict, Set
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.ack.async_ack import AsyncAck # Import AsyncAck

# Assuming agent_executor.py is in the same directory
from agent_executor import invoke_agent

logger = logging.getLogger(__name__)

# --- Environment Config ---
TARGET_CHANNEL_ID: Optional[str] = os.getenv("TARGET_CHANNEL_ID")
# Track the agent ID between requests
AGENT_USER_ID: Optional[str] = None # Will be populated on startup/first event

# Store threads initiated by the bot
BOT_INITIATED_THREADS: Set[str] = set()

async def get_agent_user_id(client: AsyncWebClient):
    """Fetches and caches the Agent User ID."""
    global AGENT_USER_ID
    if AGENT_USER_ID is None:
        try:
            # Use auth.test to get our own user ID
            auth_test = await client.auth_test()
            AGENT_USER_ID = auth_test.get("user_id")
            if AGENT_USER_ID:
                logger.info(f"Successfully fetched Agent User ID: {AGENT_USER_ID}")
            else:
                logger.error(f"Failed to get agent user ID from auth_test response: {auth_test}")
        except Exception as e:
            logger.error(f"Exception while fetching agent user ID: {e}", exc_info=True)
    return AGENT_USER_ID

def register_listeners(app: AsyncApp):
    """Registers event listeners for the Slack Bolt app."""

    @app.event("app_mention") # Trigger when the agent is @mentioned
    async def handle_app_mention(body: dict, client: AsyncWebClient, say: AsyncSay, logger_from_context):
        """Handles mentions of the agent."""
        global AGENT_USER_ID, BOT_INITIATED_THREADS
        if not AGENT_USER_ID:
             await get_agent_user_id(client) # Ensure AGENT_USER_ID is fetched

        event = body.get("event", {})
        text = event.get("text", "")
        user_id = event.get("user")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts")) # Use thread or main message ts

        # Basic validation
        if not all([text, user_id, channel_id, thread_ts]):
             logger_from_context.warning(f"Missing key information in app_mention event: {event}")
             return

        # Track this thread as initiated by the bot
        BOT_INITIATED_THREADS.add(thread_ts)
        
        # Remove the agent mention (e.g., "<@U123ABC> ") from the text
        mention_pattern = r'^<@' + (AGENT_USER_ID or '') + r'>\s*'
        processed_text = re.sub(mention_pattern, '', text).strip()

        if not processed_text:
             await say(text="Hi there! How can I help you with the shopping list?", thread_ts=thread_ts)
             return

        # Fetch user info for name
        user_name = "Unknown User" # Fallback
        try:
            user_info_response = await client.users_info(user=user_id)
            if user_info_response.get("ok"):
                 profile = user_info_response.get("user", {}).get("profile", {})
                 user_name = profile.get("display_name", profile.get("real_name", user_info_response.get("user",{}).get("name", "Unknown User")))
            else:
                logger_from_context.error(f"Error fetching user info for {user_id}: {user_info_response.get('error')}")
        except Exception as e:
            logger_from_context.error(f"Exception fetching user info for {user_id}: {e}", exc_info=True)


        # Generate a unique session ID for memory (e.g., channel + thread)
        session_id = f"slack_{channel_id}_{thread_ts}"

        # Acknowledge receipt (optional, potentially confusing with agent thinking)
        # await say(text="Thinking...", thread_ts=thread_ts)

        # Invoke the LangChain agent
        response_text = await invoke_agent(processed_text, session_id, user_id, user_name)

        # Send the agent's response back to the thread
        try:
            await say(text=response_text, thread_ts=thread_ts)
        except Exception as e:
             logger_from_context.error(f"Failed to send agent response to Slack: {e}", exc_info=True)
             # Optionally send a generic error message
             await say(text="Sorry, I encountered an issue sending my response.", thread_ts=thread_ts)


    @app.command("/order-placed")
    async def handle_order_placed(ack: AsyncAck, body: dict, say: AsyncSay, client: AsyncWebClient):
        """Handles the /order-placed command to clear the list."""
        # Import database functions here or ensure they are accessible
        try:
            from database import mark_all_ordered, get_active_items
        except ImportError:
             logger.error("Database functions could not be imported in /order-placed.")
             await ack("Sorry, there was an internal error processing this command.")
             return

        await ack("Processing order placement...")
        items = get_active_items()
        if not items:
             await say(text="There were no active items on the list to mark as ordered.")
             return

        try:
            num_ordered = mark_all_ordered()
            if num_ordered > 0:
                channel_to_notify = TARGET_CHANNEL_ID or body.get("channel_id")
                if not channel_to_notify:
                    logger.warning("No channel ID found for order placed notification.")
                    await say(text=f"Marked {num_ordered} items as ordered, but couldn't determine which channel to notify.")
                    return

                list_summary = "\n".join([f"- {item['quantity']} x {item['product_title']} (requested by {item['user_name']})" for item in items])
                await client.chat_postMessage(
                    channel=channel_to_notify,
                    text=f"✅ Order has been placed for the following items:\n{list_summary}\n\nThe list has been cleared for next week."
                )
                logger.info(f"Order placed notification sent for {num_ordered} items to {channel_to_notify}.")
            else:
                 await say(text="No active items found to mark as ordered.")
        except Exception as e:
            logger.error(f"Error processing order placement: {e}", exc_info=True)
            await say(text="Sorry, an error occurred while processing the order placement.")

    async def process_message(body: dict, client: AsyncWebClient, say: AsyncSay, logger_from_context):
        """Process a message and generate a response from the agent."""
        event = body.get("event", {})
        text = event.get("text", "").strip()
        user_id = event.get("user")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts"))  # Use thread or main message ts

        # Basic validation
        if not all([text, user_id, channel_id, thread_ts]):
            logger_from_context.warning(f"Missing key information in message event: {event}")
            return
            
        # Skip processing if message is from the bot itself
        if event.get("bot_id") or user_id == AGENT_USER_ID:
            return
        
        # Fetch user info for name
        user_name = "Unknown User"  # Fallback
        try:
            user_info_response = await client.users_info(user=user_id)
            if user_info_response.get("ok"):
                profile = user_info_response.get("user", {}).get("profile", {})
                user_name = profile.get("display_name", profile.get("real_name", user_info_response.get("user", {}).get("name", "Unknown User")))
            else:
                logger_from_context.error(f"Error fetching user info for {user_id}: {user_info_response.get('error')}")
        except Exception as e:
            logger_from_context.error(f"Exception fetching user info for {user_id}: {e}", exc_info=True)

        # Generate a unique session ID for memory
        session_id = f"slack_{channel_id}_{thread_ts}"

        # Invoke the LangChain agent
        response_text = await invoke_agent(text, session_id, user_id, user_name)

        # Send the agent's response back to the thread
        try:
            await say(text=response_text, thread_ts=thread_ts)
        except Exception as e:
            logger_from_context.error(f"Failed to send agent response to Slack: {e}", exc_info=True)
            await say(text="Sorry, I encountered an issue sending my response.", thread_ts=thread_ts)

    @app.event("message") 
    async def handle_message(client: AsyncWebClient, body: dict, say: AsyncSay, logger_from_context):
        """Handle messages, including those in threads started by the agent."""
        global AGENT_USER_ID, BOT_INITIATED_THREADS
        
        if not AGENT_USER_ID:
            await get_agent_user_id(client)
            
        event = body.get("event", {})
        
        # Skip processing if message has a subtype (like join, leave, etc.)
        if event.get("subtype"):
            return
            
        # Skip processing if message is from the bot itself
        if event.get("bot_id") or event.get("user") == AGENT_USER_ID:
            return
            
        # Check if message is in a thread
        thread_ts = event.get("thread_ts")
        
        # If message is in a thread that the bot initiated, process it without requiring mention
        if thread_ts and thread_ts in BOT_INITIATED_THREADS:
            logger.info(f"Processing message in bot-initiated thread {thread_ts}")
            await process_message(body, client, say, logger_from_context)
            return