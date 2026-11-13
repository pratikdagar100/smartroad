"""SmartRoadRuntime: wires every subsystem together and owns the worker threads.

Threads:  camera capture (in camera), GPS (in gps), DB writer (in database),
          pipeline (inference + events + video encode), coordinator
          (GPS -> route/proximity/status), health sampler, API (uvicorn).
Every worker is wrapped so one failing subsystem never takes the app down.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from camera.factory import make_camera
from database.db import Database
from detection.detector import ST_DEMO, ST_ERROR, ST_MISSING, ST_OK, make_detector
from detection.model_manager import ModelJob, load_catalog
from detection.tracker import Tracker
from . import source as source_mod
from gps.gps_service import GPSProvider, NullGPS, SerialGPSService
from gps.gps_simulator import GPSSimulator, load_route
from mapping.geolocate import make_estimator
from mapping.proximity import ProximityEngine
from mapping.route import RouteRecorder, find_repeated_route
from privacy.blur import PrivacyFilter
from sensors.imu import IMUMonitor
from system.health import HealthMonitor
from verification.fusion import CandidateEvent, EventEngine
from .config import Config
from .state import SharedState

log = logging.getLogger("smartroad.runtime")

# settings that may be changed at runtime through the API / settings page
EDITABLE = {
    "proximity.warning_distance_m": float, "proximity.critical_distance_m": float,
    "model.confidence": float, "events.min_confidence": float,
    "hazards.merge_radius_m": float, "map.default_zoom": int, "map.zoom_by_speed": bool,
    "severity.medium_threshold": float, "severity.high_threshold": float,
    "geolocation.distance_estimate_m": float, "display.brightness": int,
}


def supervised(name: str, fn: Callable[[], None], stop: threading.Event, on_error: Callable[[str], None]) -> None:
    """Run ``fn`` forever (until stop), restarting with back-off if it raises."""
    delay = 1.0
    while not stop.is_set():
        try:
            fn()
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Thread %s crashed: %s", name, exc)
            on_error(f"{name}: {exc}")
            stop.wait(delay)
            delay = min(30.0, delay * 2)


class SmartRoadRuntime:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.demo = cfg.demo_enabled
        if self.demo:  # the synthetic camera uses the flat-road model, so geolocate with it
            cfg.geolocation.mode = "bbox"
        gps_sim = self.demo or cfg.gps.mode == "simulated"
        mode = "demo" if self.demo else ("test" if gps_sim else "hardware")
        self.state = SharedState(mode=mode, gps_mode="simulated" if gps_sim else "serial")
        self.stop_event = threading.Event()
        self.restart_callback: Optional[Callable[[], None]] = None   # set by app.main
        self.model_job = ModelJob()
        self.source_mode = source_mod.current_mode(cfg)
        self._threads: List[threading.Thread] = []

        # ---- database -----------------------------------------------------
        db_path = cfg.db_path
        if self.demo and cfg.demo.reset_db:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(db_path) + suffix)
                if p.exists():
                    p.unlink()
        self.db = Database(db_path)
        self.vehicle_id = self.db.ensure_vehicle(cfg.app.vehicle_name)
        self._apply_stored_settings()

        # ---- GPS ----------------------------------------------------------
        self.simulator: Optional[GPSSimulator] = None
        self.demo_potholes: List[dict] = []
        if gps_sim:
            route = load_route(cfg.resolve(cfg.demo.route_file))
            self.simulator = GPSSimulator(route, cfg.demo.speed_kmh, cfg.demo.time_scale,
                                          cfg.demo.gps_noise_m if self.demo else 0.0)
            self.gps: GPSProvider = self.simulator
            if self.demo:
                self.demo_potholes = self._load_demo_hazards()
        else:
            self.gps = SerialGPSService(cfg.gps.device, cfg.gps.baudrate, cfg.gps.max_age_s,
                                        cfg.gps.min_satellites, cfg.gps.reconnect_seconds)

        # ---- camera / AI --------------------------------------------------
        try:
            self.camera = make_camera(cfg, self.simulator, self.demo_potholes)
        except Exception as exc:  # noqa: BLE001
            log.error("Camera could not be created: %s", exc)
            self.camera = None
            self.state.set_subsystem("camera", "ERROR", f"CAMERA ERROR: {exc}")
        self.detector = make_detector(cfg, self.demo)
        self.tracker = Tracker(cfg.events.min_stable_frames, cfg.events.max_track_misses, cfg.events.track_iou)

        # ---- logic --------------------------------------------------------
        self.imu = IMUMonitor(cfg.imu.bus, cfg.imu.address, cfg.imu.impact_g,
                              on_impact=self._on_impact) if cfg.imu.enabled else None
        self.engine = EventEngine(cfg, make_estimator(cfg), self.imu)
        p = cfg.proximity
        self.proximity = ProximityEngine(p.warning_distance_m, p.critical_distance_m, p.hysteresis_factor,
                                         p.ahead_angle_deg, p.lookup_radius_m, p.new_hazard_quiet_s,
                                         p.min_heading_speed_ms)
        r = cfg.route
        self.recorder = RouteRecorder(self.db, self.vehicle_id, r.track_interval_s, r.min_distance_m, r.gap_new_route_s)
        self.privacy = PrivacyFilter(cfg.privacy.enabled, cfg.privacy.blur_faces, cfg.privacy.blur_plates)
        self.health = HealthMonitor(cfg.resolve("data"))
        self.image_dir = cfg.resolve(cfg.hazards.image_dir)

        if self.demo and cfg.demo.preseed_known:
            self._seed_demo_hazards()
        self.reload_hazards()

    # ------------------------------------------------------------ demo data
    def _load_demo_hazards(self) -> List[dict]:
        path = self.cfg.resolve(self.cfg.demo.hazards_file)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("Cannot read demo hazards %s: %s", path, exc)
            return []

    def _seed_demo_hazards(self) -> None:
        for h in self.demo_potholes:
            if h.get("known"):
                self.db.record_hazard_event(
                    lat=h["latitude"], lon=h["longitude"], hazard_type=h.get("type", "pothole"),
                    confidence=0.9, severity=h.get("severity", "MEDIUM"), severity_score=0.5,
                    merge_radius_m=self.cfg.hazards.merge_radius_m, source="demo-seed")

    # ------------------------------------------------------------- settings
    def _set_dotted(self, key: str, value) -> None:
        sec, name = key.split(".")
        setattr(getattr(self.cfg, sec), name, value)

    def _apply_stored_settings(self) -> None:
        for k, v in self.db.all_settings().items():
            if k in EDITABLE:
                try:
                    self._set_dotted(k, EDITABLE[k](v))
                except (TypeError, ValueError):
                    pass

    def apply_settings(self, changes: Dict[str, object]) -> Dict[str, object]:
        applied = {}
        for k, v in changes.items():
            if k not in EDITABLE:
                raise KeyError(f"setting {k!r} is not editable")
            val = EDITABLE[k](v)
            self._set_dotted(k, val)
            self.db.set_setting(k, val)
            applied[k] = val
        pr = self.cfg.proximity
        if pr.critical_distance_m > pr.warning_distance_m:
            raise ValueError("critical distance must be <= warning distance")
        self.proximity.configure(warning_m=pr.warning_distance_m, critical_m=pr.critical_distance_m)
        self.engine.estimator = make_estimator(self.cfg)
        if "display.brightness" in applied:
            self._apply_brightness(applied["display.brightness"])
        return applied

    @staticmethod
    def _apply_brightness(percent: int) -> None:
        """Set the backlight if the display exposes one (Pi DSI / official touchscreen)."""
        try:
            for bl in Path("/sys/class/backlight").glob("*"):
                top = int((bl / "max_brightness").read_text())
                (bl / "brightness").write_text(str(max(1, int(top * max(0, min(100, percent)) / 100))))
                return
            log.info("No backlight device - brightness setting stored only")
        except OSError as exc:
            log.warning("Cannot set brightness: %s", exc)

    def reload_hazards(self) -> None:
        hs = self.db.get_hazards()
        self.proximity.load(hs)
        self.state.set(known_count=len(hs))
        self.state.bump_hazards()

    # ---------------------------------------------------------- AI model mgmt
    @property
    def models_dir(self) -> Path:
        return self.cfg.resolve("models")

    def reload_detector(self) -> None:
        """Hot-swap the detector after a model was installed or selected."""
        self.detector = make_detector(self.cfg, self.demo)
        self.tracker = Tracker(self.cfg.events.min_stable_frames, self.cfg.events.max_track_misses,
                               self.cfg.events.track_iou)
        log.info("Detector reloaded: %s %s", self.detector.status, self.detector.message)

    def select_model(self, filename: str) -> None:
        p = (self.models_dir / filename).resolve()
        if p.parent != self.models_dir.resolve() or not p.is_file() or p.suffix.lower() not in (".onnx", ".tflite"):
            raise ValueError("unknown model file")
        rel = p.relative_to(self.cfg.root).as_posix() if p.is_relative_to(self.cfg.root) else str(p)
        self.cfg.model.path = rel
        data = source_mod.load(self.cfg.root)
        data["model_path"] = rel
        source_mod.save(self.cfg.root, data)
        self.reload_detector()

    def install_model(self, model_id: str) -> bool:
        entry = next((e for e in load_catalog(self.models_dir) if e["id"] == model_id), None)
        if entry is None:
            raise KeyError(model_id)

        def done(path: Path) -> None:
            self.select_model(path.name)
        return self.model_job.start(entry, self.models_dir, done)

    def request_restart(self) -> bool:
        if self.restart_callback is None:
            return False
        threading.Timer(0.5, self.restart_callback).start()   # let the HTTP reply go out first
        return True

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        log.info("SmartRoad starting (mode=%s, db=%s)", self.state.mode, self.cfg.db_path)
        self.state.set_subsystem("database", "OK", str(self.cfg.db_path.name))
        self.health.start()
        self.gps.start()
        if self.camera:
            self.camera.start()
        if self.imu:
            self.imu.start()
        for name, fn in (("pipeline", self._pipeline_loop), ("coordinator", self._coordinator_loop)):
            t = threading.Thread(
                target=supervised, args=(name, fn, self.stop_event,
                                         lambda m, n=name: self.state.set_subsystem(n, "ERROR", m)),
                name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        log.info("SmartRoad stopping")
        self.stop_event.set()
        for t in self._threads:
            t.join(timeout=3)
        for comp in (self.camera, self.gps, self.imu, self.health):
            try:
                if comp:
                    comp.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.recorder.finish()
            self.db.close()
        except Exception as exc:  # noqa: BLE001
            log.error("Shutdown error: %s", exc)

    # ---------------------------------------------------------- IMU callback
    def _on_impact(self, g: float) -> None:
        gps = self.gps.get_state()
        if gps.fix:
            ids = self.db.mark_impact_verified(gps.latitude, gps.longitude, self.cfg.imu.verify_radius_m,
                                               self.cfg.imu.window_s + 5)
            if ids:
                log.info("IMU impact %.2fg verified hazards %s", g, ids)
                self.state.add_event("verified", f"Impact {g:.1f} g verified hazard #{ids[0]}")
                self.state.bump_hazards()

    # ------------------------------------------------------------- pipeline
    def _pipeline_loop(self) -> None:
        cfg, st = self.cfg, self.state
        last_seq, n, last_enc = -1, 0, 0.0
        enc_interval = 1.0 / max(1, cfg.camera.stream_fps)
        q = [int(cv2.IMWRITE_JPEG_QUALITY), int(cfg.camera.jpeg_quality)]
        dets_cache: List = []
        det_count, det_t0 = 0, time.time()
        stream_count, stream_t0 = 0, time.time()
        last_placeholder = 0.0
        while not self.stop_event.is_set():
            frame = self.camera.get_frame() if self.camera else None
            if frame is None:
                if time.time() - last_placeholder > 1.0:
                    st.set_jpeg(self._placeholder("CAMERA ERROR" if not self.camera or self.camera.status == "ERROR"
                                                  else "CONNECTING CAMERA...", q))
                    last_placeholder = time.time()
                st.set(detections=[])
                self.stop_event.wait(0.1)
                continue
            if frame.seq == last_seq:
                self.stop_event.wait(0.005)
                continue
            last_seq = frame.seq
            img = frame.image
            oh, ow = img.shape[:2]
            pw = int(cfg.camera.process_width)
            if ow > pw:
                img = cv2.resize(img, (pw, int(oh * pw / ow)), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
            sx = w / ow

            n += 1
            if self.detector.ready and n % int(cfg.model.frame_skip) == 0:
                meta = dict(frame.meta)
                if "truth" in meta:
                    meta["truth"] = [{**t, "bbox": tuple(v * sx for v in t["bbox"])} for t in meta["truth"]]
                dets_cache = self.detector.detect(img, meta)
                newly = self.tracker.update(dets_cache)
                det_count += 1
                st.set(infer_ms=self.detector.infer_ms)
                gps = self.gps.get_state()
                for tr in newly:
                    ev = self.engine.process(tr, gps, (w, h), frame_ts=frame.timestamp)
                    if ev:
                        self._commit_event(ev, img)
            elif not self.detector.ready:
                dets_cache = []

            now = time.time()
            if now - det_t0 >= 2:
                st.set(detect_fps=det_count / (now - det_t0))
                det_count, det_t0 = 0, now
            if now - last_enc >= enc_interval:
                last_enc = now
                out = img.copy()
                self._draw_detections(out)
                ok, buf = cv2.imencode(".jpg", out, q)
                if ok:
                    st.set_jpeg(buf.tobytes())
                    stream_count += 1
                st.set(detections=self._det_json(w, h))
                if now - stream_t0 >= 2:
                    st.set(stream_fps=stream_count / (now - stream_t0))
                    stream_count, stream_t0 = 0, now
            st.count("frames")

    @staticmethod
    def _placeholder(text: str, q) -> bytes:
        img = np.full((360, 640, 3), 24, np.uint8)
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
        cv2.putText(img, text, ((640 - tw) // 2, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (80, 80, 255), 2)
        return cv2.imencode(".jpg", img, q)[1].tobytes()

    def _draw_detections(self, img: np.ndarray) -> None:
        for t in self.tracker.active():
            x1, y1, x2, y2 = (int(v) for v in t.bbox)
            col = (0, 140, 255) if t.confirmed else (0, 215, 255)
            cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
            label = f"{t.cls_name.upper().replace('_', ' ')} {int(t.confidence * 100)}%"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), col, -1)
            cv2.putText(img, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    def _det_json(self, w: int, h: int) -> List[dict]:
        return [{"track": t.id, "class": t.cls_name, "confidence": round(t.confidence, 3),
                 "bbox": [int(v) for v in t.bbox], "confirmed": t.confirmed,
                 "bbox_norm": [round(t.bbox[0] / w, 3), round(t.bbox[1] / h, 3),
                               round(t.bbox[2] / w, 3), round(t.bbox[3] / h, 3)]}
                for t in self.tracker.active()]

    # --------------------------------------------------------- event commit
    def _commit_event(self, ev: CandidateEvent, img: np.ndarray) -> None:
        st = self.state
        try:
            hazard, created = self.db.record_hazard_event(
                lat=ev.latitude, lon=ev.longitude, hazard_type=ev.hazard_type, confidence=ev.confidence,
                severity=ev.severity, severity_score=ev.severity_score,
                merge_radius_m=self.cfg.hazards.merge_radius_m, source="camera", heading=ev.heading,
                speed=ev.speed, route_id=self.recorder.route_id, bbox=list(ev.bbox_px),
                vehicle_pos=(ev.vehicle_lat, ev.vehicle_lon), distance_m=ev.distance_m,
                impact=ev.impact_g, verified=ev.verified)
            if created:
                path = self._save_image(img, ev, hazard.id)
                if path:
                    self.db.set_hazard_image(hazard.id, path)
                    hazard.image_path = path
                self.proximity.quiet(hazard.id)
            self.proximity.upsert(hazard)
            st.set(known_count=self.proximity.count())
            st.bump_hazards()
            st.count("events_created" if created else "events_merged")
            st.raise_alert({"text": "POTHOLE DETECTED" if ev.hazard_type == "pothole"
                            else f"{ev.hazard_type.upper().replace('_', ' ')} DETECTED",
                            "confidence": ev.confidence, "hazard_id": hazard.id, "new": created,
                            "severity": hazard.severity, "lat": hazard.latitude, "lon": hazard.longitude})
            st.add_event("hazard_new" if created else "hazard_merged",
                         f"{'New' if created else 'Known'} {ev.hazard_type} #{hazard.id} "
                         f"({int(ev.confidence * 100)}%, est. severity {hazard.severity})", hazard_id=hazard.id)
            st.set_subsystem("database", "OK", "")
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to store hazard: %s", exc)
            st.set_subsystem("database", "ERROR", str(exc))

    def _save_image(self, img: np.ndarray, ev: CandidateEvent, hazard_id: int) -> Optional[str]:
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            folder = self.image_dir / day
            folder.mkdir(parents=True, exist_ok=True)
            out = self.privacy.apply(img)
            x1, y1, x2, y2 = ev.bbox_px
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 140, 255), 2)
            h, w = out.shape[:2]
            cv2.rectangle(out, (0, h - 34), (w, h), (0, 0, 0), -1)
            cv2.putText(out, f"HAZARD #{hazard_id:04d}  {ev.hazard_type.upper()}  {int(ev.confidence * 100)}%"
                        f"  est.sev {ev.severity}", (6, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(out, f"{datetime.now():%Y-%m-%d %H:%M:%S}  GPS {ev.latitude:.6f}, {ev.longitude:.6f}",
                        (6, h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
            f = folder / f"hazard_{hazard_id:04d}.jpg"
            cv2.imwrite(str(f), out, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            return f.relative_to(self.cfg.root).as_posix() if f.is_relative_to(self.cfg.root) else str(f)
        except Exception as exc:  # noqa: BLE001
            log.error("Could not save hazard image: %s", exc)
            return None

    # ---------------------------------------------------------- coordinator
    def _coordinator_loop(self) -> None:
        st, cfg = self.state, self.cfg
        last_repeat = 0.0
        while not self.stop_event.wait(0.2):
            g = self.gps.get_state()
            gd = g.to_dict()
            st.set(gps=gd)
            self._update_subsystems(g)
            if g.fix:
                self.recorder.update(g.latitude, g.longitude, g.altitude, g.speed, g.heading, g.accuracy)
                res = self.proximity.update(g.latitude, g.longitude, g.heading, g.speed)
                for trig in res.new_triggers:
                    log.info("Known hazard warning: #%s %s at %.0f m", trig["id"], trig["level"], trig["distance_m"])
                    st.add_event("known_warning", f"Known hazard #{trig['id']} ahead - {trig['distance_m']:.0f} m",
                                 hazard_id=trig["id"])
                st.set(proximity={"nearby": res.nearby[:20], "warning": res.warning})
            else:
                self.proximity.update(None, None, None)
                st.set(proximity={"nearby": [], "warning": None})
            rec = self.recorder
            route = {"id": rec.route_id, "distance_m": round(rec.distance_m, 1), "points": len(rec.points),
                     "repeat": st.route.get("repeat")}
            if time.time() - last_repeat > 30 and len(rec.points) >= 10:
                last_repeat = time.time()
                route["repeat"] = find_repeated_route(self.db, rec.points, rec.route_id, cfg.route.repeat_tolerance_m,
                                                      cfg.route.repeat_min_overlap)
                if route["repeat"]:
                    log.info("Repeat route detected: %s", route["repeat"])
            st.set(route=route)

    def _update_subsystems(self, g) -> None:
        st = self.state
        cam = self.camera
        if cam is None:
            pass
        elif cam.status == "OK":
            st.set_subsystem("camera", "OK", f"{cam.name} {cam.capture_fps:.0f} fps")
        elif cam.status == "CONNECTING":
            st.set_subsystem("camera", "WARN", "connecting")
        else:
            st.set_subsystem("camera", "ERROR", f"CAMERA ERROR: {cam.message}")
        d = self.detector
        if d.status == ST_OK:
            st.set_subsystem("ai", "OK", f"{d.backend} {d.infer_ms:.0f} ms")
        elif d.status == ST_DEMO:
            st.set_subsystem("ai", "OK", "SIMULATED (demo)")
        elif d.status == ST_MISSING:
            st.set_subsystem("ai", "ERROR", "AI MODEL NOT INSTALLED")
        else:
            st.set_subsystem("ai", "ERROR", d.message)
        if g.fix:
            st.set_subsystem("gps", "OK", ("SIMULATED" if g.source == "simulated" else f"{g.satellites or '?'} sats"))
        elif g.status == "UNAVAILABLE":
            st.set_subsystem("gps", "ERROR", f"GPS UNAVAILABLE: {g.message}")
        else:
            st.set_subsystem("gps", "WARN", "GPS SIGNAL LOST" if g.status == "SIGNAL_LOST" else "waiting for fix")
        if self.imu:
            st.set_subsystem("imu", "OK" if self.imu.status == "OK" else "WARN", self.imu.status)
        else:
            st.set_subsystem("imu", "OFF", "disabled")
        if not self.db.ping():
            st.set_subsystem("database", "ERROR", self.db.last_error or "unreachable")
