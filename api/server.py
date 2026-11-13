"""Local REST + WebSocket API and static UI host (bound to 127.0.0.1 by default)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import source as source_mod
from app.runtime import EDITABLE, SmartRoadRuntime
from detection.model_manager import list_installed, load_catalog
from database.models import STATUSES, iso_to_epoch
from mapping.geo import haversine_m, lat_lon_valid
from verification.severity import classify

log = logging.getLogger("smartroad.api")


class HazardIn(BaseModel):
    latitude: float
    longitude: float
    hazard_type: str = "pothole"
    severity: str = "MEDIUM"
    confidence: float = Field(1.0, ge=0, le=1)


class StatusIn(BaseModel):
    status: str


class SourceIn(BaseModel):
    mode: str
    camera_device: Optional[str] = None
    video_path: Optional[str] = None
    gps_device: Optional[str] = None


class ModelIdIn(BaseModel):
    id: str


class ModelFileIn(BaseModel):
    file: str


def create_app(rt: SmartRoadRuntime) -> FastAPI:
    cfg, st, db = rt.cfg, rt.state, rt.db
    app = FastAPI(title="SmartRoad API", version="1.0")
    frontend = cfg.root / "frontend"
    session_start = time.time()

    def require_token(authorization: Optional[str] = Header(None)) -> None:
        """If SMARTROAD_API_TOKEN is set, mutating calls need 'Bearer <token>'."""
        token = os.environ.get("SMARTROAD_API_TOKEN")
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "invalid or missing API token")

    def hz(h) -> dict:
        d = h.to_dict()
        t = iso_to_epoch(h.first_detected) or 0
        d["is_new"] = t >= session_start - 1 and h.source != "demo-seed"
        d["image_url"] = ("/" + h.image_path) if h.image_path else None
        return d

    # ------------------------------------------------------------- status
    @app.get("/api/status")
    def status():
        snap = st.snapshot()
        return {"app": cfg.app.name, "version": "1.0", "mode": snap["mode"], "source_mode": rt.source_mode, "gps_mode": snap["gps_mode"],
                "uptime_s": snap["uptime_s"], "subsystems": snap["subsystems"],
                "session_start": session_start, "fps": snap["fps"]}

    @app.get("/api/gps")
    def gps():
        return rt.gps.get_state().to_dict()

    @app.get("/api/health")
    def health():
        snap = st.snapshot()
        return {"system": rt.health.get(), "subsystems": snap["subsystems"],
                "detector": {"status": rt.detector.status, "backend": rt.detector.backend,
                             "message": rt.detector.message},
                "camera": {"status": rt.camera.status if rt.camera else "ERROR",
                           "name": rt.camera.name if rt.camera else None,
                           "fps": round(rt.camera.capture_fps, 1) if rt.camera else 0},
                "rejected_events": rt.engine.rejected, "counters": snap["counters"],
                "db": {"path": str(cfg.db_path), "ok": db.ping()}}

    # ------------------------------------------------------------ hazards
    @app.get("/api/hazards")
    def hazards(status: Optional[str] = None, hazard_type: Optional[str] = None):
        if status and status.upper() not in STATUSES:
            raise HTTPException(400, f"status must be one of {STATUSES}")
        return [hz(h) for h in db.get_hazards(status.upper() if status else None, hazard_type)]

    @app.get("/api/hazards/nearby")
    def nearby(lat: Optional[float] = None, lon: Optional[float] = None, radius: float = Query(300, le=5000)):
        if lat is None or lon is None:
            g = rt.gps.get_state()
            if not g.fix:
                raise HTTPException(409, "no GPS fix and no lat/lon supplied")
            lat, lon = g.latitude, g.longitude
        out = []
        for h in db.get_hazards():
            d = haversine_m(lat, lon, h.latitude, h.longitude)
            if d <= radius:
                x = hz(h)
                x["distance_m"] = round(d, 1)
                out.append(x)
        return sorted(out, key=lambda x: x["distance_m"])

    @app.post("/api/hazards", status_code=201, dependencies=[Depends(require_token)])
    def add_hazard(body: HazardIn):
        if not lat_lon_valid(body.latitude, body.longitude):
            raise HTTPException(422, "invalid coordinates")
        sev = body.severity.upper()
        score = {"LOW": 0.2, "MEDIUM": 0.5, "HIGH": 0.8}.get(sev)
        if score is None:
            raise HTTPException(422, "severity must be LOW|MEDIUM|HIGH")
        h, created = db.record_hazard_event(
            lat=body.latitude, lon=body.longitude, hazard_type=body.hazard_type, confidence=body.confidence,
            severity=sev, severity_score=score, merge_radius_m=cfg.hazards.merge_radius_m, source="manual")
        rt.proximity.upsert(h)
        rt.state.set(known_count=rt.proximity.count())
        st.bump_hazards()
        return {"created": created, "hazard": hz(h)}

    @app.post("/api/hazards/{hazard_id}/status", dependencies=[Depends(require_token)])
    def set_status(hazard_id: int, body: StatusIn):
        try:
            ok = db.set_hazard_status(hazard_id, body.status)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not ok:
            raise HTTPException(404, "hazard not found")
        rt.proximity.upsert(db.get_hazard(hazard_id))
        st.bump_hazards()
        return hz(db.get_hazard(hazard_id))

    # ------------------------------------------------------------- routes
    @app.get("/api/route")
    def route(route_id: Optional[int] = None, max_points: int = Query(2000, le=20000)):
        rid = route_id if route_id is not None else rt.recorder.route_id
        if rid is None:
            return {"route_id": None, "points": []}
        pts = db.get_route_points(rid)
        step = max(1, len(pts) // max_points)
        return {"route_id": rid, "points": [[round(a, 6), round(b, 6)] for a, b in pts[::step]],
                "repeat": st.route.get("repeat")}

    @app.get("/api/routes")
    def routes():
        return db.list_routes()

    # ----------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        c = cfg.to_dict()
        c["map"]["offline_tiles"] = cfg.resolve(cfg.map.offline_tiles_dir).is_dir()
        return {"config": c, "editable": sorted(EDITABLE), "stored": db.all_settings()}

    @app.post("/api/settings", dependencies=[Depends(require_token)])
    def post_settings(changes: dict):
        try:
            return {"applied": rt.apply_settings(changes)}
        except KeyError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    # ------------------------------------------------ input source switching
    @app.get("/api/source")
    def get_source():
        saved = source_mod.load(cfg.root)
        return {"current": rt.source_mode, "options": source_mod.MODES, "saved": saved,
                "camera_device": str(cfg.camera.device), "gps_device": str(cfg.gps.device),
                "video_path": str(cfg.camera.source or "")}

    @app.post("/api/source", dependencies=[Depends(require_token)])
    def set_source(body: SourceIn):
        data = source_mod.load(cfg.root)
        data["mode"] = body.mode
        for k in ("camera_device", "video_path", "gps_device"):
            v = getattr(body, k)
            if v is not None:
                data[k] = v.strip()
        err = source_mod.validate(data)
        if err:
            raise HTTPException(422, err)
        source_mod.save(cfg.root, data)
        log.info("UI requested source switch to %s", body.mode)
        return {"restarting": rt.request_restart(), "mode": body.mode}

    # ------------------------------------------------------------ AI models
    def model_info():
        cur = cfg.resolve(cfg.model.path)
        installed = list_installed(rt.models_dir)
        names = {m["file"] for m in installed}
        cat = [{**e, "installed": e["file"] in names} for e in load_catalog(rt.models_dir)]
        return {"current": {"path": cfg.model.path, "file": cur.name, "exists": cur.exists(),
                            "status": rt.detector.status, "backend": rt.detector.backend,
                            "message": rt.detector.message or ("AI MODEL NOT INSTALLED" if not cur.exists() else ""),
                            "classes": list(cfg.model.classes)},
                "installed": installed, "catalog": cat, "job": rt.model_job.snapshot()}

    @app.get("/api/models")
    def models():
        return model_info()

    @app.post("/api/models/install", dependencies=[Depends(require_token)])
    def models_install(body: ModelIdIn):
        try:
            started = rt.install_model(body.id)
        except KeyError:
            raise HTTPException(404, "unknown model id") from None
        return {"started": started, "job": rt.model_job.snapshot()}

    @app.post("/api/models/select", dependencies=[Depends(require_token)])
    def models_select(body: ModelFileIn):
        try:
            rt.select_model(body.file)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return model_info()

    @app.get("/api/statistics")
    def statistics():
        s = db.statistics()
        s["session"] = st.snapshot()["counters"]
        s["known_in_memory"] = rt.proximity.count()
        return s

    # -------------------------------------------------------------- video
    @app.get("/video.mjpg")
    async def video():
        async def gen():
            seq = -1
            loop = asyncio.get_running_loop()
            while True:
                seq, data = await loop.run_in_executor(None, st.wait_jpeg, seq, 2.0)
                if data:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/snapshot.jpg")
    def snapshot_jpg():
        data = st.get_jpeg()
        if not data:
            raise HTTPException(503, "no frame yet")
        return StreamingResponse(iter([data]), media_type="image/jpeg")

    # ---------------------------------------------------------- websocket
    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json(st.snapshot())
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("ws closed: %s", exc)

    # ------------------------------------------------------------- static
    (cfg.root / cfg.hazards.image_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/data/hazards", StaticFiles(directory=str(rt.image_dir)), name="hazard-images")
    tiles = cfg.resolve(cfg.map.offline_tiles_dir)
    if tiles.is_dir():
        app.mount("/tiles", StaticFiles(directory=str(tiles)), name="tiles")

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=str(frontend)), name="frontend")
    return app
