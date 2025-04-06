import os
import logging
import re
from typing import Optional, Dict, Set, Mapping
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.ack.async_ack import AsyncAck # Import AsyncAck
from datetime import datetime, timedelta

# Assuming agent_executor.py is in the same directory
from agent_executor import invoke_agent

logger = logging.getLogger(__name__)

# --- Environment Config ---
TARGET_CHANNEL_ID: Optional[str] = os.getenv("TARGET_CHANNEL_ID")
# Track the agent ID between requests
AGENT_USER_ID: Optional[str] = None # Will be populated on startup/first event

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
            from utils import format_price, export_shopping_list  # Import utility for exporting
        except ImportError:
             logger.error("Database functions could not be imported in /order-placed.")
             await ack("Sorry, there was an internal error processing this command.")
             return

        await ack("Processing order placement...")
        
        # Check if user is an admin
        user_id = body.get("user_id")
        if not user_id:
            await say(text="Error: Could not identify the user.", thread_ts=body.get("thread_ts"))
            return
            
        try:
            user_info = await client.users_info(user=user_id)
            is_admin = user_info.get("user", {}).get("is_admin", False)
            
            if not is_admin:
                await client.chat_postEphemeral(
                    channel=body.get("channel_id"),
                    user=user_id,
                    text="Sorry, only workspace admins can use the /order-placed command."
                )
                logger.warning(f"Non-admin user {user_id} attempted to use /order-placed")
                return
        except Exception as e:
            logger.error(f"Error checking admin status: {e}")
            await client.chat_postEphemeral(
                channel=body.get("channel_id"),
                user=user_id,
                text="Error checking admin permissions. Please try again later."
            )
            return
            
        items = get_active_items()
        if not items:
             await say(text="There were no active items on the list to mark as ordered.")
             return

        # Export the shopping list before marking as ordered
        try:
            # Get export format preference from environment or default to JSON
            export_format = os.getenv("EXPORT_FORMAT", "json").lower()
            export_path = export_shopping_list(items, export_format=export_format)
            
            if export_path:
                logger.info(f"Shopping list exported to {export_path}")
                
                # Build message with instructions for running the bridge script
                bridge_script_path = os.path.join(os.path.dirname(__file__), "target_bridge.py")
                bridge_cmd = f"python {bridge_script_path} --file \"{export_path}\" --notify"
                
                # Send a private message to the admin about the export
                await client.chat_postEphemeral(
                    channel=body.get("channel_id"),
                    user=user_id,
                    text=f"🔄 Shopping list exported to `{export_path}` for automation\n\n*To run the Target automation:*\n```\n{bridge_cmd}\n```"
                )
            else:
                logger.error("Failed to export shopping list")
                await client.chat_postEphemeral(
                    channel=body.get("channel_id"),
                    user=user_id,
                    text="⚠️ Failed to export shopping list for automation"
                )
        except Exception as e:
            logger.error(f"Error exporting shopping list: {e}", exc_info=True)
            await client.chat_postEphemeral(
                channel=body.get("channel_id"),
                user=user_id,
                text=f"⚠️ Error exporting shopping list: {str(e)}"
            )

        try:
            num_ordered = mark_all_ordered()
            if num_ordered > 0:
                channel_to_notify = TARGET_CHANNEL_ID or body.get("channel_id")
                if not channel_to_notify:
                    logger.warning("No channel ID found for order placed notification.")
                    await say(text=f"Marked {num_ordered} items as ordered, but couldn't determine which channel to notify.")
                    return

                # Group items by user for the notification
                items_by_user = {}
                for item in items:
                    user_id = item.get('user_id', 'unknown')
                    
                    # Get a proper display name for this user
                    if user_id in USER_NAMES_CACHE:
                        user_name = USER_NAMES_CACHE[user_id]
                    else:
                        # Try to get the name from the item
                        user_name = item.get('user_name')
                        if not user_name or user_name.strip() == '':
                            # Fetch from Slack API if needed
                            user_name = await get_user_display_name(client, user_id)
                    
                    # Add to the right group
                    if user_name not in items_by_user:
                        items_by_user[user_name] = []
                    items_by_user[user_name].append(item)
                
                # Calculate total price
                total_price = sum(item.get('price', 0) * item.get('quantity', 1) for item in items if item.get('price') is not None)
                
                # Format the message with items grouped by user
                message_lines = [f"✅ Order has been placed for the following {num_ordered} items (Total: {format_price(total_price)}):"]
                
                for user_name, user_items in items_by_user.items():
                    # Calculate user's subtotal
                    user_total = sum(item.get('price', 0) * item.get('quantity', 1) for item in user_items if item.get('price') is not None)
                    user_items_count = sum(item.get('quantity', 1) for item in user_items)
                    
                    # Add user section
                    message_lines.append(f"\n👤 *{user_name}* ({user_items_count} items, subtotal: {format_price(user_total)}):")
                    
                    # Add items for this user
                    for item in user_items:
                        item_price = item.get('price', 0) * item.get('quantity', 1) if item.get('price') is not None else 0
                        message_lines.append(f"• {item['quantity']} x {item['product_title']} ({format_price(item_price)})")
                
                message_lines.append("\nThe list has been cleared for next week.")
                
                await client.chat_postMessage(
                    channel=channel_to_notify,
                    text="\n".join(message_lines)
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