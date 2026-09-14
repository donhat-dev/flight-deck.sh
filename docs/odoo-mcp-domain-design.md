# Thiết kế domain MCP `odoo_*` cho FlightDeck

**Trạng thái**: đề xuất thiết kế, chưa code · **Ngày**: 2026-08-28
**Baseline khảo sát**: [erpipe-org/mcp-odoo](https://github.com/erpipe-org/mcp-odoo) (MIT), đối chiếu [ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (MPL-2.0)
**Phạm vi**: thêm một domain vào agent surface của FlightDeck — một dòng trong `_DOMAINS`, một module có bảng `TOOLS`. Không sửa registry, không sửa CLI, không sửa wrapper MCP.

---

## 0. Kết luận ngắn

Lấy erpipe làm **tài liệu tham khảo về hình dạng tool và về chính sách ghi**, không lấy làm code. Phần code thật sự dùng lại được nằm dưới 10%: lớp transport của họ là XML-RPC `execute_kw` cộng JSON-2 (chỉ Odoo 19+), trong khi ràng buộc của chúng ta bắt mọi lệnh ghi phải đi qua `/jsonrpc` — endpoint mà erpipe không dùng ở đâu cả. Luồng ghi ba bước của họ (`preview_write` → `validate_write` → `execute_approved_write` với approval token có TTL) giữ state giữa các call, còn wrapper MCP của FlightDeck spawn lại CLI mỗi lần gọi nên không có chỗ nào để giữ token đó.

Bộ tool đề xuất: **7 tool ở phase 1** (4 đọc, 3 ghi), bỏ 34 tool của erpipe. Ước lượng **7–8 man-day**.

---

## 1. Fork hay viết mới

### Đọc được gì từ erpipe-org/mcp-odoo

| Hạng mục | Sự thật đọc được | Nguồn |
|---|---|---|
| License | MIT, © 2025 Lê Anh Tuấn | `LICENSE` |
| Ngôn ngữ | Python, package `src/odoo_mcp`, quản lý bằng `uv` | repo |
| Bảo trì | commit gần nhất **2026-08-25**, 398 star, 6 issue mở, tạo 2025-03-17 | GitHub API |
| Số tool | 41 tool, chia 10 nhóm | README |
| Transport | `xmlrpc.client.ServerProxy` tới `/xmlrpc/2/common` + `/xmlrpc/2/object`, gọi `execute_kw`; hoặc External JSON-2 (`ODOO_TRANSPORT=json2`, chỉ Odoo 19+) | `src/odoo_mcp/odoo_client.py:159-163, 256-262` |
| Credential | env `ODOO_URL` / `ODOO_DB` / `ODOO_USERNAME` / `ODOO_PASSWORD` hoặc `ODOO_API_KEY`; hoặc file `odoo_config.json` dò theo 4 vị trí | `odoo_client.py:753-800` |
| Multi-instance | file `odoo_config.multi.json` có map `instances` **và một khoá `default`**; tham số `instance` là tùy chọn trên mọi tool | `odoo_config.multi.json.example` |
| Chặn ghi | `ODOO_MCP_ENABLE_WRITES=1` + approval token + `confirm=true`; allowlist method dạng `model.method` từ env hoặc `odoo_mcp_policy.json`, file hỏng thì coi như rỗng (fail-closed) | `write_policy.py` |
| Odoo hỗ trợ | 16.0 / 17.0 / 18.0 / 19.0, smoke test qua Docker Compose. **Không test 12.0** | README |
| Module phía Odoo | không cần cài gì | README |

`auth.py` của họ không phải chỗ nạp credential Odoo — đó là OAuth 2.1 introspection cho transport HTTP, thứ FlightDeck không dùng.

### Đối chiếu ivnvxd/mcp-server-odoo

MPL-2.0, commit gần nhất 2026-08-26 (v0.8.0), 377 star, 13 issue mở. Chế độ chuẩn **bắt buộc cài module `mcp_server` phía Odoo, chỉ có cho 19.0+** — loại luôn `odoo12-local` và `staging-ce`. Chế độ thay thế là `ODOO_YOLO=true`, nối thẳng XML-RPC nhưng bỏ toàn bộ tầng kiểm soát của module. Nghĩa là với estate của chúng ta, ivnvxd hoặc không chạy được, hoặc chạy ở chế độ mà chính tác giả ghi là "development/testing only". Không dùng.

### Kết luận

**Viết mới, port có chọn lọc.** Cụ thể:

| Phần của erpipe | Xử lý | Lý do |
|---|---|---|
| Ý tưởng allowlist method dạng `model.method`, file hỏng = rỗng | **Lấy nguyên tinh thần**, viết lại ~40 dòng | `write_policy.py` đúng hướng fail-closed, nhưng bám vào env riêng của họ |
| Chữ ký `search_read` / `fields_get` / retry backoff | **Tham khảo**, viết lại | ~50 dòng logic, phụ thuộc `schema_cache` và `field_ranking` của họ |
| `RedirectTransport` (XML-RPC theo redirect) | **Bỏ** | Ba instance của ta đều nói HTTP thẳng, không qua redirect |
| Toàn bộ `odoo_client.py` (1010 dòng) | **Bỏ** | Không có đường `/jsonrpc`; đây là điều kiện bắt buộc của ta cho mọi lệnh ghi |
| `preview_write` / `validate_write` / `execute_approved_write` | **Bỏ** | Giữ approval token qua nhiều call; wrapper của FlightDeck spawn process mới mỗi call nên token không sống nổi |
| `task_queue.py`, `tools_async.py` | **Bỏ** | Worker pool chết cùng process |
| `knowledge_index.py`, `tools_knowledge.py` | **Bỏ** | BM25 index cục bộ; `session_search` đã lo phần "chuyện này từng bàn ở đâu" |
| `tools_cross_instance.py` | **Bỏ** | Fan-out nhiều instance trong một call là đúng cái mơ hồ đã gây sự cố deeplink |
| `auth.py` (OAuth), `rate_limit.py`, `plugin_api.py`, transport HTTP/SSE | **Bỏ** | FlightDeck chỉ chạy stdio, một người dùng, một máy |
| `scan_addons_source`, `fit_gap_report`, `business_pack_report`, `inspect_model_relationships` | **Bỏ** | Trùng `odoo_graph` — xem §3 |

**Tỷ lệ port thẳng: dưới 10%.** MIT cho phép fork và cho phép dùng lại từng đoạn; nếu có đoạn nào chép nguyên, giữ header ghi nguồn và bản MIT của họ trong `docs/` hoặc trong chính file đó.

---

## 2. Ranh giới transport: đọc XML-RPC, ghi `/jsonrpc`

Đây là ràng buộc cứng, và nó có bằng chứng trong chính source đang chạy.

Odoo 12 phục vụ XML-RPC ở `odoo/addons/base/controllers/rpc.py`:

```python
def _xmlrpc(self, service):
    data = request.httprequest.get_data()
    params, method = loads(data)
    result = dispatch_rpc(service, method, params)
    return dumps((result,), methodresponse=1, allow_none=False)   # ← đây
```

`allow_none=False` áp cho **cả** `/xmlrpc/<service>` lẫn `/xmlrpc/2/<service>`. `dispatch_rpc` chạy xong và commit transaction trước, việc serialize kết quả xảy ra sau. Nên một method trả `None` — mọi `action_*`, `button_*` viết theo lối Odoo bình thường — sẽ ném `TypeError: cannot marshal None unless allow_none is enabled` **sau khi database đã đổi**. Agent nhận lỗi, database đã ghi. Đây là sự cố có thật trong workspace này, và trên đây là dòng code gây ra nó.

`/jsonrpc` trong cùng file trả thẳng `dispatch_rpc(...)` qua `json.dumps`, `None` thành `null`, không có lớp marshalling nào để vỡ.

**Đo trực tiếp trên hai instance đang chạy** (2026-08-28, `common.version` qua `/jsonrpc`):

| Instance | Cổng | `/jsonrpc` | `/xmlrpc/2/common` | Version báo về |
|---|---|---|---|---|
| odoo12-local | 8069 | HTTP 200 | HTTP 200 | `12.0+e` |
| odoo19-local | 8019 | HTTP 200 | HTTP 200 | `19.0-20260723` |

Cả hai endpoint đều sống trên cả hai bản. Ranh giới đặt như sau:

- **Đọc thuần** — `search_read`, `fields_get`, `search_count`, `read_group` — đi `/xmlrpc/2/object`. Những method này trả list/dict, không bao giờ trả `None`, nên lớp marshalling không có gì để vỡ. Giữ XML-RPC ở đây khiến `odoo_*` nói cùng thứ tiếng với `scripts/probe_xmlrpc_smoke.py` và với cách truy cập `staging-ce` đã ghi trong memory, nên một lỗi transport nhìn quen mắt.
- **Mọi thứ có thể ghi** — `create`, `write`, và mọi lời gọi method tùy ý — đi `/jsonrpc` với `service="object"`, `method="execute_kw"`. Không có ngoại lệ, kể cả khi biết chắc method đó trả `True`.

**Một bẫy của `/jsonrpc` phải xử lý ngay từ đầu.** Odoo 12 dựng response như sau (`odoo/http.py:631-639`):

```python
if error is not None:  response['error'] = error
if result is not None: response['result'] = result
```

Method trả `None` cho ra `{"jsonrpc": "2.0", "id": 1}` — **không có `result`, cũng không có `error`**. Client phải đọc đó là "thành công, kết quả là None". Nếu viết theo lối `resp["result"]` thì sẽ ném `KeyError` đúng vào trường hợp mà cả thiết kế này sinh ra để tránh.

**Phương án đơn giản hơn, và vì sao không chọn.** Dùng `/jsonrpc` cho tất cả thì chỉ còn một code path, một bảng lỗi, và loại bỏ hẳn cả lớp lỗi marshalling. Đổi lại, mất phần code đọc tham khảo được từ erpipe và mất sự tương đồng với các probe sẵn có. Khuyến nghị: giữ split, nhưng **cho cả hai transport đi qua đúng một hàm `_execute(instance, model, method, args, kwargs, *, transport)`** — nếu sau này muốn gộp về một transport thì đó là sửa một giá trị mặc định, không phải viết lại tool. Điều làm đảo ngược khuyến nghị này: nếu trong phase 1 gặp dù chỉ một lỗi marshalling trên đường đọc, bỏ XML-RPC luôn.

---

## 3. Vị trí trong hệ: ba thứ, ba câu hỏi khác nhau

| Công cụ | Trả lời câu hỏi | Nguồn dữ liệu | Có chạm instance đang chạy không |
|---|---|---|---|
| MCP `odoo_graph` | "Cấu trúc **tĩnh** thế nào" — module nào depends module nào, model nào `_inherit` model nào, view nào chứa field nào, đổi chỗ này thì vỡ chỗ nào | Graph dựng sẵn từ `__manifest__.py` và XML trong repo | Không |
| `Projects/scripts/` | "Stack có sống không, bảng này bao nhiêu dòng, field này có tồn tại không" — probe cố định, read-only, chỉ cho stack Odoo 12 local | `docker exec` + XML-RPC | Có, nhưng chỉ đọc và chỉ một stack |
| **`odoo_*` (thiết kế này)** | "Bản ghi này **hiện tại** đang thế nào, và đổi nó" — đọc/ghi live, có kiểm soát, trên **nhiều** instance gọi theo tên | RPC tới instance đang chạy | Có, đọc và ghi |

Ranh giới thực tế: `odoo_graph` biết `nakivo_sale` khai `depends` những gì; nó **không** biết instance nào đang thật sự cài `nakivo_sale`. `odoo_fields` biết, vì nó hỏi `ir.model.fields` của chính instance đó.

**Những tool của erpipe cố tình không tồn tại ở đây vì `odoo_graph` đã làm rồi:**

| Tool erpipe | Thay bằng |
|---|---|
| `scan_addons_source` | `odoo_graph.module_deps`, `odoo_graph.path` |
| `fit_gap_report`, `business_pack_report` | `odoo_graph.find`, `odoo_graph.model_surface` |
| `inspect_model_relationships` | `odoo_graph.model_surface`, `odoo_graph.who_uses` |
| `list_models` (phần liệt kê model của repo) | `odoo_graph.find` |
| "view nào hiện field X" | `odoo_graph.view_contents`, `odoo_graph.field_usage` |

Và những tool không tồn tại vì `scripts/` đã làm: đếm dòng bảng, kiểm tra stack sống. Nếu cần đếm trên instance khác `odoo12-local`, dùng `odoo_search` với `limit=0` (tool tự chuyển sang `search_count`, xem §4); đừng thêm `odoo_count`.

---

## 4. Bảng tool

Nguyên tắc: bộ nhỏ nhất đủ mở một hành lang đọc/ghi live. Mọi tên đều mang tiền tố `odoo_`. **Mọi tool chạm Odoo đều bắt buộc nhận `instance=`.**

### Phase 1 — 7 tool

| Tool | Mô tả một dòng | Schema props | Required | Transport | R/W | Tương ứng erpipe |
|---|---|---|---|---|---|---|
| `odoo_instances` | Liệt kê các instance đã khai báo cùng tư thế ghi của từng cái; không gọi Odoo | — | — | không | R | `list_instances` |
| `odoo_ping` | Instance này là ai: base URL, db, version, uid sau khi đăng nhập, model nào được phép ghi | `instance` | `instance` | XML-RPC | R | `health_check`, `get_odoo_profile` |
| `odoo_fields` | Metadata field của một model **trên chính instance đó** — kiểu, nhãn, required, readonly, quan hệ | `instance`, `model`, `fields`, `attributes` | `instance`, `model` | XML-RPC | R | `get_model_fields` |
| `odoo_search` | `search_read` có chặn: domain, fields, limit, offset, order. `limit=0` chuyển sang đếm — xem ghi chú dưới bảng | `instance`, `model`, `domain`, `fields`, `limit`, `offset`, `order` | `instance`, `model` | XML-RPC | R | `search_records` + `read_record` |
| `odoo_create` | Tạo một bản ghi. Chỉ chạy khi model nằm trong `write_models` của instance và `confirm=true` | `instance`, `model`, `values`, `confirm` | `instance`, `model`, `values` | JSON-RPC | W | `execute_approved_write` (create) |
| `odoo_write` | Ghi giá trị lên các id đã có. Cùng hai điều kiện trên | `instance`, `model`, `ids`, `values`, `confirm` | `instance`, `model`, `ids`, `values` | JSON-RPC | W | `execute_approved_write` (write) |
| `odoo_call` | Gọi một method trên bản ghi. Chỉ chạy khi `model.method` nằm trong `write_methods` và `confirm=true` | `instance`, `model`, `method`, `ids`, `args`, `kwargs`, `confirm` | `instance`, `model`, `method`, `ids` | JSON-RPC | W | `execute_method` |

**`limit=0` phải bị chặn lại trong tool, không được chuyển tiếp xuống Odoo.** Trong Odoo, `0` là falsy: `_search` thấy `limit=0` thì không sinh mệnh đề `LIMIT` nào cả, nên `search_read(limit=0)` trả về **toàn bộ bảng** — ngược hẳn với ý định, và trên bản restore của `odoo12-local` thì đó là cách chắc chắn nhất để vượt timeout 120 giây. Nên `odoo_search` bắt `limit == 0` và gọi `search_count` thay thế, trả `{"count": n}` không kèm dòng nào. Số `0` không bao giờ đi tới `search_read`.

`odoo_instances` là **ngoại lệ duy nhất** của luật "mọi tool nhận `instance`", và lý do là nó không gọi Odoo: đây chính là tool cho biết những tên nào hợp lệ để truyền cho sáu tool kia. Không có nó thì `instance` bắt buộc mà không có chỗ tra tên.

`confirm` để mặc định `False` chứ không phải bắt buộc — nếu bắt buộc thì theo hợp đồng schema của registry nó phải nằm trong `required`, mà lúc đó gọi `confirm=false` cũng vẫn hợp lệ và chẳng khác gì. Cách radar làm đúng hơn: mặc định `False`, không truyền thì trả về một lời từ chối kèm mô tả chính xác việc gì sẽ xảy ra.

**Một invariant có sẵn miễn phí.** `tests/test_agentsurface.py::test_every_merged_schema_matches_its_function_signature` bắt mọi tham số không có default phải nằm trong `required`, và ngược lại. Nên chỉ cần viết `def odoo_search(instance, model, domain=None, ...)` là `instance` bắt buộc được chính test suite hiện có canh, không phải viết thêm test nào.

### Phase 2 — cân nhắc sau, không làm ngay

| Tool | Vì sao hoãn |
|---|---|
| `odoo_groups` (`read_group`) | Hữu ích cho việc đối chiếu hai hệ thống trên cùng input, nhưng Odoo 12 dùng `read_group` còn Odoo 19 đổi sang `formatted_read_group`. Phải viết nhánh theo version — chờ tới khi có nhu cầu thật |

### Tool của erpipe cố ý bỏ, kèm lý do

| Bỏ | Lý do |
|---|---|
| `preview_write`, `validate_write`, `execute_approved_write` | Cần approval token sống qua nhiều call. Wrapper spawn process mới mỗi call, không có chỗ giữ. Thay bằng allowlist theo instance + `confirm=true` trong một call |
| `submit_async_task`, `get_async_task`, `cancel_async_task`, `list_async_tasks` | Worker pool chết cùng process |
| `search_across_instances`, `aggregate_across_instances`, `accounting_health_across_instances` | Fan-out ngầm nhiều instance trong một call là đúng lớp mơ hồ đã gây sự cố deeplink |
| `index_knowledge`, `search_knowledge`, `knowledge_stats` | `session_search` đã có |
| `scan_addons_source`, `fit_gap_report`, `business_pack_report`, `inspect_model_relationships` | Trùng `odoo_graph` — §3 |
| `generate_json2_payload`, `upgrade_risk_report`, `lookup_model_history` | Phục vụ đường nâng cấp XML-RPC → JSON-2 mà ta không đi |
| `receivable_payable_aging`, `accounting_health_summary` | Nghiệp vụ kế toán, không phải việc của track nào đang chạy. Muốn thì `odoo_search` là đủ |
| `search_employee`, `search_holidays` | Đường tắt nghiệp vụ; `odoo_search` trên `hr.employee` làm được, không cần thêm bề mặt |
| `chatter_post` | Ghi vào `mail.message`. Nếu thật sự cần thì đó là `odoo_call` với `message_post` trong `write_methods` — không cần tool riêng |
| `read_attachment` | Đọc binary qua stdout JSON là đường xấu; cần thì lấy qua DB hoặc filestore |
| `schema_catalog`, `build_domain`, `diagnose_odoo_call`, `diagnose_access` | Tool để chuẩn bị gọi tool khác. Chi phí bề mặt lớn hơn giá trị; `odoo_fields` + thông báo lỗi rõ ràng thay được |
| `list_models` | Phần tĩnh thuộc `odoo_graph`; phần live thì `odoo_search` trên `ir.model` làm được |

### Tool cố ý **không** tồn tại, kể cả sau này

**`odoo_unlink`.** Không có tool xóa, không có cờ bật nó lên. Xóa bản ghi Odoo kéo theo cascade qua `ondelete` mà tool không nhìn thấy trước, và `odoo12-local` là bản restore không có backup. Cần xóa thì làm bằng tay trong UI, hoặc bằng ORM trong `odoo shell` với người ngồi xem. Điều này phải kiểm chứng được: gọi `flightdeck odoo_unlink` phải thoát mã 3 ("unknown tool"), và đó là một test — xem §7.

---

## 5. Luồng một call

```
Claude gọi tools/call {name: "odoo_write", arguments: {instance: "odoo19-local", ...}}
  │
  ▼
flightdeck/mcp_server.py                  ← process sống lâu, cố tình "ngu"
  │  subprocess.run([python, -m, flightdeck.cli, "odoo_write", "--json", "-"],
  │                 input=<arguments JSON>, timeout=120)
  ▼
flightdeck/cli.py  main()                 ← PROCESS MỚI, mỗi call một lần
  │  runtime.configure(reaper=False)      ← mở DB FlightDeck, init store radar+treasures
  │                                          (odoo_* không cần bảng nào, nhưng vẫn trả phí này)
  ▼
agentsurface/registry.dispatch("odoo_write", args)
  │  tools = merged()  →  import flightdeck.odoo.mcp_server  (lazy)
  ▼
flightdeck/odoo/mcp_server.py  odoo_write(instance="odoo19-local", ...)
  │
  ├─ 1. registry.resolve("odoo19-local")           ← ~/.flightdeck/odoo.toml
  │       tên không có → {"error": ..., "known": [...]}   KHÔNG có default
  ├─ 2. guard.check_write(inst, model)             ← model ∈ write_models?
  │       không → refusal, KHÔNG gọi Odoo
  ├─ 3. confirm is True?                           ← không → refusal kèm mô tả việc sẽ làm
  ├─ 4. transport.login(inst)                      ← XML-RPC /xmlrpc/2/common authenticate → uid
  └─ 5. transport.execute(inst, uid, model, "write", [ids, values], transport="jsonrpc")
          │  POST http://127.0.0.1:8019/jsonrpc
          │  {"jsonrpc":"2.0","method":"call","params":{
          │     "service":"object","method":"execute_kw",
          │     "args":[db, uid, password, model, "write", [ids, values], {}]},"id":1}
          ▼
        Odoo  ──►  {"result": true}  |  {"error": {...}}  |  {}  ← None, cả hai khoá đều vắng
  │
  ▼
trả dict về dispatch → runtime.commit_quietly() → cli._emit(payload, 0 hoặc 2)
  │  đúng một JSON document trên stdout
  ▼
mcp_server đọc stdout, gói thành content[0].text
```

Ba tính chất đáng nhớ:

1. **Guard chạy trước khi chạm mạng.** Bước 2 và 3 xảy ra trước bước 4. Một lệnh ghi bị chặn không hề mở kết nối tới Odoo, nên phần "bị chặn" kiểm chứng được mà không cần instance sống.
2. **Không có gì sống qua mũi tên cuối.** Process chết, uid mất, transaction đóng.
3. **`runtime.configure()` vẫn mở DB FlightDeck** dù `odoo_*` không có bảng nào. Đó là chi phí chấp nhận (đo được là dưới một giây, chung với mọi tool khác). Domain này **không** thêm `store.init` của riêng nó.

---

## 6. Instance registry

### Định dạng

Hai file, tách bạch tên và giá trị:

**`~/.flightdeck/odoo.toml`** — `chmod 600`, ngoài git, **chỉ chứa tên biến env chứ không chứa mật khẩu**.

```toml
# ~/.flightdeck/odoo.toml   chmod 600
#
# Không có khoá "default". Đây là khác biệt cố ý so với odoo_config.multi.json
# của erpipe: một deeplink trỏ nhầm instance đã từng mở ra một bản ghi thật
# không liên quan mà không báo lỗi nào, vì hai instance trùng id. Một instance
# mặc định ngầm tái tạo đúng lớp sự cố đó ở tầng tool.

[instances.odoo12-local]
base_url      = "http://127.0.0.1:8069"
db            = "nakivo"
login         = "admin"                      # cần xác nhận trên bản restore
password_env  = "ODOO12_LOCAL_PASSWORD"      # TÊN biến, giá trị nằm trong env
write_models  = []                           # rỗng = read-only tuyệt đối
write_methods = []
note = """Bản restore ẩn danh của production, cron và mail đã tắt, KHÔNG có backup.
Không phải nguồn sự thật về trạng thái production. Ghi: không bao giờ."""

[instances.odoo19-local]
base_url      = "http://127.0.0.1:8019"
db            = "odoo_19_ce"
login         = "admin"
password_env  = "ODOO19_LOCAL_PASSWORD"
write_models  = ["res.partner"]              # ví dụ; sửa theo việc đang làm
write_methods = []
note = "Odoo 19 CE cho track subscription / pre-sale / HR. Sandbox riêng, dựng lại được."

[instances.staging-ce]
base_url      = "http://10.8.80.49:8269"
db            = "nakivoCE"
login         = "admin"
password_env  = "STAGING_CE_PASSWORD"
write_models  = []
write_methods = []
note = """nakivoCE — code CE đã de-coupled từ nhánh odoo12CE_legal, dữ liệu gần rỗng.
Môi trường DÙNG CHUNG với đồng nghiệp. Mặc định read-only; muốn ghi thì báo trước
rồi mới thêm model vào write_models."""
```

**`flight-deck.sh/.env`** — đã nằm trong `.gitignore` (dòng 151, đã kiểm), và `runtime._load_dotenv()` nạp sẵn cho mọi entrypoint CLI:

```
ODOO12_LOCAL_PASSWORD=...
ODOO19_LOCAL_PASSWORD=...
STAGING_CE_PASSWORD=admin
```

### Tại sao là password chứ không phải API key

Odoo 12 **không có** cơ chế API key (`res.users.apikeys` xuất hiện từ Odoo 14). Nên `odoo12-local` và `staging-ce` chỉ có một đường: login + password qua `/xmlrpc/2/common` `authenticate`. `odoo19-local` có thể dùng API key, và nên dùng ngay khi nào phải ghi — cùng ô `password_env`, chỉ đổi giá trị. Tool không cần biết khác biệt: cả hai đều đi vào cùng vị trí đối số thứ ba của `execute_kw`.

### Quy tắc phân giải

1. `instance` không truyền → registry của FlightDeck đã bắt lỗi từ tầng schema (`required`), CLI trả lỗi trước khi vào tool.
2. `instance` không có trong file → `{"error": "unknown instance 'x'", "known": ["odoo12-local", ...]}`. **Không bao giờ rơi về một instance nào khác.**
3. `password_env` trỏ tới biến không tồn tại → lỗi tầng auth, nói rõ tên biến còn thiếu, và **không** thử password rỗng.
4. Thiếu hẳn khoá `write_models` trong một entry → coi như `[]`, không phải "cho phép tất cả". Fail-closed áp cho cả cấu hình khuyết.

### uid: đăng nhập mỗi call, không cache

Mỗi call là một process mới, nên về nguyên tắc có hai lựa chọn: `authenticate` lại mỗi lần, hoặc cache uid ra đĩa kèm TTL.

**Chọn đăng nhập lại mỗi lần.** Lý do: password vẫn phải đi kèm trong **mọi** lời gọi `execute_kw` dù có uid sẵn hay không — Odoo không cấp session token cho đường RPC này. Nên cache uid chỉ tiết kiệm đúng một vòng gọi localhost, đổi lại phải quản một file state có TTL, có invalidate, và có khả năng đưa uid cũ vào một database đã bị dựng lại. Không đáng.

Nếu về sau đo thấy vòng `authenticate` thật sự đắt (chỉ có thể với `staging-ce` qua LAN), cache đặt tại `~/.flightdeck/odoo-uid-cache.json` (`chmod 600`), khoá là `(instance, db, login)`, TTL 15 phút, và **xóa entry ngay khi bất kỳ call nào trả lỗi tầng auth** — đó là điều kiện invalidate duy nhất cần thiết, vì database dựng lại luôn biểu hiện thành auth failure.

---

## 7. Mô hình bảo vệ ghi

### Ba tầng, fail-closed cả ba

```
tầng 0  Tool không tồn tại        odoo_unlink không có trong TOOLS       → CLI exit 3
tầng 1  Allowlist theo instance   model ∉ write_models                   → refusal, exit 2
        (hoặc model.method ∉ write_methods với odoo_call)
tầng 2  Xác nhận rõ ràng          confirm ≠ true                          → refusal, exit 2
```

Cả ba đều chạy **trước khi mở kết nối tới Odoo**. Sau ba tầng này, ACL của chính Odoo vẫn còn nguyên — user đăng nhập vẫn bị record rule và access right của nó ràng buộc như mọi user khác.

### Chặn cứng, không có cờ nào bật lên được

- **Không có tool xóa.** Không `unlink`, không `odoo_call` với `method="unlink"` — `unlink` nằm trong danh sách từ chối cứng của `odoo_call` bất kể `write_methods` khai gì. Đây không phải allowlist, đây là từ chối trước khi tra allowlist.
- **`write_models` rỗng nghĩa là read-only tuyệt đối**, và `odoo12-local` để rỗng vĩnh viễn. Nó là bản restore không backup.
- **Thiếu khoá cũng là rỗng.** Cấu hình khuyết không được diễn dịch thành cho phép.
- **`odoo_call` mặc định không gọi được method nào.** `write_methods` rỗng chặn hết. Một method chỉ vào danh sách khi có người đọc code của nó và ghi lý do vào `note` của entry.

### Hình dạng lời từ chối

Theo đúng khuôn `_need_confirm` của radar: một dict có khoá `error` ở cấp cao nhất, nên `cli.py` thoát mã 2 và agent đọc được như dữ liệu, không phải như crash.

Model không được phép ghi:

```json
{
  "error": "refused: writes to 'sale.order' are not allowed on instance 'odoo12-local'",
  "layer": "guard",
  "instance": "odoo12-local",
  "model": "sale.order",
  "write_models": [],
  "reason": "Bản restore ẩn danh của production, cron và mail đã tắt, KHÔNG có backup.",
  "confirmed": false
}
```

Được phép nhưng thiếu `confirm`:

```json
{
  "error": "refused: pass confirm=true to write {\"state\": \"done\"} onto 3 res.partner records on 'odoo19-local'",
  "layer": "guard",
  "instance": "odoo19-local",
  "model": "res.partner",
  "ids": [12, 15, 19],
  "values": {"state": "done"},
  "confirmed": false
}
```

Lời từ chối phải **nói ra chính xác việc gì sẽ xảy ra nếu gọi lại có `confirm`** — đúng cách `radar_delete` báo sẽ mất bao nhiêu blip và bao nhiêu move. Một lời từ chối không mô tả hậu quả thì lần gọi thứ hai chỉ là gõ thêm một tham số cho xong.

---

## 8. Xử lý lỗi — bốn lớp

Mọi hình dạng dưới đây đều có `error` ở cấp cao nhất, vì đó là điều kiện để `cli.py` thoát mã 2. Chi tiết phân lớp nằm ở các khoá anh em, đặc biệt là `layer`.

Registry đã có một `try/except` bọc ngoài biến mọi exception thành `{"error": "TypeError: ..."}`. Cái đó là lưới cuối cùng, không phải cơ chế — nó làm mất `layer`. Domain phải tự bắt và tự tạo hình bốn lớp này.

### Lớp 1 — mạng / transport

Instance không chạy, sai cổng, DNS hỏng, timeout ở tầng socket.

```json
{"error": "cannot reach instance 'staging-ce'", "layer": "transport",
 "instance": "staging-ce", "base_url": "http://10.8.80.49:8269",
 "detail": "ConnectionRefusedError: [Errno 111] Connection refused",
 "hint": "instance có thể đang tắt, hoặc không tới được từ máy này"}
```

### Lớp 2 — auth

`authenticate` trả `False` (Odoo báo sai credential kiểu này chứ không ném exception), hoặc `password_env` trỏ tới biến không tồn tại, hoặc `db` không có trên server.

```json
{"error": "authentication failed on 'odoo19-local'", "layer": "auth",
 "instance": "odoo19-local", "db": "odoo_19_ce", "login": "admin",
 "detail": "Odoo trả uid=False",
 "hint": "kiểm tra biến env ODOO19_LOCAL_PASSWORD và tên db"}
```

Ghi rõ tên biến env còn thiếu là hữu ích; **không bao giờ ghi giá trị của nó**, kể cả một phần.

### Lớp 3 — ACL của Odoo

`AccessError` và record rule. Phân biệt được bằng `data.name` trong envelope JSON-RPC: `serialize_exception` của Odoo trả về `{name, message, debug, arguments, exception_type}`, trong đó `name` là tên class đầy đủ (`odoo.exceptions.AccessError`).

```json
{"error": "Odoo từ chối quyền truy cập trên 'account.move'", "layer": "odoo_acl",
 "instance": "odoo19-local", "model": "account.move", "method": "write",
 "uid": 2, "odoo_exception": "odoo.exceptions.AccessError",
 "odoo_message": "Sorry, you are not allowed to modify this document."}
```

Đây là lớp mà lời khuyên **không** phải là "chạy bằng quyền cao hơn". Nếu ACL chặn, có nghĩa user đó không được làm việc đó, và guard của ta không phải chỗ để đi vòng.

### Lớp 4 — lỗi ORM

`ValidationError`, `UserError`, `MissingError`, vi phạm ràng buộc SQL, và mọi traceback khác. Cũng đọc từ `data.name`; những gì không rơi vào ba lớp trên thì vào lớp này.

```json
{"error": "Odoo từ chối thao tác trên 'sale.order'", "layer": "orm",
 "instance": "odoo19-local", "model": "sale.order", "method": "action_confirm",
 "ids": [42], "odoo_exception": "odoo.exceptions.UserError",
 "odoo_message": "Không thể xác nhận đơn hàng đã hủy.",
 "traceback_tail": ["...", "...", "..."]}
```

`traceback_tail` giữ tối đa 3 dòng cuối của `data.debug`. Đủ để lần ra file, không đủ để làm ngập output.

### Lớp 0 — guard refusal

`layer: "guard"` như §7. Khác ba lớp kia ở chỗ nó xảy ra **trước** khi có bất kỳ gói tin nào rời máy, nên nó nói được chắc chắn "không có gì thay đổi". Ba lớp kia không nói được điều đó — xem §10.

### Trường hợp riêng: `/jsonrpc` trả về rỗng

`{"jsonrpc":"2.0","id":1}` không có `result` và không có `error` là **thành công với kết quả `None`**, không phải lỗi. Tool trả `{"ok": true, "result": null}`. Đây là trường hợp mà `/xmlrpc/2/object` sẽ ném `TypeError` sau khi đã commit — chính lý do §2 tồn tại — nên nó phải có test riêng.

---

## 9. Kế hoạch test

### A. Unit — không cần Odoo

Chạy trong `pytest` bình thường của backend, không có marker.

| Test | Kiểm điều gì |
|---|---|
| `test_agentsurface` (đã có, chạy miễn phí) | `odoo_*` merge vào registry không đụng tên; **mọi tham số không default nằm trong `required`** — tức `instance` bắt buộc trên cả 6 tool chạm Odoo |
| Đọc registry | File thiếu → lỗi rõ ràng; entry thiếu `write_models` → coi là `[]`; tên instance lạ → error liệt kê tên hợp lệ; **không tồn tại đường nào rơi về instance mặc định** |
| Guard, với transport giả | model ∉ `write_models` → refusal, và **transport giả ghi nhận không có lời gọi nào**; `confirm` thiếu → refusal; `method="unlink"` → refusal kể cả khi có trong `write_methods` |
| Chọn transport | `odoo_search` gọi transport XML-RPC; `odoo_create` / `odoo_write` / `odoo_call` gọi transport JSON-RPC. Đây là bất biến của §2, phải có test chứ không phải chỉ có comment |
| `limit=0` không rơi xuống Odoo | `odoo_search` với `limit=0` → transport giả nhận `search_count`, **không** nhận `search_read`, và không nhận đối số `limit=0` nào. Nếu thiếu test này, Odoo sẽ trả nguyên bảng |
| Parse envelope JSON-RPC | `{}` (không result, không error) → `{"ok": true, "result": null}`; `{"error": {"data": {"name": "odoo.exceptions.AccessError"}}}` → `layer: "odoo_acl"`; `UserError` → `layer: "orm"` |
| Không rò credential | Với mọi lời từ chối và mọi lỗi ở bốn lớp, output không chứa giá trị password. Assert trực tiếp trên chuỗi JSON |

### B. Integration — chạy thật

Marker `@pytest.mark.odoo_live`, tự bỏ qua khi instance không tới được (thăm dò bằng `odoo_ping`). Chạy vào **`odoo19-local`** — sandbox của riêng ta, dựng lại được. `staging-ce` chỉ dùng cho các test đọc, vì là môi trường dùng chung.

| Test | Nội dung |
|---|---|
| `odoo_ping` | Trả về `19.0` và một uid là số nguyên |
| `odoo_fields` trên `res.partner` | Có `name`, `email`; `name` là `char` |
| `odoo_search` trên `res.partner` | `limit=5` trả tối đa 5 dòng; domain sai cú pháp → `layer: "orm"`, không phải crash |
| Vòng ghi trọn vẹn | `odoo_create` một `res.partner` tên `flightdeck-mcp-test-<uuid>` → `odoo_search` thấy nó → `odoo_write` đổi `comment` → `odoo_search` thấy giá trị mới. Bản ghi test để lại trên sandbox và nêu trong `TEMP_FILES_SHOULD_BE_REMOVE.MD` — dòng database không xóa bằng tool được |
| `odoo_call` trả `None` | Gọi qua `/jsonrpc` một method trả `None` (ví dụ `res.partner.write` gói trong một method không return, hoặc `mail.thread.message_subscribe`), khẳng định nhận `{"ok": true, "result": null}` **và** khẳng định thay đổi đã vào database. Đây là test chứng minh §2 giải quyết đúng sự cố đã trả giá |
| Đối chứng XML-RPC | Cùng method đó gọi qua `/xmlrpc/2/object` phải ném `TypeError` về marshalling **trong khi database vẫn đổi**. Test này ghi lại lý do tồn tại của cả thiết kế; nếu một ngày nó ngừng fail thì có thể bỏ split transport |

### C. Live negative test — bắt buộc trước khi coi là xong

Quy ước của workspace: guard destructive phải fail-closed **và** có test thử làm hành động bị chặn. Năm test, mỗi test kiểm chứng theo hai chiều — lệnh bị từ chối, **và** database không đổi.

| # | Thử | Phải nhận | Kiểm chứng độc lập |
|---|---|---|---|
| N1 | `odoo_write` lên `res.partner` của **`odoo12-local`** (`write_models = []`) | exit 2, `layer: "guard"`, `confirmed: false` | Đọc lại bản ghi qua XML-RPC trực tiếp (không qua tool): `write_date` không đổi |
| N2 | `odoo_call` với `method="action_confirm"` khi `write_methods = []` | exit 2, `layer: "guard"` | Trạng thái bản ghi không đổi |
| N3 | `odoo_call` với `method="unlink"` khi `write_methods` **có chứa** `unlink` | exit 2 — từ chối cứng thắng allowlist | Bản ghi vẫn tồn tại |
| N4 | `flightdeck odoo_unlink --instance odoo19-local ...` | **exit 3** (unknown tool) | Tool này không có, không phải bị tắt |
| N5 | `odoo_write --instance odoo12-locall` (gõ sai một ký tự) | exit 2, error liệt kê tên hợp lệ | **Không có instance nào bị chạm** — kiểm bằng access log của cả ba |

N5 là test trực tiếp cho sự cố deeplink: sai tên phải là một lời từ chối, không bao giờ là một thao tác lên nơi khác.

Đủ điều kiện "xong" của phase 2 (ghi): năm test này chạy thật, xanh, và kết quả dán vào doc này.

---

## 10. Ước lượng

| Giai đoạn | Việc | MD |
|---|---|---|
| P0 | Registry cấu hình (`~/.flightdeck/odoo.toml`), phân giải instance, transport seam `_execute()` cho cả XML-RPC và `/jsonrpc`, phân lớp lỗi bốn tầng | 2.0 |
| P1 | Bốn tool đọc: `odoo_instances`, `odoo_ping`, `odoo_fields`, `odoo_search`. Thêm một dòng vào `_DOMAINS`. Unit test nhóm A | 1.5 |
| P2 | Guard ghi (ba tầng + từ chối cứng), ba tool ghi: `odoo_create`, `odoo_write`, `odoo_call` | 2.0 |
| P3 | Integration test nhóm B + **năm live negative test nhóm C** trên instance thật | 1.5 |
| P4 | Skill `odoo-live` (khi nào dùng `odoo_*` / `odoo_graph` / `scripts/`), cập nhật `.mcp.json` nếu cần, ghi `MEMORY.md` | 0.5 |
| | **Tổng** | **7.5** |

Làm tròn: **7–8 MD**. Nếu chỉ làm phần đọc (P0+P1+phần A của P3) thì **3.5–4 MD**, và đó là một mốc giao được: hành lang đọc live nhiều instance đã tự nó có ích, phần ghi bật lên sau bằng cách thêm model vào `write_models`.

---

## 11. Rủi ro và giới hạn

**Timeout 120 giây, và điều nó không nói được.** Wrapper trả `{"error": "the tool call timed out after 120s"}` rồi giết process. Với một lệnh đọc, đó chỉ là phiền. Với một lệnh ghi, đó là **trạng thái không biết**: không có cách nào từ phía tool phân biệt "Odoo chưa nhận" với "Odoo đã commit rồi nhưng response chưa về". Ba biện pháp: giữ lệnh ghi nhỏ (đừng `write` lên hàng nghìn id trong một call); sau bất kỳ timeout nào của lệnh ghi, **đọc lại để xác định thực tế** trước khi thử lại; và ghi thẳng vào mô tả tool rằng timeout ≠ "không có gì xảy ra".

**Không có transaction xuyên nhiều call.** `odoo_create` rồi `odoo_write` rồi `odoo_call` là ba transaction riêng, ba process riêng. Không có rollback. Nếu bước hai hỏng, bước một đã nằm trong database. Hệ quả thực tế: **một luồng nghiệp vụ nhiều bước phải nằm gọn trong một `odoo_call` tới một method tự lo transaction của nó**, không được ghép từ ba lời gọi tool. Đây là giới hạn kiến trúc, không phải thứ vá được bằng code trong domain này.

**`odoo12-local` là bản restore không có backup**, và là Enterprise (`12.0+e`). `write_models` để rỗng vĩnh viễn là biện pháp duy nhất tương xứng. Nó cũng chạy Python 3.7 phía server — không ảnh hưởng code của ta (chạy trên máy host), nhưng có nghĩa mọi method gọi tới đều là code cũ và không có test.

**Nó cũng không phải nguồn sự thật về production.** Cron tắt, mail tắt, snapshot cũ. Một câu trả lời từ `odoo_search` trên `odoo12-local` nói về *bản restore*, không nói về hệ thống thật. Mô tả của mọi tool phải nhắc điều này, vì tool đọc live rất dễ bị nhầm là oracle của production.

**`staging-ce` dùng chung.** Ghi vào đó ảnh hưởng người khác. Mặc định rỗng, và thêm model vào `write_models` là một hành động phải báo trước.

**Password nằm trong env.** Không có API key cho Odoo 12. Bất cứ process nào của cùng user đọc được `/proc/<pid>/environ`. Với một máy dev một người dùng thì chấp nhận được; nó không phải mô hình đem sang chỗ nhiều người dùng chung được.

**`/jsonrpc` là đường bị đánh dấu deprecated.** README của erpipe ghi XML-RPC và JSON-RPC bị deprecated từ Odoo 19 và dự kiến gỡ ở Odoo 22 (khoảng cuối 2028) — đây là phát biểu của họ, chưa xác minh trên tài liệu Odoo. Đo trực tiếp ngày 2026-08-28 thì cả hai vẫn trả 200 trên `19.0-20260723`. Khi nào thật sự gỡ, đường thay thế là External JSON-2 (`/json/2/...`) mà erpipe đã có sẵn code — lúc đó `_execute()` mọc thêm một nhánh, các tool không đổi.

**Chênh version giữa 12 và 19 trên đường đọc.** `read_group` đổi tên ở các bản mới; đó là lý do `odoo_groups` bị hoãn sang phase 2. `fields_get` và `search_read` thì ổn định trên cả hai, nên bốn tool đọc phase 1 không có rủi ro này.

**Rủi ro marshalling còn sót trên đường đọc.** Về lý thuyết `search_read` chỉ trả list/dict và Odoo dùng `False` chứ không dùng `None`, nên XML-RPC an toàn. Nếu thực tế khác — dù chỉ một lần — biện pháp là bỏ XML-RPC và cho tất cả đi `/jsonrpc`. `_execute()` được thiết kế để việc đó là sửa một giá trị mặc định.

**erpipe không test Odoo 12.** Ma trận test của họ là 16–19. Mọi kiến thức mượn từ code họ, khi áp lên 12, phải kiểm lại trên chính instance 12 — đó là một phần lý do nhóm test B tồn tại.

**Chi phí `runtime.configure()`.** Mỗi call `odoo_*` vẫn mở database FlightDeck và init store của radar + treasures, dù domain này không dùng bảng nào. Chấp nhận: nó là chi phí chung của mọi tool trên surface, đo được là dưới một giây. Domain này không thêm store init của riêng nó, nên không làm chi phí đó tăng thêm.

---

## Phụ lục — những gì đã kiểm chứng trực tiếp

| Khẳng định | Cách kiểm | Kết quả |
|---|---|---|
| XML-RPC của Odoo 12 dùng `allow_none=False` | Đọc `OCB/odoo/addons/base/controllers/rpc.py` (OCB 12.0, `release.py` xác nhận `version_info = (12, 0, ...)`) | `dumps((result,), methodresponse=1, allow_none=False)`, áp cho cả `/xmlrpc/` và `/xmlrpc/2/` |
| `/jsonrpc` trả `None` thành response không có `result` | Đọc `OCB/odoo/http.py:631-639` | `if result is not None: response['result'] = result` |
| Envelope lỗi JSON-RPC có tên class exception | Đọc `OCB/odoo/http.py:660-683` | `data: serialize_exception(exception)`, trường `name` |
| `/jsonrpc` sống trên cả hai instance local | POST `common.version` ngày 2026-08-28 | 8069 → `12.0+e`; 8019 → `19.0-20260723`, cả hai HTTP 200 |
| `/xmlrpc/2/common` sống trên cả hai | POST `version` | Cả hai HTTP 200 |
| erpipe là MIT | `curl` file `LICENSE` | MIT, © 2025 Lê Anh Tuấn |
| erpipe không dùng `/jsonrpc` | `grep` trên `src/odoo_mcp/odoo_client.py` | Chỉ có `/xmlrpc/2/common`, `/xmlrpc/2/object`, và JSON-2 |
| erpipe có khoá `default` cho multi-instance | `odoo_config.multi.json.example` | `{"default": "acme", "instances": {...}}` |
| Bảo trì của hai repo | GitHub API `/repos/...` và `/commits?per_page=1` | erpipe: 2026-08-25, 398 star, 6 issue · ivnvxd: 2026-08-26, 377 star, 13 issue |
| ivnvxd cần module 19.0+ | README | Standard mode cần module `mcp_server`; nếu không thì `ODOO_YOLO` bỏ ACL |
| `db_name` của các instance | `nakivo_local_config/odoo.conf`, `odoo-19/odoo.conf`, memory `staging-nakivo-ce` | `nakivo` · `odoo_19_ce` · `nakivoCE` |
| `.env` của FlightDeck ngoài git | `git check-ignore -v .env` | Trúng `.gitignore:151` |

**Chưa xác minh, cần làm trước khi code**: tên login và password thật của `odoo12-local` trên bản restore ẩn danh (đã ẩn danh thì `admin` có thể đã đổi). Không đoán trong file cấu hình — thăm dò rồi mới điền.
