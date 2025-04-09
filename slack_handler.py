import os
import logging
import re
import requests # Add requests import
import json # Add json import
from typing import Optional, Dict, Set, Mapping
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.ack.async_ack import AsyncAck # Import AsyncAck
from slack_bolt.context.respond import Respond # Import Respond for ephemeral messages in view submissions
from datetime import datetime, timedelta

# Assuming agent_executor.py is in the same directory
from agent_executor import invoke_agent, parse_mandate_rules # Import the new function

logger = logging.getLogger(__name__)

# --- Environment Config ---
TARGET_CHANNEL_ID: Optional[str] = os.getenv("TARGET_CHANNEL_ID")
# Track the agent ID between requests
AGENT_USER_ID: Optional[str] = None # Will be populated on startup/first event
# --- New Environment Variables for Target Automation Agent ---
STAGEHAND_API_ENDPOINT: Optional[str] = os.getenv("STAGEHAND_API_ENDPOINT")
STAGEHAND_API_KEY: Optional[str] = os.getenv("STAGEHAND_API_KEY")
# --- End New ---

# Store threads initiated by the bot
BOT_INITIATED_THREADS: Set[str] = set()

# Cache for user names
USER_NAMES_CACHE: Dict[str, str] = {}

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

async def get_user_display_name(client: AsyncWebClient, user_id: str) -> str:
    """Fetch and return a user's display name from Slack API with caching."""
    global USER_NAMES_CACHE
    
    # Return from cache if available
    if user_id in USER_NAMES_CACHE:
        logger.debug(f"Using cached name for user {user_id}: {USER_NAMES_CACHE[user_id]}")
        return USER_NAMES_CACHE[user_id]
    
    # Default fallback name
    display_name = f"User {user_id}"
    
    try:
        user_info_response = await client.users_info(user=user_id)
        logger.debug(f"User info response: {user_info_response}")
        
        if user_info_response.get("ok"):
            user_data = user_info_response.get("user", {})
            
            # Comprehensive logging of all fields to help diagnose
            logger.info(f"Complete user data for {user_id}: {user_data}")
            
            # Try multiple potential fields for the name
            profile = user_data.get("profile", {})
            real_name = profile.get("real_name") or user_data.get("real_name")
            display_name_field = profile.get("display_name") or profile.get("display_name_normalized")
            
            # Log all possible name fields for debugging
            logger.info(f"User {user_id} name fields - real_name: '{real_name}', " 
                        f"display_name: '{display_name_field}', "
                        f"name: '{user_data.get('name')}', "
                        f"full_name: '{profile.get('real_name_normalized')}'")
            
            # Choose the best name available
            if display_name_field and display_name_field.strip():
                display_name = display_name_field
            elif real_name and real_name.strip():
                display_name = real_name
            elif user_data.get("name"):
                display_name = user_data.get("name")
            else:
                # As a last resort, create a user ID based name
                display_name = f"User {user_id}"
                
            logger.info(f"Final name chosen for {user_id}: '{display_name}'")
            
            # Cache the name
            USER_NAMES_CACHE[user_id] = display_name
            
    except Exception as e:
        logger.error(f"Error fetching user info for {user_id}: {e}", exc_info=True)
        
    return display_name

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

        # Get user name from Slack API
        user_name = await get_user_display_name(client, user_id)

        # Generate a unique session ID for memory (e.g., channel + thread)
        session_id = f"slack_{channel_id}_{thread_ts}"

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
            from utils import format_price 
        except ImportError:
             logger.error("Database or utility functions could not be imported in /order-placed.")
             await ack("Sorry, there was an internal error processing this command.")
             return

        await ack("Processing order placement...")
        
        # Check if user is an admin
        user_id = body.get("user_id")
        channel_id = body.get("channel_id") # Get channel_id for ephemeral messages
        if not user_id:
            # Use ephemeral message for errors before ack is confirmed
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id or body.get("user_id"), # Fallback just in case
                text="Error: Could not identify the user."
            )
            # await say(text="Error: Could not identify the user.", thread_ts=body.get("thread_ts"))
            return
            
        try:
            user_info = await client.users_info(user=user_id)
            is_admin = user_info.get("user", {}).get("is_admin", False)
            
            if not is_admin:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Sorry, only workspace admins can use the /order-placed command."
                )
                logger.warning(f"Non-admin user {user_id} attempted to use /order-placed")
                return
        except Exception as e:
            logger.error(f"Error checking admin status: {e}")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error checking admin permissions. Please try again later."
            )
            return
            
        # --- New Logic: Fetch items and call Target API ---
        try:
            items = get_active_items()
            if not items:
                 # Use ephemeral message as main message might not be sent yet
                 await client.chat_postEphemeral(
                     channel=channel_id,
                     user=user_id,
                     text="There were no active items on the list to process."
                 )
                 # await say(text="There were no active items on the list to mark as ordered.")
                 return

            # Check if API endpoint and key are configured
            if not STAGEHAND_API_ENDPOINT or not STAGEHAND_API_KEY:
                logger.error("STAGEHAND_API_ENDPOINT or STAGEHAND_API_KEY environment variables not set.")
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Error: Target Automation Agent endpoint or API key is not configured. Please contact the administrator."
                )
                return

            # Format payload for the Target Automation Agent
            items_payload = [
                {"product_title": item['product_title'], "quantity": item['quantity']}
                for item in items if item.get('product_title') # Ensure title exists
            ]

            if not items_payload:
                 logger.warning("No valid items found to send to Target Automation Agent after filtering.")
                 await client.chat_postEphemeral(
                     channel=channel_id,
                     user=user_id,
                     text="Warning: No items with valid names found in the active list. Nothing sent to automation."
                 )
                 return

            # Construct API details
            api_url = f"{STAGEHAND_API_ENDPOINT.rstrip('/')}/trigger-shopping-run"
            headers = {
                'Content-Type': 'application/json',
                'x-api-key': STAGEHAND_API_KEY
            }
            
            logger.info(f"Attempting to trigger Target Automation Agent at {api_url} with {len(items_payload)} items.")

            # Make the API call
            response = requests.post(api_url, headers=headers, json=items_payload, timeout=30)

            # --- Handle API Response ---
            if response.status_code == 202:
                logger.info(f"Successfully triggered Target Automation Agent. Response: {response.status_code}")
                
                # Mark items as ordered in the database ONLY on success
                num_ordered = mark_all_ordered()
                
                # Prepare success notification (similar structure to before, but confirming trigger)
                channel_to_notify = TARGET_CHANNEL_ID or channel_id
                if not channel_to_notify:
                    logger.warning("No channel ID found for order placed notification.")
                    await client.chat_postEphemeral(
                         channel=channel_id, 
                         user=user_id, 
                         text=f"Marked {num_ordered} items as ordered, but couldn't determine which channel to notify."
                    )
                    return # Stop here if we can't notify

                # Group items by user for the notification (reuse existing logic)
                items_by_user = {}
                for item in items:
                    item_user_id = item.get('user_id', 'unknown')
                    user_name = await get_user_display_name(client, item_user_id)
                    if user_name not in items_by_user:
                        items_by_user[user_name] = []
                    items_by_user[user_name].append(item)
                
                total_price = sum(item.get('price', 0) * item.get('quantity', 1) for item in items if item.get('price') is not None)
                
                message_lines = [
                    f"✅ Target automation run successfully triggered for {num_ordered} items (Total: {format_price(total_price)}):"
                ]
                for user_name, user_items in items_by_user.items():
                    user_total = sum(item.get('price', 0) * item.get('quantity', 1) for item in user_items if item.get('price') is not None)
                    user_items_count = sum(item.get('quantity', 1) for item in user_items)
                    message_lines.append(f"\n👤 *{user_name}* ({user_items_count} items, subtotal: {format_price(user_total)}):")
                    for item in user_items:
                        item_price = item.get('price', 0) * item.get('quantity', 1) if item.get('price') is not None else 0
                        message_lines.append(f"• {item['quantity']} x {item['product_title']} ({format_price(item_price)})")
                
                message_lines.append("\nThe shopping list has been cleared.")
                
                # Post the public success message
                await client.chat_postMessage(
                    channel=channel_to_notify,
                    text="\n".join(message_lines)
                )
                logger.info(f"Automation trigger notification sent for {num_ordered} items to {channel_to_notify}.")

            else:
                # Handle API failure - DO NOT mark items as ordered
                error_details = f"Status Code: {response.status_code}, Response: {response.text[:200]}" # Limit response length
                logger.error(f"Failed to trigger Target Automation Agent. {error_details}")
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"❌ Failed to trigger Target automation ({error_details}). The shopping list has *not* been cleared. Please try again later or check the logs."
                )

        except requests.exceptions.RequestException as e:
             # Handle network/request errors - DO NOT mark items as ordered
             logger.error(f"Error calling Target Automation Agent API: {e}", exc_info=True)
             await client.chat_postEphemeral(
                 channel=channel_id,
                 user=user_id,
                 text=f"❌ Network error communicating with Target automation: {e}. The shopping list has *not* been cleared. Please check the connection or try again later."
             )
             
        except Exception as e:
            # Catch-all for other potential errors during API call/processing
            logger.error(f"Unexpected error during order placement processing: {e}", exc_info=True)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"An unexpected error occurred: {e}. The shopping list status is uncertain. Please check the logs."
            )
            # Note: We don't mark as ordered here either, as the state is uncertain

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
        
        # Get user name using our improved function
        user_name = await get_user_display_name(client, user_id)

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

    @app.command("/schedule-reminder")
    async def handle_schedule_reminder(ack: AsyncAck, body: dict, client: AsyncWebClient, logger_from_context):
        """Handle the /schedule-reminder command to schedule custom reminders."""
        await ack()  # Acknowledge the command immediately
        
        # Get the user ID to check if they're an admin
        user_id = body.get("user_id")
        channel_id = body.get("channel_id")
        
        if not user_id:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error: Could not identify the user. Please try again."
            )
            return
        
        # Check if the user is an admin
        try:
            user_info = await client.users_info(user=user_id)
            is_admin = user_info.get("user", {}).get("is_admin", False)
            
            if not is_admin:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Sorry, only workspace admins can schedule reminders."
                )
                logger_from_context.warning(f"Non-admin user {user_id} attempted to use /schedule-reminder")
                return
        except Exception as e:
            logger_from_context.error(f"Error checking admin status: {e}")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error checking admin permissions. Please try again later."
            )
            return
        
        # Parse command text
        command_text = body.get("text", "").strip()
        
        if not command_text:
            # Show help if no arguments are provided
            help_text = """
*Schedule a reminder with `/schedule-reminder`*

*Usage:*
• One-time reminder: `/schedule-reminder once HH:MM Your reminder message`
• Weekly reminder: `/schedule-reminder weekly day HH:MM Your reminder message`

*Examples:*
• `/schedule-reminder once 15:30 Time to review the shopping list!`
• `/schedule-reminder weekly fri 17:00 Add items to the shopping list before the weekend!`

*Days:* mon, tue, wed, thu, fri, sat, sun
*Time:* 24-hour format (e.g., 14:30 for 2:30 PM)

Use `/list-reminders` to see all scheduled reminders.
            """
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=help_text
            )
            return
        
        # Parse command arguments
        args = command_text.split()
        
        if len(args) < 3:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error: Not enough arguments. Type `/schedule-reminder` for usage help."
            )
            return
        
        # Import the scheduler functions
        from scheduler import schedule_custom_reminder
        
        try:
            schedule_type = args[0].lower()
            
            if schedule_type == "once":
                # Format: /schedule-reminder once HH:MM message
                time_str = args[1]
                message = " ".join(args[2:])
                
                # Parse the time
                try:
                    hour, minute = map(int, time_str.split(":"))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError("Invalid time range")
                except:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Error: Invalid time format. Please use HH:MM in 24-hour format."
                    )
                    return
                
                # Get current date
                now = datetime.now()
                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                
                # If the time is in the past for today, schedule it for tomorrow
                if target_time < now:
                    target_time = target_time + timedelta(days=1)
                
                # Schedule the reminder
                job_id = await schedule_custom_reminder(target_time, message, client)
                
                if job_id:
                    formatted_time = target_time.strftime("%Y-%m-%d %H:%M")
                    
                    # Send ephemeral confirmation to admin
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"✅ One-time reminder scheduled for {formatted_time} (Israel time):\n> {message}"
                    )
                else:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Failed to schedule the reminder. Please try again."
                    )
                
            elif schedule_type == "weekly":
                # Format: /schedule-reminder weekly day HH:MM message
                if len(args) < 4:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Error: Not enough arguments for weekly reminder. Format: `/schedule-reminder weekly day HH:MM message`"
                    )
                    return
                
                day_str = args[1].lower()
                time_str = args[2]
                message = " ".join(args[3:])
                
                # Convert day string to day_of_week number
                day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
                if day_str not in day_map:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Error: Invalid day. Use mon, tue, wed, thu, fri, sat, or sun."
                    )
                    return
                    
                day_of_week = day_map[day_str]
                
                # Parse the time
                try:
                    hour, minute = map(int, time_str.split(":"))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError("Invalid time range")
                except:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Error: Invalid time format. Please use HH:MM in 24-hour format."
                    )
                    return
                
                # Schedule the weekly reminder
                job_id = await schedule_custom_reminder(
                    None,  # No specific date for weekly reminders
                    message,
                    client,
                    is_weekly=True,
                    day_of_week=day_of_week,
                    hour=hour,
                    minute=minute
                )
                
                if job_id:
                    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    day_name = day_names[day_of_week]
                    
                    # Send ephemeral confirmation to admin
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"✅ Weekly reminder scheduled for every {day_name} at {hour:02d}:{minute:02d} (Israel time):\n> {message}"
                    )
                else:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Failed to schedule the weekly reminder. Please try again."
                    )
            
            else:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"Error: Unknown schedule type '{schedule_type}'. Use 'once' or 'weekly'."
                )
        
        except Exception as e:
            logger_from_context.error(f"Error scheduling reminder: {e}", exc_info=True)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Error scheduling reminder: {str(e)}"
            )

    @app.command("/list-reminders")
    async def handle_list_reminders(ack: AsyncAck, body: dict, client: AsyncWebClient, logger_from_context):
        """Handle the /list-reminders command to view all scheduled reminders."""
        await ack()  # Acknowledge the command immediately
        
        # Get the user ID to check if they're an admin
        user_id = body.get("user_id")
        channel_id = body.get("channel_id")
        
        if not user_id:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error: Could not identify the user. Please try again."
            )
            return
        
        # Check if the user is an admin
        try:
            user_info = await client.users_info(user=user_id)
            is_admin = user_info.get("user", {}).get("is_admin", False)
            
            if not is_admin:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Sorry, only workspace admins can view scheduled reminders."
                )
                logger_from_context.warning(f"Non-admin user {user_id} attempted to use /list-reminders")
                return
        except Exception as e:
            logger_from_context.error(f"Error checking admin status: {e}")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error checking admin permissions. Please try again later."
            )
            return
        
        # Import the scheduler
        from scheduler import get_all_reminders
        
        try:
            # Get all scheduled reminders
            reminders = get_all_reminders()
            
            if not reminders:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="There are no scheduled reminders."
                )
                return
            
            # Format the list of reminders
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            now = datetime.now()
            
            reminder_blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "📆 Scheduled Reminders",
                        "emoji": True
                    }
                },
                {
                    "type": "divider"
                }
            ]
            
            # Group reminders by type
            one_time_reminders = []
            weekly_reminders = []
            
            for job_id, reminder in reminders.items():
                if reminder["type"] == "once":
                    one_time_reminders.append((job_id, reminder))
                elif reminder["type"] == "weekly":
                    weekly_reminders.append((job_id, reminder))
            
            # Sort one-time reminders by date
            one_time_reminders.sort(key=lambda x: x[1]["run_date"])
            
            # Sort weekly reminders by day of week then time
            weekly_reminders.sort(key=lambda x: (x[1]["day_of_week"], x[1]["hour"], x[1]["minute"]))
            
            # Add one-time reminders
            if one_time_reminders:
                reminder_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*⏰ One-time Reminders*"
                    }
                })
                
                for job_id, reminder in one_time_reminders:
                    run_date = datetime.fromisoformat(reminder["run_date"])
                    formatted_date = run_date.strftime("%Y-%m-%d %H:%M")
                    
                    reminder_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"• *{formatted_date}* (ID: {job_id})\n> {reminder['message']}"
                        }
                    })
            
            # Add weekly reminders
            if weekly_reminders:
                reminder_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*🔄 Weekly Reminders*"
                    }
                })
                
                for job_id, reminder in weekly_reminders:
                    day_name = day_names[reminder["day_of_week"]]
                    time_str = f"{reminder['hour']:02d}:{reminder['minute']:02d}"
                    
                    reminder_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"• *Every {day_name} at {time_str}* (ID: {job_id})\n> {reminder['message']}"
                        }
                    })
            
            # Send the ephemeral message with blocks
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                blocks=reminder_blocks
            )
            
        except Exception as e:
            logger_from_context.error(f"Error listing reminders: {e}", exc_info=True)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Error listing reminders: {str(e)}"
            )

    # --- New: /set-mandate Command ---
    @app.command("/set-mandate")
    async def handle_set_mandate(ack: AsyncAck, body: dict, client: AsyncWebClient, logger):
        """Handles the /set-mandate command to open a modal for setting global mandate rules."""
        await ack() # Acknowledge the command immediately
        
        user_id = body.get("user_id")
        channel_id = body.get("channel_id") # Used for sending ephemeral messages

        logger.info(f"Received /set-mandate command from user {user_id} in channel {channel_id}")
        logger.info(f"Request body keys: {list(body.keys())}")
        
        if not user_id:
            logger.error("Could not identify user in /set-mandate command.")
            return

        # --- Admin Check ---
        try:
            user_info = await client.users_info(user=user_id)
            is_admin = user_info.get("user", {}).get("is_admin", False)

            if not is_admin:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Sorry, only workspace admins can set mandate rules."
                )
                logger.warning(f"Non-admin user {user_id} attempted to use /set-mandate")
                return
        except Exception as e:
            logger.error(f"Error checking admin status for /set-mandate: {e}")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error checking admin permissions. Please try again later."
            )
            return
        # --- End Admin Check ---

        # --- Open Modal ---
        try:
            logger.info(f"Attempting to open set_mandate modal for user {user_id}")
            await client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "set_mandate_modal", # Important for the submission handler
                    "private_metadata": channel_id or '', # Pass channel_id here
                    "title": {"type": "plain_text", "text": "Agent Payment Mandate"}, # Shortened title
                    "submit": {"type": "plain_text", "text": "Submit Rules"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "Enter the rules for the agent's payment permissions here. Use natural language"
                            }
                        },
                        {
                            "type": "input",
                            "block_id": "mandate_rules_block", # ID for the block
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "mandate_rules_input", # ID for the input element
                                "multiline": True,
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "e.g., Max transaction $200\nAllow: Target, Amazon\nRequire approval > $100" # Shortened placeholder
                                }
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "Mandate Rules (Free Text)"
                            }
                        }
                    ]
                }
            )
            logger.info(f"Opened set_mandate modal for admin user {user_id}")
        except Exception as e:
            logger.error(f"Failed to open set_mandate modal: {str(e)}", exc_info=True)
            # Log more details about the error
            if hasattr(e, 'response') and hasattr(e.response, 'data'):
                logger.error(f"API Response: {e.response.data}")
            if 'trigger_id' in body:
                logger.info(f"Trigger ID: {body['trigger_id']}")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Sorry, I couldn't open the mandate settings modal. Error: {str(e)}"
            )
    # --- End /set-mandate Command ---

    # --- New View Submission Handler ---
    @app.view("set_mandate_modal")
    async def handle_set_mandate_submission(ack: AsyncAck, body: dict, client: AsyncWebClient, view: dict, logger, respond: Respond):
        """Handles the submission of the set_mandate modal."""
        await ack() # Acknowledge the submission immediately

        user_id = body["user"]["id"]
        submitted_values = view["state"]["values"]
        mandate_rules_text = submitted_values["mandate_rules_block"]["mandate_rules_input"]["value"]
        original_channel_id = view.get("private_metadata")

        logger.info(f"Received mandate rules submission from user {user_id} (Original Channel: {original_channel_id}) Text: '{mandate_rules_text}'")

        # --- New: Parse rules using LLM --- 
        parsed_json_string = ""
        parsed_mandate_object = None
        error_message = None

        try:
            parsed_json_string = await parse_mandate_rules(mandate_rules_text)
            parsed_mandate_object = json.loads(parsed_json_string) # Try parsing the JSON string
            
            # Check if the parsing itself returned an error object
            if isinstance(parsed_mandate_object, dict) and "error" in parsed_mandate_object:
                error_message = f"Error parsing rules: {parsed_mandate_object.get('error')}" 
                if 'raw_output' in parsed_mandate_object:
                     error_message += f"\nLLM Output: ```{parsed_mandate_object['raw_output']}```"
                logger.error(f"Mandate parsing failed: {error_message}")
                parsed_mandate_object = None # Clear the object if it was an error indicator
            else:
                 logger.info(f"Successfully parsed rules into object: {parsed_mandate_object}")
                 # TODO: Store the parsed_mandate_object persistently here!

        except json.JSONDecodeError as json_e:
            logger.error(f"Failed to decode JSON response from parse_mandate_rules: {json_e}. Raw string: '{parsed_json_string}'", exc_info=True)
            error_message = f"Error: Could not decode the parsed rules structure. Please check the format.\nRaw LLM Output: ```{parsed_json_string}```"
            parsed_mandate_object = None
        except Exception as e:
            logger.error(f"Unexpected error during mandate parsing call: {e}", exc_info=True)
            error_message = f"An unexpected error occurred while processing the rules: {e}"
            parsed_mandate_object = None
        # --- End Parsing --- 

        # --- Send Ephemeral Confirmation --- 
        confirmation_text = ""
        if error_message:
            confirmation_text = f"❌ {error_message}"
        elif parsed_mandate_object is not None:
            # Format the JSON object nicely for display
            formatted_json = json.dumps(parsed_mandate_object, indent=2)
            confirmation_text = f"✅ Mandate rules processed. Here is the structured mandate object:\n```json\n{formatted_json}\n```"
            # Add note about storage being mocked
            confirmation_text += "\n\n_(Note: Mandate storage is not yet implemented. This object is not saved.)_"
        else:
            # Fallback if something unexpected happened
            confirmation_text = "⚠️ Could not process mandate rules. Please check logs or try again."

        if original_channel_id:
            try:
                 await client.chat_postEphemeral(
                     channel=original_channel_id,
                     user=user_id,
                     text=confirmation_text
                 )
                 logger.info(f"Sent mandate submission confirmation/error to user {user_id} in channel {original_channel_id}")
            except Exception as e:
                 logger.error(f"Failed to send ephemeral confirmation/error to original channel {original_channel_id}: {e}", exc_info=True)
                 # Fallback DM attempt
                 try:
                     await client.chat_postEphemeral(channel=user_id, user=user_id, text=confirmation_text + "\n(Could not post to original channel)")
                 except Exception:
                     pass # Ignore DM failure if channel failed
        else:
            # DM attempt if no channel ID
             logger.warning(f"No original_channel_id found. Attempting DM confirmation/error for user {user_id}.")
             try:
                await client.chat_postEphemeral(channel=user_id, user=user_id, text=confirmation_text)
             except Exception as dm_e:
                 logger.error(f"Failed to send DM confirmation/error (no channel_id): {dm_e}", exc_info=True)
        # --- End Confirmation --- 

    # --- End View Submission Handler ---

    # --- New Command: /view-mandate ---
    @app.command("/view-mandate")
    async def handle_view_mandate(ack: AsyncAck, body: dict, client: AsyncWebClient, logger):
        """Handles the /view-mandate command to display the currently set global mandate rules."""
        await ack()

        user_id = body.get("user_id")
        channel_id = body.get("channel_id") # For ephemeral messages

        if not user_id:
            logger.error("Could not identify user in /view-mandate command.")
            # Attempt to send ephemeral message
            try:
                await client.chat_postEphemeral(channel=channel_id or user_id, user=user_id, text="Error: Could not identify the user.")
            except Exception:
                 logger.error("Failed to send ephemeral error message for missing user ID in /view-mandate.")
            return

        # --- Admin Check ---
        try:
            user_info = await client.users_info(user=user_id)
            is_admin = user_info.get("user", {}).get("is_admin", False)

            if not is_admin:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Sorry, only workspace admins can view mandate rules."
                )
                logger.warning(f"Non-admin user {user_id} attempted to use /view-mandate")
                return
        except Exception as e:
            logger.error(f"Error checking admin status for /view-mandate: {e}")
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Error checking admin permissions. Please try again later."
            )
            return
        # --- End Admin Check ---

        # --- Retrieve and Display Mandate (Placeholder) ---
        try:
            # TODO: Replace this with actual logic to retrieve saved mandate rules
            # from a database or configuration file.
            current_mandate_rules = "*Placeholder:* Mandate storage is not yet implemented."
            
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"""📄 *Current Agent Payment Mandate:*

```
{current_mandate_rules}
```

Use `/set-mandate` to define or update these rules."""
            )
            logger.info(f"Displayed placeholder mandate rules to admin user {user_id}")

        except Exception as e:
            logger.error(f"Error retrieving/displaying mandate rules: {e}", exc_info=True)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Sorry, I encountered an error trying to display the mandate rules."
            )
        # --- End Retrieve and Display ---

    # --- End /view-mandate Command ---

    # ... existing @app.command("/delete-reminder") handler ...