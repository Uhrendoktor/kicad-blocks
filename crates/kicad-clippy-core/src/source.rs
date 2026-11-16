use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use serde::{Deserialize, Serialize};

/// A half-open byte range in the original UTF-8 source.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
pub struct ByteSpan {
    /// First byte included in the span.
    pub start: usize,
    /// First byte after the span.
    pub end: usize,
}

impl ByteSpan {
    /// Creates a byte range. Validate it against a source before slicing.
    #[must_use]
    pub const fn new(start: usize, end: usize) -> Self {
        Self { start, end }
    }

    /// Returns whether the range is empty.
    #[must_use]
    pub const fn is_empty(self) -> bool {
        self.start == self.end
    }

    /// Returns whether this range is ordered, in bounds, and on UTF-8 boundaries.
    #[must_use]
    pub fn is_valid_for(self, source: &str) -> bool {
        self.start <= self.end
            && self.end <= source.len()
            && source.is_char_boundary(self.start)
            && source.is_char_boundary(self.end)
    }
}

/// A one-based line and Unicode-scalar column.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LineColumn {
    /// One-based line number.
    pub line: usize,
    /// One-based Unicode-scalar column.
    pub column: usize,
}

/// Source text plus a byte-offset-to-line index.
#[derive(Clone, Debug)]
pub struct SourceFile {
    path: PathBuf,
    text: Arc<str>,
    line_starts: Vec<usize>,
}

impl SourceFile {
    /// Creates a source file and builds its line-start index once.
    #[must_use]
    pub fn new(path: impl Into<PathBuf>, text: impl Into<String>) -> Self {
        let text = text.into();
        let mut line_starts = vec![0];

        for (offset, byte) in text.bytes().enumerate() {
            if byte == b'\n' {
                line_starts.push(offset + 1);
            }
        }

        Self {
            path: path.into(),
            text: Arc::from(text),
            line_starts,
        }
    }

    /// Returns the source path supplied by the caller.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Returns the original UTF-8 source text.
    #[must_use]
    pub fn text(&self) -> &str {
        &self.text
    }

    /// Converts a valid UTF-8 byte offset into a one-based line and column.
    #[must_use]
    pub fn line_column(&self, offset: usize) -> Option<LineColumn> {
        if offset > self.text.len() || !self.text.is_char_boundary(offset) {
            return None;
        }

        let line_index = self
            .line_starts
            .partition_point(|line_start| *line_start <= offset)
            .saturating_sub(1);
        let line_start = *self.line_starts.get(line_index)?;
        let prefix = self.text.get(line_start..offset)?;

        Some(LineColumn {
            line: line_index + 1,
            column: prefix.chars().count() + 1,
        })
    }

    /// Returns the source slice covered by a valid byte span.
    #[must_use]
    pub fn slice(&self, span: ByteSpan) -> Option<&str> {
        if !span.is_valid_for(&self.text) {
            return None;
        }

        self.text.get(span.start..span.end)
    }
}

#[cfg(test)]
mod tests {
    use super::{ByteSpan, SourceFile};

    #[test]
    fn maps_ascii_offsets_to_one_based_locations() {
        let source = SourceFile::new("example.kicad_sch", "alpha\nbeta");

        assert_eq!(
            source.line_column(0).map(|v| (v.line, v.column)),
            Some((1, 1))
        );
        assert_eq!(
            source.line_column(3).map(|v| (v.line, v.column)),
            Some((1, 4))
        );
        assert_eq!(
            source.line_column(6).map(|v| (v.line, v.column)),
            Some((2, 1))
        );
        assert_eq!(
            source.line_column(10).map(|v| (v.line, v.column)),
            Some((2, 5))
        );
    }

    #[test]
    fn columns_count_unicode_scalars_and_reject_interior_utf8_offsets() {
        let source = SourceFile::new("unicode.kicad_sch", "Aé日本\nB");

        assert_eq!(
            source.line_column(1).map(|v| (v.line, v.column)),
            Some((1, 2))
        );
        assert_eq!(
            source.line_column(3).map(|v| (v.line, v.column)),
            Some((1, 3))
        );
        assert_eq!(source.line_column(2), None);
        assert_eq!(
            source
                .line_column(source.text().len())
                .map(|v| (v.line, v.column)),
            Some((2, 2))
        );
    }

    #[test]
    fn slices_only_valid_half_open_utf8_ranges() {
        let source = SourceFile::new("slice.kicad_sch", "café schematic");

        assert_eq!(source.slice(ByteSpan::new(0, 5)), Some("café"));
        assert_eq!(source.slice(ByteSpan::new(4, 8)), None);
        assert_eq!(source.slice(ByteSpan::new(9, 3)), None);
        assert_eq!(source.slice(ByteSpan::new(0, 99)), None);
    }
}
