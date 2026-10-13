//! Bounded, model-free project inspection shared by shell commands and the TUI.

use anyhow::{anyhow, Context, Result};
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

pub(crate) const MAX_ENTRIES: usize = 2_000;
pub(crate) const MAX_VIEW_BYTES: usize = 1_048_576;
const MAX_CAT_CHARS: usize = 32_768;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum EntryKind {
    Directory,
    File,
    Link,
    Other,
}

#[derive(Debug)]
pub(crate) struct Entry {
    pub(crate) path: PathBuf,
    pub(crate) name: String,
    pub(crate) kind: EntryKind,
}

impl Entry {
    pub(crate) fn label(&self) -> String {
        let marker = match self.kind {
            EntryKind::Directory => "DIR ",
            EntryKind::File => "FILE",
            EntryKind::Link => "LINK",
            EntryKind::Other => "    ",
        };
        format!("{marker}  {}", display_label(&self.name))
    }
}

pub(crate) struct Directory {
    pub(crate) path: PathBuf,
    pub(crate) entries: Vec<Entry>,
    pub(crate) truncated: bool,
}

pub(crate) struct TextFile {
    pub(crate) path: PathBuf,
    pub(crate) content: String,
    pub(crate) truncated: bool,
}

/// TUI arguments accept a complete path, optionally quoted or prefixed with @.
pub(crate) fn path_argument(argument: &str) -> Result<String> {
    let value = argument.trim().strip_prefix('@').unwrap_or(argument.trim());
    if let Some(quote) = value.chars().next().filter(|ch| matches!(ch, '\'' | '"')) {
        if value.len() < 2 || !value.ends_with(quote) {
            return Err(anyhow!("Close the quoted file path"));
        }
        let inner = &value[quote.len_utf8()..value.len() - quote.len_utf8()];
        if quote == '"' {
            return Ok(inner.replace("\\\"", "\"").replace("\\\\", "\\"));
        }
        return Ok(inner.to_string());
    }
    Ok(value.to_string())
}

pub(crate) fn resolve(root: &Path, argument: &str) -> Result<PathBuf> {
    let root = root
        .canonicalize()
        .context("The selected project is unavailable")?;
    let path = Path::new(argument);
    let target = if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    };
    let target = target
        .canonicalize()
        .with_context(|| format!("Cannot open {}", display_text(argument)))?;
    if !target.starts_with(&root) {
        return Err(anyhow!(
            "Choose a path inside the active project; /project selects another project"
        ));
    }
    Ok(target)
}

