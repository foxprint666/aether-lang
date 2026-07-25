//! ae-lsp — Aether Language Server (Synchronous stdio implementation)
//!
//! This is a **zero-async-dependency** LSP server. It uses only:
//!   - `lsp-types` (pure Rust LSP type definitions)
//!   - `serde_json` (pure Rust JSON)
//!   - `std::io::stdin/stdout` (standard library)
//!
//! No `tokio`, no `tower-lsp`, no `parking_lot`, no C-FFI.
//! Compiles cleanly under `stable-x86_64-pc-windows-gnu`.
//!
//! ## Protocol
//! Reads LSP messages from stdin using the Content-Length framing defined in
//! https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#baseProtocol
//!
//! ## Features
//!   - `initialize` / `initialized` / `shutdown` / `exit`
//!   - `textDocument/didOpen` + `textDocument/didChange` → `publishDiagnostics`
//!   - `textDocument/hover` → inferred type + stability emoji
//!   - `aether/checkStability` custom JSON-RPC extension for AI agents

use std::collections::HashMap;
use std::io::{self, BufRead, Write};

use lsp_types::notification::Notification;
use lsp_types::request::Request;
use lsp_types::*;
use serde_json::{json, Value as JsonValue};

use ae_ast::SourceSpan;

// ─────────────────────────────────────────────
//  Document store
// ─────────────────────────────────────────────

struct DocStore {
    docs: HashMap<String, String>,
}

impl DocStore {
    fn new() -> Self {
        DocStore { docs: HashMap::new() }
    }
}

// ─────────────────────────────────────────────
//  LSP Server
// ─────────────────────────────────────────────

pub struct LspServer {
    docs:      DocStore,
    shutdown:  bool,
}

impl LspServer {
    pub fn new() -> Self {
        LspServer {
            docs:     DocStore::new(),
            shutdown: false,
        }
    }

    // ── Main stdio loop ──────────────────────

    pub fn run(&mut self) {
        let stdin  = io::stdin();
        let stdout = io::stdout();

        loop {
            // Read Content-Length header
            let mut header = String::new();
            let mut content_length = 0usize;

            // Read headers until blank line
            for line in stdin.lock().lines() {
                let line = match line {
                    Ok(l) => l,
                    Err(_) => return,
                };
                if line.is_empty() { break; }
                if line.starts_with("Content-Length: ") {
                    content_length = line["Content-Length: ".len()..].trim().parse().unwrap_or(0);
                }
                let _ = header; // suppress unused warning
            }

            if content_length == 0 { continue; }

            // Read body
            let mut body = vec![0u8; content_length];
            {
                use std::io::Read;
                if stdin.lock().read_exact(&mut body).is_err() { return; }
            }

            let msg: JsonValue = match serde_json::from_slice(&body) {
                Ok(v) => v,
                Err(_) => continue,
            };

            // Dispatch
            let response = self.dispatch(&msg);

            // Send response if present
            if let Some(resp) = response {
                let resp_str = serde_json::to_string(&resp).unwrap();
                let mut out = stdout.lock();
                write!(out, "Content-Length: {}\r\n\r\n{}", resp_str.len(), resp_str).ok();
                out.flush().ok();
            }

            if self.shutdown { break; }
        }
    }

    // ── Message dispatch ─────────────────────

