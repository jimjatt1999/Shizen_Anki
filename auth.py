import streamlit as st
import psycopg2
from passlib.hash import pbkdf2_sha256
from datetime import datetime

# Store DEV_MODE in session state
if 'DEV_MODE' not in st.session_state:
    st.session_state.DEV_MODE = False

class AuthSystem:
    def __init__(self):
        try:
            # Get database parameters from Streamlit secrets
            self.db_params = {
                "user": st.secrets.postgres.user,
                "password": st.secrets.postgres.password,
                "host": st.secrets.postgres.host,
                "port": st.secrets.postgres.port,
                "dbname": st.secrets.postgres.dbname
            }
            # Debug info
            st.write("Connection parameters:", {
                k: v if k != 'password' else '****' 
                for k, v in self.db_params.items()
            })
            self._init_db()
        except Exception as e:
            st.error(f"Failed to initialize database: {e}")
            if st.session_state.DEV_MODE:
                st.info("Continuing in development mode...")

    def _init_db(self):
        try:
            conn = psycopg2.connect(**self.db_params)
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
            
            # Create user_data table
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
            st.success("Database connection successful!")
            
        except psycopg2.Error as e:
            if not st.session_state.DEV_MODE:
                st.error(f"Database initialization failed: {e}")
            st.info("Make sure PostgreSQL parameters are configured in .streamlit/secrets.toml")
        finally:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()

    def _get_connection(self):
        try:
            return psycopg2.connect(**self.db_params)
        except psycopg2.Error as e:
            st.error(f"Database connection failed: {e}")
            return None

    def register_user(self, username, password):
        conn = self._get_connection()
        if not conn:
            return False, "Database connection failed"
        
        try:
            cur = conn.cursor()
            password_hash = pbkdf2_sha256.hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash)
            )
            conn.commit()
            return True, None
            
        except psycopg2.Error as e:
            if "duplicate key" in str(e):
                return False, "Username already exists"
            return False, str(e)
        finally:
            if 'cur' in locals():
                cur.close()
            conn.close()

    def login_user(self, username, password):
        conn = self._get_connection()
        if not conn:
            return False, None
        
        try:
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
            st.error(f"Login error: {e}")
            return False, None
        finally:
            if 'cur' in locals():
                cur.close()
            conn.close()

    def save_user_data(self, user_id, data_key, data_value):
        conn = self._get_connection()
        if not conn:
            return False
        
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO user_data (user_id, data_key, data_value, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, data_key) 
                DO UPDATE SET data_value = %s, updated_at = CURRENT_TIMESTAMP
            """, (user_id, data_key, data_value, data_value))
            
            conn.commit()
            return True
            
        except psycopg2.Error as e:
            st.error(f"Error saving user data: {e}")
            return False
        finally:
            if 'cur' in locals():
                cur.close()
            conn.close()

    def load_user_data(self, user_id, data_key):
        conn = self._get_connection()
        if not conn:
            return None
        
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT data_value FROM user_data WHERE user_id = %s AND data_key = %s",
                (user_id, data_key)
            )
            result = cur.fetchone()
            return result[0] if result else None
            
        except psycopg2.Error as e:
            st.error(f"Error loading user data: {e}")
            return None
        finally:
            if 'cur' in locals():
                cur.close()
            conn.close()

def init_auth():
    # Add development mode toggle in sidebar
    st.sidebar.checkbox("🛠️ Development Mode", 
                       key='DEV_MODE', 
                       value=False,  # Set to False for production
                       help="Toggle between development and production mode")

    if st.session_state.DEV_MODE:
        if 'user_id' not in st.session_state:
            st.session_state.user_id = 1
    else:
        if 'auth_system' not in st.session_state:
            try:
                st.session_state.auth_system = AuthSystem()
            except Exception as e:
                st.error(f"Failed to initialize auth system: {e}")
                st.session_state.DEV_MODE = True
                st.session_state.user_id = 1
        
        if 'user_id' not in st.session_state:
            st.session_state.user_id = None

def render_auth_page():
    if st.session_state.DEV_MODE:
        # Development mode: auto-login
        if 'user_id' not in st.session_state:
            st.session_state.user_id = 1
        st.sidebar.warning("🛠️ Development Mode Active")
        return True

    # Production mode authentication
    if st.session_state.user_id:
        return True

    st.title("自然暗記 - Login")
    
    tab1, tab2 = st.tabs(<span class="ml-2" /><span class="inline-block w-3 h-3 rounded-full bg-neutral-a12 align-middle mb-[0.1rem]" />
