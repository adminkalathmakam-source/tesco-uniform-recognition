import http.server
import socketserver
import urllib.parse
import json
import os
import time
import secrets
import socket
import threading
from datetime import datetime
from pathlib import Path
import firebase_service

# Initialize Firebase Admin SDK safely
try:
    firebase_service.initialize_firebase()
except Exception as e:
    print(f"[Warning] Firebase initialization failed: {e}")


PORT = 5000
SYSTEM_NAME = "VeriUniform Sentinel"
SYSTEM_TAGLINE = "Tesco & TRO Biometric Uniform Intelligence Platform"

# User credentials for TRO Officers and Teachers
USERS = {
    "tro_admin": {
        "password": "tro2026",
        "name": "TRO Officer Admin",
        "role": "TRO Officer"
    },
    "teacher": {
        "password": "school123",
        "name": "Class Teacher",
        "role": "Teacher"
    }
}

# In-memory active sessions: token -> { username, name, role, expires }
sessions = {}

def create_session(username):
    token = secrets.token_hex(24)
    sessions[token] = {
        "username": username,
        "name": USERS[username]["name"],
        "role": USERS[username]["role"],
        "expires": time.time() + 86400  # 24 hours
    }
    return token

def validate_session(token):
    if not token or token not in sessions:
        return None
    sess = sessions[token]
    if time.time() > sess["expires"]:
        del sessions[token]
        return None
    return sess

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_available_dates():
    dates_set = set()
    
    # Query distinct dates from Firebase Firestore safely
    try:
        fb_dates = firebase_service.get_available_firebase_dates()
        for fd in fb_dates:
            dates_set.add(fd)
    except Exception as e:
        print(f"Error fetching Firebase dates: {e}")

    # Query local disk log directories
    try:
        log_root = Path("logs")
        if log_root.exists():
            for d in log_root.iterdir():
                if d.is_dir() and (d / "attendance.txt").exists():
                    dates_set.add(d.name)
    except Exception as e:
        print(f"Error scanning local logs: {e}")

    today = datetime.now().strftime("%Y-%m-%d")
    dates_set.add(today)
    return sorted(list(dates_set), reverse=True)

def parse_attendance_log(date_str):
    # Attempt to fetch attendance scans from Firebase Firestore first
    try:
        fb_stats, fb_records = firebase_service.fetch_attendance_from_firebase(date_str)
        if fb_stats is not None and fb_records is not None and len(fb_records) > 0:
            return fb_stats, fb_records
    except Exception as e:
        print(f"Firebase fetch error in parse_attendance_log: {e}")

    # Fallback to local disk file if Firebase has no records or is unreachable
    log_file = Path(f"logs/{date_str}/attendance.txt")
    records = []
    scanned_names = set()
    full_pass_count = 0
    uniform_missing_count = 0
    badge_missing_count = 0

    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not (line.startswith("[") and "]" in line and " - " in line):
                        continue
                    
                    time_part = line[1:line.find("]")]
                    rest = line[line.find("] ") + 2:]
                    if " - " not in rest:
                        continue
                        
                    name_part, details = rest.split(" - ", 1)
                    name = name_part.strip()
                    scanned_names.add(name)

                    # Determine uniform status
                    wearing_uniform = False
                    if "(PASS)" in details or "Uniform: Complete" in details or "Uniform: PASS" in details:
                        wearing_uniform = True
                    elif "(FAIL)" in details:
                        wearing_uniform = False
                    elif "Uniform:" in details:
                        try:
                            pct_str = details.split("Uniform:")[1].split("%")[0].strip()
                            uniform_pct = float(pct_str)
                            wearing_uniform = uniform_pct >= 40.0
                        except Exception:
                            wearing_uniform = False

                    # Extract badge status
                    has_badge = True
                    if "Badge: NO" in details or "NO BADGE" in details:
                        has_badge = False
                    elif "Badge: YES" in details:
                        has_badge = True

                    # Calculate verdict
                    if wearing_uniform and has_badge:
                        result = "Fully Compliant"
                        full_pass_count += 1
                    elif wearing_uniform and not has_badge:
                        result = "Badge Missing"
                        badge_missing_count += 1
                    else:
                        result = "Uniform Missing"
                        uniform_missing_count += 1

                    records.append({
                        "time": time_part,
                        "name": name,
                        "uniform_status": "Uniform Complete" if wearing_uniform else "Uniform Missing",
                        "uniform_missing": not wearing_uniform,
                        "badge_status": "Badge Verified" if has_badge else "Badge Missing",
                        "badge_missing": not has_badge,
                        "result": result
                    })
        except Exception as e:
            print(f"Error parsing log file: {e}")

    # Total registered students from face_encodings.pkl if available
    total_registered = 15
    try:
        import pickle
        if os.path.exists("face_encodings.pkl"):
            with open("face_encodings.pkl", "rb") as f:
                d = pickle.load(f)
                total_registered = len(d.get("names", []))
    except Exception:
        pass

    total_scanned = len(scanned_names)
    compliance_rate = round((full_pass_count / total_scanned * 100), 1) if total_scanned > 0 else 0.0

    stats = {
        "date": date_str,
        "total_registered": total_registered,
        "total_scanned": total_scanned,
        "full_pass": full_pass_count,
        "uniform_missing_count": uniform_missing_count,
        "badge_missing_count": badge_missing_count,
        "compliance_rate": compliance_rate,
        "data_source": "Local Disk Log"
    }
    return stats, records


