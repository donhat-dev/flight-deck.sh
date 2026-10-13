## Sẽ implement gì

### Addon `flight_deck` — bắt buộc, chạy được trên Odoo 19 CE

| Loại | File | Thành phần mới hoặc sửa | Trách nhiệm |
|---|---|---|---|
| Manifest | `odoo/flight_deck/__manifest__.py` | tăng version lên `19.0.1.1.0`; thêm dependency `queue_job`; thêm XML, backend asset và unit-test asset | Nạp model source, queue records, view realtime và test JavaScript; không phụ thuộc `ai` |
| Model | `odoo/flight_deck/models/transcript_source.py` | class `FlightdeckTranscriptSource`, model `flightdeck.transcript.source` | Một cursor PostgreSQL bền vững cho mỗi transcript root/subagent; discovery, dirty selection, bounded ingest, backfill và reconciliation |
| Pure parser | `odoo/flight_deck/models/transcript_reader.py` | constants `HOT_READ_BYTES`, `BACKFILL_READ_BYTES`; functions `classify_transcript_path()`, `parse_usage_line()`, `read_usage_tail()` | Phân loại path và đọc usage từ các dòng JSONL đã kết thúc bằng newline, không dùng ORM |
| Watcher | `odoo/flight_deck/models/follow_watcher.py` | sửa `_Watcher`, `Handler`; thêm per-path debounce và maximum wait | Watch `/mnt/claude-projects` recursive, ghi đúng path bị dirty vào PostgreSQL rồi trigger cron; vẫn wake runner spool |
| Session | `odoo/flight_deck/models/session.py` | fields `session_type`, `unpriced_message_count`, `usage_source_ids`; methods `_bus_list_changed()`, `_usage_totals_values()`, `_job_repair_usage_totals()`, `action_enqueue_usage_repair()`, `action_open_usage_sources()`; sửa `create()`, `write()`, `_cron_follow()`, `action_recompute_totals()` | Lưu tổng cost/token trên một root session, phát targeted bus event, giữ transcript display follow hiện tại |
| Usage ledger | `odoo/flight_deck/models/message.py` | fields `usage_source`, `transcript_source_id`, `agent_id`, `provider`; methods `_prepare_usage_values()`, `_insert_usage_rows()`, `_record_usage()` | Một row immutable cho một billed provider response; `ON CONFLICT (uuid) DO NOTHING`; chỉ cộng totals từ row thực sự insert |
| Pricing | `odoo/flight_deck/models/model_price.py` | methods `action_enqueue_reprice()`, `_job_reprice_messages()` | Reprice ledger theo longest-prefix và timestamp trong serialized queue; sau đó repair session totals |
| Imports | `odoo/flight_deck/models/__init__.py` | import `transcript_source` | Đăng ký model mới trước khi cron/view dùng |
| Queue data | `odoo/flight_deck/data/usage_queue_data.xml` | channel `queue_channel_flightdeck_usage`; 4 `queue.job.function` records | Chạy backfill, reconcile, reprice và aggregate repair trong `root.flightdeck_usage` capacity 1 |
| Cron data | `odoo/flight_deck/data/follow_cron.xml` | giữ `cron_follow`; thêm `cron_discover_transcripts`, `cron_reconcile_usage` | Hot dirty ingest không scan toàn corpus; discovery mỗi phút là recovery; daily cron chỉ enqueue reconciliation |
| Session views | `odoo/flight_deck/views/session_views.xml` | `js_class="flightdeck_session_list"`; field/filter/buttons cho type, source và unpriced rows | List tự reload current page; form mở source và repair usage |
| Source views | `odoo/flight_deck/views/transcript_source_views.xml` | list/form/search + action `action_transcript_source` | Manager xem cursor, state, agent, dirty/error và enqueue backfill/reconcile |
| Usage views | `odoo/flight_deck/views/message_views.xml` | optional fields/filter `usage_source`, `transcript_source_id`, `agent_id`, `provider`, `priced` | Drill-down cost theo nguồn/root/subagent/provider |
| Menus | `odoo/flight_deck/views/menus.xml` | menu `menu_transcript_source` dưới Configuration | Mở source registry cho manager |
| ACL | `odoo/flight_deck/security/ir.model.access.csv` | `access_transcript_source_user`, `access_transcript_source_manager` | User read-only; manager full control, cùng pattern với các model hiện tại |
| Migration | `odoo/flight_deck/migrations/19.0.1.1.0/post-migration.py` | function `migrate(env, version)` | Gán origin/type cho dữ liệu cũ và tính `unpriced_message_count`; không scan filesystem và không chạy backfill trong upgrade transaction |
| List JS | `odoo/flight_deck/static/src/views/session_list/session_list.js` | `FlightDeckSessionListController`, `flightDeckSessionListView` | Subscribe channel `flightdeck.session.list`, coalesce 150 ms, chỉ `model.root.load()` current page khi event có thể đổi trang |
| Python tests | `odoo/flight_deck/tests/*.py` | parser, discovery, atomic ingest, jobs, bus tests | Chứng minh cursor, UUID idempotency, child rollup, pricing và bounded work |
| JS tests | `odoo/flight_deck/static/tests/session_list.test.js` | mock bus + list model tests | Chứng minh subscribe, filtering, coalescing và cleanup |

### Addon `flight_deck_ai` — tùy chọn, chỉ cài khi Enterprise `ai` có mặt

| Loại | File | Thành phần mới hoặc sửa | Trách nhiệm |
|---|---|---|---|
| Addon | `odoo/flight_deck_ai/__manifest__.py` | dependency đúng `flight_deck`, `ai`; version `19.0.1.0.0`; `post_load="post_load"` | Giữ core CE hoàn toàn không import hoặc phụ thuộc Enterprise AI |
| Hook | `odoo/flight_deck_ai/hooks.py` | `post_load()` | Cài monkey patch một lần, có guard, vào đúng hai provider helper |
| Adapter | `odoo/flight_deck_ai/services/llm_usage.py` | `_patch_helpers()`, `_openai_helper()`, `_google_helper()`, `record_provider_usage()` | Lấy normalized usage của từng provider response và commit ledger bằng cursor độc lập |
| AI agent extension | `odoo/flight_deck_ai/models/ai_agent.py` | inherit `ai.agent`; override `_generate_response()` | Đưa `flightdeck_ai_agent_id` và một `flightdeck_ai_run_id` ổn định qua toàn bộ tool loop |
| Session extension | `odoo/flight_deck_ai/models/session.py` | fields `ai_agent_id`, `ai_channel_id`, `ai_run_id`; `_job_link_ai_records()` | Liên kết session native với agent/channel sau khi outer transaction commit |
| Queue data | `odoo/flight_deck_ai/data/usage_queue_data.xml` | `job_function_session_link_ai_records` | Resolve Many2one bằng job eta 5 giây mà không giữ provider request |
| Views | `odoo/flight_deck_ai/views/session_views.xml` | fields/filter native AI | Hiện agent, Discuss channel và run ID trên session form/search |
| Tests | `odoo/flight_deck_ai/tests/test_native_usage.py` | OpenAI, Google, multi-call, idempotent patch, channel/run mapping, rollback-survival integration | Một ledger row cho mỗi response thật; core vẫn cài được khi không có `ai` |

### Runtime `docker/odoo19`

| File | Thay đổi chính |
|---|---|
| `docker/odoo19/Dockerfile` | clone OCA `queue` commit `9b68673fa5e540771f55f8535fb7ce119dbd075a` vào `/opt/oca/queue`; cài `openupgradelib==3.13.7` |
| `docker/odoo19/odoo.conf` | thêm OCA addons path, `server_wide_modules = base,web,queue_job`, `workers = 4`, `max_cron_threads = 2`, `proxy_mode = True`, evented port 8072, queue channels `root:4,root.flightdeck_usage:1` |
| `docker/odoo19/nginx.conf` | HTTP tới `odoo:8069`; `/websocket` và `/longpolling` tới `odoo:8072`; giữ timeout dài |
| `docker/odoo19/compose.yml` | thêm service `nginx`, chuyển host mapping `8029` từ Odoo sang Nginx, thêm internal network; Odoo vẫn cùng container với `fd-runner` |
| `docker/odoo19/README.md` | build/update/install/test commands, websocket check và queue check |

`runner_cost` được giữ làm số chẩn đoán từ runtime. `total_cost` vẫn là số dùng trong Logbook và chỉ lấy từ immutable usage ledger; không cộng hoặc đồng bộ từ `runner_cost`.

# Odoo Realtime Session Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task by task. Every task uses checkbox syntax. Do not commit unless the user gives explicit authorization.

**Goal:** Tự động discover Claude Code root/subagent sessions, cập nhật Logbook gần realtime và tính estimated cost từ transcript usage; tùy chọn ghi usage của native Odoo AI theo cùng ledger.

