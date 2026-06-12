use anyhow::{anyhow, Result};
use serde_json::{json, Value};
use std::env;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::Duration;

use crate::{call_apply_action, call_doctor, call_no_args, call_process_prompt, workspace_dir_string};

pub(crate) async fn serve(args: &[String]) -> Result<()> {
    let port = requested_port(args);
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    let addr = listener.local_addr()?;
    let url = format!("http://{}", addr);

    println!("ProtoAgent app is running at {url}");
    println!("Open that URL in your browser. Press Ctrl-C here to stop the server.");

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                thread::spawn(move || {
                    if let Err(err) = handle_stream(stream) {
                        eprintln!("request error: {err}");
                    }
                });
            }
            Err(err) => eprintln!("connection error: {err}"),
        }
    }

    Ok(())
}

fn requested_port(args: &[String]) -> u16 {
    if let Some(port) = args
        .iter()
        .position(|arg| arg == "--port" || arg == "-p")
        .and_then(|idx| args.get(idx + 1))
        .and_then(|value| value.parse::<u16>().ok())
    {
        return port;
    }
    if let Some(port) = args.iter().find_map(|arg| arg.parse::<u16>().ok()) {
        return port;
    }
    env::var("PROTOAGENT_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(0)
}

fn handle_stream(mut stream: TcpStream) -> Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
    let request = read_request(&mut stream)?;
    let response = route(&request);
    write_response(&mut stream, response)
}

fn route(request: &Request) -> HttpResponse {
    match (request.method.as_str(), request.path.as_str()) {
        ("GET", "/") => html(INDEX_HTML),
        ("GET", "/app.css") => css(APP_CSS),
        ("GET", "/app.js") => javascript(APP_JS),
        ("GET", "/api/status") => json_response(status_payload()),
        ("POST", "/api/chat") => match chat_payload(&request.body) {
            Ok(value) => json_response(value),
            Err(err) => error_response(&err.to_string()),
        },
        ("POST", "/api/apply") => match apply_payload(&request.body) {
            Ok(value) => json_response(value),
            Err(err) => error_response(&err.to_string()),
        },
        _ => HttpResponse {
            status: "404 Not Found",
            content_type: "text/plain; charset=utf-8",
            body: b"Not found".to_vec(),
        },
    }
}

fn status_payload() -> Value {
    let workspace = workspace_dir_string();
    json!({
        "workspace": workspace,
        "config": json_call("get_config"),
        "models": json_call("list_models"),
        "check": json_doctor(),
    })
}

fn chat_payload(body: &[u8]) -> Result<Value> {
    let payload: Value = serde_json::from_slice(body)?;
    let prompt = payload
        .get("prompt")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| anyhow!("prompt is required"))?;
    let json = call_process_prompt(prompt.to_string(), workspace_dir_string())
        .map_err(|err| anyhow!("Python core error: {err:?}"))?;
    Ok(serde_json::from_str(&json)?)
}

fn apply_payload(body: &[u8]) -> Result<Value> {
    let payload: Value = serde_json::from_slice(body)?;
    let workspace = payload
        .get("workspace")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(workspace_dir_string);
    let actions = payload
        .get("actions")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("actions array is required"))?;

    let mut applied = Vec::new();
    for action in actions {
        let result = call_apply_action(serde_json::to_string(action)?, workspace.clone())
            .map_err(|err| anyhow!("Python apply error: {err:?}"))?;
        let parsed: Value = serde_json::from_str(&result)?;
        applied.push(parsed);
    }

    Ok(json!({ "ok": true, "applied": applied }))
}

fn json_call(function: &str) -> Value {
    match call_no_args(function) {
        Ok(raw) => serde_json::from_str(&raw).unwrap_or_else(|err| json!({ "error": err.to_string() })),
        Err(err) => json!({ "error": format!("{err:?}") }),
    }
}

fn json_doctor() -> Value {
    match call_doctor(workspace_dir_string()) {
        Ok(raw) => serde_json::from_str(&raw).unwrap_or_else(|err| json!({ "error": err.to_string() })),
        Err(err) => json!({ "error": format!("{err:?}") }),
    }
}

struct Request {
    method: String,
    path: String,
    body: Vec<u8>,
}

