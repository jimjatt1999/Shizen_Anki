import streamlit as st
from datetime import datetime, date, timedelta
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import tempfile
from functools import lru_cache
import os
from gtts import gTTS
import base64
import requests
import plotly.graph_objects as go
from collections import defaultdict
import json
import re
import unicodedata
import uuid
import ollama
import pickle
from pathlib import Path
import shutil
from auth import init_auth, render_auth_page


st.set_page_config(
    page_title="Neo-Anki",
    page_icon="🌸",
    layout="wide",
    initial_sidebar_state="expanded",
)

class TimeTracker:
    def __init__(self):
        self.last_review_date = None
        self.study_sessions = []
        self.daily_stats = {}
        self.streak_count = 0
        self.last_active_date = None
        self._initialize_today()

    def _initialize_today(self):
        """Initialize stats for today"""
        today = datetime.now().date()
        if today not in self.daily_stats:
            self.daily_stats[today] = {
                'reviews': 0,
                'study_time': 0,
                'last_session_start': datetime.now()
            }

    def update_session(self):
        current_time = datetime.now()
        today = current_time.date()
        
        if today not in self.daily_stats:
            self.daily_stats[today] = {
                'reviews': 0,
                'study_time': 0,
                'last_session_start': current_time
            }
            
            if self.last_active_date:
                yesterday = today - timedelta(days=1)
                if self.last_active_date == yesterday:
                    self.streak_count += 1
                elif self.last_active_date != today:
                    self.streak_count = 1
            else:
                self.streak_count = 1
                
        self.last_active_date = today
    def log_review(self):
        current_time = datetime.now()
        today = current_time.date()
        self.update_session()  # Ensure today's stats exist
        self.daily_stats[today]['reviews'] += 1
        self.last_review_date = current_time

    def get_study_stats(self):
        current_time = datetime.now()
        stats = {
            'total_days': len(self.daily_stats),
            'current_streak': self.streak_count,
            'last_review': self.last_review_date,
            'today_reviews': self.daily_stats.get(current_time.date(), {}).get('reviews', 0),
            'total_reviews': sum(day['reviews'] for day in self.daily_stats.values()),
            'average_daily_reviews': 0
        }
        
        if stats['total_days'] > 0:
            stats['average_daily_reviews'] = stats['total_reviews'] / stats['total_days']
        
        return stats