**Architecture:** PostgreSQL giữ một cursor cho mỗi JSONL source. Watchdog chỉ debounce theo path, đánh dấu đúng source dirty và trigger bounded cron; cron đọc phần byte mới, insert immutable usage rows, cộng đúng một root session và phát targeted bus notification trong cùng transaction. Discovery recovery, large catch-up, repricing, reconciliation và aggregate repair chạy qua OCA `queue_job`, không nằm trên realtime path.

**Tech Stack:** Odoo 19 CE/Enterprise, Python 3, PostgreSQL, watchdog, Odoo bus/websocket, OWL list view registry, OCA `queue_job` 19.0.2.0.3, Docker Compose, Nginx.

**Spec:** `docs/2026-09-14-agent-events-and-session-control.md`

## Global Constraints

- Core `flight_deck` phải cài và chạy trên Odoo 19 CE khi Enterprise `ai` không có trong addons path.
- Transcript JSONL là durable source cho Claude Code usage; hook event không phải billing source.
- `flightdeck.message.uuid` là billing identity. Byte offset chỉ là read position.
- Child transcript được parse riêng nhưng mọi token/cost cộng vào root `flightdeck.session`.
- Usage insert, session totals update và source cursor advance phải commit hoặc rollback cùng nhau.
- Chỉ totals của các rows trả về từ `INSERT ... ON CONFLICT DO NOTHING RETURNING` được cộng.
- Dòng cuối chưa có newline không được parse hoặc advance cursor.
- Hot pass tối đa 32 sources, 2 giây tổng, 1 MiB/source và 200 usage rows/source.
- Watch debounce là 200 ms/path, maximum wait 1 giây/path.
- Hot path không scan toàn corpus, không chạy dashboard aggregate và không gọi `read_group`.
- Bus payload chỉ chứa IDs, changed field names và `created`; client lấy dữ liệu qua ORM/ACL.
- Credentials chỉ nằm trong gitignored `docker/odoo19/.env`; Odoo không quản lý Claude subscription credential.
- Odoo chỉ đọc `~/.claude/projects` qua ACL đã cấp; không tạo hoặc sửa hook configuration trên host.
- Ledger cũ chỉ đọc bằng `fdreader`.
- Không dùng `docker compose down -v`.
- Không reset, clean hoặc overwrite worktree đang có thay đổi. Không xóa `odoo/.inotify_probe`.
- Code, identifier, comment, log message và UI copy là English.
- Mỗi command thay đổi database trong plan dùng database riêng `flightdeck_odoo_test` hoặc database FlightDeck đã được người dùng chỉ định; không chạy nhầm vào NAKIVO databases.
- Mọi checkpoint commit trong workflow bị dừng cho đến khi user cho phép rõ ràng.

## Data model và interface cố định

### `flightdeck.transcript.source`

```python
class FlightdeckTranscriptSource(models.Model):
    _name = "flightdeck.transcript.source"
    _description = "FlightDeck transcript source"
    _order = "dirty desc, dirty_at, id"
    _rec_name = "path"

    path = fields.Char(required=True, index=True)
    source_type = fields.Selection(
        [("root", "Root session"), ("subagent", "Subagent")],
        required=True,
        index=True,
    )
    session_ref_id = fields.Many2one(
        "flightdeck.session", required=True, index=True, ondelete="cascade"
    )
    agent_id = fields.Char(index=True)
    cursor_bytes = fields.Float("Read bytes", digits=(20, 0), default=0)
    cursor_lines = fields.Integer("Read lines", default=0)
    file_size = fields.Float("File size", digits=(20, 0), default=0)
    file_device = fields.Char()
    file_inode = fields.Char()
    dirty = fields.Boolean(default=True, index=True)
    dirty_at = fields.Datetime(index=True)
    last_seen_at = fields.Datetime()
    last_read_at = fields.Datetime()
    state = fields.Selection(
        [
            ("new", "New"),
            ("ready", "Ready"),
            ("backfill", "Backfill queued"),
            ("missing", "Missing"),
            ("error", "Error"),
        ],
        default="new",
        required=True,
        index=True,
    )
    error = fields.Text()
    failure_count = fields.Integer(default=0)
    active = fields.Boolean(default=True)

    _path_uniq = models.Constraint("unique(path)", "Transcript source already exists.")
```

`cursor_bytes` và `file_size` là `Float(digits=(20, 0))` thay vì Odoo `Integer`, vì Odoo integer column là `int4`; IEEE-754 vẫn biểu diễn chính xác mọi byte offset thực tế tới `2**53`.

### Pure parser contracts

```python
HOT_READ_BYTES = 1 << 20
BACKFILL_READ_BYTES = 64 << 20
HOT_USAGE_ROWS = 200


def classify_transcript_path(path, root=TRANSCRIPT_ROOT):
    """Return root_session_id, source_type and agent_id, or None."""


def parse_usage_line(raw):
    """Return one normalized assistant usage dict, or None."""


def read_usage_tail(path, offset, max_bytes, max_rows=None):
    """Return rows, consumed_bytes, complete_lines and has_more."""
```

`read_usage_tail()` reads at most `max_bytes + 1` bytes, cuts at the final newline inside the bounded buffer, and advances only through that newline. If no newline is present, it returns zero consumed bytes. `has_more=True` means complete or partial bytes remain after the returned cursor.

### Realtime constants and bus payload

```python
TRANSCRIPT_ROOT = "/mnt/claude-projects"
HOT_SOURCE_LIMIT = 32
HOT_TIME_BUDGET_SECONDS = 2.0
DEBOUNCE_SECONDS = 0.2
MAX_WAIT_SECONDS = 1.0
SESSION_LIST_CHANNEL = "flightdeck.session.list"
SESSION_LIST_EVENT = "flightdeck.session/list_changed"
LIST_RELOAD_DELAY_MS = 150
```

```json
{
  "session_ids": [228],
  "fields": ["write_date", "message_count", "input_tokens", "output_tokens", "cache_read", "cache_create", "total_cost", "unpriced_message_count"],
  "created": false
}
```

### Native Odoo AI identity and rollback policy

- Channel chat session ID: `odoo-ai:<database_uuid>:channel:<channel_id>:agent:<ai_agent_id>`.
- Direct call session ID: `odoo-ai:<database_uuid>:run:<run_uuid>:agent:<ai_agent_id>`.
- `_generate_response()` generates one `run_uuid` only when context does not already contain it. Every provider call in the successive tool loop inherits that value.
- Each successful provider helper return receives a new billing UUID `odoo-ai:<uuid4>`. One helper return equals one billed provider response.
- The adapter writes usage with `Registry(dbname).cursor()` and commits that cursor before returning to Enterprise `ai`. Therefore provider usage survives a rollback in the request that invoked the provider.
- If a newly created Discuss channel is not visible to the independent cursor yet, the session keeps the channel ID in `session_id`; a queue job with eta 5 seconds fills `ai_channel_id` after the outer transaction commits. If it rolls back, usage remains correctly recorded without a channel relation.
- A process crash between provider return and the independent insert is the only remaining uncaptured interval. The wrapper performs no other work between those two operations.
- The adapter never changes provider input/output and logs capture failures without replacing the AI response. Failing the user request would spend a second provider call on retry while still not reconstructing the first response.

---

### Task 1: Add Queue Runtime and Websocket Proxy

**Files:**
- Modify: `docker/odoo19/Dockerfile`
- Modify: `docker/odoo19/odoo.conf`
- Modify: `docker/odoo19/compose.yml`
- Create: `docker/odoo19/nginx.conf`
- Modify: `docker/odoo19/README.md`

**Interfaces:**
- Produces: addon path `/opt/oca/queue`; channel runner `root.flightdeck_usage`; HTTP endpoint on host `8029`; evented Odoo endpoint reachable through the same host port.
- Preserves: `fd-runner` and Odoo in the same `odoo` container; Caddy continues proxying `127.0.0.1:8029` and needs no change.

- [ ] **Step 1: Pin the OCA queue source and Python dependency in the image**

Add after the existing apt install:

```dockerfile
ARG OCA_QUEUE_COMMIT=9b68673fa5e540771f55f8535fb7ce119dbd075a
RUN git clone https://github.com/OCA/queue.git /opt/oca/queue \
    && git -C /opt/oca/queue checkout "$OCA_QUEUE_COMMIT" \
    && pip3 install --no-cache-dir openupgradelib==3.13.7 \
    && test "$(git -C /opt/oca/queue rev-parse HEAD)" = "$OCA_QUEUE_COMMIT"
```

Keep credentials out of the image. Do not copy another workspace checkout into this image.

- [ ] **Step 2: Configure Odoo workers and queue runner**

