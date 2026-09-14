import http.server
import socketserver
import os
import json
import urllib.parse
import uuid
import time

PORT = int(os.environ.get('PORT', 5000))
HOST = '0.0.0.0'

# In-memory session store & user credentials
sessions = {}
USERS = {
    'tro_admin': {'password': 'tro2026', 'role': 'TRO Officer'},
    'teacher': {'password': 'school123', 'role': 'Teacher'}
}

# Ensure logs directory exists
os.makedirs('logs', exist_ok=True)

class VeriUniformHandler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Cookie')
        self.end_headers()

    def get_cookies(self):
        cookie_header = self.headers.get('Cookie')
        cookies = {}
        if cookie_header:
            for item in cookie_header.split(';'):
                if '=' in item:
                    k, v = item.strip().split('=', 1)
                    cookies[k] = v
        return cookies

    def is_authenticated(self):
        cookies = self.get_cookies()
        token = cookies.get('session_token')
        if token and token in sessions:
            if time.time() - sessions[token]['timestamp'] < 86400: # 24 hours
                return True
            else:
                del sessions[token]
        return False

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path

        # API Endpoints
        if path == '/api/attendance':
            self.send_json({'status': 'success', 'records': [], 'date': time.strftime('%Y-%m-%d')})
            return

        elif path == '/api/firebase/status':
            self.send_json({'connected': True, 'mode': 'firestore_fallback_ready'})
            return

        elif path == '/api/export-csv':
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv')
            self.send_header('Content-Disposition', 'attachment; filename="attendance.csv"')
            self.end_headers()
            self.wfile.write(b"timestamp,student_id,name,status,uniform_compliant\n")
            return

        # Page Routes
        if path == '/login':
            self.serve_login_page()
            return

        if not self.is_authenticated():
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return

        if path == '/' or path == '/index.html':
            self.serve_dashboard_page()
            return

        # Fallback to static files
        return super().do_GET()

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = {}
        if body:
            try:
                data = json.loads(body)
            except:
                data = dict(urllib.parse.parse_qsl(body))

        if path == '/api/login':
            username = data.get('username')
            password = data.get('password')
            if username in USERS and USERS[username]['password'] == password:
                token = str(uuid.uuid4())
                sessions[token] = {
                    'username': username,
                    'role': USERS[username]['role'],
                    'timestamp': time.time()
                }
                self.send_response(200)
                self.send_header('Set-Cookie', f'session_token={token}; Path=/; HttpOnly; SameSite=Lax')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'success', 'role': USERS[username]['role']}).encode())
            else:
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': 'Invalid credentials'}).encode())
            return

        elif path == '/api/logout':
            cookies = self.get_cookies()
            token = cookies.get('session_token')
            if token in sessions:
                del sessions[token]
            self.send_response(200)
            self.send_header('Set-Cookie', 'session_token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'logged_out'}).encode())
            return

        elif path == '/api/override-status':
            self.send_json({'status': 'updated'})
            return

        self.send_response(404)
        self.end_headers()

    def send_json(self, payload):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

    def serve_login_page(self):
        html = """<!DOCTYPE html>
        <html>
        <head>
            <title>VeriUniform Sentinel - Login</title>
            <style>
                body { font-family: sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
                .card { background: #1e293b; padding: 2rem; border-radius: 0.5rem; width: 320px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.5); }
                input { width: 100%; padding: 0.75rem; margin-bottom: 1rem; border-radius: 0.25rem; border: 1px. solid #334155; background: #0f172a; color: white; box-sizing: border-box; }
                button { width: 100%; padding: 0.75rem; background: #3b82f6; color: white; border: none; border-radius: 0.25rem; cursor: pointer; font-weight: bold; }
                button:hover { background: #2563eb; }
                .demo-pills { margin-top: 1rem; font-size: 0.8rem; color: #94a3b8; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>VeriUniform Sentinel</h2>
                <form id="loginForm">
                    <input type="text" id="username" placeholder="Username" required value="tro_admin">
                    <input type="password" id="password" placeholder="Password" required value="tro2026">
                    <button type="submit">Sign In</button>
                </form>
                <div class="demo-pills">
                    Admin: tro_admin / tro2026<br>
                    Teacher: teacher / school123
                </div>
            </div>
            <script>
                document.getElementById('loginForm').onsubmit = async (e) => {
                    e.preventDefault();
                    const res = await fetch('/api/login', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            username: document.getElementById('username').value,
                            password: document.getElementById('password').value
                        })
                    });
                    if (res.ok) { window.location.href = '/'; }
                    else { alert('Login failed'); }
                };
            </script>
        </body>
        </html>"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_dashboard_page(self):
        html = """<!DOCTYPE html>
        <html>
        <head>
            <title>VeriUniform Sentinel Dashboard</title>
            <style>
                body { font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; margin: 0; }
                header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 1rem; }
                .logout-btn { background: #ef4444; color: white; border: none; padding: 0.5rem 1rem; border-radius: 0.25rem; cursor: pointer; }
                .dashboard { margin-top: 2rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1rem; }
                .card { background: #1e293b; padding: 1.5rem; border-radius: 0.5rem; }
            </style>
        </head>
        <body>
            <header>
                <h1>VeriUniform Sentinel Dashboard</h1>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </header>
            <div class="dashboard">
                <div class="card">
                    <h3>System Status</h3>
                    <p id="statusText">Checking connection...</p>
                </div>
                <div class="card">
                    <h3>Compliance Feed</h3>
                    <p>Live biometric stream active.</p>
                </div>
            </div>
            <script>
                async function checkStatus() {
                    const res = await fetch('/api/firebase/status');
                    const data = await res.json();
                    document.getElementById('statusText').innerText = data.connected ? 'Online (' + data.mode + ')' : 'Offline';
                }
                async function logout() {
                    await fetch('/api/logout', { method: 'POST' });
                    window.location.href = '/login';
                }
                checkStatus();
            </script>
        </body>
        </html>"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    print(f"Starting VeriUniform Sentinel on {HOST}:{PORT}")
    with ThreadedHTTPServer((HOST, PORT), VeriUniformHandler) as server:
        server.serve_forever()
