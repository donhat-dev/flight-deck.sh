# Odoo làm control plane cho harness — hướng đi và các vòng khép kín

Tài liệu ý tưởng, nối tiếp `2026-09-08-odoo-base-app-findings.md`. Câu hỏi của thí nghiệm
trước là "Odoo giữ được dữ liệu và màn hình của FlightDeck không". Câu hỏi ở đây rộng hơn:
**Odoo có thể là nơi quản lý harness (Claude Code) đang chạy ngay trong runtime của nó,
và harness đó có thể là thứ sửa chính Odoo hay không.**

Mọi phát biểu "đã đo" bên dưới trỏ về số liệu trong findings; mọi phát biểu về thị trường
trỏ về nguồn ở cuối bài. Những gì chưa đọc được (Odoo 20) không được nhắc.

## 1. Kết luận ngắn

Thị trường đặt agent **bên cạnh** Odoo: một terminal nhúng trong backend, một sidecar
container, hoặc một MCP server phơi Odoo ra cho agent bên ngoài. Chưa có ai đặt Odoo làm
**control plane** của harness — nơi sở hữu chính sách, phê duyệt, sổ chi phí, transcript
và lệnh giao việc. Đó là chỗ trống, và nó khớp với thứ ta đã có: Odoo đã đọc transcript
thật với độ trễ đo được, đã đẩy turn qua bus, đã reload view khi code đổi; FlightDeck đã có
event bus, trigger, kênh phê duyệt và đường gửi lệnh vào session.

Câu adopt / fork / build: **build trên stack đang có**. Module apps store (`claude_code_integration`,
99 USD) là thứ gần nhất nhưng là terminal-centric và không sở hữu vòng lặp nào; fork nó không
mang lại gì mà module `flight_deck` chưa có. Adopt Odoo 19 EE AI không liên quan: đó là agent
nghiệp vụ, EE-only, không quản lý harness.

Đề xuất: **Odoo là control plane; đường gửi lệnh của FlightDeck (`send.py`) trở thành runner.**
Phần còn lại của backend (radar, missions, hub, pipeline treasures) không nằm trong câu này.
Ba spike ở §8 là bước tiếp theo, mỗi cái có tiêu chí đo được.

## 2. Thị trường đang làm gì (đã đọc, 2026-09-10)

| Sản phẩm | Agent nằm ở đâu | Chạm được gì | Điều ta rút ra |
|---|---|---|---|
| **odoo.sh** (Fabien công bố 02/2026, "live" 04/2026) | CLI trong terminal của dev branch, chỉ terminal, cần `/init` | Shell, git, DB của branch | Trải nghiệm đầu tay (19prince): CSS vỡ, 404, push staging thất bại 3 cách; "không phải bước tiến cho người không kỹ thuật". Terminal-only là điểm hỏng, không phải model |
| **oec.sh dev-sidecar** | Container riêng cùng host, sống lâu hơn env Odoo | Bind-mount addons (rw), git + SSH; **không** chạm Postgres hay container Odoo; non-root, `--cap-drop ALL` | Mẫu cô lập tốt nhất; đổi lại agent mù về dữ liệu và log |
| **`claude_code_integration`** (apps store, 18/19) | PTY thật trong worker Odoo, mỗi user một `CLAUDE_CONFIG_DIR` | Postgres qua `postgres-mcp` read-only; admin đăng ký MCP server từ backend, sinh config mỗi phiên; audit log open/close | Gần ý tưởng nhất: Odoo đã quản **cấu hình** harness. Nhưng vẫn là một terminal; không có phê duyệt, không có giao việc, không có sổ chi phí |
| **Odoo 19 EE `ai`** | Trong Odoo | Agent nghiệp vụ, prompt server action, AI field trong Studio | EE-only; CE không có gì. Không phải quản lý harness |
| Hàng chục **MCP server** cho Odoo (apps store, GitHub) | Ngoài Odoo | Agent bên ngoài đọc/ghi Odoo | Đã có thiết kế riêng: `docs/odoo-mcp-domain-design.md` |

