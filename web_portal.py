import os
import time
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
import firebase_service

# Initialize Flask web application
app = Flask(__name__)

# Initialize Firebase connection on startup
firebase_service.initialize_firebase()

def get_available_dates():
    """Fetches available attendance dates from Firebase with a fallback to today."""
    try:
        fb_dates = firebase_service.get_available_firebase_dates()
        if fb_dates:
            return fb_dates
    except Exception as e:
        print(f"Error fetching dates from Firebase: {e}")
    
    # Fallback to current date if Firebase call fails or returns empty
    return [datetime.now().strftime("%Y-%m-%d")]

# Simple HTML template for the web portal dashboard
PORTAL_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>VeriUniform Sentinel - Web Portal</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f7f6; margin: 0; padding: 20px; color: #333; }
        .container { max-width: 1000px; margin: auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #00539f; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .card { background: #f8f9fa; padding: 15px; border-radius: 6px; border-left: 5px solid #00539f; }
        .card h3 { margin: 0 0 10px 0; font-size: 14px; color: #666; }
        .card p { margin: 0; font-size: 22px; font-weight: bold; color: #111; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #00539f; color: white; }
        .badge-pass { background: #d4edda; color: #155724; padding: 5px 10px; border-radius: 4px; font-weight: bold; }
        .badge-fail { background: #f8d7da; color: #721c24; padding: 5px 10px; border-radius: 4px; font-weight: bold; }
        .date-selector { margin-bottom: 20px; }
    .status-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #28a745; margin-right: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1><span class="status-indicator"></span> VeriUniform Sentinel Dashboard</h1>
        <p>Real-time automated school/workplace uniform and compliance tracking system.</p>
        
        <div class="date-selector">
            <form method="get" action="/">
                <label for="date"><strong>Select Date:</strong></label>
                <select name="date" id="date" onchange="this.form.submit()">
                    {% for d in available_dates %}
                        <option value="{{ d }}" {% if d == selected_date %}selected{% endif %}>{{ d }}</option>
                    {% endfor %}
                </select>
            </form>
        </div>

        {% if stats %}
        <div class="stats-grid">
            <div class="card">
                <h3>Total Registered</h3>
                <p>{{ stats.total_registered }}</p>
            </div>
            <div class="card">
                <h3>Total Scanned</h3>
                <p>{{ stats.total_scanned }}</p>
            </div>
            <div class="card" style="border-left-color: #28a745;">
                <h3>Fully Compliant</h3>
                <p>{{ stats.full_pass }}</p>
            </div>
            <div class="card" style="border-left-color: #dc3545;">
                <h3>Compliance Rate</h3>
                <p>{{ stats.compliance_rate }}%</p>
            </div>
        </div>
        {% endif %}

        <h2>Attendance Records</h2>
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Name</th>
                    <th>Uniform Status</th>
                    <th>Badge Status</th>
                    <th>Overall Result</th>
                </tr>
            </thead>
            <tbody>
                {% if records %}
                    {% for rec in records %}
                    <tr>
                        <td>{{ rec.time }}</td>
                        <td><strong>{{ rec.name }}</strong></td>
                        <td>{{ rec.uniform_status }}</td>
                        <td>{{ rec.badge_status }}</td>
                        <td>
                            {% if rec.result == 'Fully Compliant' %}
                                <span class="badge-pass">PASS</span>
                            {% else %}
                                <span class="badge-fail">{{ rec.result }}</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                {% else %}
                    <tr>
                        <td colspan="5" style="text-align: center; color: #777;">No attendance records found for this date.</td>
                    </tr>
                {% endif %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    # Fetch available dates safely using the corrected service function
    available_dates = get_available_dates()
    
    selected_date = request.args.get('date')
    if not selected_date and available_dates:
        selected_date = available_dates[0]
    else:
        selected_date = datetime.now().strftime("%Y-%m-%d")

    # Fetch attendance stats and records from Firebase for the selected date
    stats, records = firebase_service.fetch_attendance_from_firebase(selected_date)

    if not stats:
        stats = {
            "date": selected_date,
            "total_registered": 0,
            "total_scanned": 0,
            "full_pass": 0,
            "compliance_rate": 0.0
        }
        records = []

    return render_template_string(
        PORTAL_TEMPLATE,
        available_dates=available_dates,
        selected_date=selected_date,
        stats=stats,
        records=records
    )

@app.route('/api/status')
def api_status():
    """Health check and live connection status API endpoint for Render."""
    connection_status = firebase_service.get_connection_status()
    return jsonify(connection_status), 200

if __name__ == '__main__':
    # Bind to Render's required environment port or default to 10000
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)