# Ba domain MCP cho FlightDeck — thiết kế cuối

`ssh_*` · `odoo_*` · `memory_*`

Tài liệu này chốt thiết kế cho cả ba hướng đã bàn, và quan trọng hơn: chốt **phần chung** mà cả ba dùng lại. Chi tiết đầy đủ của domain Odoo nằm ở [`odoo-mcp-domain-design.md`](odoo-mcp-domain-design.md) — ở đây chỉ nêu những quyết định đã chốt, không chép lại.

---

## 0. Kết luận

Ba domain, dựng trong FlightDeck chứ không adopt server ngoài, không tách repo riêng. Lý do đã ghi thành blip `homeground` #4 (*MCP nội bộ dựng trong FlightDeck*, ring Assess).

| Domain | Mở hành lang gì | Công sức | Rủi ro |
|---|---|---|---|
| `memory_*` | Đọc/ghi kho memory cục bộ | ~4.5 ngày | Thấp — bốn tool đầu chỉ đọc |
| `odoo_*` | Đọc/ghi live vào Odoo 12/19 | 7–8 ngày | Trung bình — có ghi thật |
| `ssh_*` | Chạy lệnh trên server từ xa | 4–5 ngày | Cao nhất — cần lớp duyệt |

**Thứ tự dựng: memory → odoo → ssh.** Không phải theo giá trị mà theo **rủi ro tăng dần**: memory chỉ đọc file cục bộ nên sai cũng không mất gì; odoo có ghi nhưng trong hàng rào allowlist; ssh chạm máy khác và cần cả một lớp duyệt ngoài phiên. Dựng theo thứ tự này thì khuôn chung được kiểm chứng trên domain rẻ nhất trước.

---

## 1. Phần chung — bảy ràng buộc mọi domain phải theo

Đây là phần chưa được viết ở đâu, và là lý do ba domain này nên sống chung một chỗ.

### 1.1 Một domain = một dòng trong registry

`backend/flightdeck/agentsurface/registry.py` giữ bảng `_DOMAINS`: tiền tố tên → module chứa bảng `TOOLS`. Thêm domain là thêm một dòng, cộng một module có `TOOLS = {name: (fn, description, schema_props, required)}`.

Tool viết một lần, hiện ở **ba mặt**: CLI (`flightdeck <tool>`), MCP (wrapper spawn CLI), và HTTP router. Không viết ba lần.

### 1.2 Mỗi call là một process mới — ba hệ quả không thương lượng

Wrapper MCP cố ý spawn lại CLI mỗi lần gọi, để một fix code tới được mọi phiên đang chạy mà không cần restart.

1. **Không có state trong process.** Không connection pool, không session cache, không approval token sống qua nhiều call. Thứ cần bền phải nằm trên đĩa hoặc trong một process riêng không mang code của mình.
2. **Trần 120 giây.** Việc dài hơn phải chuyển sang một cơ chế khác, không được nhồi vào một call.
3. **Không có elicitation.** Không có process sống để hỏi ngược người dùng giữa chừng. Mọi cơ chế xin phép phải đi qua contract lỗi-là-dữ-liệu ở §1.3.

### 1.3 Contract: lỗi là dữ liệu

Một JSON document trên stdout, luôn luôn. Mã thoát: `0` thành công · `2` tool từ chối (lỗi nằm trong payload, agent đọc được và xử lý được) · `3` tool không tồn tại · `1` crash.

**Mã 2 là xương sống của mọi cơ chế bảo vệ trong tài liệu này.** Một thao tác bị chặn không ném exception — nó trả về một object mô tả chính xác vì sao bị chặn và đường đi tiếp là gì.

### 1.4 Khuôn cổng ghi: allowlist + `confirm` trong một call

Cả ba domain dùng chung một hình dạng, vì spawn-per-call không cho phép hình dạng nào khác:

```
tool(..., confirm=False)
  → đối tượng không nằm trong allowlist   → mã 2, từ chối cứng
  → nằm trong allowlist nhưng confirm=False → mã 2, mô tả chính xác việc sẽ xảy ra
  → nằm trong allowlist và confirm=True     → chạy, ghi ledger
```