Set these exact values in `odoo.conf`:

```ini
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/opt/oca/queue,/mnt/flightdeck-addons
server_wide_modules = base,web,queue_job
workers = 4
max_cron_threads = 2
proxy_mode = True
gevent_port = 8072

[queue_job]
channels = root:4,root.flightdeck_usage:1
```

Keep the existing database, timeout, log and dev-mode settings.

- [ ] **Step 3: Add the internal Nginx proxy**

Create `nginx.conf`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}

upstream flightdeck_odoo_http {
    server odoo:8069;
}

upstream flightdeck_odoo_evented {
    server odoo:8072;
}

server {
    listen 8029;
    client_max_body_size 100m;

    location /websocket {
        proxy_pass http://flightdeck_odoo_evented;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 720s;
    }

    location /longpolling {
        proxy_pass http://flightdeck_odoo_evented;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 720s;
    }

    location / {
        proxy_pass http://flightdeck_odoo_http;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1200s;
    }
}
```

- [ ] **Step 4: Put Odoo and Nginx on an internal network**

In `compose.yml`:

- remove `8029:8069` from `odoo`;
- add `expose: ["8069", "8072"]` to `odoo`;
- attach `odoo` to both `pgnet` and `flightdeck`;
- add the service below;
- declare `flightdeck` as an internal bridge network.

```yaml
  nginx:
    image: nginx:1.27-alpine
    container_name: flightdeck-odoo19-proxy
    restart: unless-stopped
    depends_on:
      - odoo
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "8029:8029"
    networks:
      - flightdeck

networks:
  pgnet:
    external: true
  flightdeck:
    internal: true
```

- [ ] **Step 5: Build and verify the runtime before installing core changes**

Run:

```bash
cd /home/nathando/Documents/Projects/flight-deck.sh/docker/odoo19
docker compose config --quiet
docker compose build odoo
docker compose up -d odoo nginx
docker exec flightdeck-odoo19 python3 -c "import openupgradelib; print(openupgradelib.__version__)"
docker exec flightdeck-odoo19 git -C /opt/oca/queue rev-parse HEAD
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8029/web/login
```

Expected:

```text
3.13.7
9b68673fa5e540771f55f8535fb7ce119dbd075a
200
```

Run `docker compose ps` and confirm both containers are healthy/running. Do not use `down -v`.

### Task 2: Add Core Schema, Migration, ACL, and Source Views

**Files:**
- Modify: `odoo/flight_deck/__manifest__.py`
- Modify: `odoo/flight_deck/models/__init__.py`
- Create: `odoo/flight_deck/models/transcript_source.py`
- Modify: `odoo/flight_deck/models/session.py`
- Modify: `odoo/flight_deck/models/message.py`
- Create: `odoo/flight_deck/views/transcript_source_views.xml`
- Modify: `odoo/flight_deck/views/session_views.xml`
- Modify: `odoo/flight_deck/views/message_views.xml`
- Modify: `odoo/flight_deck/views/menus.xml`
- Modify: `odoo/flight_deck/security/ir.model.access.csv`
- Create: `odoo/flight_deck/migrations/19.0.1.1.0/post-migration.py`
- Create: `odoo/flight_deck/tests/__init__.py`
- Create: `odoo/flight_deck/tests/test_usage_schema.py`

**Interfaces:**
- Produces: exact schema in “Data model và interface cố định”.
- Consumes: `queue_job` loaded by Task 1.
- Does not yet read any transcript or schedule any job.

- [ ] **Step 1: Write schema tests**

Use `TransactionCase` and assert:

```python
from odoo.tests.common import TransactionCase


class TestUsageSchema(TransactionCase):
    def test_source_path_is_unique(self):
        vals = {
            "path": "/mnt/claude-projects/project/session.jsonl",
            "source_type": "root",
            "session_ref_id": self.env["flightdeck.session"].create({
                "session_id": "00000000-0000-0000-0000-000000000001",
                "name": "Session 00000000",
            }).id,
        }
        self.env["flightdeck.transcript.source"].create(vals)
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env["flightdeck.transcript.source"].create(vals)

    def test_existing_records_default_to_claude_code(self):
        session = self.env["flightdeck.session"].create({
            "session_id": "00000000-0000-0000-0000-000000000002",
            "name": "Session 00000000",
        })
        message = self.env["flightdeck.message"].create({
            "uuid": "schema-message",
            "session_ref_id": session.id,
        })
        self.assertEqual(session.session_type, "claude_code")
        self.assertEqual(message.usage_source, "claude_code")
```

Use `IntegrityError` rather than broad `Exception` in the final test implementation.

- [ ] **Step 2: Run the focused test and verify the missing-model failure**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -i flight_deck --test-enable \
  --test-tags /flight_deck:TestUsageSchema --stop-after-init
```

Expected before implementation: failure naming `flightdeck.transcript.source` or the missing fields.

- [ ] **Step 3: Add model fields and the source model**

Add the exact source fields above. Add to `flightdeck.session`:

```python
session_type = fields.Selection(
    [("claude_code", "Claude Code"), ("odoo_ai", "Odoo AI")],
    default="claude_code",
    required=True,
    index=True,
)
usage_source_ids = fields.One2many(
    "flightdeck.transcript.source", "session_ref_id", string="Transcript sources"
)
unpriced_message_count = fields.Integer("Unpriced rows")
```

Add to `flightdeck.message`:

```python
usage_source = fields.Selection(
    [("claude_code", "Claude Code"), ("odoo_ai", "Odoo AI")],
    default="claude_code",
    required=True,
    index=True,
)
transcript_source_id = fields.Many2one(
    "flightdeck.transcript.source", index=True, ondelete="set null"
)
agent_id = fields.Char(index=True)
provider = fields.Char(index=True)
```

Import `transcript_source` from `models/__init__.py` after `session` and before `message`.

- [ ] **Step 4: Add migration normalization only**

Create `post-migration.py`:

```python
def migrate(env, version):
    env.cr.execute("""
        UPDATE flightdeck_session
           SET session_type = 'claude_code'
         WHERE session_type IS NULL
    """)
    env.cr.execute("""
        UPDATE flightdeck_message
           SET usage_source = 'claude_code'
         WHERE usage_source IS NULL
    """)
    env.cr.execute("""
        UPDATE flightdeck_session s
           SET unpriced_message_count = counts.amount
          FROM (
                SELECT session_ref_id, count(*) AS amount
                  FROM flightdeck_message
                 WHERE NOT priced
                 GROUP BY session_ref_id
               ) counts
         WHERE s.id = counts.session_ref_id
    """)
```

No filesystem call, queue call, bus notification or aggregate rebuild belongs in this migration.

- [ ] **Step 5: Add views and ACLs**

Create manager-facing source list with these columns: `dirty`, `state`, `source_type`, `session_ref_id`, `agent_id`, `cursor_bytes`, `file_size`, `last_seen_at`, `last_read_at`, `failure_count`. Form shows `path`, inode/device and full `error`.

Add ACL rows:

```csv
access_transcript_source_user,transcript.source.user,model_flightdeck_transcript_source,group_user,1,0,0,0
access_transcript_source_manager,transcript.source.manager,model_flightdeck_transcript_source,group_manager,1,1,1,1
```

Add `action_transcript_source` and `menu_transcript_source` under `menu_config`, restricted to `flight_deck.group_manager`.

- [ ] **Step 6: Update manifest and run schema tests**

Set:

```python
"version": "19.0.1.1.0",
"depends": ["base", "web", "mail", "bus", "queue_job"],
```

Load `data/usage_queue_data.xml` only after Task 6 creates it; until then, omit that manifest entry. Load `views/transcript_source_views.xml` before `views/menus.xml`.

Run the focused test command again. Expected: `0 failed, 0 error` for `TestUsageSchema`.

### Task 3: Parse Paths and Claude Usage Without ORM

**Files:**
- Modify: `odoo/flight_deck/models/transcript_reader.py`
- Create: `odoo/flight_deck/tests/fixtures/root_usage.jsonl`
- Create: `odoo/flight_deck/tests/fixtures/subagent_usage.jsonl`
- Create: `odoo/flight_deck/tests/test_transcript_usage.py`

**Interfaces:**
- Produces: `classify_transcript_path()`, `parse_usage_line()`, `read_usage_tail()` with the contracts above.
- Consumes: no Odoo environment; tests can call the functions directly.

- [ ] **Step 1: Write path classification tests**

Cover these exact inputs:

