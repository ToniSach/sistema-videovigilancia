# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

LAN NVR/VMS (Network Video Recorder / Video Management System) for up to 4 IP cameras with FFmpeg capture, YOLOv8 object detection, motion detection, continuous + event recording, Telegram alerts, and a PySide6 desktop client. The repo contains **two applications**: a Flask backend ([backend/app/](backend/app/)) and a PySide6 desktop client ([desktop_app/src/](desktop_app/src/)), communicating via JWT-authenticated REST.

Code, comments, log messages, and commit history are in **Spanish** — keep new messages consistent with that style.

## Commands

### Install
PyTorch must be installed **before** the rest, with the CPU index URL (see comment block at top of [requirements.txt](requirements.txt)):
```powershell
pip install torch==2.6.0+cpu torchvision==0.21.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```
FFmpeg must be on `PATH` (the app calls `shutil.which("ffmpeg")` at startup). Python 3.13 is required.

If `torch` or `ultralytics` are missing, `POST /api/v1/ai/<id>/activate` returns **503 + `error_code: AI_DEPENDENCIES_MISSING`** with the exact `pip` command to fix it. The check runs upfront in `AIService.activate_ai` so the failure surfaces at the HTTP layer instead of dying silently in the worker thread (see [model_pool.py:check_dependencies](backend/app/processing/ai/model_pool.py)).

### Run
```powershell
# Backend (Flask built-in server — el modo soportado)
python backend/app/main.py

# Desktop client
python desktop_app/src/main.py
```
The backend runs on Flask's built-in server (`app.run`, threaded). It is a
single-process app by design (see CRITICAL constraint below) — there is no
Gunicorn/Waitress layer, and adding one is unnecessary for this LAN appliance.

**Docker (Windows & Linux):** `docker compose up -d --build` brings up PostgreSQL +
the backend (which bundles FFmpeg and the go2rtc binary). See [DOCKER.md](DOCKER.md).
Uses **bridge networking with published ports** (works on Docker Desktop/Windows,
not just Linux); set `HOST_LAN_IP` in `.env.docker` for WebRTC ICE. Installs from
[requirements-backend.txt](requirements-backend.txt) (no PySide6; the desktop GUI
runs natively). Entry: [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml),
[docker/entrypoint.sh](docker/entrypoint.sh).

### Tests
The canonical test runner is [backend/tests/run_tests.py](backend/tests/run_tests.py) (plain `unittest`, not pytest):
```powershell
python backend/tests/run_tests.py            # all tests
python backend/tests/run_tests.py -v         # verbose
python backend/tests/run_tests.py -t TestCircularFrameBuffer            # single class
python backend/tests/run_tests.py -t TestCircularFrameBuffer.test_clear # single method
```
Many other files in `backend/tests/` (`a.py`, `g.py`, `l.py`, `nt.py`, `nvt*.py`, `test-*.py`, `todo.py`, etc.) are **ad-hoc exploration scripts**, not part of the suite — do not assume they run cleanly or represent canonical behavior.