fn read_request(stream: &mut TcpStream) -> Result<Request> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        buffer.extend_from_slice(&chunk[..read]);
        if let Some(header_end) = find_header_end(&buffer) {
            let content_length = content_length(&buffer[..header_end]).unwrap_or(0);
            let total = header_end + 4 + content_length;
            while buffer.len() < total {
                let read = stream.read(&mut chunk)?;
                if read == 0 {
                    break;
                }
                buffer.extend_from_slice(&chunk[..read]);
            }
            break;
        }
        if buffer.len() > 1024 * 1024 {
            return Err(anyhow!("request too large"));
        }
    }

    let header_end = find_header_end(&buffer).ok_or_else(|| anyhow!("malformed HTTP request"))?;
    let headers = String::from_utf8_lossy(&buffer[..header_end]);
    let mut lines = headers.lines();
    let request_line = lines.next().ok_or_else(|| anyhow!("missing request line"))?;
    let mut request_parts = request_line.split_whitespace();
    let method = request_parts.next().unwrap_or("").to_string();
    let raw_path = request_parts.next().unwrap_or("/").to_string();
    let path = raw_path.split('?').next().unwrap_or("/").to_string();
    let length = content_length(&buffer[..header_end]).unwrap_or(0);
    let body_start = header_end + 4;
    let body_end = body_start.saturating_add(length).min(buffer.len());
    let body = buffer[body_start..body_end].to_vec();

    Ok(Request { method, path, body })
}

fn find_header_end(buffer: &[u8]) -> Option<usize> {
    buffer.windows(4).position(|window| window == b"\r\n\r\n")
}

fn content_length(headers: &[u8]) -> Option<usize> {
    let headers = String::from_utf8_lossy(headers);
    headers.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        if name.eq_ignore_ascii_case("content-length") {
            value.trim().parse::<usize>().ok()
        } else {
            None
        }
    })
}

struct HttpResponse {
    status: &'static str,
    content_type: &'static str,
    body: Vec<u8>,
}

fn html(body: &'static str) -> HttpResponse {
    response("200 OK", "text/html; charset=utf-8", body.as_bytes().to_vec())
}

fn css(body: &'static str) -> HttpResponse {
    response("200 OK", "text/css; charset=utf-8", body.as_bytes().to_vec())
}

fn javascript(body: &'static str) -> HttpResponse {
    response("200 OK", "application/javascript; charset=utf-8", body.as_bytes().to_vec())
}

fn json_response(value: Value) -> HttpResponse {
    response(
        "200 OK",
        "application/json; charset=utf-8",
        serde_json::to_vec(&value).unwrap_or_else(|_| b"{\"error\":\"serialization failed\"}".to_vec()),
    )
}

fn error_response(message: &str) -> HttpResponse {
    response(
        "500 Internal Server Error",
        "application/json; charset=utf-8",
        serde_json::to_vec(&json!({ "error": message })).unwrap_or_default(),
    )
}

fn response(status: &'static str, content_type: &'static str, body: Vec<u8>) -> HttpResponse {
    HttpResponse {
        status,
        content_type,
        body,
    }
}

fn write_response(stream: &mut TcpStream, response: HttpResponse) -> Result<()> {
    write!(
        stream,
        "HTTP/1.1 {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n",
        response.status,
        response.content_type,
        response.body.len()
    )?;
    stream.write_all(&response.body)?;
    stream.flush()?;
    Ok(())
}

const INDEX_HTML: &str = r#"<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ProtoAgent</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="app-shell">
    <header class="topbar">
      <div>
        <div class="eyebrow">LOCAL AGENT CONSOLE</div>
        <h1>ProtoAgent</h1>
      </div>
      <div class="runtime-strip">
        <span id="provider-pill">provider: loading</span>
        <span id="model-pill">model: loading</span>
        <span id="workspace-pill">workspace: loading</span>
      </div>
    </header>

    <section class="panel-zone">
      <nav class="tabs" aria-label="Status panels">
        <button class="tab active" data-panel="dashboard">Dashboard</button>
        <button class="tab" data-panel="models">Models</button>
        <button class="tab" data-panel="agents">Agents</button>
        <button class="tab" data-panel="check">Check</button>
        <button class="tab" data-panel="help">Help</button>
      </nav>
      <div id="panel" class="panel-grid"></div>
    </section>

    <section id="transcript" class="transcript" aria-live="polite"></section>

    <form id="composer" class="composer">
      <div class="prompt-marker">&gt;</div>
      <textarea id="prompt" rows="1" autocomplete="off" spellcheck="false" placeholder="Type a task or /models, /agents, /check, /help"></textarea>
      <button id="send" type="submit">Send</button>
    </form>
  </main>

  <template id="message-template">
    <article class="message">
      <div class="message-label"></div>
      <pre class="message-body"></pre>
      <div class="message-meta"></div>
    </article>
  </template>

  <script src="/app.js"></script>
