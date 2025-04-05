import logging
import json # Import json
import asyncio # Import asyncio
import re # Import regular expressions module
from typing import Type, Optional, List, Dict, Any
from pydantic.v1 import BaseModel, Field # Use v1 pydantic for Langchain tool compatibility

from langchain.tools import BaseTool
# Assuming previous functions exist in these modules
from database import (
    add_item,
    get_active_items,
    delete_item,
    find_items_by_description,
    get_item_by_id,
)
from product_service import scrape_target_url, search_products_gpt
from utils import format_price

logger = logging.getLogger(__name__)

# --- Pydantic Schemas for Tool Inputs ---

class GetProductDetailsInput(BaseModel):
    url: str = Field(description="The valid https URL for a product page on target.com")

class AddItemInput(BaseModel):
    user_id: str = Field(description="The Slack User ID of the person requesting the item")
    user_name: str = Field(description="The display name of the Slack user")
    product_title: str = Field(description="The name/title of the product")
    quantity: int = Field(description="The number of units of the product to add")
    price: Optional[float] = Field(description="The price per unit of the product, if known. Should be a number, not string.")
    url: Optional[str] = Field(description="The URL of the product page, if known")
    image_url: Optional[str] = Field(description="The URL of the product image, if known")
    final_url: Optional[str] = Field(description="The final URL after redirects, if different from the original URL", default=None)

class SearchProductsInput(BaseModel):
    query: str = Field(description="The natural language search query for the product (e.g., 'laundry detergent', 'oreo cookies')")

class ViewListInput(BaseModel):
    # No input needed, but defining for consistency
    pass

class DeleteItemInput(BaseModel):
    user_id: str = Field(description="The Slack User ID of the person requesting the deletion")
    item_description: str = Field(description="A description sufficient to identify the item to be deleted from the user's list (e.g., 'the oreo cookies', 'crest toothpaste', 'item id 5'). Be specific if multiple similar items exist.")


# --- Tool Definitions ---

class GetProductDetailsTool(BaseTool):
    name: str = "get_product_details_from_url"
    description: str = "Use this tool ONLY when you are given a specific target.com product URL. It extracts the product's title, price, and image URL from the webpage."
    args_schema: Type[BaseModel] = GetProductDetailsInput

    def _run(self, url: str) -> str:
        logger.info(f"Tool {self.name} called with URL: {url}")
        # LangChain Tools run synchronously by default. Need to run async playwright code.
        try:
            # Run the async function in the current event loop if available, or create a new one
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            product_data = loop.run_until_complete(scrape_target_url(url))

            if product_data and product_data.get("title") not in [None, "Title not found"]:
                 # Ensure price is float or None before returning
                 price_val = product_data.get('price')
                 if isinstance(price_val, str):
                     try:
                         price_val = float(price_val.replace('$',''))
                     except (ValueError, TypeError):
                         price_val = None

                 # Return structured data as a JSON string for the agent to parse easily
                 result_dict = {
                     "title": product_data.get('title', 'N/A'),
                     "price": price_val,
                     "image_url": product_data.get('image_url'),
                     "original_url": url,
                     "final_url": product_data.get('url', url)  # Use the final URL after redirects
                 }
                 # Return as string for the LLM. JSON format might be easier for it.
                 # return f"Successfully scraped: Title='{result_dict['title']}', Price={result_dict['price']}, ImageURL='{result_dict['image_url']}', OriginalURL='{url}'"
                 return json.dumps(result_dict)

            else:
                # If we couldn't extract product details directly, try searching for the product name from the URL
                logger.warning(f"Could not extract valid details from URL: {url}. Attempting to search based on URL keywords.")
                
                # Extract potential product name from URL
                product_keywords = url.split('/')[-2] if '/' in url else url
                product_keywords = product_keywords.replace('-', ' ').replace('amp;', '').replace('.html', '')
                
                # Only try searching if we have meaningful keywords
                if len(product_keywords) > 5:  # Arbitrary minimum length to avoid too generic searches
                    try:
                        # Search for the product using extracted keywords
                        search_results = loop.run_until_complete(search_products_gpt(product_keywords))
                        
                        if search_results and len(search_results) > 0:
                            logger.info(f"Found alternative product through search: {search_results[0].get('name')}")
                            # Return the first search result
                            return json.dumps({
                                "title": search_results[0].get('name'),
                                "price": search_results[0].get('price'),
                                "image_url": search_results[0].get('image_url'),
                                "original_url": url,
                                "final_url": search_results[0].get('url'),
                                "note": "Original URL failed, found similar product through search"
                            })
                    except Exception as search_e:
                        logger.error(f"Error in fallback search for URL {url}: {search_e}")
                
                return "Error: Could not extract valid product details from the URL. It might be invalid, out of stock, or the page structure changed."
        except Exception as e:
            logger.error(f"Error in {self.name} tool scraping {url}: {e}", exc_info=True)
            return f"Error: An exception occurred while trying to scrape the URL: {e}"

    # If using async agents fully:
    # async def _arun(self, url: str) -> str:
    #     logger.info(f"Tool {self.name} async called with URL: {url}")
    #     try:
    #         product_data = await scrape_target_url(url)
    #         # ... (rest of the logic same as _run but using await) ...
    #         if product_data and product_data.get("title") != "Title not found":
    #              result_dict = { ... }
    #              return json.dumps(result_dict)
    #         else: ...
    #     except Exception as e: ...


