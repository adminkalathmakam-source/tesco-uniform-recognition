import cv2
import face_recognition
import pickle
import os
import threading
import queue
import time
import pyttsx3
import numpy as np
from datetime import datetime
from pathlib import Path
from uniform_config import UNIFORM_COLORS, UNIFORM_THRESHOLD, MALE_THRESHOLD_EXTRA

# =============================================================================
# 1. FACE ENCODINGS LOADER
# =============================================================================
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
        print(f"Known students: {', '.join(known_face_names)}")
        return known_face_encodings, known_face_names
    
    except Exception as e:
        print(f"Error loading encodings: {e}")
        return None, None

known_face_encodings, known_face_names = load_encodings()

if known_face_encodings is None:
    print("\nExiting... Please generate face encodings first.")
    exit(1)


# =============================================================================
# 2. ATTENDANCE & LOGGING SYSTEM
# =============================================================================
logged_today = set()
recent_activity = []  # List of dicts: {'time': str, 'name': str, 'status': str, 'pct': float}
total_passed = 0
total_failed = 0

def init_logging():
    """Initialize daily log folder and load today's existing entries"""
    global logged_today, recent_activity, total_passed, total_failed
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_dir = Path(f"logs/{today_str}")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / "attendance.txt"
    logged_today = set()
    recent_activity = []
    total_passed = 0
    total_failed = 0
    
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    # Format: [10:15:32] Name - Uniform: 74.2% (PASS)
                    line = line.strip()
                    if line.startswith("[") and "]" in line and " - " in line:
                        time_part = line[1:line.find("]")]
                        rest = line[line.find("] ") + 2:]
                        if " - " in rest:
                            name_part, details = rest.split(" - ", 1)
                            name_part = name_part.strip()
                            logged_today.add(name_part)
                            
                            status = "PASS" if "(PASS)" in details else "FAIL"
                            pct = 0.0
                            if "Uniform:" in details:
                                try:
                                    pct_str = details.split("Uniform:")[1].split("%")[0].strip()
                                    pct = float(pct_str)
                                except Exception:
                                    pass
                            
                            if status == "PASS":
                                total_passed += 1
                            else:
                                total_failed += 1
                                
                            recent_activity.append({
                                'time': time_part,
                                'name': name_part,
                                'status': status,
                                'pct': pct
                            })
            # Keep only the last 10 activities
            recent_activity = recent_activity[-10:]
            print(f"Loaded {len(logged_today)} logged student(s) for today (Pass: {total_passed}, Fail: {total_failed}).")
        except Exception as e:
            print(f"Error reading log file: {e}")

def log_student(name, uniform_pct, status):
    """Log student to daily file and update live statistics"""
    global logged_today, recent_activity, total_passed, total_failed
    
    if name in logged_today:
        return
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_dir = Path(f"logs/{today_str}")
    log_file = log_dir / "attendance.txt"
    
    current_time = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{current_time}] {name} - Uniform: {uniform_pct:.1f}% ({status})\n"
    
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
        
        logged_today.add(name)
        if status == "PASS":
            total_passed += 1
        else:
            total_failed += 1
            
        recent_activity.append({
            'time': current_time,
            'name': name,
            'status': status,
            'pct': uniform_pct
        })
        if len(recent_activity) > 10:
            recent_activity.pop(0)
            
        print(f"[LOGGED] {name} -> {status} ({uniform_pct:.1f}%)")
    except Exception as e:
        print(f"Logging error: {e}")

init_logging()


# =============================================================================
# 3. AUDIO FEEDBACK & SPEECH WORKER (NON-BLOCKING)
# =============================================================================
speech_queue = queue.Queue()
is_speaking = False
speaking_start_time = 0

def audio_worker():
    """Dedicated audio worker thread to ensure zero UI delay"""
    global is_speaking, speaking_start_time
    try:
        import winsound
        engine = pyttsx3.init()
        voices = engine.getProperty('voices')
        if voices:
            engine.setProperty('voice', voices[0].id)
        engine.setProperty('rate', 135)
        engine.setProperty('volume', 1.0)
        
        while True:
            item = speech_queue.get()
            if item is None:
                break
            
            try:
                is_speaking = True
                speaking_start_time = time.time()
                
                if item.startswith("SOUND:"):
                    sound_type = item.split(":")[1]
                    if sound_type == "PASS":
                        # Crisp high-pitched success chime
                        winsound.Beep(1200, 180)
                    elif sound_type == "FAIL":
                        # Warning alert buzz
                        winsound.Beep(420, 320)
                else:
                    # Speech greeting
                    engine.say(item)
                    engine.runAndWait()
                    
            except Exception as e:
                print(f"Audio playback error: {e}")
            finally:
                is_speaking = False
            speech_queue.task_done()
    except Exception as e:
        print(f"Audio initialization error: {e}")

