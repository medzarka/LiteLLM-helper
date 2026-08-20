import sqlite3
import re
import os

DB_PATH = 'data/litellm_helper.db'

def get_email_type(email):
    email = email.lower()
    if '@gmail' in email: return 'gmail'
    if '@gmx' in email: return 'gmx'
    if '@hotmail' in email: return 'hotmail'
    if '@mail.com' in email: return 'mail'
    return 'other'

def migrate():
    if not os.path.exists(DB_PATH):
        print("Database not found at", DB_PATH)
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Update provider table
    try:
        cursor.execute("ALTER TABLE provider ADD COLUMN provider_type TEXT;")
        # Set provider_type to name for existing
        cursor.execute("UPDATE provider SET provider_type = name;")
        print("Added provider_type to provider table.")
    except sqlite3.OperationalError as e:
        if 'duplicate column' in str(e).lower():
            print("provider_type column already exists.")
        else:
            print("Error altering provider:", e)

    # 2. Create email_account table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL DEFAULT '',
            email_type TEXT NOT NULL DEFAULT 'other',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 3. Create new api_key table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_key_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            email_id INTEGER NOT NULL,
            key_name TEXT NOT NULL,
            key_value TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (provider_id) REFERENCES provider (id),
            FOREIGN KEY (email_id) REFERENCES email_account (id)
        )
    ''')

    # 4. Migrate data
    cursor.execute("SELECT id, provider_id, key_name, key_value, is_active, created_at FROM api_key")
    old_keys = cursor.fetchall()
    
    email_map = {} # email -> email_id
    
    for key in old_keys:
        key_id, provider_id, key_name, key_value, is_active, created_at = key
        # key_name contains the email
        email = key_name.strip()
        if not email:
            email = "unknown@example.com"
            
        if email not in email_map:
            # check if exists in db
            cursor.execute("SELECT id FROM email_account WHERE email = ?", (email,))
            row = cursor.fetchone()
            if row:
                email_map[email] = row[0]
            else:
                e_type = get_email_type(email)
                cursor.execute("INSERT INTO email_account (email, password, email_type) VALUES (?, ?, ?)", 
                               (email, "", e_type))
                email_map[email] = cursor.lastrowid
                
        email_id = email_map[email]
        
        cursor.execute('''
            INSERT INTO api_key_new (id, provider_id, email_id, key_name, key_value, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (key_id, provider_id, email_id, key_name, key_value, is_active, created_at))

    # 5. Swap tables
    cursor.execute("DROP TABLE api_key")
    cursor.execute("ALTER TABLE api_key_new RENAME TO api_key")
    
    conn.commit()
    conn.close()
    print(f"Successfully migrated {len(old_keys)} keys.")

if __name__ == '__main__':
    migrate()