LOGIN_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{SYSTEM_NAME} — Biometric Access</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --bg: #050811;
            --card-bg: rgba(13, 20, 36, 0.78);
            --card-border: rgba(56, 189, 248, 0.18);
            --primary: #0284c7;
            --primary-glow: rgba(14, 165, 233, 0.4);
            --accent-cyan: #38bdf8;
            --accent-emerald: #10b981;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --input-bg: rgba(7, 12, 24, 0.85);
            --input-border: rgba(148, 163, 184, 0.18);
        }}
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        .login-card {{
            width: 100%;
            max-width: 440px;
            background: var(--card-bg);
            backdrop-filter: blur(24px);
            border: 1px solid var(--card-border);
            border-radius: 28px;
            padding: 44px 36px;
            box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.8);
        }}
        .title {{ font-size: 1.85rem; font-weight: 800; color: #ffffff; margin-bottom: 6px; }}
        .subtitle {{ color: var(--text-muted); font-size: 0.88rem; margin-bottom: 28px; }}
        .form-group {{ margin-bottom: 20px; }}
        label {{ display: block; font-size: 0.8rem; font-weight: 600; color: #cbd5e1; margin-bottom: 8px; }}
        input {{
            width: 100%; background: var(--input-bg); border: 1px solid var(--input-border);
            border-radius: 14px; padding: 13px 16px; font-size: 0.92rem; color: #f8fafc; outline: none;
        }}
        input:focus {{ border-color: var(--accent-cyan); }}
        .btn-submit {{
            width: 100%; background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
            border: none; color: #ffffff; font-weight: 700; padding: 14px; border-radius: 14px;
            cursor: pointer; margin-top: 8px; font-size: 0.95rem;
        }}
        .error-banner {{
            background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35);
            color: #fca5a5; padding: 12px 16px; border-radius: 12px; font-size: 0.82rem;
            margin-bottom: 20px; display: none;
        }}
        .quick-section {{ margin-top: 28px; padding-top: 22px; border-top: 1px solid rgba(255, 255, 255, 0.07); }}
        .quick-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
        .quick-chip {{
            background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(148, 163, 184, 0.14);
            border-radius: 12px; padding: 10px 12px; cursor: pointer; text-align: left;
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <h1 class="title">{SYSTEM_NAME}</h1>
        <p class="subtitle">{SYSTEM_TAGLINE}</p>
        <div id="errorBanner" class="error-banner"></div>
        <form id="loginForm">
            <div class="form-group">
                <label>Authorized Username</label>
                <input type="text" id="username" placeholder="e.g. tro_admin" required>
            </div>
            <div class="form-group">
                <label>Security Credentials</label>
                <input type="password" id="password" placeholder="••••••••" required>
            </div>
            <button type="submit" id="submitBtn" class="btn-submit">Authenticate to Portal</button>
        </form>
        <div class="quick-section">
            <div style="font-size:0.75rem; color:#64748b; text-transform:uppercase; margin-bottom:10px; font-weight:700;">Quick Demo Login</div>
            <div class="quick-grid">
                <div class="quick-chip" onclick="fillCreds('tro_admin', 'tro2026')">
                    <strong style="display:block; color:#e2e8f0; font-size:0.8rem;">👮 TRO Officer</strong>
                    <span style="font-size:0.7rem; color:#94a3b8;">Admin Access</span>
                </div>
                <div class="quick-chip" onclick="fillCreds('teacher', 'school123')">
                    <strong style="display:block; color:#e2e8f0; font-size:0.8rem;">👩‍🏫 Teacher</strong>
                    <span style="font-size:0.7rem; color:#94a3b8;">Classroom View</span>
                </div>
            </div>
        </div>
    </div>
    <script>
        function fillCreds(u, p) {
            document.getElementById('username').value = u;
            document.getElementById('password').value = p;
        }
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const err = document.getElementById('errorBanner');
            btn.disabled = true;
            err.style.display = 'none';
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: document.getElementById('username').value.trim(),
                        password: document.getElementById('password').value.trim()
                    })
                });
                const data = await res.json();
                if (data.success) {
                    window.location.href = '/';
                } else {
                    err.innerText = data.error || "Authentication failed.";
                    err.style.display = 'block';
                    btn.disabled = false;
                }
            } catch (error) {
                err.innerText = "Network connection error.";
                err.style.display = 'block';
                btn.disabled = false;
            }
        });
    </script>
