import streamlit as st
import psycopg2
from passlib.hash import pbkdf2_sha256
import os
from datetime import datetime, timedelta

class AuthSystem:
    def __init__(self, db_url):
        self.db_url = db_url
        self._init_db()

    def _init_db(self):
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        # Create users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create user_data table for storing app state
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_data (
                user_id INTEGER REFERENCES users(id),
                data_key VARCHAR(50),
                data_value JSONB,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, data_key)
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()

    def register_user(self, username, password):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            password_hash = pbkdf2_sha256.hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash)
            )
            
            conn.commit()
            return True, None
            
        except psycopg2.Error as e:
            return False, str(e)
        finally:
            cur.close()
            conn.close()

    def login_user(self, username, password):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            cur.execute(
                "SELECT id, password_hash FROM users WHERE username = %s",
                (username,)
            )
            result = cur.fetchone()
            
            if result and pbkdf2_sha256.verify(password, result[1]):
                return True, result[0]  # Return user_id
            return False, None
            
        except psycopg2.Error as e:
            return False, None
        finally:
            cur.close()
            conn.close()

    def save_user_data(self, user_id, data_key, data_value):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO user_data (user_id, data_key, data_value, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, data_key) 
                DO UPDATE SET data_value = %s, updated_at = CURRENT_TIMESTAMP
            """, (user_id, data_key, data_value, data_value))
            
            conn.commit()
            return True
            
        except psycopg2.Error:
            return False
        finally:
            cur.close()
            conn.close()

    def load_user_data(self, user_id, data_key):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            cur.execute(
                "SELECT data_value FROM user_data WHERE user_id = %s AND data_key = %s",
                (user_id, data_key)
            )
            result = cur.fetchone()
            
            return result[0] if result else None
            
        except psycopg2.Error:
            return None
        finally:
            cur.close()
            conn.close()

def init_auth():
    if 'auth_system' not in st.session_state:
        db_url = os.getenv('DATABASE_URL')  
        st.session_state.auth_system = AuthSystem(db_url)
    
    if 'user_id' not in st.session_state:
        st.session_state.user_id = None
        
def render_auth_page():
    if st.session_state.user_id:
        return True

    st.title("自然暗記 - Login")
    
    tab1, tab2 = st.tabs(["Login", "Register"])
    
    with tab1:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Login")
            
            if submit:
                success, user_id = st.session_state.auth_system.login_user(username, password)
                if success:
                    st.session_state.user_id = user_id
                    st.rerun()
                else:
                    st.error("Invalid credentials")
    
    with tab2:
        with st.form("register_form"):
            new_username = st.text_input("Choose Username")
            new_password = st.text_input("Choose Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submit = st.form_submit_button("Register")
            
            if submit:
                if new_password != confirm_password:
                    st.error("Passwords don't match")
                elif len(new_password) < 8:
                    st.error("Password must be at least 8 characters")
                else:
                    success, error = st.session_state.auth_system.register_user(new_username, new_password)
                    if success:
                        st.success("Registration successful! Please login.")
                    else:
                        st.error(f"Registration failed: {error}")
    
    return False
