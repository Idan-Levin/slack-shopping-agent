import asyncio
import logging
import os
import json # Import json
from typing import Dict, Optional, List, Any # Use Any for broader dict compatibility

# Use async playwright
from playwright.async_api import async_playwright, Error as PlaywrightError
from bs4 import BeautifulSoup
import openai

logger = logging.getLogger(__name__)

# Ensure API key is configured via environment variable
if not os.getenv("OPENAI_API_KEY"):
    logger.warning("OPENAI_API_KEY not found in environment. AI search will fail.")
# Initialize OpenAI client (consider using async client if available/needed)
# openai.api_key = os.getenv("OPENAI_API_KEY") # Older versions
# Newer versions (>=1.0) use client instantiation
# from openai import AsyncOpenAI # Use async client if making async calls
# client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Scraping ---
# WARNING: Target selectors are EXTREMELY volatile. This WILL break.
# Inspect Target's product page structure regularly or use more robust methods.
TARGET_SELECTORS = {
    "title": "h1[data-test='product-title']", # Example selector - VERY LIKELY OUTDATED
    "price": "[data-test='product-price']", # More generic price selector - LIKELY OUTDATED
    "image": "div[data-test='product-image'] img", # Example selector - LIKELY OUTDATED
    # Add more selectors if needed, e.g., for description or availability
    # "availability": "[data-test='storeAvailability']", # Example
}

# Use a realistic user agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"

async def scrape_target_url(url: str) -> Optional[Dict[str, Any]]:
    """Scrapes product details from a Target URL using Playwright."""
    logger.info(f"Attempting to scrape URL: {url}")
    product_info: Dict[str, Any] = {"url": url, "title": None, "price": None, "image_url": None}
    browser = None # Initialize browser variable

    try:
        async with async_playwright() as p:
            # Launch browser (consider persisting browser instance for multiple scrapes if needed)
            try:
                 browser = await p.chromium.launch(
                     # headless=False, # Uncomment for debugging locally to see browser
                     args=["--disable-blink-features=AutomationControlled"] # Try to appear less like an automated agent
                 )
            except PlaywrightError as launch_error:
                logger.error(f"Failed to launch playwright browser: {launch_error}")
                return None # Cannot proceed without browser

            page = await browser.new_page(user_agent=USER_AGENT)
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})") # Further attempt to hide automation

            logger.debug(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=25000) # Increased timeout

            # Wait for a key element (like price or title) to be present before proceeding
            # Adjust selector and timeout as needed
            try:
                await page.wait_for_selector(TARGET_SELECTORS["price"], timeout=10000)
                logger.debug("Price element found, proceeding with scraping.")
            except PlaywrightError:
                logger.warning(f"Timed out waiting for price element on {url}. Page might not have loaded correctly or structure changed.")
                # Attempt to get content anyway, might still work for title/image
                # Consider taking a screenshot here for debugging: await page.screenshot(path='debug_screenshot.png')

            html_content = await page.content()
            # logger.debug(f"Page content length for {url}: {len(html_content)}") # Debug page load

            # Close page and browser promptly
            await page.close()
            await browser.close()
            browser = None # Ensure browser is marked as closed

            # --- Parsing with BeautifulSoup ---
            soup = BeautifulSoup(html_content, 'html.parser')

            # Extract Title
            title_element = soup.select_one(TARGET_SELECTORS["title"])
            product_info["title"] = title_element.get_text(strip=True) if title_element else "Title not found"

            # Extract Price - Complex due to variations (sale, range, currency symbol)
            price_element = soup.select_one(TARGET_SELECTORS["price"])
            if price_element:
                price_text = price_element.get_text(strip=True).replace("$", "").split(" ")[0] # Basic cleaning
                # Try to handle price ranges (e.g., "5.99 - 9.99", take the first one)
                price_text = price_text.split('-')[0].strip()
                try:
                    product_info["price"] = float(price_text)
                except (ValueError, TypeError):
                    logger.warning(f"Could not parse price from text: '{price_text}' on {url}")
                    product_info["price"] = None # Set to None if parsing fails
            else:
                 logger.warning(f"Price element not found using selector '{TARGET_SELECTORS['price']}' on {url}")
                 product_info["price"] = None


            # Extract Image URL
            image_element = soup.select_one(TARGET_SELECTORS["image"])
            product_info["image_url"] = image_element.get('src') if image_element and image_element.get('src') else None


            logger.info(f"Scraped data for {url}: Title='{product_info['title']}', Price={product_info['price']}")

            # Basic validation: return None if essential info is missing
            if product_info["title"] in [None, "Title not found"] and product_info["price"] is None:
                 logger.error(f"Failed to extract essential data (title, price) from {url}. Returning None.")
                 return None
            return product_info

    except PlaywrightError as pe:
         logger.error(f"Playwright error scraping {url}: {pe}", exc_info=True)
         return None
    except Exception as e:
        logger.error(f"Unexpected error scraping {url}: {e}", exc_info=True)
        return None
    finally:
         if browser: # Ensure browser is closed if an error occurred mid-process
             logger.warning("Closing browser due to error during scraping.")
             await browser.close()


