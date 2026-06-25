from flask import Flask, render_template_string, request, jsonify, redirect, session
import requests
import re
import json
import os
import base64
import secrets
import hashlib
import binascii
import time
from functools import wraps

app = Flask(__name__)

# --- FIX 1: Session secret from environment (persistent across restarts) ---
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# --- CONFIGURATION ---
WOLF_KEY = 'wxa_u_b711f1a197'
WOLFX_GPT_URL = 'https://apis.xwolf.space/api/ai/gpt'

# --- GITHUB CONFIG ---
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'wolfix-bots/foxy-links-data')

# --- RATE LIMITING (Simple in-memory) ---
rate_limit_store = {}
def rate_limit(limit=10, window=60):
    """Simple rate limiter: max `limit` requests per `window` seconds."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = request.remote_addr
            now = time.time()
            # Clean old entries
            for ip in list(rate_limit_store.keys()):
                if rate_limit_store[ip] and now - rate_limit_store[ip][-1] > window:
                    rate_limit_store[ip] = []
            if key not in rate_limit_store:
                rate_limit_store[key] = []
            rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window]
            if len(rate_limit_store[key]) >= limit:
                return jsonify({"error": "Too many requests. Please wait."}), 429
            rate_limit_store[key].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

# --- INPUT VALIDATION HELPERS ---
def is_valid_email(email):
    return re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email) is not None

def is_valid_url(url):
    return re.match(r'^https?://[^\s/$.?#].[^\s]*$', url, re.IGNORECASE) is not None

# --- GITHUB API HELPERS ---
def github_request(endpoint, method='GET', data=None):
    url = f'https://api.github.com/{endpoint}'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    try:
        if method == 'GET':
            r = requests.get(url, headers=headers, timeout=10)
        elif method == 'POST':
            r = requests.post(url, headers=headers, json=data, timeout=10)
        elif method == 'PUT':
            r = requests.put(url, headers=headers, json=data, timeout=10)
        elif method == 'DELETE':
            r = requests.delete(url, headers=headers, timeout=10)
        return r
    except requests.exceptions.Timeout:
        return None
    except requests.exceptions.RequestException:
        return None

def get_user_data_file(user_id):
    return f'data/{user_id}.json'

def read_user_data(user_id):
    file_path = get_user_data_file(user_id)
    url = f'repos/{GITHUB_REPO}/contents/{file_path}'
    r = github_request(url)
    if r and r.status_code == 200:
        content = r.json().get('content', '')
        if content:
            decoded = base64.b64decode(content).decode('utf-8')
            return json.loads(decoded)
    return None

def write_user_data(user_id, data):
    file_path = get_user_data_file(user_id)
    url = f'repos/{GITHUB_REPO}/contents/{file_path}'
    r = github_request(url)
    sha = r.json().get('sha') if r and r.status_code == 200 else None
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    payload = {
        'message': f'Update data for {user_id}',
        'content': content,
        'branch': 'main'
    }
    if sha:
        payload['sha'] = sha
    r = github_request(url, 'PUT', payload)
    return r and r.status_code in [200, 201]

def read_users_db():
    url = f'repos/{GITHUB_REPO}/contents/users.json'
    r = github_request(url)
    if r and r.status_code == 200:
        content = r.json().get('content', '')
        if content:
            decoded = base64.b64decode(content).decode('utf-8')
            return json.loads(decoded)
    return {}

def write_users_db(users):
    url = f'repos/{GITHUB_REPO}/contents/users.json'
    r = github_request(url)
    sha = r.json().get('sha') if r and r.status_code == 200 else None
    content = base64.b64encode(json.dumps(users, indent=2).encode()).decode()
    payload = {
        'message': 'Update users database',
        'content': content,
        'branch': 'main'
    }
    if sha:
        payload['sha'] = sha
    r = github_request(url, 'PUT', payload)
    return r and r.status_code in [200, 201]

# --- PASSWORD HASHING ---
def hash_password(password):
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return binascii.hexlify(salt).decode(), binascii.hexlify(key).decode()

def verify_password(stored_salt, stored_key, password):
    salt = binascii.unhexlify(stored_salt)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return binascii.hexlify(key).decode() == stored_key

def generate_user_id():
    return secrets.token_hex(16)

# --- AI HELPERS ---
def fetch_meta(url):
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        title = re.search(r'<title>(.*?)</title>', r.text, re.IGNORECASE)
        desc = re.search(r'<meta name="description" content="(.*?)"', r.text, re.IGNORECASE)
        return {
            "title": title.group(1).strip() if title else url,
            "description": desc.group(1).strip() if desc else "",
        }
    except:
        return {"title": url, "description": ""}

def ai_tag(url, title, description):
    prompt = f"URL: {url}\nTitle: {title}\nDescription: {description}\n\nGenerate 3-5 relevant tags for this link. Return only the tags, comma separated."
    try:
        r = requests.get(f"{WOLFX_GPT_URL}?q={requests.utils.quote(prompt)}&key={WOLF_KEY}", timeout=15)
        data = r.json()
        if data.get('status'):
            tags = data.get('result', '').strip().split(',')
            return [t.strip().lower() for t in tags if t.strip()]
        return []
    except:
        return []

# --- CSS GENERATION ---
DEFAULT_STYLES = """
:root {
    --bg-body: #0a0f1a;
    --bg-container: #111827;
    --bg-card: #1a2332;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --accent-color: #ff6600;
    --accent-glow: #ff6600;
    --border-color: #ff660030;
    --shadow: 0 20px 60px rgba(0,0,0,0.5);
}
body.light {
    --bg-body: #f8fafc;
    --bg-container: #ffffff;
    --bg-card: #f1f5f9;
    --text-primary: #0f172a;
    --text-secondary: #64748b;
    --accent-color: #ff6600;
    --accent-glow: #ff6600;
    --border-color: #ff660030;
    --shadow: 0 20px 60px rgba(0,0,0,0.08);
}
"""

def generate_css_from_prompt(prompt, current_css):
    system_instruction = (
        "You are an expert CSS designer. Given the current CSS variables and a user request, "
        "generate ONLY the new CSS :root block with updated variables. "
        "Output ONLY valid CSS code, no explanations."
    )
    full_prompt = f"{system_instruction}\n\nCurrent CSS:\n{current_css}\n\nUser request: {prompt}\n\nUpdated CSS (only :root):"
    try:
        url = f"{WOLFX_GPT_URL}?q={requests.utils.quote(full_prompt)}&key={WOLF_KEY}"
        r = requests.get(url, timeout=30)
        data = r.json()
        if data.get('status'):
            raw = data.get('result', '')
            root_match = re.search(r':root\s*{([^}]*)}', raw, re.DOTALL)
            if root_match:
                return f":root {{ {root_match.group(1).strip()} }}"
            else:
                return raw.strip()
        return None
    except:
        return None

# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>🔗 FOXY LINKS – Smart Bookmark Manager</title>
    <meta name="description" content="Save, organise, and discover your links with AI-powered tags.">
    <meta property="og:type" content="website">
    <meta property="og:title" content="FOXY LINKS – Smart Bookmark Manager">
    <meta property="og:description" content="Save, organise, and discover your links with AI-powered tags.">
    <meta property="og:image" content="https://files.catbox.moe/xkms62.jpg">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="theme-color" content="#ff6600" id="themeColorMeta">

    <style id="ai-styles">{{ default_styles }}</style>

    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: var(--bg-body);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: var(--text-primary);
            transition: background 0.3s, color 0.3s;
            min-height: 100vh;
        }
        .app {
            display: flex;
            min-height: 100vh;
        }

        .sidebar {
            position: fixed;
            top: 0; left: 0;
            width: 260px;
            height: 100%;
            background: var(--bg-container);
            border-right: 1px solid var(--border-color);
            transform: translateX(0);
            transition: transform 0.3s ease;
            z-index: 1000;
            overflow-y: auto;
            padding: 20px;
        }
        .sidebar.closed { transform: translateX(-100%); }
        .sidebar-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 30px;
        }
        .sidebar-header h2 {
            color: var(--accent-color);
            font-size: 24px;
            text-shadow: 0 0 20px var(--accent-glow);
        }
        .close-sidebar { background: none; border: none; font-size: 24px; cursor: pointer; color: var(--text-secondary); }
        .nav-item {
            display: flex; align-items: center; gap: 12px;
            padding: 12px 16px;
            border-radius: 12px;
            cursor: pointer;
            transition: 0.2s;
            color: var(--text-secondary);
            margin-bottom: 4px;
        }
        .nav-item:hover, .nav-item.active {
            background: var(--border-color);
            color: var(--accent-color);
        }
        .nav-item .icon { font-size: 20px; }

        .main-content {
            flex: 1;
            margin-left: 260px;
            padding: 30px;
            transition: margin-left 0.3s ease;
        }
        .main-content.expanded { margin-left: 0; }
        .hamburger {
            display: none;
            background: none; border: none; font-size: 28px;
            color: var(--text-primary); cursor: pointer;
            margin-bottom: 20px;
        }
        @media (max-width: 768px) {
            .hamburger { display: block; }
            .sidebar { transform: translateX(-100%); }
            .sidebar.open { transform: translateX(0); }
            .main-content { margin-left: 0; }
        }

        .panel { display: none; animation: fadeIn 0.2s; }
        .panel.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

        .top-bar {
            display: flex; justify-content: space-between; align-items: center;
            flex-wrap: wrap; gap: 12px; margin-bottom: 24px;
        }
        .page-title { font-size: 28px; font-weight: 700; color: var(--text-primary); }
        .page-title span { color: var(--accent-color); }
        .search-bar { display: flex; gap: 10px; flex: 1; max-width: 500px; }
        .search-bar input {
            flex: 1; padding: 10px 16px; border-radius: 30px;
            border: 1px solid var(--border-color);
            background: var(--bg-card);
            color: var(--text-primary);
            font-size: 14px;
        }
        .search-bar input:focus { outline: none; border-color: var(--accent-color); }
        .search-bar button {
            padding: 10px 20px; border-radius: 30px; border: none;
            background: var(--accent-color); color: #fff; font-weight: 600;
            cursor: pointer; transition: 0.2s;
        }
        .search-bar button:hover { filter: brightness(0.9); transform: scale(0.98); }

        .btn {
            padding: 10px 20px; border-radius: 30px; border: none;
            background: var(--accent-color); color: #fff; font-weight: 600;
            cursor: pointer; transition: 0.2s;
        }
        .btn:hover { filter: brightness(0.9); transform: scale(0.98); }
        .btn-secondary {
            background: transparent; border: 1px solid var(--border-color);
            color: var(--text-secondary);
        }
        .btn-secondary:hover { background: var(--border-color); }

        .links-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 16px;
            margin-top: 20px;
        }
        .link-card {
            background: var(--bg-card);
            border-radius: 16px;
            padding: 16px;
            border: 1px solid var(--border-color);
            transition: 0.2s;
            position: relative;
        }
        .link-card:hover {
            transform: translateY(-4px);
            border-color: var(--accent-color);
            box-shadow: 0 0 20px var(--accent-glow)20;
        }
        .link-card h3 { color: var(--text-primary); font-size: 16px; margin-bottom: 4px; }
        .link-card h3 a { color: inherit; text-decoration: none; }
        .link-card h3 a:hover { color: var(--accent-color); }
        .link-card p { color: var(--text-secondary); font-size: 13px; margin-bottom: 8px; }
        .link-card .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
        .link-card .tags span {
            background: var(--border-color);
            padding: 2px 10px; border-radius: 30px;
            font-size: 11px; color: var(--text-secondary);
        }
        .link-card .collection-badge {
            position: absolute; top: 12px; right: 12px;
            background: var(--accent-color); color: #fff;
            padding: 2px 10px; border-radius: 30px;
            font-size: 10px; font-weight: 600;
        }
        .link-card .actions {
            display: flex; gap: 8px; margin-top: 12px;
        }
        .link-card .actions button {
            background: none; border: none;
            color: var(--text-secondary); cursor: pointer;
            font-size: 14px; padding: 4px 8px;
            border-radius: 8px; transition: 0.2s;
        }
        .link-card .actions button:hover {
            background: var(--border-color); color: var(--accent-color);
        }

        .modal-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7); backdrop-filter: blur(6px);
            display: none; justify-content: center; align-items: center; z-index: 2000;
        }
        .modal-overlay.open { display: flex; }
        .modal {
            background: var(--bg-container);
            border-radius: 24px;
            padding: 30px;
            max-width: 500px;
            width: 90%;
            max-height: 90vh;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            box-shadow: 0 0 30px var(--accent-glow)30;
        }
        .modal h2 { color: var(--text-primary); margin-bottom: 20px; }
        .modal h2 span { color: var(--accent-color); }
        .modal .input-group {
            display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px;
        }
        .modal .input-group label { color: var(--text-secondary); font-size: 13px; font-weight: 500; }
        .modal .input-group input, .modal .input-group select, .modal .input-group textarea {
            padding: 10px 14px; border-radius: 12px;
            border: 1px solid var(--border-color);
            background: var(--bg-card);
            color: var(--text-primary);
            font-size: 14px;
            font-family: inherit;
        }
        .modal .input-group input:focus, .modal .input-group select:focus, .modal .input-group textarea:focus {
            outline: none; border-color: var(--accent-color);
        }
        .modal .btn-row { display: flex; gap: 12px; margin-top: 16px; }
        .modal .btn-row button { flex: 1; }

        .empty-state {
            text-align: center; padding: 60px 20px;
            color: var(--text-secondary);
        }
        .empty-state .icon { font-size: 64px; margin-bottom: 16px; }
        .empty-state h3 { color: var(--text-primary); margin-bottom: 8px; }
        .empty-state p { max-width: 400px; margin: 0 auto; }

        .user-section {
            padding: 12px 16px;
            background: var(--border-color);
            border-radius: 12px;
            margin-top: 20px;
        }
        .user-section .user-info {
            display: flex; align-items: center; gap: 10px;
            color: var(--text-secondary);
            font-size: 13px;
        }
        .user-section .user-info img {
            width: 28px; height: 28px; border-radius: 50%;
        }

        .saving-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            padding: 10px 20px;
            background: var(--bg-container);
            border-top: 1px solid var(--border-color);
            display: none;
            justify-content: space-between;
            align-items: center;
            z-index: 3000;
            font-size: 14px;
            color: var(--text-secondary);
        }
        .saving-bar .spinner {
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 2px solid var(--border-color);
            border-top-color: var(--accent-color);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 10px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .saving-bar .status-text { flex: 1; }
        .saving-bar .status-icon { font-size: 18px; margin-left: 10px; }

        .reminder-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(6px);
            z-index: 5000;
            display: none;
            justify-content: center;
            align-items: center;
        }
        .reminder-overlay.open { display: flex; }
        .reminder-card {
            background: var(--bg-container);
            border-radius: 24px;
            padding: 30px;
            max-width: 450px;
            width: 90%;
            text-align: center;
            border: 1px solid var(--border-color);
            box-shadow: 0 0 30px var(--accent-glow)30;
        }
        .reminder-card h3 { color: var(--accent-color); margin-bottom: 12px; }
        .reminder-card p { color: var(--text-secondary); line-height: 1.6; margin-bottom: 20px; }
        .reminder-card .btn { width: 100%; }

        .login-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.95);
            backdrop-filter: blur(8px);
            z-index: 6000;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .login-overlay.hidden { display: none; }
        .login-card {
            background: var(--bg-container);
            border-radius: 32px;
            padding: 40px;
            max-width: 400px;
            width: 90%;
            text-align: center;
            border: 1px solid var(--border-color);
            box-shadow: 0 0 30px var(--accent-glow)30;
        }
        .login-card h2 { color: var(--accent-color); margin-bottom: 8px; }
        .login-card p { color: var(--text-secondary); margin-bottom: 24px; }
        .login-card .input-group { margin-bottom: 16px; }
        .login-card .input-group label { display: block; text-align: left; color: var(--text-secondary); font-size: 13px; margin-bottom: 4px; }
        .login-card .input-group input {
            width: 100%; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--border-color);
            background: var(--bg-card); color: var(--text-primary); font-size: 14px;
        }
        .login-card .input-group input:focus { outline: none; border-color: var(--accent-color); }
        .login-card .btn { width: 100%; margin-top: 8px; }
        .login-card .toggle-link { color: var(--accent-color); cursor: pointer; margin-top: 12px; display: inline-block; }
        .login-card .error-msg { color: #ff4444; font-size: 13px; margin-top: 8px; min-height: 20px; }

        /* CSS Generation UI Styles */
        .css-gen-area textarea {
            width: 100%;
            padding: 10px;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            background: var(--bg-card);
            color: var(--text-primary);
            font-family: inherit;
            resize: vertical;
        }
        .css-gen-area textarea:focus {
            outline: none;
            border-color: var(--accent-color);
        }

        @media (max-width: 640px) {
            .main-content { padding: 20px; }
            .links-grid { grid-template-columns: 1fr; }
            .top-bar { flex-direction: column; align-items: stretch; }
            .search-bar { max-width: 100%; }
            .modal { padding: 20px; }
        }
    </style>
</head>
<body>
<div class="app">
    <!-- Sidebar -->
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <h2>🦊 FOXY</h2>
            <button class="close-sidebar" id="closeSidebarBtn">✕</button>
        </div>
        <div class="nav-item active" data-panel="all">
            <span class="icon">📚</span> All Links
        </div>
        <div class="nav-item" data-panel="collections">
            <span class="icon">📂</span> Collections
        </div>
        <div class="nav-item" data-panel="read-later">
            <span class="icon">⏰</span> Read Later
        </div>
        <div class="nav-item" id="addLinkNav">
            <span class="icon">➕</span> Add Link
        </div>
        <div class="nav-item" data-panel="settings">
            <span class="icon">⚙️</span> Settings
        </div>

        <!-- User Section -->
        <div class="user-section" id="userSection">
            <div class="user-info">
                <img id="userAvatar" src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png" alt="GitHub">
                <span id="userName">Not logged in</span>
            </div>
            <div style="margin-top:10px; display:flex; gap:8px;">
                <button class="btn" id="loginBtn" style="font-size:12px; padding:6px 16px;">🔐 Login</button>
                <button class="btn btn-secondary" id="logoutBtn" style="font-size:12px; padding:6px 16px; display:none;">🚪 Logout</button>
            </div>
            <div id="syncStatus" style="font-size:11px; color:var(--text-secondary); margin-top:6px;">⬇️ Synced with GitHub</div>
        </div>
    </div>

    <!-- Main Content -->
    <div class="main-content" id="mainContent">
        <button class="hamburger" id="hamburgerBtn">☰</button>

        <!-- All Links Panel -->
        <div class="panel active" id="panel-all">
            <div class="top-bar">
                <div class="page-title">📚 <span>All Links</span></div>
                <div class="search-bar">
                    <input type="text" id="searchInput" placeholder="Search links, tags, collections...">
                    <button id="searchBtn">🔍 Search</button>
                </div>
                <button class="btn" id="addLinkBtn">➕ Add Link</button>
            </div>
            <div id="linksContainer">
                <div class="links-grid" id="linksGrid"></div>
            </div>
        </div>

        <!-- Collections Panel -->
        <div class="panel" id="panel-collections">
            <div class="top-bar">
                <div class="page-title">📂 <span>Collections</span></div>
                <button class="btn" id="addCollectionBtn">➕ New Collection</button>
            </div>
            <div id="collectionsContainer" class="links-grid"></div>
        </div>

        <!-- Read Later Panel -->
        <div class="panel" id="panel-read-later">
            <div class="page-title">⏰ <span>Read Later</span></div>
            <div id="readLaterContainer" class="links-grid"></div>
        </div>

        <!-- Settings Panel -->
        <div class="panel" id="panel-settings">
            <div class="page-title">⚙️ <span>Settings</span></div>

            <div class="setting-row" style="padding:14px 0; border-bottom:1px solid var(--border-color);">
                <span style="font-weight:500;">🌗 Theme</span>
                <div class="theme-btn-group" style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px;">
                    <button class="theme-btn active" data-theme="dark">🌙 Dark</button>
                    <button class="theme-btn" data-theme="light">☀️ Light</button>
                </div>
            </div>

            <div class="setting-row" style="padding:14px 0; border-bottom:1px solid var(--border-color);">
                <span style="font-weight:500;">🎨 Chrome Bar Color</span>
                <div class="chrome-color-row" style="display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-top:8px;">
                    <input type="color" id="chromeColorPicker" value="#ff6600">
                    <span id="chromeColorHex">#ff6600</span>
                    <button class="btn btn-secondary" id="resetChromeColorBtn">↩️ Reset</button>
                </div>
            </div>

            <!-- CSS Generation UI -->
            <div class="setting-row css-gen-area" style="padding:14px 0;">
                <span style="font-weight:500;">🎨 AI Theme Generator</span>
                <div style="margin-top:8px;">
                    <textarea id="cssPromptInput" rows="3" placeholder="Describe your dream theme...&#10;e.g. 'Make it look like a cyberpunk sunset with purple and orange'"></textarea>
                    <button class="btn" id="generateCssBtn" style="margin-top:8px;">✨ Generate Theme</button>
                    <div id="cssGenerationResult" style="margin-top:8px; font-size:13px; color:var(--text-secondary);"></div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Login / Register Overlay -->
<div class="login-overlay" id="loginOverlay">
    <div class="login-card">
        <h2>🦊 FOXY LINKS</h2>
        <p id="loginSubtext">Sign in to access your bookmarks</p>
        <div id="loginForm">
            <div class="input-group">
                <label>Email</label>
                <input type="email" id="loginEmail" placeholder="you@example.com">
            </div>
            <div class="input-group">
                <label>Password</label>
                <input type="password" id="loginPassword" placeholder="••••••••">
            </div>
            <button class="btn" id="loginSubmitBtn">Sign In</button>
            <div class="error-msg" id="loginError"></div>
            <div class="toggle-link" id="toggleAuthLink">Don't have an account? Sign up</div>
        </div>
        <div id="registerForm" style="display:none;">
            <div class="input-group">
                <label>Email</label>
                <input type="email" id="registerEmail" placeholder="you@example.com">
            </div>
            <div class="input-group">
                <label>Password</label>
                <input type="password" id="registerPassword" placeholder="••••••••">
            </div>
            <button class="btn" id="registerSubmitBtn">Create Account</button>
            <div class="error-msg" id="registerError"></div>
            <div class="toggle-link" id="toggleLoginLink">Already have an account? Sign in</div>
        </div>
    </div>
</div>

<!-- Modals -->
<div class="modal-overlay" id="addLinkModal">
    <div class="modal">
        <h2>➕ <span>Add Link</span></h2>
        <div class="input-group">
            <label>URL</label>
            <input type="url" id="linkUrl" placeholder="https://example.com">
        </div>
        <div class="input-group">
            <label>Title (auto-filled)</label>
            <input type="text" id="linkTitle" placeholder="Page title">
        </div>
        <div class="input-group">
            <label>Description (auto-filled)</label>
            <textarea id="linkDescription" rows="2" placeholder="Page description"></textarea>
        </div>
        <div class="input-group">
            <label>Tags (comma separated)</label>
            <input type="text" id="linkTags" placeholder="python, tutorial, AI">
        </div>
        <div class="input-group">
            <label>Collection</label>
            <select id="linkCollection">
                <option value="General">General</option>
                <option value="Foxy Apps">Foxy Apps</option>
                <option value="Development">Development</option>
                <option value="Inspiration">Inspiration</option>
                <option value="Work">Work</option>
            </select>
        </div>
        <div class="btn-row">
            <button class="btn" id="saveLinkBtn">💾 Save Link</button>
            <button class="btn btn-secondary" id="cancelLinkBtn">Cancel</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="addCollectionModal">
    <div class="modal">
        <h2>📂 <span>New Collection</span></h2>
        <div class="input-group">
            <label>Collection Name</label>
            <input type="text" id="collectionNameInput" placeholder="e.g. Work, Recipes, Coding">
        </div>
        <div class="btn-row">
            <button class="btn" id="saveCollectionBtn">💾 Create</button>
            <button class="btn btn-secondary" id="cancelCollectionBtn">Cancel</button>
        </div>
    </div>
</div>

<div class="saving-bar" id="savingBar">
    <div>
        <span class="spinner" id="savingSpinner"></span>
        <span class="status-text" id="savingStatus">Saving...</span>
    </div>
    <span class="status-icon" id="savingIcon">⏳</span>
</div>

<div class="reminder-overlay" id="reminderOverlay">
    <div class="reminder-card">
        <h3>🦊 Don't Close the Site!</h3>
        <p>
            When you see <strong>"Saving..."</strong> at the bottom of the screen,
            <strong>please wait</strong> – your data is being saved to GitHub.
        </p>
        <p style="font-size:13px; color:var(--text-secondary);">
            Closing the site while saving may cause data loss.
        </p>
        <button class="btn" id="reminderGotItBtn">Got it! 👍</button>
    </div>
</div>

<script>
    // ============================================================
    // PREFIX FOR LOCALSTORAGE
    // ============================================================
    const LS_PREFIX = 'FOXYLINKS_V2_';

    function getLS(key) {
        return localStorage.getItem(LS_PREFIX + key);
    }
    function setLS(key, value) {
        localStorage.setItem(LS_PREFIX + key, value);
    }
    function removeLS(key) {
        localStorage.removeItem(LS_PREFIX + key);
    }

    // ============================================================
    // DOM ELEMENTS
    // ============================================================
    const loginOverlay = document.getElementById('loginOverlay');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const loginEmail = document.getElementById('loginEmail');
    const loginPassword = document.getElementById('loginPassword');
    const loginSubmitBtn = document.getElementById('loginSubmitBtn');
    const loginError = document.getElementById('loginError');
    const toggleAuthLink = document.getElementById('toggleAuthLink');
    const registerEmail = document.getElementById('registerEmail');
    const registerPassword = document.getElementById('registerPassword');
    const registerSubmitBtn = document.getElementById('registerSubmitBtn');
    const registerError = document.getElementById('registerError');
    const toggleLoginLink = document.getElementById('toggleLoginLink');
    const loginSubtext = document.getElementById('loginSubtext');

    const logoutBtn = document.getElementById('logoutBtn');
    const loginBtn = document.getElementById('loginBtn');
    const userName = document.getElementById('userName');
    const userAvatar = document.getElementById('userAvatar');
    const syncStatus = document.getElementById('syncStatus');

    const sidebar = document.getElementById('sidebar');
    const hamburger = document.getElementById('hamburgerBtn');
    const closeSidebarBtn = document.getElementById('closeSidebarBtn');
    const navItems = document.querySelectorAll('.nav-item');
    const panels = document.querySelectorAll('.panel');

    const addLinkBtn = document.getElementById('addLinkBtn');
    const addLinkNav = document.getElementById('addLinkNav');
    const cancelLinkBtn = document.getElementById('cancelLinkBtn');
    const saveLinkBtn = document.getElementById('saveLinkBtn');
    const linkUrl = document.getElementById('linkUrl');
    const linkTitle = document.getElementById('linkTitle');
    const linkDescription = document.getElementById('linkDescription');
    const linkTags = document.getElementById('linkTags');
    const linkCollection = document.getElementById('linkCollection');

    const addCollectionBtn = document.getElementById('addCollectionBtn');
    const cancelCollectionBtn = document.getElementById('cancelCollectionBtn');
    const saveCollectionBtn = document.getElementById('saveCollectionBtn');
    const collectionNameInput = document.getElementById('collectionNameInput');

    const searchInput = document.getElementById('searchInput');
    const searchBtn = document.getElementById('searchBtn');
    const linksGrid = document.getElementById('linksGrid');

    const themeBtns = document.querySelectorAll('.theme-btn');
    const chromeColorPicker = document.getElementById('chromeColorPicker');
    const chromeColorHex = document.getElementById('chromeColorHex');
    const resetChromeColorBtn = document.getElementById('resetChromeColorBtn');

    // CSS Generation Elements
    const cssPromptInput = document.getElementById('cssPromptInput');
    const generateCssBtn = document.getElementById('generateCssBtn');
    const cssGenerationResult = document.getElementById('cssGenerationResult');

    const savingBar = document.getElementById('savingBar');
    const savingStatus = document.getElementById('savingStatus');
    const savingSpinner = document.getElementById('savingSpinner');
    const savingIcon = document.getElementById('savingIcon');

    const reminderOverlay = document.getElementById('reminderOverlay');
    const reminderGotItBtn = document.getElementById('reminderGotItBtn');

    // ============================================================
    // STATE
    // ============================================================
    let userData = JSON.parse(getLS('user') || 'null');
    let links = [];
    let collections = ['General', 'Foxy Apps', 'Development', 'Inspiration', 'Work'];
    let currentTheme = 'dark';
    let currentChromeColor = '#ff6600';
    let isSaving = false;
    let hasUnsavedChanges = false;

    // ============================================================
    // SAVING INDICATOR
    // ============================================================
    function showSaving(message = 'Saving...') {
        isSaving = true;
        hasUnsavedChanges = true;
        savingBar.style.display = 'flex';
        savingStatus.textContent = message;
        savingSpinner.style.display = 'inline-block';
        savingIcon.textContent = '⏳';
    }

    function showSaved(message = 'Saved ✅') {
        isSaving = false;
        hasUnsavedChanges = false;
        savingStatus.textContent = message;
        savingSpinner.style.display = 'none';
        savingIcon.textContent = '✅';
        setTimeout(() => {
            savingBar.style.display = 'none';
        }, 2000);
    }

    function showError(message = 'Error saving ❌') {
        isSaving = false;
        savingStatus.textContent = message;
        savingSpinner.style.display = 'none';
        savingIcon.textContent = '❌';
        setTimeout(() => {
            savingBar.style.display = 'none';
        }, 3000);
    }

    // ============================================================
    // DAILY REMINDER
    // ============================================================
    function showReminder() {
        const today = new Date().toDateString();
        const lastShown = getLS('reminder_last_shown');
        if (lastShown !== today) {
            reminderOverlay.classList.add('open');
        }
    }

    reminderGotItBtn.addEventListener('click', () => {
        reminderOverlay.classList.remove('open');
        setLS('reminder_last_shown', new Date().toDateString());
    });

    // ============================================================
    // CLOSE WARNING
    // ============================================================
    window.addEventListener('beforeunload', (e) => {
        if (isSaving || hasUnsavedChanges) {
            e.preventDefault();
            e.returnValue = 'Data is still saving. Are you sure you want to leave?';
            return e.returnValue;
        }
    });

    // ============================================================
    // THEME & CHROME COLOR
    // ============================================================
    function applyTheme(theme) {
        currentTheme = theme;
        document.body.classList.toggle('light', theme === 'light');
        themeBtns.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.theme === theme);
        });
        if (userData) saveToGitHub();
    }

    function setChromeColor(color) {
        currentChromeColor = color;
        document.getElementById('themeColorMeta').content = color;
        chromeColorHex.textContent = color;
        chromeColorPicker.value = color;
        setLS('chrome_color', color);
        if (userData) saveToGitHub();
    }

    const savedChromeColor = getLS('chrome_color') || '#ff6600';
    setChromeColor(savedChromeColor);

    themeBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            applyTheme(this.dataset.theme);
        });
    });

    chromeColorPicker.addEventListener('input', function(e) {
        setChromeColor(e.target.value);
    });
    resetChromeColorBtn.addEventListener('click', function() {
        setChromeColor('#ff6600');
    });

    // ============================================================
    // CSS GENERATION
    // ============================================================
    generateCssBtn.addEventListener('click', async () => {
        const prompt = cssPromptInput.value.trim();
        if (!prompt) {
            cssGenerationResult.textContent = 'Please describe how you want your theme to look.';
            cssGenerationResult.style.color = '#ff4444';
            return;
        }

        cssGenerationResult.textContent = '⏳ Generating theme...';
        cssGenerationResult.style.color = 'var(--text-secondary)';

        try {
            const res = await fetch('/api/generate-css', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: prompt,
                    current_css: document.getElementById('ai-styles').textContent
                })
            });
            const data = await res.json();

            if (data.css) {
                // Update the styles
                document.getElementById('ai-styles').textContent = data.css;
                cssGenerationResult.textContent = '✅ Theme generated successfully!';
                cssGenerationResult.style.color = '#4caf50';
                // Save the theme to GitHub
                if (userData) saveToGitHub();
            } else if (data.warning) {
                cssGenerationResult.textContent = '⚠️ ' + data.warning;
                cssGenerationResult.style.color = '#ff9800';
            } else {
                cssGenerationResult.textContent = '❌ Failed to generate theme: ' + (data.error || 'Unknown error');
                cssGenerationResult.style.color = '#ff4444';
            }
        } catch (e) {
            cssGenerationResult.textContent = '❌ Network error: ' + e.message;
            cssGenerationResult.style.color = '#ff4444';
        }
    });

    // ============================================================
    // GITHUB DATA OPERATIONS
    // ============================================================
    async function loadFromGitHub() {
        if (!userData) return false;
        showSaving('Loading from GitHub...');
        try {
            const res = await fetch('/api/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userData.id })
            });
            const result = await res.json();
            if (result.success && result.data) {
                links = result.data.links || [];
                collections = result.data.collections || ['General', 'Foxy Apps', 'Development', 'Inspiration', 'Work'];
                currentChromeColor = result.data.chromeColor || '#ff6600';
                currentTheme = result.data.theme || 'dark';
                setChromeColor(currentChromeColor);
                applyTheme(currentTheme);
                renderLinks();
                showSaved('Loaded ✅');
                return true;
            } else {
                showError('Load failed');
                return false;
            }
        } catch (e) {
            console.error('Load error:', e);
            showError('Error loading');
            return false;
        }
    }

    async function saveToGitHub() {
        if (!userData) return false;
        showSaving('Saving to GitHub...');
        try {
            const res = await fetch('/api/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userData.id,
                    data: {
                        links: links,
                        collections: collections,
                        chromeColor: currentChromeColor,
                        theme: currentTheme
                    }
                })
            });
            const result = await res.json();
            if (result.success) {
                showSaved('Saved ✅');
                return true;
            } else {
                showError('Save failed: ' + (result.error || ''));
                return false;
            }
        } catch (e) {
            console.error('Save error:', e);
            showError('Error saving');
            return false;
        }
    }

    // ============================================================
    // RENDER LINKS
    // ============================================================
    function renderLinks(filter = 'all', search = '') {
        let filtered = links;

        if (filter === 'read-later') {
            filtered = links.filter(l => l.read_later);
        } else if (filter !== 'all') {
            filtered = links.filter(l => l.collection === filter);
        }

        if (search) {
            const s = search.toLowerCase();
            filtered = filtered.filter(l =>
                l.title.toLowerCase().includes(s) ||
                l.url.toLowerCase().includes(s) ||
                (l.tags && l.tags.some(t => t.toLowerCase().includes(s))) ||
                (l.collection && l.collection.toLowerCase().includes(s))
            );
        }

        if (!filtered.length) {
            linksGrid.innerHTML = `
                <div class="empty-state" style="grid-column:1/-1;">
                    <div class="icon">🔗</div>
                    <h3>No links found</h3>
                    <p>Add your first link by clicking the "Add Link" button.</p>
                </div>
            `;
            return;
        }

        let html = '';
        for (let link of filtered) {
            const tags = link.tags || [];
            html += `
                <div class="link-card" data-id="${link.id}">
                    <span class="collection-badge">${link.collection || 'General'}</span>
                    <h3><a href="${link.url}" target="_blank">${link.title}</a></h3>
                    <p>${link.description || ''}</p>
                    <div class="tags">
                        ${tags.map(t => `<span>#${t}</span>`).join('')}
                    </div>
                    <div class="actions">
                        <button onclick="toggleReadLater('${link.id}')" title="Read Later">
                            ${link.read_later ? '⏰' : '⏳'}
                        </button>
                        <button onclick="deleteLink('${link.id}')" title="Delete">🗑️</button>
                    </div>
                </div>
            `;
        }
        linksGrid.innerHTML = html;
    }

    // ============================================================
    // LINK ACTIONS
    // ============================================================
    window.toggleReadLater = function(id) {
        const link = links.find(l => l.id === id);
        if (link) {
            link.read_later = !link.read_later;
            renderLinks(getCurrentFilter(), getCurrentSearch());
            saveToGitHub();
        }
    };

    window.deleteLink = function(id) {
        if (confirm('Delete this link?')) {
            links = links.filter(l => l.id !== id);
            renderLinks(getCurrentFilter(), getCurrentSearch());
            saveToGitHub();
        }
    };

    function addLinkToCollection(newLink) {
        links.unshift(newLink);
        renderLinks(getCurrentFilter(), getCurrentSearch());
        saveToGitHub();
    }

    function addCollection(name) {
        if (!collections.includes(name)) {
            collections.push(name);
            const select = document.getElementById('linkCollection');
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
            saveToGitHub();
        }
    }

    function getCurrentFilter() {
        const active = document.querySelector('.nav-item.active');
        if (!active) return 'all';
        const panel = active.dataset.panel;
        if (panel === 'read-later') return 'read-later';
        return panel === 'all' ? 'all' : panel;
    }

    function getCurrentSearch() {
        return searchInput.value.trim();
    }

    // ============================================================
    // SIDEBAR NAVIGATION
    // ============================================================
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            if (item.id === 'addLinkNav') {
                document.getElementById('addLinkModal').classList.add('open');
                return;
            }
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');
            panels.forEach(p => p.classList.remove('active'));
            const panel = document.getElementById(`panel-${item.dataset.panel}`);
            if (panel) panel.classList.add('active');
            renderLinks(getCurrentFilter(), getCurrentSearch());
            if (window.innerWidth <= 768) {
                sidebar.classList.remove('open');
            }
        });
    });

    hamburger.addEventListener('click', () => {
        sidebar.classList.toggle('open');
    });
    closeSidebarBtn.addEventListener('click', () => {
        sidebar.classList.remove('open');
    });

    // ============================================================
    // SEARCH
    // ============================================================
    searchBtn.addEventListener('click', () => {
        renderLinks(getCurrentFilter(), searchInput.value.trim());
    });
    searchInput.addEventListener('keyup', (e) => {
        if (e.key === 'Enter') {
            renderLinks(getCurrentFilter(), searchInput.value.trim());
        }
    });

    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            searchInput.focus();
        }
    });

    // ============================================================
    // ADD LINK MODAL
    // ============================================================
    addLinkBtn.addEventListener('click', () => {
        document.getElementById('addLinkModal').classList.add('open');
    });
    addLinkNav.addEventListener('click', () => {
        document.getElementById('addLinkModal').classList.add('open');
    });
    cancelLinkBtn.addEventListener('click', () => {
        document.getElementById('addLinkModal').classList.remove('open');
    });

    saveLinkBtn.addEventListener('click', async () => {
        const url = linkUrl.value.trim();
        if (!url) { alert('Please enter a URL.'); return; }

        let title = linkTitle.value.trim();
        let description = linkDescription.value.trim();
        let tagsInput = linkTags.value.trim();
        const collection = linkCollection.value;

        if (!title) {
            try {
                const meta = await fetchMeta(url);
                title = meta.title || url;
                description = meta.description || '';
            } catch(e) {}
        }

        const tags = tagsInput ? tagsInput.split(',').map(t => t.trim()).filter(Boolean) : [];

        const newLink = {
            id: Date.now().toString(36),
            url: url,
            title: title || url,
            description: description || '',
            tags: tags,
            collection: collection || 'General',
            read_later: false,
            created_at: new Date().toISOString()
        };

        if (!tags.length) {
            try {
                const aiTags = await fetchAITags(url, title, description);
                if (aiTags.length) newLink.tags = aiTags;
            } catch(e) {}
        }

        addLinkToCollection(newLink);
        document.getElementById('addLinkModal').classList.remove('open');
        linkUrl.value = '';
        linkTitle.value = '';
        linkDescription.value = '';
        linkTags.value = '';
    });

    async function fetchMeta(url) {
        const res = await fetch(`/api/fetch-meta?url=${encodeURIComponent(url)}`);
        return await res.json();
    }

    async function fetchAITags(url, title, description) {
        const res = await fetch('/api/ai-tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, title, description })
        });
        const data = await res.json();
        return data.tags || [];
    }

    // ============================================================
    // ADD COLLECTION MODAL
    // ============================================================
    addCollectionBtn.addEventListener('click', () => {
        document.getElementById('addCollectionModal').classList.add('open');
    });
    cancelCollectionBtn.addEventListener('click', () => {
        document.getElementById('addCollectionModal').classList.remove('open');
    });

    saveCollectionBtn.addEventListener('click', () => {
        const name = collectionNameInput.value.trim();
        if (!name) { alert('Please enter a collection name.'); return; }
        addCollection(name);
        document.getElementById('addCollectionModal').classList.remove('open');
        collectionNameInput.value = '';
        renderLinks(getCurrentFilter(), getCurrentSearch());
    });

    // ============================================================
    // AUTHENTICATION
    // ============================================================
    function showLogin() {
        loginForm.style.display = 'block';
        registerForm.style.display = 'none';
        loginSubtext.textContent = 'Sign in to access your bookmarks';
        toggleAuthLink.textContent = "Don't have an account? Sign up";
        loginError.textContent = '';
    }

    function showRegister() {
        loginForm.style.display = 'none';
        registerForm.style.display = 'block';
        loginSubtext.textContent = 'Create your FOXY LINKS account';
        toggleAuthLink.textContent = 'Already have an account? Sign in';
        registerError.textContent = '';
    }

    toggleAuthLink.addEventListener('click', showRegister);
    toggleLoginLink.addEventListener('click', showLogin);

    loginSubmitBtn.addEventListener('click', async () => {
        const email = loginEmail.value.trim();
        const password = loginPassword.value.trim();
        if (!email || !password) {
            loginError.textContent = 'Please fill in all fields.';
            return;
        }
        loginError.textContent = 'Checking...';
        try {
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();
            if (data.success) {
                userData = { id: data.user_id };
                setLS('user', JSON.stringify(userData));
                loginOverlay.classList.add('hidden');
                updateUIForLoggedIn();
                loadFromGitHub().then(() => {
                    renderLinks();
                    showReminder();
                });
            } else {
                loginError.textContent = data.error || 'Invalid credentials';
            }
        } catch (e) {
            loginError.textContent = 'Network error. Try again.';
        }
    });

    registerSubmitBtn.addEventListener('click', async () => {
        const email = registerEmail.value.trim();
        const password = registerPassword.value.trim();
        if (!email || !password) {
            registerError.textContent = 'Please fill in all fields.';
            return;
        }
        registerError.textContent = 'Creating account...';
        try {
            const res = await fetch('/api/signup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();
            if (data.success) {
                userData = { id: data.user_id };
                setLS('user', JSON.stringify(userData));
                loginOverlay.classList.add('hidden');
                updateUIForLoggedIn();
                loadFromGitHub().then(() => {
                    renderLinks();
                    showReminder();
                });
            } else {
                registerError.textContent = data.error || 'Signup failed';
            }
        } catch (e) {
            registerError.textContent = 'Network error. Try again.';
        }
    });

    function updateUIForLoggedIn() {
        if (userData) {
            loginBtn.style.display = 'none';
            logoutBtn.style.display = 'block';
            userName.textContent = 'User';
            userAvatar.src = 'https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png';
            syncStatus.textContent = '✅ Synced with GitHub';
        } else {
            loginBtn.style.display = 'block';
            logoutBtn.style.display = 'none';
            userName.textContent = 'Not logged in';
            userAvatar.src = 'https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png';
            syncStatus.textContent = '⬇️ Synced with GitHub';
        }
    }

    logoutBtn.addEventListener('click', () => {
        removeLS('user');
        userData = null;
        links = [];
        collections = ['General', 'Foxy Apps', 'Development', 'Inspiration', 'Work'];
        renderLinks();
        updateUIForLoggedIn();
        loginOverlay.classList.remove('hidden');
        showLogin();
    });

    // ============================================================
    // INIT
    // ============================================================
    if (userData) {
        loginOverlay.classList.add('hidden');
        updateUIForLoggedIn();
        loadFromGitHub().then(() => {
            renderLinks();
            showReminder();
        });
    } else {
        loginOverlay.classList.remove('hidden');
        showLogin();
        // Show sample links to make the page look nice
        links = [
            { id: '1', title: 'FOXY FLOW - Remote Media Player', url: 'https://foxy-flow.onrender.com', description: 'Cast audio from phone to TV', tags: ['foxy', 'audio'], collection: 'Foxy Apps', read_later: false },
            { id: '2', title: 'FOXY KITCHEN - AI Cooking Assistant', url: 'https://foxy-kitchen.onrender.com', description: 'AI-powered recipe search', tags: ['foxy', 'cooking'], collection: 'Foxy Apps', read_later: false },
        ];
        renderLinks();
    }
</script>
</body>
</html>
"""

