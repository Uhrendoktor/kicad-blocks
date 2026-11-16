#![forbid(unsafe_code)]

//! Lossless `KiCad` schematic parsing over the kiutils S-expression CST.

mod schematic;

pub use schematic::{SchematicDocument, SchematicParseError};
