#![forbid(unsafe_code)]

use std::{
    io::{self, Write},
    path::{Path, PathBuf},
    process::{Command, ExitCode},
};

use clap::{Parser, Subcommand, ValueEnum};
use kicad_clippy_core::{Diagnostic, Severity};
use kicad_clippy_erc::run_native_erc;
use kicad_clippy_parser::SchematicDocument;
use serde::Serialize;
use thiserror::Error;

/// Clippy-inspired checks for KiCad schematic source files.
#[derive(Debug, Parser)]
#[command(version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Parse a schematic and run KiCad's native Electrical Rules Checker.
    Lint {
        /// Schematic source file to inspect.
        schematic: PathBuf,
        /// Output format.
        #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
        format: OutputFormat,
        /// Parse the file without launching KiCad ERC.
        #[arg(long)]
        skip_erc: bool,
        /// Explicit path or command name for kicad-cli.
        #[arg(long, value_name = "PATH")]
        kicad_cli: Option<PathBuf>,
    },
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum OutputFormat {
    /// Human-readable diagnostics.
    Text,
    /// Structured JSON diagnostics.
    Json,
}

#[derive(Debug, Error)]
enum CliError {
    #[error(transparent)]
    Schematic(#[from] kicad_clippy_parser::SchematicParseError),
    #[error(transparent)]
    Erc(#[from] kicad_clippy_erc::ErcError),
    #[error("KiCad CLI was not found; install KiCad or pass --kicad-cli PATH")]
    KiCadCliNotFound,
    #[error("failed to serialize JSON output: {0}")]
    Json(#[from] serde_json::Error),
    #[error("failed to write CLI output: {0}")]
    Io(#[from] io::Error),
}

#[derive(Debug, Serialize)]
struct LintOutput {
    schema_version: &'static str,
    tool_version: &'static str,
    schematic: String,
    syntax_valid: bool,
    erc_skipped: bool,
    kicad_version: Option<String>,
    diagnostics: Vec<Diagnostic>,
    summary: Summary,
}

#[derive(Debug, Serialize)]
struct Summary {
    errors: usize,
    warnings: usize,
    notes: usize,
    excluded: usize,
}

impl Summary {
    fn from_diagnostics(diagnostics: &[Diagnostic]) -> Self {
        Self {
            errors: diagnostics
                .iter()
                .filter(|diagnostic| diagnostic.is_active_error())
                .count(),
            warnings: diagnostics
                .iter()
                .filter(|diagnostic| {
                    diagnostic.severity == Severity::Warning && !diagnostic.excluded
                })
                .count(),
            notes: diagnostics
                .iter()
                .filter(|diagnostic| diagnostic.severity == Severity::Note && !diagnostic.excluded)
                .count(),
            excluded: diagnostics
                .iter()
                .filter(|diagnostic| diagnostic.excluded)
                .count(),
        }
    }
}

fn main() -> ExitCode {
    match execute(Cli::parse()) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("kicad-clippy: {error}");
            ExitCode::FAILURE
        }
    }
}

fn execute(cli: Cli) -> Result<ExitCode, CliError> {
    match cli.command {
        Commands::Lint {
            schematic,
            format,
            skip_erc,
            kicad_cli,
        } => lint(&schematic, format, skip_erc, kicad_cli),
    }
}

fn lint(
    schematic_path: &Path,
    format: OutputFormat,
    skip_erc: bool,
    explicit_cli: Option<PathBuf>,
) -> Result<ExitCode, CliError> {
    let schematic = SchematicDocument::from_path(schematic_path)?;
    let erc_report = if skip_erc {
        None
    } else {
        let cli_path = resolve_kicad_cli(explicit_cli)?;
        Some(run_native_erc(cli_path, schematic_path)?)
    };

    let (kicad_version, diagnostics) = match erc_report {
        Some(report) => (report.kicad_version, report.diagnostics),
        None => (None, Vec::new()),
    };
    let summary = Summary::from_diagnostics(&diagnostics);
    let output = LintOutput {
        schema_version: "1",
        tool_version: env!("CARGO_PKG_VERSION"),
        schematic: schematic.source().path().display().to_string(),
        syntax_valid: true,
        erc_skipped: skip_erc,
        kicad_version,
        diagnostics,
        summary,
    };

    match format {
        OutputFormat::Text => render_text(&output)?,
        OutputFormat::Json => {
            let json = serde_json::to_string_pretty(&output)?;
            writeln!(io::stdout().lock(), "{json}")?;
        }
    }

    if output.summary.errors > 0 {
        Ok(ExitCode::FAILURE)
    } else {
        Ok(ExitCode::SUCCESS)
    }
}

fn render_text(output: &LintOutput) -> Result<(), io::Error> {
    let mut stdout = io::stdout().lock();
    writeln!(stdout, "kicad-clippy {}", output.tool_version)?;
    writeln!(stdout, "S-expression syntax: valid ({})", output.schematic)?;

    if output.erc_skipped {
        writeln!(stdout, "Native KiCad ERC: skipped")?;
    } else if let Some(version) = &output.kicad_version {
        writeln!(stdout, "Native KiCad ERC: KiCad {version}")?;
    } else {
        writeln!(stdout, "Native KiCad ERC: completed")?;
    }

    if output.diagnostics.is_empty() && !output.erc_skipped {
        writeln!(stdout, "No native ERC violations reported.")?;
    }

    for diagnostic in &output.diagnostics {
        render_diagnostic(&mut stdout, diagnostic)?;
    }

    writeln!(
        stdout,
        "Summary: {} errors, {} warnings, {} notes, {} excluded",
        output.summary.errors,
        output.summary.warnings,
        output.summary.notes,
        output.summary.excluded
    )?;
    writeln!(
        stdout,
        "Custom SCH rules are not migrated yet; this build runs the native ERC engine only."
    )?;
    Ok(())
}

fn render_diagnostic(stdout: &mut impl Write, diagnostic: &Diagnostic) -> Result<(), io::Error> {
    write!(
        stdout,
        "{}[{}]: {}",
        diagnostic.severity.as_str(),
        diagnostic.code,
        diagnostic.message
    )?;

    if let Some(location) = diagnostic.schematic_location {
        write!(
            stdout,
            " at ({:.3} mm, {:.3} mm)",
            location.x_mm, location.y_mm
        )?;
    }
    if let Some(sheet_path) = &diagnostic.sheet_path {
        write!(stdout, " on sheet {sheet_path}")?;
    }
    if diagnostic.excluded {
        write!(stdout, " [excluded]")?;
    }
    writeln!(stdout)?;

    for related in &diagnostic.related {
        if related.uuid.is_some() || !related.description.is_empty() {
            write!(stdout, "  item: {}", related.description)?;
            if let Some(uuid) = &related.uuid {
                write!(stdout, " [{uuid}]")?;
            }
            writeln!(stdout)?;
        }
    }

    Ok(())
}

fn resolve_kicad_cli(explicit: Option<PathBuf>) -> Result<PathBuf, CliError> {
    if let Some(path) = explicit {
        return Ok(path);
    }

    if let Some(path) = std::env::var_os("KICAD_CLI") {
        return Ok(PathBuf::from(path));
    }

    for candidate in ["kicad-cli-nightly", "kicad-cli"] {
        if Command::new(candidate).arg("version").output().is_ok() {
            return Ok(PathBuf::from(candidate));
        }
    }

    Err(CliError::KiCadCliNotFound)
}

#[cfg(test)]
mod tests {
    use kicad_clippy_core::{Diagnostic, Severity};

    use super::Summary;

    #[test]
    fn summary_counts_only_active_errors() {
        let mut excluded = Diagnostic::new("ERC/EXCLUDED", Severity::Error, "ignored");
        excluded.excluded = true;
        let diagnostics = vec![
            Diagnostic::new("ERC/ERROR", Severity::Error, "blocking"),
            Diagnostic::new("ERC/WARNING", Severity::Warning, "review"),
            Diagnostic::new("ERC/NOTE", Severity::Note, "info"),
            excluded,
        ];
        let summary = Summary::from_diagnostics(&diagnostics);

        assert_eq!(summary.errors, 1);
        assert_eq!(summary.warnings, 1);
        assert_eq!(summary.notes, 1);
        assert_eq!(summary.excluded, 1);
    }
}
