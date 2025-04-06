import re
import logging
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any # Import Optional

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

def export_shopping_list(items: List[Dict[str, Any]], export_format: str = "json") -> Optional[str]:
    """
    Export the shopping list to a file in the specified format.
    
    Args:
        items: List of shopping items to export
        export_format: Format to export ('json' or 'txt')
        
    Returns:
        Path to the exported file or None if export failed
    """
    if not items:
        logger.warning("No items to export")
        return None
    
    # Create exports directory if it doesn't exist
    export_dir = os.getenv("EXPORT_DIR", "./exports")
    if not os.path.exists(export_dir):
        os.makedirs(export_dir, exist_ok=True)
        logger.info(f"Created export directory: {export_dir}")
    
    # Generate a timestamp for the filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if export_format.lower() == "json":
        # Create a simplified version of the items for export
        export_items = []
        for item in items:
            export_items.append({
                "id": item.get("id"),
                "product_title": item.get("product_title"),
                "product_url": item.get("product_url"),
                "price": item.get("price"),
                "quantity": item.get("quantity", 1),
                "user_name": item.get("user_name", "Unknown User")
            })
        
        # Generate the filename
        filename = f"shopping_list_{timestamp}.json"
        file_path = os.path.join(export_dir, filename)
        
        try:
            with open(file_path, 'w') as f:
                json.dump(export_items, f, indent=2)
            logger.info(f"Successfully exported shopping list to JSON: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to export shopping list to JSON: {e}", exc_info=True)
            return None
    
    elif export_format.lower() == "txt":
        # Generate the filename
        filename = f"shopping_list_{timestamp}.txt"
        file_path = os.path.join(export_dir, filename)
        
        try:
            with open(file_path, 'w') as f:
                f.write("Shopping List\n")
                f.write("============\n\n")
                
                # Group items by user
                items_by_user = {}
                for item in items:
                    user_name = item.get("user_name", "Unknown User")
                    if user_name not in items_by_user:
                        items_by_user[user_name] = []
                    items_by_user[user_name].append(item)
                
                # Write items grouped by user
                for user_name, user_items in items_by_user.items():
                    f.write(f"User: {user_name}\n")
                    for item in user_items:
                        product_title = item.get("product_title", "Unknown Item")
                        quantity = item.get("quantity", 1)
                        price = item.get("price")
                        price_str = format_price(price) if price is not None else "Price not found"
                        
                        line = f"- {quantity} x {product_title} ({price_str})"
                        
                        product_url = item.get("product_url")
                        if product_url:
                            line += f"\n  URL: {product_url}"
                        
                        f.write(line + "\n")
                    f.write("\n")
            
            logger.info(f"Successfully exported shopping list to TXT: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to export shopping list to TXT: {e}", exc_info=True)
            return None
    
    else:
        logger.error(f"Unsupported export format: {export_format}")
        return None