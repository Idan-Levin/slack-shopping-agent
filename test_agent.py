"""
Simple test script to run the agent locally and interact with it through the console.
"""
import sys
import asyncio
import logging
import os
from dotenv import load_dotenv
import uuid
from langchain.memory import ConversationBufferWindowMemory
from agent_executor import invoke_agent
from database import initialize_db

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def main():
    # Check if OpenAI API key is set
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is not set.")
        sys.exit(1)
    
    # Initialize the database
    try:
        initialize_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        print(f"Error initializing database: {e}")
        sys.exit(1)
    
    # Generate a session ID
    session_id = str(uuid.uuid4())
    # Use a placeholder user ID and name for testing
    user_id = "test_user"
    user_name = "Test User"
    
    print("\n=== Shopping List Agent Test Console ===")
    print("Type your messages to interact with the agent.")
    print("Examples: 'find milk', 'add https://www.target.com/p/some-product-url'")
    print("Type 'exit', 'quit', or 'bye' to end the session.\n")
    
    while True:
        # Get user input
        user_input = input("You: ")
        
        # Check if user wants to exit
        if user_input.lower() in ['exit', 'quit', 'bye']:
            print("Exiting test console.")
            break
        
        try:
            # Invoke the agent with the user input
            response = await invoke_agent(
                user_input,
                session_id,
                user_id,
                user_name
            )
            print(f"\nAgent: {response}\n")
        except Exception as e:
            logger.error(f"Error invoking agent: {e}")
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 