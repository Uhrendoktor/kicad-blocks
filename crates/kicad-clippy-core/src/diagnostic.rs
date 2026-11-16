use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::source::ByteSpan;

/// Diagnostic severity used by native ERC and custom rules.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    /// A blocking finding.
    Error,
    /// A non-blocking finding.
    Warning,
    /// Informational output.
    Note,
}

impl Severity {
    /// Returns the stable lowercase label used in text output.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Error => "error",
            Self::Warning => "warning",
            Self::Note => "note",
        }
    }
}

/// A schematic coordinate in millimetres, independent of source byte offsets.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct SchematicLocation {
    /// X coordinate in millimetres.
    pub x_mm: f64,
    /// Y coordinate in millimetres.
    pub y_mm: f64,
}

/// An affected item related to a diagnostic.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RelatedLocation {
    /// Human-readable description supplied by KiCad or a rule.
    pub description: String,
    /// KiCad item UUID, when available.
    pub uuid: Option<String>,
    /// Physical schematic location, when available.
    pub location: Option<SchematicLocation>,
}

/// A normalized lint or ERC finding.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Diagnostic {
    /// Stable rule identifier.
    pub code: String,
    /// Severity after the rule's configured level is applied.
    pub severity: Severity,
    /// One-line finding summary.
    pub message: String,
    /// Longer explanation of why the finding matters.
    pub explanation: Option<String>,
    /// Suggested next action.
    pub help: Option<String>,
    /// Source file associated with the finding.
    pub source: Option<PathBuf>,
    /// Source byte span, when the finding maps to text.
    pub span: Option<ByteSpan>,
    /// KiCad sheet path for hierarchical designs.
    pub sheet_path: Option<String>,
    /// Physical schematic location in millimetres.
    pub schematic_location: Option<SchematicLocation>,
    /// Additional affected items.
    pub related: Vec<RelatedLocation>,
    /// Whether KiCad marked the finding as excluded.
    pub excluded: bool,
}

impl Diagnostic {
    /// Creates a diagnostic with optional context left empty.
    #[must_use]
    pub fn new(code: impl Into<String>, severity: Severity, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            severity,
            message: message.into(),
            explanation: None,
            help: None,
            source: None,
            span: None,
            sheet_path: None,
            schematic_location: None,
            related: Vec::new(),
            excluded: false,
        }
    }

    /// Returns whether this diagnostic is an active error.
    #[must_use]
    pub const fn is_active_error(&self) -> bool {
        matches!(self.severity, Severity::Error) && !self.excluded
    }
}

#[cfg(test)]
mod tests {
    use super::{Diagnostic, Severity};

    #[test]
    fn excluded_errors_do_not_fail_a_run() {
        let mut diagnostic = Diagnostic::new("ERC/EXAMPLE", Severity::Error, "sample");
        diagnostic.excluded = true;

        assert!(!diagnostic.is_active_error());
    }
}
