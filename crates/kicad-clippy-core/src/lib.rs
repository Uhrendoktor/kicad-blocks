
#![forbid(unsafe_code)]

//! Shared data contracts for the kicad-clippy schematic linter.

pub mod diagnostic;
pub mod rules;
pub mod source;

pub use diagnostic::{Diagnostic, RelatedLocation, SchematicLocation, Severity};
pub use rules::{RuleDescriptor, RuleLevel, RuleRegistry, RuleRegistryError};
pub use source::{ByteSpan, LineColumn, SourceFile};
