import re
import logging
from typing import Optional # Import Optional

logger = logging.getLogger(__name__)

# Basic URL detection focusing on Target product pages
# Improved to be slightly more robust, but still basic
TARGET_URL_PATTERN = re.compile(r'https?://(?:www\.)?target\.com/p/([-/\w]+)/-/A-\w+')

def extract_target_url(text: str) -> Optional[str]:
    """Extracts the first matching Target product URL from text."""
    if not isinstance(text, str):
        return None
    match = TARGET_URL_PATTERN.search(text)
    if match:
         url = match.group(0)
         logger.debug(f"Extracted Target URL: {url}")
         return url
    return None

def format_price(price: Optional[float]) -> str:
    """Formats a float price into a string like $X.XX, or indicates if not found."""
    if price is None:
        return "Price not found"
    try:
        # Ensure price is float before formatting
        price_float = float(price)
        return f"${price_float:.2f}"
    except (ValueError, TypeError):
        logger.warning(f"Could not format invalid price value: {price}")
        return "Invalid price data"

# Add more helper functions as needed, e.g., for cleaning text
def clean_text(text: str) -> str:
     """Basic text cleaning (e.g., removing extra whitespace)."""
     if not isinstance(text, str):
          return ""
     return " ".join(text.split())