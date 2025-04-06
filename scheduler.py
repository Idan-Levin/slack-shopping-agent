import os
import logging
import uuid
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Global scheduler instance - will be initialized by setup_scheduler
global_scheduler = None

# Dictionary to store custom reminder jobs
custom_reminders = {}

async def send_test_message():
    """Sends an immediate test message with a fresh client."""
    channel_id = os.getenv("TARGET_CHANNEL_ID")
    if not channel_id:
        logger.error("TARGET_CHANNEL_ID not set in environment variables. Cannot send test message.")
        return

    token = os.getenv("SLACK_AGENT_TOKEN")
    if not token:
        logger.error("SLACK_AGENT_TOKEN not set in environment. Cannot send test message.")
        return

    # Create a fresh client with explicit token
    test_client = AsyncWebClient(token=token)
    logger.info(f"Created test client with token: {token[:5]}...{token[-5:] if len(token) > 10 else ''}")
    
    try:
        logger.info(f"Attempting to send test message to channel {channel_id}")
        response = await test_client.chat_postMessage(
            channel=channel_id,
            text="🧪 This is a test message to verify Slack authentication. If you see this, the bot can post scheduled messages!"
        )
        logger.info(f"Test message sent successfully. Timestamp: {response.get('ts')}")
        return True
    except SlackApiError as e:
        logger.error(f"Slack API error sending test message: {e.response['error']}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending test message: {e}", exc_info=True)
        return False

async def send_weekly_reminder(client: AsyncWebClient):
    """Sends the weekly shopping list reminder to the target channel."""
    channel_id = os.getenv("TARGET_CHANNEL_ID")
    if not channel_id:
        logger.error("TARGET_CHANNEL_ID not set in environment variables. Cannot send reminder.")
        return

    # Verify client has token set
    if not client.token:
        # Try to get token from environment as fallback
        token = os.getenv("SLACK_AGENT_TOKEN")
        if token:
            logger.warning("Client token missing, using token from environment")
            client.token = token
        else:
            logger.error("No Slack token available in client or environment. Cannot send reminder.")
            return
    
    logger.info(f"Preparing to send reminder with client. Token present: {bool(client.token)}")
    reminder_text = "Friendly reminder! 🛒 Please add any items you need to the shopping list by 5 PM today. Mention me (@ShopAgent) with your request (e.g., `@ShopAgent add https://...` or `@ShopAgent find detergent`)."

    try:
        logger.info(f"Attempting to send weekly reminder to channel {channel_id}")
        await client.chat_postMessage(
            channel=channel_id,
            text=reminder_text
        )
        logger.info(f"Sent weekly reminder to channel {channel_id}")
    except SlackApiError as e:
        logger.error(f"Slack API error sending weekly reminder to {channel_id}: {e.response['error']}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending weekly reminder: {e}", exc_info=True)

async def send_custom_reminder(client: AsyncWebClient, message: str, channel_id: str = None):
    """Sends a custom reminder message to the specified channel."""
    if not channel_id:
        channel_id = os.getenv("TARGET_CHANNEL_ID")
        if not channel_id:
            logger.error("TARGET_CHANNEL_ID not set in environment variables. Cannot send reminder.")
            return

    # Verify client has token set
    if not client.token:
        # Try to get token from environment as fallback
        token = os.getenv("SLACK_AGENT_TOKEN")
        if token:
            logger.warning("Client token missing, using token from environment")
            client.token = token
        else:
            logger.error("No Slack token available in client or environment. Cannot send reminder.")
            return
    
    logger.info(f"Preparing to send custom reminder with client. Token present: {bool(client.token)}")

    try:
        logger.info(f"Attempting to send custom reminder to channel {channel_id}")
        await client.chat_postMessage(
            channel=channel_id,
            text=message
        )
        logger.info(f"Sent custom reminder to channel {channel_id}")
        return True
    except SlackApiError as e:
        logger.error(f"Slack API error sending custom reminder to {channel_id}: {e.response['error']}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending custom reminder: {e}", exc_info=True)
        return False