## 3. Nguyên liệu đã có trong tay

**Phía Odoo (`flight_deck`, đã đo):** ledger 240k dòng import 21 s; đọc JSONL theo byte
offset, watcher chỉ gọi `ir.cron._trigger()`; 0.17 s từ phản hồi tới đĩa, 4 ms từ commit
tới vẽ; view đổi → form vẽ lại không reload (1.3 s); bundle đổi → tự reload; `session_send`
gọi được runner.

**Phía FlightDeck (backend, chưa commit):** `events.py` bus có kind và payload;
`hub/triggers.py` chạy flow theo event hoặc webhook, có kill switch và một run mỗi trigger;
`decisions.py` + `bin/fd-approve` là kênh phê duyệt qua PreToolUse hook, allowlist regex,
fail-open 90 s; `routers/send.py` là runner `claude --resume -p` với khoá theo session và
kiểm tra session đang sống.

**Phía harness (CLI 2.1.266, đã đọc doc):** hook có 5 loại, trong đó **`type: "http"`**
nhận POST JSON và trả cùng schema với command hook — nghĩa là Odoo controller có thể là
hook trực tiếp. Sự kiện `PermissionRequest` chỉ nổ khi cần một quyết định quyền, nhận
`tool_use_id`, trả `decision: allow|deny`, timeout mặc định 600 s và **khi timeout thì đi
tiếp theo luồng quyền thường** (không block). `-p` có `--permission-prompts none`,
`--permission-mode dontAsk`, `--json-schema`, `--output-format json` mang `total_cost_usd`,
`--session-id` cấp id trước, `--fork-session`, `--resume <đường dẫn .jsonl>`. `--bare`
là chế độ khuyến nghị cho script, và **không đọc OAuth**: chạy không người là API key.

## 4. Mô hình ba tầng

```
Control plane  = Odoo models: run, policy, decision, session, ledger, chatter, cron, bus
Runner         = một process nhỏ trên host, spawn `claude -p`, nói chuyện với Odoo qua HTTP
Harness        = claude, trong sandbox, hook http trỏ về Odoo
```

Một ERP đã có sẵn đúng những thứ một harness cần mà chưa ai làm cho nó: bản ghi có trạng
thái, chatter làm sổ, `mail.activity` để giao việc cho người, `mail.tracking` làm audit,
quyền theo group, cron và bus. Không cần phát minh — chỉ cần harness gọi vào.

## 5. Các vòng khép kín

Mỗi vòng phải đi hết: **kích hoạt → agent làm → bằng chứng quay về Odoo → người quyết →
hiệu lực.** Xếp theo khoảng cách tới thứ đã có.

| Vòng | Kích hoạt | Agent làm | Bằng chứng về Odoo | Người quyết | Hiệu lực | Đã chứng minh | Còn thiếu |
|---|---|---|---|---|---|---|---|
| **A. Quan sát** | file JSONL lớn lên | (không) | turn + tool output, bus append | (không) | UI vẽ | Toàn bộ, đo 4 ms | tổng chi phí không theo turn mới; `end_ts` không tiến |
| **B. Phê duyệt** | `PermissionRequest` → http hook → Odoo | chờ | bản ghi `decision` + `mail.activity` cho người duyệt | approve / reject trong chatter | hook trả `allow`/`deny` | kênh này đang chạy ở FlightDeck (SQLite, 90 s) | controller giữ request thế nào (§5.1); chính sách nằm trong Odoo |
| **C. Giao việc** | server action "Run agent" trên một bản ghi, hoặc automation rule | `claude -p --bare --json-schema` trong sandbox | JSON theo schema ghi lên bản ghi, cost vào ledger, session được follow | duyệt kết quả trên bản ghi | trạng thái bản ghi đổi | `send.py` spawn được CLI; `--json-schema` có sẵn | model `run`; `queue_job`; ai đọc JSONL (§5.2) |
| **D. Sửa chính Odoo** | ticket / mission có nhãn "code" | sửa module trên nhánh riêng, DB nháp | diff, kết quả `--test-tags`, form vẽ lại qua live reload | duyệt merge và `-u` | staging cập nhật | live reload đo được | DB nháp tự tạo; test chạy tự động; gate merge |
| **E. Chính sách** | admin sửa bản ghi `policy` | (không) | `mail.tracking` trên từng dòng | chính là người sửa | hook đọc policy mới ở call kế | `approve.conf` regex đang chạy | chuyển regex thành bản ghi; hook đọc từ Odoo |
| **F. Học** | run kết thúc | (không) | treasure / ghi chú sinh từ run | tag, giữ hay bỏ | MCP resource cho run sau | treasures đã có trong Odoo | chưa có gì nối run → treasure |

