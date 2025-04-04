"""
Simple test script to run the agent locally and interact with it through the console.
"""
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from agent_executor import invoke_agent
import uuid
from database import init_db

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("agent_test")

async def main():
    # Check environment variables
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set in environment variables.")
        print("Error: OPENAI_API_KEY not set. Please add it to your .env file or environment.")
        return

    # Initialize the database
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        print(f"Error: Failed to initialize database. {str(e)}")
        return
        
    # Create a test session ID
    session_id = f"local_test_{uuid.uuid4()}"
    # Test user information
    user_id = "U12345678"
    user_name = "Test User"

    print("\n=== Shopping Agent Local Test ===")
    print("Type your message to interact with the agent. Type 'exit' to quit.")
    print("Example: 'find milk', 'add https://www.target.com/p/some-product', 'what's on the list?'\n")

    # Main interaction loop
    while True:
        # Get user input
        user_input = input("You: ")
        
        # Exit condition
        if user_input.lower() in ["exit", "quit", "bye"]:
            print("Goodbye!")
            break
        
        # Skip empty input
        if not user_input.strip():
            continue
            
        print("\nProcessing...")
        
        try:
            # Invoke the agent
            response = await invoke_agent(
                user_input=user_input,
                session_id=session_id,
                user_id=user_id,
                user_name=user_name
            )
            
            # Print the response
            print(f"\nAgent: {response}\n")
            
        except Exception as e:
            logger.error(f"Error during agent invocation: {e}", exc_info=True)
            print(f"\nError: Failed to get response from agent. See logs for details.\n")

if __name__ == "__main__":
    try:
        # Run the main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest terminated by user.")
        sys.exit(0) 