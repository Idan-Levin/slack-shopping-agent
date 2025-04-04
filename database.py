import sqlite3
import os
import logging
from dotenv import load_dotenv
from contextlib import contextmanager
from typing import List, Dict, Any, Optional

# Load environment variables to get DATABASE_PATH
load_dotenv()
# Default to './shopping_list.db' for local dev if not set for Render/Docker
DATABASE_PATH = os.getenv("DATABASE_PATH", "./shopping_list.db")
logger = logging.getLogger(__name__)

@contextmanager
def get_db_connection():
    """Provides a database connection."""
    # Ensure the directory for the database exists (important for Docker volumes)
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        logger.info(f"Database directory '{db_dir}' not found, creating.")
        os.makedirs(db_dir, exist_ok=True)

    conn = None
    try:
        logger.debug(f"Connecting to database at: {DATABASE_PATH}")
        conn = sqlite3.connect(DATABASE_PATH, timeout=10) # Add timeout
        conn.row_factory = sqlite3.Row # Return rows as dict-like objects
        yield conn
    except sqlite3.Error as e:
         logger.error(f"Database connection error to {DATABASE_PATH}: {e}", exc_info=True)
         raise # Re-raise the exception after logging
    finally:
        if conn:
            conn.close()
            logger.debug("Database connection closed.")

def initialize_db():
    """Initializes the database schema if the table doesn't exist."""
    schema_file = 'db_schema.sql'
    if not os.path.exists(schema_file):
        logger.error(f"Database schema file '{schema_file}' not found.")
        return

    try:
        with get_db_connection() as conn:
            logger.info("Initializing database schema...")
            with open(schema_file, 'r') as f:
                conn.executescript(f.read())
            conn.commit() # Ensure schema changes are committed
            logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database schema: {e}", exc_info=True)
    except IOError as e:
        logger.error(f"Failed to read schema file '{schema_file}': {e}", exc_info=True)


def add_item(user_id: str, user_name: str, title: str, quantity: int, price: Optional[float] = None, url: Optional[str] = None, image_url: Optional[str] = None) -> int:
    """Adds an item to the shopping list."""
    logger.info(f"Adding item: Qty={quantity}, Title='{title}', User={user_id}({user_name})")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO shopping_items (user_id, user_name, product_title, quantity, price, product_url, product_image_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (user_id, user_name, title, quantity, price, url, image_url)
        )
        conn.commit()
        last_id = cursor.lastrowid
        logger.info(f"Item added successfully with ID: {last_id}")
        return last_id # type: ignore # cursor.lastrowid can be None but unlikely on successful insert

def get_active_items() -> List[Dict[str, Any]]:
    """Retrieves all active items from the shopping list."""
    logger.debug("Fetching all active items.")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM shopping_items WHERE status = 'active' ORDER BY added_at DESC")
        items = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Found {len(items)} active items.")
        return items

def get_item_by_id(item_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves a specific item by its ID."""
    logger.debug(f"Fetching item by ID: {item_id}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if row:
             logger.debug(f"Item ID {item_id} found.")
             return dict(row)
        else:
             logger.debug(f"Item ID {item_id} not found.")
             return None

def find_items_by_description(user_id: str, description: str) -> List[Dict[str, Any]]:
    """Find active items for a specific user matching a description (case-insensitive)."""
    logger.debug(f"Finding items for user {user_id} matching description: '{description}'")
    # Simple search using LIKE, can be improved with fuzzy matching libraries if needed
    query = f"%{description}%"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Use LOWER() for case-insensitive search on product_title
        cursor.execute(
            """
            SELECT * FROM shopping_items
            WHERE user_id = ? AND status = 'active' AND LOWER(product_title) LIKE LOWER(?)
            ORDER BY added_at DESC
            """,
            (user_id, query)
        )
        items = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Found {len(items)} items matching description for user {user_id}.")
        return items

def delete_item(item_id: int, user_id_requesting: Optional[str] = None) -> bool:
    """Deletes an item by ID, optionally checking ownership."""
    logger.info(f"Attempting to delete item ID: {item_id}, requested by user: {user_id_requesting}")
    item_to_delete = get_item_by_id(item_id)
    if not item_to_delete:
        logger.warning(f"Item ID {item_id} not found for deletion.")
        return False # Item doesn't exist

    # Permission Check (Optional but recommended): Only allow deletion if requested by the user who added it
    if user_id_requesting and item_to_delete['user_id'] != user_id_requesting:
        logger.warning(f"Permission denied: User {user_id_requesting} cannot delete item {item_id} added by {item_to_delete['user_id']}")
        raise PermissionError(f"User {user_id_requesting} cannot delete item {item_id} added by {item_to_delete['user_id']}")

    # Proceed with deletion
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
        conn.commit()
        deleted_count = cursor.rowcount
        if deleted_count > 0:
             logger.info(f"Successfully deleted item ID: {item_id}")
             return True
        else:
             # Should not happen if get_item_by_id found it, maybe race condition?
             logger.warning(f"Item ID {item_id} existed but no rows deleted (maybe deleted concurrently?).")
             return False

def update_item_quantity(item_id: int, quantity: int, user_id_requesting: Optional[str] = None) -> bool:
    """Updates the quantity of an item, optionally checking ownership."""
    logger.info(f"Attempting to update quantity for item ID: {item_id} to {quantity}, requested by user: {user_id_requesting}")
    if quantity <= 0:
         logger.warning(f"Invalid quantity {quantity} for update. Deleting item instead.")
         # If quantity is 0 or less, treat as deletion
         return delete_item(item_id, user_id_requesting)

    item_to_update = get_item_by_id(item_id)
    if not item_to_update:
        logger.warning(f"Item ID {item_id} not found for quantity update.")
        return False

    # Permission Check
    if user_id_requesting and item_to_update['user_id'] != user_id_requesting:
        logger.warning(f"Permission denied: User {user_id_requesting} cannot update item {item_id} added by {item_to_update['user_id']}")
        raise PermissionError(f"User {user_id_requesting} cannot update quantity for item {item_id}")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE shopping_items SET quantity = ? WHERE id = ?", (quantity, item_id))
        conn.commit()
        updated_count = cursor.rowcount
        if updated_count > 0:
            logger.info(f"Successfully updated quantity for item ID: {item_id} to {quantity}")
            return True
        else:
            logger.warning(f"Item ID {item_id} found but no rows updated.")
            return False


def mark_all_ordered() -> int:
    """Marks all active items as 'ordered'."""
    logger.info("Marking all active items as 'ordered'.")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE shopping_items SET status = 'ordered' WHERE status = 'active'")
        conn.commit()
        count = cursor.rowcount
        logger.info(f"Marked {count} items as ordered.")
        return count

# Auto-initialize DB on first import if DB file doesn't exist
# Note: `main.py` also calls initialize_db on startup, which is more robust for server restarts
# if not os.path.exists(DATABASE_PATH):
#     logger.info(f"Database file not found at {DATABASE_PATH} on module load, initializing...")
#     initialize_db()