async def schedule_custom_reminder(
    target_time: datetime = None, 
    message: str = "", 
    client: AsyncWebClient = None,
    is_weekly: bool = False,
    day_of_week: int = None,
    hour: int = None,
    minute: int = None,
    channel_id: str = None
) -> str:
    """
    Schedule a custom reminder.
    
    Args:
        target_time: Datetime for one-time reminders
        message: The reminder message to send
        client: Slack client to use
        is_weekly: Whether this is a weekly recurring reminder
        day_of_week: Day of week (0-6) for weekly reminders
        hour: Hour (0-23) for weekly reminders
        minute: Minute (0-59) for weekly reminders
        channel_id: Target channel ID (defaults to TARGET_CHANNEL_ID)
        
    Returns:
        str: Job ID if successful, None otherwise
    """
    global global_scheduler
    
    if not global_scheduler:
        logger.error("Scheduler not initialized. Cannot schedule custom reminder.")
        return None
    
    if not message:
        logger.error("No message provided for custom reminder.")
        return None
    
    # Use environment token if client not provided
    if not client:
        token = os.getenv("SLACK_AGENT_TOKEN")
        if not token:
            logger.error("No Slack token available. Cannot schedule reminder.")
            return None
        client = AsyncWebClient(token=token)
    
    # Generate a unique job ID
    job_id = f"custom_reminder_{uuid.uuid4().hex[:8]}"
    
    try:
        if is_weekly and day_of_week is not None and hour is not None and minute is not None:
            # Schedule a weekly reminder
            global_scheduler.add_job(
                send_custom_reminder,
                trigger='cron',
                day_of_week=day_of_week,
                hour=hour,
                minute=minute,
                args=[client, message, channel_id],
                id=job_id,
                replace_existing=False
            )
            
            # Store reminder details for management
            custom_reminders[job_id] = {
                "type": "weekly",
                "day_of_week": day_of_week,
                "hour": hour,
                "minute": minute,
                "message": message,
                "channel_id": channel_id
            }
            
            logger.info(f"Scheduled weekly custom reminder (ID: {job_id}) for day {day_of_week} at {hour:02d}:{minute:02d}")
            
        elif target_time:
            # Schedule a one-time reminder
            global_scheduler.add_job(
                send_custom_reminder,
                trigger='date',
                run_date=target_time,
                args=[client, message, channel_id],
                id=job_id,
                replace_existing=False
            )
            
            # Store reminder details for management
            custom_reminders[job_id] = {
                "type": "once",
                "run_date": target_time.isoformat(),
                "message": message,
                "channel_id": channel_id
            }
            
            logger.info(f"Scheduled one-time custom reminder (ID: {job_id}) for {target_time}")
            
        else:
            logger.error("Invalid parameters for scheduling custom reminder.")
            return None
            
        # Save custom reminders to file
        save_custom_reminders()
        
        return job_id
        
    except Exception as e:
        logger.error(f"Error scheduling custom reminder: {e}", exc_info=True)
        return None

def get_all_reminders():
    """
    Get all currently scheduled custom reminders.
    
    Returns:
        dict: Dictionary of reminders mapped by job_id
    """
    global custom_reminders
    return custom_reminders.copy()

def save_custom_reminders():
    """Save custom reminders to a file for persistence."""
    try:
        reminders_to_save = {}
        for job_id, reminder in custom_reminders.items():
            # Create a serializable copy
            reminders_to_save[job_id] = reminder.copy()
        
        with open("custom_reminders.json", "w") as f:
            json.dump(reminders_to_save, f)
        
        logger.info(f"Saved {len(reminders_to_save)} custom reminders to file")
    except Exception as e:
        logger.error(f"Error saving custom reminders: {e}", exc_info=True)

