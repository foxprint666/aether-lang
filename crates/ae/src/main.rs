//! ae — The Aether language CLI
//!
//! Subcommands:
//!   ae run   <file.ae>          — parse, analyze, and execute
//!   ae check <file.ae>          — parse + semantic analysis only
//!   ae check <file.ae> --json   — machine-readable JSON diagnostics (for AI agents)
//!   ae dump-ast <file.ae>       — print full AstStore as JSON
//!   ae lsp                      — start synchronous stdio LSP server

use std::{fs, process};

use clap::{Parser, Subcommand};

use ae_codegen::Interpreter;
use ae_sema::{DiagSeverity, analyze};
use ae_syntax::parse;

// ─────────────────────────────────────────────
//  CLI definition
// ─────────────────────────────────────────────

#[derive(Parser)]
#[command(
    name = "ae",
    about = "The Aether (.ae) language toolchain",
    version = env!("CARGO_PKG_VERSION"),
    disable_help_flag = false,
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Parse, analyze, and execute an Aether source file
    Run {
        /// Path to the .ae source file
        file: String,
    },
    /// Run the parser and semantic analyzer; report diagnostics
    Check {
        /// Path to the .ae source file
        file: String,
        /// Emit diagnostics as machine-readable JSON (for AI agents)
        #[arg(long)]
        json: bool,
    },
    /// Print the content-addressable AST store as JSON
    #[command(name = "dump-ast")]
    DumpAst {
        /// Path to the .ae source file
        file: String,
    },
    /// Start the Aether Language Server on stdio (for editors/agents)
    Lsp,
}

// ─────────────────────────────────────────────
//  Entry point
// ─────────────────────────────────────────────

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Command::Run { file }   => cmd_run(&file),
        Command::Check { file, json } => cmd_check(&file, json),
        Command::DumpAst { file }     => cmd_dump_ast(&file),
        Command::Lsp                  => ae_lsp::run_lsp(),
    }
}

// ─────────────────────────────────────────────
//  ae run <file>
// ─────────────────────────────────────────────

fn cmd_run(path: &str) {
    let src = read_file(path);

    // 1. Parse
    let result = parse(&src, path);
    print_lex_parse_errors(path, &result);
    if !result.ok() { process::exit(1); }

    // 2. Semantic analysis
    let sema = analyze(result.root, &result.store, &result.spans);
    let mut had_error = false;
    for d in &sema.diagnostics {
        match d.severity {
            DiagSeverity::Error => {
                eprintln!("error: {}", d.message);
                if let Some(s) = &d.suggestion { eprintln!("  hint: {}", s); }
                had_error = true;
            }
            DiagSeverity::Warning => eprintln!("warning: {}", d.message),
            DiagSeverity::Info    => {}
        }
    }
    if had_error { process::exit(1); }

    // 3. Execute
    let mut interp = Interpreter::new(&result.store, &result.spans, &sema);
    if let Err(e) = interp.run(result.root) {
        eprintln!("runtime error: {}", e);
        process::exit(1);
    }
}

// ─────────────────────────────────────────────
//  ae check <file> [--json]
// ─────────────────────────────────────────────

fn cmd_check(path: &str, json_mode: bool) {
    let src = read_file(path);

    if json_mode {
        // Machine-readable output via LSP server's check_json
        let server = ae_lsp::LspServer::new();
        let diag_json = server.check_json(&src, path);
        println!("{}", serde_json::to_string_pretty(&diag_json).unwrap());
        let status = diag_json["status"].as_str().unwrap_or("error");
        if status == "error" { process::exit(1); }
        return;
    }

    // Human-readable
    let result = parse(&src, path);
    print_lex_parse_errors(path, &result);

    let mut had_error = !result.lex_errors.is_empty() || !result.errors.is_empty();

    if result.ok() {
        let sema = analyze(result.root, &result.store, &result.spans);
        for d in &sema.diagnostics {
            match d.severity {
                DiagSeverity::Error => {
                    eprintln!("error: {} [{}]", d.message, d.hash_hex);
                    if let Some(s) = &d.suggestion { eprintln!("  hint: {}", s); }
                    had_error = true;
                }
                DiagSeverity::Warning => eprintln!("warning: {}", d.message),
                DiagSeverity::Info    => {}
            }
        }
        eprintln!("check: {} nodes in AstStore", result.store.len());
    }

    if had_error { process::exit(1); }
    eprintln!("check: OK ({})", path);
}

// ─────────────────────────────────────────────
//  ae dump-ast <file>
// ─────────────────────────────────────────────

fn cmd_dump_ast(path: &str) {
    let src = read_file(path);
    let result = parse(&src, path);

    print_lex_parse_errors(path, &result);
    if !result.ok() { process::exit(1); }

    // Serialize AST store to JSON
    // Each node: { hash_hex, kind_json, span? }
    let mut nodes: Vec<serde_json::Value> = Vec::new();

    for node in result.store.iter_nodes() {
        let hash_hex = ae_ast::hash_to_hex(&node.hash);
        let kind_json = serde_json::to_value(&node.kind).unwrap_or(serde_json::Value::Null);
        let span_json = result.spans.get(&node.hash).map(|s| serde_json::json!({
            "file": s.file,
            "start": s.start,
            "end": s.end,
            "line": s.line,
            "col": s.col,
        }));

        nodes.push(serde_json::json!({
            "hash": hash_hex,
            "kind": kind_json,
            "span": span_json,
        }));
    }

    // Sort by hash for deterministic output
    nodes.sort_by(|a, b| {
        a["hash"].as_str().unwrap_or("").cmp(b["hash"].as_str().unwrap_or(""))
    });

    let output = serde_json::json!({
        "file": path,
        "root": ae_ast::hash_to_hex(&result.root),
        "node_count": nodes.len(),
        "nodes": nodes,
    });

    println!("{}", serde_json::to_string_pretty(&output).unwrap());
}

// ─────────────────────────────────────────────
//  Helpers
// ─────────────────────────────────────────────

fn read_file(path: &str) -> String {
    fs::read_to_string(path).unwrap_or_else(|e| {
        eprintln!("error: cannot read `{}`: {}", path, e);
        process::exit(1);
    })
}

fn print_lex_parse_errors(path: &str, result: &ae_syntax::ParseResult) {
    for e in &result.lex_errors {
        eprintln!("lex error [{}]: {}", path, e);
    }
    for e in &result.errors {
        eprintln!("parse error [{}]: {}", path, e);
    }
}