# ========== FLASK ROUTES ==========

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, default_styles=DEFAULT_STYLES)

@app.route('/api/fetch-meta', methods=['GET'])
@rate_limit(limit=20, window=60)
def fetch_meta_api():
    url = request.args.get('url', '')
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Invalid URL"}), 400
    try:
        meta = fetch_meta(url)
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ai-tags', methods=['POST'])
@rate_limit(limit=10, window=60)
def ai_tags_api():
    data = request.json
    url = data.get('url', '')
    title = data.get('title', '')
    description = data.get('description', '')
    if not url or not is_valid_url(url):
        return jsonify({"error": "Valid URL required"}), 400
    try:
        tags = ai_tag(url, title, description)
        return jsonify({"tags": tags})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/generate-css', methods=['POST'])
@rate_limit(limit=5, window=60)
def generate_css():
    data = request.json
    prompt = data.get('prompt', '')
    current_css = data.get('current_css', DEFAULT_STYLES)
    if not prompt:
        return jsonify({"error": "Prompt required"}), 400
    try:
        new_css = generate_css_from_prompt(prompt, current_css)
        if new_css:
            return jsonify({"css": new_css})
        return jsonify({"css": current_css, "warning": "AI generation failed, using current styles"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync', methods=['POST'])
@rate_limit(limit=10, window=60)
def sync_api():
    data = request.json
    user_id = data.get('user_id')
    user_data = data.get('data', {})
    if not user_id:
        return jsonify({"success": False, "error": "No user ID"}), 400
    try:
        success = write_user_data(user_id, user_data)
        if success:
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Failed to write to repo"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/load', methods=['POST'])
@rate_limit(limit=20, window=60)
def load_api():
    data = request.json
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "No user ID"}), 400
    try:
        user_data = read_user_data(user_id)
        if user_data:
            return jsonify({"success": True, "data": user_data})
        return jsonify({"success": True, "data": {"links": [], "collections": []}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/signup', methods=['POST'])
@rate_limit(limit=5, window=60)
def signup():
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({"success": False, "error": "Email and password required"}), 400
    if not is_valid_email(email):
        return jsonify({"success": False, "error": "Invalid email format"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400

    users = read_users_db()
    if email in users:
        return jsonify({"success": False, "error": "User already exists"}), 400

    user_id = generate_user_id()
    salt, key = hash_password(password)

    users[email] = {
        "salt": salt,
        "key": key,
        "user_id": user_id
    }
    if not write_users_db(users):
        return jsonify({"success": False, "error": "Failed to save user"}), 500

    if not write_user_data(user_id, {"links": [], "collections": ['General', 'Foxy Apps', 'Development', 'Inspiration', 'Work'], "chromeColor": "#ff6600", "theme": "dark"}):
        return jsonify({"success": False, "error": "Failed to create user data"}), 500

    return jsonify({"success": True, "user_id": user_id})

@app.route('/api/login', methods=['POST'])
@rate_limit(limit=10, window=60)
def login():
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({"success": False, "error": "Email and password required"}), 400
    if not is_valid_email(email):
        return jsonify({"success": False, "error": "Invalid email format"}), 400

    users = read_users_db()
    if email not in users:
        return jsonify({"success": False, "error": "User not found"}), 404

    record = users[email]
    if not verify_password(record['salt'], record['key'], password):
        return jsonify({"success": False, "error": "Invalid password"}), 401

    return jsonify({"success": True, "user_id": record['user_id']})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