### Database migrations
PostgreSQL via Alembic. Config at [alembic.ini](alembic.ini), scripts in [backend/app/database/migrations/](backend/app/database/migrations/). Note that [backend/app/main.py:145](backend/app/main.py#L145) also calls `Base.metadata.create_all()` on startup, so a fresh DB will work even without running `alembic upgrade head`.

## Architecture

### CRITICAL: process-wide singleton state
Almost every long-lived component is a `__new__`-based singleton holding live threads, FFmpeg subprocesses, frame buffers, model pools, or DB pools in **process memory**:
- `CameraManager` ([backend/app/cameras/camera_manager.py](backend/app/cameras/camera_manager.py))
- `DependencyContainer` ([backend/app/container.py](backend/app/container.py))
- `DatabaseManager` ([backend/app/database/connection.py](backend/app/database/connection.py))
- `EventManager` ([backend/app/events/event_manager.py](backend/app/events/event_manager.py))
- `GlobalExecutor` ([backend/app/core/executor.py](backend/app/core/executor.py), capped at 50 threads)
- `metrics_collector`, `consistency_checker`, `go2rtc_manager` (sidecar de medios)

Consequence: **the backend MUST run as a single process.** It runs on Flask's built-in threaded server (`app.run(threaded=True)` in [backend/app/main.py](backend/app/main.py)) — a single process with a thread pool, which fits the singleton design. Do not put it behind a multi-worker WSGI server (Gunicorn/uWSGI) without redesigning the singletons (e.g. moving state to Redis/DB); a single Waitress/Gunicorn worker would also work but adds nothing over `app.run` for this LAN appliance.

### Per-camera processing pipeline
For each active camera, `CameraManager.start_camera()` builds and links:

```
RTSP → FFmpegWorker → CircularFrameBuffer → FrameDistributor → consumers
                                                                ├─ AIScheduler (motion-gated YOLOv8)
                                                                └─ recording_manager

Live preview is NOT served from this pipeline — go2rtc (sidecar) talks RTSP to
each camera once and re-exposes WebRTC/RTSP/HLS directly to clients. The
FFmpegWorker pipeline only feeds AI + recording. (MJPEG was fully removed.)
```

- `FFmpegWorker` spawns `ffmpeg` as a subprocess, parses raw frames via stdout, auto-rescales to a target resolution, reconnects with backoff (up to `MAX_RECONNECT = 10`), and has a watchdog (`WATCHDOG_TIMEOUT = 30s`) that marks the camera `FROZEN` if no frames arrive.
- `CircularFrameBuffer` is a small (default 3-frame) thread-safe deque — newest-frame-wins, drops are counted.
- `FrameDistributor` fans frames out to N consumers on the `GlobalExecutor` pool. Consumers register with `needs_copy=True/False` (zero-copy when the consumer copies/serializes immediately).

**Dual-lens cameras** (`Camera.is_dual_lens = True`) take a single side-by-side RTSP stream and split it into two logical streams identified by `stream_id` (`"l1"` / `"l2"`) — go2rtc publishes each lens separately (`cam_X_l1`/`cam_X_l2`). They do **not** get separate buffers or distributors. See `CameraManager.start_dual_lens_camera()` and [backend/app/cameras/dual_lens_splitter.py](backend/app/cameras/dual_lens_splitter.py).

### AI inference
`AIScheduler` ([backend/app/processing/ai/ai_scheduler.py](backend/app/processing/ai/ai_scheduler.py)) runs as a worker thread, not on the distributor callback (fix F1.2 — see comments in the file). Motion detection gates expensive YOLO calls; alerts have per-class cooldowns. Inference goes through `InferenceQueue` + `YLOModelPool`.

Only **one** camera at a time runs YOLO — controlled by the `AI_CAMERA_ID` env var. Do not assume per-camera AI is supported.

### Event bus
Workers (FFmpeg, AI, motion) publish `EventData` to `EventManager.publish(...)`, which dispatches to subscribers on a shared 8-thread pool (`EventManager._executor`). Subscribers include `EventService` (persists to DB), `TelegramNotifier`, `NotificationRouter` (per-user routing rules), and `MetricsCollector`. New event sources should publish through `EventManager`, never call notifiers directly.

### Flask app composition
[backend/app/main.py:create_app()](backend/app/main.py) wires everything:
1. JWT + CORS + rate limiter
2. `db_manager.init_db()` (creates tables)
3. `get_container()` (builds the DI container with all repos/services)
4. Spawns a daemon thread that waits 0.5s, then calls `CameraManager().start_all_active()` — cameras come up asynchronously so the HTTP server is ready immediately.
5. Starts `StorageManager`, `MetricsCollector`, `LiveHLSService`, `ConsistencyChecker`, and a stalled-camera monitor.
6. Registers blueprints via `safe_register()` — **blueprint import failures are logged but non-fatal**. If an API route is missing in production, check the startup log for `"Error registrando '<name>'"` rather than assuming the route doesn't exist.

### Data model & multi-tenancy
[backend/app/database/models.py](backend/app/database/models.py) (SQLAlchemy 2.0, `DeclarativeBase` + `Mapped[...]`):
- `User` (admin/user role) owns `Camera`s. `UserCameraPermission` grants other users access to a specific camera with per-permission flags (view, control PTZ, etc.). Always check permission via `PermissionService` — `owner_id` alone isn't sufficient because shared cameras exist.
- `Event` and `Recording` belong to a `Camera`.
- `MobileDevice` (FCM tokens), `NotificationPreference`, `UserTelegramChat` support the multi-channel notification system.

### Config & env
[backend/app/config.py](backend/app/config.py) (`Settings` singleton) loads `.env` from repo root and exposes everything as attributes. Important knobs:
- `POSTGRES_*` — the active database. The full env template is [.env-example](.env-example) (copy to `.env`); the code always calls `get_database_url()` → `postgresql+psycopg2://...`. SQLite is legacy/unused.
- `MAX_CAMERAS` (default 4), `MAX_CONCURRENT_FFMPEG`, `MAX_AI_INFERENCE_QUEUE` — capacity limits enforced at runtime.
- `GO2RTC_ENABLED` (default **true**) — go2rtc is the only live-streaming layer; `GO2RTC_*` ports/host configure the sidecar.
- `FFMPEG_RESOLUTION_WIDTH/HEIGHT/FPS` — applies to all single-lens cameras.
- `FFMPEG_DUAL_LENS_WIDTH/HEIGHT` — the **raw** stream size before split (e.g. `1280x1440` for two stacked 720p lenses).
- `AI_CAMERA_ID` — single-camera AI selector (see above).

### Desktop client
[desktop_app/src/](desktop_app/src/) is a separate process. [api_client.py](desktop_app/src/services/api_client.py) handles JWT auth + refresh; live preview is played with VLC/libVLC from the go2rtc RTSP restream ([rtsp_video.py](desktop_app/src/ui/components/rtsp_video.py)); `playback_service.py` handles recording playback. Models in `desktop_app/src/models/` mirror — but are **not** the same classes as — the SQLAlchemy models on the backend. Keep API DTOs in sync when changing either side.

## Gotchas

- **Do not run multiple backend processes** against the same data dir — see "process-wide singleton state" above.
- The `env/` directory in the repo root is a checked-in virtualenv. Don't edit anything under it; ignore it for searches.
- `backend/tests/` mixes the real suite (`run_tests.py`) with one-off scripts. When writing new tests, add classes to `run_tests.py` rather than creating new files unless you're extending the structured suite.
- Blueprint registration is best-effort — if you add a new route file, verify it loaded by hitting `/api/v1/health` (returns the `blueprints` list).
- Recordings, snapshots, models (`*.pt`), and the DB are gitignored; the repo contains code only.

## Latency tuning (live preview lag)

The biggest lever for live-preview lag is **not** in this codebase — it's the **GOP / I-frame interval** on the camera itself. FFmpeg cannot decode anything until it receives a keyframe, so a GOP of 50 (typical XiongMai default) = ~3.3s of unavoidable latency at 15fps when (re)connecting; GOP=100 = ~6.6s.

What the code already does for low latency:
- `-probesize 32 -analyzeduration 0` (no 5s warm-up before delivering frames).
- `-fflags nobuffer+flush_packets -flags low_delay -flags2 +fast`.
- `CircularFrameBuffer maxsize=2` (always the most recent frame, no queueing).
- Live preview is served by go2rtc (WebRTC/RTSP/HLS) straight to the client, not re-encoded by the backend.
- Desktop client: VLC `--network-caching=150 --rtsp-tcp --clock-jitter=0` for minimal live latency.
- `_detect_real_resolution` uses `ffprobe` with the same fast flags (was `cv2.VideoCapture` which paid full default analyzeduration).

To diagnose where the delay sits, hit `GET /api/v1/cameras/<id>/latency?stream=main` with the JWT. It returns `frame_age` (capture → send) — if under 500ms, the backend is fluid and the delay is camera-side (GOP) or client/network.

To fix camera-side: log into the camera's web panel, look for "I-frame interval", "GOP length", or "Keyframe interval" in the video encoding settings, set it to ≤30 (= 2s at 15fps) or even 15 (= 1s) for snappier live view. Lower GOP = bigger bitrate; for IP cameras on LAN that's fine.