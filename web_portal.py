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

# Initialize Firebase Admin SDK
firebase_service.initialize_firebase()


PORT = int(os.environ.get("PORT", 5000))
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
    
    # Query distinct dates from Firebase Firestore
    fb_dates = firebase_service.get_available_firebase_dates()
    for fd in fb_dates:
        dates_set.add(fd)

    # Query local disk log directories
    log_root = Path("logs")
    if log_root.exists():
        for d in log_root.iterdir():
            if d.is_dir() and (d / "attendance.txt").exists():
                dates_set.add(d.name)

    today = datetime.now().strftime("%Y-%m-%d")
    dates_set.add(today)
    return sorted(list(dates_set), reverse=True)

def parse_attendance_log(date_str):
    # Attempt to fetch attendance scans from Firebase Firestore first
    fb_stats, fb_records = firebase_service.fetch_attendance_from_firebase(date_str)
    if fb_stats is not None and fb_records is not None and len(fb_records) > 0:
        return fb_stats, fb_records

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

                    # Determine uniform status (supporting legacy % logs and status logs)
                    wearing_uniform = False
                    if "(PASS)" in details:
                        wearing_uniform = True
                    elif "(FAIL)" in details:
                        wearing_uniform = False
                    elif "Uniform: Complete" in details or "Uniform: PASS" in details:
                        wearing_uniform = True
                    elif "Uniform:" in details:
                        try:
                            pct_str = details.split("Uniform:")[1].split("%")[0].strip()
                            uniform_pct = float(pct_str)
                            # 40%+ uniform coverage counts as wearing full uniform
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
            position: relative;
            overflow-x: hidden;
        }}

        /* Dynamic background glow spheres */
        .glow-sphere {{
            position: fixed;
            border-radius: 50%;
            filter: blur(100px);
            pointer-events: none;
            z-index: 0;
            opacity: 0.55;
            animation: pulseGlow 14s infinite alternate ease-in-out;
        }}
        .sphere-1 {{ width: 500px; height: 500px; background: radial-gradient(circle, rgba(14,165,233,0.3) 0%, transparent 70%); top: -120px; left: -100px; }}
        .sphere-2 {{ width: 450px; height: 450px; background: radial-gradient(circle, rgba(99,102,241,0.25) 0%, transparent 70%); bottom: -100px; right: -80px; animation-delay: -6s; }}
        .sphere-3 {{ width: 350px; height: 350px; background: radial-gradient(circle, rgba(16,185,129,0.18) 0%, transparent 70%); top: 40%; left: 60%; animation-delay: -10s; }}

        @keyframes pulseGlow {{
            0% {{ transform: translate(0, 0) scale(1); }}
            100% {{ transform: translate(40px, 30px) scale(1.12); }}
        }}

        .login-card {{
            position: relative;
            z-index: 1;
            width: 100%;
            max-width: 440px;
            background: var(--card-bg);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid var(--card-border);
            border-radius: 28px;
            padding: 44px 36px;
            box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.8), 0 0 40px -10px rgba(14, 165, 233, 0.15);
        }}

        .brand-badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(14, 165, 233, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: var(--accent-cyan);
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            padding: 6px 14px;
            border-radius: 9999px;
            margin-bottom: 20px;
        }}
        .brand-badge span {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--accent-cyan);
            box-shadow: 0 0 10px var(--accent-cyan);
            animation: blink 2s infinite;
        }}
        @keyframes blink {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.3; }}
        }}

        .title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.85rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            color: #ffffff;
            margin-bottom: 6px;
        }}
        .subtitle {{
            color: var(--text-muted);
            font-size: 0.88rem;
            line-height: 1.5;
            margin-bottom: 28px;
        }}

        .form-group {{ margin-bottom: 20px; }}
        label {{
            display: block;
            font-size: 0.8rem;
            font-weight: 600;
            color: #cbd5e1;
            margin-bottom: 8px;
            letter-spacing: 0.01em;
        }}
        .input-wrapper {{
            position: relative;
        }}
        input {{
            width: 100%;
            background: var(--input-bg);
            border: 1px solid var(--input-border);
            border-radius: 14px;
            padding: 13px 16px;
            font-size: 0.92rem;
            color: #f8fafc;
            outline: none;
            transition: all 0.25s ease;
            font-family: inherit;
        }}
        input:focus {{
            border-color: var(--accent-cyan);
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
            background: rgba(11, 18, 35, 0.95);
        }}

        .btn-submit {{
            width: 100%;
            background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
            border: 1px solid rgba(56, 189, 248, 0.4);
            color: #ffffff;
            font-weight: 700;
            font-size: 0.95rem;
            padding: 14px;
            border-radius: 14px;
            cursor: pointer;
            transition: all 0.25s ease;
            box-shadow: 0 8px 24px -6px var(--primary-glow);
            margin-top: 8px;
            font-family: 'Outfit', sans-serif;
            letter-spacing: 0.02em;
        }}
        .btn-submit:hover {{
            transform: translateY(-2px);
            box-shadow: 0 12px 28px -4px rgba(14, 165, 233, 0.5);
            background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%);
        }}
        .btn-submit:active {{ transform: translateY(0); }}

        .error-banner {{
            background: rgba(239, 68, 68, 0.12);
            border: 1px solid rgba(239, 68, 68, 0.35);
            color: #fca5a5;
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 0.82rem;
            font-weight: 500;
            margin-bottom: 20px;
            display: none;
        }}

        /* One-Click Quick Login Pills */
        .quick-section {{
            margin-top: 28px;
            padding-top: 22px;
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }}
        .quick-title {{
            font-size: 0.75rem;
            font-weight: 700;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 12px;
        }}
        .quick-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }}
        .quick-chip {{
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid rgba(148, 163, 184, 0.14);
            border-radius: 12px;
            padding: 10px 12px;
            text-align: left;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .quick-chip:hover {{
            background: rgba(30, 41, 59, 0.85);
            border-color: rgba(56, 189, 248, 0.35);
            transform: translateY(-1px);
        }}
        .quick-role {{
            font-size: 0.8rem;
            font-weight: 700;
            color: #e2e8f0;
            display: block;
        }}
        .quick-sub {{
            font-size: 0.7rem;
            color: #94a3b8;
        }}
    </style>
</head>
<body>
    <div class="glow-sphere sphere-1"></div>
    <div class="glow-sphere sphere-2"></div>
    <div class="glow-sphere sphere-3"></div>

    <div class="login-card">
        <div class="brand-badge">
            <span></span> AI Sentinel Live Node
        </div>
        <h1 class="title">{SYSTEM_NAME}</h1>
        <p class="subtitle">{SYSTEM_TAGLINE}</p>

        <div id="errorBanner" class="error-banner"></div>

        <form id="loginForm">
            <div class="form-group">
                <label for="username">Authorized Username</label>
                <div class="input-wrapper">
                    <input type="text" id="username" placeholder="e.g. tro_admin" autocomplete="username" required>
                </div>
            </div>
            <div class="form-group">
                <label for="password">Security Credentials</label>
                <div class="input-wrapper">
                    <input type="password" id="password" placeholder="••••••••" autocomplete="current-password" required>
                </div>
            </div>
            <button type="submit" id="submitBtn" class="btn-submit">Authenticate to Portal</button>
        </form>

        <div class="quick-section">
            <div class="quick-title">Quick Demo Login</div>
            <div class="quick-grid">
                <div class="quick-chip" onclick="fillCreds('tro_admin', 'tro2026')">
                    <span class="quick-role">👮 TRO Officer</span>
                    <span class="quick-sub">Admin Access</span>
                </div>
                <div class="quick-chip" onclick="fillCreds('teacher', 'school123')">
                    <span class="quick-role">👩‍🏫 Teacher</span>
                    <span class="quick-sub">Classroom View</span>
                </div>
            </div>
        </div>
    </div>

    <script>
        function fillCreds(u, p) {{
            document.getElementById('username').value = u;
            document.getElementById('password').value = p;
            document.getElementById('submitBtn').focus();
        }}

        document.getElementById('loginForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const err = document.getElementById('errorBanner');
            btn.disabled = true;
            btn.innerText = "Verifying Credentials...";
            err.style.display = 'none';

            try {{
                const res = await fetch('/api/login', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        username: document.getElementById('username').value.trim(),
                        password: document.getElementById('password').value.trim()
                    }})
                }});
                const data = await res.json();
                if (data.success) {{
                    window.location.href = '/';
                }} else {{
                    err.innerText = data.error || "Authentication failed. Invalid username or password.";
                    err.style.display = 'block';
                    btn.disabled = false;
                    btn.innerText = "Authenticate to Portal";
                }}
            }} catch (error) {{
                err.innerText = "Network communication error. Verify host connectivity.";
                err.style.display = 'block';
                btn.disabled = false;
                btn.innerText = "Authenticate to Portal";
            }}
        }});
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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --bg: #050811;
            --surface: #0a1122;
            --surface-card: rgba(14, 23, 42, 0.72);
            --border: rgba(56, 189, 248, 0.12);
            --border-highlight: rgba(56, 189, 248, 0.3);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #0284c7;
            --cyan: #38bdf8;
            --emerald: #10b981;
            --emerald-bg: rgba(16, 185, 129, 0.14);
            --emerald-border: rgba(16, 185, 129, 0.35);
            --rose: #f43f5e;
            --rose-bg: rgba(244, 63, 94, 0.14);
            --rose-border: rgba(244, 63, 94, 0.35);
            --amber: #f59e0b;
            --amber-bg: rgba(245, 158, 11, 0.14);
            --amber-border: rgba(245, 158, 11, 0.35);
        }}
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }}

        /* Glow ambient background */
        .bg-glow {{
            position: fixed;
            top: 0; left: 0; width: 100vw; height: 100vh;
            pointer-events: none;
            z-index: 0;
            background: radial-gradient(circle at 10% 15%, rgba(14,165,233,0.09) 0%, transparent 45%),
                        radial-gradient(circle at 90% 80%, rgba(99,102,241,0.08) 0%, transparent 45%);
        }}

        /* Header Bar */
        header {{
            position: sticky;
            top: 0;
            z-index: 50;
            background: rgba(7, 13, 27, 0.88);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
            padding: 0 32px;
            height: 72px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .brand-container {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .brand-icon {{
            width: 42px;
            height: 42px;
            border-radius: 12px;
            background: linear-gradient(135deg, #0284c7 0%, #6366f1 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.35rem;
            box-shadow: 0 0 20px rgba(14, 165, 233, 0.35);
        }}
        .brand-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.28rem;
            font-weight: 800;
            letter-spacing: -0.01em;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .brand-subtitle {{
            font-size: 0.74rem;
            color: var(--text-muted);
            letter-spacing: 0.02em;
        }}

        .host-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #34d399;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 9999px;
            letter-spacing: 0.05em;
        }}
        .host-badge-dot {{
            width: 6px; height: 6px;
            border-radius: 50%;
            background: #10b981;
            box-shadow: 0 0 8px #10b981;
            animation: pulseDot 1.6s infinite;
        }}
        @keyframes pulseDot {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.4; transform: scale(0.85); }}
        }}

        .header-actions {{
            display: flex;
            align-items: center;
            gap: 18px;
        }}
        .user-pill {{
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid var(--border);
            padding: 6px 14px 6px 8px;
            border-radius: 9999px;
        }}
        .user-avatar {{
            width: 30px; height: 30px;
            border-radius: 50%;
            background: linear-gradient(135deg, #38bdf8, #0284c7);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.82rem;
            font-weight: 700;
            color: #fff;
        }}
        .user-meta {{ line-height: 1.2; }}
        .user-name {{ font-size: 0.84rem; font-weight: 700; color: #f1f5f9; }}
        .user-role {{ font-size: 0.7rem; color: #38bdf8; font-weight: 600; text-transform: uppercase; }}

        .btn-header {{
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border);
            color: #e2e8f0;
            font-size: 0.84rem;
            font-weight: 600;
            padding: 9px 16px;
            border-radius: 12px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
        }}
        .btn-header:hover {{
            background: rgba(30, 41, 59, 0.9);
            border-color: var(--cyan);
            color: #fff;
            transform: translateY(-1px);
        }}
        .btn-logout:hover {{
            border-color: rgba(244, 63, 94, 0.5);
            color: #fca5a5;
        }}

        /* Main Container */
        main {{
            position: relative;
            z-index: 1;
            flex: 1;
            max-width: 1400px;
            width: 100%;
            margin: 0 auto;
            padding: 32px 32px 64px 32px;
            display: flex;
            flex-direction: column;
            gap: 28px;
        }}

        /* Top Hero Alert Bar */
        .system-banner {{
            background: linear-gradient(90deg, rgba(14,165,233,0.12) 0%, rgba(99,102,241,0.08) 100%);
            border: 1px solid rgba(56, 189, 248, 0.25);
            border-radius: 20px;
            padding: 18px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 16px;
        }}
        .banner-left {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .banner-beacon {{
            width: 12px; height: 12px;
            border-radius: 50%;
            background: #10b981;
            box-shadow: 0 0 16px #10b981;
            animation: pulseDot 2s infinite;
        }}
        .banner-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.05rem;
            font-weight: 700;
            color: #ffffff;
        }}
        .banner-desc {{
            font-size: 0.82rem;
            color: var(--text-muted);
        }}
        .banner-right {{
            display: flex;
            align-items: center;
            gap: 14px;
            font-size: 0.82rem;
            color: #cbd5e1;
        }}
        .ip-tag {{
            background: rgba(0, 0, 0, 0.35);
            padding: 6px 12px;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            color: #38bdf8;
        }}

        /* KPI Metric Cards Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 18px;
        }}
        .kpi-card {{
            background: var(--surface-card);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 22px;
            padding: 24px;
            position: relative;
            overflow: hidden;
            transition: all 0.25s ease;
        }}
        .kpi-card:hover {{
            transform: translateY(-2px);
            border-color: var(--border-highlight);
            box-shadow: 0 16px 32px -8px rgba(0, 0, 0, 0.5);
        }}
        .kpi-card::after {{
            content: '';
            position: absolute;
            top: 0; right: 0;
            width: 120px; height: 120px;
            border-radius: 50%;
            filter: blur(40px);
            opacity: 0.15;
            pointer-events: none;
        }}
        .card-scanned::after {{ background: #38bdf8; }}
        .card-pass::after {{ background: #10b981; }}
        .card-unimiss::after {{ background: #f43f5e; }}
        .card-badgemiss::after {{ background: #f59e0b; }}

        .kpi-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 14px;
        }}
        .kpi-title {{
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
        }}
        .kpi-icon {{
            font-size: 1.25rem;
        }}
        .kpi-value {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.3rem;
            font-weight: 800;
            color: #ffffff;
            line-height: 1;
            margin-bottom: 8px;
        }}
        .kpi-subtext {{
            font-size: 0.78rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .text-cyan {{ color: #38bdf8; }}
        .text-emerald {{ color: #34d399; }}
        .text-rose {{ color: #fb7185; }}
        .text-amber {{ color: #fbbf24; }}

        /* Control & Filter Toolbar */
        .toolbar {{
            background: var(--surface-card);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 18px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 16px;
        }}
        .filter-tabs {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .tab-btn {{
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border);
            color: var(--text-muted);
            font-size: 0.82rem;
            font-weight: 600;
            padding: 8px 16px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .tab-btn:hover {{
            background: rgba(30, 41, 59, 0.9);
            color: #fff;
            border-color: rgba(56, 189, 248, 0.3);
        }}
        .tab-btn.active {{
            background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
            color: #ffffff;
            border-color: rgba(56, 189, 248, 0.5);
            box-shadow: 0 4px 14px -3px rgba(14, 165, 233, 0.4);
        }}

        .search-controls {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .date-select {{
            background: rgba(7, 12, 24, 0.9);
            border: 1px solid var(--border);
            color: #f8fafc;
            padding: 9px 14px;
            border-radius: 12px;
            font-size: 0.84rem;
            outline: none;
            cursor: pointer;
            font-family: inherit;
        }}
        .date-select:focus {{
            border-color: var(--cyan);
        }}
        .search-box {{
            position: relative;
        }}
        .search-box input {{
            background: rgba(7, 12, 24, 0.9);
            border: 1px solid var(--border);
            color: #f8fafc;
            padding: 9px 14px 9px 36px;
            border-radius: 12px;
            font-size: 0.84rem;
            outline: none;
            width: 220px;
            transition: all 0.2s ease;
            font-family: inherit;
        }}
        .search-box input:focus {{
            width: 280px;
            border-color: var(--cyan);
        }}
        .search-icon {{
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            font-size: 0.85rem;
            pointer-events: none;
        }}

        /* Table Card */
        .table-card {{
            background: var(--surface-card);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 24px;
            overflow: hidden;
            box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.6);
        }}
        .table-wrap {{
            overflow-x: auto;
            width: 100%;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        thead tr {{
            background: rgba(11, 18, 35, 0.95);
            border-bottom: 1px solid var(--border);
        }}
        th {{
            padding: 16px 22px;
            font-size: 0.74rem;
            font-weight: 700;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.07em;
        }}
        tbody tr {{
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            transition: background 0.15s ease;
        }}
        tbody tr:hover {{
            background: rgba(255, 255, 255, 0.03);
        }}
        td {{
            padding: 18px 22px;
            font-size: 0.88rem;
            color: #e2e8f0;
            vertical-align: middle;
        }}

        .time-col {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.82rem;
            color: #94a3b8;
            white-space: nowrap;
        }}
        .name-cell {{
            display: flex;
            align-items: center;
            gap: 12px;
            font-weight: 600;
            color: #ffffff;
        }}
        .student-avatar {{
            width: 34px; height: 34px;
            border-radius: 10px;
            background: rgba(30, 41, 59, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.8rem;
            font-weight: 700;
            color: #38bdf8;
        }}

        /* Status Pills - EXPLICIT TEXT, NO AMBIGUOUS PERCENTAGES */
        .status-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border-radius: 9999px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.01em;
            white-space: nowrap;
        }}
        .pill-uniform-complete {{
            background: var(--emerald-bg);
            border: 1px solid var(--emerald-border);
            color: #34d399;
        }}
        .pill-uniform-missing {{
            background: var(--rose-bg);
            border: 1px solid var(--rose-border);
            color: #fb7185;
            animation: softPulse 2.5s infinite;
        }}
        .pill-badge-verified {{
            background: var(--emerald-bg);
            border: 1px solid var(--emerald-border);
            color: #34d399;
        }}
        .pill-badge-missing {{
            background: var(--amber-bg);
            border: 1px solid var(--amber-border);
            color: #fbbf24;
        }}

        @keyframes softPulse {{
            0%, 100% {{ box-shadow: 0 0 0 rgba(244, 63, 94, 0); }}
            50% {{ box-shadow: 0 0 10px rgba(244, 63, 94, 0.35); }}
        }}

        /* Verdict Badges */
        .verdict-tag {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 14px;
            border-radius: 10px;
            font-size: 0.8rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-family: 'Outfit', sans-serif;
        }}
        .verdict-pass {{
            background: rgba(16, 185, 129, 0.16);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: #10b981;
        }}
        .verdict-unimiss {{
            background: rgba(244, 63, 94, 0.16);
            border: 1px solid rgba(244, 63, 94, 0.4);
            color: #f43f5e;
        }}
        .verdict-badgemiss {{
            background: rgba(245, 158, 11, 0.16);
            border: 1px solid rgba(245, 158, 11, 0.4);
            color: #f59e0b;
        }}

        .btn-inspect {{
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border);
            color: #cbd5e1;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 0.76rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-inspect:hover {{
            background: rgba(30, 41, 59, 0.9);
            border-color: var(--cyan);
            color: #ffffff;
        }}

        .btn-override-quick {{
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.35);
            color: #10b981;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 0.76rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}
        .btn-override-quick:hover {{
            background: #10b981;
            color: #050811;
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.4);
            transform: translateY(-1px);
        }}

        .btn-override-pass {{
            background: #10b981;
            color: #050811;
            border: none;
            padding: 9px 14px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 700;
            cursor: pointer;
            transition: transform 0.15s ease;
        }}
        .btn-override-pass:hover {{ transform: scale(1.03); }}

        .btn-override-badgemiss {{
            background: rgba(245, 158, 11, 0.2);
            color: #f59e0b;
            border: 1px solid rgba(245, 158, 11, 0.4);
            padding: 9px 14px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
        }}

        .btn-override-fail {{
            background: rgba(244, 63, 94, 0.2);
            color: #f43f5e;
            border: 1px solid rgba(244, 63, 94, 0.4);
            padding: 9px 14px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
        }}

        .empty-state {{
            text-align: center;
            padding: 56px 20px;
            color: var(--text-muted);
        }}
        .empty-icon {{ font-size: 2.4rem; margin-bottom: 12px; opacity: 0.7; }}
        .empty-title {{ font-size: 1.05rem; font-weight: 700; color: #cbd5e1; margin-bottom: 4px; }}

        /* Modal Dialog */
        .modal-overlay {{
            position: fixed;
            top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(3, 7, 18, 0.82);
            backdrop-filter: blur(8px);
            z-index: 999;
            display: none;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        .modal-card {{
            background: #0b1324;
            border: 1px solid var(--border-highlight);
            border-radius: 24px;
            width: 100%;
            max-width: 520px;
            padding: 32px;
            box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.8), 0 0 30px rgba(56, 189, 248, 0.15);
            position: relative;
            animation: modalPop 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        @keyframes modalPop {{
            0% {{ opacity: 0; transform: scale(0.94); }}
            100% {{ opacity: 1; transform: scale(1); }}
        }}
        .modal-close {{
            position: absolute;
            top: 20px; right: 20px;
            background: rgba(255, 255, 255, 0.08);
            border: none;
            color: #cbd5e1;
            width: 32px; height: 32px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 1.1rem;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
        }}
        .modal-close:hover {{
            background: rgba(255, 255, 255, 0.15);
            color: #fff;
        }}

        .modal-header {{
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
        }}
        .modal-avatar {{
            width: 54px; height: 54px;
            border-radius: 16px;
            background: linear-gradient(135deg, #0284c7, #6366f1);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.3rem;
            font-weight: 800;
            color: #fff;
            box-shadow: 0 8px 16px -4px rgba(14, 165, 233, 0.4);
        }}
        .modal-student-name {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.35rem;
            font-weight: 800;
            color: #ffffff;
        }}
        .modal-time {{
            font-size: 0.8rem;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
        }}

        .checklist-group {{
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-bottom: 24px;
        }}
        .check-item {{
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 14px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .check-left {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .check-title {{ font-size: 0.88rem; font-weight: 700; color: #f1f5f9; }}
        .check-desc {{ font-size: 0.74rem; color: var(--text-muted); }}

        /* Responsive */
        @media (max-width: 868px) {{
            header {{ padding: 0 18px; height: 64px; }}
            main {{ padding: 20px 16px 48px 16px; gap: 20px; }}
            .brand-title {{ font-size: 1.1rem; }}
            .user-meta {{ display: none; }}
            .kpi-grid {{ grid-template-columns: 1fr 1fr; }}
            .toolbar {{ flex-direction: column; align-items: stretch; }}
            .search-controls {{ width: 100%; justify-content: space-between; }}
            .search-box input {{ width: 100%; }}
        }}
        @media (max-width: 520px) {{
            .kpi-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="bg-glow"></div>

    <header>
        <div class="brand-container">
            <div class="brand-icon">🛡️</div>
            <div>
                <div class="brand-title">
                    {SYSTEM_NAME}
                    <div class="host-badge"><span class="host-badge-dot"></span> LIVE NODE</div>
                </div>
                <div class="brand-subtitle">{SYSTEM_TAGLINE}</div>
            </div>
        </div>

        <div class="header-actions">
            <div class="user-pill">
                <div class="user-avatar" id="headerUserAvatar">T</div>
                <div class="user-meta">
                    <div class="user-name" id="headerUserName">Officer</div>
                    <div class="user-role" id="headerUserRole">TRO</div>
                </div>
            </div>
            <button class="btn-header" onclick="exportCSV()">
                <span>📥</span> Export CSV
            </button>
            <button class="btn-header btn-logout" onclick="logout()">
                <span>🚪</span> Sign Out
            </button>
        </div>
    </header>

    <main>
        <!-- System Banner -->
        <div class="system-banner">
            <div class="banner-left">
                <div class="banner-beacon"></div>
                <div>
                    <div class="banner-title">Sentinel Attendance & Uniform Telemetry Online</div>
                    <div class="banner-desc">Biometric recognition and full-torso uniform compliance checking running real-time.</div>
                </div>
            </div>
            <div class="banner-right">
                <span>Active Network Endpoint:</span>
                <span class="ip-tag" id="networkEndpoint">http://localhost:{PORT}</span>
            </div>
        </div>

        <!-- KPI Summary Cards -->
        <div class="kpi-grid">
            <div class="kpi-card card-scanned">
                <div class="kpi-header">
                    <span class="kpi-title">Total Scanned</span>
                    <span class="kpi-icon">👥</span>
                </div>
                <div class="kpi-value text-cyan" id="statScanned">0</div>
                <div class="kpi-subtext text-cyan">
                    <span>Registered in roster:</span> <strong id="statRegistered">0</strong>
                </div>
            </div>

            <div class="kpi-card card-pass">
                <div class="kpi-header">
                    <span class="kpi-title">Fully Compliant</span>
                    <span class="kpi-icon">✅</span>
                </div>
                <div class="kpi-value text-emerald" id="statPass">0</div>
                <div class="kpi-subtext text-emerald">
                    <span>Full Pass Rate:</span> <strong id="statRate">0%</strong>
                </div>
            </div>

            <div class="kpi-card card-unimiss">
                <div class="kpi-header">
                    <span class="kpi-title">Uniform Missing</span>
                    <span class="kpi-icon">❌</span>
                </div>
                <div class="kpi-value text-rose" id="statUniformViolations">0</div>
                <div class="kpi-subtext text-rose">
                    <span>Defaulters flagged today</span>
                </div>
            </div>

            <div class="kpi-card card-badgemiss">
                <div class="kpi-header">
                    <span class="kpi-title">Badge Missing</span>
                    <span class="kpi-icon">⚠️</span>
                </div>
                <div class="kpi-value text-amber" id="statBadgeViolations">0</div>
                <div class="kpi-subtext text-amber">
                    <span>ID badge not identified</span>
                </div>
            </div>
        </div>

        <!-- Filter & Search Toolbar -->
        <div class="toolbar">
            <div class="filter-tabs">
                <button class="tab-btn active" onclick="setFilter('ALL', this)">All Scans (<span id="countAll">0</span>)</button>
                <button class="tab-btn" onclick="setFilter('PASS', this)">Fully Compliant (<span id="countPass">0</span>)</button>
                <button class="tab-btn" onclick="setFilter('UNIFORM_MISSING', this)">Uniform Missing (<span id="countUniMiss">0</span>)</button>
                <button class="tab-btn" onclick="setFilter('BADGE_MISSING', this)">Badge Missing (<span id="countBadgeMiss">0</span>)</button>
            </div>

            <div class="search-controls">
                <select id="datePicker" class="date-select" onchange="onDateChange()">
                    <!-- Populated dynamically -->
                </select>
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="searchInput" placeholder="Search student name..." oninput="onSearch()">
                </div>
            </div>
        </div>

        <!-- Attendance Records Table -->
        <div class="table-card">
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Scan Time</th>
                            <th>Student Name</th>
                            <th>Uniform Compliance</th>
                            <th>ID Badge Verification</th>
                            <th>Official Verdict</th>
                            <th>Inspection</th>
                        </tr>
                    </thead>
                    <tbody id="recordsBody">
                        <tr>
                            <td colspan="6" class="empty-state">
                                <div class="empty-icon">⏳</div>
                                <div class="empty-title">Loading telemetry data...</div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- Inspection Detail Modal -->
    <div id="inspectModal" class="modal-overlay" onclick="closeModalOnBg(event)">
        <div class="modal-card">
            <button class="modal-close" onclick="closeModal()">✕</button>
            
            <div class="modal-header">
                <div class="modal-avatar" id="modalAvatar">S</div>
                <div>
                    <div class="modal-student-name" id="modalName">Student Name</div>
                    <div class="modal-time" id="modalTime">Logged: 00:00:00</div>
                </div>
            </div>

            <div class="checklist-group">
                <div class="check-item">
                    <div class="check-left">
                        <span style="font-size:1.3rem;">👕</span>
                        <div>
                            <div class="check-title">Uniform Verification</div>
                            <div class="check-desc" id="modalUniDesc">Torso uniform color coverage verification</div>
                        </div>
                    </div>
                    <div id="modalUniPill"></div>
                </div>

                <div class="check-item">
                    <div class="check-left">
                        <span style="font-size:1.3rem;">🪪</span>
                        <div>
                            <div class="check-title">Official ID Badge</div>
                            <div class="check-desc" id="modalBadgeDesc">Chest badge optical recognition</div>
                        </div>
                    </div>
                    <div id="modalBadgePill"></div>
                </div>

                <div class="check-item">
                    <div class="check-left">
                        <span style="font-size:1.3rem;">👁️</span>
                        <div>
                            <div class="check-title">Biometric Identity</div>
                            <div class="check-desc">128D Face Encoding Neural Matching</div>
                        </div>
                    </div>
                    <div class="status-pill pill-uniform-complete">✓ VERIFIED IDENTITY</div>
                </div>
            </div>

            <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 14px; padding: 14px; text-align: center;">
                <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; margin-bottom: 4px; font-weight:700;">Final Disciplinary Status</div>
                <div id="modalVerdictTag"></div>
            </div>

            <!-- Teacher / Admin Manual Override Controls -->
            <div style="margin-top: 16px; background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 14px; padding: 14px;">
                <div style="font-size:0.8rem; font-weight:700; color: #10b981; text-transform:uppercase; margin-bottom:6px;">✏️ Teacher / Admin Manual Override</div>
                <div style="font-size:0.78rem; color: var(--text-muted); margin-bottom:12px;">If camera recognition was incorrect, teachers can override the result below:</div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <button class="btn-override-pass" onclick="submitOverride('PASS', 'Fully Compliant', 'Uniform Complete', 'Badge Verified')">✓ Mark Full Uniform</button>
                    <button class="btn-override-badgemiss" onclick="submitOverride('FAIL', 'Badge Missing', 'Uniform Complete', 'Badge Missing')">⚠️ Mark Badge Missing</button>
                    <button class="btn-override-fail" onclick="submitOverride('FAIL', 'Uniform Missing', 'Uniform Missing', 'Badge Missing')">❌ Mark Uniform Missing</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentDate = '';
        let allRecords = [];
        let currentFilter = 'ALL';
        let searchQuery = '';

        async function init() {{
            try {{
                const res = await fetch('/api/attendance');
                if (!res.ok) {{
                    window.location.href = '/login';
                    return;
                }}
                const data = await res.json();
                
                // Update User Info
                if (data.user) {{
                    document.getElementById('headerUserName').innerText = data.user.name;
                    document.getElementById('headerUserRole').innerText = data.user.role;
                    document.getElementById('headerUserAvatar').innerText = data.user.name.charAt(0);
                }}

                // Populate Available Dates
                const dateSelect = document.getElementById('datePicker');
                dateSelect.innerHTML = '';
                (data.available_dates || []).forEach(d => {{
                    const opt = document.createElement('option');
                    opt.value = d;
                    opt.text = d === data.today ? `${{d}} (Today)` : d;
                    if (d === data.selected_date) opt.selected = true;
                    dateSelect.appendChild(opt);
                }});

                currentDate = data.selected_date;
                renderData(data.stats, data.records);
            }} catch (e) {{
                console.error("Init failed:", e);
            }}
        }}

        function renderData(stats, records) {{
            allRecords = records || [];

            // Update KPI cards
            document.getElementById('statScanned').innerText = stats.total_scanned || 0;
            document.getElementById('statRegistered').innerText = stats.total_registered || 0;
            document.getElementById('statPass').innerText = stats.full_pass || 0;
            document.getElementById('statRate').innerText = (stats.compliance_rate || 0) + '%';
            document.getElementById('statUniformViolations').innerText = stats.uniform_missing_count || 0;
            document.getElementById('statBadgeViolations').innerText = stats.badge_missing_count || 0;

            // Tab Counts
            document.getElementById('countAll').innerText = allRecords.length;
            document.getElementById('countPass').innerText = stats.full_pass || 0;
            document.getElementById('countUniMiss').innerText = stats.uniform_missing_count || 0;
            document.getElementById('countBadgeMiss').innerText = stats.badge_missing_count || 0;

            renderTable();
        }}

        function setFilter(filterType, btnElem) {{
            currentFilter = filterType;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btnElem.classList.add('active');
            renderTable();
        }}

        function onSearch() {{
            searchQuery = document.getElementById('searchInput').value.trim().toLowerCase();
            renderTable();
        }}

        async function onDateChange() {{
            const select = document.getElementById('datePicker');
            currentDate = select.value;
            try {{
                const res = await fetch('/api/attendance?date=' + encodeURIComponent(currentDate));
                if (res.ok) {{
                    const data = await res.json();
                    renderData(data.stats, data.records);
                }}
            }} catch (e) {{
                console.error("Date change failed:", e);
            }}
        }}

        function renderTable() {{
            const tbody = document.getElementById('recordsBody');
            
            // Filter logic
            let filtered = allRecords.filter(r => {{
                // Search match
                if (searchQuery && !r.name.toLowerCase().includes(searchQuery)) {{
                    return false;
                }}
                // Status tab match
                if (currentFilter === 'PASS') return r.result === 'Fully Compliant';
                if (currentFilter === 'UNIFORM_MISSING') return r.uniform_missing === true;
                if (currentFilter === 'BADGE_MISSING') return r.badge_missing === true;
                return true;
            }});

            if (filtered.length === 0) {{
                tbody.innerHTML = `
                    <tr>
                        <td colspan="6" class="empty-state">
                            <div class="empty-icon">📋</div>
                            <div class="empty-title">No attendance records found</div>
                            <div style="font-size:0.8rem; color:var(--text-muted);">Try adjusting the filter tab, search keywords, or selected date.</div>
                        </td>
                    </tr>
                `;
                return;
            }}

            tbody.innerHTML = filtered.map((r, idx) => {{
                // Uniform pill (EXPLICIT WORDS, NO PERCENTAGES)
                const uniPill = r.uniform_missing
                    ? `<span class="status-pill pill-uniform-missing">✗ Uniform Missing</span>`
                    : `<span class="status-pill pill-uniform-complete">✓ Uniform Complete</span>`;

                // Badge pill
                const badgePill = r.badge_missing
                    ? `<span class="status-pill pill-badge-missing">⚠️ Badge Missing</span>`
                    : `<span class="status-pill pill-badge-verified">✓ Badge Verified</span>`;

                // Verdict
                let verdictBadge = '';
                if (r.result === 'Fully Compliant') {{
                    verdictBadge = `<span class="verdict-tag verdict-pass">✓ Fully Compliant</span>`;
                }} else if (r.uniform_missing) {{
                    verdictBadge = `<span class="verdict-tag verdict-unimiss">✗ Uniform Missing</span>`;
                }} else {{
                    verdictBadge = `<span class="verdict-tag verdict-badgemiss">⚠️ Badge Missing</span>`;
                }}

                return `
                    <tr>
                        <td class="time-col">${{r.time}}</td>
                        <td>
                            <div class="name-cell">
                                <div class="student-avatar">${{r.name.charAt(0)}}</div>
                                <span>${{r.name}}</span>
                            </div>
                        </td>
                        <td>${{uniPill}}</td>
                        <td>${{badgePill}}</td>
                        <td>${{verdictBadge}}</td>
                        <td>
                            <div style="display:flex; gap:6px; align-items:center;">
                                <button class="btn-inspect" onclick='openModal(${{JSON.stringify(r)}})'>Inspect</button>
                                <button class="btn-override-quick" onclick='quickOverrideFullUniform("${{r.name.replace(/'/g, "\\'")}}")' title="Teacher Quick Override to Full Uniform">✏️ Set Full Uniform</button>
                            </div>
                        </td>
                    </tr>
                `;
            }}).join('');
        }}

        let currentModalRecord = null;

        function openModal(record) {{
            currentModalRecord = record;
            document.getElementById('modalName').innerText = record.name;
            document.getElementById('modalAvatar').innerText = record.name.charAt(0);
            document.getElementById('modalTime').innerText = `Scanned at ${{record.time}} (${{currentDate}})`;

            const uniPill = document.getElementById('modalUniPill');
            const uniDesc = document.getElementById('modalUniDesc');
            if (record.uniform_missing) {{
                uniPill.innerHTML = `<span class="status-pill pill-uniform-missing">✗ Uniform Missing</span>`;
                uniDesc.innerText = "Required uniform items were not detected on person";
            }} else {{
                uniPill.innerHTML = `<span class="status-pill pill-uniform-complete">✓ Uniform Complete</span>`;
                uniDesc.innerText = "All required uniform clothing confirmed by neural model";
            }}

            const badgePill = document.getElementById('modalBadgePill');
            const badgeDesc = document.getElementById('modalBadgeDesc');
            if (record.badge_missing) {{
                badgePill.innerHTML = `<span class="status-pill pill-badge-missing">⚠️ Badge Missing</span>`;
                badgeDesc.innerText = "ID badge missing or concealed";
            }} else {{
                badgePill.innerHTML = `<span class="status-pill pill-badge-verified">✓ Badge Verified</span>`;
                badgeDesc.innerText = "Official identification badge correctly worn";
            }}

            const verdictTag = document.getElementById('modalVerdictTag');
            if (record.result === 'Fully Compliant') {{
                verdictTag.innerHTML = `<span class="verdict-tag verdict-pass">✓ FULL PASS — COMPLIANT</span>`;
            }} else if (record.uniform_missing) {{
                verdictTag.innerHTML = `<span class="verdict-tag verdict-unimiss">✗ NON-COMPLIANT — UNIFORM MISSING</span>`;
            }} else {{
                verdictTag.innerHTML = `<span class="verdict-tag verdict-badgemiss">⚠️ NON-COMPLIANT — BADGE MISSING</span>`;
            }}

            document.getElementById('inspectModal').style.display = 'flex';
        }}

        async function quickOverrideFullUniform(studentName) {{
            if (!confirm(`Are you sure you want to mark ${{studentName}} as wearing FULL UNIFORM?`)) return;
            await sendOverride(studentName, 'PASS', 'Fully Compliant', 'Uniform Complete', 'Badge Verified');
        }}

        async function submitOverride(status, result, uniformStatus, badgeStatus) {{
            if (!currentModalRecord) return;
            await sendOverride(currentModalRecord.name, status, result, uniformStatus, badgeStatus);
            closeModal();
        }}

        async function sendOverride(name, status, result, uniformStatus, badgeStatus) {{
            try {{
                const res = await fetch('/api/override-status', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        date: currentDate,
                        status: status,
                        result: result,
                        uniform_status: uniformStatus,
                        badge_status: badgeStatus
                    }})
                }});
                const data = await res.json();
                if (data.success) {{
                    showToast(`✓ Updated ${{name}} to ${{result}}`);
                    loadAttendance(currentDate);
                }} else {{
                    alert('Override failed: ' + (data.error || 'Unknown error'));
                }}
            }} catch (e) {{
                alert('Error connecting to server: ' + e);
            }}
        }}

        function showToast(msg) {{
            let toast = document.getElementById('toastNotice');
            if (!toast) {{
                toast = document.createElement('div');
                toast.id = 'toastNotice';
                toast.style.cssText = 'position:fixed; bottom:24px; right:24px; background:#10b981; color:#050811; font-weight:700; padding:12px 20px; border-radius:10px; z-index:9999; font-family:sans-serif; box-shadow:0 4px 14px rgba(16,185,129,0.4); font-size:0.9rem; transition:all 0.3s;';
                document.body.appendChild(toast);
            }}
            toast.innerText = msg;
            toast.style.display = 'block';
            setTimeout(() => {{ toast.style.display = 'none'; }}, 3000);
        }}

        function closeModal() {{
            document.getElementById('inspectModal').style.display = 'none';
        }}

        function closeModalOnBg(e) {{
            if (e.target.id === 'inspectModal') closeModal();
        }}

        function exportCSV() {{
            window.location.href = '/api/export-csv?date=' + encodeURIComponent(currentDate);
        }}

        async function logout() {{
            await fetch('/api/logout', {{ method: 'POST' }});
            window.location.href = '/login';
        }}

        // Real-time live auto-refresh polling every 3 seconds
        setInterval(async () => {{
            const today = new Date().toISOString().split('T')[0];
            if (currentDate === today) {{
                try {{
                    const res = await fetch('/api/attendance?date=' + encodeURIComponent(today));
                    if (res.ok) {{
                        const data = await res.json();
                        renderData(data.stats, data.records);
                    }}
                }} catch (e) {{}}
            }}
        }}, 3000);

        init();
    </script>
</body>
</html>
"""


class TROPortalHandler(http.server.BaseHTTPRequestHandler):
    def get_cookie(self, name):
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == name:
                    return v.strip()
        return None

    def get_authenticated_user(self):
        token = self.get_cookie("session_token")
        return validate_session(token)

    def send_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Login page
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

        # Authenticate user for protected endpoints
        user = self.get_authenticated_user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Main Dashboard
        if path == "/":
            content = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # API: Attendance Data
        elif path == "/api/attendance":
            today = datetime.now().strftime("%Y-%m-%d")
            available_dates = get_available_dates()
            # If date requested, use it; otherwise prefer today, or latest date with logs
            default_date = today
            if today not in available_dates and len(available_dates) > 0:
                default_date = available_dates[0]
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

        # API: Export CSV (Clean semantic statuses without raw percentages)
        elif path == "/api/export-csv":
            today = datetime.now().strftime("%Y-%m-%d")
            date_req = query.get("date", [today])[0]
            stats, records = parse_attendance_log(date_req)

            csv_lines = ["Date,Time,Student Name,Uniform Status,ID Badge Status,Overall Verdict"]
            for r in records:
                csv_lines.append(f'"{date_req}","{r["time"]}","{r["name"]}","{r["uniform_status"]}","{r["badge_status"]}","{r["result"]}"')

            csv_content = "\r\n".join(csv_lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="attendance_{date_req}.csv"')
            self.send_header("Content-Length", str(len(csv_content)))
            self.end_headers()
            self.wfile.write(csv_content)
            return

        # API: Firebase Connection & Queue Health Status
        elif path == "/api/firebase/status":
            status = firebase_service.get_connection_status()
            self.send_json(200, status)
            return

        else:
            self.send_error(404, "Page Not Found")


    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/login":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
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
            except Exception as e:
                self.send_json(400, {"success": False, "error": str(e)})
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

        elif path == "/api/override-status":
            user = self.get_authenticated_user()
            if not user:
                self.send_json(401, {"success": False, "error": "Unauthorized"})
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                student_name = data.get("name", "").strip()
                date_req = data.get("date", datetime.now().strftime("%Y-%m-%d")).strip()
                new_status = data.get("status", "PASS")
                new_result = data.get("result", "Fully Compliant")
                new_uniform_status = data.get("uniform_status", "Uniform Complete")
                new_badge_status = data.get("badge_status", "Badge Verified")

                if not student_name:
                    self.send_json(400, {"success": False, "error": "Student name is required"})
                    return

                # 1. Update Firebase Firestore
                fb_update = {
                    "status": new_status,
                    "result": new_result,
                    "uniform_status": new_uniform_status,
                    "badge_status": new_badge_status,
                    "uniform_pct": 100.0 if new_status == "PASS" else 0.0,
                    "overridden_by": user["name"],
                    "overridden_at": datetime.now().isoformat()
                }
                firebase_service.update_attendance_scan_in_firebase(student_name, date_req, fb_update)

                # 2. Update Local Disk Log file if present
                log_file = Path(f"logs/{date_req}/attendance.txt")
                if log_file.exists():
                    try:
                        lines = []
                        updated_local = False
                        with open(log_file, "r", encoding="utf-8") as f:
                            for line in f:
                                if f" {student_name} - " in line:
                                    time_part = line[1:line.find("]")] if "[" in line and "]" in line else datetime.now().strftime("%H:%M:%S")
                                    badge_str = "YES" if new_badge_status == "Badge Verified" else "NO"
                                    lines.append(f"[{time_part}] {student_name} - {new_uniform_status} - Badge: {badge_str} ({new_status})\n")
                                    updated_local = True
                                else:
                                    lines.append(line)
                        if not updated_local:
                            lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {student_name} - {new_uniform_status} - Badge: YES ({new_status})\n")
                        
                        with open(log_file, "w", encoding="utf-8") as f:
                            f.writelines(lines)
                    except Exception as ex:
                        print(f"Error updating local log file: {ex}")

                self.send_json(200, {
                    "success": "True",
                    "message": f"Successfully updated status for {student_name} to {new_result}",
                    "name": student_name,
                    "result": new_result
                })
            except Exception as e:
                self.send_json(400, {"success": False, "error": str(e)})
            return

        else:
            self.send_error(404, "Endpoint Not Found")

    def log_message(self, format, *args):
        # Suppress noisy HTTP request console logging
        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_web_server(port=PORT, background=False):
    """Starts the VeriUniform Sentinel Web Server"""
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), TROPortalHandler)
    local_ip = get_local_ip()
    
    print("\n" + "="*70)
    print(f"[ONLINE] {SYSTEM_NAME} - LIVE SERVER ACTIVE & HOSTED")
    print("="*70)
    print(f"[*] Localhost:      http://localhost:{port}")
    print(f"[*] Local Network:  http://{local_ip}:{port}")
    print("Default Logins:")
    print("  - TRO Officer Admin: [tro_admin / tro2026]")
    print("  - Class Teacher:     [teacher / school123]")
    print("="*70 + "\n")

    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            server.server_close()


if __name__ == "__main__":
    start_web_server(port=PORT, background=False)