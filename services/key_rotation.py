try:
    from ..models.models import Database
except (ImportError, ValueError):
    from models.models import Database

def get_rotation_settings():
    """Get the current rotation settings from the database"""
    db = Database()
    cursor = db.conn.cursor()
    cursor.execute('SELECT routing_strategy, key_rotation_strategy, cooldown_time, allowed_fails, num_retries FROM rotation_settings')
    row = cursor.fetchone()
    db.close()
    
    if row:
        return {
            'routing_strategy': row[0],
            'key_rotation_strategy': row[1],
            'cooldown_time': row[2],
            'allowed_fails': row[3],
            'num_retries': row[4]
        }
    else:
        return {
            'routing_strategy': 'simple-shuffle',
            'key_rotation_strategy': 'round-robin',
            'cooldown_time': 60,
            'allowed_fails': 2,
            'num_retries': 1
        }

def update_rotation_settings(routing_strategy, key_rotation_strategy, cooldown_time, allowed_fails, num_retries):
    """Update the rotation settings in the database"""
    db = Database()
    cursor = db.conn.cursor()
    cursor.execute('''
        UPDATE rotation_settings
        SET routing_strategy = ?, key_rotation_strategy = ?, cooldown_time = ?, allowed_fails = ?, num_retries = ?
        WHERE id = 1
    ''', (routing_strategy, key_rotation_strategy, cooldown_time, allowed_fails, num_retries))
    db.conn.commit()
    db.close()
    
    return True

def reset_rotation_settings():
    """Reset the rotation settings to default values"""
    db = Database()
    cursor = db.conn.cursor()
    cursor.execute('''
        UPDATE rotation_settings
        SET routing_strategy = ?, key_rotation_strategy = ?, cooldown_time = ?, allowed_fails = ?, num_retries = ?
        WHERE id = 1
    ''', ('simple-shuffle', 'round-robin', 60, 2, 1))
    db.conn.commit()
    db.close()
    
    return True