```python
ROOT = "/mnt/claude-projects"

self.assertEqual(
    transcript_reader.classify_transcript_path(
        f"{ROOT}/-home-user-project/11111111-1111-1111-1111-111111111111.jsonl"
    ),
    {
        "root_session_id": "11111111-1111-1111-1111-111111111111",
        "source_type": "root",
        "agent_id": False,
    },
)
self.assertEqual(
    transcript_reader.classify_transcript_path(
        f"{ROOT}/-home-user-project/11111111-1111-1111-1111-111111111111/"
        "subagents/agent-a42.jsonl"
    ),
    {
        "root_session_id": "11111111-1111-1111-1111-111111111111",
        "source_type": "subagent",
        "agent_id": "a42",
    },
)
self.assertIsNone(transcript_reader.classify_transcript_path(f"{ROOT}/x/random.jsonl"))
self.assertIsNone(transcript_reader.classify_transcript_path("/tmp/session.jsonl"))
```

Use `uuid.UUID()` to validate the root ID and `os.path.realpath()` plus `os.path.commonpath()` to reject paths outside the mount.

- [ ] **Step 2: Write normalized usage tests**

Fixture assistant usage must contain:

```json
{"type":"assistant","uuid":"usage-1","sessionId":"11111111-1111-1111-1111-111111111111","cwd":"/home/user/project","timestamp":"2026-09-14T10:00:00.000Z","message":{"model":"claude-opus-5","usage":{"input_tokens":100,"cache_read_input_tokens":80,"cache_creation":{"ephemeral_5m_input_tokens":20,"ephemeral_1h_input_tokens":10},"output_tokens":30,"service_tier":"standard"},"content":[{"type":"text","text":"done"}]}}
```

Expected normalized dict:

```python
{
    "uuid": "usage-1",
    "session_id": "11111111-1111-1111-1111-111111111111",
    "project": "/home/user/project",
    "model": "claude-opus-5",
    "ts": "2026-09-14T10:00:00.000+00:00",
    "input_tokens": 100,
    "cache_read": 80,
    "cache_create_5m": 20,
    "cache_create_1h": 10,
    "output_tokens": 30,
    "service_tier": "standard",
}
```

Also assert user rows, assistant rows without usage, malformed JSON, NUL/Unicode failures and rows without UUID return `None`.

- [ ] **Step 3: Write bounded-tail and torn-line tests**

Tests must prove:

1. a complete first line and partial second line returns only the first line;
2. `consumed_bytes` stops after the first newline;
3. appending the rest plus newline makes the second row visible from the same offset;
4. a small byte limit does not read a complete following line;
5. `max_rows=1` returns one usage row but still advances through non-usage complete lines before it;
6. `has_more` is true when complete or partial bytes remain.

- [ ] **Step 4: Run tests and verify they fail before parser code exists**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -u flight_deck --test-enable \
  --test-tags /flight_deck:TestTranscriptUsage --stop-after-init
