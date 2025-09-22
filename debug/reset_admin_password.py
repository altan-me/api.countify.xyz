#!/usr/bin/env python3
"""
Script to reset the admin password
"""
import sqlite3
import os
import getpass
from werkzeug.security import generate_password_hash
try:
    from argon2 import PasswordHasher
except ImportError:
    PasswordHasher = None

def reset_admin_password():
    database = os.getenv('DATABASE', 'counters.db')
    
    if not os.path.exists(database):
        print(f"Database file {database} does not exist!")
        return
    
    # Get new password
    new_password = getpass.getpass("Enter new admin password: ")
    confirm_password = getpass.getpass("Confirm new admin password: ")
    
    if new_password != confirm_password:
        print("❌ Passwords don't match!")
        return
    
    if len(new_password) < 8:
        print("❌ Password must be at least 8 characters long!")
        return
    
    try:
        with sqlite3.connect(database) as db:
            db.row_factory = sqlite3.Row
            cursor = db.cursor()
            
            # Check if admin user exists
            cursor.execute('SELECT username FROM users WHERE username = ?', ('admin',))
            if not cursor.fetchone():
                print("❌ Admin user not found!")
                return
            
            # Hash the new password
            if PasswordHasher:
                password_hash = PasswordHasher().hash(new_password)
                hash_type = "Argon2"
            else:
                password_hash = generate_password_hash(new_password)
                hash_type = "PBKDF2"
            
            # Update the password
            cursor.execute('UPDATE users SET password_hash = ? WHERE username = ?', (password_hash, 'admin'))
            db.commit()
            
            print(f"✓ Admin password updated successfully using {hash_type}!")
            print("You can now login with username 'admin' and your new password.")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    reset_admin_password()