pub(crate) fn list_directory(root: &Path, argument: &str) -> Result<Directory> {
    let path = resolve(root, argument)?;
    if !path.is_dir() {
        return Err(anyhow!("This path is a file. Use /open or /cat to view it"));
    }
    let mut entries = Vec::new();
    let mut truncated = false;
    for entry in fs::read_dir(&path)? {
        let entry = entry?;
        if entries.len() == MAX_ENTRIES {
            truncated = true;
            break;
        }
        let kind = entry.file_type()?;
        entries.push(Entry {
            name: entry.file_name().to_string_lossy().to_string(),
            path: entry.path(),
            kind: if kind.is_symlink() {
                EntryKind::Link
            } else if kind.is_dir() {
                EntryKind::Directory
            } else if kind.is_file() {
                EntryKind::File
            } else {
                EntryKind::Other
            },
        });
    }
    entries.sort_by(|a, b| {
        (a.kind != EntryKind::Directory)
            .cmp(&(b.kind != EntryKind::Directory))
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(Directory {
        path,
        entries,
        truncated,
    })
}

pub(crate) fn directory_text(root: &Path, argument: &str) -> Result<String> {
    let directory = list_directory(root, argument)?;
    let mut rows = vec![display_label(&directory.path.to_string_lossy())];
    rows.extend(directory.entries.iter().map(Entry::label));
    if directory.entries.is_empty() {
        rows.push("(empty directory)".into());
    }
    if directory.truncated {
        rows.push(format!(
            "Showing {MAX_ENTRIES} entries; open a known path directly for additional files."
        ));
    }
    Ok(rows.join("\n"))
}

pub(crate) fn read_text(root: &Path, argument: &str) -> Result<TextFile> {
    let path = resolve(root, argument)?;
    if !fs::metadata(&path)?.is_file() {
        return Err(anyhow!(
            "Choose a regular text file; /browse navigates directories"
        ));
    }
    let mut bytes = Vec::new();
    File::open(&path)?
        .take((MAX_VIEW_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    let truncated = bytes.len() > MAX_VIEW_BYTES;
    bytes.truncate(MAX_VIEW_BYTES);
    if bytes.contains(&0) {
        return Err(anyhow!("Binary files cannot be displayed as text"));
    }
    let content = match String::from_utf8(bytes) {
        Ok(value) => value,
        Err(error) if truncated && error.utf8_error().error_len().is_none() => {
            let valid = error.utf8_error().valid_up_to();
            String::from_utf8(error.into_bytes()[..valid].to_vec())?
        }
        Err(_) => return Err(anyhow!("This file is not UTF-8 text")),
    };
    Ok(TextFile {
        path,
        content: display_text(&content),
        truncated,
    })
}

pub(crate) fn cat_text(file: &TextFile) -> String {
    let end = file
        .content
        .char_indices()
        .nth(MAX_CAT_CHARS)
        .map(|(offset, _)| offset);
    let mut text = file.content[..end.unwrap_or(file.content.len())].to_string();
    if file.truncated || end.is_some() {
        text.push_str("\n[Preview truncated. Use /open for the scrollable file viewer; the viewer loads at most 1 MiB.]\n");
    }
    text
}

/// Keep terminal control sequences inert without changing project files.
pub(crate) fn display_text(value: &str) -> String {
    let mut out = String::new();
    for ch in value.replace("\r\n", "\n").chars() {
        if ch.is_control() && !matches!(ch, '\n' | '\t') {
            out.push_str(&format!("\\x{:02x}", ch as u32));
        } else {
            out.push(ch);
        }
    }
    out
}

pub(crate) fn display_label(value: &str) -> String {
    display_text(value)
        .replace('\n', "\\n")
        .replace('\t', "\\t")
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "protoagent-files-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn directory_listing_is_shallow_sorted_and_includes_hidden_files() {
        let fixture = Fixture::new();
        fs::create_dir(fixture.0.join("src")).unwrap();
        fs::write(fixture.0.join("src/nested.py"), "source").unwrap();
        fs::write(fixture.0.join("z.py"), "z").unwrap();
        fs::write(fixture.0.join(".env"), "local config").unwrap();
        let list = list_directory(&fixture.0, ".").unwrap();
        assert_eq!(list.entries.len(), 3);
        assert_eq!(list.entries[0].name, "src");
        assert!(directory_text(&fixture.0, ".").unwrap().contains(".env"));
        assert!(!list.truncated);
    }
    #[test]
    fn file_reads_support_quoted_unicode_paths_and_inert_controls() {
        let fixture = Fixture::new();
        let name = "my files résumé.py";
        fs::write(fixture.0.join(name), "print('hi')\r\n\x1b[2J").unwrap();
        let path = path_argument(&format!("@\"{name}\"")).unwrap();
        let file = read_text(&fixture.0, &path).unwrap();
        assert_eq!(cat_text(&file), "print('hi')\n\\x1b[2J");
        assert!(!file.truncated);
        assert!(path_argument("\"missing quote").is_err());
        assert!(read_text(&fixture.0, ".").is_err());
    }
    #[test]
    fn previews_are_bounded_and_keep_utf8_at_the_byte_limit() {
        let fixture = Fixture::new();
        let text = "a".repeat(MAX_VIEW_BYTES - 1) + "é-more";
        fs::write(fixture.0.join("large.txt"), text).unwrap();
        let file = read_text(&fixture.0, "large.txt").unwrap();
        assert!(file.truncated);
        assert_eq!(file.content.len(), MAX_VIEW_BYTES - 1);
        assert!(cat_text(&file).contains("Preview truncated"));
        fs::write(fixture.0.join("binary"), [0, 1, 2]).unwrap();
        assert!(read_text(&fixture.0, "binary").is_err());
    }
    #[test]
    fn paths_and_links_cannot_escape_the_selected_project() {
        let fixture = Fixture::new();
        let outside = Fixture::new();
        fs::write(outside.0.join("private.txt"), "outside").unwrap();
        assert!(resolve(&fixture.0, &outside.0.to_string_lossy()).is_err());
        assert!(resolve(&fixture.0, "..").is_err());
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(outside.0.join("private.txt"), fixture.0.join("link"))
                .unwrap();
            assert!(read_text(&fixture.0, "link").is_err());
            assert_eq!(
                list_directory(&fixture.0, ".").unwrap().entries[0].kind,
                EntryKind::Link
            );
        }
    }
}