# --- AI Search ---
async def search_products_gpt(query: str) -> Optional[List[Dict[str, Any]]]:
    """Uses OpenAI GPT to search for products based on a query."""
    logger.info(f"Performing AI product search for query: '{query}'")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        logger.error("OpenAI API key not configured for search.")
        return None

    try:
        # Use the newer client method if openai version >= 1.0
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_api_key)

        response = await client.chat.completions.create(
            model="gpt-4-turbo", # Or "gpt-3.5-turbo" for faster/cheaper results
            messages=[
                {
                    "role": "system",
                    "content": """You are a product search assistant. Find product options based on the user's query.
                    Focus ONLY on products likely available at Target (target.com) in the US.
                    For each product (max 3), provide ONLY the following information in a JSON list format:
                    - name: The product name.
                    - price: The approximate price (as a number, e.g., 10.99, NOT a string like '$10.99'). Use null if unknown.
                    - url: A plausible URL to the product page on target.com. If unsure, provide the base Target URL or null.
                    - image_url: A direct URL to an image of the product. Use null if unavailable.
                    - in_stock: Boolean (true/false) or null, indicating likely availability based on search context.

                    Return ONLY the JSON list. Example format:
                    [
                      { "name": "Tide PODS Laundry Detergent Pacs - Spring Meadow (81 Count)", "price": 21.49, "url": "https://www.target.com/p/tide-pods...", "image_url": "https://...", "in_stock": true },
                      { "name": "Example Product B", "price": null, "url": "https://www.target.com/", "image_url": null, "in_stock": null }
                    ]
                    If you cannot find relevant items at Target, return an empty list [].
                    Do not include any explanatory text outside the JSON list itself.
                    """,
                },
                {"role": "user", "content": f"Find products at Target for: {query}"},
            ],
            temperature=0.1, # Low temperature for factual, structured output
            response_format={"type": "json_object"}, # Enforce JSON output if model supports
        )

        # content = response.choices[0].message.content.strip() # Updated attribute access
        message_content = response.choices[0].message.content
        if not message_content:
             logger.error("AI search returned empty content.")
             return None
        content = message_content.strip()

        logger.debug(f"Raw OpenAI response content for '{query}': {content}")

        # Attempt to parse the JSON directly (assuming response_format worked)
        try:
            # The response might be a JSON object containing the list, e.g., {"results": [...] }
            # Or it might be the list directly if the model follows instructions perfectly.
            data = json.loads(content)
            # Adjust based on actual model output structure. If it returns {"results": [...]}, use data = data['results']
            if isinstance(data, list):
                 products = data
            elif isinstance(data, dict) and isinstance(data.get("results"), list): # Example if nested
                 products = data["results"]
            elif isinstance(data, dict) and isinstance(data.get("products"), list): # Another possible nesting
                 products = data["products"]
            else:
                 logger.error(f"AI search returned JSON, but not in the expected list format: {content}")
                 return None


            if isinstance(products, list):
                logger.info(f"AI Search successful for '{query}', found {len(products)} potential products.")
                # Basic validation/cleaning of results
                validated_products = []
                for p in products:
                    if isinstance(p, dict) and 'name' in p and 'url' in p:
                         # Ensure price is float or None
                         if 'price' in p and p['price'] is not None and not isinstance(p['price'], (int, float)):
                             try:
                                 p['price'] = float(str(p['price']).replace('$',''))
                             except (ValueError, TypeError):
                                 p['price'] = None
                         # Basic URL check
                         if not isinstance(p.get('url'), str) or not p['url'].startswith("https://www.target.com"):
                             p['url'] = None # Nullify invalid URLs

                         validated_products.append(p)
                    else:
                        logger.warning(f"Skipping invalid product structure from AI for query '{query}': {p}")
                return validated_products
            else:
                # Should have been caught above, but belt-and-suspenders
                logger.error(f"AI search parsing failed, result was not a list. Raw content: {content}")
                return None
        except json.JSONDecodeError as json_e:
             logger.error(f"Failed to decode JSON from AI response for query '{query}': {json_e}. Raw response: {content}")
             # Sometimes models add ```json ... ``` markdown, try stripping it
             if content.startswith("```json"):
                 content = content[7:]
             if content.endswith("```"):
                 content = content[:-3]
             content = content.strip()
             try: # Retry parsing after stripping markdown
                 data = json.loads(content)
                 # Repeat list extraction logic... (refactor into a helper function if needed)
                 if isinstance(data, list): products = data
                 elif isinstance(data, dict) and isinstance(data.get("results"), list): products = data["results"]
                 elif isinstance(data, dict) and isinstance(data.get("products"), list): products = data["products"]
                 else: return None # Give up if structure still wrong
                 # Repeat validation logic...
                 # ...
                 return validated_products # Return validated list
             except json.JSONDecodeError:
                  logger.error(f"Still failed to decode JSON after stripping markdown for query '{query}'. Final attempt content: {content}")
                  return None # Give up

    except Exception as e:
        logger.error(f"Unexpected error during OpenAI search for '{query}': {e}", exc_info=True)
        return None