`confirm` để **mặc định `False`**, không đưa vào `required`. Nếu đưa vào `required` thì `confirm=false` vẫn hợp lệ và cổng chẳng chặn gì.

### 1.5 Credential: env giữ giá trị, file giữ tên biến

`~/.flightdeck/<domain>.toml`, chmod 600, **không bao giờ vào git**. File config chỉ chứa **tên biến môi trường** (`password_env = "SSH_STAGING_CE_PASS"`), giá trị đọc từ env lúc chạy. Một file config lộ ra ngoài không lộ mật khẩu nào.

### 1.6 Nhật ký vào ledger Postgres, không ra file rời

Mỗi call có tác dụng phụ ghi một dòng: thời điểm, domain, tool, đối tượng, quyết định (allow / escalate / deny), kết quả, session id. Giá trị nhạy cảm thay bằng `***`.

Đây là lý do kỹ thuật mạnh nhất để ba domain sống trong FlightDeck: nhật ký nằm chung một sổ với phần còn lại của hệ, và có sẵn giao diện đọc nó.

### 1.7 Fail-closed, và định nghĩa "xong" gồm một test âm chạy thật

Không khớp luật nào = từ chối, không phải cho qua. Và theo quy ước workspace: **mọi guard chỉ được coi là xong sau khi chạy thật một thao tác bị chặn và chứng minh nó bị chặn.** Đọc code rồi tin là chưa xong.

Mỗi domain bên dưới đều liệt kê test âm của nó.

---

## 2. `ssh_*` — chạy lệnh trên server từ xa

### 2.1 Kết nối: `ssh` hệ thống + ControlMaster, không phải paramiko

Lần `ssh` đầu tạo một **unix socket trên đĩa** và tách ra chạy nền làm master. Các lệnh sau ghép kênh vào kết nối TCP đã xác thực sẵn — chi phí còn dưới 10ms thay vì 200–600ms bắt tay lại. `ControlPersist=60s` giữ master sống 60 giây sau client cuối, nên trạng thái **sống xuyên qua các lần spawn** mà không nằm trong process của mình.

Ranh giới đáng nhớ: **thứ được phép sống lâu là đường truyền, thứ không được phép sống lâu là logic ra quyết định.** Master của `ssh` không mang một dòng code nào của mình — nó giữ một socket. Policy vẫn được đọc lại từ đĩa mỗi call. Một daemon paramiko tự viết thì giữ cả hai, và nửa sau sẽ đóng băng.

Cấu hình: `ControlPath` chứa `%C`, đặt trong `~/.flightdeck/cm/`, mode 600. `ssh -O check` trước mỗi call; hỏng thì `-O exit` rồi mở lại.

*Chưa đo thật:* con số 60s là chọn theo lý thuyết. Máy này không chạy sshd nên chưa có host để thử. **Đo handshake lạnh so với qua socket, trên đường tới staging, là việc đầu tiên khi bắt đầu.**

### 2.2 Ba luồng thao tác

| Luồng | Ví dụ | Xử lý |
|---|---|---|
| One-shot ngắn | `systemctl status`, `docker ps`, `df -h` | `ssh_run`, vừa trong 120s |
| Chạy dài, không cần người | `docker compose up -d --build`, `odoo -u all`, `pg_dump` | **tmux trên server** — sống độc lập với kết nối, laptop sleep hay VPN rớt vẫn chạy |
| Tương tác | `psql`, `odoo shell`, lệnh hỏi y/n | tmux trên server + người tự `ssh -t host tmux attach` |

Fallback `nohup` + log file cho host không có tmux. Phần tmux **cục bộ** đã có `tmux-mcp` và skill `tmux-interactive-sessions` — domain này chỉ lo phần remote.

### 2.3 Session ghi-một-lần — đóng đường vòng qua policy