</body>
</html>
"#;

const APP_CSS: &str = r#":root {
  color-scheme: dark;
  --bg: #070910;
  --surface: #10131d;
  --surface-2: #171b27;
  --surface-3: #202637;
  --line: #2d3446;
  --text: #eef3f8;
  --muted: #9aa7b8;
  --faint: #657084;
  --cyan: #58dce9;
  --magenta: #e056d8;
  --yellow: #f3c65b;
  --green: #74df9f;
  --red: #ff7a8a;
}

* { box-sizing: border-box; }

html, body {
  height: 100%;
  margin: 0;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
}

body {
  letter-spacing: 0;
}

button, textarea {
  font: inherit;
}

.app-shell {
  height: 100vh;
  width: 100%;
  min-width: 0;
  display: grid;
  grid-template-rows: 72px 180px 1fr 76px;
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(224, 86, 216, 0.08), transparent 28%),
    var(--bg);
}

.app-shell > * {
  width: 100%;
  min-width: 0;
  max-width: 100%;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 20px 12px;
  background: #0b0e16;
  border-bottom: 1px solid var(--line);
}

.topbar > div {
  min-width: 0;
}

.eyebrow {
  color: var(--magenta);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .14em;
}

h1 {
  margin: 2px 0 0;
  font-size: 24px;
  line-height: 1;
}

