import cv2
import face_recognition
import pickle
import os
import threading

# Load face encodings
def load_encodings():
    """Load pre-generated face encodings from file"""
    if not os.path.exists('face_encodings.pkl'):
        print("Error: face_encodings.pkl not found!")
        print("Please run 'python generate_encodings.py' first to generate encodings.")
        return None, None
    
    try:
        with open('face_encodings.pkl', 'rb') as f:
            data = pickle.load(f)
        
        known_face_encodings = data['encodings']
        known_face_names = data['names']
        
        print(f"Loaded {len(known_face_names)} face encoding(s)")
        print(f"Employees: {', '.join(known_face_names)}")
        return known_face_encodings, known_face_names
    
    except Exception as e:
        print(f"Error loading encodings: {e}")
        return None, None

# Load encodings at startup
known_face_encodings, known_face_names = load_encodings()

if known_face_encodings is None:
    print("\nExiting... Please generate face encodings first.")
    exit(1)

# Function to recognize Tesco workers using face recognition
def recognize_tesco_worker(frame):
    # Resize frame for faster processing (optional)
    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    
    # Convert BGR (OpenCV) to RGB (face_recognition)
    rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
    
    # Find all face locations and encodings in the current frame
    face_locations = face_recognition.face_locations(rgb_small_frame)
    face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
    
    face_names = []
    face_confidences = []
    
    # Process each detected face
    for face_encoding in face_encodings:
        # Compare with known faces
        matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.6)
        name = "Unknown"
        confidence = 0.0
        
        # Calculate face distances (lower is better match)
        face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
        
        if len(face_distances) > 0:
            best_match_index = face_distances.argmin()
            
            if matches[best_match_index]:
                name = known_face_names[best_match_index]
                # Convert distance to confidence percentage (0-1 scale)
                confidence = 1 - face_distances[best_match_index]
        
        face_names.append(name)
        face_confidences.append(confidence)
    
    # Draw rectangles and labels on the frame
    for (top, right, bottom, left), name, confidence in zip(face_locations, face_names, face_confidences):
        # Scale back up face locations (we resized to 1/4 size)
        top *= 4
        right *= 4
        bottom *= 4
        left *= 4
        
        # Choose color based on recognition
        if name == "Unknown":
            color = (0, 0, 255)  # Red for unknown
            label = "Unknown"
        else:
            color = (0, 255, 0)  # Green for recognized
            label = f"{name} ({confidence:.2f})"
        
        # Draw rectangle around face
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        
        # Draw label background
        cv2.rectangle(frame, (left, bottom - 35), (right, bottom), color, cv2.FILLED)
        
        # Draw label text
        cv2.putText(frame, label, (left + 6, bottom - 6), 
                   cv2.FONT_HERSHEY_DUPLEX, 0.6, (255, 255, 255), 1)
    
    return frame

# Function to run video capture and recognition in a separate thread
def capture_and_recognize():
    print("Searching for camera devices...")
    cap = None
    
    # Priority: external webcam first (1, 2, 3), then fallback to built-in laptop camera (0)
    env_cam = os.environ.get("CAMERA_INDEX")
    if env_cam is not None:
        camera_stages = [("Specified Camera", [int(env_cam)])]
    else:
        camera_stages = [
            ("External Webcam", [1, 2, 3]),
            ("Built-in Laptop Camera", [0])
        ]

    for stage_name, cam_indices in camera_stages:
        print(f"Scanning for {stage_name} (indices {cam_indices})...")
        for cam_idx in cam_indices:
            for backend in [cv2.CAP_DSHOW, cv2.CAP_ANY]:
                try:
                    temp_cap = cv2.VideoCapture(cam_idx, backend)
                    if temp_cap.isOpened():
                        ret, _ = temp_cap.read()
                        if ret:
                            cap = temp_cap
                            backend_name = "DirectShow" if backend == cv2.CAP_DSHOW else "Default"
                            print(f"[OK] Using {stage_name} (Index {cam_idx}) with {backend_name} backend")
                            break
                        temp_cap.release()
                except Exception:
                    pass
            if cap is not None:
                break
        if cap is not None:
            break

    # Check if the video capture is successful
    if cap is None or not cap.isOpened():
        print("Error: No camera could be opened!")
        print("Checked external webcam indices (1, 2, 3) and laptop camera (0).")
        print("Please check:")
        print("1. Camera is connected and functional")
        print("2. No other application is using the camera")
        print("3. Camera drivers are installed")
        return

    print("\n" + "="*50)
    print("Camera opened successfully!")
    print("Press 'q' to quit")
    print("="*50 + "\n")

    while True:
        # Read a frame from the video capture
        ret, frame = cap.read()

        # Check if the frame is read successfully
        if not ret:
            print("Error: Could not read frame.")
            break

        # Call the recognition function
        frame_with_recognition = recognize_tesco_worker(frame)

        # Display the resulting frame
        cv2.imshow('Tesco Worker Recognition', frame_with_recognition)

        # Break the loop when 'q' key is pressed
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # Release the video capture object
    cap.release()

    # Destroy all OpenCV windows
    cv2.destroyAllWindows()

# Create a thread for video capture and recognition
thread = threading.Thread(target=capture_and_recognize)

# Start the thread
thread.start()

# Wait for the thread to finish
thread.join()
