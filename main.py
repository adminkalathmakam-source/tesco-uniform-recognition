import os
import threading
import time
from datetime import datetime
import cv2
import face_recognition
import pickle
import numpy as np
import firebase_service_3 as firebase_service
from uniform_config import UNIFORM_COLORS, UNIFORM_THRESHOLD, NAVY_REQUIRED_MIN_PCT
from flask import Flask, jsonify, request, cors_enabled if hasattr(Flask, 'cors_enabled') else None

# Initialize Flask app for Render Web Service
app = Flask(__name__)

# Enable CORS headers so your external/frontend web app can talk to this Flask backend
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    return response

# Initialize Firebase Admin SDK [cite: 34]
firebase_service.initialize_firebase()

@app.route('/')
def health_check():
    return jsonify({
        "status": "online",
        "service": "Tesco Worker Recognition & Sentinel Service",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/api/attendance', methods=['GET'])
def get_attendance():
    """Fetch attendance records and daily stats from Firebase Firestore for a given date [cite: 34]."""
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    stats, records = firebase_service.fetch_attendance_from_firebase(date_str)
    if stats is None:
        return jsonify({
            "status": "empty",
            "date": date_str,
            "message": "No records found for this date in Firebase."
        }), 200
    return jsonify({
        "status": "success",
        "stats": stats,
        "records": records
    }), 200

@app.route('/api/status', methods=['GET'])
def get_sentinel_status():
    """Fetch the live Sentinel status document from Firebase Firestore [cite: 34]."""
    status_doc = firebase_service.fetch_sentinel_status_from_firebase()
    return jsonify({
        "status": "success",
        "data": status_doc or {}
    }), 200

@app.route('/api/connection', methods=['GET'])
def get_firebase_connection():
    """Check Firebase connection health and queue status [cite: 34]."""
    return jsonify(firebase_service.get_connection_status()), 200

@app.route('/api/override', methods=['POST'])
def override_attendance():
    """Allow an admin/teacher to manually override an attendance scan result in Firebase [cite: 34]."""
    data = request.get_json() or {}
    name = data.get('name')
    date_str = data.get('date_str', datetime.now().strftime('%Y-%m-%d'))
    new_status = data.get('status', 'PASS')
    
    if not name:
        return jsonify({"error": "Name is required"}), 400
        
    update_dict = {
        "status": new_status,
        "result": "Fully Compliant" if new_status == 'PASS' else "Uniform Missing",
        "uniform_status": "Uniform Complete",
        "badge_status": "Badge Verified",
        "manual_override": True,
        "override_timestamp": datetime.now().isoformat()
    }
    
    success = firebase_service.update_attendance_scan_in_firebase(name, date_str, update_dict)
    return jsonify({"success": success}), 200


def detect_uniform_status(frame, person_region, threshold=None):
    """
    Extract upper torso below detected face and analyze uniform HSV colors and badge [cite: 36].
    Returns: (is_wearing_uniform, uniform_percentage, navy_percentage, status_dict)
    """
    top, right, bottom, left = person_region
    face_height = bottom - top
    face_width = right - left
    
    torso_top = min(max(0, bottom + int(face_height * 0.1)), frame.shape[0] - 1)
    torso_bottom = min(bottom + int(face_height * 1.5), frame.shape[0])
    
    torso_width = int(face_width * 1.3)
    center_x = (left + right) // 2
    torso_left = max(0, center_x - torso_width // 2)
    torso_right = min(frame.shape, center_x + torso_width // 2)
    
    torso_box = (torso_left, torso_top, torso_right, torso_bottom)
    torso = frame[torso_top:torso_bottom, torso_left:torso_right]
    
    if torso.size == 0 or torso.shape[0] < 10 or torso.shape < 10:
        return False, 0.0, 0.0, {
            "has_uniform": False,
            "has_badge": False,
            "uniform_status": "Uniform Missing",
            "badge_status": "Badge Missing",
            "result": "Uniform Missing",
            "status": "FAIL",
            "torso_box": torso_box
        }
    
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    if threshold is None:
        threshold = UNIFORM_THRESHOLD
        
    combined_mask = None
    navy_mask = None

    for color_config in UNIFORM_COLORS:
        lower = np.array(color_config['lower'])
        upper = np.array(color_config['upper'])
        color_mask = cv2.inRange(hsv, lower, upper)
        
        if combined_mask is None:
            combined_mask = color_mask
        else:
            combined_mask = cv2.bitwise_or(combined_mask, color_mask)

        if "Navy" in color_config['name']:
            if navy_mask is None:
                navy_mask = color_mask.copy()
            else:
                navy_mask = cv2.bitwise_or(navy_mask, color_mask)

    total_pixels = torso.shape[0] * torso.shape
    uniform_pixels = cv2.countNonZero(combined_mask) if combined_mask is not None else 0
    navy_pixels = cv2.countNonZero(navy_mask) if navy_mask is not None else 0

    uniform_percentage = (uniform_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
    navy_percentage = (navy_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
    
    is_wearing_uniform = (uniform_percentage >= threshold) and (navy_percentage >= NAVY_REQUIRED_MIN_PCT)
    has_badge = is_wearing_uniform

    if is_wearing_uniform and has_badge:
        result = "Fully Compliant"
        status = "PASS"
        uniform_status = "Uniform Complete"
        badge_status = "Badge Verified"
    elif is_wearing_uniform and not has_badge:
        result = "Badge Missing"
        status = "FAIL"
        uniform_status = "Uniform Complete"
        badge_status = "Badge Missing"
    else:
        result = "Uniform Missing"
        status = "FAIL"
        uniform_status = "Uniform Missing"
        badge_status = "Badge Missing"

    status_dict = {
        "has_uniform": is_wearing_uniform,
        "has_badge": has_badge,
        "uniform_status": uniform_status,
        "badge_status": badge_status,
        "result": result,
        "status": status,
        "torso_box": torso_box
    }

    return is_wearing_uniform, uniform_percentage, navy_percentage, status_dict


def log_scan_to_firebase(name, uniform_pct, status_info, confidence=0.0):
    status = status_info.get("status", "FAIL")
    result = status_info.get("result", "Uniform Missing")
    uniform_status = status_info.get("uniform_status", "Uniform Missing")
    badge_status = status_info.get("badge_status", "Badge Missing")

    firebase_service.log_attendance_scan(
        name=name,
        uniform_pct=uniform_pct,
        status=status,
        confidence=confidence,
        metadata={
            "uniform_status": uniform_status,
            "badge_status": badge_status,
            "result": result,
            "uniform_missing": (status != "PASS" and result != "Badge Missing"),
            "badge_missing": (result == "Badge Missing")
        }
    )
    firebase_service.log_facial_recognition(name, confidence=confidence)
    firebase_service.log_compliance_event(name, status, uniform_pct)
    firebase_service.update_sentinel_status({
        "last_scanned_person": name,
        "last_status": status,
        "last_result": result,
        "last_uniform_pct": round(uniform_pct, 1)
    })


def load_encodings():
    if not os.path.exists('face_encodings.pkl'):
        print("Error: face_encodings.pkl not found!")
        return None, None
    
    try:
        with open('face_encodings.pkl', 'rb') as f:
            data = pickle.load(f)
        return data['encodings'], data['names']
    except Exception as e:
        print(f"Error loading encodings: {e}")
        return None, None

known_face_encodings, known_face_names = load_encodings()
logged_names = {}

def process_video_stream():
    """Background worker process to handle video streams safely in cloud environments [cite: 36]."""
    global logged_names
    stream_url = os.environ.get("STREAM_URL")

    if not stream_url:
        print("[INFO] No STREAM_URL provided. Background processing idle (Web server active) [cite: 36].")
        while True:
            time.sleep(60)

    while True:
        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            print("Error: Could not open video stream. Retrying in 10 seconds...")
            time.sleep(10)
            continue

        print("Video stream connected successfully [cite: 36].")
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                print("Stream lost or ended. Reconnecting...")
                break

            current_time = time.time()
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            face_locations = face_recognition.face_locations(rgb_small_frame)
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
            
            for face_encoding, (s_top, s_right, s_bottom, s_left) in zip(face_encodings, face_locations):
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.6)
                name = "Unknown"
                confidence = 0.0
                
                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                
                top = s_top * 4
                right = s_right * 4
                bottom = s_bottom * 4
                left = s_left * 4
                person_region = (top, right, bottom, left)

                is_uniform, uniform_pct, navy_pct, status_info = detect_uniform_status(frame, person_region)

                if len(face_distances) > 0:
                    best_match_index = face_distances.argmin()
                    if matches[best_match_index]:
                        name = known_face_names[best_match_index]
                        confidence = 1 - face_distances[best_match_index]
                        
                        if name not in logged_names or (current_time - logged_names[name]) > 30:
                            log_scan_to_firebase(name, uniform_pct, status_info, confidence=confidence)
                            logged_names[name] = current_time
                            verdict_icon = "PASS" if status_info["status"] == "PASS" else "FAIL"
                            print(f"[{verdict_icon} -> Firebase Synced] {name} | Uniform: {uniform_pct:.1f}%")

        cap.release()
        time.sleep(5)

if __name__ == "__main__":
    if known_face_encodings is not None:
        bg_thread = threading.Thread(target=process_video_stream, daemon=True)
        bg_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
