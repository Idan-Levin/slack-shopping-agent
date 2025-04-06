import asyncio
import logging
import os
import json # Import json
from typing import Dict, Optional, List, Any # Use Any for broader dict compatibility
import aiohttp # For URL validation

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

# --- URL Validation ---
async def validate_target_url(url: str) -> bool:
    """Check if a Target URL is valid and accessible."""
    if not url or not isinstance(url, str):
        return False
        
    if not url.startswith("https://www.target.com/p/"):
        return False
        
    try:
        timeout = aiohttp.ClientTimeout(total=10)  # 10 second timeout
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
            }
            async with session.head(url, headers=headers, allow_redirects=True) as response:
                if response.status == 200:
                    return True
                    
                logger.warning(f"URL validation failed for {url} with status code: {response.status}")
                return False
    except Exception as e:
        logger.warning(f"Error validating URL {url}: {e}")
        return False

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

    # New York location data
    NY_ZIP_CODE = "10001"  # Manhattan
    NY_HEADERS = {
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    NY_COOKIES = [
        {"name": "visitorZipCode", "value": NY_ZIP_CODE, "domain": ".target.com"},
        {"name": "visitorId", "value": "01876543210ABCDEF", "domain": ".target.com"},
        {"name": "GuestLocation", "value": f"{{\"zipCode\":\"{NY_ZIP_CODE}\"}}", "domain": ".target.com"}
    ]

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

            # Create a context with New York location
            context = await browser.new_context(
                user_agent=USER_AGENT,
                extra_http_headers=NY_HEADERS
            )
            
            # Add cookies for New York location
            await context.add_cookies(NY_COOKIES)
            
            # Create page using the context
            page = await context.new_page()
            
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})") # Further attempt to hide automation

            logger.debug(f"Navigating to {url}")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=25000) # Increased timeout
            
            # Capture the final URL after any redirects
            final_url = page.url
            if final_url != url:
                logger.info(f"URL redirected from {url} to {final_url}")
                product_info["url"] = final_url  # Update with the actual final URL
            
            # Wait for a key element (like price or title) to be present before proceeding
            # Adjust selector and timeout as needed
            try:
                await page.wait_for_selector(TARGET_SELECTORS["price"], timeout=10000)
                logger.debug("Price element found, proceeding with scraping.")
            except PlaywrightError:
                logger.warning(f"Timed out waiting for price element on {final_url}. Page might not have loaded correctly or structure changed.")
                # Attempt to get content anyway, might still work for title/image
                # Consider taking a screenshot here for debugging: await page.screenshot(path='debug_screenshot.png')

            html_content = await page.content()
            # logger.debug(f"Page content length for {url}: {len(html_content)}") # Debug page load

            # Close page and browser promptly
            await page.close()
            await context.close()
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
                    logger.warning(f"Could not parse price from text: '{price_text}' on {final_url}")
                    product_info["price"] = None # Set to None if parsing fails
            else:
                 logger.warning(f"Price element not found using selector '{TARGET_SELECTORS['price']}' on {final_url}")
                 product_info["price"] = None


            # Extract Image URL
            image_element = soup.select_one(TARGET_SELECTORS["image"])
            product_info["image_url"] = image_element.get('src') if image_element and image_element.get('src') else None


            logger.info(f"Scraped data for {final_url}: Title='{product_info['title']}', Price={product_info['price']}")

            # Basic validation: return None if essential info is missing
            if product_info["title"] in [None, "Title not found"] and product_info["price"] is None:
                 logger.error(f"Failed to extract essential data (title, price) from {final_url}. Returning None.")
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
    """Uses OpenAI GPT with web search to find accurate product information based on a query."""
    logger.info(f"Performing AI product search with web search for query: '{query}'")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        logger.error("OpenAI API key not configured for search.")
        return None

    try:
        # Use the newer client method if openai version >= 1.0
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_api_key)

        response = await client.chat.completions.create(
            model="gpt-4o-search-preview",  # Use the full model with better web search capabilities
            web_search_options={
                "search_context_size": "medium",  # Balance between quality and cost
                "user_location": {
                    "type": "approximate",
                    "approximate": {
                        "country": "US",
                        "city": "New York",
                        "region": "New York"
                    }
                }
            },
            messages=[
                {
                    "role": "system",
                    "content": """You are a product search assistant that finds accurate Target products.
                    Search the web to find REAL and CURRENT products available at Target (target.com).
                    For each product (max 3), provide ONLY the following information in a JSON list format:
                    - name: The product name exactly as shown on Target.com.
                    - price: The current price as a number (e.g., 10.99). Use null if unknown.
                    - url: The EXACT and VALID URL to the product page on target.com. 
                    - image_url: The direct URL to the product image. Use null if unavailable.
                    - in_stock: Boolean (true/false) or null, indicating if the product is in stock.

                    CRITICAL: Use web search to find REAL target.com URLs. Do not generate URLs.
                    Return ONLY the JSON list. Example format:
                    [
                      { "name": "Tide PODS Laundry Detergent Pacs - Spring Meadow (81 Count)", "price": 21.49, "url": "https://www.target.com/p/tide-pods-laundry-detergent-pacs-spring-meadow-81ct/-/A-50570157", "image_url": "https://target.scene7.com/is/image/Target/GUEST_44e47f56-ea5b-4b60-ae9a-bad46af9dcff", "in_stock": true },
                      { "name": "Example Product B", "price": null, "url": "https://www.target.com/p/actual-product-url", "image_url": null, "in_stock": null }
                    ]
                    If you cannot find relevant items at Target, return an empty list [].
                    """,
                },
                {"role": "user", "content": f"Find current products at Target for: {query}. Make sure to use the web search to find REAL products with VALID URLs."},
            ]
        )

        message_content = response.choices[0].message.content
        if not message_content:
            logger.error("AI search returned empty content.")
            return None
        content = message_content.strip()

        logger.debug(f"Raw OpenAI response content for '{query}': {content}")
        
        # Extract citation information if available
        citations = []
        if hasattr(response.choices[0].message, 'annotations'):
            annotations = response.choices[0].message.annotations
            if annotations:
                for annotation in annotations:
                    if annotation.type == 'url_citation':
                        citations.append({
                            'url': annotation.url_citation.url,
                            'title': annotation.url_citation.title
                        })
                logger.info(f"Search returned {len(citations)} citations")

        # The response might not be perfectly formatted JSON, so we need to extract it
        # Look for JSON list pattern in the content
        json_pattern = r'\[\s*\{.*?\}\s*\]'
        import re
        json_match = re.search(json_pattern, content, re.DOTALL)
        
        if json_match:
            try:
                json_str = json_match.group(0)
                data = json.loads(json_str)
                logger.info(f"Successfully extracted JSON data from response")
                products = data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                logger.warning(f"Found JSON-like content but failed to parse: {json_str}")
                # Fall back to full content parsing
                data = None
        else:
            logger.warning("No JSON list pattern found in response, attempting to parse full content")
            data = None
            
        # If JSON extraction failed, try parsing the full content
        if data is None:
            try:
                # Attempt to parse the JSON directly
                data = json.loads(content)
                
                products = []
                
                # Case 1: Response is a list of products
                if isinstance(data, list):
                    products = data
                # Case 2: Response is a dict with a list under "results" or "products" key
                elif isinstance(data, dict) and isinstance(data.get("results"), list):
                    products = data["results"]
                elif isinstance(data, dict) and isinstance(data.get("products"), list):
                    products = data["products"]
                # Case 3: Response is a single product object (not in a list)
                elif isinstance(data, dict) and 'name' in data and 'url' in data:
                    products = [data]
                    logger.info(f"AI search returned a single product object, converting to list: {data}")
                else:
                    logger.error(f"AI search returned JSON, but not in the expected format: {content}")
                    # Last resort - try to extract JSON from text with regex
                    products = []
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
                    # Repeat list extraction logic
                    if isinstance(data, list): 
                        products = data
                    elif isinstance(data, dict) and isinstance(data.get("results"), list): 
                        products = data["results"]
                    elif isinstance(data, dict) and isinstance(data.get("products"), list): 
                        products = data["products"]
                    else:
                        # Try to find any array in the content
                        array_match = re.search(r'\[(.*)\]', content, re.DOTALL)
                        if array_match:
                            try:
                                products = json.loads(array_match.group(0))
                                if not isinstance(products, list):
                                    products = []
                            except:
                                products = []
                        else:
                            products = []
                except:
                    logger.error(f"Failed all attempts to parse response as JSON for query '{query}'")
                    products = []

        if len(products) > 0:
            logger.info(f"AI Search successful for '{query}', found {len(products)} potential products.")
            # Basic validation/cleaning of results
            validated_products = []
            
            # Validate all URLs concurrently
            validation_tasks = []
            for i, p in enumerate(products):
                if isinstance(p, dict) and 'name' in p:
                    # Ensure price is float or None
                    if 'price' in p and p['price'] is not None and not isinstance(p['price'], (int, float)):
                        try:
                            p['price'] = float(str(p['price']).replace('$',''))
                        except (ValueError, TypeError):
                            p['price'] = None
                    
                    # URLs are expected to be reliable but still need validation
                    if 'url' in p and isinstance(p['url'], str):
                        # Fix common URL issues
                        if p['url'].startswith("www.target.com"):
                            p['url'] = "https://" + p['url']
                        elif p['url'].startswith("target.com"):
                            p['url'] = "https://www." + p['url']
                            
                        # Only validate if it has the right URL format
                        if p['url'].startswith("https://www.target.com"):
                            validation_tasks.append((i, validate_target_url(p['url'])))
                        else:
                            logger.warning(f"URL format doesn't match Target product URL: {p.get('url')}")
                            p['url'] = None
                            validated_products.append(p)
                    else:
                        p['url'] = None
                        validated_products.append(p)
                else:
                    logger.warning(f"Skipping invalid product structure from AI for query '{query}': {p}")
            
            # Wait for all validation tasks to complete
            if validation_tasks:
                validation_results = await asyncio.gather(*[task for _, task in validation_tasks])
                
                # Add products with valid URLs to the result
                valid_url_count = 0
                for i, (product_idx, _) in enumerate(validation_tasks):
                    is_valid = validation_results[i]
                    product = products[product_idx]
                    
                    if is_valid:
                        valid_url_count += 1
                        validated_products.append(product)
                    else:
                        # Keep the product but mark the URL as invalid
                        logger.warning(f"Invalid Target URL found and marked as None: {product.get('url')}")
                        product['url'] = None
                        validated_products.append(product)
                
                logger.info(f"URL validation complete: {valid_url_count} valid URLs out of {len(validation_tasks)} tested")
            
            # If no products have valid URLs but we have validated products, return them anyway
            if validated_products:
                logger.info(f"Returning {len(validated_products)} products, some may have invalid URLs")
                return validated_products
            # Fall back to products without URL validation as a last resort
            elif products:
                logger.warning(f"No products with valid URLs found, returning unvalidated products as fallback")
                return products
            else:
                logger.warning(f"No valid products found for query '{query}'")
                return []
        else:
            # No products found
            logger.warning(f"AI search found no products for query '{query}'")
            return []

    except Exception as e:
        logger.error(f"Unexpected error during OpenAI search for '{query}': {e}", exc_info=True)
        return None