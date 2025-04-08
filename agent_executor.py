import os
import logging
from typing import Dict, Any
import json
import re

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
    model="gpt-4o-mini",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY") # Explicitly pass key
)

# Define the prompt template
# Note: Adjust instructions based on observed agent behavior
SYSTEM_PROMPT = """You are "ShopAgent", a helpful Slack assistant for managing a weekly company shopping list, primarily focused on Target.com.

Your capabilities:
1.  **Add Items via URL:** If a user provides a target.com product URL, use `get_product_details_from_url` to get its details. The tool will return the details as a string. Note that Target product URLs might redirect, so always use the final_url from the tool's response when available. THEN, present the details (Title, Price) and explicitly ASK the user how many they want BEFORE deciding to use `add_item_to_shopping_list`. Store the details temporarily.
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

# --- Function to Invoke Agent (Shopping List) ---
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


# --- New Function to Parse Mandate Rules ---
MANDATE_PARSE_SYSTEM_PROMPT = """You are an assistant that parses natural language mandate rules into a structured JSON object.
Rules might include:
- Spending limits (per transaction, per day, etc.)
- Allowed or blocked merchants (by name or category)
- Requirements for human approval under certain conditions
- Time-based restrictions
- Restrictions on item types (e.g., no alcohol)
- Settings for autonomous purchases (allowed up to a certain amount, etc.)

Analyze the user's input text and represent the extracted rules as a JSON object.
If specific values aren't mentioned (e.g., just "spending limit"), represent that appropriately (e.g., `"spending_limit": "unspecified"` or `null`).
If no rules can be extracted, return an empty JSON object `{}`.
Return ONLY the JSON object itself, with no other text before or after it.
Example Input: 'Max transaction $200. Block merchants: alcohol, tobacco. Allow autonomous purchases up to $50'
Example Output:
```json
{
  "max_transaction_amount": 200,
  "blocked_merchant_categories": ["alcohol", "tobacco"],
  "autonomous_purchase_limit": 50
}
```
"""

mandate_prompt = ChatPromptTemplate.from_messages([
    ("system", MANDATE_PARSE_SYSTEM_PROMPT),
    ("human", "{mandate_text}"),
])

# Combine prompt and LLM
mandate_parser_chain = mandate_prompt | LLM

async def parse_mandate_rules(mandate_text: str) -> str:
    """Uses an LLM chain to parse natural language mandate rules into a JSON string."""
    logger.info(f"Attempting to parse mandate rules: '{mandate_text}'")
    try:
        # Invoke the specialized chain
        response = await mandate_parser_chain.ainvoke({"mandate_text": mandate_text})
        
        # The response object has a 'content' attribute with the text
        json_string = response.content.strip()
        
        # Basic validation: Check if it looks like JSON
        if not (json_string.startswith('{') and json_string.endswith('}')) and \
           not (json_string.startswith('[') and json_string.endswith(']')):
            logger.warning(f"LLM did not return a valid JSON structure. Raw output: {json_string}")
            # Attempt to find JSON within potential markdown code fences
            match = re.search(r'```json\s*({.*?})\s*```', json_string, re.DOTALL)
            if match:
                json_string = match.group(1)
                logger.info("Extracted JSON from markdown code fence.")
            else:
                # Return an error structure if not JSON
                error_json = json.dumps({"error": "Failed to parse rules into JSON.", "raw_output": json_string})
                logger.error(f"Returning error JSON: {error_json}")
                return error_json

        # Further validation: Try parsing the JSON to ensure it's valid
        try:
            json.loads(json_string)
            logger.info(f"Successfully parsed mandate rules into JSON string: {json_string}")
            return json_string
        except json.JSONDecodeError as json_e:
            logger.error(f"LLM output looked like JSON but failed to parse: {json_e}. Raw output: {json_string}")
            error_json = json.dumps({"error": "Invalid JSON structure returned.", "raw_output": json_string})
            return error_json

    except Exception as e:
        logger.error(f"Error parsing mandate rules with LLM: {e}", exc_info=True)
        # Return a JSON string indicating an error
        return json.dumps({"error": "An unexpected error occurred during mandate parsing.", "details": str(e)})

# --- End Mandate Parsing Function ---
