import os
import threading
import time
from datetime import datetime
import cv2
import face_recognition
import pickle
import numpy as np
import firebase_service
from uniform_config import UNIFORM_COLORS, UNIFORM_THRESHOLD, NAVY_REQUIRED_MIN_PCT

# Initialize Firebase Admin SDK
firebase_service.initialize_firebase()

def detect_uniform_status(frame, person_region, threshold=None):
    """
    Extract upper torso below detected face and analyze uniform HSV colors and badge.
    Returns: (is_wearing_uniform, uniform_percentage, navy_percentage, status_dict)
    """
    top, right, bottom, left = person_region
    face_height = bottom - top
    face_width = right - left
    
    # Calculate Upper Torso Region below face
    torso_top = min(max(0, bottom + int(face_height * 0.1)), frame.shape[0] - 1)
    torso_bottom = min(bottom + int(face_height * 1.5), frame.shape[0])
    
    torso_width = int(face_width * 1.3)
    center_x = (left + right) // 2
    torso_left = max(0, center_x - torso_width // 2)
    torso_right = min(frame.shape[1], center_x + torso_width // 2)
    
    torso_box = (torso_left, torso_top, torso_right, torso_bottom)
    torso = frame[torso_top:torso_bottom, torso_left:torso_right]
    
    if torso.size == 0 or torso.shape[0] < 10 or torso.shape[1] < 10:
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

        # Track Navy Blue coverage specifically
        if "Navy" in color_config['name']:
            if navy_mask is None:
                navy_mask = color_mask.copy()
            else:
                navy_mask = cv2.bitwise_or(navy_mask, color_mask)

    total_pixels = torso.shape[0] * torso.shape[1]
    uniform_pixels = cv2.countNonZero(combined_mask) if combined_mask is not None else 0
    navy_pixels = cv2.countNonZero(navy_mask) if navy_mask is not None else 0

    uniform_percentage = (uniform_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
    navy_percentage = (navy_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
    
    # 1. Main Uniform Detection (Must meet general uniform threshold and Navy Blue percentage)
    is_wearing_uniform = (uniform_percentage >= threshold) and (navy_percentage >= NAVY_REQUIRED_MIN_PCT)

    # 2. Outer Badge Verification (Lanyard / Badge aligned with uniform)
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
    """Log complete face + uniform verification event to Firebase."""
    status = status_info.get("status", "FAIL")
    result = status_info.get("result", "Uniform Missing")
    uniform_status = status_info.get("uniform_status", "Uniform Missing")
    badge_status = status_info.get("badge_status", "Badge Missing")

    # 1. Attendance scan event for the live portal
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

    # 2. Facial recognition log
    firebase_service.log_facial_recognition(name, confidence=confidence)

    # 3. Compliance status metric
    firebase_service.log_compliance_event(name, status, uniform_pct)

    # 4. Live Sentinel system status
    firebase_service.update_sentinel_status({
        "last_scanned_person": name,
        "last_status": status,
        "last_result": result,
        "last_uniform_pct": round(uniform_pct, 1)
    })


def load_encodings():
    if not os.path.exists('face_encodings.pkl'):
        print("Error: face_encodings.pkl not found!")
        print("Please run 'python generate_encodings.py' first.")
        return None, None
    
    try:
        with open('face_encodings.pkl', 'rb') as f:
            data = pickle.load(f)
        known_face_encodings = data['encodings']
        known_face_names = data['names']
        print(f"Loaded {len(known_face_names)} face encoding(s): {', '.join(known_face_names)}")
        return known_face_encodings, known_face_names
    except Exception as e:
        print(f"Error loading encodings: {e}")
        return None, None

known_face_encodings, known_face_names = load_encodings()

if known_face_encodings is None:
    print("\nExiting... Please generate face encodings first.")
    exit(1)

# Track logged faces with timestamps to allow re-logging after 30 seconds
logged_names = {}

def recognize_tesco_worker(frame):
    global logged_names
    current_time = time.time()
    
    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
    
    face_locations = face_recognition.face_locations(rgb_small_frame)
    face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
    
    face_names = []
    face_confidences = []
    detection_results = []
    
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

        # Check Uniform & Badge
        is_uniform, uniform_pct, navy_pct, status_info = detect_uniform_status(frame, person_region)

        if len(face_distances) > 0:
            best_match_index = face_distances.argmin()
            
            if matches[best_match_index]:
                name = known_face_names[best_match_index]
                confidence = 1 - face_distances[best_match_index]
                
                # Cooldown check: log to Firebase if first time or 30s elapsed since last log
                if name not in logged_names or (current_time - logged_names[name]) > 30:
                    log_scan_to_firebase(name, uniform_pct, status_info, confidence=confidence)
                    logged_names[name] = current_time
                    verdict_icon = "PASS" if status_info["status"] == "PASS" else "FAIL"
                    print(f"[{verdict_icon} -> Firebase Synced] {name} | Uniform: {uniform_pct:.1f}% (Navy: {navy_pct:.1f}%) | {status_info['result']}")

        face_names.append(name)
        face_confidences.append(confidence)
        detection_results.append(status_info)
    
    # Draw Visual Overlays on Screen
    for (s_top, s_right, s_bottom, s_left), name, confidence, status_info in zip(face_locations, face_names, face_confidences, detection_results):
        top = s_top * 4
        right = s_right * 4
        bottom = s_bottom * 4
        left = s_left * 4
        
        is_pass = (status_info["status"] == "PASS" and name != "Unknown")
        color = (0, 220, 0) if is_pass else ((0, 165, 255) if name != "Unknown" else (0, 0, 240))
        label = f"{name} ({confidence:.2f})" if name != "Unknown" else "Unknown Person"
        
        # Face box
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.rectangle(frame, (left, bottom - 30), (right, bottom), color, cv2.FILLED)
        cv2.putText(frame, label, (left + 6, bottom - 8), cv2.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1)

        # Torso uniform box
        t_left, t_top, t_right, t_bottom = status_info["torso_box"]
        cv2.rectangle(frame, (t_left, t_top), (t_right, t_bottom), color, 2)
        status_label = f"{status_info['result']}"
        cv2.putText(frame, status_label, (t_left + 6, t_top + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    
    return frame

def capture_and_recognize():
    print("Searching for camera devices...")
    cap = None
    
    env_cam = os.environ.get("CAMERA_INDEX")
    if env_cam is not None:
        camera_stages = [("Specified Camera", [int(env_cam)])]
    else:
        camera_stages = [
            ("External Webcam", [1, 2, 3]),
            ("Built-in Laptop Camera", [0])
        ]

    for stage_name, cam_indices in camera_stages:
        for cam_idx in cam_indices:
            for backend in [cv2.CAP_DSHOW, cv2.CAP_ANY]:
                try:
                    temp_cap = cv2.VideoCapture(cam_idx, backend)
                    if temp_cap.isOpened():
                        ret, _ = temp_cap.read()
                        if ret:
                            cap = temp_cap
                            print(f"[OK] Using {stage_name} (Index {cam_idx})")
                            break
                        temp_cap.release()
                except Exception:
                    pass
            if cap is not None:
                break
        if cap is not None:
            break

    if cap is None or not cap.isOpened():
        print("Error: No camera could be opened!")
        return

    print("Camera running! Press 'q' to quit.\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error: Could not read frame.")
            break

        frame_with_recognition = recognize_tesco_worker(frame)
        cv2.imshow('Tesco Worker Recognition', frame_with_recognition)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    thread = threading.Thread(target=capture_and_recognize)
    thread.start()
    thread.join()