class SearchProductsTool(BaseTool):
    name: str = "search_target_products"
    description: str = "Use this tool to find products available at Target based on a user's search query (e.g., 'cheap detergent', 'milk', 'birthday card'). Returns a list of products found."
    args_schema: Type[BaseModel] = SearchProductsInput

    def _run(self, query: str) -> str:
        logger.info(f"Tool {self.name} called with query: {query}")
        try:
            # Run async search function
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            results = loop.run_until_complete(search_products_gpt(query))

            if results:
                # Return the raw results as a JSON string list for the LLM agent
                # The LLM is responsible for presenting this nicely
                return json.dumps(results)
            else:
                return f"Sorry, I couldn't find any products matching '{query}' at Target right now."
        except Exception as e:
            logger.error(f"Error in {self.name} tool searching for '{query}': {e}", exc_info=True)
            return f"Error: An exception occurred during the product search: {e}"

    # async def _arun(self, query: str) -> str:
    #     logger.info(f"Tool {self.name} async called with query: {query}")
    #     try:
    #         results = await search_products_gpt(query)
    #         if results: return json.dumps(results)
    #         else: ...
    #     except Exception as e: ...

class AddItemTool(BaseTool):
    name: str = "add_item_to_shopping_list"
    description: str = "Use this tool to add a specific product with its quantity to the user's weekly shopping list. Only use AFTER confirming the product details AND quantity with the user."
    args_schema: Type[BaseModel] = AddItemInput

    def _run(self, user_id: str, user_name: str, product_title: str, quantity: int, price: Optional[float] = None, url: Optional[str] = None, image_url: Optional[str] = None, final_url: Optional[str] = None) -> str:
        logger.info(f"Tool {self.name} called for user {user_id} ({user_name}) to add '{product_title}', Qty: {quantity}")
        if not isinstance(quantity, int) or quantity <= 0:
             return f"Error: Invalid quantity '{quantity}'. Quantity must be a positive whole number."
        # Basic check for potentially problematic price types passed by LLM
        if price is not None and not isinstance(price, (int, float)):
             logger.warning(f"Received non-numeric price '{price}' type: {type(price)}. Attempting conversion.")
             try:
                 price = float(price)
             except (ValueError, TypeError):
                  logger.error(f"Failed to convert price '{price}' to float.")
                  price = None # Set price to None if conversion fails

        # Use the final URL (after redirect) if available, otherwise use the original URL
        product_url = final_url if final_url else url

        try:
            item_id = add_item(
                user_id=user_id,
                user_name=user_name,
                title=product_title,
                quantity=quantity,
                price=price,
                url=product_url,
                image_url=image_url
            )
            return f"Success! Added {quantity} x '{product_title}' to the shopping list for {user_name} (Item ID: {item_id})."
        except Exception as e:
            logger.error(f"Error in {self.name} tool adding item for {user_id}: {e}", exc_info=True)
            return f"Error: Could not add '{product_title}' to the list due to an internal error: {e}"


class ViewListTool(BaseTool):
    name: str = "view_shopping_list"
    description: str = "Use this tool to view all items currently on the active shopping list."
    args_schema: Type[BaseModel] = ViewListInput # No args, but schema required

    def _run(self, **kwargs) -> str: # Accept dummy kwargs if agent sends any
        logger.info(f"Tool {self.name} called.")
        try:
            items = get_active_items()
            if not items:
                return "The shopping list is currently empty."

            # Format the list clearly for the LLM to relay
            response_lines = ["*🛒 Current Shopping List:*"]
            for i, item in enumerate(items):
                 response_lines.append(
                     f"{i+1}. *{item['product_title']}* (ID: {item['id']})\n"
                     f"   Qty: {item['quantity']} | Price: {format_price(item.get('price'))} | Added by: {item['user_name']}" +
                     (f" | <{item['product_url']}|Link>" if item.get('product_url') else "")
                 )
            return "\n---\n".join(response_lines) # Use divider for readability
        except Exception as e:
            logger.error(f"Error in {self.name} tool: {e}", exc_info=True)
            return f"Error: Could not retrieve the shopping list: {e}"

