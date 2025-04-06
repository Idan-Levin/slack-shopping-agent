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
    product_info: Dict[str, Any] = {"url": url, "title": None, "price": None}
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
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                # headless=False,  # Set to False for debugging
                # args=['--disable-web-security', '--disable-features=site-per-process']
            )
            # Add a longer timeout for potentially slow pages
            context = await browser.new_context(
                user_agent=USER_AGENT,
                locale="en-US",
                viewport={"width": 1920, "height": 1080},
                bypass_csp=True
            )

            # Set location headers and cookies for New York
            await context.set_extra_http_headers(NY_HEADERS)
            page = await context.new_page()
            for cookie in NY_COOKIES:
                await page.add_cookie(cookie)

            # Prepare for handling potential bot detection or redirects
            await page.route("**/*", lambda route: route.continue_() if not route.request.url.startswith("data:") else route.abort())

            # Set a generous timeout
            page_timeout = 30000  # 30 seconds for page load, increase if needed for slower connections
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=page_timeout)
                # Wait for critical content to be visible
                title_wait_timeout = 10000  # 10 seconds for title element - adjust if needed
                try:
                    await page.wait_for_selector(TARGET_SELECTORS["title"], timeout=title_wait_timeout)
                except Exception as wait_error:
                    logger.warning(f"Timeout waiting for title selector on {url}: {wait_error}")
                    # Continue anyway - we'll check what we got

                # Check if we were redirected (e.g., product not available)
                final_url = page.url
                product_info['url'] = final_url

                # If we're redirected away from a product page, that's a sign the product isn't available
                if '/p/' not in final_url:
                    logger.warning(f"URL {url} redirected to non-product page: {final_url}")
                    return None  # Product not found or not available

                # Get the page content for parsing
                content = await page.content()
                # Now let's parse with BeautifulSoup for more reliable extraction
                soup = BeautifulSoup(content, 'html.parser')

                # Extract Title
                title_element = soup.select_one(TARGET_SELECTORS["title"])
                product_info["title"] = title_element.get_text(strip=True) if title_element else "Title not found"

                # Extract Price - handle different possible formats
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

                logger.info(f"Scraped data for {final_url}: Title='{product_info['title']}', Price={product_info['price']}")

                # Basic validation: return None if essential info is missing
                if product_info["title"] in [None, "Title not found"] and product_info["price"] is None:
                     logger.error(f"Failed to extract essential data (title, price) from {final_url}. Returning None.")
                     return None
                return product_info

            except PlaywrightError as pe:
                 logger.error(f"Playwright error navigating to {url}: {pe}")
                 return None

        # If we reach here, something unexpected happened
        logger.error(f"Unexpected flow in scraping {url} - reached end of try block without return")
        return None  # Explicit return None for clarity

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
async def search_products_gpt(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Search for products using GPT-4o with web search capabilities
    Returns a list of products with their details
    """
    from openai import AsyncOpenAI
    import os

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not found in environment")
        return []

    client = AsyncOpenAI(api_key=api_key)
    
    system_message = """You are a Target shopping assistant that helps users find products on target.com.
For each search query, use the web search tools to find relevant products from Target's website.
Always return information in the following JSON format:

[
  {
    "product_title": "Product name",
    "price": price as a number (e.g. 9.99),
    "url": "Valid URL to the product page on target.com",
    "in_stock": true or false
  },
  ...
]

Only include the fields shown above. Do not include additional fields.
Only include products from Target. Products must be actually available on target.com.
Return JSON array with at most 3 products. If no products are found, return an empty array [].

For the URL field, only use real, valid Target product URLs from your search results.
"""

    try:
        logger.info(f"Searching for products with query: '{query}'")
        
        response = await client.chat.completions.create(
            model="gpt-4o-search-preview",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": f"Find Target products for: {query}"}
            ],
            max_tokens=1500,
            temperature=0,
            response_format={"type": "json_object"},
            tools=[
                {
                    "type": "web_search",
                }
            ]
        )
        
        # Extract products
        message_content = response.choices[0].message.content
        if not message_content:
            logger.error("AI search returned empty content.")
            return []
        response_content = message_content.strip()
        logger.debug(f"GPT response: {response_content}")
        
        # Extract URL citations if available
        citations = []
        if hasattr(response.choices[0].message, 'annotations'):
            for annotation in response.choices[0].message.annotations:
                if annotation.type == 'url_citation':
                    citation_info = {
                        'text': annotation.text,
                        'url': annotation.url,
                        'title': annotation.title if hasattr(annotation, 'title') else None
                    }
                    citations.append(citation_info)
                    logger.debug(f"Found citation: {citation_info}")
        
        # Try to parse the JSON response
        try:
            # For newer response format
            data = json.loads(response_content)
            if 'products' in data:
                products = data['products']
            else:
                products = data
                
            # If products is not a list, try to handle other formats
            if not isinstance(products, list):
                if isinstance(products, dict) and any(key.startswith('product') for key in products.keys()):
                    # Handle case where it's a single product as dict
                    products = [products]
                else:
                    logger.warning(f"Unexpected product format: {products}")
                    products = []
            
            # Process and validate each product
            valid_products = []
            for product in products:
                if not isinstance(product, dict):
                    continue
                
                # Validate required fields
                if not all(k in product for k in ['product_title', 'url']):
                    continue
                
                # Validate URL format
                url = product.get('url', '')
                if not url.startswith('https://www.target.com/'):
                    # Try to find a matching URL in citations
                    product_title = product.get('product_title', '').lower()
                    for citation in citations:
                        if (citation['url'].startswith('https://www.target.com/') and 
                            (product_title in citation['title'].lower() if citation['title'] else False)):
                            url = citation['url']
                            product['url'] = url
                            logger.info(f"Replaced invalid URL with citation URL: {url}")
                            break
                    
                    # If still not valid, skip this product
                    if not product['url'].startswith('https://www.target.com/'):
                        logger.warning(f"Skipping product with invalid URL: {product}")
                        continue
                
                # Format price as float
                if 'price' in product:
                    try:
                        if isinstance(product['price'], str):
                            # Remove currency symbols and convert to float
                            price_str = product['price'].replace('$', '').replace(',', '')
                            product['price'] = float(price_str)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid price format: {product['price']}")
                        product['price'] = None
                else:
                    product['price'] = None
                
                # Ensure in_stock is boolean
                if 'in_stock' in product:
                    if isinstance(product['in_stock'], str):
                        product['in_stock'] = product['in_stock'].lower() == 'true'
                else:
                    product['in_stock'] = True  # Default to True if not specified
                
                valid_products.append(product)
            
            return valid_products[:max_results]
            
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {response_content}")
            return []
        
    except Exception as e:
        logger.error(f"Error in search_products_gpt: {str(e)}")
        return []