import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.web.async_client import AsyncWebClient
import uvicorn

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Set langchain logger level higher to avoid excessive debug messages if needed
logging.getLogger("langchain").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Initialize Bolt app
slack_app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN"), # Use .get() for safer access
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

# Initialize FastAPI app
api = FastAPI()

# Import and register listener functions AFTER initializing slack_app
# Ensure slack_handler is imported correctly
import slack_handler
from slack_handler import register_listeners, get_bot_user_id
register_listeners(slack_app)

# Import scheduler setup AFTER initializing slack_app and getting client
from scheduler import setup_scheduler
# Create Slack client instance using the token from environment variable
slack_client = AsyncWebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
scheduler = setup_scheduler(slack_client) # Pass the client instance

# Create request handler for FastAPI
app_handler = AsyncSlackRequestHandler(slack_app)

# --- FastAPI Endpoints ---
@api.post("/slack/events")
async def endpoint_events(req: Request):
    #logger.debug("Received request on /slack/events") # Can be noisy
    return await app_handler.handle(req)

@api.post("/slack/interactive")
async def endpoint_interactive(req: Request):
    # This endpoint might be needed if you add interactive elements back later
    logger.debug("Received request on /slack/interactive")
    return await app_handler.handle(req)

@api.post("/slack/commands")
async def endpoint_commands(req: Request):
    logger.debug("Received request on /slack/commands")
    return await app_handler.handle(req)

@api.get("/")
async def health_check():
    # Use the imported BOT_USER_ID from slack_handler
    return {"status": "ok", "scheduler_running": scheduler.running, "bot_id_fetched": slack_handler.BOT_USER_ID is not None}

# --- Application Startup/Shutdown ---
@api.on_event("startup")
async def startup_event():
    logger.info("Starting up FastAPI application...")
    # Initialize database
    from database import initialize_db, DATABASE_PATH
    # Ensure DATABASE_PATH uses environment variable for Render's persistent disk
    db_path = os.getenv("DATABASE_PATH", "shopping_list.db") # Default for local
    if not os.path.exists(db_path):
        logger.info(f"Database not found at {db_path}, initializing.")
        initialize_db()
    else:
        logger.info(f"Database file found at {db_path}.")
    # Proactively fetch Bot User ID on startup
    await get_bot_user_id(slack_client)


@api.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down FastAPI application...")
    if scheduler.running:
        scheduler.shutdown()
    logger.info("Scheduler shut down.")

# --- Run the app ---
if __name__ == "__main__":
    logger.info("Starting Uvicorn server...")
    port = int(os.getenv("PORT", 8000)) # Use PORT from environment for Render compatibility
    uvicorn.run("main:api", host="0.0.0.0", port=port, reload=True) # Use reload=True only for local dev