`tmux send-keys` là một đường vòng qua toàn bộ policy engine: agent gọi `ssh_run "tmux send-keys -t sess 'rm -rf /' Enter"` thì policy chỉ thấy chuỗi bắt đầu bằng `tmux`.

Cách đóng: **lệnh được duyệt tại lúc tạo session**, và sau đó session là **chỉ-đọc** với agent — chỉ có `capture-pane`, không có `send-keys`. Muốn gõ thêm thì con người tự attach. `tmux` và `send-keys` nằm trong danh sách chặn cứng của `ssh_run`.

### 2.4 Bộ tool — 7

| Tool | Đối số | Lớp |
|---|---|---|
| `ssh_hosts` | — | đọc |
| `ssh_run` | `host`, `command`, `timeout?`, `approve?` | theo policy |
| `ssh_session_start` | `host`, `command`, `name` | theo policy |
| `ssh_session_list` | `host` | đọc |
| `ssh_session_read` | `host`, `name`, `lines?` | đọc |
| `ssh_session_kill` | `host`, `name` | ghi — chỉ session tiền tố `fd-` |
| `ssh_audit` | `host?`, `since?`, `decision?` | đọc |

Cố ý không có: tool copy/rsync riêng, tool backup/database/deploy, tool sudo riêng (sudo là một lớp policy, không phải một tool).

### 2.5 Policy — bốn trạng thái

```
allow     → chạy ngay, ghi ledger
escalate  → từ chối kèm ticket, có đường xin phép
deny      → từ chối cứng, không có đường vòng
unknown   → coi như escalate        ← fail-closed
```

```toml
# ~/.flightdeck/hosts.toml   chmod 600
[hosts.staging-ce]
hostname     = "10.8.80.49"
user         = "root"
password_env = "SSH_STAGING_CE_PASS"     # chỉ TÊN biến
mode         = "escalate"
allow = ['^(systemctl status|journalctl) ', '^docker (ps|logs|inspect)\b',
         '^(ls|cat|head|tail|df|free|uptime|ps)\b', '^git (status|log|diff)\b']
deny  = ['\brm\s+-rf\b', '\bdd\b', 'docker compose\s+down\b.*\s-v\b',
         '\btmux\b', '\bmkfs\b', '>\s*/dev/[sn]d', '\bshutdown\b|\breboot\b']
tmux  = true
```

Thứ tự: `deny` trước → `allow` → còn lại rơi vào `escalate`. Lệnh **chuẩn hoá trước khi khớp** (bỏ khoảng trắng thừa, khai triển alias) — `mcp-ssh-manager` đã vấp và sửa đúng chỗ này.

Chặn cứng ở tầng code, không cấu hình được: `tmux`/`send-keys`, và mọi chuỗi chứa `;` `&&` `|` `$(` hoặc backtick **khi host ở mode `escalate`** — policy khớp regex trên một lệnh, không parse được cả pipeline.

### 2.6 Escalate: duyệt ngoài phiên

**Giả định đã chốt** *(nêu làm mặc định khi chốt phạm vi tài liệu và được chấp nhận cùng lúc đó, chứ chưa phải một lựa chọn được cân nhắc riêng; nếu muốn đổi sang duyệt-trong-chat thì §2.6 là phần duy nhất phải viết lại)*:

Ticket vào **hàng đợi trong FlightDeck UI**, người duyệt ở tab riêng, tool poll tới khi thấy duyệt. Chậm hơn duyệt-trong-chat khoảng một ngày công cho UI + endpoint, nhưng **agent không tự vượt được**.

Lý do chọn: nếu "phê duyệt" chỉ là agent gọi lại kèm `--approve`, agent tự gật cho chính nó được — tầng `escalate` khi đó không khác `allow` kèm một câu hỏi lịch sự, và lý do mạnh nhất để domain này sống trong FlightDeck cũng biến mất.

### 2.7 Test âm bắt buộc

Chạy thật `ssh_run` với một lệnh trong `deny` và chứng minh bị chặn. Chạy thật `ssh_run "tmux send-keys ..."` và chứng minh bị chặn cứng. Chạy thật một lệnh `escalate` và chứng minh nó **không** chạy khi chưa có duyệt trong hàng đợi.