class SessionStateManager:
    def __init__(self, storage_path="./data"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)
        self.state_file = self.storage_path / "session_state.pkl"
        self.backup_dir = self.storage_path / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        self.content_dir = self.storage_path / "content"
        self.content_dir.mkdir(exist_ok=True)

    def save_state(self, content_manager, review_system, time_tracker):
        """Save complete application state including content sources"""
        try:
            # Serialize datetime objects in daily_stats
            serialized_daily_stats = {}
            for date_key, stats in time_tracker.daily_stats.items():
                serialized_stats = stats.copy()
                serialized_stats['last_session_start'] = self._serialize_datetime(stats['last_session_start'])
                serialized_daily_stats[self._serialize_datetime(date_key)] = serialized_stats

            # Serialize active sources
            serialized_sources = {}
            for source_id, source in content_manager.active_sources.items():
                serialized_source = source.copy()
                serialized_source['created_date'] = self._serialize_datetime(source['created_date'])
                if source['progress'].get('last_processed'):
                    serialized_source['progress']['last_processed'] = self._serialize_datetime(
                        source['progress']['last_processed']
                    )
                # Don't serialize file_data in main state
                serialized_source['file_data'] = None
                serialized_sources[source_id] = serialized_source

            state_data = {
                'timestamp': datetime.now().isoformat(),
                'content_manager': {
                    'sentences': [
                        {**s, 'created': self._serialize_datetime(s['created'])}
                        for s in content_manager.sentences
                    ],
                    'sources': content_manager.sources,
                    'active_sources': serialized_sources
                },
                'review_system': {
                    'schedule': {
                        k: {
                            'next_review': self._serialize_datetime(v['next_review']),
                            'interval': v['interval'],
                            'last_response': v['last_response']
                        }
                        for k, v in review_system.schedule.items()
                    },
                    'history': [
                        {
                            'sentence_id': h['sentence_id'],
                            'response': h['response'],
                            'timestamp': self._serialize_datetime(h['timestamp'])
                        }
                        for h in review_system.history
                    ]
                },
                'time_tracker': {
                    'last_review_date': self._serialize_datetime(time_tracker.last_review_date),
                    'study_sessions': time_tracker.study_sessions,
                    'daily_stats': serialized_daily_stats,
                    'streak_count': time_tracker.streak_count,
                    'last_active_date': self._serialize_datetime(time_tracker.last_active_date)
                }
            }

            # Create backup first
            self._create_backup()

            # Save current state
            with open(self.state_file, 'wb') as f:
                pickle.dump(state_data, f)

            # Save content files separately
            self._save_content_files(content_manager.active_sources)

            self._cleanup_old_backups()
            return True, "State saved successfully"

        except Exception as e:
            return False, f"Error saving state: {str(e)}"

    def load_state(self):
        """Load complete application state including content sources"""
        try:
            if not self.state_file.exists():
                return False, "No saved state found", None

            with open(self.state_file, 'rb') as f:
                state_data = pickle.load(f)

            # Deserialize datetime objects in daily_stats
            if 'time_tracker' in state_data:
                deserialized_daily_stats = {}
                for date_str, stats in state_data['time_tracker']['daily_stats'].items():
                    deserialized_stats = stats.copy()
                    deserialized_stats['last_session_start'] = self._deserialize_datetime(
                        stats['last_session_start']
                    )
                    deserialized_daily_stats[datetime.fromisoformat(date_str).date()] = deserialized_stats
                state_data['time_tracker']['daily_stats'] = deserialized_daily_stats

                # Deserialize other datetime fields
                state_data['time_tracker']['last_review_date'] = self._deserialize_datetime(
                    state_data['time_tracker']['last_review_date']
                )
                state_data['time_tracker']['last_active_date'] = self._deserialize_datetime(
                    state_data['time_tracker']['last_active_date']
                )

            # Deserialize content manager dates and sources
            if 'content_manager' in state_data:
                # Deserialize sentences
                for sentence in state_data['content_manager']['sentences']:
                    sentence['created'] = self._deserialize_datetime(sentence['created'])

                # Deserialize active sources
                active_sources = state_data['content_manager']['active_sources']
                for source_id, source in active_sources.items():
                    source['created_date'] = self._deserialize_datetime(source['created_date'])
                    if source['progress'].get('last_processed'):
                        source['progress']['last_processed'] = self._deserialize_datetime(
                            source['progress']['last_processed']
                        )
                    # Load file data from content directory
                    source['file_data'] = self._load_source_file_data(source_id, source['type'])

            # Deserialize review system dates
            if 'review_system' in state_data:
                for schedule in state_data['review_system']['schedule'].values():
                    schedule['next_review'] = self._deserialize_datetime(schedule['next_review'])
                
                for history_item in state_data['review_system']['history']:
                    history_item['timestamp'] = self._deserialize_datetime(history_item['timestamp'])

            return True, "State loaded successfully", state_data

        except Exception as e:
            return False, f"Error loading state: {str(e)}", None

    def _create_backup(self):
        """Create a backup of the current state"""
        if self.state_file.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_dir / f"state_backup_{timestamp}.pkl"
            shutil.copy2(self.state_file, backup_file)
            
            # Backup content files
            content_backup_dir = self.backup_dir / f"content_backup_{timestamp}"
            if self.content_dir.exists():
                shutil.copytree(self.content_dir, content_backup_dir)

    def _save_content_files(self, active_sources):
        """Save content files separately from main state"""
        for source_id, source in active_sources.items():
            if source['file_data']:
                source_dir = self.content_dir / source['type'] / source_id
                source_dir.mkdir(parents=True, exist_ok=True)
                
                with open(source_dir / 'content.data', 'wb') as f:
                    f.write(source['file_data'])
                
                # Save metadata
                metadata = source.copy()
                metadata['file_data'] = None  # Don't include file data in metadata
                metadata['created_date'] = self._serialize_datetime(metadata['created_date'])
                if metadata['progress'].get('last_processed'):
                    metadata['progress']['last_processed'] = self._serialize_datetime(
                        metadata['progress']['last_processed']
                    )
                
                with open(source_dir / 'metadata.json', 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)

    def _load_source_file_data(self, source_id, source_type):
        """Load source file data from content directory"""
        try:
            source_dir = self.content_dir / source_type / source_id
            content_file = source_dir / 'content.data'
            
            if content_file.exists():
                with open(content_file, 'rb') as f:
                    return f.read()
            return None
        except Exception:
            return None

    def _cleanup_old_backups(self, keep_last_n=5):
        """Clean up old backup files"""
        # Clean up state backups
        backup_files = sorted(self.backup_dir.glob("state_backup_*.pkl"))
        if len(backup_files) > keep_last_n:
            for old_file in backup_files[:-keep_last_n]:
                old_file.unlink()

        # Clean up content backups
        content_backups = sorted(self.backup_dir.glob("content_backup_*"))
        if len(content_backups) > keep_last_n:
            for old_backup in content_backups[:-keep_last_n]:
                if old_backup.is_dir():
                    shutil.rmtree(old_backup)

    def create_backup(self):
        """Create a manual backup"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_dir / f"manual_backup_{timestamp}.zip"
            
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add state file
                if self.state_file.exists():
                    zipf.write(self.state_file, self.state_file.name)
                
                # Add content files
                if self.content_dir.exists():
                    for root, _, files in os.walk(self.content_dir):
                        for file in files:
                            file_path = Path(root) / file
                            arcname = file_path.relative_to(self.storage_path)
                            zipf.write(file_path, arcname)
            
            return backup_file
        except Exception as e:
            raise Exception(f"Backup creation failed: {str(e)}")

    def restore_from_backup(self, backup_file_path):
        """Restore system state from backup file"""
        try:
            with zipfile.ZipFile(backup_file_path, 'r') as zipf:
                # Clear existing data
                if self.state_file.exists():
                    self.state_file.unlink()
                if self.content_dir.exists():
                    shutil.rmtree(self.content_dir)
                
                # Extract all files
                zipf.extractall(self.storage_path)
            
            return True
        except Exception as e:
            return False

    def _serialize_datetime(self, obj):
        """Serialize datetime objects"""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return obj

    def _deserialize_datetime(self, obj):
        """Deserialize datetime strings"""
        if isinstance(obj, str):
            try:
                return datetime.fromisoformat(obj)
            except ValueError:
                return obj
        return obj

class ContentSource:
    def __init__(self, source_id, source_type, name, file_data=None):
        self.id = source_id
        self.type = source_type  # 'epub', 'text', 'url'
        self.name = name
        self.created_date = datetime.now()
        self.progress = {
            'total_units': 0,
            'processed_units': 0,
            'current_position': 0,
            'last_processed': None
        }
        self.file_data = file_data  # Store file data for EPUB


class ContentManager:
    def __init__(self):
        self.sentences = []
        self.sources = {}  # Traditional sources tracking
        self.active_sources = {}  # Active content sources with progress
        self.content_path = Path("./data/content")
        self.content_path.mkdir(parents=True, exist_ok=True)
        
    def add_source(self, source_type, name, content=None, file_data=None):
        """Add a new content source"""
        try:
            source_id = str(uuid.uuid4())
            source = {
                'id': source_id,
                'type': source_type,
                'name': name,
                'created_date': datetime.now(),
                'content': content,  # Store the content
                'progress': {
                    'total_units': 0,
                    'processed_units': 0,
                    'current_position': 0,
                    'last_processed': None
                },
                'file_data': file_data,
                'status': 'active'
            }
            
            # Create source directory
            source_dir = self.content_path / source_type / source_id
            source_dir.mkdir(parents=True, exist_ok=True)
            
            # Save source metadata and content
            self._save_source_files(source, source_dir)
            
            self.active_sources[source_id] = source
            return source_id, None
            
        except Exception as e:
            return None, f"Error adding source: {str(e)}"

    def process_source_content(self, source_id, batch_size=5):
        """Process content from a source in batches"""
        try:
            source = self.active_sources.get(source_id)
            if not source:
                return 0, 0, "Source not found"
            
            if source['type'] == 'epub':
                return self._process_epub_batch(source, batch_size)
            elif source['type'] == 'text':
                return self._process_text_content(source)
            elif source['type'] == 'url':
                return self._process_url_content(source)
            
            return 0, 0, "Unknown source type"
            
        except Exception as e:
            return 0, 0, f"Error processing content: {str(e)}"


    def _process_url_content(self, source):
        """Process URL content"""
        try:
            url = None
            content = None
            source_dir = self.content_path / 'url' / source['id']
            
            # Get URL from source
            if isinstance(source.get('content'), dict):
                url = source['content'].get('url')
                content = source['content'].get('html')
            
            if not url:
                return 0, 0, "URL not found"
                    
            if not content:
                # Fetch content if not provided
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(url, headers=headers, verify=True)
                response.raise_for_status()
                content = response.text
            
            # Extract text from HTML
            soup = BeautifulSoup(content, 'html.parser')
            for tag in soup(['style', 'script', 'nav', 'header', 'footer', 'iframe']):
                tag.decompose()
            
            text = ''
            article = soup.find('article') or soup.find('div', class_=['article', 'content', 'main-content'])
            if article:
                text = article.get_text()
            else:
                text = soup.get_text()
            
            # Clean up the text
            text = re.sub(r'\s+', ' ', text)
            text = unicodedata.normalize('NFKC', text)
            
            # Save processed content
            with open(source_dir / 'content.txt', 'w', encoding='utf-8') as f:
                f.write(text)
            
            added, duplicates = self.add_content(text, source['name'])
            
            source['progress']['processed_units'] = 1
            source['progress']['total_units'] = 1
            source['progress']['last_processed'] = datetime.now()
            
            # Update source metadata
            self._update_source_metadata(source)
            
            return added, duplicates, None
            
        except Exception as e:
            return 0, 0, f"Error processing URL: {str(e)}"

    def _save_source_files(self, source, source_dir):
        """Save source files (metadata and content)"""
        # Save metadata
        metadata = {k: v for k, v in source.items() if k not in ['file_data', 'content']}
        metadata['created_date'] = metadata['created_date'].isoformat()
        if metadata['progress'].get('last_processed'):
            metadata['progress']['last_processed'] = metadata['progress']['last_processed'].isoformat()
        
        with open(source_dir / 'metadata.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        # Save content based on type
        if source['type'] == 'text':
            if isinstance(source.get('content'), dict) and 'text' in source['content']:
                with open(source_dir / 'content.txt', 'w', encoding='utf-8') as f:
                    f.write(source['content']['text'])
        elif source['type'] == 'url':
            if isinstance(source.get('content'), dict):
                # Save URL
                if 'url' in source['content']:
                    with open(source_dir / 'url.txt', 'w', encoding='utf-8') as f:
                        f.write(source['content']['url'])
                # Save HTML content if available
                if 'html' in source['content']:
                    with open(source_dir / 'content.html', 'w', encoding='utf-8') as f:
                        f.write(source['content']['html'])
        elif source['file_data']:
            with open(source_dir / 'content.data', 'wb') as f:
                f.write(source['file_data'])

    def _process_text_content(self, source):
        """Process text content"""
        try:
            content = None
            source_dir = self.content_path / 'text' / source['id']
            
            # Try to get content from source object
            if isinstance(source.get('content'), dict) and 'text' in source['content']:
                content = source['content']['text']
            # Try to get content from file
            elif (source_dir / 'content.txt').exists():
                with open(source_dir / 'content.txt', 'r', encoding='utf-8') as f:
                    content = f.read()
            else:
                return 0, 0, "Text content not found"
            
            added, duplicates = self.add_content(content, source['name'])
            
            source['progress']['processed_units'] = 1
            source['progress']['total_units'] = 1
            source['progress']['last_processed'] = datetime.now()
            
            # Update source metadata
            self._update_source_metadata(source)
            
            return added, duplicates, None
            
        except Exception as e:
            return 0, 0, f"Error processing text: {str(e)}"

    def _process_url_content(self, source):
        """Process URL content"""
        try:
            url = None
            content = None
            source_dir = self.content_path / 'url' / source['id']
            
            # Get URL from source or file
            if isinstance(source.get('content'), dict) and 'url' in source['content']:
                url = source['content']['url']
            elif (source_dir / 'url.txt').exists():
                with open(source_dir / 'url.txt', 'r', encoding='utf-8') as f:
                    url = f.read().strip()
                    
            if not url:
                return 0, 0, "URL not found"
            
            # Try to get content from cache first
            if isinstance(source.get('content'), dict) and 'html' in source['content']:
                content = source['content']['html']
            elif (source_dir / 'content.html').exists():
                with open(source_dir / 'content.html', 'r', encoding='utf-8') as f:
                    content = f.read()
            else:
                # Fetch content if not cached
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(url, headers=headers, verify=True)
                response.raise_for_status()
                content = response.text
                
                # Cache the content
                with open(source_dir / 'content.html', 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Extract text from HTML
            soup = BeautifulSoup(content, 'html.parser')
            for tag in soup(['style', 'script', 'nav', 'header', 'footer', 'iframe']):
                tag.decompose()
            
            text = ''
            article = soup.find('article') or soup.find('div', class_=['article', 'content', 'main-content'])
            if article:
                text = article.get_text()
            else:
                text = soup.get_text()
            
            # Clean up the text
            text = re.sub(r'\s+', ' ', text)
            text = unicodedata.normalize('NFKC', text)
            
            added, duplicates = self.add_content(text, source['name'])
            
            source['progress']['processed_units'] = 1
            source['progress']['total_units'] = 1
            source['progress']['last_processed'] = datetime.now()
            
            # Update source metadata
            self._update_source_metadata(source)
            
            return added, duplicates, None
            
        except Exception as e:
            return 0, 0, f"Error processing URL: {str(e)}"

    def _process_epub_batch(self, source, batch_size=5):
        """Process EPUB content in batches"""
        try:
            if not source['file_data']:
                return 0, 0, "No EPUB data found"
                
            # Create temporary file to process EPUB
            with tempfile.NamedTemporaryFile(delete=False, suffix='.epub') as tmp_file:
                tmp_file.write(source['file_data'])
                tmp_file_path = tmp_file.name

            # Read EPUB
            book = epub.read_epub(tmp_file_path, options={'ignore_ncx': True})
            items = [item for item in book.get_items() 
                    if item.get_type() == ebooklib.ITEM_DOCUMENT]
            
            # Calculate batch range
            start_pos = source['progress']['current_position']
            end_pos = min(start_pos + batch_size, len(items))
            
            # Process batch
            total_added = 0
            total_duplicates = 0
            
            for i in range(start_pos, end_pos):
                item = items[i]
                content = item.get_content().decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content, 'html.parser')
                
                # Clean up HTML
                for tag in soup(['style', 'script', 'nav', 'header', 'footer']):
                    tag.decompose()
                
                # Extract text
                text = ""
                paragraphs = soup.find_all('p')
                if paragraphs:
                    text += "\n".join(p.get_text().strip() for p in paragraphs)
                
                if not text:
                    content_divs = soup.find_all('div', class_=['text', 'content', 'body'])
                    text += "\n".join(div.get_text().strip() for div in content_divs)
                
                if not text:
                    text = soup.get_text().strip()
                
                # Process if Japanese text found
                if text and any(ord(c) > 0x3000 for c in text):
                    text = re.sub(r'\s+', ' ', text)
                    text = unicodedata.normalize('NFKC', text)
                    added, duplicates = self.add_content(text, source['name'])
                    total_added += added
                    total_duplicates += duplicates
            
            # Update progress
            source['progress']['current_position'] = end_pos
            source['progress']['processed_units'] = end_pos
            source['progress']['total_units'] = len(items)
            source['progress']['last_processed'] = datetime.now()
            
            # Clean up
            os.unlink(tmp_file_path)
            
            # Update metadata
            self._update_source_metadata(source)
            
            return total_added, total_duplicates, None
            
        except Exception as e:
            return 0, 0, f"Error processing EPUB batch: {str(e)}"


    def _extract_text_from_soup(self, soup):
        """Extract text content from BeautifulSoup object"""
        # Remove unwanted elements
        for tag in soup(['style', 'script', 'nav', 'header', 'footer']):
            tag.decompose()
        
        text = ""
        # Try paragraphs first
        paragraphs = soup.find_all('p')
        if paragraphs:
            text += "\n".join(p.get_text().strip() for p in paragraphs)
        
        # If no paragraphs, try content divs
        if not text:
            content_divs = soup.find_all('div', class_=['text', 'content', 'body'])
            text += "\n".join(div.get_text().strip() for div in content_divs)
        
        # If still no text, get all text
        if not text:
            text = soup.get_text().strip()
        
        # Clean up the text
        text = re.sub(r'\s+', ' ', text)
        text = unicodedata.normalize('NFKC', text)
        
        return text.strip()

    def _update_source_metadata(self, source):
        """Update source metadata file"""
        source_dir = self.content_path / source['type'] / source['id']
        with open(source_dir / 'metadata.json', 'w', encoding='utf-8') as f:
            metadata = {k: v for k, v in source.items() if k != 'file_data'}
            metadata['created_date'] = metadata['created_date'].isoformat()
            if 'last_processed' in metadata['progress']:
                metadata['progress']['last_processed'] = metadata['progress']['last_processed'].isoformat()
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def remove_source(self, source_id):
        """Remove a content source"""
        try:
            if source_id in self.active_sources:
                source = self.active_sources[source_id]
                source_dir = self.content_path / source['type'] / source_id
                
                # Remove source directory
                if source_dir.exists():
                    shutil.rmtree(source_dir)
                
                # Remove from active sources
                del self.active_sources[source_id]
                
                return True, None
            return False, "Source not found"
            
        except Exception as e:
            return False, f"Error removing source: {str(e)}"

    def get_source_progress(self, source_id):
        """Get progress information for a source"""
        source = self.active_sources.get(source_id)
        if not source:
            return None
        
        progress = source['progress']
        return {
            'name': source['name'],
            'type': source['type'],
            'processed': progress['processed_units'],
            'total': progress['total_units'],
            'percentage': (progress['processed_units'] / progress['total_units'] * 100) 
                        if progress['total_units'] > 0 else 0,
            'last_processed': progress['last_processed']
        }
        
    def add_content(self, text, source_name=None):
        new_sentences = self.split_into_sentences(text)
        added_count = 0
        duplicate_count = 0
        
        for sentence in new_sentences:
            if not self.is_duplicate(sentence['text']):
                sentence['source'] = source_name
                self.sentences.append(sentence)
                added_count += 1
            else:
                duplicate_count += 1
                
        if source_name and added_count > 0:
            self.sources[source_name] = {
                'added_date': datetime.now(),
                'sentence_count': added_count
            }
            
        return added_count, duplicate_count
    
    def is_duplicate(self, text):
        # Normalize text for comparison
        normalized_text = unicodedata.normalize('NFKC', text.strip())
        return any(
            unicodedata.normalize('NFKC', s['text'].strip()) == normalized_text 
            for s in self.sentences
        )
    
    def split_into_sentences(self, text):
        sentences = []
        segments = re.split(r'([。！？])', text)
        
        for i in range(0, len(segments)-1, 2):
            if segments[i]:
                current = (segments[i] + (segments[i+1] if i+1 < len(segments) else '')).strip()
                if self.is_valid_sentence(current):
                    sentences.append({
                        'id': str(uuid.uuid4()),
                        'text': current,
                        'created': datetime.now(),
                        'difficulty': self.calculate_difficulty(current),
                        'reviews': 0,
                        'next_review': None,
                        'status': 'new'
                    })
        
        return sentences
    
    def is_valid_sentence(self, text):
        return (
            text and
            5 <= len(text) <= 200 and
            any(ord(c) > 0x3000 for c in text)
        )
    
    def calculate_difficulty(self, text):
        kanji_count = len([c for c in text if 0x4E00 <= ord(c) <= 0x9FFF])
        length = len(text)
        
        if length == 0:
            return 1.0
        
        kanji_score = min(5, (kanji_count / length) * 10)
        length_score = min(5, length / 40)
        
        return round((kanji_score + length_score) / 2, 1)
    
    def get_sentence_by_id(self, sentence_id):
        for sentence in self.sentences:
            if sentence['id'] == sentence_id:
                return sentence
        return None

class ReviewSystem:
    def __init__(self):
        self.schedule = {}
        self.history = []
    
    def process_response(self, sentence_id, response):
        interval = self.calculate_next_interval(sentence_id, response)
        
        self.schedule[sentence_id] = {
            'next_review': datetime.now() + timedelta(days=interval),
            'interval': interval,
            'last_response': response
        }
        
        self.history.append({
            'sentence_id': sentence_id,
            'response': response,
            'timestamp': datetime.now()
        })
        
        sentence = st.session_state.content_manager.get_sentence_by_id(sentence_id)
        if sentence:
            sentence['status'] = 'reviewed'
    
    def calculate_next_interval(self, sentence_id, response):
        current = self.schedule.get(sentence_id, {}).get('interval', 0)
        
        if response == 'hard':
            return max(1, current * 1.2)
        elif response == 'good':
            return current * 2.5 if current else 1
        else:  # easy
            return current * 3.5 if current else 2
    
    def get_due_reviews(self):
        now = datetime.now()
        return [
            sentence_id for sentence_id, schedule in self.schedule.items()
            if schedule['next_review'] <= now
        ]
    
    def get_next_review_date(self, sentence_id):
        if sentence_id in self.schedule:
            return self.schedule[sentence_id]['next_review']
        return None

def get_grammar_analysis(text):
    """Separate AI call for grammar analysis"""
    prompt = f"""
    Analyze the grammar points in this Japanese text:
    {text}

    Provide:
    1. Main grammar patterns used
    2. JLPT level of each pattern
    3. Example sentences using same patterns
    """
    try:
        response = ollama.generate(
            model='llama3.2:1b',
            prompt=prompt
        )
        return response['response']
    except Exception as e:
        return "Grammar analysis failed"

def get_vocabulary_analysis(text):
    """Separate AI call for vocabulary analysis"""
    prompt = f"""
    Analyze the vocabulary in this Japanese text:
    {text}

    Provide:
    1. Key vocabulary with readings
    2. JLPT level of each word
    3. Common collocations
    4. Related vocabulary
    """
    try:
        response = ollama.generate(
            model='llama3.2:1b',
            prompt=prompt
        )
        return response['response']
    except Exception as e:
        return "Vocabulary analysis failed"
    
def init_streamlit():
    # Add to your existing styles
    st.markdown("""
        <style>
        /* Existing styles ... */
        
        /* Source management styles */
        .source-card {
            background-color: #f8f9fa;
            border-radius: 8px;
            padding: 15px;
            margin: 10px 0;
            border: 1px solid #dee2e6;
        }
        
        .source-card:hover {
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .source-progress {
            margin: 10px 0;
        }
        
        .source-stats {
            color: #6c757d;
            font-size: 0.9em;
        }
        
        .source-actions {
            display: flex;
            gap: 10px;
            margin-top: 10px;
        }
        
        /* Dark mode adjustments */
        .dark-mode .source-card {
            background-color: #2d2d2d;
            border-color: #404040;
        }
        
        .dark-mode .source-stats {
            color: #a0a0a0;
        }
        </style>
    """, unsafe_allow_html=True)

    # Initialize session state
    if 'initialized' not in st.session_state:
        state_manager = SessionStateManager()
        success, message, state_data = state_manager.load_state()

        if success:
            st.session_state.content_manager = ContentManager()
            st.session_state.content_manager.sentences = state_data['content_manager']['sentences']
            
            st.session_state.review_system = ReviewSystem()
            st.session_state.review_system.schedule = state_data['review_system']['schedule']
            st.session_state.review_system.history = state_data['review_system']['history']
            
            st.session_state.time_tracker = TimeTracker()
            if 'time_tracker' in state_data:
                st.session_state.time_tracker.last_review_date = state_data['time_tracker']['last_review_date']
                st.session_state.time_tracker.study_sessions = state_data['time_tracker']['study_sessions']
                st.session_state.time_tracker.daily_stats = state_data['time_tracker']['daily_stats']
                st.session_state.time_tracker.streak_count = state_data['time_tracker']['streak_count']
                st.session_state.time_tracker.last_active_date = state_data['time_tracker']['last_active_date']
            
            st.success("Previous session restored!")
        else:
            st.session_state.content_manager = ContentManager()
            st.session_state.review_system = ReviewSystem()
            st.session_state.time_tracker = TimeTracker()
            if message != "No saved state found":
                st.warning(f"Started new session: {message}")

        st.session_state.initialized = True
        st.session_state.state_manager = state_manager
        st.session_state.last_save = datetime.now()
        st.session_state.dark_mode = False

    # Add analysis components styling
    render_analysis_components()
    if 'analysis_language' not in st.session_state:
        st.session_state.analysis_language = 'english'

def render_card(sentence, review_system):
    # Keys for analysis visibility and cached content
    analysis_key = f"show_analysis_{sentence['id']}"
    cache_key = f"analysis_cache_{sentence['id']}"
    
    # Initialize session state keys if they don't exist
    if analysis_key not in st.session_state:
        st.session_state[analysis_key] = False
    if cache_key not in st.session_state:
        st.session_state[cache_key] = None

    with st.container():
        # Main card container
        st.markdown(f"""
        <div class="card-container">
            <div class="main-card" style="background-color: {'#fff0f0' if sentence['status'] == 'new' else '#f0fff0'}">
                <div class="text">{sentence['text']}</div>
                <div class="stats">
                    <span>Difficulty: {sentence['difficulty']:.1f}</span>
                    <span>Reviews: {sentence['reviews']}</span>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Button row
        col1, col2, col3, col4, col5 = st.columns([1,1,1,1,1])
        
        with col1:
            if st.button("Hard", key=f"hard_{sentence['id']}"):
                review_system.process_response(sentence['id'], 'hard')
                sentence['reviews'] += 1
                st.session_state.time_tracker.log_review()
                st.rerun()
        
        with col2:
            if st.button("Good", key=f"good_{sentence['id']}"):
                review_system.process_response(sentence['id'], 'good')
                sentence['reviews'] += 1
                st.session_state.time_tracker.log_review()
                st.rerun()
        
        with col3:
            if st.button("Easy", key=f"easy_{sentence['id']}"):
                review_system.process_response(sentence['id'], 'easy')
                sentence['reviews'] += 1
                st.session_state.time_tracker.log_review()
                st.rerun()
        
        with col4:
            if st.button("Audio", key=f"audio_{sentence['id']}"):
                audio_html = text_to_speech(sentence['text'])
                if audio_html:
                    st.markdown(audio_html, unsafe_allow_html=True)
        
        with col5:
            # Toggle button with arrow indicators
            if st.session_state[analysis_key]:
                if st.button("↑ Hide Study", key=f"study_{sentence['id']}", type="secondary"):
                    st.session_state[analysis_key] = False
                    st.rerun()
            else:
                if st.button("↓ Study", key=f"study_{sentence['id']}", type="primary"):
                    st.session_state[analysis_key] = True
                    st.rerun()
        
        # Analysis section appears directly under the card
        if st.session_state[analysis_key]:
            # Language toggle
            col1, col2 = st.columns([3, 1])
            with col2:
                selected_language = st.selectbox(
                    "Analysis Language",
                    ["English", "Japanese"],
                    key=f"lang_{sentence['id']}",
                    index=0 if st.session_state.analysis_language == 'english' else 1,
                )
                # Update global language preference
                st.session_state.analysis_language = 'english' if selected_language == "English" else 'japanese'

            # Create tabs including Word Breakdown
            tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Word Breakdown", "Grammar", "Practice"])
            
            # Generate content only if not cached or language changed
            cache_key = f"analysis_cache_{sentence['id']}_{st.session_state.analysis_language}"
            if cache_key not in st.session_state:
                with st.spinner("Loading analysis..."):
                    is_japanese = st.session_state.analysis_language == 'japanese'
                    st.session_state[cache_key] = {
                        'translation': get_translation(sentence['text']) if not is_japanese else get_japanese_translation(sentence['text']),
                        'key_points': get_key_points(sentence['text']) if not is_japanese else get_japanese_key_points(sentence['text']),
                        'word_breakdown': get_word_breakdown(sentence['text']),  # Always show word breakdown
                        'grammar': get_grammar_analysis(sentence['text']) if not is_japanese else get_japanese_grammar_analysis(sentence['text']),
                        'examples': get_practice_examples(sentence['text']) if not is_japanese else get_japanese_practice_examples(sentence['text'])
                    }
            
            # Use cached content
            cached = st.session_state[cache_key]
            
            # Overview tab
            with tab1:
                col1, col2 = st.columns(2)
                with col1:
                    render_analysis_card("Translation", cached['translation'])
                with col2:
                    render_analysis_card("Key Points", cached['key_points'])
            
            # Word Breakdown tab
            with tab2:
                st.markdown("""
                    <style>
                    .word-breakdown-table {
                        font-size: 16px;
                        margin-top: 10px;
                    }
                    .word-breakdown-table th {
                        background-color: #f0f2f6;
                        padding: 8px;
                    }
                    .word-breakdown-table td {
                        padding: 8px;
                    }
                    </style>
                """, unsafe_allow_html=True)
                render_analysis_card("Word-by-Word Analysis", cached['word_breakdown'])
            
            # Grammar tab
            with tab3:
                render_analysis_card("Grammar Analysis", cached['grammar'])
            
            # Practice tab
            with tab4:
                render_analysis_card("Practice Examples", cached['examples'])

            # Add a divider after the analysis section
            st.markdown("<hr style='margin: 20px 0;'>", unsafe_allow_html=True)


def process_epub_content(uploaded_file):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.epub') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        st.write("Starting EPUB processing...")
        
        book = epub.read_epub(tmp_file_path, options={'ignore_ncx': True})
        items = [item for item in book.get_items() 
                if item.get_type() == ebooklib.ITEM_DOCUMENT]
        
        st.write(f"Found {len(items)} chapters to process")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        text_content = []
        
        start_idx = 0 if 'processed_chapters' not in st.session_state else st.session_state.processed_chapters
        end_idx = min(start_idx + 5, len(items))  # Process 5 chapters at a time
        
        for i, item in enumerate(items[start_idx:end_idx], start=start_idx):
            status_text.text(f"Processing chapter {i+1}/{len(items)}")
            
            try:
                content = item.get_content().decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content, 'html.parser')
                
                for tag in soup(['style', 'script', 'nav', 'header', 'footer']):
                    tag.decompose()
                
                text = ""
                paragraphs = soup.find_all('p')
                if paragraphs:
                    text += "\n".join(p.get_text().strip() for p in paragraphs)
                
                if not text:
                    content_divs = soup.find_all('div', class_=['text', 'content', 'body'])
                    text += "\n".join(div.get_text().strip() for div in content_divs)
                
                if not text:
                    text = soup.get_text().strip()
                
                if text and any(ord(c) > 0x3000 for c in text):
                    text = re.sub(r'\s+', ' ', text)
                    text = unicodedata.normalize('NFKC', text)
                    text_content.append(text)
                    st.write(f"✓ Chapter {i+1}: Found {len(text)} characters")
                
            except Exception as e:
                st.warning(f"Error in chapter {i+1}: {str(e)}")
            
            progress_bar.progress((i + 1 - start_idx) / (end_idx - start_idx))
        
        progress_bar.empty()
        status_text.empty()
        os.unlink(tmp_file_path)
        
        if not text_content:
            st.warning("No Japanese text found in these chapters.")
            return None, False
        
        combined_text = ' '.join(text_content)
        st.success(f"Successfully extracted {len(combined_text)} characters")
        
        st.session_state.processed_chapters = end_idx
        
        has_more = end_idx < len(items)
        return combined_text, has_more
        
    except Exception as e:
        st.error(f"EPUB processing error: {str(e)}")
        return None, False