.runtime-strip {
  min-width: 0;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-strip span,
.command-chip,
.metric {
  max-width: 100%;
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--muted);
  border-radius: 6px;
  padding: 6px 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.panel-zone {
  min-width: 0;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
  display: grid;
  grid-template-rows: 42px 1fr;
}

.tabs {
  min-width: 0;
  display: flex;
  align-items: end;
  gap: 4px;
  padding: 8px 14px 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.tab {
  flex: 0 0 auto;
  height: 34px;
  border: 1px solid transparent;
  border-bottom: 0;
  background: transparent;
  color: var(--muted);
  border-radius: 6px 6px 0 0;
  padding: 0 12px;
  cursor: pointer;
}

.tab.active {
  background: var(--surface-2);
  color: var(--cyan);
  border-color: var(--line);
}

.panel-grid {
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding: 10px 14px 14px;
  overflow: hidden;
}

.panel-card {
  min-width: 0;
  border: 1px solid var(--line);
  background: var(--surface-2);
  border-radius: 8px;
  padding: 10px;
  overflow: hidden;
}

.panel-card h2 {
  margin: 0 0 8px;
  color: var(--cyan);
  font-size: 13px;
  line-height: 1.1;
}

.panel-card p,
.panel-card li {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
}

.panel-card p {
  margin: 0;
  white-space: pre-wrap;
}

.panel-card ul {
  margin: 0;
  padding-left: 16px;
}

.transcript {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 30px 34px;
  background: var(--bg);
}

.message {
  width: min(1180px, 100%);
  margin: 0 auto 22px;
  border-left: 3px solid var(--line);
  background: transparent;
  border-radius: 0;
  overflow: hidden;
  padding-left: 14px;
}

.message.user {
  border-left-color: var(--magenta);
}

.message.assistant {
  border-left-color: var(--cyan);
}

.message.command {
  border-left-color: var(--faint);
}

.message.error {
  border-left-color: var(--red);
}

.message-label {
  display: block;
  padding: 0 0 7px;
  border-bottom: 0;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

.message.user .message-label { color: var(--magenta); }
.message.assistant .message-label { color: var(--cyan); }
.message.error .message-label { color: var(--red); }

.message-body {
  margin: 0;
  padding: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
  font-size: 14px;
  line-height: 1.58;
}

.message-meta {
  display: none;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 0 0;
}

.message-meta:not(:empty) {
  display: flex;
}

.meta-pill {
  color: var(--muted);
  border: 1px solid var(--line);
  background: rgba(32, 38, 55, .72);
  border-radius: 6px;
  padding: 4px 7px;
  font-size: 11px;
}

.details {
  margin: 12px 0 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(12, 15, 23, .76);
}

.details summary {
  cursor: pointer;
  color: var(--yellow);
  padding: 8px 10px;
}

.details pre {
  margin: 0;
  padding: 10px;
  border-top: 1px solid var(--line);
  overflow: auto;
  max-height: 320px;
}

.approval-row {
  display: flex;
  gap: 8px;
  padding: 12px 0 0;
}

.approval-row button,
.composer button {
  border: 1px solid var(--cyan);
  background: var(--cyan);
  color: #061014;
  border-radius: 6px;
  font-weight: 800;
  cursor: pointer;
}

.approval-row button.deny {
  border-color: var(--line);
  background: var(--surface-3);
  color: var(--muted);
}

.composer {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 92px;
  gap: 10px;
  align-items: center;
  padding: 12px 14px;
  background: #0b0e16;
  border-top: 1px solid var(--line);
}

.prompt-marker {
  color: var(--cyan);
  font-weight: 900;
  text-align: center;
}

textarea {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  resize: none;
  min-height: 48px;
  max-height: 48px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--text);
  padding: 13px 12px;
  outline: none;
}

textarea:focus {
  border-color: var(--cyan);
}

.composer button {
  height: 48px;
  width: 100%;
  min-width: 0;
  padding: 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 900px) {
  .app-shell {
    grid-template-rows: 98px 220px 1fr 76px;
  }

  .topbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .runtime-strip {
    justify-content: flex-start;
  }

  .panel-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .transcript {
    padding-left: 16px;
    padding-right: 16px;
  }
}

@media (max-width: 560px) {
  .app-shell {
    grid-template-rows: 118px 238px 1fr 76px;
  }

  .topbar {
    padding-left: 14px;
    padding-right: 14px;
  }

  h1 {
    font-size: 21px;
  }

  .tabs {
    padding-left: 10px;
    padding-right: 10px;
  }

  .tab {
    padding: 0 9px;
  }

  .panel-grid {
    grid-template-columns: 1fr;
    padding-left: 10px;
    padding-right: 10px;
  }

  .composer {
    grid-template-columns: 22px minmax(0, 1fr) 68px;
    gap: 8px;
    padding-left: 10px;
    padding-right: 10px;
  }
}
"#;

const APP_JS: &str = r#"const state = {
  status: null,
  panel: 'dashboard',
  busy: false,
  lastResponse: null,
};

const panel = document.querySelector('#panel');
const transcript = document.querySelector('#transcript');
const composer = document.querySelector('#composer');
const promptEl = document.querySelector('#prompt');
const sendBtn = document.querySelector('#send');
const template = document.querySelector('#message-template');

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => switchPanel(tab.dataset.panel, true));
});

composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = promptEl.value.trim();
  if (!text || state.busy) return;
  promptEl.value = '';
  if (text.startsWith('/')) {
    await runCommand(text);
  } else {
    await sendPrompt(text);
  }
});

promptEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

boot();

async function boot() {
  addMessage('system', 'Ready', 'This is no longer a terminal takeover. The browser owns the layout, colors, panels, transcript, and input.');
  await refreshStatus();
  renderPanel();
  promptEl.focus();
}

async function refreshStatus() {
  const response = await fetch('/api/status');
  state.status = await response.json();
  const config = state.status.config || {};
  const provider = config.active_provider || 'unknown';
  const model = config.providers?.[provider]?.model || config.active_model || 'not selected';
  document.querySelector('#provider-pill').textContent = `provider: ${provider}`;
  document.querySelector('#model-pill').textContent = `model: ${model || 'not selected'}`;
  document.querySelector('#workspace-pill').textContent = `workspace: ${state.status.workspace || 'unknown'}`;
}