---

## 3. `odoo_*` — hành lang đọc/ghi live

Chi tiết đầy đủ: [`odoo-mcp-domain-design.md`](odoo-mcp-domain-design.md) (533 dòng). Ở đây là những gì đã chốt.

**Viết mới, không fork.** Dưới 10% code của `erpipe-org/mcp-odoo` dùng lại được. Tầng transport của họ không có đường `/jsonrpc` nào — mà đó là đường bắt buộc cho mọi thao tác ghi ở đây. Cổng kiểm soát ghi của họ chuyền một approval token qua nhiều call, thứ spawn-per-call không giữ được. License MIT nên lấy được **hình dạng** file policy của họ, không phải code.

**Ranh giới transport: đọc XML-RPC, ghi `/jsonrpc`.** XML-RPC không marshal được `None`, nên một method trả `None` sẽ raise **sau khi write đã commit** — agent thấy lỗi trong khi database đã đổi. Và một bẫy nữa đã kiểm chứng trong source: trên Odoo 12, `/jsonrpc` khi method trả `None` trả về `{"jsonrpc":"2.0","id":1}` — **không có `result`, cũng không có `error`**; client viết `resp["result"]` sẽ ném `KeyError` đúng vào ca mà việc đổi transport sinh ra để chữa.

**Bảy tool phase 1:** `odoo_instances`, `odoo_ping`, `odoo_fields`, `odoo_search` (đọc) · `odoo_create`, `odoo_write`, `odoo_call` (ghi). 34 tool của erpipe bị bỏ kèm lý do từng cái.

**`odoo_unlink` cố ý không tồn tại, kể cả sau này.** Xóa kéo theo cascade qua `ondelete` mà tool không nhìn thấy trước, và `odoo12-local` là bản restore không có backup. Test âm: gọi `flightdeck odoo_unlink` phải thoát **mã 3**.

**Registry theo tên, không có khoá `default`.** Ba instance: `odoo12-local` (`write_models` **rỗng** — chỉ đọc tuyệt đối), `odoo19-local`, `staging-ce`. Mọi tool chạm Odoo bắt buộc nhận `instance=`. Lý do là một sự cố thật: một deeplink trỏ nhầm instance mở ra một bản ghi thật không liên quan mà **không báo lỗi**, vì hai instance trùng id.

**Vị trí so với thứ đã có:** `odoo_graph` = cấu trúc tĩnh · `scripts/` = probe read-only cố định · `odoo_*` = hành lang live có kiểm soát. Ba thứ không chồng nhau.

**Một việc phải làm trước khi code:** dò login/password thật của `odoo12-local` trên bản restore ẩn danh.

**Ước lượng: 7–8 MD.** Riêng phần chỉ-đọc là 3.5–4 MD và ship được độc lập.

---

## 4. `memory_*` — chỉ mục có khẳng định, đứng trên kho nguyên bản

### 4.1 Nguyên tắc

Ký ức người là **tái dựng**, không phải bản ghi: nội dung, ngữ cảnh và nguồn đều có thể suy giảm, trộn lẫn, gán nhầm. Đặc tính đó cho phép khái quát hoá và liên tưởng, nhưng khiến trí nhớ không dùng làm hệ bằng chứng được.

Một hệ AI vận hành không cần sao chép giới hạn đó. **Tách hai chức năng mà não trộn vào nhau: liên tưởng để tìm, bằng chứng để biết.** Kho nguyên bản giữ những gì thực sự đã tồn tại; tầng memory tạo đường dẫn liên tưởng tới kho đó. Retrieval được phép gần đúng, reasoning được phép suy luận — nhưng khi một khẳng định cần được xem là fact, hệ phải quay về được provenance nguyên bản.

Chính xác hơn một nửa bậc: **memory là một khẳng định *cộng* một đường dẫn.** Khẳng định là phần có giá trị nén (đọc lại transcript gốc không cho ra kết luận đã chưng cất); đường dẫn là thứ khiến khẳng định **bác bỏ được**. Bỏ đường dẫn còn tin đồn; bỏ khẳng định còn một cái bookmark.