def get_ai_analysis(text):
    prompt = f"""
    As a Japanese language learning assistant, analyze this text:
    {text}

    Provide a clear, structured response with:
    1. 🔄 English translation
    2. 📚 Word breakdown (with readings and meanings)
    3. 📝 Grammar point explanation
    4. 💡 Similar example sentences

    Format the response in a clean, easy-to-read way using markdown.
    """
    
    try:
        response = ollama.generate(
            model='llama3.2:1b',
            prompt=prompt
        )
        return response['response']
    except Exception as e:
        return f"Analysis error: {str(e)}"





def render_analysis_components():
    st.markdown("""
        <style>
        .card-container {
            margin-bottom: 10px;
        }
        
        .main-card {
            padding: 20px;
            border-radius: 10px 10px 0 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .analysis-section {
            background: white;
            border-radius: 0 0 10px 10px;
            margin-top: -10px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .analysis-tabs {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
        }
        
        .tab {
            cursor: pointer;
            padding: 8px 16px;
            border-radius: 20px;
            background: #f8f9fa;
        }
        
        .tab.active {
            background: #e9ecef;
            font-weight: bold;
        }
        
        .analysis-card {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 15px;
            margin: 10px 0;
            transition: all 0.3s ease;
        }
        
        .analysis-header {
            font-weight: bold;
            margin-bottom: 10px;
            color: #333;
        }
        
        .analysis-content {
            font-size: 16px;
            line-height: 1.6;
        }

        /* Loading spinner styles */
        .stSpinner {
            text-align: center;
            margin: 20px 0;
        }
        
        /* Smooth transition for analysis section */
        .analysis-section {
            transition: all 0.3s ease;
        }
        </style>
    """, unsafe_allow_html=True)