</body>
</html>
"""


DASHBOARD_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{SYSTEM_NAME} — Live Compliance Sentinel</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&family=Plus+Jakarta+Sans:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --bg: #050811; --surface: #0a1122; --surface-card: rgba(14, 23, 42, 0.72);
            --border: rgba(56, 189, 248, 0.12); --text-main: #f8fafc; --text-muted: #94a3b8;
            --cyan: #38bdf8; --emerald: #10b981; --rose: #f43f5e; --amber: #f59e0b;
        }}
        body {{ font-family: 'Plus Jakarta Sans', sans-serif; background: var(--bg); color: var(--text-main); min-height: 100vh; display: flex; flex-direction: column; }}
        header {{
            position: sticky; top: 0; z-index: 50; background: rgba(7, 13, 27, 0.88);
            backdrop-filter: blur(20px); border-bottom: 1px solid var(--border); padding: 0 32px; height: 72px;
            display: flex; align-items: center; justify-content: space-between;
        }}
        main {{ flex: 1; max-width: 1400px; width: 100%; margin: 0 auto; padding: 32px; display: flex; flex-direction: column; gap: 28px; }}
        .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 18px; }}
        .kpi-card {{ background: var(--surface-card); border: 1px solid var(--border); border-radius: 22px; padding: 24px; }}
        .toolbar {{ background: var(--surface-card); border: 1px solid var(--border); border-radius: 20px; padding: 18px 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; }}
        .table-card {{ background: var(--surface-card); border: 1px solid var(--border); border-radius: 24px; overflow: hidden; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th, td {{ padding: 16px 22px; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 0.88rem; }}
        th {{ font-size: 0.74rem; color: #94a3b8; text-transform: uppercase; background: rgba(11, 18, 35, 0.95); }}
        .status-pill {{ display: inline-flex; padding: 6px 12px; border-radius: 9999px; font-size: 0.78rem; font-weight: 700; }}
        .pill-pass {{ background: rgba(16,185,129,0.14); color: #34d399; border: 1px solid rgba(16,185,129,0.35); }}
        .pill-fail {{ background: rgba(244,63,94,0.14); color: #fb7185; border: 1px solid rgba(244,63,94,0.35); }}
        .btn-header {{ background: rgba(15, 23, 42, 0.8); border: 1px solid var(--border); color: #e2e8f0; padding: 9px 16px; border-radius: 12px; cursor: pointer; }}
    </style>
</head>
<body>
    <header>
        <div style="display:flex; align-items:center; gap:14px;">
            <div style="font-size:1.35rem;">🛡️</div>
            <div>
                <div style="font-family:'Outfit'; font-weight:800; font-size:1.2rem;">{SYSTEM_NAME}</div>
                <div style="font-size:0.74rem; color:var(--text-muted);">{SYSTEM_TAGLINE}</div>
            </div>
        </div>
        <div style="display:flex; gap:12px; align-items:center;">
            <button class="btn-header" onclick="logout()">🚪 Sign Out</button>
        </div>
    </header>

    <main>
        <div class="kpi-grid">
            <div class="kpi-card">
                <div style="color:var(--text-muted); font-size:0.8rem; font-weight:700;">TOTAL SCANNED</div>
                <div style="font-family:'Outfit'; font-size:2.2rem; font-weight:800; color:var(--cyan); margin-top:8px;" id="statScanned">0</div>
            </div>
            <div class="kpi-card">
                <div style="color:var(--text-muted); font-size:0.8rem; font-weight:700;">FULLY COMPLIANT</div>
                <div style="font-family:'Outfit'; font-size:2.2rem; font-weight:800; color:var(--emerald); margin-top:8px;" id="statPass">0</div>
            </div>
            <div class="kpi-card">
                <div style="color:var(--text-muted); font-size:0.8rem; font-weight:700;">COMPLIANCE RATE</div>
                <div style="font-family:'Outfit'; font-size:2.2rem; font-weight:800; color:var(--amber); margin-top:8px;" id="statRate">0%</div>
            </div>
        </div>

        <div class="toolbar">
            <div style="font-weight:700;">Attendance Telemetry</div>
            <select id="datePicker" style="background:#070c18; border:1px solid var(--border); color:#fff; padding:8px 12px; border-radius:10px;" onchange="onDateChange()"></select>
        </div>

        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Student Name</th>
                        <th>Uniform Status</th>
                        <th>Badge Status</th>
                        <th>Verdict</th>
                    </tr>
                </thead>
                <tbody id="recordsBody">
                    <tr><td colspan="5" style="text-align:center; padding:30px; color:var(--text-muted);">Loading telemetry...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <script>
        let currentDate = '';

        async function init() {
            try {
                const res = await fetch('/api/attendance');
                if (res.status === 401 || res.status === 302 || res.redirected) {
                    window.location.href = '/login';
                    return;
                }
                const data = await res.json();
                if (!data.selected_date) {
                    window.location.href = '/login';
                    return;
                }
                currentDate = data.selected_date;

                const dateSelect = document.getElementById('datePicker');
                dateSelect.innerHTML = '';
                (data.available_dates || []).forEach(d => {
                    const opt = document.createElement('option');
                    opt.value = d;
                    opt.text = d;
                    if (d === currentDate) opt.selected = true;
                    dateSelect.appendChild(opt);
                });

                renderData(data.stats, data.records);
            } catch (e) {
                console.error("Init error:", e);
            }
        }

        function renderData(stats, records) {
            document.getElementById('statScanned').innerText = stats.total_scanned || 0;
            document.getElementById('statPass').innerText = stats.full_pass || 0;
            document.getElementById('statRate').innerText = (stats.compliance_rate || 0) + '%';

            const tbody = document.getElementById('recordsBody');
            if (!records || records.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:30px; color:var(--text-muted);">No attendance records found for this date.</td></tr>`;
                return;
            }

            tbody.innerHTML = records.map(r => `
                <tr>
                    <td style="font-family:'JetBrains Mono';">${{r.time}}</td>
                    <td><strong>${{r.name}}</strong></td>
                    <td><span class="status-pill ${{r.uniform_missing ? 'pill-fail' : 'pill-pass'}}">${{r.uniform_status}}</span></td>
                    <td><span class="status-pill ${{r.badge_missing ? 'pill-fail' : 'pill-pass'}}">${{r.badge_status}}</span></td>
                    <td><strong style="color:${{r.result === 'Fully Compliant' ? 'var(--emerald)' : 'var(--rose)'}};">${{r.result}}</strong></td>
                </tr>
            `).join('');
        }

        async function onDateChange() {
            currentDate = document.getElementById('datePicker').value;
            try {
                const res = await fetch('/api/attendance?date=' + encodeURIComponent(currentDate));
                if (res.ok) {
                    const data = await res.json();
                    renderData(data.stats, data.records);
                }
            } catch (e) {}
        }

        async function logout() {
            await fetch('/api/logout', { method: 'POST' });
            window.location.href = '/login';
        }

        // Stable polling interval (every 8 seconds) with error bounds to prevent feedback loops
        setInterval(async () => {
            const today = new Date().toISOString().split('T')[0];
            if (currentDate === today) {
                try {
                    const res = await fetch('/api/attendance?date=' + encodeURIComponent(today));
                    if (res.status === 401 || res.status === 302) {
                        window.location.href = '/login';
                        return;
                    }
                    if (res.ok) {
                        const data = await res.json();
                        renderData(data.stats, data.records);
                    }
                } catch (e) {
                    // Suppress network blip errors quietly
                }
            }
        }, 8000);

        init();
    </script>
</body>
</html>
"""


