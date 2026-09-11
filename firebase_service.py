"""
VeriUniform Sentinel - Firebase Service Module
----------------------------------------------
Provides real-time Firebase Admin SDK integration, Firestore database sync,
non-blocking asynchronous queue processing, automatic connection retries,
and local disk fallback for camera feeds.
"""

import os
import json
import time
import logging
import threading
import queue
from datetime import datetime
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FirebaseService")

# Global Firebase state
_app_initialized = False
_firestore_db = None
_project_id = "uniform-recognition-app"
_log_queue = queue.Queue(maxsize=2000)
_worker_thread = None
_stats = {
    "logged_events_count": 0,
    "failed_events_count": 0,
    "retried_count": 0,
    "last_error": None,
    "last_success_time": None
}

# Optional Firebase import check
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.warning("firebase_admin package not installed. Fallback disk logging will be used.")


def initialize_firebase(service_account_path=None):
    """
    Initialize Firebase Admin SDK with credentials or environment configuration.
    Idempotent and safe against multiple initializations.
    """
    global _app_initialized, _firestore_db, _project_id, _worker_thread

    if not FIREBASE_AVAILABLE:
        _stats["last_error"] = "firebase_admin package not available"
        logger.error("Cannot initialize Firebase: firebase_admin module missing.")
        _start_worker_thread()
        return False

    if _app_initialized and _firestore_db is not None:
        return True

    # Check if already initialized by firebase_admin internal state
    if firebase_admin._apps:
        try:
            _firestore_db = firestore.client()
            _app_initialized = True
            logger.info("Connected to existing Firebase Admin app instance.")
            _start_worker_thread()
            return True
        except Exception as e:
            logger.warning(f"Failed getting Firestore client from existing app: {e}")

    # Determine credentials path
    candidate_paths = []
    if service_account_path:
        candidate_paths.append(service_account_path)

    env_key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")
    if env_key:
        candidate_paths.append(env_key)

    google_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if google_creds:
        candidate_paths.append(google_creds)

    # Project default paths
    candidate_paths.extend([
        os.path.join("firebase", "serviceAccountKey.json"),
        "serviceAccountKey.json",
        os.path.join(os.path.dirname(__file__), "firebase", "serviceAccountKey.json")
    ])

    cred = None
    used_path = None

    for path in candidate_paths:
        if path and os.path.exists(path):
            try:
                cred = credentials.Certificate(path)
                used_path = path
                logger.info(f"Loaded Firebase service account key from: {path}")
                break
            except Exception as e:
                logger.warning(f"Error loading credentials from {path}: {e}")

    try:
        if cred:
            app = firebase_admin.initialize_app(cred, {
                'projectId': _project_id
            })
        else:
            logger.info("No service account file found. Attempting Application Default Credentials...")
            cred = credentials.ApplicationDefault()
            app = firebase_admin.initialize_app(cred, {
                'projectId': _project_id
            })

        _firestore_db = firestore.client()
        _app_initialized = True
        logger.info("Firebase Admin SDK successfully initialized for VeriUniform Sentinel!")
    except Exception as e:
        _stats["last_error"] = str(e)
        logger.error(f"Failed to initialize Firebase Admin SDK: {e}")

    _start_worker_thread()
    return _app_initialized


def _start_worker_thread():
    """Ensure the background log processor worker thread is running."""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_firebase_worker, daemon=True, name="FirebaseLoggerWorker")
        _worker_thread.start()


def _firebase_worker():
    """Background worker processing log items from queue to Firebase with retries."""
    global _stats
    while True:
        try:
            item = _log_queue.get()
            if item is None:
                break

            collection_name, doc_id, data_dict, retry_count = item

            success = _write_to_firebase(collection_name, doc_id, data_dict)

            if success:
                _stats["logged_events_count"] += 1
                _stats["last_success_time"] = datetime.now().isoformat()
            else:
                if retry_count < 3:
                    _stats["retried_count"] += 1
                    backoff = (2 ** retry_count) * 0.5  # 0.5s, 1s, 2s
                    time.sleep(backoff)
                    logger.info(f"Retrying Firebase write to '{collection_name}' (attempt {retry_count + 2}/4)...")
                    _log_queue.put((collection_name, doc_id, data_dict, retry_count + 1))
                else:
                    _stats["failed_events_count"] += 1
                    logger.error(f"Exhausted retries for Firebase write to '{collection_name}'. Writing fallback log.")
                    _fallback_disk_log(collection_name, doc_id, data_dict)

            _log_queue.task_done()
        except Exception as e:
            _stats["last_error"] = str(e)
            logger.error(f"Error in Firebase worker thread: {e}")
            time.sleep(1)