### 4.2 Đo được gì trên kho hiện tại

| Phép đo | Kết quả |
|---|---|
| File memory / index | 37 file + `MEMORY.md` (~88KB nội dung thật) |
| Transcript cạnh đó | **1.2GB** — tỷ lệ ~1 : 14.000 |
| Wikilink | 48; phần lớn node degree 0–3, hub duy nhất `nakivo-local-docker-setup` (6 vào, 0 ra) |
| Type | `reference` 16 · `project` 11 · `feedback` 10 · `user` 0 |
| **File từng được đọc toàn văn** | **6 / 37** (15 lần đọc trên 69 transcript, 7 trong đó là chính `MEMORY.md`) |
| **Provenance còn sống** | **27 / 37**; **10 file** trỏ tới transcript đã mất |
| Lệch cấu trúc | 1 mồ côi khỏi index · 3 wikilink gãy · 3 cô lập · 0 link index chết |

Hai con số đáng đặt lên đầu màn hình: **6/37 chưa từng được đọc** và **10/37 đứt nguồn**. Một file mồ côi là một lỗi; ba mươi mốt file chưa từng được đọc là một câu hỏi về thiết kế truy xuất.

### 4.3 Vì sao truy xuất hỏng: một kênh gợi ý duy nhất

Não truy xuất **theo nội dung** — hoàn thiện mẫu từ gợi ý bộ phận và gián tiếp, kích hoạt lan truyền tự động, không có kho memory riêng (mọi thứ từng đọc vừa là gợi ý vừa là đích). Harness truy xuất **theo địa chỉ**: một kênh gợi ý (dòng `description` trong index), một thư mục có tường bao, và recall chỉ xảy ra khi model **quyết định** gọi `Read`.

6/37 là hệ quả đo được của khác biệt đó: memory nào có mô tả không khớp câu hỏi thì không có đường thứ hai để được tìm ra.

### 4.4 Bộ tool — 4 ở phase 1

| Tool | Trả lời câu gì | Lớp |
|---|---|---|
| `memory_lint` | Kho có đang trôi không, trôi ở đâu, sửa thế nào | đọc |
| `memory_search` | Tôi đã ghi gì về X | đọc |
| `memory_graph` | Những gì tôi biết nối với nhau ra sao, chỗ nào đứt | đọc |
| `memory_history` | Memory này đổi ra sao và vì sao | đọc |

Bốn tool **chỉ đọc**, nên không đụng vào thứ harness đang sở hữu — rủi ro gần bằng không.

**`memory_add` hoãn sang phase 2**, vì một câu chưa trả lời được: harness đóng dấu `node_type` / `originSessionId` / `modified` bằng cách **chặn tool `Write`**. Một `memory_add` chạy trong CLI ghi qua subprocess Python thì harness không thấy, nên các trường đó nhiều khả năng không được đóng dấu. Phải thử thật mới biết.

**`memory_consolidate` cố ý không tồn tại.** autoDream đã nằm trong bản build công khai và có thể được bật bất kỳ lúc nào bằng cờ từ xa; tự viết bản hợp nhất riêng là đặt hai tiến trình cùng sửa một `MEMORY.md`.

### 4.5 `memory_lint` — tám luật, xếp theo sức nặng

| # | Luật | Số hiện tại | Vì sao |
|---|---|---|---|
| 1 | Memory `reference` không có `source` phân giải được | (chưa có trường) | **Không kiểm chứng được** — nặng hơn "cũ" |
| 2 | `originSessionId` trỏ tới transcript đã mất | **10** | Hạ cấp bậc bằng chứng |
| 3 | Mô tả trùng lặp gần nhau hoặc quá tổng quát | chưa đo | Quá tải gợi ý: một gợi ý gắn với quá nhiều mục thì không truy xuất được mục nào |
| 4 | File có trên đĩa nhưng không trong index | **1** | Vĩnh viễn vô hình |
| 5 | Wikilink trỏ tới memory không tồn tại | **3** | Kèm gợi ý fuzzy-match khi có |
| 6 | File không có link vào lẫn link ra | **3** | |
| 7 | Link trong index trỏ tới file không tồn tại | **0** | |
| 8 | Chưa từng được đọc (cột `Used`) | **31** | Tín hiệu giữ-hay-bỏ đúng, thay cho tuổi |

