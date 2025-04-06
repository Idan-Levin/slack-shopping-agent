#!/usr/bin/env python
"""
Test script for the Target Automation Bridge.
This script creates sample data and tests the bridge functionality.
"""

import os
import json
import logging
from target_bridge import TargetBridge

logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Sample shopping list items
SAMPLE_ITEMS = [
    {
        "id": 1,
        "product_title": "Tide Pods Laundry Detergent Pacs - Spring Meadow",
        "product_url": "https://www.target.com/p/tide-pods-laundry-detergent-pacs-spring-meadow/-/A-13967308",
        "price": 12.99,
        "quantity": 2,
        "user_name": "John Doe"
    },
    {
        "id": 2,
        "product_title": "Bounty Select-A-Size Paper Towels",
        "product_url": "https://www.target.com/p/bounty-select-a-size-paper-towels/-/A-13288866",
        "price": 15.99,
        "quantity": 1,
        "user_name": "Jane Smith"
    },
    {
        "id": 3,
        "product_title": "Cheerios Breakfast Cereal",
        "product_url": "https://www.target.com/p/cheerios-breakfast-cereal/-/A-13301319",
        "price": 3.99,
        "quantity": 3,
        "user_name": "John Doe"
    },
    {
        "id": 4,
        "product_title": "Folgers Classic Roast Ground Coffee",
        "product_url": "https://www.target.com/p/folgers-classic-roast-ground-coffee/-/A-12945397",
        "price": 8.49,
        "quantity": 1,
        "user_name": "Jane Smith"
    },
    {
        "id": 5,
        "product_title": "Clorox Disinfecting Wipes",
        "product_url": "https://www.target.com/p/clorox-disinfecting-wipes/-/A-12992342",
        "price": 4.99,
        "quantity": 2,
        "user_name": "Admin User"
    }
]

def create_sample_data():
    """Create sample data files for testing."""
    # Create exports directory if it doesn't exist
    export_dir = os.getenv("EXPORT_DIR", "./exports")
    if not os.path.exists(export_dir):
        os.makedirs(export_dir, exist_ok=True)
        logger.info(f"Created export directory: {export_dir}")
    
    # Create sample JSON file
    sample_json_path = os.path.join(export_dir, "sample_shopping_list.json")
    try:
        with open(sample_json_path, 'w') as f:
            json.dump(SAMPLE_ITEMS, f, indent=2)
        logger.info(f"Created sample JSON file: {sample_json_path}")
        return sample_json_path
    except Exception as e:
        logger.error(f"Error creating sample data: {e}")
        return None

def test_bridge_with_sample_data():
    """Test the Target Bridge with sample data."""
    sample_file = create_sample_data()
    if not sample_file:
        logger.error("Failed to create sample data")
        return False
    
    logger.info("Testing Target Bridge with sample data...")
    try:
        # Initialize bridge
        bridge = TargetBridge()
        
        # Test loading sample data
        shopping_list = bridge.load_shopping_list(sample_file)
        if not shopping_list:
            logger.error("Failed to load shopping list")
            return False
        
        logger.info(f"Successfully loaded {len(shopping_list)} items from sample data")
        
        # Test the automation launch (this is a placeholder test)
        success = bridge.launch_automation(shopping_list)
        
        # Test Slack notification
        if success and os.getenv("SLACK_AGENT_TOKEN") and os.getenv("TARGET_CHANNEL_ID"):
            logger.info("Testing Slack notification...")
            bridge.notify_slack("✅ Test notification from Target Automation Bridge!")
        
        logger.info("Bridge test completed successfully!")
        return True
    except Exception as e:
        logger.error(f"Error testing bridge: {e}")
        return False

if __name__ == "__main__":
    test_bridge_with_sample_data() 