function switchPanel(name, announce) {
  state.panel = name;
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.panel === name);
  });
  renderPanel();
  if (announce) addMessage('command', `/${name}`, `Switched fixed panel to ${name}.`);
}

async function runCommand(text) {
  const [command] = text.split(/\s+/);
  if (command === '/clear') {
    transcript.innerHTML = '';
    addMessage('command', '/clear', 'Transcript cleared.');
    return;
  }
  if (command === '/dashboard' || command === '/dash' || command === '/status') {
    switchPanel('dashboard', true);
    return;
  }
  if (command === '/models') {
    switchPanel('models', true);
    return;
  }
  if (command === '/agents') {
    switchPanel('agents', true);
    return;
  }
  if (command === '/check') {
    addMessage('command', '/check', 'Refreshing runtime status.');
    await refreshStatus();
    switchPanel('check', false);
    return;
  }
  if (command === '/config') {
    switchPanel('config', true);
    return;
  }
  if (command === '/help' || command === '/menu') {
    switchPanel('help', true);
    return;
  }
  if (command === '/last') {
    if (state.lastResponse) renderResponse(state.lastResponse, true);
    else addMessage('system', '/last', 'No response yet.');
    return;
  }
  if (command === '/run') {
    const task = text.replace(/^\/run\s*/, '').trim();
    if (task) await sendPrompt(task);
    else addMessage('error', '/run', 'Usage: /run your task');
    return;
  }
  addMessage('error', command, 'Unknown command. Use /help for available commands.');
}

async function sendPrompt(text) {
  state.busy = true;
  sendBtn.disabled = true;
  addMessage('user', 'You', text);
  const working = addMessage('system', 'Working', 'Architect routing request\\nExplorer mapping workspace\\nCoder preparing approval-safe output');
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: text }),
    });
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.error || 'Request failed');
    working.remove();
    state.lastResponse = payload;
    renderResponse(payload, false);
    await refreshStatus();
    renderPanel();
  } catch (error) {
    working.remove();
    addMessage('error', 'Error', error.message);
  } finally {
    state.busy = false;
    sendBtn.disabled = false;
    promptEl.focus();
  }
}

function renderResponse(response, replay) {
  const body = response.answer?.trim() || response.headline?.trim() || '(no answer text)';
  const node = addMessage('assistant', replay ? 'Assistant replay' : 'Assistant', body);
  const meta = node.querySelector('.message-meta');
  [
    `status: ${response.status || 'unknown'}`,
    `provider: ${response.provider || 'unknown'}`,
    `model: ${response.model || 'not selected'}`,
    `elapsed: ${response.elapsed_ms || 0} ms`,
  ].forEach((item) => meta.appendChild(pill(item)));
  if (response.file_target) meta.appendChild(pill(`target: ${response.file_target}`));
  if (response.warning) meta.appendChild(pill(`warning: ${response.warning}`));
  if (response.thought_process) node.appendChild(details('Core notes', response.thought_process));
  if (response.diff) node.appendChild(details('Proposed diff', response.diff));
  if (response.actions?.length) node.appendChild(approvalControls(response));
}

function approvalControls(response) {
  const row = document.createElement('div');
  row.className = 'approval-row';
  const apply = document.createElement('button');
  apply.type = 'button';
  apply.textContent = `Apply ${response.actions.length} action(s)`;
  const deny = document.createElement('button');
  deny.type = 'button';
  deny.className = 'deny';
  deny.textContent = 'Deny';
  apply.addEventListener('click', async () => {
    apply.disabled = true;
    deny.disabled = true;
    try {
      const result = await fetch('/api/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actions: response.actions, workspace: response.workspace }),
      });
      const payload = await result.json();
      if (!result.ok || payload.error) throw new Error(payload.error || 'Apply failed');
      addMessage('system', 'Approval', `Applied ${payload.applied?.length || 0} action(s).`);
    } catch (error) {
      addMessage('error', 'Approval failed', error.message);
    }
  });
  deny.addEventListener('click', () => {
    apply.disabled = true;
    deny.disabled = true;
    addMessage('system', 'Approval', 'Denied. No files changed.');
  });
  row.append(apply, deny);
  return row;
}