class TROPortalHandler(http.server.BaseHTTPRequestHandler):
    def get_cookie(self, name):
        try:
            cookie_header = self.headers.get("Cookie", "")
            for part in cookie_header.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip() == name:
                        return v.strip()
        except Exception:
            pass
        return None

    def get_authenticated_user(self):
        token = self.get_cookie("session_token")
        return validate_session(token)

    def send_json(self, status, payload):
        try:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            if path == "/login":
                user = self.get_authenticated_user()
                if user:
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                content = LOGIN_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

            user = self.get_authenticated_user()
            if not user:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if path == "/":
                content = DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

            elif path == "/api/attendance":
                today = datetime.now().strftime("%Y-%m-%d")
                available_dates = get_available_dates()
                default_date = today if today in available_dates else (available_dates[0] if available_dates else today)
                date_req = query.get("date", [default_date])[0]
                
                stats, records = parse_attendance_log(date_req)
                self.send_json(200, {
                    "user": {"username": user["username"], "name": user["name"], "role": user["role"]},
                    "today": today,
                    "selected_date": date_req,
                    "available_dates": available_dates,
                    "stats": stats,
                    "records": records
                })
                return

            else:
                self.send_error(404, "Page Not Found")
        except Exception as e:
            print(f"Error handling GET request: {e}")
            try:
                self.send_error(500, "Internal Server Error")
            except Exception:
                pass

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/api/login":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                username = data.get("username", "").strip()
                password = data.get("password", "").strip()

                if username in USERS and USERS[username]["password"] == password:
                    token = create_session(username)
                    resp_data = json.dumps({"success": True, "user": USERS[username]["name"]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Set-Cookie", f"session_token={token}; Path=/; HttpOnly; Max-Age=86400")
                    self.send_header("Content-Length", str(len(resp_data)))
                    self.end_headers()
                    self.wfile.write(resp_data)
                else:
                    self.send_json(401, {"success": False, "error": "Invalid username or password"})
                return

            elif path == "/api/logout":
                token = self.get_cookie("session_token")
                if token and token in sessions:
                    del sessions[token]
                resp_data = json.dumps({"success": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "session_token=deleted; Path=/; Max-Age=0")
                self.send_header("Content-Length", str(len(resp_data)))
                self.end_headers()
                self.wfile.write(resp_data)
                return

            else:
                self.send_error(404, "Endpoint Not Found")
        except Exception as e:
            print(f"Error handling POST request: {e}")
            self.send_json(500, {"success": False, "error": str(e)})

    def log_message(self, format, *args):
        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_web_server(port=PORT, background=False):
    server = ThreadingHTTPServer(("0.0.0.0", port), TROPortalHandler)
    local_ip = get_local_ip()
    print(f"\n[ONLINE] {SYSTEM_NAME} Server Active at http://localhost:{port} (Network: http://{local_ip}:{port})\n")
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    start_web_server(port=PORT, background=False)