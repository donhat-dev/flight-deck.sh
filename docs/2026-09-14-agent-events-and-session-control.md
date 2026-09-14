# Bắt sự kiện và quản lý session agent

Odoo muốn biết mỗi agent đang ở đâu — đang chạy tool nào, đang chờ ai duyệt, đã dừng vì lý do
gì — và muốn can thiệp được. Tài liệu này so ba nguồn sự kiện đang có thật trong CLI hiện tại,
rồi đề xuất cách ghép chúng lại. Mọi con số ở đây là đo trên máy này, CLI bản 2026-09-11.

## 1. Kết luận ngắn

Ba nguồn không thay thế nhau, chúng trả lời ba câu hỏi khác nhau:

| Nguồn | Trả lời câu hỏi | Vai trong hệ |
|---|---|---|
| File transcript (byte offset) | *Agent đã nói và làm gì?* | sự thật, đọc lại được, đã chạy |
| Hook | *Vừa có chuyện gì xảy ra, ngay lúc này?* | chuyển trạng thái, và là chỗ chặn được |
| CLI (`claude agents`, `stop`, …) | *Session nào còn sống? Dừng nó thế nào?* | điểm danh **tiến trình cấp trên cùng** và hành động |

Transcript là nội dung, hook là trạng thái, CLI là quyền điều khiển. Một câu hỏi thứ tư —
*trong session này, ngay lúc này, việc gì đang chạy* — không nguồn nào trả lời một mình: §6 dựng
nó từ transcript, §2.1 lấy cùng câu trả lời từ hook. Thiếu hook thì Odoo chỉ
biết chuyện sau khi model viết ra file; thiếu transcript thì mất một sự kiện là mất luôn; thiếu
CLI thì không biết process nào còn sống và không tắt được nó.

## 2. Hook cho biết gì — đo thật

