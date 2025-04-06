import os
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

async def send_weekly_reminder(client: AsyncWebClient):
    """Sends the weekly shopping list reminder to the target channel."""
    channel_id = os.getenv("TARGET_CHANNEL_ID")
    if not channel_id:
        logger.error("TARGET_CHANNEL_ID not set in environment variables. Cannot send reminder.")
        return

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

def setup_scheduler(client: AsyncWebClient):
    """Sets up and starts the APScheduler."""
    # Use environment variable for timezone, default to UTC if not set
    scheduler_timezone = os.getenv("TZ", "UTC")
    logger.info(f"Initializing scheduler with timezone: {scheduler_timezone}")

    scheduler = AsyncIOScheduler(timezone=scheduler_timezone)

    try:
        # For testing: Schedule a notification at 12:35 PM Israel time today
        test_time = datetime.now()
        # Set hour to 12 and minute to 35
        test_time = test_time.replace(hour=12, minute=35, second=0, microsecond=0)
        
        # Add the test job to trigger at 12:35 PM
        scheduler.add_job(
            send_weekly_reminder,
            'date',  # Use date trigger for a one-time execution
            run_date=test_time,
            args=[client],
            id='test_reminder_job',
            replace_existing=True
        )
        
        logger.info(f"Added test reminder scheduled for today at 12:35 PM Israel time: {test_time}")
        
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

        scheduler.start()
        logger.info("APScheduler started. Weekly reminder scheduled for Fridays at 5 PM.")
        # Log all scheduled jobs
        jobs = scheduler.get_jobs()
        logger.info(f"Scheduled jobs: {[job.id for job in jobs]}")

    except Exception as e:
        logger.error(f"Failed to setup or start scheduler: {e}", exc_info=True)
        # Depending on severity, might want to raise error or handle differently

    return scheduler