    fn dispatch(&mut self, msg: &JsonValue) -> Option<JsonValue> {
        let id     = msg.get("id").cloned();
        let method = msg.get("method")?.as_str()?;

        match method {
            // ── Lifecycle ─────────────────────────────────────────────────
            "initialize" => {
                let result = json!({
                    "capabilities": {
                        "textDocumentSync": 1,  // Full sync
                        "hoverProvider": true,
                        "semanticTokensProvider": {
                            "legend": {
                                "tokenTypes": ["variable", "function", "parameter", "type"],
                                "tokenModifiers": ["static", "dynamic", "dynamic.critical"]
                            },
                            "full": true
                        },
                        "inlayHintProvider": true
                    },
                    "serverInfo": {
                        "name": "ae-lsp",
                        "version": "0.1.0"
                    }
                });
                id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": result }))
            }
            "initialized" => None,
            "shutdown" => {
                self.shutdown = true;
                id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": null }))
            }
            "exit" => {
                self.shutdown = true;
                None
            }

            // ── Document sync ─────────────────────────────────────────────
            "textDocument/didOpen" => {
                if let Some(params) = msg.get("params") {
                    if let (Some(uri), Some(text)) = (
                        params["textDocument"]["uri"].as_str(),
                        params["textDocument"]["text"].as_str(),
                    ) {
                        let text = text.to_string();
                        self.docs.docs.insert(uri.to_string(), text.clone());
                        let diags = self.analyze_and_build_diags(uri, &text);
                        self.publish_diagnostics(uri, diags);
                    }
                }
                None
            }
            "textDocument/didChange" => {
                if let Some(params) = msg.get("params") {
                    if let Some(uri) = params["textDocument"]["uri"].as_str() {
                        if let Some(changes) = params["contentChanges"].as_array() {
                            if let Some(last) = changes.last() {
                                if let Some(text) = last["text"].as_str() {
                                    let text = text.to_string();
                                    self.docs.docs.insert(uri.to_string(), text.clone());
                                    let diags = self.analyze_and_build_diags(uri, &text);
                                    self.publish_diagnostics(uri, diags);
                                }
                            }
                        }
                    }
                }
                None
            }
            "textDocument/didClose" => {
                if let Some(uri) = msg["params"]["textDocument"]["uri"].as_str() {
                    self.docs.docs.remove(uri);
                }
                None
            }

            // ── Hover ─────────────────────────────────────────────────────
            "textDocument/hover" => {
                let uri = msg["params"]["textDocument"]["uri"].as_str()?;
                let src = self.docs.docs.get(uri)?.clone();

                let line = msg["params"]["position"]["line"].as_u64()? as u32;
                let col  = msg["params"]["position"]["character"].as_u64()? as u32;
                let offset = lsp_pos_to_byte(&src, line, col);

                let parse_result = ae_syntax::parse(&src, uri);
                if parse_result.ok() {
                    let sema = ae_sema::analyze(parse_result.root, &parse_result.store, &parse_result.spans);
                    for (hash, span) in &parse_result.spans.spans {
                        if span.start <= offset && offset < span.end {
                            if let Some(ty) = sema.types.get(hash) {
                                let stability = match sema.stability_level(hash) {
                                    0 => "✅ monomorphized",
                                    1 => "🟡 dynamic dispatch",
                                    _ => "🔴 dynamic.critical",
                                };
                                let value = format!(
                                    "**Type**: `{}`\n\n**Stability**: {}\n\n`hash: {}`",
                                    ty, stability, ae_ast::hash_to_hex(hash)
                                );
                                let result = json!({
                                    "contents": {
                                        "kind": "markdown",
                                        "value": value
                                    }
                                });
                                return id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": result }));
                            }
                        }
                    }
                }
                id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": null }))
            }

            // ── Semantic tokens ───────────────────────────────────────────
            "textDocument/semanticTokens/full" => {
                let uri = msg["params"]["textDocument"]["uri"].as_str()?;
                let src = self.docs.docs.get(uri)?.clone();
                let parse_result = ae_syntax::parse(&src, uri);
                if !parse_result.ok() {
                    return id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": null }));
                }
                let sema = ae_sema::analyze(parse_result.root, &parse_result.store, &parse_result.spans);

                let mut raw: Vec<(u32, u32, u32, u32, u32)> = Vec::new();
                for (hash, span) in &parse_result.spans.spans {
                    let level = sema.stability_level(hash);
                    if level == 0 { continue; }
                    let (start_line, start_col) = byte_to_lsp_pos(&src, span.start);
                    let len = (span.end - span.start) as u32;
                    raw.push((start_line, start_col, len, 0, 1u32 << level));
                }
                raw.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));

                let mut data: Vec<u32> = Vec::new();
                let mut prev_line = 0u32;
                let mut prev_col  = 0u32;
                for (line, col, len, tok_type, tok_mods) in raw {
                    let delta_line  = line - prev_line;
                    let delta_start = if delta_line == 0 { col - prev_col } else { col };
                    data.extend_from_slice(&[delta_line, delta_start, len, tok_type, tok_mods]);
                    prev_line = line;
                    prev_col  = col;
                }
                let result = json!({ "data": data });
                id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": result }))
            }

