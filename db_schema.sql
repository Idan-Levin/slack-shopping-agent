CREATE TABLE IF NOT EXISTS shopping_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    product_title TEXT NOT NULL,
    product_url TEXT,
    product_image_url TEXT,
    price REAL,
    quantity INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' -- 'active', 'ordered'
);

CREATE INDEX IF NOT EXISTS idx_user_id_status ON shopping_items (user_id, status);
CREATE INDEX IF NOT EXISTS idx_status ON shopping_items (status);