def render_analysis_card(title, content):
    st.markdown(f"""
        <div class="analysis-card">
            <div class="analysis-header">{title}</div>
            <div class="analysis-content">{content}</div>
        </div>
    """, unsafe_allow_html=True)

@st.cache_data(ttl=3600)
def get_translation(text):
    prompt = f"Translate this Japanese text to natural English: {text}"
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "Translation unavailable"

@st.cache_data(ttl=3600)
def get_key_points(text):
    prompt = f"Identify key learning points in this Japanese text: {text}"
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "Key points unavailable"

@st.cache_data(ttl=3600)
def get_word_breakdown(text):
    prompt = f"""
    Analyze each word in this Japanese text:
    {text}

    For each word provide:
    1. Word in kanji/kana (if applicable)
    2. Reading (furigana)
    3. Part of speech
    4. Basic meaning
    5. JLPT level (if applicable)

    Format as a table with markdown:
    | Word | Reading | Part of Speech | Meaning | Level |
    |------|---------|----------------|---------|--------|
    | 言葉 | ことば | Noun | word, language | N5 |
    """
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "Word breakdown unavailable"

@st.cache_data(ttl=3600)
def get_grammar_analysis(text):
    prompt = f"""
    Analyze the grammar points in this Japanese text:
    {text}

    Provide:
    1. Main grammar patterns used
    2. JLPT level of each pattern
    3. Example sentences using same patterns
    """
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "Grammar analysis unavailable"

