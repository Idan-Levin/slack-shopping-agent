#!/usr/bin/env python3

import asyncio
import os
import json
import logging
from dotenv import load_dotenv
from product_service import search_products_gpt, validate_target_url

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_search")

# Load environment variables from .env file
load_dotenv()

# Ensure OpenAI API key is available
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logger.error("OPENAI_API_KEY environment variable is not set. Please set it before running this test.")
    exit(1)
else:
    logger.info("OPENAI_API_KEY found in environment")

async def test_search(query):
    """Test the GPT-4o-mini Search Preview for product search"""
    logger.info(f"Testing product search for: '{query}'")
    
    try:
        # Call the search function
        results = await search_products_gpt(query)
        
        if results is None:
            logger.error("Search returned None")
            return False
            
        logger.info(f"Search returned {len(results)} results")
        
        # Print results in a readable format
        for i, product in enumerate(results, 1):
            logger.info(f"Product {i}:")
            logger.info(f"  Name: {product.get('name')}")
            logger.info(f"  Price: ${product.get('price')}" if product.get('price') is not None else "  Price: Unknown")
            
            # Enhanced URL information
            url = product.get('url')
            if url:
                logger.info(f"  URL: {url}")
                # Validate URL
                is_valid = await validate_target_url(url)
                validation_status = "✅ VALID" if is_valid else "❌ INVALID"
                logger.info(f"  URL Validation: {validation_status}")
            else:
                logger.info(f"  URL: None")
                
            logger.info(f"  Image URL: {product.get('image_url') or 'None'}")
            logger.info(f"  In Stock: {product.get('in_stock')}")
            
        # Validate URLs
        valid_urls = []
        for product in results:
            url = product.get('url')
            if url and url.startswith('https://www.target.com/p/'):
                is_valid = await validate_target_url(url)
                if is_valid:
                    valid_urls.append(url)
                    
        logger.info(f"Found {len(valid_urls)} verified working Target product URLs out of {len(results)} results")
        
        # Save results to a file for inspection
        with open(f"search_results_{query.replace(' ', '_')}.json", "w") as f:
            json.dump(results, f, indent=2)
            logger.info(f"Saved results to search_results_{query.replace(' ', '_')}.json")
            
        return len(valid_urls) > 0
        
    except Exception as e:
        logger.error(f"Error during test: {e}", exc_info=True)
        return False

async def main():
    """Run tests with different queries"""
    test_queries = [
        "Oreo cookies original",
        "Dawn dish soap",
        "Nintendo Switch game"
    ]
    
    results = []
    for query in test_queries:
        success = await test_search(query)
        results.append((query, success))
        logger.info("-" * 50)
    
    # Print summary
    logger.info("\nTest Results Summary:")
    for query, success in results:
        status = "PASSED" if success else "FAILED"
        logger.info(f"{status}: {query}")

if __name__ == "__main__":
    asyncio.run(main()) 