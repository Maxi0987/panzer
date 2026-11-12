"""Run with: python -m unittest discover -s tests -v (no robot required)."""

import importlib.util
import io
from pathlib import Path
import queue
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
import tkinter as tk
from ultralytics.engine.results import Results


MODULE_PATH = Path(__file__).resolve().parents[1] / "PanzerPersonenGUI.py"
SPEC = importlib.util.spec_from_file_location("person_tracker", MODULE_PATH)
tracker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracker)


class PersonTrackerTests(unittest.TestCase):
    def setUp(self):
        # Real Tk widgets verify the GUI state without showing a window or
        # connecting to the user's robot/camera.
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(tracker.TankTrackerGUI, "load_yolo_model"), \
                patch.object(tracker.TankTrackerGUI, "start_socket"):
            self.app = tracker.TankTrackerGUI(self.root)
        self.app.model = Mock(return_value=[])
        self.app.conn = Mock()
        self.now = 100.0
        clock = patch.object(tracker.time, "monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.app.last_detection_at = self.now
        self.frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.person = {
            "bbox": (280, 100, 360, 300), "center": (320, 200), "confidence": 0.9,
        }

    def tearDown(self):
        self.app.on_closing()

    def result(self, boxes):
        return Results(self.frame, path="test.jpg", names={0: "person", 39: "bottle"},
                       boxes=np.array(boxes, dtype=np.float32).reshape(-1, 6))

    def test_only_person_is_selected_and_drawn_in_mixed_results(self):
        self.app.model.return_value = [self.result([
            [280, 100, 360, 300, 0.9, 0],
            [300, 220, 340, 260, 0.99, 39],  # Bottle is closer to the center.
            [10, 10, 40, 60, 0.3, 0],  # Low-confidence person is ignored.
        ])]
        with patch.object(tracker.cv2, "rectangle", wraps=cv2.rectangle) as draw:
            person, image, detections = self.app.detect_person(self.frame)
        self.assertIsNone(person)
        self.assertEqual([det["bbox"] for det in detections], [self.person["bbox"]])
        self.assertEqual(draw.call_count, 1)
        self.assertEqual(self.app.model.call_args.kwargs["classes"], [0])
        self.assertEqual(self.app.model.call_args.kwargs["conf"], 0.5)
        self.assertFalse(np.array_equal(image, self.frame))
        self.assertFalse(self.frame.any(), "Detection must not modify its input")

    def test_bottle_only_is_never_a_target_or_box(self):
        self.app.model.return_value = [self.result([[280, 100, 360, 300, 0.99, 39]])]
        with patch.object(tracker.cv2, "rectangle") as draw:
            person, _, _ = self.app.detect_person(self.frame)
        self.assertIsNone(person)
        draw.assert_not_called()

    def test_people_are_not_selected_automatically(self):
        self.app.model.return_value = [self.result([
            [10, 100, 90, 300, 0.95, 0], [280, 100, 360, 300, 0.8, 0],
        ])]
        person, _, detections = self.app.detect_person(self.frame)
        self.assertIsNone(person)
        self.assertEqual(len(detections), 2)
        self.assertTrue(all(set(det) == {'bbox', 'center', 'confidence'} for det in detections))

    def show_clickable_people(self, people):
        self.app.camera_active = self.app.tracking_active = True
        self.app.displayed_people = people
        self.app.displayed_frame_at = self.now
        self.app.displayed_revision = self.app.tracking_revision
        self.app.camera_image_geometry = self.frame.shape[:2]
        self.app.current_frame = tracker.ImageTk.PhotoImage(tracker.Image.new('RGB', (320, 240)))
        self.app.camera_canvas.create_image(200, 200, image=self.app.current_frame,
                                            anchor='center', tags='camera_image')

    def test_click_selects_person_using_scaled_image_and_letterbox_offsets(self):
        left_person = dict(self.person, bbox=(80, 100, 160, 300), center=(120, 200))
        self.show_clickable_people([self.person, left_person])
        self.assertTrue(self.app.camera_canvas.bind('<Button-1>'))
        self.app.auto_mode = True
        self.app.vl = self.app.vr = 150
        old_revision = self.app.tracking_revision
        self.app.select_person_at_click(SimpleNamespace(x=100, y=180))
        self.assertEqual(self.app.selected_target, (left_person, (480, 640)))
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.app.conn.sendall.assert_called_once_with(b'0 0\n')
        self.assertTrue(self.app.auto_mode)
        self.assertGreater(self.app.tracking_revision, old_revision)
        self.assertIsNone(self.app.last_detection_at)
        self.assertIsNone(self.app.last_person)

    def test_empty_space_letterbox_stale_and_disabled_clicks_are_ignored(self):
        self.show_clickable_people([self.person])
        for click in (SimpleNamespace(x=10, y=180), SimpleNamespace(x=100, y=180)):
            self.app.select_person_at_click(click)
            self.assertIsNone(self.app.selected_target)
        self.now += 2
        self.app.select_person_at_click(SimpleNamespace(x=200, y=180))
        self.assertIsNone(self.app.selected_target)
        self.app.displayed_frame_at = self.now
        self.app.tracking_active = False
        self.app.select_person_at_click(SimpleNamespace(x=200, y=180))
        self.assertIsNone(self.app.selected_target)
        self.app.conn.sendall.assert_not_called()

    def test_click_changes_target_without_restarting_tracking(self):
        other = dict(self.person, bbox=(80, 100, 160, 300), center=(120, 200))
        self.app.selected_target = (self.person, self.frame.shape[:2])
        self.show_clickable_people([self.person, other])
        self.app.select_person_at_click(SimpleNamespace(x=100, y=180))
        self.assertEqual(self.app.selected_target[0], other)

    def test_click_selects_smaller_person_when_boxes_overlap(self):
        outer = dict(self.person, bbox=(240, 50, 400, 400), center=(320, 225))
        self.show_clickable_people([outer, self.person])
        self.app.select_person_at_click(SimpleNamespace(x=200, y=180))
        self.assertEqual(self.app.selected_target[0], self.person)

    def test_click_rejects_in_flight_result_for_previous_person(self):
        other = dict(self.person, bbox=(80, 100, 160, 300), center=(120, 200))
        self.app.auto_mode = True
        self.show_clickable_people([self.person, other])
        old_revision = self.app.tracking_revision
        self.app.select_person_at_click(SimpleNamespace(x=100, y=180))
        self.app.conn.sendall.reset_mock()
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put((old_revision, self.person, self.frame,
                                    None, self.now, [self.person, other]))
        self.app.run_camera()
        self.assertEqual(self.app.selected_target[0], other)
        self.assertIsNone(self.app.last_person)
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.app.conn.sendall.assert_not_called()

    def test_clicked_person_is_followed_after_fresh_capture(self):
        other = dict(self.person, bbox=(80, 100, 160, 300), center=(120, 200))
        self.show_clickable_people([self.person, other])
        self.app.auto_mode = True
        self.app.select_person_at_click(SimpleNamespace(x=100, y=180))
        self.app.conn.sendall.reset_mock()
        self.app.model.return_value = [self.result([
            [80, 100, 160, 300, 0.9, 0], [280, 100, 360, 300, 0.95, 0],
        ])]
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put(self.capture_one_frame(True))
        self.app.run_camera()
        self.assertEqual(self.app.last_person['center'], other['center'])
        self.assertGreater(self.app.vr, 0)
        self.assertLess(self.app.vl, self.app.vr)

    def test_lost_clicked_person_stops_and_requires_new_click(self):
        self.app.camera_active = self.app.tracking_active = self.app.auto_mode = True
        self.app.selected_target = (self.person, self.frame.shape[:2])
        self.app.vl = self.app.vr = 150
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put((0, None, self.frame, None, self.now, []))
        self.app.run_camera()
        self.assertIsNone(self.app.selected_target)
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.app.model.return_value = [self.result([[280, 100, 360, 300, 0.9, 0]])]
        _, person, _, error, _, detections = self.capture_one_frame(True)
        self.assertIsNone(error)
        self.assertIsNone(person)
        self.assertEqual(len(detections), 1)

    def test_empty_results_clear_target_state(self):
        self.app.update_person(self.person)
        person, _, _ = self.app.detect_person(self.frame)
        self.app.update_person(person)
        self.assertFalse(self.app.person_detected)
        self.assertIsNone(self.app.last_person_position)
        self.assertIsNone(self.app.last_person)

    def test_selected_person_is_kept_when_another_person_is_more_centered(self):
        previous = dict(self.person, bbox=(80, 100, 160, 300), center=(120, 200))
        self.app.model.return_value = [self.result([
            [90, 100, 170, 300, 0.8, 0], [280, 100, 360, 300, 0.95, 0],
        ])]
        person, _, _ = self.app.detect_person(self.frame, previous)
        self.assertEqual(person['center'], (130, 200))

    def test_unrelated_person_does_not_replace_a_missing_locked_target(self):
        previous = dict(self.person, bbox=(10, 100, 90, 300), center=(50, 200))
        self.app.model.return_value = [self.result([[500, 100, 580, 300, 0.95, 0]])]
        person, _, _ = self.app.detect_person(self.frame, previous)
        self.assertIsNone(person)

    def test_ambiguous_people_stop_instead_of_selecting_one(self):
        self.app.model.return_value = [self.result([
            [270, 100, 350, 300, 0.9, 0], [290, 100, 370, 300, 0.9, 0],
        ])]
        person, _, _ = self.app.detect_person(self.frame, self.person)
        self.assertIsNone(person)

    def test_invalid_detection_box_is_ignored(self):
        self.app.model.return_value = [self.result([[350, 100, 280, 300, 0.9, 0]])]
        person, _, _ = self.app.detect_person(self.frame)
        self.assertIsNone(person)

    def test_full_stop_cannot_be_overridden_by_next_detection(self):
        self.app.auto_mode = True
        self.app.vl = self.app.vr = 100
        self.app.full_stop()
        self.app.follow_person(self.person)
        self.assertFalse(self.app.auto_mode)
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.app.conn.sendall.assert_called_once_with(b"0 0\n")

    def test_lost_person_stops_motion_but_allows_reacquisition(self):
        self.app.auto_mode = True
        self.app.vl = self.app.vr = 100
        self.app.follow_person(None)
        self.app.conn.sendall.assert_called_with(b"0 0\n")
        self.assertTrue(self.app.auto_mode)
        self.app.follow_person(self.person)
        self.app.conn.sendall.assert_called_with(b"75 75\n")

    def test_large_person_box_stops_approach(self):
        self.app.auto_mode = True
        person = dict(self.person, bbox=(200, 0, 440, 450))
        self.app.follow_person(person)
        self.app.conn.sendall.assert_called_with(b"0 0\n")

    def settle_follow(self, person):
        for _ in range(10):
            self.now += 0.1
            self.app.follow_person(person)
        return self.app.vl, self.app.vr

    def test_nearer_person_reduces_speed_before_stop(self):
        self.app.auto_mode = True
        far = dict(self.person, bbox=(280, 100, 360, 244))
        near = dict(self.person, bbox=(280, 50, 360, 410))
        far_speed = self.settle_follow(far)[0]
        near_speed = self.settle_follow(near)[0]
        self.assertEqual(far_speed, 220)
        self.assertGreater(far_speed, near_speed)
        self.assertGreater(near_speed, 0)

    def test_person_at_previous_stop_distance_is_followed(self):
        self.app.auto_mode = True
        person = dict(self.person, bbox=(280, 0, 360, 350))
        self.app.follow_person(person)
        self.assertEqual((self.app.vl, self.app.vr), (75, 75))
        left, right = self.settle_follow(person)
        self.assertGreater(left, 100)
        self.assertEqual(left, right)
        self.assertFalse(self.app.proximity_stopped)

    def test_proximity_stop_uses_hysteresis(self):
        self.app.auto_mode = True
        self.settle_follow(self.person)
        self.app.follow_person(dict(self.person, bbox=(280, 0, 360, 440)))
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        # A slightly smaller box must not immediately restart the motors.
        self.app.follow_person(dict(self.person, bbox=(280, 0, 360, 400)))
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.app.follow_person(dict(self.person, bbox=(280, 0, 360, 360)))
        self.assertGreater(self.app.vl, 0)

    def test_small_offsets_are_inside_steering_deadband(self):
        self.app.auto_mode = True
        left, right = self.settle_follow(dict(self.person, center=(340, 200)))
        self.assertEqual(left, right)

    def test_larger_offset_increases_steering_continuously(self):
        self.app.auto_mode = True
        mild = self.settle_follow(dict(self.person, center=(400, 200)))
        strong = self.settle_follow(dict(self.person, center=(500, 200)))
        self.assertGreater(mild[0], mild[1])
        self.assertGreater(strong[0] - strong[1], mild[0] - mild[1])

    def test_steering_is_independent_of_image_resolution(self):
        self.app.auto_mode = True
        normal = self.settle_follow(dict(self.person, center=(440, 200)))
        self.app.frame_height *= 2
        self.app.center_x *= 2
        double = dict(self.person, bbox=(560, 200, 720, 600), center=(880, 400))
        self.assertEqual(normal, self.settle_follow(double))

    def test_acceleration_is_limited_but_target_loss_stops_immediately(self):
        self.app.auto_mode = True
        self.app.follow_person(self.person)
        self.assertEqual(self.app.vl, 75)
        self.assertEqual(self.app.vl, self.app.vr)
        before = self.app.vl
        self.now += 0.05
        self.app.follow_person(self.person)
        self.assertTrue(0 < self.app.vl - before <= 38)
        self.app.follow_person(None)
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))

    def test_tracking_off_stops_auto_and_clears_target(self):
        self.app.tracking_active = self.app.auto_mode = True
        self.app.camera_active = True
        self.app.update_person(self.person)
        self.app.toggle_tracking()
        self.assertFalse(self.app.tracking_active)
        self.assertFalse(self.app.auto_mode)
        self.assertFalse(self.app.person_detected)
        self.app.conn.sendall.assert_called_with(b"0 0\n")
        self.assertTrue(self.app.camera_active)

    def test_camera_stop_clears_tracking_and_motion(self):
        self.app.tracking_active = self.app.auto_mode = self.app.camera_active = True
        self.app.update_person(self.person)
        self.app.stop_camera()
        self.assertFalse(self.app.tracking_active)
        self.assertFalse(self.app.auto_mode)
        self.assertTrue(self.app.camera_stop.is_set())
        self.assertFalse(self.app.person_detected)
        self.app.conn.sendall.assert_called_with(b"0 0\n")

    def test_auto_follow_is_not_enabled_without_model(self):
        self.app.model = None
        with patch.object(tracker.messagebox, "showerror") as error:
            self.app.toggle_auto_follow()
        error.assert_called_once()
        self.assertFalse(self.app.auto_mode)
        self.assertFalse(self.app.tracking_active)
        self.assertFalse(self.app.camera_active)

    def test_manual_controls_override_auto_and_use_left_right_protocol(self):
        self.app.auto_mode = True
        self.app.left_forward()
        self.assertFalse(self.app.auto_mode)
        self.app.conn.sendall.assert_called_with(b"20 0\n")
        self.app.right_reverse()
        self.app.conn.sendall.assert_called_with(b"20 -20\n")

    def test_adding_left_track_keeps_right_track_velocity(self):
        self.app.right_forward()
        self.app.right_forward()
        self.app.conn.sendall.assert_called_with(b"0 40\n")
        for expected_left in range(20, 260, 20):
            self.app.left_forward()
            self.assertEqual(self.app.vr, 40)
            self.app.conn.sendall.assert_called_with(f"{expected_left} 40\n".encode())
        self.app.left_stop()
        self.app.conn.sendall.assert_called_with(b"0 40\n")

    def test_adding_right_track_keeps_left_track_velocity(self):
        self.app.left_forward()
        for expected_right in range(20, 260, 20):
            self.app.right_forward()
            self.assertEqual(self.app.vl, 20)
            self.app.conn.sendall.assert_called_with(f"20 {expected_right}\n".encode())
        self.app.right_stop()
        self.app.conn.sendall.assert_called_with(b"20 0\n")

    def test_typing_in_ip_entry_does_not_move_robot(self):
        action = Mock()
        self.app.handle_shortcut(SimpleNamespace(widget=self.app.entry_ip), action)
        action.assert_not_called()
        self.app.handle_shortcut(SimpleNamespace(widget=self.app.camera_canvas), action)
        action.assert_called_once()

    def test_both_controls_drive_equally_after_individual_controls(self):
        for action, initial, expected in [
            (self.app.both_forward, (0, 120), b"140 140\n"),
            (self.app.both_forward, (-120, -80), b"-60 -60\n"),
            (self.app.both_forward, (250, 20), b"255 255\n"),
            (self.app.both_reverse, (-120, 0), b"-140 -140\n"),
            (self.app.both_reverse, (120, 80), b"60 60\n"),
            (self.app.both_reverse, (-250, -20), b"-255 -255\n"),
        ]:
            with self.subTest(initial=initial, expected=expected):
                self.app.vl, self.app.vr = initial
                action()
                self.app.conn.sendall.assert_called_with(expected)

    def test_motor_test_isolates_each_side_and_stops_automatically(self):
        for left, right in [(120, 0), (-120, 0), (0, 120), (0, -120)]:
            with self.subTest(left=left, right=right):
                self.app.auto_mode = True
                self.app.vl, self.app.vr = 80, 160
                # Capture the scheduled callback, then execute it without waiting.
                with patch.object(self.root, "after", return_value="test-timer") as after:
                    self.app.test_motor(left, right)
                self.assertFalse(self.app.auto_mode)
                self.assertEqual((self.app.vl, self.app.vr), (left, right))
                self.app.conn.sendall.assert_called_with(f"{left} {right}\n".encode())
                delay, callback = after.call_args.args
                self.assertEqual(delay, 1000)
                callback()
                self.assertEqual((self.app.vl, self.app.vr), (0, 0))
                self.app.conn.sendall.assert_called_with(b"0 0\n")
                self.assertIsNone(self.app.motor_test_after_id)

    def test_new_control_cancels_old_motor_test_timer(self):
        for action in [self.app.full_stop, self.app.left_forward,
                       self.app.both_reverse, self.app.toggle_auto_follow]:
            with self.subTest(action=action.__name__):
                self.app.tracking_active = self.app.camera_active = True
                self.app.test_motor(120, 0)
                timer = self.app.motor_test_after_id
                action()
                self.assertIsNone(self.app.motor_test_after_id)
                self.assertNotIn(timer, self.root.tk.call("after", "info"))

    def test_follow_uses_independent_left_right_velocities(self):
        self.app.auto_mode = True
        for x in [100, 320, 540]:
            with self.subTest(x=x):
                self.app.vl = self.app.vr = 0
                self.app.follow_update_at = None
                for _ in range(10):
                    self.now += 0.1
                    self.app.follow_person(dict(self.person, center=(x, 200)))
                left, right = self.app.vl, self.app.vr
                self.assertTrue(0 <= left <= self.app.follow_max_pwm)
                self.assertTrue(0 <= right <= self.app.follow_max_pwm)
                if x < 320:
                    self.assertLess(left, right)
                elif x > 320:
                    self.assertGreater(left, right)
                else:
                    self.assertEqual(left, right)
                    self.assertGreater(left, 0)
                self.app.conn.sendall.assert_called_with(f"{left} {right}\n".encode())

    def capture_one_frame(self, tracking_active):
        self.app.tracking_active = tracking_active
        stop = threading.Event()
        frames = queue.Queue(maxsize=1)
        _, encoded = cv2.imencode(".jpg", self.frame)
        with patch.object(tracker.urllib.request, "urlopen",
                          return_value=io.BytesIO(encoded.tobytes())), \
                patch.object(stop, "wait", side_effect=lambda _: stop.set()):
            self.app.capture_frames("test-camera", stop, frames)
        return frames.get_nowait()

    def test_camera_preview_skips_yolo_when_tracking_off(self):
        _, person, frame, error, _, detections = self.capture_one_frame(False)
        self.app.model.assert_not_called()
        self.assertIsNone(person)
        self.assertIsNone(error)
        self.assertEqual(frame.shape, self.frame.shape)

    def test_camera_runs_yolo_when_tracking_on(self):
        _, _, frame, error, _, detections = self.capture_one_frame(True)
        self.app.model.assert_called_once()
        self.assertIsNone(error)
        self.assertEqual(frame.shape, self.frame.shape)

    def test_capture_drops_target_after_loss(self):
        self.app.tracking_active = True
        self.app.selected_target = (self.person, self.frame.shape[:2])
        stop = threading.Event()
        frames = queue.Queue(maxsize=1)
        _, encoded = cv2.imencode('.jpg', self.frame)
        iteration = 0
        def next_iteration(_):
            nonlocal iteration
            iteration += 1
            self.now += 0.1 if iteration == 1 else 60
            if iteration == 3:
                stop.set()
        with patch.object(tracker.urllib.request, 'urlopen',
                          side_effect=lambda *a, **k: io.BytesIO(encoded.tobytes())), \
                patch.object(self.app, 'detect_person', side_effect=[
                    (self.person, self.frame, [self.person]), (None, self.frame, []), (None, self.frame, []),
                ]) as detect, patch.object(stop, 'wait', side_effect=next_iteration):
            self.app.capture_frames('test-camera', stop, frames)
        previous = [call.args[1] for call in detect.call_args_list]
        self.assertEqual(previous, [self.person, self.person, None])

    def test_capture_keeps_selection_on_auto_toggle_and_clears_on_tracking_restart(self):
        for restart_tracking in (False, True):
            with self.subTest(restart_tracking=restart_tracking):
                self.app.tracking_active = True
                self.app.selected_target = (self.person, self.frame.shape[:2])
                stop = threading.Event()
                frames = queue.Queue(maxsize=1)
                _, encoded = cv2.imencode('.jpg', self.frame)
                iteration = 0
                def next_iteration(_):
                    nonlocal iteration
                    iteration += 1
                    self.app.tracking_revision += 1  # Auto-follow also changes this.
                    if restart_tracking:
                        self.app.selected_target = None
                    if iteration == 2:
                        stop.set()
                with patch.object(tracker.urllib.request, 'urlopen',
                                  side_effect=lambda *a, **k: io.BytesIO(encoded.tobytes())), \
                        patch.object(self.app, 'detect_person',
                                     return_value=(self.person, self.frame, [self.person])) as detect, \
                        patch.object(stop, 'wait', side_effect=next_iteration):
                    self.app.capture_frames('test-camera', stop, frames)
                previous = [call.args[1] for call in detect.call_args_list]
                self.assertEqual(previous, [self.person, None if restart_tracking else self.person])

    def test_capture_preserves_lock_across_resolution_change(self):
        self.app.tracking_active = True
        self.app.selected_target = (self.person, self.frame.shape[:2])
        stop = threading.Event()
        frames = queue.Queue(maxsize=1)
        large_frame = np.zeros((960, 1280, 3), dtype=np.uint8)
        images = [cv2.imencode('.jpg', frame)[1].tobytes()
                  for frame in (self.frame, large_frame)]
        waits = iter((False, True))
        with patch.object(tracker.urllib.request, 'urlopen',
                          side_effect=[io.BytesIO(data) for data in images]), \
                patch.object(self.app, 'detect_person', side_effect=[
                    (self.person, self.frame, [self.person]), (None, large_frame, []),
                ]) as detect, patch.object(stop, 'wait',
                    side_effect=lambda _: stop.set() if next(waits) else None):
            self.app.capture_frames('test-camera', stop, frames)
        previous = detect.call_args_list[1].args[1]
        self.assertEqual(previous['bbox'], (560, 200, 720, 600))
        self.assertEqual(previous['center'], (640, 400))

    def test_tracking_toggle_during_http_capture_marks_result_as_old(self):
        self.app.tracking_active = True
        stop = threading.Event()
        frames = queue.Queue(maxsize=1)
        _, encoded = cv2.imencode('.jpg', self.frame)
        def response(*args, **kwargs):
            self.app.tracking_revision += 1
            return io.BytesIO(encoded.tobytes())
        with patch.object(tracker.urllib.request, 'urlopen', side_effect=response), \
                patch.object(stop, 'wait', side_effect=lambda _: stop.set()):
            self.app.capture_frames('test-camera', stop, frames)
        self.assertNotEqual(frames.get_nowait()[0], self.app.tracking_revision)

    def test_invalid_camera_image_returns_error(self):
        frames = queue.Queue(maxsize=1)
        with patch.object(tracker.urllib.request, "urlopen", return_value=io.BytesIO(b"invalid")):
            self.app.capture_frames("test-camera", threading.Event(), frames)
        self.assertIn("invalid image", frames.get_nowait()[3])

    def test_camera_error_stops_robot_on_gui_thread(self):
        self.app.camera_active = self.app.tracking_active = self.app.auto_mode = True
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put((None, None, None, "test timeout", self.now, []))
        with patch.object(tracker.messagebox, "showerror"):
            self.app.run_camera()
        self.assertFalse(self.app.auto_mode)
        self.assertFalse(self.app.camera_active)
        self.app.conn.sendall.assert_called_with(b"0 0\n")

    def test_old_result_after_tracking_toggle_cannot_restart_motion(self):
        self.app.camera_active = self.app.tracking_active = self.app.auto_mode = True
        self.app.tracking_revision = 2
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put((1, self.person, self.frame, None, self.now, [self.person]))
        self.app.run_camera()
        self.app.conn.sendall.assert_not_called()
        self.assertIsNone(self.app.current_frame)

    def test_current_result_updates_real_tk_canvas_and_stops_on_no_person(self):
        self.app.camera_active = self.app.tracking_active = self.app.auto_mode = True
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put((0, None, self.frame, None, self.now, [self.person]))
        self.app.run_camera()
        self.assertIsNotNone(self.app.current_frame)
        self.assertEqual(len(self.app.camera_canvas.find_all()), 1)
        self.app.conn.sendall.assert_called_with(b"0 0\n")

    def test_stale_image_cannot_start_motion(self):
        self.app.camera_active = self.app.tracking_active = self.app.auto_mode = True
        self.app.vl = self.app.vr = 100
        self.app.camera_frames = queue.Queue()
        self.app.camera_frames.put((0, self.person, self.frame, None, self.now - 2, [self.person]))
        self.app.run_camera()
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.assertFalse(self.app.person_detected)
        self.assertIsNone(self.app.current_frame)

    def test_missing_camera_frames_stop_auto_before_refreshing_commands(self):
        self.app.auto_mode = True
        self.app.vl = self.app.vr = 100
        self.app.last_detection_at = self.now - 2
        self.app.poll_connection()
        self.app.conn.sendall.assert_called_once_with(b"0 0\n")
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.assertTrue(self.app.auto_mode)

    def test_manual_drive_is_refreshed_for_esp_watchdog(self):
        self.app.left_forward()
        self.app.conn.sendall.reset_mock()
        self.now += 0.3
        self.app.poll_connection()
        self.app.conn.sendall.assert_called_once_with(b"20 0\n")

    def test_refresh_is_not_sent_before_interval_has_elapsed(self):
        self.app.left_forward()
        self.app.conn.sendall.reset_mock()
        self.now += 0.05
        self.app.poll_connection()
        self.app.conn.sendall.assert_not_called()

    def test_single_follow_command_expires_and_manual_override_cancels_it(self):
        self.app.tracking_active = True
        self.app.update_person(self.person)
        self.app.follow_nearest_person()
        self.assertGreater(self.app.vl, 0)
        self.now += 0.6
        self.app.check_follow_timeout()
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.assertIsNone(self.app.follow_once_until)
        self.app.follow_nearest_person()
        self.app.right_reverse()
        self.assertEqual((self.app.vl, self.app.vr), (0, -20))
        self.assertIsNone(self.app.follow_once_until)

    def test_single_follow_rejects_stale_person(self):
        self.app.tracking_active = True
        self.app.update_person(self.person)
        self.now += 2
        self.app.follow_nearest_person()
        self.app.conn.sendall.assert_not_called()

    def test_auto_start_waits_for_result_from_new_capture(self):
        self.app.tracking_active = self.app.camera_active = True
        self.app.vl, self.app.vr = 150, -150
        old_revision = self.app.tracking_revision
        self.app.toggle_auto_follow()
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        self.assertIsNone(self.app.last_detection_at)
        self.assertGreater(self.app.tracking_revision, old_revision)

    def test_socket_send_failure_clears_connection_and_auto(self):
        conn = self.app.conn
        conn.sendall.side_effect = OSError("test disconnect")
        self.app.auto_mode = True
        self.app.send_command(100, 100)
        self.assertIsNone(self.app.conn)
        self.assertFalse(self.app.auto_mode)
        self.assertEqual((self.app.vl, self.app.vr), (0, 0))
        conn.close.assert_called_once()

    def test_real_local_socket_handoff_commands_and_reconnect(self):
        self.app.host = "127.0.0.1"
        self.app.port = 0  # OS chooses an unused test port.
        self.app.start_socket()
        deadline = time.perf_counter() + 2
        port = 0
        while not port and time.perf_counter() < deadline:
            if self.app.socket is not None:
                port = self.app.socket.getsockname()[1]
            if not port:
                threading.Event().wait(0.01)
        self.assertNotEqual(port, 0, "Test socket did not start")

        def accept_on_gui_thread():
            event = self.app.connection_events.get(timeout=2)
            self.app.connection_events.put(event)
            self.root.after_cancel(self.app.connection_after_id)
            self.app.poll_connection()

        with socket.create_connection(("127.0.0.1", port), timeout=2) as first:
            accept_on_gui_thread()
            self.assertEqual(first.recv(100), b"0 0\n")
            self.app.left_forward()
            self.assertEqual(first.recv(100), b"20 0\n")
            self.now += 0.3
            self.root.after_cancel(self.app.connection_after_id)
            self.app.poll_connection()
            self.assertEqual(first.recv(100), b"20 0\n")  # Actual TCP refresh.
            with socket.create_connection(("127.0.0.1", port), timeout=2) as second:
                accept_on_gui_thread()
                self.assertEqual(second.recv(100), b"0 0\n")
                self.app.right_reverse()
                self.assertEqual(second.recv(100), b"0 -20\n")


if __name__ == "__main__":
    unittest.main()