Một lượt `claude -p` tầm thường (một lệnh `echo`) bắn 12 hook. Payload là JSON trên stdin. Mọi
hook đều mang `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `scratchpad_dir`; riêng
`prompt_id` và `permission_mode` chỉ có **từ `UserPromptSubmit` trở đi** — `SessionStart` và
`InstructionsLoaded` xảy ra trước khi có lượt nào nên không có hai trường đó. Nghĩa là: nối về
session thì hook nào cũng nối được, nối về đúng lượt thì phải từ `UserPromptSubmit`.

| Hook | Trường riêng đo được | Odoo dùng để |
|---|---|---|
| `SessionStart` | `source`, `transcript_path` | tạo/kích hoạt bản ghi session, biết file để theo dõi |
| `InstructionsLoaded` | `file_path`, `memory_type`, `load_reason`, `parent_file_path` | biết session đang chạy dưới bộ luật nào (CLAUDE.md nào đã nạp) |
| `UserPromptSubmit` | `prompt`, `prompt_id`, `permission_mode` | mở một lượt, ghi mode đang áp dụng |
| `PreToolUse` | `tool_name`, `tool_input`, `tool_use_id` | trạng thái "đang chạy tool X"; cũng là chỗ chặn được |
| `PostToolUse` | `tool_response`, `duration_ms` | thời lượng mỗi tool; `tool_response` là toàn bộ output nên **không ghi lại** (xem §4) |
| `PostToolBatch` | `tool_calls[]` | gộp một loạt tool của cùng lượt |
| `MessageDisplay` | `delta`, `final`, `index`, `message_id`, `turn_id` | dòng chữ đang stream, nếu muốn hiện sớm hơn file |
| `Stop` | `last_assistant_message`, `background_tasks`, `session_crons`, `stop_hook_active` | đóng lượt, và biết agent còn để lại việc nền |
| `SessionEnd` | `reason` | đóng session kèm lý do |

Còn 15 hook nữa có trong binary mà lượt thử không chạm tới — `PermissionRequest`,
`TaskCreated` / `TaskCompleted`, `PreCompact` / `PostCompact`,
`PreModelSwitch` / `PostModelSwitch`, `Notification`, `TeammateIdle`, `ConfigChange`,
`WorktreeCreate` / `WorktreeRemove`, `CwdChanged`, `FileChanged`, `PostToolUseFailure`,
`StopFailure`, `Elicitation` / `ElicitationResult`. Tên của chúng có thật trong bản CLI này;
payload thì chưa đo.

**Giá của một hook: ~1,8 ms.** 200 lần spawn `sh -c 'cat >> file'` hết 0,352 s. Một lượt 12
hook tốn cỡ 20 ms, một lượt 20 tool cỡ 60 ms. Đủ rẻ để hook ghi thẳng vào file — và cũng là lý
do **hook không được gọi Odoo qua HTTP**: mỗi lần gọi là một lượt chờ nằm chắn ngang phiên làm việc.

### 2.1 Agent con — đo bằng một lượt riêng

Lượt thử thứ hai bảo agent gọi tool `Agent` để mở một agent con. Thực tế không có
`TaskCreated` / `TaskCompleted` nào chạy; cái chạy là cặp **`SubagentStart` / `SubagentStop`**:

| Hook | Trường riêng đo được |
|---|---|
| `PreToolUse` (tool `Agent`) | `tool_name: "Agent"`, `tool_input.prompt`, `tool_use_id` |
| `SubagentStart` | `agent_id`, `agent_type` (`general-purpose`) |
| `PreToolUse` / `PostToolUse` **bên trong agent con** | thêm `agent_id`, `agent_type` |
| `SubagentStop` | `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message` |

Ba hệ quả cho Odoo:

- **Tool của agent con phân biệt được với tool của agent cha** bằng `agent_id` có trong payload.
  Không cần đoán theo thứ tự thời gian.
- **Agent con có file transcript riêng**, nằm trong thư mục con của session cha:
  `…/<session_id>/subagents/agent-<agent_id>.jsonl` (kèm một `.meta.json`). Bộ đọc theo byte
  offset hiện tại chỉ mở đúng một file `<session_id>.jsonl`, nên muốn đọc nội dung agent con thì
  phải theo dõi thêm những file này — `SubagentStop.agent_transcript_path` đưa đường dẫn sẵn,
  không phải quét thư mục.
- **Một lượt người dùng có thể sinh hai `Stop`** với hai `prompt_id` khác nhau: một khi agent cha
  giao việc xong, một khi agent con trả kết quả và agent cha nói tiếp. Nên "lượt kết thúc" phải
  tính theo `prompt_id`, không phải đếm `Stop`.
- **`Stop.background_tasks` là bản kiểm kê việc nền còn sống tại đúng thời điểm lượt kết thúc.**
  Đo được nguyên văn:

  ```json
  [{"id":"a900e341cb31ec088","type":"subagent","status":"running",
    "description":"Run bash command and report output","agent_type":"general-purpose"}]
  ```

  `Stop` thứ nhất liệt kê agent con đang chạy; `Stop` thứ hai (sau khi nó xong) trả về `[]`. Đây
  là câu trả lời trực tiếp cho "ngay lúc này session đang chạy gì ở nền" — có `id`, `type`,
  `status`, không phải suy đoán.

### 2.2 Agent nền mở từ một session thường — chỗ điểm danh không nhìn thấy

Đây là khoảng trống lớn nhất, và nó có thật. Lấy session `395a72f8` làm ví dụ: nó **có** trong
`claude agents --json --all` (dòng `interactive`, `pid` 2177516). Cái không có trong đó là
**50 lần gọi `Agent` với `run_in_background`, 209 agent con, và 3 workflow** mà session đó đã mở.
Điểm danh chỉ thấy tiến trình cấp trên cùng; con cháu bên trong một session không bao giờ xuất hiện.

Hai sổ đăng ký mà `claude agents` đọc, đo thật:

| Sổ | Cho session nào | Nội dung |
|---|---|---|
| `~/.claude/sessions/<pid>.json` | interactive | **tĩnh**: `pid`, `sessionId`, `cwd`, `name`, `entrypoint`, `messagingSocketPath`. `updatedAt` = lúc mở, không cập nhật. Không có trạng thái. |
| `~/.claude/jobs/<short-id>/state.json` | background (`--bg`) | **giàu**: `state`, `tempo` (`idle`/`busy`), `inFlight{tasks,queued,kinds}`, `tokens`, `children`, `sessionId`, kèm `timeline.jsonl` |

Một chi tiết đáng chú ý trong `state.json`: `linkScanOffset` + `linkScanPath` — **chính CLI cũng
theo dõi transcript bằng byte offset**. Hướng đọc của FlightDeck trùng với hướng CLI tự dùng.

Hệ quả chia việc: với session `--bg` thì **đọc thẳng `jobs/<id>/state.json`**, không tự suy lại.
Với session interactive thì sổ đăng ký không nói gì về trạng thái, phải suy từ transcript + hook.

Còn agent con và workflow mở từ bên trong một session thì không có mục nào trong hai sổ trên.
Dấu vết của chúng nằm ở ba chỗ, đều đo được:

- `projects/<proj>/<session_id>/subagents/agent-<agent_id>.jsonl` — transcript riêng của agent con,
  kèm `agent-<agent_id>.meta.json` chứa `agentType`, `description`, `toolUseId`, `spawnDepth`,
  `model` (thỉnh thoảng `stoppedByUser`, `parentAgentId`). **Không có trường trạng thái.** Khi
  không có hook, dấu hiệu còn sống là *mtime của `.jsonl` vẫn tăng* trong khi `.meta.json` đứng yên
  từ lúc sinh (đo được: meta 12:09, jsonl 12:16 trên cùng một agent).
- `projects/<proj>/<session_id>/workflows/wf_<id>.json` — `status`, `phases`, `agentCount`,
  `totalTokens`, `totalToolCalls`, `durationMs`, và `workflowProgress[]` với từng agent
  `{agentId, label, phaseIndex, phaseTitle, model, state, startedAt}`.
  ⚠️ **Chưa xác minh file này có được ghi liên tục hay không.** Hai workflow bị kill đều có mtime
  đúng bằng thời điểm kill và `workflowProgress` vẫn ghi `state: "progress"` — dấu hiệu của một
  bản chụp ghi lúc kết thúc, không phải ghi dần. Cách kiểm chứng: chạy một workflow thật rồi
  `stat` file đó nhiều lần trong lúc nó chạy. Việc này tốn tiền nên chờ chủ máy đồng ý.
- Scratchpad `tasks/<agent_id>.output` — chỉ là **symlink** trỏ về đúng file `.jsonl` ở trên, không
  phải một file trạng thái riêng.

Agent con **không** ghi lẫn vào transcript cha: đếm được `isSidechain:true` = **0 dòng** trên
113.458 dòng của `395a72f8`. Muốn đọc nội dung agent con thì phải mở file riêng của nó.

## 3. CLI cho biết gì — đo thật

`claude agents --json --all` trả về cả session nền lẫn session người dùng đang mở:

```json
{"id":"88d32c3f","sessionId":"88d32c3f-…","kind":"background","cwd":"…","name":"config file management","startedAt":1787648368833,"state":"blocked"}
{"pid":2177516,"sessionId":"395a72f8-…","kind":"interactive","cwd":"…","name":"projects-1c","startedAt":1788937136147}
```

Khác biệt quan trọng: **background** có `id` ngắn và `state` (`blocked` / `done`), **interactive**
có `pid` nhưng không có `state`. Cả hai đều có `sessionId`, nên nối được về bản ghi trong Odoo và
về đúng file transcript.

Phần vòng đời CLI làm sẵn: `claude --bg` chạy nền rồi in id, `claude attach` mở lại trong
terminal, `claude logs` in output gần nhất, `claude stop` dừng mà giữ hội thoại, `claude respawn`
khởi động lại theo bản CLI hiện tại, `claude rm` xoá hẳn.

**Nhưng danh sách lệnh đó không có cách gửi một prompt vào một session `--bg` đang chạy** —
`attach` là một terminal, không phải một API gọi từ script. Nên CLI thay được đúng hai việc:
*điểm danh các tiến trình cấp trên cùng* và *dừng / khởi động lại*. Kênh nhập liệu và vai trò host
trả lời quyền vẫn là fd-runner; không có chuyện bỏ fd-runner đi.

Giới hạn phải nói rõ vì nó đổi cách viết `_reconcile`: điểm danh chỉ kết luận được về **session**,
không bao giờ về một agent con. Một session biến mất khỏi danh sách thì mọi agent con của nó cũng
chết theo, nhưng chiều ngược lại không đúng — session còn trong danh sách không có nghĩa là agent
con của nó còn chạy.

## 4. Kiến trúc đề xuất — ba lớp, một đường đi

```
   agent (host hoặc container)                     Odoo
   ─────────────────────────────                   ────────────────────
   hook  ──append──►  spool/events.jsonl  ──byte offset──►  cron đọc  ──► session.state
   claude ─write──►  ~/.claude/projects/*.jsonl ──byte offset──►  reader ──► message.text
   fd-runner ◄─file── spool/cmd/*.json  ◄────────────────────  lệnh từ Odoo
   claude agents --json  ◄──exec───────────────────────────── điểm danh định kỳ
```

Điểm cốt lõi: **hook ghi vào đúng cái spool fd-runner đang dùng**, không thêm cổng, không thêm
daemon, không đặt thông tin đăng nhập Odoo vào môi trường agent. Odoo đọc `events.jsonl` bằng
chính bộ đọc theo byte offset đã có, nên hook và fd-runner nhập vào cùng một đường. Ghi thêm một
dòng bằng `O_APPEND` với một lần `write` nhỏ là nguyên tử, nên hai tiến trình cùng ghi một file
không cần khoá.

Thân hook nên là **một binary tĩnh nhỏ** (`fd-hook`) chứ không phải script: đọc stdin, gắn `ts`,
`kind`, `zone`, ghi một dòng, thoát 0. Nó không được phép làm gì khác — một hook treo là một
phiên treo.

Và nó phải **cắt `tool_response` trước khi ghi**, chỉ giữ `tool_use_id` + `duration_ms`.
`PostToolUse.tool_response` là toàn bộ output của tool: một lượt chụp màn hình đẩy ~200 KB vào
`events.jsonl`, đúng thứ đã nằm sẵn trong transcript, trong khi `events.jsonl` chưa có cơ chế
xoay vòng. Nội dung ở transcript, sự kiện ở spool.

## 5. Máy trạng thái của một session

| Trạng thái | Vào bằng | Ra bằng |
|---|---|---|
| `starting` | Odoo gửi `start` | `SessionStart` |
| `idle` | `Stop`, hoặc `SessionStart` | `UserPromptSubmit` |
| `busy` | `UserPromptSubmit`, `PreToolUse` | `Stop` |
| `waiting` (chờ duyệt) | `PermissionRequest`, hoặc control channel của fd-runner | với session của fd-runner: `decide` từ Odoo hoặc hết giờ. Với session chỉ nhìn qua hook: **suy ra** — `PostToolUse` cùng `tool_use_id` (đã cho phép), hoặc `PreToolUse`/`Stop` kế tiếp (đã từ chối) |
| `stopped` | `SessionEnd` kèm `reason`, hoặc điểm danh không thấy nữa | `start` mới |

Hai nguồn cùng nói về `waiting` là có chủ ý: fd-runner biết chính xác với session nó tự mở
(nó là host trả lời quyền), còn hook `PermissionRequest` phủ được cả session người dùng tự mở
trong terminal — chỗ mà fd-runner không nhìn thấy gì. Điểm khác nhau phải nhớ: với session
terminal **người dùng trả lời ngay trong terminal**, không có hook "đã quyết định" nào cả, nên
Odoo chỉ hiển thị được "đang chờ" rồi tự đóng khi thấy `PostToolUse`.

Khi cả hai nguồn cùng thấy một yêu cầu (session do fd-runner mở mà máy lại bật hook cấp máy),
khoá gộp là **`tool_use_id`** — `flightdeck.decision` đã có sẵn trường này. `request_id` của
control channel không được chia sẻ sang hook, nên không dùng làm khoá gộp được.

Điểm danh bằng `claude agents --json` là cái chốt: hook có thể mất (`kill -9` thì không hook nào
chạy), nên trạng thái nào không còn xuất hiện trong danh sách thì Odoo hạ về `stopped`. Đây chính
là `_reconcile` đang có, đổi nguồn từ file state của runner sang danh sách của CLI.

## 6. Ngay lúc này session đang làm gì — sổ hai cột

Một session sống hàng tháng, đi qua hàng nghìn lượt; mỗi lượt có thể là một câu trả lời thường,
một `Agent`, một `Agent` chạy nền, hay một `Workflow`. Câu hỏi "ngay lúc này nó đang làm gì" trả
lời được chính xác, không cần đoán, bằng ba mẩu sau.

**Khoá của một lượt là `promptId`.** Mọi dòng transcript đều mang trường này, và nó **trùng đúng
với `prompt_id` trong payload hook** — đây là chỗ hai nguồn ghép lại được. Lượt hiện tại = `promptId`
của dòng cuối file. Dấu lượt đã đóng nằm ngay trong transcript: một dòng
`type: "system", subtype: "stop_hook_summary"` của đúng `promptId` đó.

**Lượt đó thuộc loại gì** thì đọc tên tool xuất hiện dưới `promptId` ấy: `Agent` (nền hay không thì
xem `input.run_in_background`), `Workflow`, `Bash` chạy nền, `Monitor`. Không cần mô hình riêng cho
"workflow turn" — nó chỉ là một lượt có tool `Workflow`.

**Việc gì còn đang chạy** thì giữ hai cột mở, và đây là chỗ dễ làm sai nhất:

| Cột | Mở khi | Đóng khi |
|---|---|---|
| Tool chạy trực tiếp | có `tool_use` | có `tool_result` cùng `tool_use_id` |
| Việc chạy nền | `tool_result` báo *đã khởi động* (trả về ngay, kèm `agentId`) | có dòng `<task-notification>` với `<tool-use-id>` khớp |

Cột thứ hai tồn tại vì một `Agent` chạy nền **đóng ngay lập tức ở cột thứ nhất**: tool_result của
nó về sau vài mili giây với nội dung "Async agent launched successfully… agentId: …". Ai chỉ đếm
`tool_use` chưa có `tool_result` sẽ kết luận sai rằng session đang rảnh trong khi 10 agent con vẫn
chạy. Kết thúc thật đến sau, dưới dạng một dòng `user` chứa:

```
<task-notification><task-id>…</task-id><tool-use-id>toolu_…</tool-use-id>
<output-file>…/tasks/<agent_id>.output</output-file><status>completed</status>…
```

`status` đo được có bốn giá trị: `completed` (346), `failed` (8), `killed` (13), `stopped` (4) trên
session `395a72f8`. `Bash` chạy nền và `Monitor` kết thúc theo đúng khuôn này.

**Ba nguồn nói cùng một chuyện, dùng nguồn nào là tuỳ có hook hay không:**

- Có hook: `Stop.background_tasks` cho thẳng danh sách việc nền còn sống kèm `status` (§2.1), và
  `SubagentStart` chưa có `SubagentStop` cùng `agent_id` là tập agent con đang chạy.
- Không có hook: hai cột mở ở trên, tính lại từ transcript. Tính được cả với session đã đóng, nên
  đây là nguồn kiểm chứng cho hook.
- Tool nào thuộc agent con nào: `agent_id` có sẵn trong `PreToolUse`/`PostToolUse` của agent con.

Ghi chú một thứ thấy nhưng không dùng: `sessions/<pid>.json` có `messagingSocketPath`
(`/run/user/1000/cc-socks/<pid>.sock`) — một socket riêng có khoá, không có tài liệu. Không xây gì
trên đó; transcript là bề mặt ổn định.

## 7. Nối vào cái đang có

- `flightdeck.runner._apply_event` đã phân loại theo `kind`; hook chỉ là thêm `kind` mới
  (`hook`), với `payload` là nguyên văn JSON của hook. Không cần transport mới.
- `flightdeck.decision` đã có đủ trường cho một yêu cầu quyền; `PermissionRequest` điền được
  cùng bộ trường đó cho session ngoài tầm fd-runner.
- Cần thêm một model mỏng `flightdeck.session.event`: `session_ref_id`, `kind`, `prompt_id`,
  `tool_use_id`, `ts`, `payload`. Đây là thứ trả lời "5 phút qua agent làm gì" mà không phải đọc
  lại transcript, và là nguồn cho mọi biểu đồ thời lượng (`duration_ms` đã có sẵn trong payload).
- `tool_use_id` nối hook với đúng dòng transcript đã lưu, nên hai nguồn ghép được về một hàng.
- Cần một model con `flightdeck.session.agent` cho agent con và workflow: `session_ref_id`,
  `agent_id`, `agent_type`, `prompt_id`, `tool_use_id`, `spawn_depth`, `transcript_path`, `state`.
  Đây là thứ mà `claude agents` không bao giờ trả lời được, và là chỗ để bảng trạng thái hiện
  "session này đang chạy 4 agent con" thay vì chỉ "đang bận".
- `_reconcile` giữ nguyên vai trò với session, nhưng **không được dùng để hạ trạng thái agent con**;
  agent con đóng bằng `SubagentStop` hoặc bằng `<task-notification>` (§6).

## 8. Bẫy đã biết

- **Hook là at-most-once.** Mất một sự kiện là mất hẳn; transcript mới là thứ đọc lại được.
  Vì vậy không được xây bất cứ tổng kết nào chỉ dựa trên hook.
- **Hook chạy bằng quyền của người dùng, từ file cấu hình của người dùng.** Odoo đọc sự kiện,
  **không được ghi cấu hình hook** — cùng nguyên tắc với thông tin đăng nhập.
- **`--bare` bỏ qua toàn bộ hook.** Session chạy ở chế độ đó chỉ còn transcript và điểm danh.
- **Hai đường cấu hình, hai mức phủ.** `--settings` lúc `start` chỉ phủ session do Odoo mở;
  `~/.claude/settings.json` phủ mọi session trên máy, kể cả terminal người dùng — nên đó là lựa
  chọn của chủ máy, không phải của Odoo.
- **`kill -9`, mất điện, container dừng: không hook nào chạy.** Điểm danh là cái duy nhất phát
  hiện được.
- **Agent nền đóng ngay ở cột tool.** Đếm `tool_use` thiếu `tool_result` sẽ báo rảnh trong khi
  agent con vẫn chạy; phải đợi `<task-notification>` (§6).
- **Điểm danh không thấy con cháu.** `claude agents` chỉ liệt kê tiến trình cấp trên cùng.
- **`workflows/wf_*.json` chưa chắc ghi liên tục** — xem §2.2; đừng dựng bảng tiến độ trên nó
  trước khi đo.
- **Session interactive không có `state`.** Với chúng, "đang bận hay không" phải suy từ hook
  hoặc từ byte cuối của transcript, không hỏi được CLI.

## 9. Lộ trình

1. `fd-hook` + cấu hình `--settings` cho session do Odoo mở: `SessionStart`, `UserPromptSubmit`,
   `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`. Đủ để trạng thái trên form không còn phải
   suy đoán từ transcript.
2. Điểm danh bằng `claude agents --json --all` trong cron đang có (chỉ cho session); với session
   `--bg` đọc thêm `jobs/<id>/state.json` để lấy `state` + `tempo`; đổi `_reconcile` sang nguồn
   này; thêm nút Stop / Respawn / Logs cho session nền.
2b. Sổ hai cột ở §6 trong bộ đọc transcript, cộng bảng agent con lấy từ
   `<session_id>/subagents/*.meta.json`. Đây là bước làm cho form trả lời được "đang chạy gì",
   không chỉ "đang bận hay rảnh" — và nó chạy được cả khi chưa có hook nào.
3. Mở rộng sang session người dùng: bật hook ở cấu hình cấp máy, Odoo nhìn thấy cả những phiên
   nó không mở. Đây là bước biến Odoo từ "nơi chạy agent" thành "nơi quan sát mọi agent".

## 10. Không làm

- Hook gọi HTTP vào Odoo. Mỗi lượt gọi là một lượt chờ chắn ngang phiên làm việc.
- Dùng hook làm nguồn duy nhất cho chi phí hay lịch sử. Transcript giữ vai đó.
- Ghi `tool_response` vào `events.jsonl`. Nội dung đã có ở transcript; spool sẽ phình không kiểm soát.
- Để Odoo sinh hoặc sửa file cấu hình hook trên máy người dùng.