def load_custom_reminders(scheduler, client):
    """Load custom reminders from file and reschedule them."""
    global custom_reminders, global_scheduler
    global_scheduler = scheduler
    
    try:
        if not os.path.exists("custom_reminders.json"):
            logger.info("No custom reminders file found. Starting with empty reminder list.")
            return
            
        with open("custom_reminders.json", "r") as f:
            saved_reminders = json.load(f)
        
        for job_id, reminder in saved_reminders.items():
            try:
                if reminder["type"] == "weekly":
                    scheduler.add_job(
                        send_custom_reminder,
                        trigger='cron',
                        day_of_week=reminder["day_of_week"],
                        hour=reminder["hour"],
                        minute=reminder["minute"],
                        args=[client, reminder["message"], reminder.get("channel_id")],
                        id=job_id,
                        replace_existing=True
                    )
                elif reminder["type"] == "once":
                    run_date = datetime.fromisoformat(reminder["run_date"])
                    # Only schedule if it's in the future
                    if run_date > datetime.now():
                        scheduler.add_job(
                            send_custom_reminder,
                            trigger='date',
                            run_date=run_date,
                            args=[client, reminder["message"], reminder.get("channel_id")],
                            id=job_id,
                            replace_existing=True
                        )
                    else:
                        # Skip expired one-time reminders
                        continue
                
                # Add to memory
                custom_reminders[job_id] = reminder
                logger.info(f"Loaded custom reminder {job_id} from file")
                
            except Exception as e:
                logger.error(f"Error loading reminder {job_id}: {e}")
        
        logger.info(f"Loaded {len(custom_reminders)} custom reminders")
    except Exception as e:
        logger.error(f"Error loading custom reminders file: {e}", exc_info=True)

def setup_scheduler(client: AsyncWebClient):
    """Sets up and starts the APScheduler."""
    global global_scheduler
    
    # Use environment variable for timezone, default to UTC if not set
    scheduler_timezone = os.getenv("TZ", "UTC")
    logger.info(f"Initializing scheduler with timezone: {scheduler_timezone}")
    
    # Verify client has token
    if not client.token:
        logger.warning("Client passed to scheduler has no token!")
        # Try to fix it
        token = os.getenv("SLACK_AGENT_TOKEN")
        if token:
            logger.info("Setting client token from environment variable")
            client.token = token

    scheduler = AsyncIOScheduler(timezone=scheduler_timezone)
    global_scheduler = scheduler

    try:
        # For testing: Schedule a notification at 12:55 PM Israel time today
        test_time = datetime.now()
        # Set hour to 12 and minute to 55
        test_time = test_time.replace(hour=12, minute=55, second=0, microsecond=0)
        
        # Add the test job to trigger at 12:55 PM
        scheduler.add_job(
            send_weekly_reminder,
            'date',  # Use date trigger for a one-time execution
            run_date=test_time,
            args=[client],
            id='test_reminder_job',
            replace_existing=True
        )
        
        # Removed automatic test message on startup
        
        logger.info(f"Added test reminder scheduled for today at 12:55 PM Israel time: {test_time}")
        
        # Keep the regular weekly schedule as well
        scheduler.add_job(
            send_weekly_reminder,
            trigger='cron',
            day_of_week='fri',
            hour=17,
            minute=0,
            args=[client], # Pass the Slack client instance
            id='weekly_reminder_job', # Assign an ID for potential management
            replace_existing=True # Replace job if it already exists (e.g., on restart)
        )

        # Load custom reminders at startup
        load_custom_reminders(scheduler, client)
        
        scheduler.start()
        logger.info("APScheduler started. Weekly reminder scheduled for Fridays at 5 PM.")
        # Log all scheduled jobs
        jobs = scheduler.get_jobs()
        logger.info(f"Scheduled jobs: {[job.id for job in jobs]}")

    except Exception as e:
        logger.error(f"Failed to setup or start scheduler: {e}", exc_info=True)
        # Depending on severity, might want to raise error or handle differently

    return scheduler