import streamlit as st
import psycopg2
from passlib.hash import pbkdf2_sha256
from datetime import datetime

# Initialize session state variables
if 'DEV_MODE' not in st.session_state:
    st.session_state.DEV_MODE = False
if 'auth_initialized' not in st.session_state:
    st.session_state.auth_initialized = False

class AuthSystem:
    def __init__(self):
        try:
            # Debug: Show available secrets
            st.write("🔍 Checking Streamlit secrets...")
            st.write("Available secrets:", list(st.secrets.keys()))
            
            if 'postgres' in st.secrets:
                st.write("✓ Found postgres configuration")
                if 'url' in st.secrets.postgres:
                    self.db_url = st.secrets.postgres.url
                    # Hide password in debug output
                    safe_url = self.db_url.replace(
                        self.db_url.split(':')[2].split('@')[0], 
                        '****'
                    )
                    st.write("📌 Database URL:", safe_url)
                    st.write("🔌 Attempting database connection...")
                else:
                    st.error("❌ Missing 'url' in postgres configuration")
                    return
            else:
                st.error("❌ Missing 'postgres' section in secrets")
                return
            
            self._init_db()
            st.session_state.auth_initialized = True
            
        except Exception as e:
            st.error(f"❌ Failed to initialize database: {str(e)}")
            st.info("💡 Check Streamlit settings > Secrets")

    def _init_db(self):
        try:
            st.write("🔄 Connecting to database...")
            conn = psycopg2.connect(
                self.db_url,
                connect_timeout=10
            )
            st.write("✓ Connection established")
            
            cur = conn.cursor()
            st.write("🔄 Creating tables if they don't exist...")
            
            # Create users table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            st.write("✓ Users table ready")
            
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
            st.write("✓ User data table ready")
            
            conn.commit()
            st.success("✅ Database initialization successful!")
            
        except psycopg2.Error as e:
            st.error(f"❌ Database error: {str(e)}")
            st.error(f"Error code: {e.pgcode}")
            st.error(f"Error message: {e.pgerror}")
        finally:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()

    def _get_connection(self):
        try:
            return psycopg2.connect(self.db_url)
        except psycopg2.Error as e:
            st.error(f"❌ Connection failed: {str(e)}")
            return None

    def register_user(self, username, password):
        conn = self._get_connection()
        if not conn:
            return False, "Database connection failed"
        
        try:
            cur = conn.cursor()
            st.write("🔄 Registering new user...")
            password_hash = pbkdf2_sha256.hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash)
            )
            conn.commit()
            st.write("✓ User registered successfully")
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
            st.write("🔄 Attempting login...")
            cur.execute(
                "SELECT id, password_hash FROM users WHERE username = %s",
                (username,)
            )
            result = cur.fetchone()
            
            if result and pbkdf2_sha256.verify(password, result[1]):
                st.write("✓ Login successful")
                return True, result[0]
            return False, None
            
        except psycopg2.Error as e:
            st.error(f"❌ Login error: {str(e)}")
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
            st.error(f"❌ Error saving user data: {str(e)}")
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
            st.error(f"❌ Error loading user data: {str(e)}")
            return None
        finally:
            if 'cur' in locals():
                cur.close()
            conn.close()

def init_auth():
    try:
        # Add development mode toggle in sidebar
        st.sidebar.checkbox("🛠️ Development Mode", 
                           key='DEV_MODE', 
                           value=False,
                           help="Toggle between development and production mode")

        if st.session_state.DEV_MODE:
            st.session_state.user_id = 1
            st.sidebar.warning("🛠️ Development Mode Active")
            return

        if not st.session_state.auth_initialized:
            st.write("🔄 Initializing authentication system...")
            st.session_state.auth_system = AuthSystem()
            
        if 'user_id' not in st.session_state:
            st.session_state.user_id = None

    except Exception as e:
        st.error(f"❌ Authentication initialization error: {str(e)}")
        st.session_state.DEV_MODE = True
        st.session_state.user_id = 1

def render_auth_page():
    if st.session_state.DEV_MODE:
        return True

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
                if not username or not password:
                    st.error("Please enter both username and password")
                else:
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
                if not new_username or not new_password:
                    st.error("Please fill in all fields")
                elif new_password != confirm_password:
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
