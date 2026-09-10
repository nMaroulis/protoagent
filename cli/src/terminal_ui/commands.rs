//! Discoverable command templates shared by the composer hint and Tab picker.

pub(super) const COMMANDS: &[(&str, &str)] = &[
    ("/dashboard", "Project and runtime overview"),
    ("/project", "Choose a workspace"),
    ("/project clear", "Clear the active workspace"),
    ("/model", "Choose provider and model"),
    ("/models", "Model inventory"),
    ("/key", "Store a provider API key"),
    ("/agents", "Agent roles and capabilities"),
    (
        "/agents profile",
        "Choose auto, small, medium, large, or api",
    ),
    ("/agents scout on", "Enable public web research"),
    ("/agents scout off", "Disable public web research"),
    ("/context", "Inspect or preview workspace evidence"),
    ("/context history", "Inspect model-facing memory"),
    ("/context compact", "Compact model-facing memory"),
    ("/context reset", "Clear project conversation memory"),
    ("/context window", "Set an Ollama context window"),
    ("/context on", "Enable persistent conversation memory"),
    ("/context off", "Use task-local conversation memory"),
    ("/index refresh", "Refresh Context Loom"),
    ("/sessions", "List saved project sessions"),
    ("/session resume", "Reopen a saved workspace"),
    ("/session rename", "Name the current session"),
    ("/checkpoints", "List recoverable agent file writes"),
    (
        "/undo",
        "Review and undo the latest file write, or supply its ID",
    ),
    ("/diff", "Review the latest diff"),
    ("/timeline", "Inspect the latest agent path"),
    ("/trace", "Inspect runtime events"),
    ("/check", "Check runtime readiness"),
    ("/config", "Show redacted configuration"),
    ("/version", "Show component versions"),
    ("/help", "Show help or ask Guide a question"),
    ("/last", "Replay the last response"),
    ("/run", "Run a task"),
    ("/clear", "Clear the visible transcript"),
    ("/quit", "Exit ProtoAgent"),
];

/// Prefer prefix matches, then ordered-character fuzzy matches.
pub(super) fn matching_commands(query: &str) -> Vec<(&'static str, &'static str)> {
    let query = query.trim().to_lowercase();
    let mut matches: Vec<_> = COMMANDS
        .iter()
        .copied()
        .filter(|(command, _)| {
            let mut characters = command.chars();
            query
                .chars()
                .all(|needle| characters.any(|ch| ch == needle))
        })
        .collect();
    matches.sort_by_key(|(command, _)| (!command.starts_with(&query), command.len()));
    matches
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ranks_prefixes_and_resolves_fuzzy_templates() {
        assert_eq!(matching_commands("/cont")[0].0, "/context");
        assert!(matching_commands("/ctcmp")
            .iter()
            .any(|item| item.0 == "/context compact"));
        assert!(matching_commands("/unknownzz").is_empty());
    }
}