**Cột `Used` thay cột tuổi làm tín hiệu chính.** Việc truy xuất củng cố dấu vết mạnh hơn việc đọc lại; thứ quyết định một ký ức còn hay mất là tần suất được dùng, không phải nó bao nhiêu tuổi. Một fact hai tháng tuổi dùng hằng tuần mạnh hơn một fact mới toanh chưa ai chạm. Đếm được bằng cách quét transcript tìm `Read`/`Grep` trỏ vào đường dẫn memory. Tuổi vẫn hiển thị nhưng tụt xuống hàng phụ, và dùng **ngưỡng rời rạc + icon** (`fresh` <7 ngày · mặc định 7–30 · `stale` >45), không dùng thang màu liên tục.

**Một luật parse phải chốt trước khi code.** `claude-usage-cap-and-fable-fallback.md` có `[[fable5-persona project|fable5-persona]]` — target chứa khoảng trắng, trỏ tới thứ không tồn tại. Parse thẳng thì đó là link gãy **thứ tư** (3→4); không tính là link thì file đó thành node cô lập **thứ tư** (3→4). Con số 3/3 chỉ đúng khi tool coi nó *là* link nhưng *không* báo gãy. **Đề xuất: nhãn riêng `Points to something that does not exist yet`**, không gộp vào nhóm gãy.

### 4.6 Provenance phải phân loại, không áp đều

| Type | Số | Nguồn thật ở đâu | Yêu cầu |
|---|---|---|---|
| `reference` | 16 | Hệ thống sống — code, DB, config | **Bắt buộc.** Rữa nhanh nhất, `file:line` mục nát ở đây |
| `project` | 11 | Một quyết định; có thể có ticket | Trỏ ticket/doc nếu có; nếu không, memory **là** bản ghi duy nhất |
| `feedback` | 10 | Một câu người dùng nói; transcript **là** nguồn | Không có sự thật ngoài để đối chiếu, không rữa theo code |

Áp một luật provenance đồng loạt cho cả 37 file sẽ đẻ ra rất nhiều cảnh báo vô nghĩa trên nhóm không có gì để verify — đúng kiểu nhiễu làm người ta tắt lint.

**Hai trường mới trong frontmatter:**

- `source` — **kiểm được bằng máy**, không phải văn xuôi. Bốn dạng: đường dẫn file + git sha · mã ticket · URL · `session-id + từ khoá grep`. Dạng cuối vá điểm yếu của `originSessionId`: nó trỏ tới **một phiên**, không trỏ tới **một chỗ trong phiên**, nên ngay cả 27 con trỏ còn sống cũng chỉ đưa tới một file JSONL hàng chục MB.
- `tier` — bậc trên thang bằng chứng lúc memory được tạo, theo đúng thang đã có trong `AGENTS.md`: *chính sách từ đội quyết định, hoặc production/live > DB hiện tại > staging > bản restore cục bộ > tài liệu > suy luận của agent > cảm nhận*. Một memory `reference` ở bậc "bản restore cục bộ" **không được** dùng để khẳng định điều gì về production — và đó là luật máy kiểm được, không phải điều agent phải nhớ.

**Hạ cấp, không xoá.** Một memory mất nguồn không phải rác; nó tụt xuống bậc "suy luận của agent" và mất quyền được trích như fact. Đây cũng là hàng rào cho autoDream: xoá vì "mâu thuẫn" là quá tay, **hạ bậc vì mất nguồn** mới là hành động đúng.

### 4.7 `memory_search` — mở rộng một bước láng giềng

