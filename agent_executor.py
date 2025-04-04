import os
import logging
from typing import Dict, Any

from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.runnables.history import RunnableWithMessageHistory
# from langchain.callbacks import StdOutCallbackHandler # Uncomment for debugging

# Assuming agent_tools.py is in the same directory
from agent_tools import tools

logger = logging.getLogger(__name__)

# --- Agent Configuration ---
# Ensure OPENAI_API_KEY is loaded from environment
if not os.getenv("OPENAI_API_KEY"):
    logger.error("OPENAI_API_KEY environment variable not set!")
    # Handle this case appropriately - maybe raise an error or use a dummy key for tests
    # raise ValueError("OPENAI_API_KEY environment variable not set!")

LLM = ChatOpenAI(
    model="gpt-4-turbo",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY") # Explicitly pass key
)

# Define the prompt template
# Note: Adjust instructions based on observed agent behavior
SYSTEM_PROMPT = """You are "ShopAgent", a helpful Slack assistant for managing a weekly company shopping list, primarily focused on Target.com.

Your capabilities:
1.  **Add Items via URL:** If a user provides a target.com product URL, use `get_product_details_from_url` to get its details. The tool will return the details as a string. THEN, present the details (Title, Price) and explicitly ASK the user how many they want BEFORE deciding to use `add_item_to_shopping_list`. Store the details temporarily.
2.  **Search for Items:** If a user asks you to find an item (e.g., "find toothpaste", "look for cheap snacks"), use `search_target_products`. Present the findings (Name, Price, Link) to the user. If they choose one (e.g., by name or saying "add the first one"), extract its details (title, price, url) from the search results, then ASK for quantity BEFORE deciding to use `add_item_to_shopping_list`. Store the details temporarily.
3.  **Add Items After Confirmation:** Use `add_item_to_shopping_list` ONLY AFTER you have the product details (title, price, url) AND the user has confirmed the quantity in a recent message. You MUST have the user_id and user_name. Make sure you extract the correct product details from previous context or tool results before adding.
4.  **View List:** If the user asks to see the list ("what's on the list?", "show list", "view items"), use `view_shopping_list`.
5.  **Delete Items:** If a user asks to delete an item ("delete the cookies", "remove item id 5"), use `delete_shopping_list_item`. You need the user's user_id and a description or ID of the item. If the tool finds multiple matches or no matches for that user, relay that information back clearly. Do not try to guess. Ask for clarification if needed, possibly referencing the item ID shown by `view_shopping_list`. Ensure you only delete items added by the requesting user.
6.  **Quantity Handling:** When you need a quantity (after finding/scraping), your response MUST clearly ask "How many do you need?" or similar. The user's next message will likely contain the quantity (e.g., "3", "just one"). Extract this number. Then, retrieve the product details you stored from the previous step (using conversation history) and call `add_item_to_shopping_list` with the details and the extracted quantity.
7.  **User Identification:** You will be given the user's Slack ID (`user_id`) and display name (`user_name`) in the input. Pass these correctly to tools like `add_item_to_shopping_list` and `delete_shopping_list_item`.
8.  **Conversation Context:** Pay close attention to the chat history (`chat_history`) to understand the context, especially when confirming quantity, remembering product details, or clarifying which item to add/delete.
9.  **Clarity & Errors:** Be clear and concise. Ask for clarification if the user's request is ambiguous. If a tool returns an error message (often starting with "Error:"), explain the problem simply to the user without exposing internal details. Do not apologize excessively.
10. **Focus:** Primarily deal with shopping list tasks related to Target. For unrelated questions, politely state you can only help with the shopping list for Target. Do not invent products or URLs.
"""

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        # Input includes user info + message
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"), # For agent intermediate steps
    ]
)

# Create the agent using the OpenAI Functions structure
agent = create_openai_functions_agent(LLM, tools, prompt)

# --- Memory Management ---
# Using a simple in-memory dictionary. Production might need Redis/DB store.
# Keyed by session_id (e.g., "slack_channel_threadts")
conversation_memory_store: Dict[str, ConversationBufferWindowMemory] = {}

def get_session_history(session_id: str) -> ConversationBufferWindowMemory:
    """Retrieves or creates memory for a given session ID."""
    if session_id not in conversation_memory_store:
        logger.info(f"Creating new memory buffer for session: {session_id}")
        conversation_memory_store[session_id] = ConversationBufferWindowMemory(
             memory_key="chat_history",
             return_messages=True, # Important for prompt structure
             k=10 # Remember last 10 message exchanges
         )
    # else: logger.debug(f"Reusing memory buffer for session: {session_id}") # Can be noisy
    return conversation_memory_store[session_id]


# Create the Agent Executor with message history handling
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)

logger.info("LangChain agent executor created with tools and memory.")

# --- Function to Invoke Agent ---
async def invoke_agent(user_input: str, session_id: str, user_id: str, user_name: str) -> str:
    """Invokes the LangChain agent with user input and session context."""
    logger.info(f"Invoking agent for session {session_id}, user {user_id} ({user_name}), input: '{user_input}'")

    # Augment input with user info for the agent/tools
    augmented_input = f"[UserInfo: id='{user_id}', name='{user_name}']\n{user_input}"

    try:
        # Get the memory for this session
        memory = get_session_history(session_id)
        
        # Get the chat history
        chat_history = memory.load_memory_variables({})["chat_history"]
        
        # Run the agent
        response = await agent_executor.ainvoke({
            "input": augmented_input,
            "chat_history": chat_history,
            "agent_scratchpad": []
        })
        
        # Save the interaction to memory
        memory.save_context(
            {"input": augmented_input}, 
            {"output": response["output"]}
        )

        # Extract the final output string
        agent_output = response.get("output", "Sorry, I encountered an issue and couldn't process that.")
        logger.info(f"Agent response for session {session_id}: '{agent_output}'")
        return agent_output

    except Exception as e:
        logger.error(f"Error invoking agent for session {session_id}: {e}", exc_info=True)
        # Provide a user-friendly error message
        return "Sorry, an internal error occurred while processing your request. Please try again later."
