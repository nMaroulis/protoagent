use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::{timeline, CoreResponse};

const MAX_SESSIONS: usize = 40;
const MAX_TURNS_PER_SESSION: usize = 60;
const PREVIEW_CHARS: usize = 420;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub(crate) struct SessionStore {
    #[serde(default)]
    pub(crate) sessions: Vec<SessionRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SessionRecord {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) workspace: String,
    pub(crate) created_at: u64,
    pub(crate) updated_at: u64,
    pub(crate) turns: usize,
    #[serde(default)]
    pub(crate) last_prompt: String,
    #[serde(default)]
    pub(crate) last_status: String,
    #[serde(default)]
    pub(crate) provider: String,
    #[serde(default)]
    pub(crate) model: String,
    #[serde(default)]
    pub(crate) elapsed_ms: u64,
    #[serde(default)]
    pub(crate) history: Vec<SessionTurn>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SessionTurn {
    pub(crate) at: u64,
    pub(crate) prompt: String,
    pub(crate) answer_preview: String,
    pub(crate) status: String,
    pub(crate) provider: String,
    pub(crate) model: String,
    pub(crate) responder: String,
    pub(crate) elapsed_ms: u64,
    pub(crate) event_count: usize,
    pub(crate) requires_approval: bool,
    pub(crate) file_target: String,
    #[serde(default)]
    pub(crate) timeline: Vec<timeline::TimelineItem>,
}

pub(crate) fn record_turn(prompt: &str, response: &CoreResponse) -> Result<()> {
    let workspace = if response.workspace.trim().is_empty() {
        crate::workspace_dir_string()
    } else {
        response.workspace.clone()
    };
    let now = now_secs();
    let id = crate::project_session_id(&workspace);
    let mut store = load_store();
    let position = store
        .sessions
        .iter()
        .position(|session| session.id == id)
        .unwrap_or_else(|| {
            store.sessions.push(SessionRecord {
                id: id.clone(),
                name: workspace_name(&workspace),
                workspace: workspace.clone(),
                created_at: now,
                updated_at: now,
                turns: 0,
                last_prompt: String::new(),
                last_status: String::new(),
                provider: String::new(),
                model: String::new(),
                elapsed_ms: 0,
                history: Vec::new(),
            });
            store.sessions.len() - 1
        });

    let session = &mut store.sessions[position];
    session.workspace = workspace.clone();
    session.updated_at = now;
    session.turns = session.turns.saturating_add(1);
    session.last_prompt = prompt.to_string();
    session.last_status = response.status.clone();
    session.provider = response.provider.clone();
    session.model = response.model.clone();
    session.elapsed_ms = response.elapsed_ms;
    session.history.push(SessionTurn {
        at: now,
        prompt: prompt.to_string(),
        answer_preview: preview_text(&response.answer.if_empty(&response.headline)),
        status: response.status.clone(),
        provider: response.provider.clone(),
        model: response.model.clone(),
        responder: response.responder.clone(),
        elapsed_ms: response.elapsed_ms,
        event_count: response.events.len(),
        requires_approval: response.requires_approval || !response.actions.is_empty(),
        file_target: response.file_target.clone(),
        timeline: timeline::build_timeline(&response.events),
    });
    if session.history.len() > MAX_TURNS_PER_SESSION {
        let overflow = session.history.len() - MAX_TURNS_PER_SESSION;
        session.history.drain(0..overflow);
    }

    store.sessions.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    store.sessions.truncate(MAX_SESSIONS);
    save_store(&store)
}

pub(crate) fn load_store() -> SessionStore {
    let path = sessions_path();
    let Ok(raw) = fs::read_to_string(path) else {
        return SessionStore::default();
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

pub(crate) fn active_session() -> Option<SessionRecord> {
    let workspace = crate::active_project_dir()?.to_string_lossy().to_string();
    let id = crate::project_session_id(&workspace);
    load_store().sessions.into_iter().find(|session| session.id == id)
}

pub(crate) fn recent_sessions() -> Vec<SessionRecord> {
    load_store().sessions
}

pub(crate) fn rename_current(name: &str) -> Result<()> {
    let workspace = crate::require_project_dir_string()?;
    let id = crate::project_session_id(&workspace);
    let mut store = load_store();
    if let Some(session) = store.sessions.iter_mut().find(|session| session.id == id) {
        session.name = name.trim().to_string();
        session.updated_at = now_secs();
    } else {
        store.sessions.insert(
            0,
            SessionRecord {
                id,
                name: name.trim().to_string(),
                workspace,
                created_at: now_secs(),
                updated_at: now_secs(),
                turns: 0,
                last_prompt: String::new(),
                last_status: "new".to_string(),
                provider: String::new(),
                model: String::new(),
                elapsed_ms: 0,
                history: Vec::new(),
            },
        );
    }
    save_store(&store)
}

pub(crate) fn session_panel_rows() -> Vec<String> {
    let store = load_store();
    let active = crate::active_project_dir().map(|path| crate::project_session_id(&path.to_string_lossy()));
    let mut rows = Vec::new();
    if let Some(session) = active_session() {
        rows.push(format!(
            "Current: {} | {} turn(s) | updated {}",
            session.name,
            session.turns,
            relative_time(session.updated_at)
        ));
        rows.push(format!("Session id: {}", short_id(&session.id)));
        rows.push(format!("Workspace: {}", session.workspace));
    } else if let Some(project) = crate::active_project_dir() {
        rows.push(format!("Current: {} | no recorded turns yet", project.to_string_lossy()));
    } else {
        rows.push("Current: no project selected".to_string());
    }

    rows.push(format!("Store: {}", sessions_path().to_string_lossy()));
    rows.push("Commands: /sessions choose | /session rename NAME | /timeline".to_string());

    if store.sessions.is_empty() {
        rows.push("Recent: no saved sessions yet.".to_string());
        return rows;
    }

    rows.push("Recent sessions:".to_string());
    for (idx, session) in store.sessions.iter().take(8).enumerate() {
        let marker = if active.as_deref() == Some(session.id.as_str()) { "*" } else { " " };
        rows.push(format!(
            "{} {:>2}. {} | {} turn(s) | {} | {}",
            marker,
            idx + 1,
            session.name,
            session.turns,
            relative_time(session.updated_at),
            session.workspace
        ));
    }
    rows
}

pub(crate) fn session_detail(session: &SessionRecord) -> String {
    let mut rows = vec![
        format!("Session: {} ({})", session.name, short_id(&session.id)),
        format!("Workspace: {}", session.workspace),
        format!("Turns: {} | updated {}", session.turns, relative_time(session.updated_at)),
    ];
    for turn in session.history.iter().rev().take(6) {
        rows.push(format!(
            "- {} | {} / {} | {} ms | {}",
            relative_time(turn.at),
            turn.provider.if_empty("unknown"),
            turn.model.if_empty("not selected"),
            turn.elapsed_ms,
            turn.prompt
        ));
    }
    rows.join("\n")
}

pub(crate) fn sessions_path() -> std::path::PathBuf {
    crate::protoagent_config_dir().join("sessions.json")
}

fn save_store(store: &SessionStore) -> Result<()> {
    let path = sessions_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let raw = serde_json::to_string_pretty(store)?;
    fs::write(path, raw)?;
    Ok(())
}

fn workspace_name(workspace: &str) -> String {
    std::path::Path::new(workspace)
        .file_name()
        .and_then(|name| name.to_str())
        .map(str::to_string)
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| workspace.to_string())
}

fn preview_text(value: &str) -> String {
    let value = value.trim();
    if value.chars().count() <= PREVIEW_CHARS {
        return value.to_string();
    }
    let mut preview = value.chars().take(PREVIEW_CHARS.saturating_sub(3)).collect::<String>();
    preview.push_str("...");
    preview
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn relative_time(timestamp: u64) -> String {
    let now = now_secs();
    let age = now.saturating_sub(timestamp);
    if age < 60 {
        format!("{age}s ago")
    } else if age < 3_600 {
        format!("{}m ago", age / 60)
    } else if age < 86_400 {
        format!("{}h ago", age / 3_600)
    } else {
        format!("{}d ago", age / 86_400)
    }
}

fn short_id(id: &str) -> String {
    id.chars().rev().take(8).collect::<String>().chars().rev().collect()
}

trait EmptyFallback {
    fn if_empty<'a>(&'a self, fallback: &'a str) -> &'a str;
}

impl EmptyFallback for String {
    fn if_empty<'a>(&'a self, fallback: &'a str) -> &'a str {
        if self.trim().is_empty() { fallback } else { self.as_str() }
    }
}

impl EmptyFallback for str {
    fn if_empty<'a>(&'a self, fallback: &'a str) -> &'a str {
        if self.trim().is_empty() { fallback } else { self }
    }
}