Thứ tự làm: **B → E → C → D**. A đã đóng; F chờ C.

### 5.1 Hook chờ người — request được giữ ở đâu

Hook http **chặn agent** cho tới khi Odoo trả lời. Với `workers=0` một request giữ một
thread; với `workers>0` giữ một worker. Hai cách, chọn cách hai:

- **Controller long-poll**: hook `type: "http"` trỏ thẳng vào controller; controller tạo
  `decision`, ngủ và poll bản ghi tới khi có kết quả. Đây là cách duy nhất mà "Odoo
  controller là hook" đúng theo nghĩa đen. Nhưng với `workers>0`, `limit_time_real`
  (mặc định 120 s) giết request đang giữ trước khi người kịp duyệt; với `workers=0` không
  có giới hạn đó nhưng mỗi phê duyệt treo một thread.
- **Hook poll**: hook là `type: "command"`, một script mỏng do runner đặt: POST tạo bản
  ghi `decision`, rồi GET `/flight_deck/decision/<id>` mỗi giây tới khi có kết quả hoặc hết
  hạn. Odoo không giữ request nào; controller chỉ là hai endpoint ngắn.

Timeout phải thống nhất: hook `PermissionRequest` timeout thì **đi tiếp theo luồng quyền
thường**, tức là với `dontAsk` là deny, với `default` là hỏi người (và trong `-p` không có
ai để hỏi). FlightDeck hiện fail-open 90 s. Trong Odoo chọn **fail-closed theo mặc định,
fail-open là một cột trên bản ghi policy** — vì `dcg` và guard vẫn chạy bên dưới cho lệnh
phá hoại, nhưng người đọc bản ghi policy phải thấy được lựa chọn đó.

### 5.2 Hai bẫy đã ghi trong code, giờ hết hoãn được

- **Hai người ghi một session** (`send.py`): không bao giờ `--resume` một session tương tác
  đang sống. Giao việc dùng `--session-id` mới hoặc `--fork-session`. Model `run` giữ
  `session_id` của chính nó.
- **Hai người đọc một nguồn** (findings, "Still open"): khi Odoo tạo session qua giao việc,
  Odoo phải follow nó, và FlightDeck ingest cũng sẽ đọc. `seq` sẽ lệch. Quyết định: session
  do Odoo tạo, **chỉ Odoo đọc** — runner trả `session_id`, Odoo đặt `following=true` trước
  khi run bắt đầu, FlightDeck bỏ qua theo danh sách. Không còn là "an toàn vì ít session".

### 5.3 Tiền và danh tính đổi

Run không người đi qua `--bare` là **API key, không phải subscription** của người dùng.
Cost phải về ledger từ `total_cost_usd` của `--output-format json`, nếu không tab spend mù
với toàn bộ vòng C và D. Session tương tác của người vẫn đi OAuth như hôm nay; hai nguồn
tiền, một sổ.

## 6. Process `claude` chạy ở đâu — chỗ rẽ kiến trúc