            // ── Custom: aether/checkStability ─────────────────────────────
            // AI-agent extension: returns full machine-readable diagnostic JSON.
            "aether/checkStability" => {
                let uri = msg["params"]["uri"].as_str()?;
                let src = self.docs.docs.get(uri)?.clone();
                let result = self.check_json(&src, uri);
                id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": result }))
            }

            _ => {
                // Unknown request — return null result or no response for notifications
                if id.is_some() {
                    id.map(|id| json!({ "jsonrpc": "2.0", "id": id, "result": null }))
                } else {
                    None
                }
            }
        }
    }

    // ── Helpers ─────────────────────────────

    fn analyze_and_build_diags(&self, uri: &str, src: &str) -> Vec<JsonValue> {
        let mut diags = Vec::new();
        let parse_result = ae_syntax::parse(src, uri);

        for le in &parse_result.lex_errors {
            let (sl, sc) = byte_to_lsp_pos(src, le.span.start);
            let (el, ec) = byte_to_lsp_pos(src, le.span.end);
            diags.push(json!({
                "range": lsp_range(sl, sc, el, ec),
                "severity": 1,
                "source": "ae-lsp",
                "message": format!("unexpected character `{}`", le.slice)
            }));
        }
        for pe in &parse_result.errors {
            let (start, end) = match pe {
                ae_syntax::ParseError::Unexpected { start, end, .. } => (*start, *end),
                _ => (0, 0),
            };
            let (sl, sc) = byte_to_lsp_pos(src, start);
            let (el, ec) = byte_to_lsp_pos(src, end);
            diags.push(json!({
                "range": lsp_range(sl, sc, el, ec),
                "severity": 1,
                "source": "ae-lsp",
                "message": pe.to_string()
            }));
        }

        if parse_result.ok() {
            let sema = ae_sema::analyze(parse_result.root, &parse_result.store, &parse_result.spans);
            for diag in &sema.diagnostics {
                let span = parse_result.spans.get(&diag.hash).cloned()
                    .unwrap_or(SourceSpan { file: uri.to_string(), start: 0, end: 0, line: 1, col: 0 });
                let (sl, sc) = byte_to_lsp_pos(src, span.start);
                let (el, ec) = byte_to_lsp_pos(src, span.end);
                let severity = match diag.severity {
                    ae_sema::DiagSeverity::Error   => 1,
                    ae_sema::DiagSeverity::Warning  => 2,
                    ae_sema::DiagSeverity::Info     => 3,
                };
                let mut d = json!({
                    "range": lsp_range(sl, sc, el, ec),
                    "severity": severity,
                    "source": "ae-lsp",
                    "message": diag.message
                });
                if let Some(sugg) = &diag.suggestion {
                    d["data"] = json!({
                        "suggestion": sugg,
                        "stabilityLevel": diag.stability_level,
                        "hashHex": diag.hash_hex
                    });
                }
                diags.push(d);
            }
        }
        diags
    }

    fn publish_diagnostics(&self, uri: &str, diags: Vec<JsonValue>) {
        let notif = json!({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "diagnostics": diags
            }
        });
        let s = serde_json::to_string(&notif).unwrap();
        let stdout = io::stdout();
        let mut out = stdout.lock();
        write!(out, "Content-Length: {}\r\n\r\n{}", s.len(), s).ok();
        out.flush().ok();
    }

    /// Returns structured JSON diagnostics — used by `aether/checkStability`
    /// and by `ae check --json` CLI mode.
    pub fn check_json(&self, src: &str, uri: &str) -> JsonValue {
        let parse_result = ae_syntax::parse(src, uri);
        let mut diagnostics = Vec::<JsonValue>::new();

        for le in &parse_result.lex_errors {
            let (sl, sc) = byte_to_lsp_pos(src, le.span.start);
            diagnostics.push(json!({
                "severity": "error",
                "code": "L0001",
                "message": format!("unexpected character `{}`", le.slice),
                "span": { "start": le.span.start, "end": le.span.end, "line": sl, "column": sc }
            }));
        }

        for pe in &parse_result.errors {
            let (start, end) = match pe {
                ae_syntax::ParseError::Unexpected { start, end, .. } => (*start, *end),
                _ => (0, 0),
            };
            let (sl, sc) = byte_to_lsp_pos(src, start);
            diagnostics.push(json!({
                "severity": "error",
                "code": "P0001",
                "message": pe.to_string(),
                "span": { "start": start, "end": end, "line": sl, "column": sc }
            }));
        }

        let status = if diagnostics.is_empty() && parse_result.ok() {
            // Semantic pass
            let sema = ae_sema::analyze(parse_result.root, &parse_result.store, &parse_result.spans);
            for diag in &sema.diagnostics {
                let span = parse_result.spans.get(&diag.hash).cloned()
                    .unwrap_or(SourceSpan { file: uri.to_string(), start: 0, end: 0, line: 1, col: 0 });
                let severity = match diag.severity {
                    ae_sema::DiagSeverity::Error   => "error",
                    ae_sema::DiagSeverity::Warning  => "warning",
                    ae_sema::DiagSeverity::Info     => "info",
                };
                diagnostics.push(json!({
                    "severity": severity,
                    "code": format!("E{:04}", diag.stability_level + 100),
                    "message": diag.message,
                    "span": {
                        "start": span.start,
                        "end": span.end,
                        "line": span.line,
                        "column": span.col
                    },
                    "suggestion": diag.suggestion,
                    "hashHex": diag.hash_hex,
                    "stabilityLevel": diag.stability_level
                }));
            }
            if sema.has_errors() { "error" } else { "ok" }
        } else if diagnostics.is_empty() {
            "ok"
        } else {
            "error"
        };

        json!({
            "status": status,
            "diagnostics": diagnostics
        })
    }
}

// ─────────────────────────────────────────────
//  Span utilities
// ─────────────────────────────────────────────

fn byte_to_lsp_pos(src: &str, byte: usize) -> (u32, u32) {
    let byte = byte.min(src.len());
    let text = &src[..byte];
    let line = text.chars().filter(|&c| c == '\n').count() as u32;
    let col  = text.rfind('\n').map(|p| byte - p - 1).unwrap_or(byte) as u32;
    (line, col)
}

fn lsp_pos_to_byte(src: &str, line: u32, col: u32) -> usize {
    let mut cur_line = 0u32;
    let mut byte = 0usize;
    for ch in src.chars() {
        if cur_line == line { break; }
        if ch == '\n' { cur_line += 1; }
        byte += ch.len_utf8();
    }
    byte + col as usize
}

fn lsp_range(sl: u32, sc: u32, el: u32, ec: u32) -> JsonValue {
    json!({
        "start": { "line": sl, "character": sc },
        "end":   { "line": el, "character": ec }
    })
}

// ─────────────────────────────────────────────
//  Public entry point
// ─────────────────────────────────────────────

/// Start the synchronous stdio LSP server.
pub fn run_lsp() {
    let mut server = LspServer::new();
    server.run();
}