function renderPanel() {
  const status = state.status || {};
  const config = status.config || {};
  const models = status.models || {};
  const check = status.check || {};
  const provider = config.active_provider || 'unknown';
  const model = config.providers?.[provider]?.model || models.active_model || 'not selected';
  const providers = models.providers || [];
  const totalModels = providers.reduce((sum, item) => sum + (item.models?.length || 0), 0);
  const readyProviders = providers.filter((item) => ['online', 'configured'].includes(item.status)).length;

  const cards = {
    dashboard: [
      ['Active model', `${provider} / ${model || 'not selected'}`],
      ['Workspace', status.workspace || 'unknown'],
      ['Model inventory', `${totalModels} models across ${providers.length} providers, ${readyProviders} ready`],
      ['Runtime', check.error ? check.error : checkLine(check)],
    ],
    models: [
      ['Active', `${provider} / ${model || 'not selected'}`],
      ['Inventory', `${totalModels} models across ${providers.length} providers`],
      ['Providers', providers.slice(0, 6).map((p) => `${p.name || p.id}: ${p.status}, ${p.models?.length || 0} model(s)`).join('\n') || 'No providers reported'],
      ['Config', models.config_path || config.config_path || 'unknown'],
    ],
    agents: [
      ['Architect', 'Owns intake, routing, final response, and approval gate.'],
      ['Explorer', 'Read-only workspace context: files, directories, regex search, git status.'],
      ['Coder', 'Produces approval-safe diffs and file payloads.'],
      ['Approval', 'Side effects require explicit human approval before writes land.'],
    ],
    check: [
      ['Python', check.python || 'unknown'],
      ['ProtoLink', checkLine(check)],
      ['Active provider', `${check.active_provider || provider} / ${check.active_model || model || 'not selected'}`],
      ['Platform', check.platform || 'unknown'],
    ],
    config: [
      ['Config path', config.config_path || 'unknown'],
      ['Active provider', provider],
      ['Active model', model || 'not selected'],
      ['Keys', keySummary(config)],
    ],
    help: [
      ['Chat', 'Type any task. Shift+Enter inserts a newline. Enter sends.'],
      ['Panels', '/dashboard /models /agents /check /config /help'],
      ['Transcript', '/clear /last /run <task>'],
      ['Approvals', 'Actions appear below an answer with Apply and Deny controls.'],
    ],
  };

  panel.innerHTML = '';
  (cards[state.panel] || cards.dashboard).forEach(([title, body]) => {
    const card = document.createElement('section');
    card.className = 'panel-card';
    const h2 = document.createElement('h2');
    h2.textContent = title;
    const p = document.createElement('p');
    p.textContent = body;
    card.append(h2, p);
    panel.appendChild(card);
  });
}

function checkLine(check) {
  if (!check || check.error) return check?.error || 'not checked';
  const proto = check.protolink || {};
  if (proto.installed && proto.agent_ready) {
    return `ProtoLink ${proto.version || 'unknown'} ready, streaming ${proto.streaming_ready ? 'ready' : 'unavailable'}`;
  }
  if (proto.installed) return `ProtoLink blocked: ${proto.error || 'unknown error'}`;
  return `ProtoLink missing: ${proto.error || 'unknown error'}`;
}

function keySummary(config) {
  const providers = config.providers || {};
  const entries = Object.entries(providers).filter(([, value]) => value.api_key_set);
  if (!entries.length) return 'No API keys stored in visible config.';
  return entries.map(([name, value]) => `${name}: ${value.from_env ? 'env' : 'config'}`).join('\n');
}

function addMessage(kind, label, body) {
  const fragment = template.content.cloneNode(true);
  const node = fragment.querySelector('.message');
  node.classList.add(kind);
  node.querySelector('.message-label').textContent = label;
  node.querySelector('.message-body').textContent = body;
  transcript.appendChild(node);
  transcript.scrollTop = transcript.scrollHeight;
  return node;
}

function details(label, body) {
  const root = document.createElement('details');
  root.className = 'details';
  const summary = document.createElement('summary');
  summary.textContent = label;
  const pre = document.createElement('pre');
  pre.textContent = body;
  root.append(summary, pre);
  return root;
}

function pill(text) {
  const span = document.createElement('span');
  span.className = 'meta-pill';
  span.textContent = text;
  return span;
}
"#;
