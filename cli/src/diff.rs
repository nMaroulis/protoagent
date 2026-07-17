#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub(crate) struct DiffReview {
    pub(crate) files: Vec<DiffFile>,
    pub(crate) additions: usize,
    pub(crate) removals: usize,
    pub(crate) hunks: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DiffFile {
    pub(crate) path: String,
    pub(crate) additions: usize,
    pub(crate) removals: usize,
    pub(crate) hunks: usize,
    pub(crate) lines: Vec<DiffLine>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DiffLine {
    pub(crate) kind: DiffLineKind,
    pub(crate) text: String,
    pub(crate) old_line: Option<usize>,
    pub(crate) new_line: Option<usize>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum DiffLineKind {
    Meta,
    Hunk,
    Add,
    Remove,
    Context,
}

impl DiffReview {
    pub(crate) fn line_number_width(&self) -> usize {
        let mut max_line = 0usize;
        for file in &self.files {
            for line in &file.lines {
                if let Some(value) = line.old_line {
                    max_line = max_line.max(value);
                }
                if let Some(value) = line.new_line {
                    max_line = max_line.max(value);
                }
            }
        }
        max_line.to_string().len().max(3)
    }
}

impl DiffFile {
    fn new(path: String) -> Self {
        Self {
            path,
            additions: 0,
            removals: 0,
            hunks: 0,
            lines: Vec::new(),
        }
    }

    fn has_content(&self) -> bool {
        !self.lines.is_empty() || self.additions > 0 || self.removals > 0 || self.hunks > 0
    }

    fn has_changes(&self) -> bool {
        self.additions > 0 || self.removals > 0 || self.hunks > 0
    }
}

pub(crate) fn parse_diff(diff: &str) -> DiffReview {
    let mut review = DiffReview::default();
    let mut current: Option<DiffFile> = None;
    let mut pending_old_path = String::new();
    let mut old_cursor: Option<usize> = None;
    let mut new_cursor: Option<usize> = None;

    for raw_line in diff.lines() {
        if raw_line.starts_with("diff --git ") {
            finish_file(&mut review, &mut current);
            old_cursor = None;
            new_cursor = None;
            pending_old_path.clear();
            let path =
                parse_git_diff_path(raw_line).unwrap_or_else(|| "(patch metadata)".to_string());
            let mut file = DiffFile::new(path);
            push_meta_line(&mut file, raw_line);
            current = Some(file);
            continue;
        }

        if let Some(rest) = raw_line.strip_prefix("--- ") {
            let starts_new_file = match current.as_ref() {
                Some(file) => file.has_changes(),
                None => true,
            };
            if starts_new_file {
                finish_file(&mut review, &mut current);
                old_cursor = None;
                new_cursor = None;
            }
            pending_old_path = clean_diff_path(rest);
            let file = current.get_or_insert_with(|| DiffFile::new(pending_old_path.clone()));
            if file.path == "(patch)" || file.path == "(patch metadata)" {
                file.path = pending_old_path.clone();
            }
            push_meta_line(file, raw_line);
            continue;
        }

        if let Some(rest) = raw_line.strip_prefix("+++ ") {
            let file = current.get_or_insert_with(|| DiffFile::new("(patch)".to_string()));
            let new_path = clean_diff_path(rest);
            if new_path != "/dev/null" {
                file.path = new_path;
            } else if !pending_old_path.is_empty() {
                file.path = pending_old_path.clone();
            }
            push_meta_line(file, raw_line);
            continue;
        }

        let file = current.get_or_insert_with(|| DiffFile::new("(patch)".to_string()));
        let kind = classify_diff_line(raw_line);
        match kind {
            DiffLineKind::Hunk => {
                file.hunks += 1;
                if let Some((old_start, new_start)) = parse_hunk_header(raw_line) {
                    old_cursor = Some(old_start);
                    new_cursor = Some(new_start);
                } else {
                    old_cursor = None;
                    new_cursor = None;
                }
                push_line(file, kind, raw_line, None, None);
            }
            DiffLineKind::Add => {
                file.additions += 1;
                let new_line = take_line_number(&mut new_cursor);
                push_line(file, kind, raw_line, None, new_line);
            }
            DiffLineKind::Remove => {
                file.removals += 1;
                let old_line = take_line_number(&mut old_cursor);
                push_line(file, kind, raw_line, old_line, None);
            }
            DiffLineKind::Context => {
                let (old_line, new_line) = if raw_line.starts_with(' ') {
                    (
                        take_line_number(&mut old_cursor),
                        take_line_number(&mut new_cursor),
                    )
                } else {
                    (None, None)
                };
                push_line(file, kind, raw_line, old_line, new_line);
            }
            DiffLineKind::Meta => push_line(file, kind, raw_line, None, None),
        }
    }

    finish_file(&mut review, &mut current);
    review
}

pub(crate) fn compact_diff_stats(review: &DiffReview) -> String {
    if review.files.is_empty() {
        return "unstructured patch".to_string();
    }
    format!(
        "{} file(s), +{} -{}, {} hunk(s)",
        review.files.len(),
        review.additions,
        review.removals,
        review.hunks
    )
}

pub(crate) fn format_file_heading(file: &DiffFile) -> String {
    format!(
        "{}  +{} -{}  {} hunk(s)",
        file.path, file.additions, file.removals, file.hunks
    )
}

pub(crate) fn format_guttered_line(line: &DiffLine, number_width: usize) -> String {
    let old = format_line_number(line.old_line, number_width);
    let new = format_line_number(line.new_line, number_width);
    match line.kind {
        DiffLineKind::Add => format!("{old} {new} | +{}", diff_body(&line.text, '+')),
        DiffLineKind::Remove => format!("{old} {new} | -{}", diff_body(&line.text, '-')),
        DiffLineKind::Context => format!("{old} {new} |  {}", diff_body(&line.text, ' ')),
        DiffLineKind::Hunk | DiffLineKind::Meta => format!("{old} {new} | {}", line.text),
    }
}

fn finish_file(review: &mut DiffReview, current: &mut Option<DiffFile>) {
    let Some(file) = current.take() else {
        return;
    };
    if !file.has_content() {
        return;
    }
    review.additions += file.additions;
    review.removals += file.removals;
    review.hunks += file.hunks;
    review.files.push(file);
}

fn push_meta_line(file: &mut DiffFile, text: &str) {
    push_line(file, DiffLineKind::Meta, text, None, None);
}

fn push_line(
    file: &mut DiffFile,
    kind: DiffLineKind,
    text: &str,
    old_line: Option<usize>,
    new_line: Option<usize>,
) {
    file.lines.push(DiffLine {
        kind,
        text: text.to_string(),
        old_line,
        new_line,
    });
}

fn classify_diff_line(raw_line: &str) -> DiffLineKind {
    if raw_line.starts_with("@@") {
        DiffLineKind::Hunk
    } else if raw_line.starts_with('+') {
        DiffLineKind::Add
    } else if raw_line.starts_with('-') {
        DiffLineKind::Remove
    } else if raw_line.starts_with('\\') || is_diff_metadata(raw_line) {
        DiffLineKind::Meta
    } else {
        DiffLineKind::Context
    }
}

fn is_diff_metadata(raw_line: &str) -> bool {
    [
        "index ",
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "similarity index ",
        "dissimilarity index ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "Binary files ",
    ]
    .iter()
    .any(|prefix| raw_line.starts_with(prefix))
}

fn parse_git_diff_path(line: &str) -> Option<String> {
    line.split_whitespace()
        .nth(3)
        .map(clean_diff_path)
        .filter(|path| !path.is_empty())
}

fn clean_diff_path(path: &str) -> String {
    let first = path.split_whitespace().next().unwrap_or(path).trim();
    let cleaned = first.trim_matches('"');
    cleaned
        .strip_prefix("a/")
        .or_else(|| cleaned.strip_prefix("b/"))
        .unwrap_or(cleaned)
        .to_string()
}

fn parse_hunk_header(line: &str) -> Option<(usize, usize)> {
    let header = line.strip_prefix("@@")?;
    let end = header.find("@@")?;
    let mut ranges = header[..end].split_whitespace();
    let old = parse_range_start(ranges.next()?, '-')?;
    let new = parse_range_start(ranges.next()?, '+')?;
    Some((old, new))
}

fn parse_range_start(token: &str, prefix: char) -> Option<usize> {
    let value = token.strip_prefix(prefix)?;
    value.split(',').next()?.parse().ok()
}

fn take_line_number(cursor: &mut Option<usize>) -> Option<usize> {
    let current = (*cursor)?;
    *cursor = Some(current.saturating_add(1));
    if current == 0 {
        None
    } else {
        Some(current)
    }
}

fn format_line_number(value: Option<usize>, width: usize) -> String {
    match value {
        Some(number) => format!("{number:>width$}"),
        None => " ".repeat(width),
    }
}

fn diff_body(text: &str, marker: char) -> &str {
    text.strip_prefix(marker).unwrap_or(text)
}

#[cfg(test)]
mod tests {
    use super::{format_guttered_line, parse_diff, DiffLineKind};

    #[test]
    fn parses_git_diff_as_one_file_with_line_numbers() {
        let diff = "\
diff --git a/src/lib.rs b/src/lib.rs
index 1111111..2222222 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -10,3 +10,4 @@
 line a
-old b
+new b
 line c
+line d
";

        let review = parse_diff(diff);

        assert_eq!(review.files.len(), 1);
        let file = &review.files[0];
        assert_eq!(file.path, "src/lib.rs");
        assert_eq!(file.additions, 2);
        assert_eq!(file.removals, 1);
        assert_eq!(file.hunks, 1);

        let removal = file
            .lines
            .iter()
            .find(|line| line.kind == DiffLineKind::Remove)
            .unwrap();
        assert_eq!(removal.old_line, Some(11));
        assert_eq!(removal.new_line, None);

        let additions = file
            .lines
            .iter()
            .filter(|line| line.kind == DiffLineKind::Add)
            .collect::<Vec<_>>();
        assert_eq!(additions[0].new_line, Some(11));
        assert_eq!(additions[1].new_line, Some(13));
        assert_eq!(format_guttered_line(additions[0], 3), "     11 | +new b");
    }

    #[test]
    fn parses_plain_unified_diff_file_boundaries() {
        let diff = "\
--- a/one.txt
+++ b/one.txt
@@ -1 +1 @@
-old
+new
--- a/two.txt
+++ b/two.txt
@@ -4,0 +5 @@
+added
";

        let review = parse_diff(diff);

        assert_eq!(review.files.len(), 2);
        assert_eq!(review.files[0].path, "one.txt");
        assert_eq!(review.files[0].additions, 1);
        assert_eq!(review.files[0].removals, 1);
        assert_eq!(review.files[1].path, "two.txt");
        assert_eq!(review.files[1].additions, 1);
        assert_eq!(review.files[1].removals, 0);
    }
}
