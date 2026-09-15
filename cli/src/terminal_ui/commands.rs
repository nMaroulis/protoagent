//! Discoverable command templates shared by the composer hint and Tab picker.

use std::sync::OnceLock;

#[derive(serde::Deserialize)]
struct CommandReference {
    tui: Vec<(String, String)>,
}

fn commands() -> &'static [(String, String)] {
    static COMMANDS: OnceLock<CommandReference> = OnceLock::new();
    &COMMANDS
        .get_or_init(|| {
            serde_json::from_str(include_str!(
                "../../../core/protoagent_core/command_reference.json"
            ))
            .expect("bundled command reference")
        })
        .tui
}

/// Prefer prefix matches, then ordered-character fuzzy matches.
pub(super) fn matching_commands(query: &str) -> Vec<(&'static str, &'static str)> {
    let query = query.trim().to_lowercase();
    let mut matches: Vec<_> = commands()
        .iter()
        .map(|(command, description)| (command.as_str(), description.as_str()))
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
