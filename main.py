import os
import threading
import time
from datetime import datetime
import cv2
import face_recognition
import pickle
import firebase_service

# Initialize Firebase Admin SDK
firebase_service.initialize_firebase()

def log_scan_to_firebase(name, uniform_status="Uniform Verified", badge_status="Badge Verified", overall_result="Fully Compliant", confidence=0.0):
    """Log scan event to Firebase via non-blocking service."""
    status = "PASS" if overall_result == "Fully Compliant" else "FAIL"
    firebase_service.log_attendance_scan(
        name=name,
        uniform_pct=100.0 if status == "PASS" else 0.0,
        status=status,
        confidence=confidence,
        metadata={
            "uniform_status": uniform_status,
            "badge_status": badge_status,
            "result": overall_result
        }
    )


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
    
    for face_encoding in face_encodings:
        matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.6)
        name = "Unknown"
        confidence = 0.0
        
        face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
        
        if len(face_distances) > 0:
            best_match_index = face_distances.argmin()
            
            if matches[best_match_index]:
                name = known_face_names[best_match_index]
                confidence = 1 - face_distances[best_match_index]
                
                # Cooldown check: log if first time or 30s elapsed since last log
                if name not in logged_names or (current_time - logged_names[name]) > 30:
                    log_scan_to_firebase(name, confidence=confidence)
                    firebase_service.log_facial_recognition(name, confidence=confidence)
                    firebase_service.log_compliance_event(name, "PASS", 100.0)
                    logged_names[name] = current_time
                    print(f"✅ [Firebase Logged] Recognized {name} at {datetime.now().strftime('%H:%M:%S')}")

        
        face_names.append(name)
        face_confidences.append(confidence)
    
    for (top, right, bottom, left), name, confidence in zip(face_locations, face_names, face_confidences):
        top *= 4
        right *= 4
        bottom *= 4
        left *= 4
        
        color = (0, 0, 255) if name == "Unknown" else (0, 255, 0)
        label = "Unknown" if name == "Unknown" else f"{name} ({confidence:.2f})"
        
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.rectangle(frame, (left, bottom - 35), (right, bottom), color, cv2.FILLED)
        cv2.putText(frame, label, (left + 6, bottom - 6), cv2.FONT_HERSHEY_DUPLEX, 0.6, (255, 255, 255), 1)
    
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