- **Trong worker Odoo** (mẫu apps store): không thêm container, nhưng worker Odoo mang
  Node, `claude`, credential; sandbox của harness và sandbox của Odoo chồng lên nhau;
  `workers>0` chưa được thử ở đây.
- **Runner riêng trên host** (mẫu oec.sh; `send.py` chính là nó): Odoo ra lệnh qua HTTP có
  token, runner spawn `claude -p`, mount addons và `~/.claude` như hôm nay, hook http trỏ
  về Odoo. Odoo không cần Node, không giữ credential Anthropic.
- **Odoo thuê runner từ xa**: như trên nhưng runner ở máy khác; chỉ khác ở mount.

**Khuyến nghị: runner riêng**, và runner đó là `send.py` mở rộng thành `POST /run` nhận
`{cwd, prompt, schema, permission_profile, session_id}`. Điều làm đổi khuyến nghị: một môi
trường kiểu odoo.sh không cho phép sidecar; khi đó mới quay về mẫu trong worker.

Trong Odoo, phần chờ và gọi runner đi qua **`queue_job`** (`identity_key` = một run mỗi bản
ghi, `eta` để hoãn, retry cho lỗi mạng), theo quy ước của workspace. `ir.cron._trigger()` ở
follower giữ nguyên vì nó là đánh thức, không phải hàng đợi.

## 7. Không làm

- Không nhúng PTY terminal vào Odoo: đã có sản phẩm, và terminal-only là chính thứ 19prince
  thấy hỏng.
- Không follow toàn bộ session: 873 MB, một file 380 MB.
- Không dựng lại EE `ai` agent nghiệp vụ trên CE.
- Không giữ credential Anthropic trong DB Odoo; runner giữ, Odoo giữ token gọi runner.

## 8. Ba spike kế tiếp

1. **Phê duyệt qua Odoo.** `PermissionRequest` http hook → controller tạo `flightdeck.decision`
   → activity cho người duyệt → approve trong chatter → tool chạy. Tiêu chí: đo round trip
   approve và deny; timeout tạo bản ghi `auto_denied`; một lệnh bị `dcg` chặn không bao giờ
   tới Odoo.
2. **Giao việc từ một bản ghi.** Server action trên `flightdeck.session` (hoặc mission) →
   `queue_job` → runner `POST /run` → `claude -p --bare --json-schema` → JSON lên bản ghi,
   cost vào ledger, session mới xuất hiện trong logbook với `following=true` và có turn.
   Tiêu chí: cost trên bản ghi bằng `total_cost_usd`; chạy hai lần cùng `identity_key` chỉ
   tạo một run. Còn thiếu trước khi chạy: stack `flightdeck-odoo19` là `odoo:19.0` trần,
   cần mount OCA `queue_job`, `server_wide_modules = base,web,queue_job` và cấu hình channel.
3. **Agent sửa `flight_deck`.** Trên DB nháp (`pg_dump | psql` sang `flightdeck_scratch`;
   `createdb -T` thất bại khi container Odoo còn nối vào DB gốc), ticket "thêm field X vào
   form session" → agent sửa trên nhánh riêng → live reload cho thấy field →
   `--test-tags /flight_deck` xanh (dấu `/` chọn module; không có `/` là chọn tag) →
   người bấm "-u staging". Tiêu chí: field hiện trong
   form không reload trang; test log gắn vào ticket; không có ghi nào chạm DB chính.

Thứ nào trong ba spike cũng dùng lại code đang có; không spike nào cần model mới ngoài
`flightdeck.run`, `flightdeck.policy`, `flightdeck.decision`.

## 9. Quyết định của chủ dự án (2026-09-10) — thay khuyến nghị ở §6

Bối cảnh đặt cược được thu hẹp: **Odoo on-premise, một instance chính (mô hình doanh nghiệp
SE), không có CI/CD phức tạp, cần một lớp tương tác nhanh.** Hai kiểu agent được chấp nhận
là hai thứ khác nhau:

