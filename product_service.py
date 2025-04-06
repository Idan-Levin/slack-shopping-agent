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
async def validate_target_url(url: str, skip_http_check: bool = False) -> bool:
    """
    Validate that a URL is a valid Target product URL.
    Now with option to skip HTTP validation and only check format.
    """
    import re
    import aiohttp
    
    # Basic format validation
    if not url or not isinstance(url, str):
        return False
    
    # Check if it's a Target product URL with the expected format
    target_product_pattern = r'^https://www\.target\.com/p/[^/]+/(?:-/[A-Z0-9-]+)?$'
    if not re.match(target_product_pattern, url):
        logger.debug(f"URL failed format validation: {url}")
        return False
    
    # If we're skipping HTTP checks, return True after format validation
    if skip_http_check:
        logger.info(f"URL format validation passed (HTTP check skipped): {url}")
        return True
    
    # Otherwise, make an HTTP request to validate
    try:
        timeout = aiohttp.ClientTimeout(total=5)  # 5 second timeout
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                # Use GET instead of HEAD as Target might block HEAD requests
                async with session.get(url, allow_redirects=True) as response:
                    if response.status == 200:
                        logger.info(f"URL validated successfully: {url}")
                        return True
                    elif response.status == 403:
                        # Target might return 403 for bot protection, but URL could still be valid
                        logger.warning(f"URL returned 403 Forbidden (may still be valid): {url}")
                        return True  # Consider 403 as valid to be less strict
                    else:
                        logger.warning(f"URL validation failed with status {response.status}: {url}")
                        return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Connection errors might be temporary, so we'll consider the URL potentially valid
                logger.warning(f"Connection error during URL validation (considering valid): {url} - {str(e)}")
                return True  # Be lenient on connection errors
    except Exception as e:
        logger.error(f"Error validating URL: {url} - {str(e)}")
        # On unexpected errors, be lenient and consider valid
        return True

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
    import re
    from difflib import SequenceMatcher

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not found in environment")
        return []

    # Since we're having issues with URL validation, let's be more permissive
    # Set this to True to skip HTTP validation and only check URL format
    SKIP_URL_HTTP_VALIDATION = True

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
        
        # Using Chat Completions API with the search model
        query_string = f"Find Target products for: {query} site:target.com"
        
        # First try with the search-preview model
        try:
            response = await client.chat.completions.create(
                model="gpt-4o-search-preview",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": query_string}
                ]
            )
        except Exception as e:
            logger.warning(f"Error with search model, falling back to regular GPT-4o: {e}")
            # If the search model fails, fall back to standard model
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": query_string}
                ]
            )
        
        # Extract products
        message_content = response.choices[0].message.content
        if not message_content:
            logger.error("AI search returned empty content.")
            return []
        response_content = message_content.strip()
        logger.debug(f"GPT response: {response_content}")
        
        # --- FIRST EXTRACT ALL CITATIONS ---
        # These are more reliable as they come directly from search results
        target_citations = []
        if hasattr(response.choices[0].message, 'annotations'):
            for annotation in response.choices[0].message.annotations:
                if annotation.type == 'url_citation':
                    url = annotation.url
                    title = annotation.title if hasattr(annotation, 'title') else None
                    
                    # Only keep Target product URLs
                    if url and url.startswith('https://www.target.com/p/'):
                        citation_info = {
                            'url': url,
                            'title': title,
                            'text': annotation.text if hasattr(annotation, 'text') else '',
                            'used': False  # Track if this citation has been matched to a product
                        }
                        target_citations.append(citation_info)
                        logger.info(f"Found Target citation: {title} - {url}")
            
            logger.info(f"Found {len(target_citations)} valid Target product citations")
        
        # Check if response is in markdown code block and extract JSON
        if response_content.startswith("```") and "```" in response_content[3:]:
            # Extract content between markdown code blocks
            pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
            matches = re.search(pattern, response_content)
            if matches:
                response_content = matches.group(1).strip()
                logger.info(f"Extracted JSON from markdown code block: {response_content[:100]}...")
        
        # Function to find best matching citation for a product title
        def find_best_citation_match(product_title):
            if not target_citations:
                return None
                
            best_score = 0
            best_match = None
            
            product_title_lower = product_title.lower()
            
            # First try direct substring match
            for citation in target_citations:
                if citation['used']:
                    continue
                    
                citation_title = citation.get('title', '')
                if citation_title and (product_title_lower in citation_title.lower() or 
                                      citation_title.lower() in product_title_lower):
                    citation['used'] = True
                    return citation
            
            # If no direct substring match, use sequence matcher
            for citation in target_citations:
                if citation['used']:
                    continue
                    
                citation_title = citation.get('title', '')
                if not citation_title:
                    continue
                    
                score = SequenceMatcher(None, product_title_lower, citation_title.lower()).ratio()
                if score > best_score and score > 0.6:  # 60% similarity threshold
                    best_score = score
                    best_match = citation
            
            if best_match:
                best_match['used'] = True
                logger.info(f"Matched product '{product_title}' with citation '{best_match['title']}' (similarity: {best_score:.2f})")
            
            return best_match
        
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
                
                # Allow both product_title and name fields
                if 'product_title' in product:
                    product_title = product['product_title']
                elif 'name' in product:
                    product_title = product['name']
                    product['product_title'] = product_title  # Standardize field name
                else:
                    continue  # Skip if no title found
                
                # --- PRIORITY REVERSAL: First use citation URL, then fall back to JSON URL ---
                # Try to find a matching citation first
                matching_citation = find_best_citation_match(product_title)
                
                if matching_citation and matching_citation['url']:
                    # Use the citation URL instead of the JSON URL
                    original_url = product.get('url', 'none')
                    product['url'] = matching_citation['url']
                    logger.info(f"Using citation URL instead of original: {original_url} -> {matching_citation['url']}")
                    
                    # Also validate the citation URL
                    is_valid = await validate_target_url(matching_citation['url'], SKIP_URL_HTTP_VALIDATION)
                    if not is_valid:
                        logger.warning(f"Citation URL failed validation: {matching_citation['url']}")
                        # If citation URL is invalid, we'll try the original URL as fallback
                        if original_url.startswith('https://www.target.com/'):
                            is_original_valid = await validate_target_url(original_url, SKIP_URL_HTTP_VALIDATION)
                            if is_original_valid:
                                product['url'] = original_url
                                logger.info(f"Falling back to original URL that passed validation: {original_url}")
                            else:
                                logger.warning(f"Both citation and original URLs are invalid, skipping product: {product_title}")
                                continue
                        else:
                            # Try remaining unused citations as a last resort
                            found_backup = False
                            for citation in target_citations:
                                if not citation['used']:
                                    is_valid = await validate_target_url(citation['url'], SKIP_URL_HTTP_VALIDATION)
                                    if is_valid:
                                        product['url'] = citation['url']
                                        citation['used'] = True
                                        logger.info(f"Using backup citation URL for {product_title}: {citation['url']}")
                                        found_backup = True
                                        break
                            
                            if not found_backup:
                                logger.warning(f"No valid URL found for product, skipping: {product_title}")
                                continue
                else:
                    # No matching citation, validate the JSON URL
                    url = product.get('url', '')
                    if not url or not url.startswith('https://www.target.com/'):
                        # Try finding ANY unused citation as fallback
                        found_backup = False
                        for citation in target_citations:
                            if not citation['used']:
                                is_valid = await validate_target_url(citation['url'], SKIP_URL_HTTP_VALIDATION)
                                if is_valid:
                                    product['url'] = citation['url']
                                    citation['used'] = True
                                    logger.info(f"Using unmatched citation URL for {product_title}: {citation['url']}")
                                    found_backup = True
                                    break
                        
                        if not found_backup:
                            logger.warning(f"No valid URL for product and no unused citations, skipping: {product_title}")
                            continue
                    else:
                        # Validate the JSON URL
                        is_valid = await validate_target_url(url, SKIP_URL_HTTP_VALIDATION)
                        if not is_valid:
                            # If JSON URL is invalid, try finding ANY unused citation
                            found_backup = False
                            for citation in target_citations:
                                if not citation['used']:
                                    is_valid = await validate_target_url(citation['url'], SKIP_URL_HTTP_VALIDATION)
                                    if is_valid:
                                        product['url'] = citation['url']
                                        citation['used'] = True
                                        logger.info(f"JSON URL invalid, using unmatched citation for {product_title}: {citation['url']}")
                                        found_backup = True
                                        break
                            
                            if not found_backup:
                                logger.warning(f"No valid URL found for product, skipping: {product_title}")
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
            
            # If we have unused citations but not enough products, add them as products
            if len(valid_products) < max_results:
                for citation in target_citations:
                    if not citation['used'] and citation.get('title') and citation.get('url'):
                        # Create a new product from the citation
                        is_valid = await validate_target_url(citation['url'], SKIP_URL_HTTP_VALIDATION)
                        if is_valid:
                            new_product = {
                                'product_title': citation['title'],
                                'price': None,  # We don't have price info from citations
                                'url': citation['url'],
                                'in_stock': True,  # Assume in stock
                                'source': 'citation_only'  # Tag that this came directly from a citation
                            }
                            valid_products.append(new_product)
                            logger.info(f"Added product directly from unused citation: {citation['title']}")
                            
                            if len(valid_products) >= max_results:
                                break
            
            if valid_products:
                logger.info(f"Successfully found {len(valid_products)} products for '{query}'")
                return valid_products[:max_results]
            else:
                # If no valid products with strict validation, try with more permissive validation
                if not SKIP_URL_HTTP_VALIDATION:
                    logger.warning(f"No valid products with strict validation, trying with permissive validation")
                    # Try again with the data we already have but skip HTTP validation
                    valid_products = []
                    for product in products:
                        if not isinstance(product, dict):
                            continue
                        
                        # Simple format check
                        url = product.get('url', '')
                        if url and url.startswith('https://www.target.com/p/'):
                            product['validation_skipped'] = True
                            valid_products.append(product)
                    
                    if valid_products:
                        logger.info(f"Found {len(valid_products)} products with permissive validation")
                        return valid_products[:max_results]
                
                logger.warning(f"No valid products found for '{query}'")
                return []
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e} - Content: {response_content}")
            # Try a different approach - look for a JSON array in the response
            json_pattern = r'\[\s*\{.*?\}\s*\]'
            matches = re.search(json_pattern, response_content, re.DOTALL)
            if matches:
                try:
                    json_str = matches.group(0)
                    logger.info(f"Found JSON-like content, attempting to parse: {json_str[:100]}...")
                    data = json.loads(json_str)
                    if isinstance(data, list) and len(data) > 0:
                        logger.info(f"Successfully extracted {len(data)} products using regex")
                        
                        # Process these products with reversal priority for URLs
                        valid_products = []
                        for product in data:
                            if not isinstance(product, dict):
                                continue
                                
                            # Allow both product_title and name fields
                            if 'product_title' in product:
                                product_title = product['product_title']
                            elif 'name' in product:
                                product_title = product['name']
                                product['product_title'] = product_title  # Standardize field name
                            else:
                                continue  # Skip if no title found
                            
                            # Try to find a citation match first
                            matching_citation = find_best_citation_match(product_title)
                            if matching_citation:
                                product['url'] = matching_citation['url']
                                
                            # Basic validation
                            if product.get('url') and product['url'].startswith('https://www.target.com/'):
                                is_valid = await validate_target_url(product['url'], SKIP_URL_HTTP_VALIDATION)
                                if is_valid:
                                    valid_products.append(product)
                                
                        if valid_products:
                            logger.info(f"Returning {len(valid_products)} products after fallback parsing")
                            return valid_products[:max_results]
                except Exception as parse_e:
                    logger.error(f"Error in fallback JSON parsing: {parse_e}")
            
            # If we still have no products but have citations, create products from citations
            if target_citations:
                valid_products = []
                for citation in target_citations:
                    if citation.get('title') and citation.get('url'):
                        is_valid = await validate_target_url(citation['url'], SKIP_URL_HTTP_VALIDATION)
                        if is_valid:
                            new_product = {
                                'product_title': citation['title'],
                                'price': None,  # We don't have price info 
                                'url': citation['url'],
                                'in_stock': True,  # Assume in stock
                                'source': 'citation_only'  # Tag as citation-only
                            }
                            valid_products.append(new_product)
                            logger.info(f"Created product from citation when JSON parsing failed: {citation['title']}")
                            
                            if len(valid_products) >= max_results:
                                break
                
                if valid_products:
                    logger.info(f"Returning {len(valid_products)} products created from citations")
                    return valid_products[:max_results]
            
            # Last resort: create basic product entries from the response with permissive URL validation
            if SKIP_URL_HTTP_VALIDATION:
                # Extract possible Target URLs from the text
                url_pattern = r'https://www\.target\.com/p/[^\s\'")\]>]+'
                url_matches = re.findall(url_pattern, response_content)
                if url_matches:
                    valid_products = []
                    for i, url in enumerate(url_matches):
                        if i >= max_results:
                            break
                        
                        # Create a basic product
                        new_product = {
                            'product_title': f"Product from URL {i+1}",  # Generic title
                            'price': None,
                            'url': url,
                            'in_stock': True,
                            'source': 'extracted_url'
                        }
                        valid_products.append(new_product)
                        logger.info(f"Created product from extracted URL: {url}")
                    
                    if valid_products:
                        logger.info(f"Returning {len(valid_products)} products from extracted URLs")
                        return valid_products
            
            return []
        
    except Exception as e:
        logger.error(f"Error in search_products_gpt: {str(e)}")
        return []