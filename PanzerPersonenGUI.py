import tkinter as tk
from tkinter import messagebox
from pathlib import Path
import queue
import socket
import threading
import math
import time
from PIL import Image, ImageTk

# Libraries for XIAO ESP32S3 Camera
import cv2
import urllib.request
import numpy as np
from ultralytics import YOLO

class TankTrackerGUI:
    """
    Tank-style control GUI with person tracking using YOLO.
    Automatically follows detected persons.
    """
    def __init__(self, root):
        self.root = root
        self.root.title("Tank Robot with Person Tracker")
        self.root.geometry("1200x800")
        self.root.configure(bg="#2c3e50")
        
        # Configure grid weights for responsive layout
        self.root.grid_columnconfigure(0, weight=3)
        self.root.grid_columnconfigure(1, weight=2)
        self.root.grid_rowconfigure(1, weight=1)
        
        self.socket = None
        self.socket_thread = None
        self.conn = None
        self.closing = threading.Event()
        self.connection_events = queue.Queue()
        self.last_command_at = 0.0
        self.command_refresh_interval = 0.2  # ESP32 watchdog needs fresh commands.
        self.status = tk.StringVar(value="Disconnected")
        self.tracking_status = tk.StringVar(value="Tracking: OFF")
        self.host = "192.168.1.111"  # Your laptop IP
        print("Server IP address:", self.host)
        self.port = 5001
        
        # Tank control variables
        self.vl = 0  # Left track velocity (-255 to 255)
        self.vr = 0  # Right track velocity (-255 to 255)
        self.acceleration = 20  # Acceleration step
        self.motor_test_after_id = None
        
        # Tracking variables
        self.tracking_active = False
        self.auto_mode = False
        self.last_person_position = None
        self.last_person = None
        self.person_detected = False
        self.person_class_id = 0  # COCO class "person"
        self.detection_confidence = 0.5
        self.tracking_revision = 0
        self.selected_target = None  # (clicked detection, original frame shape)
        self.displayed_people = []
        self.displayed_frame_at = None
        self.displayed_revision = None
        self.camera_image_geometry = None
        self.target_match_margin = 0.15
        # Bounding-box height is a rough proximity estimate, not a distance sensor.
        self.stop_person_height_ratio = 0.9
        self.resume_person_height_ratio = 0.8
        self.slow_person_height_ratio = 0.6
        self.follow_speed = 220
        self.follow_turn_gain = 90
        self.follow_max_pwm = 255
        self.follow_acceleration = 750  # PWM units per second, automatic mode only.
        self.follow_update_at = None
        self.proximity_stopped = False
        self.last_detection_at = None
        self.frame_timeout = 1.0  # Includes HTTP capture and inference time.
        self.follow_once_until = None
        self.tracking_threshold = 50  # Pixel distance threshold for adjustment
        self.center_x = 320  # Assuming 640x480 frame width/2
        self.center_y = 240  # Updated from the actual camera frame
        
        # Camera settings
        self.cameraip = '192.168.1.211'  # default camera IP
        self.camera_active = False
        self.current_frame = None
        self.frame_height = 480
        self.camera_stop = threading.Event()
        self.camera_after_id = None
        self.inference_lock = threading.Lock()
        
        # YOLO Model
        self.model = None
        self.load_yolo_model()
        
        # Create main container
        self.create_widgets()
        
        # Bind keyboard controls
        self.bind_keyboard()
        
        # Start socket connection
        self.start_socket()
        self.connection_after_id = self.root.after(100, self.poll_connection)
    
    def load_yolo_model(self):
        """Load YOLO model for person detection"""
        try:
            self.model = YOLO(str(Path(__file__).with_name("yolo11n.pt")))
            print("YOLO model loaded successfully")
        except Exception as e:
            print(f"Error loading YOLO model: {e}")
            messagebox.showerror("Model Error", "Could not load YOLO model. Tracking disabled.")
            self.model = None
    
    def create_widgets(self):
        # ============ HEADER ============
        header_frame = tk.Frame(self.root, bg="#34495e", height=60)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))
        header_frame.grid_propagate(False)
        
        # Title
        title_label = tk.Label(
            header_frame, text="🤖 TANK ROBOT CONTROLLER", 
            font=("Helvetica", 18, "bold"), bg="#34495e", fg="#ecf0f1"
        )
        title_label.pack(side="left", padx=20)
        
        # Status indicators
        status_frame = tk.Frame(header_frame, bg="#34495e")
        status_frame.pack(side="right", padx=20)
        
        # Connection status
        conn_circle = tk.Canvas(status_frame, width=20, height=20, bg="#34495e", highlightthickness=0)
        conn_circle.create_oval(2, 2, 18, 18, fill="red", outline="white", width=2)
        conn_circle.pack(side="left", padx=(0, 5))
        self.conn_circle = conn_circle
        
        self.status_label = tk.Label(
            status_frame, textvariable=self.status, font=("Helvetica", 12),
            bg="#34495e", fg="#ecf0f1"
        )
        self.status_label.pack(side="left", padx=(0, 20))
        
        # Tracking status
        track_circle = tk.Canvas(status_frame, width=20, height=20, bg="#34495e", highlightthickness=0)
        track_circle.create_oval(2, 2, 18, 18, fill="gray", outline="white", width=2)
        track_circle.pack(side="left", padx=(0, 5))
        self.track_circle = track_circle
        
        tk.Label(
            status_frame, textvariable=self.tracking_status, font=("Helvetica", 12),
            bg="#34495e", fg="#ecf0f1"
        ).pack(side="left")
        
        # ============ LEFT PANEL - CAMERA & TRACKING ============
        left_panel = tk.Frame(self.root, bg="#2c3e50")
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=5)
        
        # Camera display frame
        camera_frame = tk.LabelFrame(
            left_panel, text="Live Camera Feed", 
            font=("Helvetica", 11, "bold"),
            bg="#34495e", fg="white", bd=2, relief="ridge"
        )
        camera_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Camera display canvas
        self.camera_canvas = tk.Canvas(
            camera_frame, bg="#1c2833", highlightthickness=0
        )
        self.camera_canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self.camera_canvas.bind("<Button-1>", self.select_person_at_click)
        
        # Camera overlay info
        self.camera_info = tk.Label(
            camera_frame, text="Camera inactive", 
            font=("Helvetica", 10), bg="#1c2833", fg="#f39c12"
        )
        self.camera_info.place(relx=0.5, rely=0.5, anchor="center")
        
        # Camera controls
        cam_control_frame = tk.Frame(left_panel, bg="#2c3e50")
        cam_control_frame.pack(fill="x", padx=5, pady=5)
        
        # Camera IP input
        ip_frame = tk.Frame(cam_control_frame, bg="#2c3e50")
        ip_frame.pack(side="left", padx=5)
        
        tk.Label(ip_frame, text="Camera IP:", bg="#2c3e50", fg="white", 
                font=("Helvetica", 9)).pack(side="left", padx=2)
        self.entry_ip = tk.Entry(ip_frame, width=18, font=("Helvetica", 9))
        self.entry_ip.insert(0, self.cameraip)
        self.entry_ip.pack(side="left", padx=2)
        
        # Camera control buttons
        self.start_cam_btn = tk.Button(
            cam_control_frame, text="▶ Start Camera", command=self.start_camera,
            bg="#27ae60", fg="white", activebackground="#229954",
            font=("Helvetica", 9, "bold"), width=12
        )
        self.start_cam_btn.pack(side="left", padx=2)
        
        self.stop_cam_btn = tk.Button(
            cam_control_frame, text="■ Stop Camera", command=self.stop_camera,
            bg="#e74c3c", fg="white", activebackground="#c0392b",
            font=("Helvetica", 9, "bold"), width=12, state="disabled"
        )
        self.stop_cam_btn.pack(side="left", padx=2)
        
        # Detection info frame
        detect_frame = tk.LabelFrame(
            left_panel, text="Detection Info", 
            font=("Helvetica", 10, "bold"),
            bg="#34495e", fg="white", bd=1, relief="groove"
        )
        detect_frame.pack(fill="x", padx=5, pady=(5, 10))
        
        self.person_info = tk.Label(
            detect_frame, text="Start Tracking, click a person, then Auto Follow", 
            font=("Helvetica", 10), bg="#34495e", fg="#f1c40f"
        )
        self.person_info.pack(pady=5)
        
        # ============ RIGHT PANEL - CONTROLS ============
        right_panel = tk.Frame(self.root, bg="#2c3e50")
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=5)
        
        # Control buttons section
        control_section = tk.LabelFrame(
            right_panel, text="Tracking & Control", 
            font=("Helvetica", 11, "bold"),
            bg="#34495e", fg="white", bd=2, relief="ridge"
        )
        control_section.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Tracking control buttons
        track_frame = tk.Frame(control_section, bg="#34495e")
        track_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        self.track_btn = tk.Button(
            track_frame, text="🚀 Start Tracking", command=self.toggle_tracking,
            bg="#2980b9", fg="white", activebackground="#2471a3",
            font=("Helvetica", 10, "bold"), width=20, pady=8
        )
        self.track_btn.pack(pady=5)
        
        self.auto_follow_btn = tk.Button(
            track_frame, text="👤 Auto Follow", command=self.toggle_auto_follow,
            bg="#9b59b6", fg="white", activebackground="#8e44ad",
            font=("Helvetica", 10, "bold"), width=20, pady=8
        )
        self.auto_follow_btn.pack(pady=5)
        
        # Velocity display
        vel_frame = tk.Frame(control_section, bg="#34495e")
        vel_frame.pack(pady=10)
        
        tk.Label(
            vel_frame, text="CURRENT VELOCITY", 
            font=("Helvetica", 10, "bold"), bg="#34495e", fg="#bdc3c7"
        ).pack()
        
        self.velocity_display = tk.Label(
            vel_frame, text="LEFT: 0  |  RIGHT: 0", 
            font=("Helvetica", 14, "bold"), bg="#34495e", fg="#f39c12"
        )
        self.velocity_display.pack(pady=5)
        tk.Label(
            vel_frame, text="Manual buttons: 20 PWM per click (maximum 255)",
            font=("Helvetica", 9), bg="#34495e", fg="#bdc3c7",
        ).pack()
        
        # Track control frames
        tracks_frame = tk.Frame(control_section, bg="#34495e")
        tracks_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Left track control
        left_track_frame = tk.LabelFrame(
            tracks_frame, text="LEFT TRACK", 
            font=("Helvetica", 10, "bold"),
            bg="#2c3e50", fg="white", bd=1, relief="groove"
        )
        left_track_frame.pack(side="left", fill="both", expand=True, padx=5)
        
        self.left_value_label = tk.Label(
            left_track_frame, text="0", font=("Helvetica", 24, "bold"),
            bg="#2c3e50", fg="#3498db", width=4
        )
        self.left_value_label.pack(pady=10)
        
        tk.Button(
            left_track_frame, text="▲ FORWARD", command=self.left_forward,
            bg="#27ae60", fg="white", activebackground="#229954",
            font=("Helvetica", 9, "bold"), width=12, pady=6
        ).pack(pady=3)
        
        tk.Button(
            left_track_frame, text="▼ REVERSE", command=self.left_reverse,
            bg="#e74c3c", fg="white", activebackground="#c0392b",
            font=("Helvetica", 9, "bold"), width=12, pady=6
        ).pack(pady=3)
        
        tk.Button(
            left_track_frame, text="STOP", command=self.left_stop,
            bg="#7f8c8d", fg="white", activebackground="#95a5a6",
            font=("Helvetica", 9, "bold"), width=12, pady=6
        ).pack(pady=10)
        
        # Right track control
        right_track_frame = tk.LabelFrame(
            tracks_frame, text="RIGHT TRACK", 
            font=("Helvetica", 10, "bold"),
            bg="#2c3e50", fg="white", bd=1, relief="groove"
        )
        right_track_frame.pack(side="right", fill="both", expand=True, padx=5)
        
        self.right_value_label = tk.Label(
            right_track_frame, text="0", font=("Helvetica", 24, "bold"),
            bg="#2c3e50", fg="#3498db", width=4
        )
        self.right_value_label.pack(pady=10)
        
        tk.Button(
            right_track_frame, text="▲ FORWARD", command=self.right_forward,
            bg="#27ae60", fg="white", activebackground="#229954",
            font=("Helvetica", 9, "bold"), width=12, pady=6
        ).pack(pady=3)
        
        tk.Button(
            right_track_frame, text="▼ REVERSE", command=self.right_reverse,
            bg="#e74c3c", fg="white", activebackground="#c0392b",
            font=("Helvetica", 9, "bold"), width=12, pady=6
        ).pack(pady=3)
        
        tk.Button(
            right_track_frame, text="STOP", command=self.right_stop,
            bg="#7f8c8d", fg="white", activebackground="#95a5a6",
            font=("Helvetica", 9, "bold"), width=12, pady=6
        ).pack(pady=10)
        
        # Combined controls
        combined_frame = tk.Frame(control_section, bg="#34495e")
        combined_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        tk.Button(
            combined_frame, text="⏫ BOTH FORWARD", command=self.both_forward,
            bg="#2ecc71", fg="white", activebackground="#27ae60",
            font=("Helvetica", 9, "bold"), width=15, pady=8
        ).pack(side="left", padx=5)
        
        tk.Button(
            combined_frame, text="⏬ BOTH REVERSE", command=self.both_reverse,
            bg="#e74c3c", fg="white", activebackground="#c0392b",
            font=("Helvetica", 9, "bold"), width=15, pady=8
        ).pack(side="left", padx=5)
        
        tk.Button(
            combined_frame, text="⏹ FULL STOP", command=self.full_stop,
            bg="#f39c12", fg="white", activebackground="#d35400",
            font=("Helvetica", 9, "bold"), width=15, pady=8
        ).pack(side="left", padx=5)
        
        # Preset maneuvers
        preset_frame = tk.Frame(control_section, bg="#34495e")
        preset_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        tk.Label(
            preset_frame, text="Preset Maneuvers:", 
            font=("Helvetica", 9, "bold"), bg="#34495e", fg="#bdc3c7"
        ).pack(anchor="w", pady=(0, 5))
        
        maneuver_frame = tk.Frame(preset_frame, bg="#34495e")
        maneuver_frame.pack(fill="x")
        
        tk.Button(
            maneuver_frame, text="↶ Rotate Left", command=self.rotate_left,
            bg="#3498db", fg="white", activebackground="#2980b9",
            font=("Helvetica", 9), width=12, pady=5
        ).pack(side="left", padx=2)
        
        tk.Button(
            maneuver_frame, text="↷ Rotate Right", command=self.rotate_right,
            bg="#3498db", fg="white", activebackground="#2980b9",
            font=("Helvetica", 9), width=12, pady=5
        ).pack(side="left", padx=2)
        
        tk.Button(
            maneuver_frame, text="👥 Follow Person", command=self.follow_nearest_person,
            bg="#1abc9c", fg="white", activebackground="#16a085",
            font=("Helvetica", 9), width=12, pady=5
        ).pack(side="left", padx=2)

        test_frame = tk.LabelFrame(
            control_section, text="Motor test: one side, PWM 120, 1 second",
            font=("Helvetica", 9), bg="#34495e", fg="white",
        )
        test_frame.pack(fill="x", padx=10, pady=(0, 10))
        for column, (label, left, right) in enumerate([
            ("Left +", 120, 0), ("Left -", -120, 0),
            ("Right +", 0, 120), ("Right -", 0, -120),
        ]):
            test_frame.grid_columnconfigure(column, weight=1)
            tk.Button(
                test_frame, text=label,
                command=lambda l=left, r=right: self.test_motor(l, r),
                bg="#7f8c8d", fg="white", font=("Helvetica", 9),
            ).grid(row=0, column=column, sticky="ew", padx=2, pady=4)
        
        # ============ FOOTER ============
        footer_frame = tk.Frame(self.root, bg="#34495e", height=40)
        footer_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 10))
        footer_frame.grid_propagate(False)
        
        # Keyboard shortcuts info
        shortcuts = [
            "A/Q/Z: Left Track",
            "J/K/L: Right Track",
            "↑/↓: Both Tracks",
            "T: Toggle Tracking",
            "F: Auto-Follow",
            "Space: Full Stop"
        ]
        
        for i, shortcut in enumerate(shortcuts):
            tk.Label(
                footer_frame, text=shortcut, font=("Helvetica", 9),
                bg="#34495e", fg="#bdc3c7"
            ).pack(side="left", padx=15)
    
    def bind_keyboard(self):
        bindings = {
            "a": self.left_forward, "q": self.left_reverse, "z": self.left_stop,
            "l": self.right_forward, "j": self.right_reverse, "k": self.right_stop,
            "<Up>": self.both_forward, "<Down>": self.both_reverse,
            "t": self.toggle_tracking, "f": self.toggle_auto_follow,
        }
        for key, action in bindings.items():
            self.root.bind(key, lambda event, callback=action: self.handle_shortcut(event, callback))
        self.root.bind("<space>", lambda e: self.full_stop())
        self.root.bind("<Escape>", lambda e: self.on_closing())

    def handle_shortcut(self, event, action):
        """Typing a camera address must not move the robot."""
        if event.widget.winfo_class() not in {"Entry", "TEntry", "Text", "Spinbox"}:
            action()
            return "break"
    
    def update_display(self):
        """Update the velocity display labels"""
        self.left_value_label.config(text=str(self.vl))
        self.right_value_label.config(text=str(self.vr))
        self.velocity_display.config(text=f"LEFT: {self.vl}  |  RIGHT: {self.vr}")
        
        # Update connection circle color
        if self.conn:
            self.conn_circle.itemconfig(1, fill="green")  # 1 is the first item (circle)
            self.status.set("Connected")
        else:
            self.conn_circle.itemconfig(1, fill="red")
            self.status.set("Disconnected")
    
    def send_command(self, vl, vr):
        """Send left then right velocity, matching the ESP32 client protocol."""
        if self.conn:
            try:
                command = f"{vl} {vr}\n"
                self.conn.sendall(command.encode('utf-8'))
                self.last_command_at = time.monotonic()
                print(f"Sent: {command.strip()}")
            except OSError as e:
                print(f"Failed to send command: {e}")
                self.conn.close()
                self.conn = None
                self.auto_mode = False
                self.follow_once_until = None
                self.follow_update_at = None
                self.vl = self.vr = 0
                self.refresh_tracking_controls()
                self.update_display()
    
    def detect_person(self, frame, previous_person=None):
        """Detect people and match the clicked target by position; never access Tk."""
        if self.model is None:
            return None, frame, []

        results = self.model(
            frame, classes=[self.person_class_id], conf=self.detection_confidence,
            verbose=False,
        )
        height, width = frame.shape[:2]
        center = (width // 2, height // 2)
        annotated_frame = frame.copy()
        detections = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                if cls != self.person_class_id or conf < self.detection_confidence:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                x1, x2 = max(0, x1), min(width, x2)
                y1, y2 = max(0, y1), min(height, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                detections.append({
                    'bbox': (x1, y1, x2, y2),
                    'confidence': conf,
                    'center': ((x1 + x2) // 2, (y1 + y2) // 2),
                })
                # Draw only accepted person boxes, never unfiltered result.plot().
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(annotated_frame, f"person {conf:.2f}", (x1, max(15, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        cv2.line(annotated_frame, (center[0] - 20, center[1]),
                 (center[0] + 20, center[1]), (0, 255, 0), 2)
        cv2.line(annotated_frame, (center[0], center[1] - 20),
                 (center[0], center[1] + 20), (0, 255, 0), 2)
        if previous_person is None:
            person = None  # A person must be explicitly selected in the camera view.
        else:
            # Follow the clicked box through successive frames without storing appearance.
            px1, py1, px2, py2 = previous_person['bbox']
            def match_score(det):
                x1, y1, x2, y2 = det['bbox']
                intersection = max(0, min(px2, x2) - max(px1, x1)) * max(
                    0, min(py2, y2) - max(py1, y1))
                area = (x2 - x1) * (y2 - y1)
                previous_area = max(1, (px2 - px1) * (py2 - py1))
                overlap = intersection / max(1, area + previous_area - intersection)
                distance = math.hypot(
                    (det['center'][0] - previous_person['center'][0]) / width,
                    (det['center'][1] - previous_person['center'][1]) / height)
                size_ratio = area / previous_area
                if not 0.4 <= size_ratio <= 2.5 or (overlap < 0.1 and distance > 0.2):
                    return float('inf')
                return distance + (1 - overlap)
            candidates = sorted(
                ((match_score(det), index, det) for index, det in enumerate(detections)),
                key=lambda candidate: candidate[0],
            )
            candidates = [candidate for candidate in candidates if math.isfinite(candidate[0])]
            person = candidates[0][2] if candidates else None
            if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < self.target_match_margin:
                person = None  # Ambiguous matches must stop, not switch the target.
        if person:
            cv2.line(annotated_frame, center, person['center'], (255, 0, 0), 2)
            x1, y1, x2, y2 = person['bbox']
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 255), 3)
            cv2.putText(annotated_frame, "SELECTED", person['center'],
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        return person, annotated_frame, detections

    def select_person_at_click(self, event):
        """Map a click in the displayed image back to a detected person's box."""
        if (not self.tracking_active or self.camera_image_geometry is None
                or self.displayed_revision != self.tracking_revision
                or self.displayed_frame_at is None
                or time.monotonic() - self.displayed_frame_at > self.frame_timeout):
            return
        # Canvas item coordinates remain valid even after a canvas resize.
        item = self.camera_canvas.find_withtag("camera_image")
        bounds = self.camera_canvas.bbox(item[0]) if item else None
        if bounds is None:
            return
        left, top, right, bottom = bounds
        if not (left <= event.x < right and top <= event.y < bottom):
            return
        frame_height, frame_width = self.camera_image_geometry
        x = (event.x - left) * frame_width / (right - left)
        y = (event.y - top) * frame_height / (bottom - top)
        candidates = [person for person in self.displayed_people
                      if person['bbox'][0] <= x <= person['bbox'][2]
                      and person['bbox'][1] <= y <= person['bbox'][3]]
        if not candidates:
            return
        person = min(candidates, key=lambda det:
                     (det['bbox'][2] - det['bbox'][0]) * (det['bbox'][3] - det['bbox'][1]))
        self.stop_movement()
        self.selected_target = (dict(person), (frame_height, frame_width))
        self.tracking_revision += 1  # Reject in-flight results for the previous selection.
        self.last_detection_at = None
        self.proximity_stopped = False
        self.update_person(None)
        self.person_info.config(text="Person selected - waiting for fresh image", fg="#f1c40f")

    def update_person(self, person):
        """Apply detection state on the GUI thread only."""
        self.last_person = person
        self.person_detected = person is not None
        self.last_person_position = person['center'] if person else None
        if person:
            self.person_info.config(text=f"Selected person ({person['confidence']:.2f})", fg="#2ecc71")
        else:
            self.person_info.config(text="Click a person in the camera view" if self.tracking_active
                                    else "No person detected", fg="#e74c3c")
        
    def follow_person(self, person, once=False):
        """Proportional steering and approach speed with immediate loss/proximity stop."""
        if not self.auto_mode and not once:
            return
        self.cancel_motor_test()
        if not person:
            self.stop_movement()
            return

        # Box height is only an apparent-size estimate, not a measured distance.
        x1, y1, x2, y2 = person['bbox']
        height_ratio = (y2 - y1) / max(1, self.frame_height)
        if height_ratio >= self.stop_person_height_ratio:
            self.proximity_stopped = True
        elif height_ratio < self.resume_person_height_ratio:
            self.proximity_stopped = False
        if self.proximity_stopped:
            self.stop_movement()
            return

        error = max(-1.0, min(1.0, (person['center'][0] - self.center_x) /
                              max(1, self.center_x)))
        deadband = min(0.95, self.tracking_threshold / 320)
        steering = math.copysign(self.follow_turn_gain * max(
            0, (abs(error) - deadband) / (1 - deadband)), error)
        approach = max(0, min(1, (self.stop_person_height_ratio - height_ratio) /
                                 (self.stop_person_height_ratio - self.slow_person_height_ratio)))
        forward = self.follow_speed * approach * (1 - 0.7 * abs(error))
        targets = [max(0, min(self.follow_max_pwm, round(forward + steering))),
                   max(0, min(self.follow_max_pwm, round(forward - steering)))]
        now = time.monotonic()
        dt = 0.1 if self.follow_update_at is None else max(0, min(0.2, now - self.follow_update_at))
        self.follow_update_at = now
        step = self.follow_acceleration * dt
        # Preserve the left/right difference while limiting acceleration of the pair.
        largest_change = max(abs(targets[0] - self.vl), abs(targets[1] - self.vr))
        fraction = min(1, step / largest_change) if largest_change else 1
        self.vl = round(self.vl + (targets[0] - self.vl) * fraction)
        self.vr = round(self.vr + (targets[1] - self.vr) * fraction)
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def follow_nearest_person(self):
        """A single follow command is a bounded pulse, never indefinite motion."""
        if (self.last_person and self.person_detected and self.tracking_active
                and self.last_detection_at is not None
                and time.monotonic() - self.last_detection_at <= self.frame_timeout):
            self.full_stop()
            self.follow_once_until = time.monotonic() + 0.5
            self.follow_person(self.last_person, once=True)
    
    def start_camera(self):
        """Start background capture so HTTP and YOLO cannot freeze Tk."""
        if self.camera_active:
            return True
        cameraip = self.entry_ip.get().strip()
        if not cameraip:
            messagebox.showerror("Camera Error", "Please enter the camera IP address.")
            return False
        self.cameraip = cameraip
        self.last_detection_at = None
        self.camera_active = True
        self.camera_stop = threading.Event()
        self.camera_frames = queue.Queue(maxsize=1)
        self.camera_info.config(text="Connecting to camera...", fg="#f39c12")
        self.camera_info.place(relx=0.5, rely=0.5, anchor="center")
        self.start_cam_btn.config(state="disabled")
        self.stop_cam_btn.config(state="normal")
        self.entry_ip.config(state="disabled")
        threading.Thread(target=self.capture_frames, args=(
            cameraip, self.camera_stop, self.camera_frames,
        ), daemon=True).start()
        self.run_camera()
        return True

    def capture_frames(self, cameraip, stop_event, frames):
        """Capture/inference worker; all widget updates stay in run_camera."""
        previous_person = None
        previous_selection = None
        previous_shape = None
        while not stop_event.is_set():
            captured_at = time.monotonic()
            revision = self.tracking_revision
            selection = self.selected_target
            tracking = self.tracking_active
            try:
                with urllib.request.urlopen(f'http://{cameraip}/capture', timeout=2) as response:
                    data = np.frombuffer(response.read(), dtype=np.uint8)
                frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
                if frame is None:
                    raise ValueError("Camera returned an invalid image")
                person = None
                detections = []
                if selection is not previous_selection:
                    previous_person = dict(selection[0]) if selection is not None else None
                    previous_shape = selection[1] if selection is not None else None
                if previous_person is not None and frame.shape[:2] != previous_shape[:2]:
                    # Preserve the clicked box when the camera resolution changes.
                    old_height, old_width = previous_shape[:2]
                    scale_x, scale_y = frame.shape[1] / old_width, frame.shape[0] / old_height
                    x1, y1, x2, y2 = previous_person['bbox']
                    cx, cy = previous_person['center']
                    previous_person = dict(previous_person,
                        bbox=(round(x1 * scale_x), round(y1 * scale_y),
                              round(x2 * scale_x), round(y2 * scale_y)),
                        center=(round(cx * scale_x), round(cy * scale_y)))
                previous_selection, previous_shape = selection, frame.shape
                if tracking:
                    with self.inference_lock:
                        if stop_event.is_set():
                            return
                        person, frame, detections = self.detect_person(frame, previous_person)
                    if time.monotonic() - captured_at <= self.frame_timeout:
                        previous_person = person
                result = (revision, person, frame, None, captured_at, detections)
            except Exception as e:
                result = (None, None, None, str(e), captured_at, [])
            if stop_event.is_set():
                return
            # Keep only the latest frame; never build a backlog of old commands.
            try:
                frames.get_nowait()
            except queue.Empty:
                pass
            frames.put_nowait(result)
            if result[3] is not None:
                return
            stop_event.wait(0.05)
    
    def run_camera(self):
        """Poll worker results on Tk's main thread."""
        self.camera_after_id = None
        if not self.camera_active or self.closing.is_set():
            return
        self.check_follow_timeout()
        try:
            revision, person, frame, error, captured_at, detections = self.camera_frames.get_nowait()
        except queue.Empty:
            self.camera_after_id = self.root.after(50, self.run_camera)
            return
        if error is not None:
            self.stop_camera()
            self.camera_info.config(text=f"Camera Error: {error[:60]}", fg="#e74c3c")
            messagebox.showerror("Camera Error", f"Camera or detection failed: {error}")
            return
        # Discard results from before a tracking toggle.
        if revision == self.tracking_revision and time.monotonic() - captured_at <= self.frame_timeout:
            self.last_detection_at = captured_at
            self.frame_height, frame_width = frame.shape[:2]
            self.center_x, self.center_y = frame_width // 2, self.frame_height // 2
            if person is None and self.selected_target is not None:
                self.selected_target = None  # Lost targets require another explicit click.
            self.update_person(person if self.tracking_active else None)
            if self.auto_mode or self.follow_once_until is not None:
                self.follow_person(person, once=self.follow_once_until is not None)

            canvas_width = max(1, self.camera_canvas.winfo_width())
            canvas_height = max(1, self.camera_canvas.winfo_height())
            scale = min(canvas_width / frame_width, canvas_height / self.frame_height)
            frame = cv2.resize(frame, (max(1, int(frame_width * scale)),
                                       max(1, int(self.frame_height * scale))))
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.current_frame = ImageTk.PhotoImage(image=img)
            self.displayed_people = detections if self.tracking_active else []
            self.displayed_frame_at = captured_at
            self.displayed_revision = revision
            self.camera_image_geometry = (self.frame_height, frame_width)
            self.camera_canvas.delete("all")
            self.camera_canvas.create_image(canvas_width // 2, canvas_height // 2,
                                            anchor="center", image=self.current_frame,
                                            tags="camera_image")
            self.camera_info.place_forget()
        elif revision == self.tracking_revision:
            if self.auto_mode or self.follow_once_until is not None:
                self.stop_movement()
            self.update_person(None)
            self.camera_info.config(text="Camera frame too old - waiting for fresh image", fg="#f39c12")
            self.camera_info.place(relx=0.5, rely=0.5, anchor="center")
        self.camera_after_id = self.root.after(50, self.run_camera)
    
    def stop_camera(self):
        """Stop camera feed"""
        self.camera_active = False
        self.camera_stop.set()
        if self.camera_after_id is not None:
            self.root.after_cancel(self.camera_after_id)
            self.camera_after_id = None
        self.tracking_active = False
        self.last_detection_at = None
        self.tracking_revision += 1
        self.selected_target = None
        self.displayed_people = []
        self.camera_image_geometry = None
        self.full_stop()
        self.update_person(None)
        self.camera_info.config(text="Camera inactive", fg="#f39c12")
        self.camera_info.place(relx=0.5, rely=0.5, anchor="center")
        self.start_cam_btn.config(state="normal")
        self.stop_cam_btn.config(state="disabled")
        self.entry_ip.config(state="normal")
        
        # Clear canvas
        self.camera_canvas.delete("all")
        self.current_frame = None
    
    def toggle_tracking(self):
        """Toggle person detection tracking"""
        if not self.tracking_active and self.model is None:
            messagebox.showerror("Tracking Error", "YOLO model not loaded!")
            return
        
        if self.tracking_active:
            self.tracking_active = False
            self.update_person(None)
            self.full_stop()
        else:
            if not self.start_camera():
                return
            self.tracking_active = True
        self.tracking_revision += 1
        self.selected_target = None
        self.displayed_people = []
        self.update_person(None)
        self.last_detection_at = None
        self.proximity_stopped = False
        self.refresh_tracking_controls()
    
    def toggle_auto_follow(self):
        """Toggle automatic following mode"""
        if self.auto_mode:
            self.full_stop()
            return
        if not self.tracking_active:
            self.toggle_tracking()
        if self.tracking_active and self.camera_active:
            self.cancel_motor_test()
            self.stop_movement()
            self.tracking_revision += 1
            self.last_detection_at = None
            self.proximity_stopped = False
            self.auto_mode = True
            self.refresh_tracking_controls()

    def refresh_tracking_controls(self):
        if self.tracking_active:
            self.track_btn.config(text="🛑 Stop Tracking", bg="#e74c3c")
            self.track_circle.itemconfig(1, fill="#27ae60")
        else:
            self.track_btn.config(text="🚀 Start Tracking", bg="#2980b9")
            self.track_circle.itemconfig(1, fill="gray")
        self.tracking_status.set("Auto-Follow: ON" if self.auto_mode else
                                 "Tracking: ON" if self.tracking_active else "Tracking: OFF")
        self.auto_follow_btn.config(bg="#8e44ad" if self.auto_mode else "#9b59b6")

    def manual_override(self):
        self.cancel_motor_test()
        if self.auto_mode or self.follow_once_until is not None:
            self.full_stop()

    def cancel_motor_test(self):
        if self.motor_test_after_id is not None:
            self.root.after_cancel(self.motor_test_after_id)
            self.motor_test_after_id = None

    def test_motor(self, left, right):
        """Use absolute test values; the other side always starts at zero."""
        self.full_stop()
        self.vl, self.vr = left, right
        self.update_display()
        self.send_command(left, right)
        self.motor_test_after_id = self.root.after(1000, self.finish_motor_test)

    def finish_motor_test(self):
        self.motor_test_after_id = None
        self.stop_movement()
    
    # Manual control methods
    def left_forward(self):
        self.manual_override()
        self.vl = min(255, self.vl + self.acceleration)
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def left_reverse(self):
        self.manual_override()
        self.vl = max(-255, self.vl - self.acceleration)
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def left_stop(self):
        self.manual_override()
        self.vl = 0
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def right_forward(self):
        self.manual_override()
        self.vr = min(255, self.vr + self.acceleration)
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def right_reverse(self):
        self.manual_override()
        self.vr = max(-255, self.vr - self.acceleration)
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def right_stop(self):
        self.manual_override()
        self.vr = 0
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def both_forward(self):
        self.manual_override()
        # Straight driving must clear any difference left by individual controls.
        speed = min(255, max(self.vl, self.vr) + self.acceleration)
        self.vl = self.vr = speed
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def both_reverse(self):
        self.manual_override()
        speed = max(-255, min(self.vl, self.vr) - self.acceleration)
        self.vl = self.vr = speed
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def full_stop(self):
        """Stop also cancels auto-follow, so the next frame cannot restart motion."""
        self.auto_mode = False
        self.refresh_tracking_controls()
        self.stop_movement()

    def stop_movement(self):
        """Stop tracks while retaining auto mode when a target is lost."""
        self.cancel_motor_test()
        self.follow_once_until = None
        self.follow_update_at = None
        self.vl = 0
        self.vr = 0
        self.update_display()
        self.send_command(0, 0)
    
    def rotate_left(self):
        self.manual_override()
        self.vl = -150
        self.vr = 150
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    def rotate_right(self):
        self.manual_override()
        self.vl = 150
        self.vr = -150
        self.update_display()
        self.send_command(self.vl, self.vr)
    
    # Socket functions
    def start_socket(self):
        self.socket_thread = threading.Thread(target=self.connect_socket, daemon=True)
        self.socket_thread.start()
    
    def connect_socket(self):
        """Accept connections in a worker; hand sockets to the GUI thread."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                self.socket = server
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen()
                server.settimeout(0.5)
                print(f"Listening on {self.host}:{self.port}")
                while not self.closing.is_set():
                    try:
                        conn, addr = server.accept()
                    except socket.timeout:
                        continue
                    conn.settimeout(0.2)
                    if self.closing.is_set():
                        conn.close()
                        return
                    self.connection_events.put((conn, addr, None))
        except OSError as e:
            if not self.closing.is_set():
                self.connection_events.put((None, None, str(e)))

    def poll_connection(self):
        if self.closing.is_set():
            return
        while True:
            try:
                conn, addr, error = self.connection_events.get_nowait()
            except queue.Empty:
                break
            if error:
                self.status.set(f"Connection error: {error}")
                print(f"Connection error: {error}")
                continue
            self.full_stop()
            if self.conn:
                self.conn.close()
            self.conn, self.addr = conn, addr
            print(f"Connected by {addr}")
            self.stop_movement()
        self.check_follow_timeout()
        if self.conn and time.monotonic() - self.last_command_at >= self.command_refresh_interval:
            self.send_command(self.vl, self.vr)
        self.connection_after_id = self.root.after(100, self.poll_connection)

    def check_follow_timeout(self):
        """Never refresh a moving follow command after capture/inference has stalled."""
        if not self.auto_mode and self.follow_once_until is None:
            return
        now = time.monotonic()
        stale = self.last_detection_at is None or now - self.last_detection_at > self.frame_timeout
        pulse_ended = self.follow_once_until is not None and now >= self.follow_once_until
        if stale or pulse_ended:
            if self.vl or self.vr or self.follow_once_until is not None:
                self.stop_movement()
            if stale:
                self.update_person(None)
    
    def on_closing(self):
        """Cleanup on window close"""
        if self.closing.is_set():
            return
        self.closing.set()
        self.stop_camera()
        self.root.after_cancel(self.connection_after_id)
        if self.conn:
            self.conn.close()
            self.conn = None
        # The accept worker closes its listener after the short socket timeout.
        if self.socket_thread is not None:
            self.socket_thread.join(timeout=0.6)
        while True:
            try:
                conn, _, _ = self.connection_events.get_nowait()
            except queue.Empty:
                break
            if conn:
                conn.close()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = TankTrackerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