- *native agent*: module `ai` của Odoo, chạy "trên" runtime, thao tác bề mặt nghiệp vụ. Trên
  stack CE này nó là EE, hoặc là server action "Hỏi agent" đóng vai thay.
- *embed agent*: Claude nhúng vào hệ thống sản xuất để quản runtime — DB, log, module — như
  odoo.sh đã nhúng vào dev instance.

Từ đó **`claude` chạy trong chính container Odoo**, không phải runner riêng. §6 giữ lại làm
ghi chép về phương án bị loại. Điều kiện đầu tiên của mọi phương án nhúng: process `claude`
sinh ra từ container này thừa kế env của entrypoint (`USER`, `PASSWORD`, `HOST`) và ngồi
cạnh binary `odoo`, nên nó chạy được `odoo shell` với quyền superuser và `psql` với role của
ứng dụng. Mọi lớp kiểm soát trong Odoo đều bị chính process đó đi vòng nếu kiểm soát không
nằm ở tầng hook/permission, env không được lọc, và role DB cấp cho nó không phải read-only
(`fdreader` đã có trong `.env`). Ghi đi qua tool của Odoo (`odoo_*`), không qua psql.

Config dir đặt riêng trong container (`CLAUDE_CONFIG_DIR=/var/lib/odoo/.claude`): follower
đọc file cục bộ, bẫy ACL và inotify qua bind mount biến mất; đổi lại ledger trên host không
thấy các session này, Odoo tự ghi cost từ `total_cost_usd` — đúng với §5.2.

Phương án đã chọn: **1 (session là bản ghi) rồi 2 (agent thường trực)**; 3 là demo, 4 bỏ.
Đăng nhập harness là **login subscription, thiết lập tay** — kể cả trên production, và Odoo
không quản credential đó. Việc còn lại và số đã đo nằm trong `TODO.MD` cùng nhánh.

Số đo cho quyết định process: `claude -p` một lượt trên host, không `--bare`, cold start
**6.5 s wall / 3.3 s API / 303 MB RSS**, và tốn 0.48 USD vì nạp 48k token context của
workspace. Trên 2 s nên phương án 1 dùng **process sống lâu** (`--input-format stream-json`),
không spawn mỗi turn; trong container `--bare` hoặc cwd trống sẽ cắt phần context đó.

## Nguồn

- [Three Sessions. Three Meltdowns. Claude Code in Odoo.sh](https://www.19prince.com/blog/blog-1/three-sessions-three-meltdowns-my-first-take-on-claude-code-in-odoo-sh-5)
- [Odoo.sh Meets AI (teqstars, 2026-04-03)](https://teqstars.com/blog/news-6/odoo-sh-meets-ai-odoo-19-ai-features-for-developers-business-14)
- [Odoo News Digest February 2026 (muchconsulting)](https://muchconsulting.com/blog/odoo-2/odoo-news-february-2026-139)
- [AI Coding Tools Built Into Your Odoo Dev Environment (oec.sh)](https://oec.sh/blog/ai-coding-odoo-dev-sidecar)
- [Vibe Coding for Odoo (oec.sh)](https://oec.sh/guides/vibe-coding-odoo)
- [Odoo Claude Code — apps store module](https://apps.odoo.com/apps/modules/18.0/claude_code_integration)
- [Building an Odoo Coding Agent with Claude Agent SDK (DeployMonkey)](https://deploymonkey.com/blog/building-odoo-coding-agent-claude-sdk)
- [rosenvladimirov/odoo-claude-mcp](https://github.com/rosenvladimirov/odoo-claude-mcp)
- [Odoo 19 AI Agent Orchestration via MCP (Codemarchant)](https://codemarchant.com/blog/dev-diaries-1/ai-agent-orchestration-in-odoo-sub-agent-delegation-via-mcp-6)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)
- [Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
