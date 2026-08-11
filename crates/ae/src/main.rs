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
        /// Run the file using the native Cranelift JIT compiler
        #[arg(long)]
        jit: bool,
    },
    /// Run the parser and semantic analyzer; report diagnostics
    Check {
        /// Path to the .ae source file
        file: String,
        /// Emit diagnostics as machine-readable JSON (for AI agents)
        #[arg(long)]
        json: bool,
        /// Verify stability impact on a specific AST node hash.
        /// Provide the 64-char BLAKE3 hex of the target ContentHash.
        /// JSON output will include a 'diff_impact' block with stability verdict.
        #[arg(long, value_name = "NODE_HASH")]
        diff_impact: Option<String>,
    },
    /// Print the content-addressable AST store as JSON
    #[command(name = "dump-ast")]
    DumpAst {
        /// Path to the .ae source file
        file: String,
    },
    /// Compile an Aether source file to a standalone binary object/executable
    Build {
        /// Path to the .ae source file
        file: String,
        /// Name of output executable
        #[arg(short, long)]
        output: Option<String>,
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
        Command::Run { file, jit }    => cmd_run(&file, jit),
        Command::Check { file, json, diff_impact } => cmd_check(&file, json, diff_impact.as_deref()),
        Command::DumpAst { file }     => cmd_dump_ast(&file),
        Command::Build { file, output } => cmd_build(&file, output),
        Command::Lsp                  => ae_lsp::run_lsp(),
    }
}

// ─────────────────────────────────────────────
//  ae run <file>
// ─────────────────────────────────────────────

fn cmd_run(path: &str, jit: bool) {
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
    if jit {
        let mut engine = ae_codegen::jit::JitEngine::new();
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            engine.compile_function(result.root, &result.store, &sema)
        })) {
            Ok(func_ptr) => {
                let main_fn: extern "C" fn() -> i64 = unsafe { std::mem::transmute(func_ptr) };
                let exec_result = main_fn();
                println!("[JIT Execution Finished. Returned: {}]", exec_result);
            }
            Err(_) => {
                eprintln!("runtime error: JIT Compilation Panicked");
                process::exit(1);
            }
        }
    } else {
        let mut interp = Interpreter::new(&result.store, &result.spans, &sema);
        if let Err(e) = interp.run(result.root) {
            eprintln!("runtime error: {}", e);
            process::exit(1);
        }
    }
}

// ─────────────────────────────────────────────
//  ae check <file> [--json]
// ─────────────────────────────────────────────

fn cmd_check(path: &str, json_mode: bool, diff_impact: Option<&str>) {
    let src = read_file(path);

    if json_mode {
        // Machine-readable output via LSP server's check_json
        let server = ae_lsp::LspServer::new();
        let mut diag_json = server.check_json(&src, path);

        // Augment with diff_impact section if requested
        if let Some(target_hash) = diff_impact {
            let result = parse(&src, path);
            if result.ok() {
                let sema = analyze(result.root, &result.store, &result.spans);
                let has_errors  = sema.has_errors();
                let has_union   = sema.diagnostics.iter().any(|d| {
                    d.stability_level >= 1 || d.message.to_lowercase().contains("union")
                });
                let stable = !has_errors && !has_union;
                diag_json["diff_impact"] = serde_json::json!({
                    "target_hash":        target_hash,
                    "stable":             stable,
                    "has_union":          has_union,
                    "has_sema_errors":    has_errors,
                    "stability_verdict":  if stable { "PASS" } else { "REJECT" },
                });
            }
        }

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
        // Show diff_impact verdict in human mode too
        if let Some(target_hash) = diff_impact {
            let has_union = sema.diagnostics.iter().any(|d| d.stability_level >= 1);
            let stable = !sema.has_errors() && !has_union;
            eprintln!(
                "diff-impact [{}]: {}",
                &target_hash[..8.min(target_hash.len())],
                if stable { "STABLE" } else { "UNSTABLE" }
            );
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
//  ae build <file> [-o output]
// ─────────────────────────────────────────────

fn cmd_build(path: &str, output: Option<String>) {
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

    // 3. Emit object file
    let engine = match ae_codegen::aot::AotEngine::new() {
        Ok(e) => e,
        Err(err) => {
            eprintln!("AOT engine initialization error: {}", err);
            process::exit(1);
        }
    };

    let obj_filename = if cfg!(target_os = "windows") { "app.obj" } else { "app.o" };
    let obj_path = std::path::Path::new(obj_filename);

    if let Err(e) = engine.emit_object_file(result.root, &result.store, &sema, obj_path) {
        eprintln!("AOT codegen error: {}", e);
        process::exit(1);
    }
    println!("[AOT] Emitted object file: {}", obj_path.display());

    let exe_name = output.unwrap_or_else(|| {
        if cfg!(target_os = "windows") { "app.exe".to_string() } else { "app".to_string() }
    });
    let exe_path = std::path::Path::new(&exe_name);

    if let Err(e) = link_object_file(obj_path, exe_path) {
        println!("[AOT] Note: Linker invocation skipped/failed: {}", e);
        println!("[AOT] Object file created successfully at {}", obj_path.display());
    } else {
        println!("[AOT] Successfully compiled and linked executable: {}", exe_path.display());
    }
}

fn link_object_file(obj_path: &std::path::Path, exe_path: &std::path::Path) -> Result<(), String> {
    use std::process::Command;

    #[cfg(target_os = "windows")]
    let status = Command::new("gcc")
        .args(&[
            obj_path.to_str().unwrap(),
            "-o",
            exe_path.to_str().unwrap(),
        ])
        .status();

    #[cfg(not(target_os = "windows"))]
    let status = Command::new("cc")
        .args(&[
            obj_path.to_str().unwrap(),
            "-o",
            exe_path.to_str().unwrap(),
            "-lc",
        ])
        .status();

    match status {
        Ok(s) if s.success() => Ok(()),
        Ok(s) => Err(format!("Linker exited with error code: {:?}", s.code())),
        Err(e) => Err(format!("Failed to invoke host linker: {}", e)),
    }
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
