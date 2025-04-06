#!/usr/bin/env python
"""
Target Automation Bridge Script

This script serves as a bridge between the Slack Shopping Agent and the Target Automation system.
It processes exported shopping lists and prepares them for the Target automation.
"""

import os
import sys
import json
import logging
import argparse
from typing import Dict, List, Optional, Any
from datetime import datetime
import requests  # For potential API integrations

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'target_bridge.log'))
    ]
)
logger = logging.getLogger(__name__)

class TargetBridge:
    """Bridge between Slack Shopping Agent and Target Automation."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the bridge with optional configuration."""
        self.config = self._load_config(config_path)
        self.export_dir = self.config.get("export_dir", "./exports")
        self.automation_path = self.config.get("automation_path", "")
        
        # Ensure export directory exists
        if not os.path.exists(self.export_dir):
            os.makedirs(self.export_dir, exist_ok=True)
            logger.info(f"Created export directory: {self.export_dir}")
    
    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """Load configuration from a file or environment variables."""
        config = {}
        
        # Default configuration
        config["export_dir"] = os.getenv("EXPORT_DIR", "./exports")
        config["automation_path"] = os.getenv("TARGET_AUTOMATION_PATH", "")
        config["slack_token"] = os.getenv("SLACK_AGENT_TOKEN", "")
        config["target_channel"] = os.getenv("TARGET_CHANNEL_ID", "")
        
        # If config file provided, override with its values
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    file_config = json.load(f)
                    config.update(file_config)
                logger.info(f"Loaded configuration from {config_path}")
            except Exception as e:
                logger.error(f"Error loading config from {config_path}: {e}")
        
        return config
    
    def get_latest_export(self, file_pattern: str = "shopping_list_*.json") -> Optional[str]:
        """Find the most recent export file matching the pattern."""
        if not os.path.exists(self.export_dir):
            logger.error(f"Export directory does not exist: {self.export_dir}")
            return None
            
        # Find all files matching the pattern
        import glob
        pattern = os.path.join(self.export_dir, file_pattern)
        matching_files = glob.glob(pattern)
        
        if not matching_files:
            logger.error(f"No files found matching pattern: {pattern}")
            return None
            
        # Sort by modification time (newest first)
        matching_files.sort(key=os.path.getmtime, reverse=True)
        latest_file = matching_files[0]
        logger.info(f"Found latest export: {latest_file}")
        return latest_file
    
    def load_shopping_list(self, file_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Load shopping list from a JSON file."""
        if not file_path:
            file_path = self.get_latest_export()
            if not file_path:
                logger.error("No export file found to load")
                return []
        
        try:
            with open(file_path, 'r') as f:
                shopping_list = json.load(f)
            
            if not isinstance(shopping_list, list):
                logger.error(f"Invalid shopping list format in {file_path}")
                return []
                
            logger.info(f"Loaded {len(shopping_list)} items from {file_path}")
            return shopping_list
        except Exception as e:
            logger.error(f"Error loading shopping list from {file_path}: {e}")
            return []
    
    def launch_automation(self, shopping_list: List[Dict[str, Any]]) -> bool:
        """
        Launch the Target automation with the provided shopping list.
        
        This is a placeholder - the actual implementation would depend on
        how the Target automation system is designed to be triggered.
        """
        if not shopping_list:
            logger.error("No items in shopping list to process")
            return False
            
        automation_path = self.config.get("automation_path")
        if not automation_path:
            logger.error("No automation path configured")
            return False
            
        logger.info(f"Preparing to launch automation for {len(shopping_list)} items")
        
        # PLACEHOLDER: This is where you would implement the integration with your 
        # Target automation system. For example:
        # 
        # 1. Write the shopping list to a specific format the automation expects
        # 2. Call a command-line tool or script
        # 3. Invoke an API endpoint
        # 4. Trigger a workflow in an automation platform
        
        logger.info("This is a placeholder for the actual automation integration")
        logger.info(f"Would launch: {automation_path} with {len(shopping_list)} items")
        
        # For testing, just log what we would do
        for item in shopping_list:
            product = item.get("product_title", "Unknown product")
            quantity = item.get("quantity", 1)
            price = item.get("price", "unknown price")
            logger.info(f"Would order: {quantity}x {product} (${price})")
        
        # Return True to simulate successful automation launch
        return True
    
    def notify_slack(self, message: str) -> bool:
        """Send a notification back to Slack."""
        slack_token = self.config.get("slack_token")
        channel_id = self.config.get("target_channel")
        
        if not slack_token or not channel_id:
            logger.error("Slack token or channel ID not configured")
            return False
            
        try:
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={"channel": channel_id, "text": message}
            )
            
            if response.status_code == 200 and response.json().get("ok"):
                logger.info(f"Sent Slack notification to channel {channel_id}")
                return True
            else:
                logger.error(f"Failed to send Slack notification: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending Slack notification: {e}")
            return False
    
    def process_latest_export(self) -> bool:
        """Process the latest export file and launch automation."""
        latest_export = self.get_latest_export()
        if not latest_export:
            return False
            
        shopping_list = self.load_shopping_list(latest_export)
        if not shopping_list:
            return False
            
        # Launch the automation
        success = self.launch_automation(shopping_list)
        
        # Notify Slack of the result
        if success:
            self.notify_slack(f"✅ Successfully processed shopping list with {len(shopping_list)} items for Target automation!")
        else:
            self.notify_slack(f"❌ Failed to process shopping list for Target automation. Check logs for details.")
            
        return success


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Target Automation Bridge")
    parser.add_argument("--config", help="Path to configuration file")
    parser.add_argument("--file", help="Path to specific shopping list file to process")
    parser.add_argument("--notify", action="store_true", help="Send notification to Slack")
    args = parser.parse_args()
    
    try:
        bridge = TargetBridge(config_path=args.config)
        
        if args.file:
            logger.info(f"Processing specific file: {args.file}")
            shopping_list = bridge.load_shopping_list(args.file)
            if not shopping_list:
                logger.error(f"Failed to load shopping list from {args.file}")
                return 1
                
            success = bridge.launch_automation(shopping_list)
        else:
            logger.info("Processing latest export file")
            success = bridge.process_latest_export()
        
        if args.notify:
            if success:
                bridge.notify_slack("✅ Target automation process completed successfully!")
            else:
                bridge.notify_slack("❌ Target automation process failed. Check logs for details.")
        
        return 0 if success else 1
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main()) 