@st.cache_data(ttl=3600)
def get_practice_examples(text):
    prompt = f"Generate similar example sentences based on this Japanese text: {text}"
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "Examples unavailable"

@st.cache_data(ttl=3600)
def get_japanese_translation(text):
    prompt = f"""
    この日本語の文章を分かりやすく言い換えてください：
    {text}

    以下の形式で：
    1. 原文
    2. 言い換え
    3. 補足説明
    """
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "翻訳できませんでした"

@st.cache_data(ttl=3600)
def get_japanese_key_points(text):
    prompt = f"""
    この文章の重要ポイントを日本語で説明してください：
    {text}

    以下の項目について：
    1. 主要な文法点
    2. 重要な表現
    3. 文脈・意図
    """
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "分析できませんでした"

@st.cache_data(ttl=3600)
def get_japanese_grammar_analysis(text):
    prompt = f"""
    この文章の文法を詳しく解説してください：
    {text}

    以下の項目を含めて：
    1. 文法構造の説明
    2. 使用されている文型
    3. 助詞の使い方
    4. 類似表現との比較
    """
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "文法解析できませんでした"

@st.cache_data(ttl=3600)
def get_japanese_practice_examples(text):
    prompt = f"""
    この文章で使われている表現を使った例文を作成してください：
    {text}

    以下を含めて：
    1. 類似の例文3つ
    2. 使い方の説明
    3. 注意点
    """
    try:
        response = ollama.generate(model='llama3.2:1b', prompt=prompt)
        return response['response']
    except Exception as e:
        return "例文を生成できませんでした"

def text_to_speech(text):
    try:
        with st.spinner('Generating audio...'):
            # Extract only Japanese text (remove English and other non-Japanese content)
            japanese_text = re.sub(r'[a-zA-Z():].*?[。]', '', text)  # Remove English sections
            japanese_text = re.sub(r'\s+', ' ', japanese_text).strip()  # Clean up whitespace
            
            # Check if we have valid Japanese text
            if not japanese_text or not any(ord(c) > 0x3000 for c in japanese_text):
                st.warning("No valid Japanese text found for audio generation")
                return None

            # Generate audio for Japanese text
            tts = gTTS(text=japanese_text, lang='ja')
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as fp:
                tts.save(fp.name)
                with open(fp.name, 'rb') as audio_file:
                    audio_base64 = base64.b64encode(audio_file.read()).decode()
                os.unlink(fp.name)
            
            # Display the text being read
            st.markdown("**Text being read:**")
            st.markdown(f"```{japanese_text}```")
            
            return f'''
                <audio controls style="width: 100%">
                    <source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3">
                </audio>
                '''
    except Exception as e:
        st.warning(f"Audio generation failed: {str(e)}")
        return None