class DeleteItemTool(BaseTool):
    name: str = "delete_shopping_list_item"
    description: str = "Use this tool to delete an item from the shopping list based on user description or item ID. You MUST provide the user_id and a description/ID."
    args_schema: Type[BaseModel] = DeleteItemInput

    def _run(self, user_id: str, item_description: str) -> str:
        logger.info(f"Tool {self.name} called by user {user_id} to delete item matching '{item_description}'.")
        item_to_delete_id = None
        product_title = item_description # For error messages

        # Try to interpret item_description as an ID first
        try:
            # Look for "id 5", "item 12", etc.
            match = re.search(r'\b(id|item)\s*(\d+)\b', item_description, re.IGNORECASE)
            if match:
                item_to_delete_id = int(match.group(2))
                logger.info(f"Interpreted '{item_description}' as Item ID: {item_to_delete_id}")
                # Verify this item exists and belongs to the user
                item_data = get_item_by_id(item_to_delete_id)
                if not item_data:
                     return f"Error: Item with ID {item_to_delete_id} not found."
                if item_data['user_id'] != user_id:
                     # This check is also in delete_item, but good to have early
                     return f"Error: You cannot delete Item ID {item_to_delete_id} because you did not add it (added by {item_data['user_name']})."
                product_title = item_data['product_title'] # Use actual title for confirmation
            # else: # If no ID pattern, treat as description below
        except ValueError:
             # If conversion to int fails somehow
             logger.warning(f"Could not parse ID from '{item_description}'. Treating as description.")
             item_to_delete_id = None
        except Exception as e:
             logger.error(f"Error interpreting delete description '{item_description}': {e}")
             return f"Error: Could not interpret '{item_description}' for deletion."

        # If ID wasn't found or parsed, search by description for that user
        if item_to_delete_id is None:
            try:
                matching_items = find_items_by_description(user_id, item_description)

                if not matching_items:
                    return f"Sorry, I couldn't find any active items added by you that match '{item_description}'. Use the 'view_shopping_list' tool to see your items and their IDs."
                elif len(matching_items) > 1:
                    # List the items found so the user can be more specific
                    item_list = "\n".join([f"- '{item['product_title']}' (ID: {item['id']})" for item in matching_items])
                    return f"Found multiple items matching '{item_description}' added by you:\n{item_list}\nPlease try deleting again using the specific Item ID."
                else:
                    # Exactly one match found by description
                    item_to_delete_id = matching_items[0]['id']
                    product_title = matching_items[0]['product_title']
                    logger.info(f"Found unique item by description: ID {item_to_delete_id} ('{product_title}')")

            except Exception as e:
                logger.error(f"Error searching for item to delete '{item_description}': {e}", exc_info=True)
                return f"Error: An internal error occurred while searching for the item to delete: {e}"

        # If we have a unique ID to delete (either found by ID or description)
        if item_to_delete_id is not None:
            try:
                # delete_item performs the final permission check using user_id
                deleted = delete_item(item_to_delete_id, user_id_requesting=user_id)
                if deleted:
                    return f"Success! Deleted '{product_title}' (ID: {item_to_delete_id}) from the shopping list."
                else:
                    # This might happen if the item was deleted between find and delete, or other DB error
                    return f"Error: Could not delete '{product_title}' (ID: {item_to_delete_id}). It might have already been removed."
            except PermissionError as pe:
                 logger.warning(f"Permission denied in {self.name} trying to delete {item_to_delete_id}: {pe}")
                 return f"Error: You do not have permission to delete Item ID {item_to_delete_id} because you did not add it."
            except Exception as e:
                logger.error(f"Error in {self.name} tool deleting ID {item_to_delete_id}: {e}", exc_info=True)
                return f"Error: An internal error occurred while trying to delete item ID {item_to_delete_id}: {e}"
        else:
             # Should not happen if logic above is correct, but as a fallback
             return f"Error: Could not identify a unique item to delete based on '{item_description}'."


# List of tools for the agent executor
tools = [
    GetProductDetailsTool(),
    SearchProductsTool(),
    AddItemTool(),
    ViewListTool(),
    DeleteItemTool(),
]