Grep toàn văn, cộng một **index phái sinh xoá-dựng-lại-được** (SQLite FTS trước; ngữ nghĩa cục bộ qua Ollama sau, nếu cần). Nguyên tắc: **markdown là bản gốc, mọi index chỉ là sản phẩm phái sinh** — FlightDeck không bao giờ sở hữu tầng lưu trữ.

Điểm mới so với bản đầu: **khi một memory được trả về, đưa luôn dòng mô tả của các láng giềng trực tiếp vào cùng kết quả.** Rẻ (chỉ là mô tả một dòng) và biến 48 wikilink từ trang trí thành cơ chế truy xuất thật — phiên bản máy của kích hoạt lan truyền.

Kết quả search **là một danh sách**, không lọc trên một graph nền — tránh chi phí giữ hai view đồng bộ.

### 4.8 `memory_graph` — danh sách trước, graph sau

Nghiên cứu kết luận **không dùng force-directed graph làm hình thái chủ đạo**: ở 37 node degree 0–3, không có cụm để tách; nghiên cứu kiểm soát Ghoniem/Fekete/Castagliola cho thấy từ ~20 node trở lên matrix thắng node-link ở hầu hết tác vụ trừ tìm đường (tab này không có tác vụ đó); cộng đồng Obsidian đã tự chữa bằng Local Graph thay Full Graph; và vị trí node trong force layout không ổn định giữa các lần render nên không dùng làm địa chỉ tinh thần được.

Mặc định: **danh sách nhóm theo `type`**, cột `In`/`Out`, sort *ít link nhất trước* nên node cô lập nằm ngay đầu. Toggle `Graph` chuyển sang node-link **tĩnh, bốn cột theo type**, thứ tự node trong cột trùng thứ tự bảng. Link gãy nét đứt đỏ, node cô lập viền vàng + `!`, thêm cột thứ năm `Missing targets` cho ba tên không tồn tại.

### 4.9 Git: lớp an toàn dưới, tool đọc trên

Hai hướng — git quản lý file memory, và commit history *là* memory — **không cùng một tầng**, nên không phải chọn một. Hướng 1 là lớp an toàn nằm dưới, không đụng cách harness nạp. Hướng 2 là đề xuất truy xuất, và nó thua ở điểm nạp: `MEMORY.md` là thứ duy nhất được inject, mà một commit không thể là file được inject. Quan trọng hơn: **hướng 1 tạo ra chính dữ liệu hướng 2 cần đọc.**

Kết luận: **làm hướng 1, lấy phần đáng giá của hướng 2 làm `memory_history`.**

- `git init --bare` **ngoài** thư mục memory, thao tác bằng `--git-dir`. Đã kiểm chứng: worktree sạch tuyệt đối, `ls -a` không có thêm entry nào, mà vẫn đủ `log` / `diff` / `show`.
- Lý do phải đặt ngoài: `memoryScan.ts` gọi `readdir(memoryDir, { recursive: true })` lọc **duy nhất bằng đuôi `.md`**. Bất kỳ `.md` nào ở bất kỳ đâu dưới thư mục đó đều trở thành một memory — một `README.md` giải thích cách cài git sẽ lặng lẽ biến thành memory. Và `readdir` không loại trừ thư mục ẩn, nên `.git/` bị đi hết cây mỗi lần quét.
- Một **Stop hook** commit sau mỗi lượt.
- Nút khôi phục đi đường `git show <ref>:<path>` ghi ra file — **không** dùng `git checkout --`, vì guard `dcg` chặn lệnh đó.

### 4.10 Giao diện

Mock hi-fi đã dựng trên canvas Pencil: `~/.pencil/documents/1b54e1ec-.../pencil-new.pen`, bốn screen 1920px — Problems, Search, Links (list), Links (graph). Ba tool là **ba tab trong một trang**, không phải ba mục nav; Memory nằm trong nhóm **Systems** của sidebar.