```

Expected before implementation: missing attribute failures for the three contracts.

- [ ] **Step 5: Implement path classification and usage parsing**

Keep existing `parse_line()` unchanged for transcript display. Add a separate usage parser:

```python
def parse_usage_line(raw):
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if obj.get("type") != "assistant" or not obj.get("uuid"):
        return None
    message = obj.get("message") or {}
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    cache_creation = usage.get("cache_creation") or {}
    return {
        "uuid": obj["uuid"],
        "session_id": obj.get("sessionId"),
        "project": obj.get("cwd"),
        "model": message.get("model"),
        "ts": (obj.get("timestamp") or "").replace("Z", "+00:00") or None,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_create_5m": int(cache_creation.get("ephemeral_5m_input_tokens") or 0),
        "cache_create_1h": int(cache_creation.get("ephemeral_1h_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "service_tier": usage.get("service_tier"),
    }
```

Guard integer conversion with a small `_token_count()` helper that returns zero for negative, boolean, non-numeric and overflow-like values; token counts must be non-negative integers.

- [ ] **Step 6: Implement bounded read and rerun tests**

Read `max_bytes + 1`, find newline boundaries, and stop before the first line crossing the byte boundary. `max_rows` limits normalized usage rows, not raw JSONL lines. Expected: every `TestTranscriptUsage` test passes.

### Task 4: Register Root and Subagent Sources

**Files:**
- Modify: `odoo/flight_deck/models/transcript_source.py`
- Create: `odoo/flight_deck/tests/test_transcript_source.py`

**Interfaces:**
- Consumes: `classify_transcript_path(path)` from Task 3.
- Produces:

```python
@api.model
def _register_paths(self, paths):
    """Create or mark exact valid sources dirty and return their records."""

@api.model
def _discover_paths(self):
    """Yield valid root and subagent JSONL paths under TRANSCRIPT_ROOT."""

@api.model
def _cron_discover_transcripts(self):
    """Recover missed watcher events and mark changed files dirty."""
```

- [ ] **Step 1: Write registration tests**

Create temporary root and child files under a patched `TRANSCRIPT_ROOT`. Prove:

- one root path creates one `flightdeck.session` and one root source;
- child registration creates/uses the root session named by its ancestor directory;
- child source stores `agent_id` but no child `flightdeck.session` row;
- repeated registration leaves one source and marks it dirty;
- a root and child source both point to the same root session;
- invalid/outside/missing paths are ignored;
- the fallback session name is `Session <first 8 chars>`;
- `last_seen_at`, `file_size`, `file_device` and `file_inode` are updated from `os.stat()`.

- [ ] **Step 2: Write recovery discovery tests**

Patch `_discover_paths()` to return two exact files. Assert `_cron_discover_transcripts()`:

- creates new sources;
- marks a source dirty only when size, device or inode differs from stored state;
- sets unseen registered sources to `state="missing"` without deleting them;
- never reads file content and never calls a dashboard aggregate.

- [ ] **Step 3: Run and verify focused failures**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -u flight_deck --test-enable \
  --test-tags /flight_deck:TestTranscriptSource --stop-after-init
```

Expected before implementation: missing `_register_paths` and `_cron_discover_transcripts`.

- [ ] **Step 4: Implement race-safe source/session creation**

`_register_paths()` must:

1. normalize and deduplicate paths;
2. classify only valid transcript paths;
3. stat each existing file;
4. find/create root sessions by `session_id`;
5. find/create sources by `path`;
6. catch unique races inside `env.cr.savepoint()` and re-search;
7. set `dirty=True`, `dirty_at=fields.Datetime.now()`, `state="new"` for new sources;
8. never modify the session `follow_*` fields.

Use context `fd_no_list_bus=True` only for a recovery batch if a separate coalesced bus event is sent after all creations. Normal watcher registration must announce each new session immediately.

- [ ] **Step 5: Implement recursive recovery with `os.scandir()`**

Walk only these shapes:

```text
<root>/<project>/<uuid>.jsonl
<root>/<project>/<uuid>/subagents/agent-<agent-id>.jsonl
```

Do not walk arbitrary deeper JSONL paths and do not follow symlinks. Return exact absolute paths. Rerun tests; expected: all source tests pass.

### Task 5: Insert Usage Atomically and Roll Child Cost into Root

**Files:**
- Modify: `odoo/flight_deck/models/message.py`
- Modify: `odoo/flight_deck/models/transcript_source.py`
- Modify: `odoo/flight_deck/models/session.py`
- Create: `odoo/flight_deck/tests/test_usage_ingest.py`

**Interfaces:**
- Consumes: source cursor, normalized usage rows, `flightdeck.model.price.rate_index()`, `match()`, `cost_of()`.
- Produces:

```python
@api.model
def _prepare_usage_values(self, session, rows, *, usage_source,
                          transcript_source=None, agent_id=None, provider=None):
    """Return priced database values for normalized rows."""

@api.model
def _insert_usage_rows(self, vals_list):
    """Insert idempotently and return only inserted aggregate inputs."""

@api.model
def _record_usage(self, session, vals_list):
    """Lock session, insert rows, increment totals and notify in one transaction."""
```

- [ ] **Step 1: Write cost and rollup tests**

Create a root source and child source for one session. Append one priced row to each. Assert exact totals:

```python
self.assertEqual(session.message_count, 2)
self.assertEqual(session.input_tokens, 200)
self.assertEqual(session.output_tokens, 60)
self.assertEqual(session.cache_read, 160)
self.assertEqual(session.cache_create, 60)
self.assertAlmostEqual(session.total_cost, expected_cost, places=6)
```

Assert child message provenance:

```python
self.assertEqual(child_message.transcript_source_id, child_source)
self.assertEqual(child_message.agent_id, "a42")
self.assertEqual(child_message.session_ref_id, root_session)
```

- [ ] **Step 2: Write idempotency and atomicity tests**

Prove:

- ingesting the same UUID twice creates one message and increments once;
- the same UUID copied into another source still increments once globally;
- no matching price creates `priced=False`, `cost=0`, increments `unpriced_message_count`, and still counts tokens;
- an exception injected after insert but before cursor write rolls back message, totals and cursor;
- a torn final line advances none of its bytes;
- source truncation or inode replacement resets its read position to zero, relies on UUID deduplication and schedules backfill rather than doing an unbounded read;
- `start_ts`, `end_ts` and `duration_hours` expand only from newly inserted rows.

- [ ] **Step 3: Run the focused tests and confirm missing behavior**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -u flight_deck --test-enable \
  --test-tags /flight_deck:TestUsageIngest --stop-after-init
```

Expected before implementation: assertions show no ledger rows or unchanged cursor/totals.

- [ ] **Step 4: Implement normalized pricing**

`_prepare_usage_values()` converts timestamps to naive UTC, calculates `total_tokens`, uses one price index per batch and fills all provenance fields. For Claude Code:

```python
{
    "usage_source": "claude_code",
    "transcript_source_id": source.id,
    "agent_id": source.agent_id or False,
    "provider": "anthropic",
}
```

Do not infer usage from parent task notifications or transcript text length.

- [ ] **Step 5: Implement idempotent SQL insert with `RETURNING`**

Use one SQL statement against `flightdeck_message`, including log-access columns:

```sql
INSERT INTO flightdeck_message (
    uuid, session_ref_id, project_id, model, ts, service_tier,
    input_tokens, cache_read, cache_create_5m, cache_create_1h,
    output_tokens, total_tokens, cost, priced, usage_source,
    transcript_source_id, agent_id, provider,
    create_uid, create_date, write_uid, write_date
)
VALUES %s
ON CONFLICT (uuid) DO NOTHING
RETURNING id, session_ref_id, input_tokens, cache_read,
          cache_create_5m, cache_create_1h, output_tokens,
          total_tokens, cost, priced, ts
```

Build the values list with `psycopg2.extras.execute_values(..., fetch=True)`. Never calculate aggregate increments from the input list.

- [ ] **Step 6: Lock the root session and update stored totals once**

Before insert:

```sql
SELECT id FROM flightdeck_session WHERE id = %s FOR UPDATE
```

Sum only returned rows, then call one `session.write()` with all total and date fields. Include `write_date` in the later bus payload. Invalidate message/session caches after raw SQL where needed.

- [ ] **Step 7: Advance one source cursor in the same transaction**

`_ingest_chunk(max_bytes, max_rows)` does this exact order:

```text
lock source row
stat file and detect replacement/truncation
read complete bounded bytes
prepare usage values
lock root session
insert usage rows idempotently
increment root totals from RETURNING rows
write cursor_bytes/cursor_lines/file stat/last_read_at/state
send targeted session bus event when rows were inserted
return has_more
```

No intermediate commit. Caller owns commit/rollback. Rerun focused tests; expected: all pass.

### Task 6: Add Bounded Cron and Deferred Queue Jobs

**Files:**
- Create: `odoo/flight_deck/data/usage_queue_data.xml`
- Modify: `odoo/flight_deck/data/follow_cron.xml`
- Modify: `odoo/flight_deck/__manifest__.py`
- Modify: `odoo/flight_deck/models/transcript_source.py`
- Modify: `odoo/flight_deck/models/model_price.py`
- Modify: `odoo/flight_deck/models/session.py`
- Create: `odoo/flight_deck/tests/test_usage_jobs.py`

**Interfaces:**
- Produces: `_cron_ingest_dirty()`, `_job_backfill()`, `_job_reconcile_all()`, `_cron_enqueue_reconciliation()`, `_job_reprice_messages()`, `_job_repair_usage_totals()`.
- Consumes: atomic chunk and aggregate methods from Task 5.

- [ ] **Step 1: Write dirty-source budget tests**

Assert `_cron_ingest_dirty()`:

- uses SQL `FOR UPDATE SKIP LOCKED` ordered by `dirty_at, id`;
- handles no more than 32 source IDs;
- stops scheduling new work after 2 seconds using `time.monotonic()`;
- commits successful sources separately so one malformed/unreadable source does not roll back earlier sources;
- rolls back a failed source transaction, then records `state="error"`, `failure_count + 1` and a bounded error message in a fresh transaction;
- does not call `_discover_paths()`;
- preserves the existing display-follow and runner polling calls.

- [ ] **Step 2: Write queue scheduling tests**

Use OCA `trap_jobs()` or `mock_with_delay()` and assert exact identity keys:

```text
flightdeck-usage-backfill:<source_id>:<cursor_bytes>
flightdeck-usage-reconcile:<YYYY-MM-DD>
flightdeck-usage-reprice:<price_id>:<write_date_iso>
flightdeck-usage-repair:<session_id>
```

Assert all jobs use `channel="root.flightdeck_usage"`. A hot chunk with `has_more=True` must enqueue backfill instead of immediately reading the next chunk.

- [ ] **Step 3: Write reprice and repair tests**

Prove:

- repricing uses each message timestamp and longest matching model prefix;
- rows remain unpriced if no rate matches;
- repricing queues repair only for sessions whose message values changed;
- repair recomputes `message_count`, token/cache totals, unpriced count, cost and date range from ledger rows;
- repair sends one list event per affected recordset;
- source reconciliation marks replaced/grown files dirty and missing files missing.

- [ ] **Step 4: Run focused tests and verify expected failures**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -u flight_deck --test-enable \
  --test-tags /flight_deck:TestUsageJobs --stop-after-init
```

Expected before implementation: missing queue methods and no trapped jobs.

- [ ] **Step 5: Implement bounded dirty selection**

Use:

```sql
SELECT id
  FROM flightdeck_transcript_source
 WHERE active AND dirty
 ORDER BY dirty_at, id
 FOR UPDATE SKIP LOCKED
 LIMIT 32
```

Process one source per transaction. A successful hot pass sets `dirty=False` when at EOF. If more bytes remain, set `state="backfill"`, clear `dirty`, and enqueue exactly one cursor-keyed job.

- [ ] **Step 6: Implement one-chunk queue jobs**

`_job_backfill()` processes at most 64 MiB in one queue transaction. If bytes remain, enqueue a continuation with the new cursor in its identity key. This bounds memory and transaction duration without a manual commit inside a queue job.

`_job_reconcile_all()` performs the full filesystem/source comparison, then enqueues per-source backfill as needed. `_cron_enqueue_reconciliation()` only enqueues the date-keyed global job.

- [ ] **Step 7: Add queue XML records**

Create:

```xml
<odoo noupdate="1">
    <record id="queue_channel_flightdeck_usage" model="queue.job.channel">
        <field name="name">flightdeck_usage</field>
        <field name="parent_id" ref="queue_job.channel_root"/>
    </record>
    <record id="job_function_transcript_source_backfill" model="queue.job.function">
        <field name="model_id" ref="model_flightdeck_transcript_source"/>
        <field name="method">_job_backfill</field>
        <field name="channel_id" ref="queue_channel_flightdeck_usage"/>
    </record>
    <record id="job_function_transcript_source_reconcile" model="queue.job.function">
        <field name="model_id" ref="model_flightdeck_transcript_source"/>
        <field name="method">_job_reconcile_all</field>
        <field name="channel_id" ref="queue_channel_flightdeck_usage"/>
    </record>
    <record id="job_function_model_price_reprice_messages" model="queue.job.function">
        <field name="model_id" ref="model_flightdeck_model_price"/>
        <field name="method">_job_reprice_messages</field>
        <field name="channel_id" ref="queue_channel_flightdeck_usage"/>
    </record>
    <record id="job_function_session_repair_usage_totals" model="queue.job.function">
        <field name="model_id" ref="model_flightdeck_session"/>
        <field name="method">_job_repair_usage_totals</field>
        <field name="channel_id" ref="queue_channel_flightdeck_usage"/>
    </record>
</odoo>
```

- [ ] **Step 8: Separate hot and recovery crons**

Keep `cron_follow` one-minute fallback and watcher trigger. Add:

```xml
<record id="cron_discover_transcripts" model="ir.cron">
    <field name="name">FlightDeck: discover transcripts</field>
    <field name="model_id" ref="model_flightdeck_transcript_source"/>
    <field name="state">code</field>
    <field name="code">model._cron_discover_transcripts()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">minutes</field>
    <field name="active" eval="True"/>
</record>
<record id="cron_reconcile_usage" model="ir.cron">
    <field name="name">FlightDeck: queue usage reconciliation</field>
    <field name="model_id" ref="model_flightdeck_transcript_source"/>
    <field name="state">code</field>
    <field name="code">model._cron_enqueue_reconciliation()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">days</field>
    <field name="active" eval="True"/>
</record>
```

The watcher triggers only `cron_follow`, never either recovery cron.

- [ ] **Step 9: Preserve cron composition**

Final call order:

```text
flightdeck.session_send._cron_follow()
  → flightdeck.runner._cron_poll_events()
  → super()
flightdeck.session._cron_follow()
  → flightdeck.transcript.source._cron_ingest_dirty()
  → each existing following=True session._follow_tick()
```

No method may replace or bypass the runner poll or transcript display follow.

- [ ] **Step 10: Load XML and rerun tests**

Add `data/usage_queue_data.xml` before `data/follow_cron.xml` in the manifest. Expected focused suite result: `0 failed, 0 error`, and trapped jobs show channel `root.flightdeck_usage`.

### Task 7: Replace Global Watch Debounce with Exact Per-Path Dirtiness

**Files:**
- Modify: `odoo/flight_deck/models/follow_watcher.py`
- Create: `odoo/flight_deck/tests/test_follow_watcher.py`

**Interfaces:**
- Consumes: `flightdeck.transcript.source._register_paths(paths)` and existing `flight_deck.cron_follow`.
- Produces: exact dirty source rows within 200 ms quiet time or 1 second sustained-write maximum.
- Preserves: advisory lock `0x1F1D0001`, one watcher/database, runner spool wake-up.

- [ ] **Step 1: Write pure debounce-state tests**

Extract a small `_DuePaths` class with:

```python
def add(self, path, now): ...
def pop_due(self, now): ...
```

Test:

- two paths retain independent deadlines;
- repeated writes move only that path's quiet deadline;
- a path becomes due at its original `first_seen + 1.0` even under sustained writes;
- duplicate events return one path;
- `moved` uses `dest_path` when that is the JSONL destination.

- [ ] **Step 2: Write watcher integration tests with mocked Registry**

Assert one flush calls:

```python
env["flightdeck.transcript.source"]._register_paths(sorted(paths))
env.ref("flight_deck.cron_follow").sudo()._trigger()
cr.commit()
```

Assert invalid JSONL paths do not register. Runner spool activity still triggers the cron but does not create a transcript source.

- [ ] **Step 3: Run focused tests and verify current global-debounce failure**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -u flight_deck --test-enable \
  --test-tags /flight_deck:TestFollowWatcher --stop-after-init
```

Expected before implementation: `_DuePaths` missing and changed paths absent from database call.

- [ ] **Step 4: Watch transcript root recursively**

Schedule one recursive watch for `/mnt/claude-projects` when it exists. Keep non-recursive watches for runner spool directories. Do not watch only `following=True` session directories anymore.

- [ ] **Step 5: Flush exact paths and trigger once per batch**

A flush opens one registry cursor, registers all due transcript paths, triggers `cron_follow` once, then commits. If registration raises, rollback and log; the one-minute discovery cron recovers it. The watcher never opens or parses a transcript.

- [ ] **Step 6: Rerun tests and inspect one live event**

Append one newline-terminated fixture row to a readable test transcript. Expected Odoo logs, in order:

```text
transcript watcher started for flightdeck_odoo
registered 1 changed transcript source
```

No log should mention a global metrics snapshot. Restore the fixture by appending only; do not truncate a real transcript.

### Task 8: Publish Targeted Session-List Events

**Files:**
- Modify: `odoo/flight_deck/models/session.py`
- Modify: `odoo/flight_deck/models/message.py`
- Create: `odoo/flight_deck/tests/test_session_bus.py`

**Interfaces:**
- Produces:

```python
SESSION_LIST_CHANNEL = "flightdeck.session.list"
SESSION_LIST_EVENT = "flightdeck.session/list_changed"

@api.model
def _bus_list_changed(self, session_ids, changed_fields, created=False): ...
```

- Consumes: Odoo transactional `bus.bus._sendone()`.

- [ ] **Step 1: Write bus payload tests**

Patch `_sendone` and prove:

- session create sends `created=True`, one ID and all list identity fields;
- a write intersecting list fields sends one event;
- a runtime-state write sends the changed runtime fields plus `write_date`;
- source cursor writes send no session-list event;
- context `fd_no_list_bus=True` suppresses import/migration events;
- usage aggregate update sends one event after totals are written;
- payload contains no title, cost, token values or unrestricted data.

- [ ] **Step 2: Define the list field set**

```python
LIST_EVENT_FIELDS = {
    "name", "project_id", "start_ts", "end_ts", "duration_hours",
    "message_count", "tool_call_count", "input_tokens", "output_tokens",
    "cache_read", "cache_create", "total_cost", "unpriced_message_count",
    "runtime_state", "live_permission_mode", "live_model", "live_effort",
    "following", "session_type",
}
```

Every relevant `write()` event adds `write_date` to the payload because default list ordering can change even when `write_date` was not in `vals`.

- [ ] **Step 3: Implement transactional event sending**

Use:

```python
self.env["bus.bus"]._sendone(
    SESSION_LIST_CHANNEL,
    SESSION_LIST_EVENT,
    {
        "session_ids": sorted(set(session_ids)),
        "fields": sorted(set(changed_fields)),
        "created": bool(created),
    },
)
```

Odoo bus rows share the current transaction, so rolled-back usage never reaches the browser.

- [ ] **Step 4: Refactor aggregate repair to one write**

Change `action_recompute_totals()` to build one values dict per session and call `write()` once. Make it call or share `_usage_totals_values()` with `_job_repair_usage_totals()` so repair and manual action cannot diverge.

- [ ] **Step 5: Run focused tests**

Expected: all bus tests pass and no event payload contains usage values.

### Task 9: Add the FlightDeck Session List Controller

**Files:**
- Create: `odoo/flight_deck/static/src/views/session_list/session_list.js`
- Modify: `odoo/flight_deck/views/session_views.xml`
- Modify: `odoo/flight_deck/__manifest__.py`
- Create: `odoo/flight_deck/static/tests/session_list.test.js`

**Interfaces:**
- Consumes: channel `flightdeck.session.list`, event `flightdeck.session/list_changed`.
- Produces: registered JS view `flightdeck_session_list`; current-page reload through public `model.root.load()`.
- Does not patch `ListController.prototype` and does not call `record.update()` for server-originated values.

- [ ] **Step 1: Write JavaScript unit tests**

Use Odoo 19 helpers `defineModels`, `mountView`, `onRpc`, `asyncStep`, `waitForSteps`. Tests must prove:

1. the controller adds only `flightdeck.session.list`;
2. two eligible events inside 150 ms cause one `web_search_read` current-page reload;
3. `created=true` always reloads;
4. an ID on the current page reloads;
5. a non-visible ID whose changed fields include the active order field reloads;
6. a non-visible ID changing only an unrelated field does not reload;
7. invalid payload and other notification types do not reload;
8. unmount clears the timer, unsubscribes and deletes the channel.

- [ ] **Step 2: Run unit tests and confirm the view is unregistered**

Run the Odoo JS unit-test route after updating test assets:

```bash
curl -fsS 'http://127.0.0.1:8029/web/tests?mod=flight_deck' -o /tmp/flightdeck-web-tests.html
grep -q 'flight_deck/static/tests/session_list.test.js' /tmp/flightdeck-web-tests.html
```

Before implementation, expected grep status is non-zero.

- [ ] **Step 3: Implement the registered controller**

Core shape:

```javascript
/** @odoo-module **/

import { onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";

const CHANNEL = "flightdeck.session.list";
const EVENT = "flightdeck.session/list_changed";
const RELOAD_DELAY = 150;

export class FlightDeckSessionListController extends ListController {
    setup() {
        super.setup();
        this.bus = useService("bus_service");
        this.reloadTimer = null;
        this.onSessionListChanged = this.onSessionListChanged.bind(this);
        onMounted(() => {
            this.bus.addChannel(CHANNEL);
            this.bus.subscribe(EVENT, this.onSessionListChanged);
        });
        onWillUnmount(() => {
            clearTimeout(this.reloadTimer);
            this.bus.unsubscribe(EVENT, this.onSessionListChanged);
            this.bus.deleteChannel(CHANNEL);
        });
    }

    onSessionListChanged(payload) {
        if (!this.shouldReload(payload) || this.reloadTimer) {
            return;
        }
        this.reloadTimer = setTimeout(async () => {
            this.reloadTimer = null;
            await this.model.root.load();
        }, RELOAD_DELAY);
    }
}

export const flightDeckSessionListView = {
    ...listView,
    Controller: FlightDeckSessionListController,
};

registry.category("views").add("flightdeck_session_list", flightDeckSessionListView);
```

`shouldReload(payload)` checks loaded `resId` values and `this.model.root.orderBy` field names. It returns true for `created`, current-page IDs or changed active-order fields.

- [ ] **Step 4: Opt only the Logbook list into this controller**

Change:

```xml
<list string="Logbook" create="0" default_order="write_date desc"
      js_class="flightdeck_session_list">
```

No global list patch.

- [ ] **Step 5: Add assets and run JS tests**

Manifest:

```python
"web.assets_backend": [
    "flight_deck/static/src/components/**/*.js",
    "flight_deck/static/src/components/**/*.xml",
    "flight_deck/static/src/components/**/*.scss",
    "flight_deck/static/src/views/**/*.js",
    "flight_deck/static/src/dev/*.js",
],
"web.assets_unit_tests": [
    "flight_deck/static/tests/**/*.test.js",
],
```

Run the tests through Odoo’s browser test runner. Expected: all `flight_deck` JavaScript tests green, one reload step for a burst of events.

### Task 10: Complete Manager Actions and Core Behavioral Test

**Files:**
- Modify: `odoo/flight_deck/models/transcript_source.py`
- Modify: `odoo/flight_deck/models/session.py`
- Modify: `odoo/flight_deck/models/model_price.py`
- Modify: `odoo/flight_deck/views/transcript_source_views.xml`
- Modify: `odoo/flight_deck/views/session_views.xml`
- Modify: `odoo/flight_deck/views/price_views.xml`
- Modify: `docker/odoo19/README.md`
- Create: `odoo/flight_deck/tests/test_usage_actions.py`
- Create during verification: `devtools_mcp_trace/flightdeck-realtime-session-usage/` evidence files

**Interfaces:**
- Produces manager actions that enqueue, never synchronously execute, long work.
- Produces behavioral evidence for near-realtime list update.

- [ ] **Step 1: Test action-to-job mapping**

Assert:

```text
source.action_enqueue_backfill() → source._job_backfill()
source.action_enqueue_reconciliation() → source-model._job_reconcile_all()
price.action_enqueue_reprice() → price._job_reprice_messages()
session.action_enqueue_usage_repair() → session._job_repair_usage_totals()
```

Every action returns an `ir.actions.client` display notification naming the queued operation. No action reads a full transcript synchronously.

- [ ] **Step 2: Add buttons and source smart button**

- Source form: `Queue backfill`, `Queue reconciliation`.
- Price form/list server action: `Reprice messages`.
- Session form: `Repair usage totals` and a smart button that opens `usage_source_ids` through `action_open_usage_sources()`.
- Keep existing `Recompute totals` only as a compatibility alias calling the queue action; do not run an unbounded recompute in the HTTP transaction.

- [ ] **Step 3: Upgrade the module and restart the registry**

Run:

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo -u flight_deck --stop-after-init
docker compose -f /home/nathando/Documents/Projects/flight-deck.sh/docker/odoo19/compose.yml restart odoo
```

Expected Odoo log: module `flight_deck` loaded without missing model/view/XML ID errors; queue runner logs channel configuration.

- [ ] **Step 4: Run the architecture checks**

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo --no-http <<'PY'
Source = env["flightdeck.transcript.source"]
print("sources", Source.search_count([]))
print("dirty", Source.search_count([("dirty", "=", True)]))
print("queue_channel", env.ref("flight_deck.queue_channel_flightdeck_usage").complete_name)
print("ai_installed", bool(env["ir.module.module"].search_count([
    ("name", "=", "ai"), ("state", "=", "installed")
])))
PY
```

Expected:

```text
sources <non-negative integer>
dirty <non-negative integer>
queue_channel root.flightdeck_usage
ai_installed False
```

The exact counts depend on the mounted corpus. `flight_deck` must load while `ai_installed` is false.

- [ ] **Step 5: Run the behavioral browser test**

Use a dedicated fixture session, never edit a real transcript:

1. create a valid fixture root JSONL under a test project directory on the mounted host path;
2. open Logbook at `http://flightdoo.localhost` and leave the list visible;
3. capture the initial row/message count;
4. append one newline-terminated assistant usage line with a unique UUID and priced model;
5. wait up to 5 seconds;
6. assert the new/updated row is visible without manual reload;
7. assert `message_count` and `total_cost` changed to the expected values;
8. inspect Network and confirm one coalesced `web_search_read`, not a full transcript-corpus request;
9. save before/after screenshots and relevant network output under `devtools_mcp_trace/flightdeck-realtime-session-usage/`.

If fixture cleanup is required, queue its absolute path and reason in workspace `TEMP_FILES_SHOULD_BE_REMOVE.MD`; do not delete it.

- [ ] **Step 6: Record expected latency and fallback behavior**

The pass condition is:

```text
watch event → source dirty + cron trigger ≤ 1.2 s under sustained writes
quiet append → source dirty + cron trigger ≈ 0.2–0.4 s
bus event → current-page reload after 150 ms coalescing
missed watch event → discovery recovery ≤ 60 s
```

These are operational bounds, not sleep-based unit-test assertions. Use timestamps from logs/browser evidence.

### Task 11: Add Optional Native Odoo AI Usage Bridge

**Files:**
- Create: `odoo/flight_deck_ai/__init__.py`
- Create: `odoo/flight_deck_ai/__manifest__.py`
- Create: `odoo/flight_deck_ai/hooks.py`
- Create: `odoo/flight_deck_ai/models/__init__.py`
- Create: `odoo/flight_deck_ai/models/ai_agent.py`
- Create: `odoo/flight_deck_ai/models/session.py`
- Create: `odoo/flight_deck_ai/services/__init__.py`
- Create: `odoo/flight_deck_ai/services/llm_usage.py`
- Create: `odoo/flight_deck_ai/data/usage_queue_data.xml`
- Create: `odoo/flight_deck_ai/views/session_views.xml`
- Create: `odoo/flight_deck_ai/tests/__init__.py`
- Create: `odoo/flight_deck_ai/tests/test_native_usage.py`

**Interfaces:**
- Consumes: `LLMApiService._request_llm_openai_helper(body, tools=None, inputs=())`, `LLMApiService._request_llm_google_helper(body, llm_model, inputs=())`, and `flightdeck.message._record_usage()`.
- Produces: one `usage_source="odoo_ai"` message per successful provider helper response.
- Never imports Enterprise code from core `flight_deck`.

- [ ] **Step 1: Create the optional manifest and installability boundary**

```python
{
    "name": "FlightDeck AI",
    "version": "19.0.1.0.0",
    "summary": "Native Odoo AI usage in FlightDeck",
    "author": "NAKIVO",
    "license": "LGPL-3",
    "depends": ["flight_deck", "ai"],
    "data": [
        "data/usage_queue_data.xml",
        "views/session_views.xml",
    ],
    "post_load": "post_load",
    "installable": True,
}
```

`flight_deck/__manifest__.py`, its imports and its tests must contain no `ai` reference.

- [ ] **Step 2: Write patch-boundary tests**

With fake originals, prove:

- calling `post_load()` twice wraps each helper once;
- wrapper returns the original tuple unchanged;
- OpenAI maps `cached_tokens` to `cache_read`;
- Google maps `cached_tokens` to `cache_read`;
- empty usage creates no row;
- three helper returns in one run produce three unique ledger rows;
- helper exception creates no row and propagates unchanged.

- [ ] **Step 3: Write session identity tests**

Assert exact IDs for a fixed database UUID:

```text
odoo-ai:db-uuid:channel:42:agent:7
odoo-ai:db-uuid:run:run-uuid:agent:7
```

Assert channel calls share one session, direct runs with different `run_uuid` create different sessions, and all successive tool calls within one run share the same session.

- [ ] **Step 4: Extend `ai.agent._generate_response()` only to add context**

```python
import uuid

from odoo import models


class AIAgent(models.Model):
    _inherit = "ai.agent"

    def _generate_response(self, prompt, chat_history=None, extra_system_context=""):
        self.ensure_one()
        run_id = self.env.context.get("flightdeck_ai_run_id") or str(uuid.uuid4())
        record = self.with_context(
            flightdeck_ai_agent_id=self.id,
            flightdeck_ai_run_id=run_id,
        )
        return super(AIAgent, record)._generate_response(
            prompt,
            chat_history=chat_history,
            extra_system_context=extra_system_context,
        )
```

Do not copy or fork Enterprise request-loop logic.

- [ ] **Step 5: Implement guarded helper wrappers**

`hooks.post_load()` calls `_patch_helpers()`. The adapter stores originals as module constants and sets `_flightdeck_usage_patched=True` on the class. Wrapper shape:

```python
def _openai_helper(service, body, tools=None, inputs=()):
    result = _ORIGINAL_OPENAI(service, body, tools=tools, inputs=inputs)
    usage = result[3]
    record_provider_usage(
        service.env,
        provider="openai",
        model=body.get("model"),
        usage=usage,
    )
    return result
```

Google uses the explicit `llm_model` argument. Preserve positional and keyword compatibility with the original signatures.

- [ ] **Step 6: Commit usage on an independent cursor**

`record_provider_usage()` captures only primitive values from the old environment, then opens:

```python
with Registry(dbname).cursor() as cr:
    new_env = api.Environment(cr, SUPERUSER_ID, {})
    # find/create session, price values, insert one row, update totals
    cr.commit()
```

Normalized row:

```python
{
    "uuid": f"odoo-ai:{uuid.uuid4()}",
    "model": model,
    "ts": fields.Datetime.now(),
    "input_tokens": int(usage.get("input_tokens") or 0),
    "cache_read": int(usage.get("cached_tokens") or 0),
    "cache_create_5m": 0,
    "cache_create_1h": 0,
    "output_tokens": int(usage.get("output_tokens") or 0),
    "service_tier": False,
}
```

Pass `usage_source="odoo_ai"`, `provider`, no transcript source and no child agent ID into core `_prepare_usage_values()`.

Do not seed guessed OpenAI/Google rates. The existing `flightdeck.model.price` UI supplies exact time-bounded rates. Until a model prefix is configured, native rows are visible as unpriced and token counts remain correct.

- [ ] **Step 7: Add deferred native relation linking**

Session extension:

```python
ai_agent_id = fields.Many2one("ai.agent", index=True, ondelete="set null")
ai_channel_id = fields.Many2one("discuss.channel", index=True, ondelete="set null")
ai_run_id = fields.Char(index=True)
```

After independent insert, enqueue:

```python
session.with_delay(
    eta=timedelta(seconds=5),
    channel="root.flightdeck_usage",
    identity_key=f"flightdeck-ai-link:{session.id}:{agent_id}:{channel_id or 0}",
)._job_link_ai_records(agent_id, channel_id)
```

Register `_job_link_ai_records` as a `queue.job.function` in the optional addon. The job checks records still exist before writing Many2ones.

- [ ] **Step 8: Test rollback survival on the dedicated test database**

The integration test must:

1. create committed agent/channel fixtures;
2. start an outer savepoint/transaction;
3. invoke the usage recorder;
4. roll back the outer work;
5. open a fresh registry cursor;
6. assert the `odoo-ai:<uuid4>` message and session totals remain;
7. clean test database rows through ORM at the end of the dedicated test run.

This test is tagged `post_install` and runs only on `flightdeck_odoo_test`, never a shared database.

- [ ] **Step 9: Run optional-addon tests in an Enterprise-capable image only**

The current CE runtime does not mount `/home/nathando/Documents/Projects/19EE/odoo/addons/ai`. Run this phase only after adding an explicitly authorized Enterprise addons mount to a separate test service/image:

```bash
/entrypoint.sh odoo -c /etc/odoo/odoo.conf -d flightdeck_odoo_test \
  -i flight_deck_ai --test-enable --test-tags /flight_deck_ai \
  --stop-after-init
```

Expected: OpenAI, Google, multi-call, mapping and rollback tests pass. Absence of Enterprise `ai` must leave `flight_deck_ai` unavailable but must not affect installation, upgrade or tests of `flight_deck`.

### Task 12: Run Full Verification and Update the Runbook

**Files:**
- Modify: `docker/odoo19/README.md`
- Verify only: all files above

**Interfaces:**
- Produces: a repeatable release gate for core CE and a separate release gate for optional Enterprise integration.

- [ ] **Step 1: Run static and XML checks**

```bash
python3 -m compileall -q /home/nathando/Documents/Projects/flight-deck.sh/odoo/flight_deck
python3 -m compileall -q /home/nathando/Documents/Projects/flight-deck.sh/odoo/flight_deck_ai
find /home/nathando/Documents/Projects/flight-deck.sh/odoo/flight_deck \
  /home/nathando/Documents/Projects/flight-deck.sh/odoo/flight_deck_ai \
  -name '*.xml' -print0 | xargs -0 -n1 xmllint --noout
```

Expected: exit 0. If `xmllint` is not installed on the host, run the same check in a disposable tooling container; do not install packages into the Odoo application container solely for this check.

- [ ] **Step 2: Run all core Python tests without Enterprise `ai`**

```bash
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
  -d flightdeck_odoo_test -u flight_deck --test-enable \
  --test-tags /flight_deck --stop-after-init
```

Expected: `0 failed, 0 error`; logs show no import attempt for addon `ai`.

- [ ] **Step 3: Run all FlightDeck JavaScript tests**

Open Odoo’s unit-test page for `flight_deck` in the browser runner and save the result. Expected: all tests under `flight_deck/static/tests` pass, no browser console error, no leaked bus subscription after unmount.

- [ ] **Step 4: Verify queue execution, not only enqueueing**

On the test database, enqueue one source backfill and inspect `queue.job`:

```python
jobs = env["queue.job"].search([
    ("channel", "=", "root.flightdeck_usage"),
], order="date_created desc", limit=5)
print([(job.name, job.state, job.identity_key) for job in jobs])
```

Expected state progression: `pending/enqueued → started → done`. The source cursor and session totals change only when the job transaction commits.

- [ ] **Step 5: Verify websocket routing**

In browser Network, confirm `/websocket` returns `101 Switching Protocols` through `flightdoo.localhost`. Confirm a list event arrives without polling the full corpus. If it fails, inspect Nginx and Odoo evented-worker logs before changing Caddy; host port 8029 remains the contract.

- [ ] **Step 6: Verify performance invariants from database and logs**

Report these claims with actual output:

| Claim | Evidence |
|---|---|
| Hot cron touches at most 32 sources | SQL/log count from one invocation |
| One source reads at most 1 MiB before queueing | cursor delta and queued identity key |
| Child usage rolls into root | source agent ID, message session ID, root totals |
| Duplicate UUID does not change totals | before/after totals and one ledger row |
| Unpriced usage is visible | `priced=False`, `unpriced_message_count > 0` |
| Realtime path has no corpus scan | call spy/test plus cron log |
| Current page reload is coalesced | browser network count |
| Core works without `ai` | installed module state and successful core suite |

- [ ] **Step 7: Update operational instructions**

Document exact commands for:

- image rebuild after OCA pin changes;
- module upgrade and mandatory Odoo restart;
- checking `queue_job` channel state;
- checking transcript-source error rows;
- manually queueing backfill/reprice/repair from UI;
- granting and checking host ACL for uid 100;
- testing websocket route;
- running core and optional-addon test suites;
- preserving `fdreader` read-only access and `.env` credential location.

- [ ] **Step 8: Final consistency scan**

Run:

```bash
PLAN=/home/nathando/Documents/Projects/flight-deck.sh/docs/superpowers/plans/2026-09-14-odoo-realtime-session-usage.md
grep -nE 'TBD|TODO|implement later|fill in|appropriate error|similar to Task' "$PLAN"
grep -RIn --include='*.py' --include='*.xml' --include='*.js' \
  'flightdeck_session_list\|flightdeck.session/list_changed\|root.flightdeck_usage\|flightdeck.transcript.source' \
  /home/nathando/Documents/Projects/flight-deck.sh/odoo
```

Expected: the first grep prints nothing. The second shows one consistent spelling for every interface across model, XML, JS and tests.

## Dependency order and delivery gates

```text
Task 1  queue_job + workers + proxy
  ↓
Task 2  schema + migration + ACL
  ↓
Task 3  pure parser
  ↓
Task 4  source discovery
  ↓
Task 5  atomic ledger + root rollup
  ↓
Task 6  bounded cron + deferred jobs
  ↓
Task 7  exact-path watcher
  ↓
Task 8  server bus event
  ↓
Task 9  list controller
  ↓
Task 10 core CE behavioral gate
  ↓
Task 11 optional Enterprise AI bridge
  ↓
Task 12 full verification and runbook
```

Core delivery is complete at Task 10 and can ship without Task 11. Task 11 is a separate installable addon and test gate; failure or absence of Enterprise `ai` cannot block core transcript discovery, cost calculation or realtime Logbook updates.

## Expected end state

```text
Claude JSONL append
  → watchdog path debounce (200 ms, max 1 s)
  → register/dirty one transcript source
  → trigger cron_follow
  → lock one source + one root session
  → read ≤1 MiB / ≤200 usage rows
  → INSERT ON CONFLICT DO NOTHING RETURNING
  → increment stored root totals from returned rows
  → advance source cursor
  → commit
  → targeted list bus event
  → coalesced current-page reload (150 ms)
```

```text
Large/new/lagging/repriced/reconciled work
  → with_delay(channel="root.flightdeck_usage", identity_key=...)
  → serialized queue worker
  → bounded chunk transaction
  → continuation job when bytes remain
```

```text
Native Odoo AI provider response
  → patched provider helper returns normalized usage
  → independent Odoo cursor
  → native session identity
  → same immutable ledger + stored totals
  → independent commit survives outer rollback
  → deferred agent/channel relation link
  → same targeted list bus event
```
