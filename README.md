# EE475-Smart-Fridge-Manager
Shared Git repository for the EE 475 Embedded Systems Capstone project, supporting collaborative development, version control, and documentation of the Smart Refrigerator Manager system, including embedded software, data processing, and system integration.

## Collision Resolver Workflow
The backend now treats parsed detections as high-level `PUT_IN` / `TAKE_OUT` operations and resolves them into append-only SQLite rows. The `events` table remains the single source of truth for item state, and the latest row for each `item_instance_id` determines whether that specific instance is currently `IN_FRIDGE`, `PENDING`, or `REMOVED`.

### Tables
- `events`: append-only item instance history. New columns:
  `item_instance_id`, `pending_type`, `case_id`, `updated_at_utc`
- `collision_cases`: open/resolved UI-facing collision cases
- `collision_actions`: UI-written confirmation actions for the backend to consume

### Ambiguous identical-item removals
When `TAKE_OUT(item_name)` arrives and multiple current `IN_FRIDGE` instances exist:
- The backend picks the FIFO candidate (earliest put-in time).
- If the put-in time span across the current identical items is `<= 60s`, it auto-resolves and appends a `REMOVED` row for the FIFO instance.
- Otherwise it appends a `PENDING` row for the FIFO instance, creates or updates one open `collision_cases` row for `(item_name, pending_type)`, and merges later ambiguous removals into that same case.

### UI contract
The UI should read:
- `events` for inventory display as before, filtering to latest-instance state `IN_FRIDGE`
- `collision_cases` where `status='OPEN'` to render pending confirmation prompts

The UI should write:
- one row into `collision_actions` with:
  `action_id`, `case_id`, `created_at_utc`, `remove_item_ids_json`, optional `note`, and `status='NEW'`

`remove_item_ids_json` must be a JSON array of `item_instance_id` values the user confirms should become `REMOVED`. The backend consumer will:
- append `REMOVED` rows for the selected instances
- append `IN_FRIDGE` rows for pending instances in the same case that were not selected
- mark the case `RESOLVED`
- mark the action `PROCESSED`

### Developer usage
- Schema bootstrap: `/Users/liluoqi/Documents/Luoqi_github/EE475-Smart-Fridge-Manager/scr/db/schemas/manifest.json`
- Resolver implementation: `/Users/liluoqi/Documents/Luoqi_github/EE475-Smart-Fridge-Manager/scr/data_detection_layer/collision_resolver.py`
- Session pipeline integration: `/Users/liluoqi/Documents/Luoqi_github/EE475-Smart-Fridge-Manager/scr/data_detection_layer/detection_runner.py`
- Workflow test: `python3 -m unittest tests.test_collision_workflow`
