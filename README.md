# Smart Fridge Manager

Smart Fridge Manager is a multi-layer refrigerator inventory system that combines embedded door events, camera-based object detection, backend event resolution, and a lightweight web dashboard.

The repository is organized around the live runtime path and the main system entrypoint:

```bash
scripts/run_system.sh
```

## Overview

When the fridge door opens and closes, the system:

1. receives door and environment events from the edge device
2. captures session frames from the camera
3. runs object detection on the captured session
4. converts detections into inventory events
5. resolves ambiguous removals through a collision workflow
6. serves the current inventory and collision state in a web UI

## Architecture

The codebase is split into four functional layers under `src/`:

- `src/edge/`
  Edge-side hardware integration for door events, sensor readings, and camera capture.
- `src/detection/`
  AI inference layer that listens for closed sessions and produces `vision.json`.
- `src/data_detection_layer/`
  Backend logic that validates vision output, infers fridge operations, updates SQLite, and processes collision actions.
- `src/web/`
  Flask server and static frontend for inventory, environment, and collision views.

## Repository Layout

```text
.
├── data/
│   ├── db/
│   │   └── fridge.db
│   └── sessions/
├── scripts/
│   ├── run_system.sh
│   ├── start_system.sh
│   ├── clear_collision_tables.py
│   ├── clear_table.py
│   ├── fridge_cli_common.py
│   ├── print_collision_tables.py
│   ├── print_table.py
│   ├── put_in_items.py
│   ├── send_session_socket.py
│   └── take_out_item.py
├── src/
│   ├── edge/
│   ├── detection/
│   ├── data_detection_layer/
│   └── web/
├── requirements.txt
└── README.md
```

## Runtime Components

`scripts/run_system.sh` starts the full system in this order:

- clears runtime tables in `data/db/fridge.db`
- starts the edge bridge
- starts the detection socket listener
- starts the backend detection runner
- starts the Flask web server

The script expects runtime data to live in:

- `data/db/fridge.db`
- `data/sessions/`

## Requirements

The project uses Python 3 and depends on:

- `Flask` for the web server
- `opencv-python` for camera capture and image handling
- `pyserial` for edge serial communication
- `ultralytics` and ONNX runtime dependencies for detection
- `sqlite3` from the Python standard library for storage

Install dependencies with:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

Run the full system:

```bash
./scripts/run_system.sh
```

Legacy compatibility entrypoint:

```bash
./scripts/start_system.sh
```

The web server is started from:

```bash
src/web/server.py
```

## Layer Entry Points

Run individual services directly if needed:

```bash
python3 src/edge/door_bridge.py
python3 src/detection/model/socket_listener.py
python3 src/data_detection_layer/detection_runner.py
python3 src/web/server.py
```

## Data Flow

1. `src/edge/door_bridge.py` listens for serial door events and sensor updates.
2. Camera frames are written to `data/sessions/<session_id>/frames/`.
3. `src/detection/model/socket_listener.py` receives `SESSION_CLOSED` over a Unix socket.
4. `src/detection/model/inference.py` writes `data/sessions/<session_id>/vision.json`.
5. `src/data_detection_layer/detection_runner.py` validates the session output and updates the database.
6. `src/web/server.py` exposes APIs and serves the frontend.

## Database and Collision Workflow

The backend stores inventory history and collision state in SQLite.

Primary tables:

- `events`
- `collision_cases`
- `collision_actions`
- `environment`

Schema files are stored in:

```text
src/data_detection_layer/db/schemas/
```

If multiple identical items could have been removed, the backend creates an open collision case instead of auto-removing all candidates. The frontend can then submit a confirmation action, which the backend consumes and resolves.

Run the backend collision worker only:

```bash
python3 src/data_detection_layer/detection_runner.py --actions-only
```

## Useful Scripts

- `scripts/clear_collision_tables.py`
  Clears runtime event and collision tables.
- `scripts/clear_table.py`
  Clears a specific database table.
- `scripts/print_table.py`
  Prints a specific database table.
- `scripts/print_collision_tables.py`
  Prints collision-related tables.
- `scripts/put_in_items.py`
  Inserts inventory events through backend logic.
- `scripts/take_out_item.py`
  Removes inventory through backend logic.
- `scripts/send_session_socket.py`
  Sends a manual session message to the detection socket.

## Hardware Notes

The edge layer currently assumes:

- a serial-connected controller sending fridge event lines
- a USB camera available to OpenCV
- Unix domain sockets under `/tmp`

The Arduino sketch for the edge device is included here:

```text
src/edge/door_session_stm32.ino
```

## Publish Notes

This repository has been cleaned around the active runtime system:

- tests and demo assets were removed
- session output is not committed
- training artifacts are not part of the runtime path
- source layout reflects the production layers instead of historical folders

If you publish this project, make sure the target environment has the required Python packages, camera access, serial access, and the model file at:

```text
src/detection/model/fruit_model_data3.onnx
```
