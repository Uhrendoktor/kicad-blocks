use std::collections::{BTreeMap, btree_map::Entry};

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Effective level for a configurable custom rule.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RuleLevel {
    /// Disable the rule.
    Allow,
    /// Emit a warning.
    Warn,
    /// Emit an error.
    Deny,
}

/// Stable metadata used to document and configure a rule.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RuleDescriptor {
    /// Stable identifier, for example SCH001.
    pub id: String,
    /// Short display name.
    pub name: String,
    /// Rule category such as electrical, layout, or documentation.
    pub category: String,
    /// Concise statement of what the rule checks.
    pub summary: String,
    /// Longer explanation of the design reason for the rule.
    pub explanation: String,
    /// Default level when no configuration overrides it.
    pub default_level: RuleLevel,
    /// Optional documentation URL.
    pub documentation_url: Option<String>,
    /// Searchable tags.
    pub tags: Vec<String>,
}

impl RuleDescriptor {
    /// Creates a descriptor with no external documentation or tags.
    #[must_use]
    pub fn new(
        id: impl Into<String>,
        name: impl Into<String>,
        category: impl Into<String>,
        summary: impl Into<String>,
        explanation: impl Into<String>,
        default_level: RuleLevel,
    ) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            category: category.into(),
            summary: summary.into(),
            explanation: explanation.into(),
            default_level,
            documentation_url: None,
            tags: Vec::new(),
        }
    }
}

/// Error raised when a rule identifier is registered more than once.
#[derive(Debug, Error, Eq, PartialEq)]
#[error("rule identifier {0:?} is already registered")]
pub struct RuleRegistryError(pub String);

/// Deterministic catalogue of rule metadata.
#[derive(Clone, Debug, Default)]
pub struct RuleRegistry {
    rules: BTreeMap<String, RuleDescriptor>,
}

impl RuleRegistry {
    /// Creates an empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Registers a unique rule descriptor.
    ///
    /// # Errors
    ///
    /// Returns [`RuleRegistryError`] when another rule already uses the identifier.
    pub fn register(&mut self, descriptor: RuleDescriptor) -> Result<(), RuleRegistryError> {
        match self.rules.entry(descriptor.id.clone()) {
            Entry::Vacant(entry) => {
                entry.insert(descriptor);
                Ok(())
            }
            Entry::Occupied(entry) => Err(RuleRegistryError(entry.key().clone())),
        }
    }

    /// Looks up a descriptor by its stable identifier.
    #[must_use]
    pub fn get(&self, id: &str) -> Option<&RuleDescriptor> {
        self.rules.get(id)
    }

    /// Iterates descriptors in stable identifier order.
    pub fn iter(&self) -> impl Iterator<Item = &RuleDescriptor> {
        self.rules.values()
    }

    /// Returns the number of registered descriptors.
    #[must_use]
    pub fn len(&self) -> usize {
        self.rules.len()
    }

    /// Returns whether no descriptors are registered.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.rules.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::{RuleDescriptor, RuleLevel, RuleRegistry};

    fn sample_rule() -> RuleDescriptor {
        RuleDescriptor::new(
            "SCH001",
            "wire geometry",
            "layout",
            "Keep wires orthogonal.",
            "Orthogonal routing is easier to inspect and maintain.",
            RuleLevel::Deny,
        )
    }

    #[test]
    fn registry_is_deterministic_and_rejects_duplicate_ids() {
        let mut registry = RuleRegistry::new();

        assert!(registry.register(sample_rule()).is_ok());
        assert_eq!(
            registry.get("SCH001").map(|rule| rule.name.as_str()),
            Some("wire geometry")
        );
        assert!(registry.register(sample_rule()).is_err());
        assert_eq!(registry.len(), 1);
    }
}