**Đã xử, 2026-09-07.** Mâu thuẫn giữa skill `design-system-flightdeck-night` (Outfit, bo pill, coral `#D93A18`) và `flight-deck.sh/DESIGN.md` (Satoshi, góc vuông, offset shadow, coral `#E84D2A`) được giải bằng cách chọn một nguồn: **DESIGN.md là nguồn duy nhất**, skill Night Ops đã retire vào `orphans/` và ghi trong `OUTDATED_CHOICES.md`. DESIGN.md §5 nay là thang depth bốn bậc r0/r2/r3/r4. Mock Memory trên canvas vẫn đang ở token Night Ops nên **phải re-token trước khi code UI**, không phải hợp nhất tài liệu nữa.

### 4.11 Tầng còn thiếu: transcript

1.2GB transcript **chính là tầng "mọi thứ từng đọc"** — thứ tương ứng với vùng làm cho một liên tưởng bật ra. Hôm nay nó nằm hoàn toàn ngoài đường truy xuất. Có đúng một tính năng mở cửa đó — mục "Searching past context" trong prompt memory, sau cờ GrowthBook `tengu_coral_fern`, dạy model đúng một lệnh grep.

Không đưa vào phase 1, nhưng ghi ở đây vì nó định hướng index phái sinh ở §4.7: **index nên được thiết kế để sau này phủ được cả 1.2GB, không chỉ 88KB đã tuyển.**

### 4.12 Test âm bắt buộc

Chạy `memory_lint` trên một bản sao có cố ý gãy sẵn từng loại, và chứng minh mỗi luật bắt đúng cái của nó. Chứng minh bốn tool phase 1 **không** ghi được gì vào thư mục memory.

---

## 5. Thứ tự dựng

| Bước | Việc | Công | Chặn bởi |
|---|---|---|---|
| 1 | Lớp git dưới thư mục memory + Stop hook | 0.5 | — |
| 2 | `memory_lint` (8 luật) | 1.0 | Chốt luật parse alias (câu hỏi 1). Cột `Used` đọc từ transcript nên không cần bước 1 |
| 3 | `memory_history` | 0.5 | Bước 1 |
| 4 | `memory_search` + index phái sinh | 1.0 | — |
| 5 | `memory_graph` + tab UI | 1.5 | Hợp nhất hai tài liệu design system |
| 6 | `odoo_*` phần chỉ-đọc | 3.5–4 | Dò credential `odoo12-local` |
| 7 | `odoo_*` phần ghi | 3.5–4 | Bước 6 |
| 8 | `ssh_*` tool + policy engine | 3 | — |
| 9 | Hàng đợi duyệt trong FlightDeck UI | 1–2 | Bước 8 |

**Bước 1 phải đứng trước việc bật `autoDreamEnabled`.** Bật dream khi chưa có git là để một agent xoá file trên một kho không có bản lùi: `~/.claude` không phải git repo và `~/.claude/backups` chỉ chứa bản sao `.claude.json`. Và dream sẽ **nổ ngay ở lượt đầu tiên** sau khi bật, không phải sau 24 giờ — vì chưa có file khoá nên `lastConsolidatedAt` bằng 0, cổng thời gian mở tức thì, còn cổng session cần 5 mà máy đang có 69 transcript.

---

## 6. Câu hỏi còn mở

| # | Câu hỏi | Chặn bước nào |
|---|---|---|
| 1 | Luật parse wikilink dạng alias — con số lệch phụ thuộc vào nó | 2 |
| 2 | Hai tài liệu design system chọn cái nào làm chuẩn | 5 |
| 3 | Login/password thật của `odoo12-local` | 6 |
| 4 | `memory_add` có được harness đóng dấu metadata không — phải thử thật | phase 2 |
| 5 | Đo thật ControlMaster trên đường tới staging | 8 |

**Giả định đã chốt trong tài liệu này:** escalate của `ssh_*` là **duyệt ngoài phiên qua FlightDeck UI** (§2.6) — nêu làm mặc định khi chốt phạm vi và được chấp nhận cùng lúc đó. Nếu đổi ý thì §2.6 là phần duy nhất phải viết lại.