def render_feed(content_manager, review_system):
    if 'epub_state' in st.session_state and st.session_state.get('processed_chapters', 0) > 0:
        total_items = len([item for item in content_manager.sentences if item['status'] == 'new'])
        processed = len([item for item in content_manager.sentences if item['status'] == 'reviewed'])
        
        st.markdown(f"""
        <div class="load-more-container">
            <div class="progress-info">
                Processed: {processed} cards • New: {total_items} cards
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    if not content_manager.sentences:
        st.info("Start by adding content from the sidebar")
        return
    
    due_reviews = review_system.get_due_reviews()
    new_cards = [s for s in content_manager.sentences 
                 if s['id'] not in review_system.schedule][:5]
    
    for sentence_id in due_reviews:
        sentence = content_manager.get_sentence_by_id(sentence_id)
        if sentence:
            render_card(sentence, review_system)
    
    for sentence in new_cards:
        if sentence['id'] not in due_reviews:
            render_card(sentence, review_system)

def render_schedule(content_manager, review_system):
    if not content_manager.sentences:
        st.info("No cards yet. Add content to get started!")
        return
    
    today = datetime.now().date()
    scheduled_cards = defaultdict(list)
    
    for sentence in content_manager.sentences:
        if sentence['id'] in review_system.schedule:
            next_review = review_system.schedule[sentence['id']]['next_review'].date()
            days_until = (next_review - today).days
            
            if days_until < 0:
                scheduled_cards["Due Now"].append(sentence)
            else:
                scheduled_cards[f"In {days_until} days"].append(sentence)
    
    new_cards = [s for s in content_manager.sentences 
                 if s['id'] not in review_system.schedule]
    if new_cards:
        scheduled_cards["New Cards"] = new_cards
    
    for schedule, cards in sorted(scheduled_cards.items()):
        with st.expander(f"{schedule} ({len(cards)} cards)"):
            for card in cards:
                interval = review_system.schedule.get(card['id'], {}).get('interval', 0)
                card_color = "#fff0f0" if card['status'] == 'new' else "#f0fff0"
                st.markdown(f"""
                <div class="schedule-card" style="background-color: {card_color}">
                    <div class="text">{card['text']}</div>
                    <div class="stats">
                        Reviews: {card['reviews']} • 
                        Interval: {interval:.1f} days • 
                        Difficulty: {card['difficulty']:.1f}
                    </div>
                </div>
                """, unsafe_allow_html=True)

def render_stats(content_manager, review_system, time_tracker):
    stats = time_tracker.get_study_stats()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Study Streak", f"{stats['current_streak']} days")
    with col2:
        st.metric("Total Study Days", stats['total_days'])
    with col3:
        st.metric("Today's Reviews", stats['today_reviews'])
    with col4:
        st.metric("Average Daily Reviews", f"{stats['average_daily_reviews']:.1f}")

    # Review history chart
    reviews_by_day = defaultdict(int)
    for review in review_system.history:
        date = review['timestamp'].date()
        reviews_by_day[date] += 1

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(reviews_by_day.keys()),
        y=list(reviews_by_day.values()),
        mode='lines+markers',
        name='Reviews'
    ))
    fig.update_layout(
        title='Review History',
        xaxis_title='Date',
        yaxis_title='Number of Reviews'
    )
    st.plotly_chart(fig)

    # Add source statistics
    st.markdown("### Content Source Statistics")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        total_sources = len(content_manager.active_sources)
        st.metric("Total Sources", total_sources)
    
    with col2:
        total_chapters = sum(s['progress']['total_units'] for s in content_manager.active_sources.values())
        processed_chapters = sum(s['progress']['processed_units'] for s in content_manager.active_sources.values())
        st.metric("Processed Chapters", f"{processed_chapters}/{total_chapters}")
    
    with col3:
        epub_sources = len([s for s in content_manager.active_sources.values() if s['type'] == 'epub'])
        st.metric("Active Books", epub_sources)
    
    with col4:
        other_sources = len([s for s in content_manager.active_sources.values() if s['type'] != 'epub'])
        st.metric("Other Sources", other_sources)   

def get_tutor_context(content_manager, review_system):
    """Get context about user's learning progress for the tutor"""
    total_cards = len(content_manager.sentences)
    reviewed_cards = len([s for s in content_manager.sentences if s['status'] == 'reviewed'])
    avg_difficulty = sum(s['difficulty'] for s in content_manager.sentences) / total_cards if total_cards > 0 else 0
    
    recent_sentences = [s['text'] for s in content_manager.sentences if s['status'] == 'reviewed'][-5:]
    
    context = f"""
    Student Profile:
    - Total cards: {total_cards}
    - Reviewed cards: {reviewed_cards}
    - Average difficulty level: {avg_difficulty:.1f}
    - Recent studied sentences: {', '.join(recent_sentences)}
    """
    return context

def chat_with_tutor(message, content_manager, review_system):
    """Generate tutor response based on context and message"""
    context = get_tutor_context(content_manager, review_system)
    
    prompt = f"""
    You are a friendly and knowledgeable Japanese language tutor. Use this context about the student:
    {context}

    Based on their learning progress and recent sentences, provide helpful, personalized responses.
    
    Student's message: {message}
    
    Respond in a supportive and educational way, incorporating relevant Japanese examples when appropriate.
    """
    
    try:
        response = ollama.generate(
            model='llama3.2:1b',
            prompt=prompt
        )
        return response['response']
    except Exception as e:
        return f"Tutor response error: {str(e)}"

def render_chat_interface(content_manager, review_system):
    """Render the chat interface with the tutor"""
    st.markdown("""
        <div style="padding: 20px; background-color: #f8f9fa; border-radius: 10px; margin-bottom: 20px;">
            <h3>👋 Chat with Your Japanese Tutor</h3>
            <p>Ask questions about your study materials, get explanations, or practice conversation!</p>
        </div>
    """, unsafe_allow_html=True)
    
    # Initialize chat history in session state if it doesn't exist
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    
    # Display chat history
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Chat input
    if prompt := st.chat_input("Type your message here..."):
        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Add user message to chat history
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        
        # Get and display tutor response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = chat_with_tutor(prompt, content_manager, review_system)
                st.markdown(response)
        
        # Add tutor response to chat history
        st.session_state.chat_history.append({"role": "assistant", "content": response})

    # Optional: Add clear chat history button
    if st.session_state.chat_history and st.button("Clear Chat History"):
        st.session_state.chat_history = []
        st.rerun()

def export_progress(content_manager, review_system, time_tracker):
    """Export study progress to JSON file"""
    try:
        export_data = {
            'metadata': {
                'export_date': datetime.now().isoformat(),
                'total_sentences': len(content_manager.sentences),
                'total_reviews': len(review_system.history)
            },
            'sentences': [
                {
                    'text': s['text'],
                    'created': s['created'].isoformat(),
                    'difficulty': s['difficulty'],
                    'reviews': s['reviews'],
                    'status': s['status']
                }
                for s in content_manager.sentences
            ],
            'review_history': [
                {
                    'sentence_text': content_manager.get_sentence_by_id(h['sentence_id'])['text'],
                    'response': h['response'],
                    'timestamp': h['timestamp'].isoformat()
                }
                for h in review_system.history
            ],
            'stats': {
                'streak': time_tracker.streak_count,
                'total_study_days': len(time_tracker.daily_stats),
                'total_reviews': sum(day['reviews'] for day in time_tracker.daily_stats.values())
            }
        }

        # Convert to JSON string
        json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
        
        return True, json_str.encode('utf-8')
    except Exception as e:
        return False, str(e)
    

def backup_system_state(state_manager):
    """Create downloadable backup of entire system state"""
    try:
        backup_file = state_manager.create_backup()
        with open(backup_file, 'rb') as f:
            return f.read()
    except Exception as e:
        st.error(f"Backup creation failed: {str(e)}")
        return None

def restore_system_state(uploaded_file, state_manager):
    """Restore system state from backup file"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pkl') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            success = state_manager.restore_from_backup(tmp_file.name)
            os.unlink(tmp_file.name)
            return success
    except Exception as e:
        return False, f"Restore failed: {str(e)}"

def render_settings_section():
    """Render the settings section in the sidebar"""
    with st.expander("⚙️ Settings"):
        st.markdown("### Backup & Restore")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Download Backup"):
                backup_data = backup_system_state(st.session_state.state_manager)
                if backup_data:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    st.download_button(
                        label="Save Backup File",
                        data=backup_data,
                        file_name=f"neoanki_backup_{timestamp}.pkl",
                        mime="application/octet-stream"
                    )
        
        with col2:
            if st.button("📤 Export", help="Export your data"):
                with st.spinner("Exporting..."):
                    success, result = export_progress(
                        st.session_state.content_manager,
                        st.session_state.review_system,
                        st.session_state.time_tracker
                    )
                    if success:
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        st.download_button(
                            label="📥 Download Export",
                            data=result,
                            file_name=f"neoanki_export_{timestamp}.json",
                            mime="application/json"
                        )
                    else:
                        st.error(f"Export failed: {result}")
        
        st.markdown("### Content Sources")
        if st.session_state.content_manager.sources:
            for source, info in st.session_state.content_manager.sources.items():
                st.markdown(f"""
                    **{source}**  
                    Added: {info['added_date'].strftime('%Y-%m-%d')}  
                    Sentences: {info['sentence_count']}
                """)
        else:
            st.info("No content sources added yet")

def show_confirmation_dialog(message):
    return st.warning(message, icon="⚠️")

def show_loading_state():
    return st.spinner()

def render_stats_summary():
    if st.session_state.content_manager.sentences:
        total_cards = len(st.session_state.content_manager.sentences)
        reviewed_cards = len([s for s in st.session_state.content_manager.sentences if s['status'] == 'reviewed'])
        due_cards = len(st.session_state.review_system.get_due_reviews())
        
        st.markdown("""
            <div style='padding: 1rem; background-color: #f8f9fa; border-radius: 10px; margin-bottom: 1rem;'>
        """, unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📚 Total Cards", total_cards)
        with col2:
            st.metric("✅ Reviewed", reviewed_cards)
        with col3:
            st.metric("⏰ Due Now", due_cards)
            
        # Progress bar
        progress = reviewed_cards / total_cards if total_cards > 0 else 0
        st.progress(progress, text=f"Overall Progress: {progress*100:.1f}%")
        
        st.markdown("</div>", unsafe_allow_html=True)

def render_content_input():
    st.markdown("## Content Sources")
    
    # Initialize source states if not exists
    if 'source_states' not in st.session_state:
        st.session_state.source_states = {}
    
    # Group sources by type
    sources_by_type = {
        'epub': [],
        'text': [],
        'url': []
    }
    
    for source_id, source in st.session_state.content_manager.active_sources.items():
        sources_by_type[source['type']].append(source)

    # Render existing sources sections
    with st.expander("📚 EPUB Books", expanded=True):
        if sources_by_type['epub']:
            for source in sources_by_type['epub']:
                render_epub_source(source)
        else:
            st.info("No EPUB books added yet")

    with st.expander("📝 Text Entries", expanded=True):
        if sources_by_type['text']:
            for source in sources_by_type['text']:
                render_text_source(source)
        else:
            st.info("No text entries added yet")

    with st.expander("🔗 URL Sources", expanded=True):
        if sources_by_type['url']:
            for source in sources_by_type['url']:
                render_url_source(source)
        else:
            st.info("No URL sources added yet")

    # Add new content section
    st.markdown("### Add New Content")
    input_method = st.radio(
        "Choose input method:",
        ["EPUB", "Text", "URL"],
        horizontal=True,
        key="content_input_method_main"
    )

    # Render appropriate input method
    if input_method == "EPUB":
        render_epub_upload(key_prefix="main_")
    elif input_method == "Text":
        render_text_input(key_prefix="main_")
    else:  # URL
        render_url_input(key_prefix="main_")


def render_epub_source(source):
    """Render an individual EPUB source with progress"""
    with st.container():
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.markdown(f"**{source['name']}**")
            progress = source['progress']
            progress_pct = (progress['processed_units'] / progress['total_units'] * 100) if progress['total_units'] > 0 else 0
            st.progress(progress_pct / 100, f"{progress['processed_units']}/{progress['total_units']} chapters")
            
            if progress['last_processed']:
                # Handle both string and datetime objects for last_processed
                try:
                    if isinstance(progress['last_processed'], str):
                        last_processed = datetime.fromisoformat(progress['last_processed'])
                    else:
                        last_processed = progress['last_processed']
                    st.caption(f"Last processed: {last_processed.strftime('%Y-%m-%d %H:%M')}")
                except Exception:
                    st.caption(f"Last processed: {progress['last_processed']}")
        
        with col2:
            if progress['processed_units'] < progress['total_units']:
                if st.button("Continue", key=f"continue_{source['id']}"):
                    with st.spinner("Processing next batch..."):
                        added, duplicates, error = st.session_state.content_manager.process_source_content(
                            source['id'], batch_size=5)
                        if error:
                            st.error(error)
                        else:
                            st.success(f"Added {added} new sentences! ({duplicates} duplicates skipped)")
                            st.rerun()
            
            if st.button("Remove", key=f"remove_{source['id']}", type="secondary"):
                if show_confirmation_dialog("Are you sure you want to remove this book?"):
                    success, error = st.session_state.content_manager.remove_source(source['id'])
                    if success:
                        st.success("Book removed successfully!")
                        st.rerun()
                    else:
                        st.error(error)

def render_text_source(source):
    """Render an individual text source"""
    with st.container():
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.markdown(f"**{source['name']}**")
            st.caption(f"Added: {source['created_date'].strftime('%Y-%m-%d %H:%M')}")
            
            if 'sentence_count' in source:
                st.caption(f"Sentences: {source['sentence_count']}")
        
        with col2:
            if st.button("Remove", key=f"remove_{source['id']}", type="secondary"):
                if show_confirmation_dialog("Are you sure you want to remove this text?"):
                    success, error = st.session_state.content_manager.remove_source(source['id'])
                    if success:
                        st.success("Text removed successfully!")
                        st.rerun()
                    else:
                        st.error(error)

def render_url_source(source):
    """Render an individual URL source"""
    with st.container():
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.markdown(f"**{source['name']}**")
            st.caption(f"URL: {source.get('url', 'N/A')}")
            st.caption(f"Added: {source['created_date'].strftime('%Y-%m-%d %H:%M')}")
        
        with col2:
            if st.button("Refresh", key=f"refresh_{source['id']}"):
                with st.spinner("Refreshing content..."):
                    added, duplicates, error = st.session_state.content_manager.process_source_content(source['id'])
                    if error:
                        st.error(error)
                    else:
                        st.success(f"Added {added} new sentences! ({duplicates} duplicates skipped)")
            
            if st.button("Remove", key=f"remove_{source['id']}", type="secondary"):
                if show_confirmation_dialog("Are you sure you want to remove this URL source?"):
                    success, error = st.session_state.content_manager.remove_source(source['id'])
                    if success:
                        st.success("URL source removed successfully!")
                        st.rerun()
                    else:
                        st.error(error)

def render_epub_upload(key_prefix=""):
    uploaded_file = st.file_uploader(
        "Upload EPUB file",
        type=['epub'],
        key=f"{key_prefix}epub_uploader",
        help="Drop your EPUB file here or click to browse (Max 200MB)"
    )
    
    if uploaded_file:
        source_name = st.text_input(
            "Book name (optional)", 
            value=uploaded_file.name.replace('.epub', ''),
            key=f"{key_prefix}epub_source_name"
        )
        
        if st.button("Add Book", key=f"{key_prefix}add_epub"):
            with st.spinner("Processing book..."):
                source_id, error = st.session_state.content_manager.add_source(
                    'epub',
                    source_name,
                    file_data=uploaded_file.getvalue()
                )
                
                if error:
                    st.error(error)
                else:
                    added, duplicates, error = st.session_state.content_manager.process_source_content(
                        source_id, batch_size=5)
                    
                    if error:
                        st.error(error)
                    else:
                        st.success(f"Added {added} new sentences! ({duplicates} duplicates skipped)")
                        st.rerun()

def render_url_input(key_prefix=""):
    url = st.text_input(
        "Enter article URL",
        key=f"{key_prefix}url_input"
    )
    
    source_name = st.text_input(
        "Source name (optional)",
        key=f"{key_prefix}url_source_name"
    )
    
    if st.button("Add URL", key=f"{key_prefix}add_url"):
        if url:
            with st.spinner("Fetching content..."):
                try:
                    # Add proper headers and verify SSL
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    response = requests.get(url, headers=headers, verify=True)
                    response.raise_for_status()  # Raise an error for bad status codes
                    
                    source_id, error = st.session_state.content_manager.add_source(
                        'url',
                        source_name or url,
                        content={
                            'url': url,
                            'html': response.text  # Store both URL and fetched content
                        }
                    )
                    
                    if error:
                        st.error(error)
                    else:
                        added, duplicates, error = st.session_state.content_manager.process_source_content(source_id)
                        if error:
                            st.error(error)
                        else:
                            st.success(f"Added {added} new sentences! ({duplicates} duplicates skipped)")
                            st.rerun()
                except Exception as e:
                    st.error(f"Error fetching URL: {str(e)}")
        else:
            st.warning("Please enter a URL")

def render_text_input(key_prefix=""):
    text_input = st.text_area(
        "Paste Japanese text",
        height=200,
        key=f"{key_prefix}text_input"
    )
    
    source_name = st.text_input(
        "Text name (optional)",
        key=f"{key_prefix}text_source_name"
    )
    
    if st.button("Add Text", key=f"{key_prefix}add_text"):
        if text_input:
            with st.spinner("Processing text..."):
                source_id, error = st.session_state.content_manager.add_source(
                    'text',
                    source_name or "Text Entry",
                    content={
                        'text': text_input,
                        'type': 'raw_text'  # Add type information
                    }
                )
                
                if error:
                    st.error(error)
                else:
                    added, duplicates, error = st.session_state.content_manager.process_source_content(source_id)
                    if error:
                        st.error(error)
                    else:
                        st.success(f"Added {added} new sentences! ({duplicates} duplicates skipped)")
                        st.rerun()
        else:
            st.warning("Please enter some text")
    
def main():
    # Initialize authentication first
    init_auth()
    
    # Check if user is logged in
    if not render_auth_page():
        return
    
    # Initialize user-specific data storage
    user_data_path = f"./data/user_{st.session_state.user_id}"
    
    # Continue with existing initialization
    init_streamlit()
    
    st.title("自然暗記 - Natural Anki")
    st.subheader("Natural Japanese Learning Through Social Scrolling")
    
    # Sidebar content
    with st.sidebar:
        # Add logout button at top
        if st.button("�logout", type="secondary"):
            st.session_state.user_id = None
            st.session_state.initialized = False
            st.rerun()
            
        # Rest of your existing sidebar code
        st.markdown("### 📚 Content Sources")
        
        if st.session_state.content_manager.active_sources:
            epub_count = len([s for s in st.session_state.content_manager.active_sources.values() if s['type'] == 'epub'])
            text_count = len([s for s in st.session_state.content_manager.active_sources.values() if s['type'] == 'text'])
            url_count = len([s for s in st.session_state.content_manager.active_sources.values() if s['type'] == 'url'])
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Books", epub_count)
            with col2:
                st.metric("Texts", text_count)
            with col3:
                st.metric("URLs", url_count)
        
        # Content input section with new UI
        render_content_input()
        
        st.markdown("---")
        
        # Existing save/export/restore controls
        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("💾 Save", help="Save your current progress"):
                with st.spinner("Saving..."):
                    success, message = st.session_state.state_manager.save_state(
                        st.session_state.content_manager,
                        st.session_state.review_system,
                        st.session_state.time_tracker
                    )
                    if success:
                        st.success("Saved!")
                    else:
                        st.error(message)
        
        with col2:
            if st.button("📤 Export", help="Export your data"):
                with st.spinner("Exporting..."):
                    success, result = export_progress(
                        st.session_state.content_manager,
                        st.session_state.review_system,
                        st.session_state.time_tracker
                    )
                    if success:
                        st.download_button(
                            "📥 Download",
                            result,
                            "progress_export.json"
                        )
        
        with col3:
            uploaded_file = st.file_uploader(
                "🔄 Restore",
                type=['pkl'],
                help="Restore from backup",
                key="restore_uploader"
            )
            if uploaded_file:
                if st.button("Restore"):
                    with st.spinner("Restoring..."):
                        success = restore_system_state(uploaded_file, st.session_state.state_manager)
                        if success:
                            st.success("Restored!")
                            st.rerun()
                        else:
                            st.error("Restore failed")
        
        # Dark mode toggle
        st.markdown("---")
        if st.toggle("🌙 Dark Mode"):
            st.markdown("""
                <style>
                .main-card, .stApp, .css-1d391kg, .css-12oz5g7, .css-18e3th9 {
                    background-color: #1a1a1a;
                    color: #ffffff;
                }
                .card-container, .stTextInput>div>div>input {
                    background-color: #2d2d2d;
                    color: #ffffff;
                }
                .analysis-card {
                    background-color: #2d2d2d;
                    color: #ffffff;
                }
                .stMarkdown, .stText, .stTextInput label {
                    color: #ffffff !important;
                }
                .stButton>button {
                    background-color: #4a4a4a;
                    color: #ffffff;
                }
                </style>
            """, unsafe_allow_html=True)
    
    # Main content area
    search_query = st.text_input("🔍 Search cards", 
        help="Filter cards by text content",
        placeholder="Type to search...",
        key="search_input"
    )
    
    # Stats summary
    render_stats_summary()
    
    # Main tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Learn", 
        "Schedule", 
        "Stats", 
        "Sources",
        "Chat with Tutor"
    ])
    
    with tab1:
        if search_query:
            filtered_sentences = [
                s for s in st.session_state.content_manager.sentences 
                if search_query.lower() in s['text'].lower()
            ]
            if filtered_sentences:
                temp_manager = ContentManager()
                temp_manager.sentences = filtered_sentences
                render_feed(temp_manager, st.session_state.review_system)
            else:
                st.info("No matching cards found")
        else:
            render_feed(st.session_state.content_manager, st.session_state.review_system)
    
    with tab2:
        render_schedule(
            st.session_state.content_manager,
            st.session_state.review_system
        )
    
    with tab3:
        render_stats(
            st.session_state.content_manager,
            st.session_state.review_system,
            st.session_state.time_tracker
        )
    
    with tab4:
        st.markdown("### Content Sources")
        
        # EPUB Books section
        with st.expander("📚 EPUB Books", expanded=True):
            epub_sources = [s for s in st.session_state.content_manager.active_sources.values() 
                          if s['type'] == 'epub']
            if epub_sources:
                for source in epub_sources:
                    progress = source['progress']
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**{source['name']}**")
                        progress_pct = (progress['processed_units'] / progress['total_units']) if progress['total_units'] > 0 else 0
                        st.progress(progress_pct, f"{progress['processed_units']}/{progress['total_units']} chapters")
                    with col2:
                        if progress['processed_units'] < progress['total_units']:
                            st.button("Continue", key=f"src_continue_{source['id']}")
                        st.button("Remove", key=f"src_remove_{source['id']}")
            else:
                st.info("No EPUB books added")
        
        # Text Entries section
        with st.expander("📝 Text Entries", expanded=True):
            text_sources = [s for s in st.session_state.content_manager.active_sources.values() 
                          if s['type'] == 'text']
            if text_sources:
                for source in text_sources:
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**{source['name']}**")
                        st.caption(f"Added: {source['created_date'].strftime('%Y-%m-%d %H:%M')}")
                    with col2:
                        st.button("Remove", key=f"src_remove_{source['id']}")
            else:
                st.info("No text entries added")
        
        # URL Sources section
        with st.expander("🔗 URL Sources", expanded=True):
            url_sources = [s for s in st.session_state.content_manager.active_sources.values() 
                         if s['type'] == 'url']
            if url_sources:
                for source in url_sources:
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**{source['name']}**")
                        st.caption(f"URL: {source.get('url', 'N/A')}")
                    with col2:
                        st.button("Refresh", key=f"src_refresh_{source['id']}")
                        st.button("Remove", key=f"src_remove_{source['id']}")
            else:
                st.info("No URL sources added")
    
    with tab5:
        render_chat_interface(
            st.session_state.content_manager,
            st.session_state.review_system
        )
    
    # Auto-save check
    current_time = datetime.now()
    if (current_time - st.session_state.last_save).seconds >= 300:  # 5 minutes
        with st.spinner("Auto-saving..."):
            success, message = st.session_state.state_manager.save_state(
                st.session_state.content_manager,
                st.session_state.review_system,
                st.session_state.time_tracker
            )
            st.session_state.last_save = current_time
if __name__ == "__main__":
    main()