def _write_to_firebase(collection_name, doc_id, data_dict):
    """Perform actual sync write to Firestore."""
    if not _app_initialized or _firestore_db is None:
        return False

    try:
        col_ref = _firestore_db.collection(collection_name)
        if doc_id:
            col_ref.document(doc_id).set(data_dict, merge=True)
        else:
            col_ref.add(data_dict)
        return True
    except Exception as e:
        _stats["last_error"] = str(e)
        logger.warning(f"Firestore write error on '{collection_name}': {e}")
        return False


def _fallback_disk_log(collection_name, doc_id, data_dict):
    """Write log entry to local disk fallback JSONL file when Firebase is unavailable."""
    try:
        log_dir = Path("logs") / "firebase_fallback"
        log_dir.mkdir(parents=True, exist_ok=True)
        fallback_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"

        entry = {
            "collection": collection_name,
            "doc_id": doc_id,
            "data": data_dict,
            "fallback_timestamp": datetime.now().isoformat()
        }

        with open(fallback_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed writing disk fallback log: {e}")


def enqueue_log(collection_name, data_dict, doc_id=None):
    """
    Queue a document for asynchronous real-time Firebase writing.
    Non-blocking to preserve computer vision loop frame rate.
    """
    now = datetime.now()
    payload = {
        "timestamp": now.timestamp(),
        "datetime_iso": now.isoformat(),
        "date_str": now.strftime("%Y-%m-%d"),
        "time_str": now.strftime("%H:%M:%S")
    }
    payload.update(data_dict)

    try:
        _log_queue.put_nowait((collection_name, doc_id, payload, 0))
        return True
    except queue.Full:
        logger.warning("Firebase log queue full! Writing direct fallback log.")
        _fallback_disk_log(collection_name, doc_id, payload)
        return False


def log_attendance_scan(name, uniform_pct, status, confidence=None, metadata=None):
    """
    Write an attendance scan event to Firebase in real-time.
    Collection: 'attendance_scans'
    """
    data = {
        "name": name,
        "uniform_pct": float(uniform_pct),
        "status": status,  # "PASS" or "FAIL"
        "confidence": float(confidence) if confidence is not None else None,
        "system": "VeriUniform Sentinel",
        "event_type": "ATTENDANCE_SCAN"
    }
    if metadata:
        data.update(metadata)

    # Document ID format: YYYY-MM-DD_HHMMSS_Name
    doc_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name.replace(' ', '_')}"
    return enqueue_log("attendance_scans", data, doc_id=doc_id)


def log_facial_recognition(name, confidence, location=None, metadata=None):
    """
    Write a facial recognition log event to Firebase in real-time.
    Collection: 'facial_recognition_logs'
    """
    data = {
        "name": name,
        "confidence": float(confidence) if confidence is not None else 0.0,
        "is_known": name != "Unknown",
        "location": list(location) if location else None,
        "system": "VeriUniform Sentinel",
        "event_type": "FACIAL_RECOGNITION"
    }
    if metadata:
        data.update(metadata)

    return enqueue_log("facial_recognition_logs", data)


def log_compliance_event(name, status, uniform_pct, is_male=False, threshold=None, metadata=None):
    """
    Write a compliance status update to Firebase in real-time.
    Collection: 'compliance_statuses'
    """
    data = {
        "name": name,
        "compliance_status": status,  # "PASS" or "FAIL"
        "uniform_percentage": float(uniform_pct),
        "is_male": is_male,
        "threshold": float(threshold) if threshold is not None else None,
        "system": "VeriUniform Sentinel",
        "event_type": "COMPLIANCE_STATUS"
    }
    if metadata:
        data.update(metadata)

    return enqueue_log("compliance_statuses", data)


def update_sentinel_status(status_dict):
    """Update live Sentinel system metrics document in Firebase Firestore."""
    data = {
        "last_updated": datetime.now().isoformat(),
        "system_name": "VeriUniform Sentinel"
    }
    data.update(status_dict)
    return enqueue_log("sentinel_status", data, doc_id="current_status")


def get_connection_status():
    """Return status summary of Firebase connection and queue statistics."""
    return {
        "firebase_available": FIREBASE_AVAILABLE,
        "initialized": _app_initialized,
        "project_id": _project_id,
        "pending_queue_size": _log_queue.qsize(),
        "logged_events_count": _stats["logged_events_count"],
        "failed_events_count": _stats["failed_events_count"],
        "retried_count": _stats["retried_count"],
        "last_error": _stats["last_error"],
        "last_success_time": _stats["last_success_time"]
    }