audio_thread = threading.Thread(target=audio_worker, daemon=True)
audio_thread.start()

def speak_status(status):
    """Queue audio status sound"""
    if status == "PASS":
        speech_queue.put("SOUND:PASS")
    else:
        speech_queue.put("SOUND:FAIL")

# Backward compatibility alias
pyhtospeak_status = speak_status


# =============================================================================
# 4. UNIFORM COLOR DETECTION
# =============================================================================
def detect_uniform(frame, person_region, threshold=None):
    """
    Detect if person is wearing a uniform based on HSV color detection.
    Returns: (is_wearing_uniform, uniform_percentage)
    """
    top, right, bottom, left = person_region
    face_height = bottom - top
    face_width = right - left
    
    # Torso region relative to face position
    torso_top = min(max(0, bottom + int(face_height * 0.1)), frame.shape[0] - 1)
    torso_bottom = min(bottom + int(face_height * 1.5), frame.shape[0])
    
    torso_width = int(face_width * 1.2)
    center_x = (left + right) // 2
    torso_left = max(0, center_x - torso_width // 2)
    torso_right = min(frame.shape[1], center_x + torso_width // 2)
    
    torso = frame[torso_top:torso_bottom, torso_left:torso_right]
    if torso.size == 0 or torso.shape[0] < 5 or torso.shape[1] < 5:
        return False, 0.0
    
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    if threshold is None:
        threshold = UNIFORM_THRESHOLD
        
    combined_mask = None
    for color_config in UNIFORM_COLORS:
        lower = np.array(color_config['lower'])
        upper = np.array(color_config['upper'])
        color_mask = cv2.inRange(hsv, lower, upper)
        
        if combined_mask is None:
            combined_mask = color_mask
        else:
            combined_mask = cv2.bitwise_or(combined_mask, color_mask)
            
    total_pixels = torso.shape[0] * torso.shape[1]
    uniform_pixels = cv2.countNonZero(combined_mask)
    uniform_percentage = (uniform_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
    
    is_wearing_uniform = uniform_percentage >= threshold
    return is_wearing_uniform, uniform_percentage


# =============================================================================
# 5. ASYNCHRONOUS AI RECOGNITION WORKER (ELIMINATES ALL LAG)
# =============================================================================
class AIWorker:
    """
    Runs face recognition & uniform checking in a background thread.
    Allows the camera and UI loop to run at full 30+ FPS without ANY lag or stutter!
    """
    def __init__(self, encodings, names):
        self.known_encodings = encodings
        self.known_names = names
        
        self.lock = threading.Lock()
        self.pending_frame = None
        self.has_new_frame = False
        self.running = True
        
        self.latest_detections = []
        self.ai_latency_ms = 0.0
        self.trackers = {}
        
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()

    def submit_frame(self, frame):
        """Submit frame for recognition if worker is ready (non-blocking)"""
        if self.lock.acquire(blocking=False):
            try:
                if not self.has_new_frame:
                    self.pending_frame = frame.copy()
                    self.has_new_frame = True
                    return True
            finally:
                self.lock.release()
        return False

    def get_detections(self):
        """Fetch latest recognition results (thread-safe, non-blocking)"""
        with self.lock:
            return list(self.latest_detections), self.ai_latency_ms

    def _worker_loop(self):
        global is_speaking, speaking_start_time
        
        while self.running:
            frame_to_process = None
            with self.lock:
                if self.has_new_frame and self.pending_frame is not None:
                    frame_to_process = self.pending_frame
                    self.pending_frame = None
                    self.has_new_frame = False

            if frame_to_process is None:
                time.sleep(0.005)
                continue

            t_start = time.time()
            
            # Downscale frame for ultra-fast HOG face detection
            small_frame = cv2.resize(frame_to_process, (0, 0), fx=0.25, fy=0.25)
            rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            # Detect face locations
            face_locations = face_recognition.face_locations(rgb_small, model="hog")
            detections = []
            current_frame_names = set()

            if face_locations:
                # Extract face encodings
                face_encodings = face_recognition.face_encodings(rgb_small, face_locations, num_jitters=1)
                
                for face_encoding, face_loc in zip(face_encodings, face_locations):
                    matches = face_recognition.compare_faces(self.known_encodings, face_encoding, tolerance=0.45)
                    name = "Unknown"
                    confidence = 0.0
                    
                    face_distances = face_recognition.face_distance(self.known_encodings, face_encoding)
                    if len(face_distances) > 0:
                        best_match_idx = int(face_distances.argmin())
                        if matches[best_match_idx]:
                            name = self.known_names[best_match_idx]
                            confidence = max(0.0, min(1.0, 1.0 - float(face_distances[best_match_idx])))

                    current_frame_names.add(name)

                    # Scale coordinates back up to full frame
                    top, right, bottom, left = face_loc
                    top *= 4
                    right *= 4
                    bottom *= 4
                    left *= 4

                    # Parse gender and display name
                    full_name = name
                    display_name = name
                    is_male = False
                    if "  " in full_name:
                        parts = full_name.split("  ")
                        display_name = parts[0]
                        if len(parts) > 1 and "MALE" in parts[1].upper():
                            is_male = True

                    # Threshold calculation
                    threshold = UNIFORM_THRESHOLD + (MALE_THRESHOLD_EXTRA if is_male else 0)
                    has_uniform, uniform_pct = detect_uniform(
                        frame_to_process, (top, right, bottom, left), threshold=threshold
                    )

                    # Crop high-res face avatar for UI
                    pad_y = int((bottom - top) * 0.15)
                    pad_x = int((right - left) * 0.15)
                    c_top = max(0, top - pad_y)
                    c_bot = min(frame_to_process.shape[0], bottom + pad_y)
                    c_left = max(0, left - pad_x)
                    c_right = min(frame_to_process.shape[1], right + pad_x)
                    
                    face_crop = None
                    if c_bot > c_top and c_right > c_left:
                        face_crop = frame_to_process[c_top:c_bot, c_left:c_right].copy()

                    # Persistence and audio announcement tracking
                    if full_name not in self.trackers:
                        self.trackers[full_name] = {'count': 0, 'state': None, 'greeted': False}
                    
                    tracker = self.trackers[full_name]
                    current_state = None
                    if display_name != "Unknown":
                        if has_uniform and confidence > 0.45:
                            current_state = "PASS"
                        elif not has_uniform:
                            current_state = "FAIL"
                            
                    if current_state == tracker['state'] and current_state is not None:
                        tracker['count'] += 1
                    else:
                        tracker['count'] = 0
                        tracker['state'] = current_state

                    # Trigger greeting / audio after 3 consistent frames
                    if tracker['count'] >= 3:
                        if not tracker['greeted']:
                            if display_name != "Unknown":
                                speech_queue.put(f"{display_name}, have a great day.")
                                if current_state:
                                    log_student(display_name, uniform_pct, current_state)
                            else:
                                speech_queue.put("Have a great day.")
                            tracker['greeted'] = True
                        elif not is_speaking and speech_queue.empty():
                            if current_state == "PASS":
                                speak_status("PASS")
                            elif current_state == "FAIL":
                                speak_status("FAIL")

                    # Watchdog for stuck speech
                    now = time.time()
                    if is_speaking and (now - speaking_start_time > 2.5):
                        is_speaking = False

                    detections.append({
                        'name': full_name,
                        'display_name': display_name,
                        'confidence': confidence,
                        'has_uniform': has_uniform,
                        'uniform_pct': uniform_pct,
                        'threshold': threshold,
                        'is_male': is_male,
                        'location': (top, right, bottom, left),
                        'face_crop': face_crop,
                        'timestamp': now
                    })

            # Cleanup exited trackers
            active_names = list(self.trackers.keys())
            for t_name in active_names:
                if t_name not in current_frame_names:
                    del self.trackers[t_name]

            latency = (time.time() - t_start) * 1000.0

            with self.lock:
                self.latest_detections = detections
                self.ai_latency_ms = latency


# =============================================================================
# 6. NEXT-GEN 10X SCI-FI BIOMETRIC HUD RENDERING
# =============================================================================
class BiometricHUD:
    """
    Renders high-tech, futuristic biometric overlays, reticles, and side panel
    with antialiased graphics and smooth bounding-box interpolation.
    """
    # Cyber Color Palette (BGR)
    COLOR_BG_OBSIDIAN = (18, 14, 12)       # Darkest slate
    COLOR_PANEL_BG    = (26, 21, 18)       # Rich panel dark
    COLOR_CARD_BG     = (38, 30, 24)       # Card dark fill
    COLOR_CARD_BORDER = (65, 52, 42)       # Card border
    COLOR_ACCENT_CYAN = (255, 205, 0)      # Neon Cyan / Electric Blue
    COLOR_ACCENT_DIM  = (160, 110, 0)      # Dim Cyan
    COLOR_PASS_NEON   = (80, 225, 75)      # Emerald Neon
    COLOR_FAIL_NEON   = (65, 70, 245)      # Cyber Crimson
    COLOR_WARN_AMBER  = (0, 180, 255)      # Cyber Amber
    COLOR_TEXT_WHITE  = (255, 255, 255)
    COLOR_TEXT_SILVER = (215, 220, 225)
    COLOR_TEXT_MUTED  = (135, 145, 160)

    def __init__(self, panel_width=380):
        self.panel_width = panel_width
        self.smoothed_boxes = {}  # key -> [top, right, bottom, left]
        self.last_seen = {}       # key -> timestamp
        self.radar_angle = 0.0

    def draw_hud_brackets(self, img, pt1, pt2, color, length=24, thickness=2):
        """Draw 4 futuristic target lock bracket corners"""
        x1, y1 = pt1
        x2, y2 = pt2
        l = min(length, (x2 - x1) // 3, (y2 - y1) // 3)
        
        # Top-Left
        cv2.line(img, (x1, y1), (x1 + l, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y1), (x1 + l, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y1), (x1, y1 + l), color, thickness, cv2.LINE_AA)
        # Top-Right
        cv2.line(img, (x2, y1), (x2 - l, y1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x2, y1), (x2, y1 + l), color, thickness, cv2.LINE_AA)
        # Bottom-Left
        cv2.line(img, (x1, y2), (x1 + l, y2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x1, y2), (x1, y2 - l), color, thickness, cv2.LINE_AA)
        # Bottom-Right
        cv2.line(img, (x2, y2), (x2 - l, y2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (x2, y2), (x2 - l, y2), color, thickness, cv2.LINE_AA)

    def draw_card(self, img, pt1, pt2, border_color, bg_color, corner_length=15):
        """Draw a high-tech card container with glowing tech corner notches"""
        x1, y1 = pt1
        x2, y2 = pt2
        cv2.rectangle(img, (x1, y1), (x2, y2), bg_color, -1)
        cv2.rectangle(img, (x1, y1), (x2, y2), border_color, 1)
        
        c = min(corner_length, (x2 - x1) // 4, (y2 - y1) // 4)
        cv2.line(img, (x1, y1), (x1 + c, y1), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x1, y1), (x1, y1 + c), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x2, y1), (x2 - c, y1), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x2, y1), (x2, y1 + c), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x1, y2), (x1 + c, y2), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x1, y2), (x1, y2 - c), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x2, y2), (x2 - c, y2), border_color, 2, cv2.LINE_AA)
        cv2.line(img, (x2, y2), (x2 - c, y2), border_color, 2, cv2.LINE_AA)

    def draw_progress_bar(self, img, x, y, w, h, pct, fill_color, threshold=None):
        """Draw an illuminated status meter with threshold notch"""
        pct_clamped = max(0.0, min(100.0, pct))
        # Background track
        cv2.rectangle(img, (x, y), (x + w, y + h), (18, 16, 15), -1)
        # Active fill
        fill_w = int(w * (pct_clamped / 100.0))
        if fill_w > 0:
            cv2.rectangle(img, (x, y), (x + fill_w, y + h), fill_color, -1)
        cv2.rectangle(img, (x, y), (x + w, y + h), (70, 65, 60), 1)
        
        # Draw threshold target marker
        if threshold is not None and 0 < threshold < 100:
            tx = x + int(w * (threshold / 100.0))
            cv2.line(img, (tx, y - 2), (tx, y + h + 2), (255, 255, 255), 2, cv2.LINE_AA)

    def draw_camera_overlays(self, frame, fps, cam_index):
        """Draw live telemetry, framing brackets, and clock over camera view"""
        h, w = frame.shape[:2]
        
        # 1. Tech Framing Reticles in 4 corners
        l = 40
        c_color = self.COLOR_ACCENT_DIM
        cv2.line(frame, (15, 15), (15 + l, 15), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (15, 15), (15, 15 + l), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (w - 15, 15), (w - 15 - l, 15), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (w - 15, 15), (w - 15, 15 + l), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (15, h - 15), (15 + l, h - 15), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (15, h - 15), (15, h - 15 - l), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (w - 15, h - 15), (w - 15 - l, h - 15), c_color, 2, cv2.LINE_AA)
        cv2.line(frame, (w - 15, h - 15), (w - 15, h - 15 - l), c_color, 2, cv2.LINE_AA)

        # 2. Top-Left Live Telemetry Pill
        badge_w, badge_h = 245, 30
        overlay = frame.copy()
        cv2.rectangle(overlay, (20, 20), (20 + badge_w, 20 + badge_h), (12, 10, 8), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        cv2.rectangle(frame, (20, 20), (20 + badge_w, 20 + badge_h), self.COLOR_ACCENT_DIM, 1)
        
        # Pulsing green dot
        pulse = (int(time.time() * 3) % 2 == 0)
        dot_color = self.COLOR_PASS_NEON if pulse else (30, 120, 30)
        cv2.circle(frame, (32, 35), 5, dot_color, -1, cv2.LINE_AA)
        
        cam_label = "LAPTOP CAM" if cam_index == 0 else f"EXT CAM {cam_index}"
        cam_text = f"{cam_label} | {fps:4.1f} FPS | {w}x{h}"
        cv2.putText(frame, cam_text, (44, 39), cv2.FONT_HERSHEY_SIMPLEX, 0.42, self.COLOR_TEXT_WHITE, 1, cv2.LINE_AA)

        # 3. Top-Right Real-Time Clock Pill
        now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        clock_w = 205
        overlay = frame.copy()
        cv2.rectangle(overlay, (w - clock_w - 20, 20), (w - 20, 20 + badge_h), (12, 10, 8), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        cv2.rectangle(frame, (w - clock_w - 20, 20), (w - 20, 20 + badge_h), self.COLOR_ACCENT_DIM, 1)
        cv2.putText(frame, now_str, (w - clock_w - 12, 39), cv2.FONT_HERSHEY_SIMPLEX, 0.42, self.COLOR_ACCENT_CYAN, 1, cv2.LINE_AA)

    def draw_face_targets(self, frame, detections):
        """
        Draw smoothed Sci-Fi target brackets, floating labels, and uniform meters
        directly attached to detected faces.
        """
        now = time.time()
        current_keys = set()
        
        for person in detections:
            key = person['name']
            current_keys.add(key)
            target_box = np.array(person['location'], dtype=np.float32)
            
            if key in self.smoothed_boxes:
                # Smooth interpolation (alpha=0.4 gives instant responsiveness without jitter)
                self.smoothed_boxes[key] = self.smoothed_boxes[key] * 0.6 + target_box * 0.4
            else:
                self.smoothed_boxes[key] = target_box
            self.last_seen[key] = now

        # Prune older untracked faces after 0.5s grace period
        for key in list(self.smoothed_boxes.keys()):
            if key not in current_keys and (now - self.last_seen.get(key, 0) > 0.5):
                del self.smoothed_boxes[key]
                if key in self.last_seen:
                    del self.last_seen[key]

        # Render each tracked face
        for person in detections:
            key = person['name']
            box = self.smoothed_boxes.get(key, person['location'])
            top, right, bottom, left = [int(v) for v in box]
            
            # Status colors
            is_unknown = (person['display_name'] == "Unknown")
            has_uniform = person['has_uniform']
            
            if is_unknown:
                theme_color = self.COLOR_WARN_AMBER
                status_text = "UNREGISTERED"
            elif has_uniform:
                theme_color = self.COLOR_PASS_NEON
                status_text = "FULL UNIFORM"
            else:
                theme_color = self.COLOR_FAIL_NEON
                status_text = "NO UNIFORM"

            # 1. Sci-Fi Corner Brackets
            self.draw_hud_brackets(frame, (left, top), (right, bottom), theme_color, length=24, thickness=2)
            
            # Subtle center targeting tick marks
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            cv2.line(frame, (cx - 8, cy), (cx + 8, cy), (theme_color[0]//2, theme_color[1]//2, theme_color[2]//2), 1, cv2.LINE_AA)
            cv2.line(frame, (cx, cy - 8), (cx, cy + 8), (theme_color[0]//2, theme_color[1]//2, theme_color[2]//2), 1, cv2.LINE_AA)

            # 2. Floating Name Pill above the face
            label = person['display_name']
            (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.65, 1)
            pill_w = max(text_w + 20, 110)
            pill_h = 26
            pill_x = max(10, left)
            pill_y = max(10, top - pill_h - 8)

            overlay = frame.copy()
            cv2.rectangle(overlay, (pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h), (15, 12, 10), -1)
            cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
            cv2.rectangle(frame, (pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h), theme_color, 1)
            cv2.putText(frame, label, (pill_x + 8, pill_y + 18), cv2.FONT_HERSHEY_DUPLEX, 0.6, self.COLOR_TEXT_WHITE, 1, cv2.LINE_AA)

            # 3. Floating Status & Uniform % below the face
            bot_w = max(right - left, 140)
            bot_h = 24
            bot_x = left
            bot_y = min(frame.shape[0] - bot_h - 5, bottom + 8)

            overlay = frame.copy()
            cv2.rectangle(overlay, (bot_x, bot_y), (bot_x + bot_w, bot_y + bot_h), (15, 12, 10), -1)
            cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
            cv2.rectangle(frame, (bot_x, bot_y), (bot_x + bot_w, bot_y + bot_h), theme_color, 1)
            
            status_label = status_text
            cv2.putText(frame, status_label, (bot_x + 6, bot_y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, theme_color, 1, cv2.LINE_AA)

    def draw_telemetry_panel(self, h, detections, ai_latency, external_cam_idx):
        """
        Build the futuristic intelligence & telemetry panel (right-hand column)
        """
        panel = np.zeros((h, self.panel_width, 3), dtype=np.uint8)
        panel[:] = self.COLOR_PANEL_BG
        pw = self.panel_width

        # -------------------------------------------------------------
        # 1. TOP HEADER BANNER
        # -------------------------------------------------------------
        banner_h = 68
        cv2.rectangle(panel, (0, 0), (pw, banner_h), (35, 26, 20), -1)
        cv2.line(panel, (0, banner_h), (pw, banner_h), self.COLOR_ACCENT_CYAN, 2)
        
        # Tech logo accent
        cv2.rectangle(panel, (16, 16), (46, 46), self.COLOR_ACCENT_CYAN, 1)
        cv2.line(panel, (20, 31), (42, 31), self.COLOR_ACCENT_CYAN, 2, cv2.LINE_AA)
        cv2.line(panel, (31, 20), (31, 42), self.COLOR_ACCENT_CYAN, 2, cv2.LINE_AA)
        
        # Header text
        cv2.putText(panel, "BIOMETRIC HUD", (56, 33), cv2.FONT_HERSHEY_DUPLEX, 0.7, self.COLOR_TEXT_WHITE, 1, cv2.LINE_AA)
        cv2.putText(panel, "UNIFORM COMPLIANCE & ACCESS", (56, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.36, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)

        # -------------------------------------------------------------
        # 2. TODAY'S ATTENDANCE STATS (3 MINI CARDS)
        # -------------------------------------------------------------
        y_stats = 78
        card_w = (pw - 40) // 3
        card_h = 52
        
        # Scanned Today
        self.draw_card(panel, (15, y_stats), (15 + card_w, y_stats + card_h), (55, 45, 38), (32, 25, 20))
        cv2.putText(panel, "SCANNED", (22, y_stats + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.35, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)
        cv2.putText(panel, str(len(logged_today)), (22, y_stats + 42), cv2.FONT_HERSHEY_DUPLEX, 0.7, self.COLOR_TEXT_WHITE, 1, cv2.LINE_AA)

        # Verified / Pass
        x_pass = 15 + card_w + 5
        self.draw_card(panel, (x_pass, y_stats), (x_pass + card_w, y_stats + card_h), (40, 80, 40), (22, 36, 22))
        cv2.putText(panel, "PASSED", (x_pass + 7, y_stats + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (140, 200, 140), 1, cv2.LINE_AA)
        cv2.putText(panel, str(total_passed), (x_pass + 7, y_stats + 42), cv2.FONT_HERSHEY_DUPLEX, 0.7, self.COLOR_PASS_NEON, 1, cv2.LINE_AA)

        # Violations / Fail
        x_fail = x_pass + card_w + 5
        self.draw_card(panel, (x_fail, y_stats), (x_fail + card_w, y_stats + card_h), (40, 40, 85), (36, 22, 25))
        cv2.putText(panel, "VIOLATION", (x_fail + 5, y_stats + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (140, 140, 220), 1, cv2.LINE_AA)
        cv2.putText(panel, str(total_failed), (x_fail + 7, y_stats + 42), cv2.FONT_HERSHEY_DUPLEX, 0.7, self.COLOR_FAIL_NEON, 1, cv2.LINE_AA)

        # -------------------------------------------------------------
        # 3. ACTIVE SUBJECT BIOMETRIC CARD
        # -------------------------------------------------------------
        y_subj = 142
        cv2.putText(panel, "ACTIVE BIOMETRIC SCAN", (16, y_subj), cv2.FONT_HERSHEY_SIMPLEX, 0.42, self.COLOR_ACCENT_CYAN, 1, cv2.LINE_AA)
        
        card_top = y_subj + 10
        card_bot = card_top + 160
        
        if detections:
            # Display primary subject
            person = detections[0]
            display_name = person['display_name']
            conf_pct = int(person['confidence'] * 100)
            has_uniform = person['has_uniform']
            uniform_pct = person['uniform_pct']
            threshold = person['threshold']
            is_male = person['is_male']
            face_crop = person.get('face_crop')

            # Status theme
            if display_name == "Unknown":
                theme_color = self.COLOR_WARN_AMBER
                banner_text = "UNREGISTERED SUBJECT"
            elif has_uniform:
                theme_color = self.COLOR_PASS_NEON
                banner_text = "UNIFORM VERIFIED"
            else:
                theme_color = self.COLOR_FAIL_NEON
                banner_text = "VIOLATION DETECTED"

            # Outer Card
            self.draw_card(panel, (15, card_top), (pw - 15, card_bot), theme_color, self.COLOR_CARD_BG, corner_length=18)
            
            # Real-Time Cropped Face Avatar
            avatar_size = 66
            av_x, av_y = 28, card_top + 14
            if face_crop is not None and face_crop.size > 0:
                try:
                    avatar_resized = cv2.resize(face_crop, (avatar_size, avatar_size))
                    panel[av_y:av_y + avatar_size, av_x:av_x + avatar_size] = avatar_resized
                except Exception:
                    cv2.rectangle(panel, (av_x, av_y), (av_x + avatar_size, av_y + avatar_size), (45, 38, 30), -1)
            else:
                cv2.rectangle(panel, (av_x, av_y), (av_x + avatar_size, av_y + avatar_size), (45, 38, 30), -1)
                cv2.putText(panel, "SCAN", (av_x + 12, av_y + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.45, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)
                
            # Avatar Frame
            cv2.rectangle(panel, (av_x, av_y), (av_x + avatar_size, av_y + avatar_size), theme_color, 2)

            # Details next to avatar
            tx = av_x + avatar_size + 14
            cv2.putText(panel, display_name[:15], (tx, av_y + 20), cv2.FONT_HERSHEY_DUPLEX, 0.65, self.COLOR_TEXT_WHITE, 1, cv2.LINE_AA)
            
            tag_text = "MALE (TIE REQ)" if is_male else "STUDENT"
            cv2.putText(panel, tag_text, (tx, av_y + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.38, self.COLOR_ACCENT_CYAN, 1, cv2.LINE_AA)
            cv2.putText(panel, f"Face Match: {conf_pct}%", (tx, av_y + 56), cv2.FONT_HERSHEY_SIMPLEX, 0.38, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)

            # Status Banner Pill inside card
            pill_y = av_y + avatar_size + 12
            pill_w = pw - 56
            cv2.rectangle(panel, (28, pill_y), (28 + pill_w, pill_y + 24), (20, 16, 14), -1)
            cv2.rectangle(panel, (28, pill_y), (28 + pill_w, pill_y + 24), theme_color, 1)
            cv2.putText(panel, banner_text, (38, pill_y + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, theme_color, 1, cv2.LINE_AA)

            # Uniform Precision Meter
            bar_y = pill_y + 32
            bar_w = pill_w
            self.draw_progress_bar(panel, 28, bar_y, bar_w, 12, uniform_pct, theme_color, threshold=threshold)
            
            score_str = f"Uniform Status: {'COMPLETE' if has_uniform else 'MISSING'}"
            cv2.putText(panel, score_str, (28, bar_y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.35, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)

        else:
            # Standby / Radar Scanning Mode
            self.draw_card(panel, (15, card_top), (pw - 15, card_bot), (50, 42, 35), self.COLOR_CARD_BG)
            
            # Animated Radar Graphic
            rcx, rcy = pw // 2, card_top + 60
            self.radar_angle = (self.radar_angle + 0.15) % (2 * np.pi)
            cv2.circle(panel, (rcx, rcy), 36, (40, 32, 26), 1, cv2.LINE_AA)
            cv2.circle(panel, (rcx, rcy), 22, (50, 40, 32), 1, cv2.LINE_AA)
            
            sweep_x = int(rcx + 36 * np.cos(self.radar_angle))
            sweep_y = int(rcy + 36 * np.sin(self.radar_angle))
            cv2.line(panel, (rcx, rcy), (sweep_x, sweep_y), self.COLOR_ACCENT_CYAN, 2, cv2.LINE_AA)
            
            cv2.putText(panel, "SEARCHING FOR SUBJECT...", (rcx - 90, card_top + 120), cv2.FONT_HERSHEY_SIMPLEX, 0.44, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)
            cv2.putText(panel, "Align face with camera viewfinder", (rcx - 95, card_top + 138), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (90, 85, 80), 1, cv2.LINE_AA)

        # -------------------------------------------------------------
        # 4. RECENT VERIFICATION ACTIVITY FEED
        # -------------------------------------------------------------
        y_act = card_bot + 22
        cv2.putText(panel, "RECENT VERIFICATIONS", (16, y_act), cv2.FONT_HERSHEY_SIMPLEX, 0.42, self.COLOR_ACCENT_CYAN, 1, cv2.LINE_AA)
        
        feed_top = y_act + 8
        max_feed_items = min(4, max(1, (h - feed_top - 42) // 30))
        
        if recent_activity:
            items_to_show = list(reversed(recent_activity[-max_feed_items:]))
            for i, item in enumerate(items_to_show):
                item_y = feed_top + i * 30
                status_color = self.COLOR_PASS_NEON if item['status'] == "PASS" else self.COLOR_FAIL_NEON
                
                # Mini row container
                cv2.rectangle(panel, (15, item_y), (pw - 15, item_y + 24), (22, 18, 16), -1)
                cv2.rectangle(panel, (15, item_y), (pw - 15, item_y + 24), (45, 36, 30), 1)
                
                # Timestamp
                cv2.putText(panel, item['time'], (22, item_y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.36, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)
                # Name
                cv2.putText(panel, item['name'][:16], (85, item_y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.38, self.COLOR_TEXT_WHITE, 1, cv2.LINE_AA)
                # Status Tag
                tag = "COMPLETE" if item['status'] == "PASS" else "MISSING"
                cv2.putText(panel, tag, (pw - 85, item_y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.36, status_color, 1, cv2.LINE_AA)
        else:
            cv2.putText(panel, "No attendance records yet today.", (20, feed_top + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.36, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)

        # -------------------------------------------------------------
        # 5. SYSTEM DIAGNOSTICS FOOTER
        # -------------------------------------------------------------
        footer_y = h - 30
        cv2.rectangle(panel, (0, footer_y), (pw, h), (16, 12, 10), -1)
        cv2.line(panel, (0, footer_y), (pw, footer_y), (40, 32, 26), 1)
        
        cam_label = "LAPTOP CAM" if external_cam_idx == 0 else f"EXT CAM {external_cam_idx}"
        diag_str = f"{cam_label}  |  AI Latency: {ai_latency:.0f}ms  |  Audio: ON"
        cv2.putText(panel, diag_str, (16, footer_y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.35, self.COLOR_TEXT_MUTED, 1, cv2.LINE_AA)

        return panel


# =============================================================================
# 7. MAIN CAPTURE & ASYNC PIPELINE LOOP
# =============================================================================
def capture_and_recognize():
    import atexit
    
    print("Searching for camera devices...")
    cap = None
    selected_cam_idx = None
    
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
                            selected_cam_idx = cam_idx
                            backend_name = "DirectShow" if backend == cv2.CAP_DSHOW else "Default"
                            print(f"[OK] Successfully connected to {stage_name} (Index {cam_idx}) using {backend_name}")
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
        print("Checked external webcam indices (1, 2, 3) and laptop camera (0).")
        print("Please ensure your webcam is plugged in or camera permissions are granted.")
        return

    # Set camera buffer size to 1 to eliminate frame lag
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # Initialize Asynchronous AI Worker
    print("Starting Asynchronous AI Recognition Pipeline...")
    ai_worker = AIWorker(known_face_encodings, known_face_names)
    hud = BiometricHUD(panel_width=380)

    def cleanup():
        ai_worker.running = False
        if cap.isOpened():
            cap.release()
        cv2.destroyAllWindows()
        print("Camera and resources released.")

    atexit.register(cleanup)

    print("\n" + "="*60)
    print("✓ External Camera Active: Index", selected_cam_idx)
    print("✓ Asynchronous Zero-Lag Pipeline: RUNNING")
    print("✓ Next-Gen Biometric HUD: READY")
    print("Press 'q' in the camera window to exit.")
    print("="*60 + "\n")

    window_name = 'Tesco Uniform Recognition & Biometric System'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    # FPS Calculation
    fps = 30.0
    prev_time = time.time()

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                print("Error: Could not read frame from camera.")
                break

            # Measure smooth FPS
            now = time.time()
            dt = now - prev_time
            prev_time = now
            if dt > 0:
                current_fps = 1.0 / dt
                fps = fps * 0.9 + current_fps * 0.1

            # Submit frame to background AI thread (non-blocking)
            ai_worker.submit_frame(frame)

            # Retrieve latest detections and latency (instantaneous, never blocks!)
            detections, ai_latency = ai_worker.get_detections()

            # Render Camera HUD & Face Target Reticles directly on frame
            hud.draw_camera_overlays(frame, fps, selected_cam_idx)
            hud.draw_face_targets(frame, detections)

            # Render Biometric Telemetry Side Panel
            panel = hud.draw_telemetry_panel(frame.shape[0], detections, ai_latency, selected_cam_idx)

            # Combine camera frame and side panel
            final_display = np.hstack([frame, panel])

            # Display
            cv2.imshow(window_name, final_display)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
    except Exception as e:
        print(f"\nRuntime error: {e}")
    finally:
        cleanup()


if __name__ == "__main__":
    capture_and_recognize()
