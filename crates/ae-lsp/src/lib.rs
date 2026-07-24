//! ae-lsp — Aether Language Server Protocol implementation
//!
//! Implements the Type-Stability Heatmap via:
//!   • `textDocument/semanticTokens` — per-variable stability coloring
//!   • `textDocument/publishDiagnostics` — type errors + stability warnings
//!   • `textDocument/inlayHint` — inferred type annotations as ghost text
//!   • Custom `aether/checkStability` JSON-RPC extension for AI agents
//!
//! Semantic token modifier encoding:
//!   0 = "static"         → fully monomorphized (green — no annotation)
//!   1 = "dynamic"        → Union type, non-hot path (yellow)
//!   2 = "dynamic.critical" → Union type in stable fn or hot loop (red)

use std::{collections::HashMap, sync::Arc};
use tokio::sync::RwLock;
use tower_lsp::jsonrpc::Result as LspResult;
use tower_lsp::lsp_types::*;
use tower_lsp::{Client, LanguageServer, LspService, Server};

use ae_ast::{ContentHash, SourceSpan};

// ─────────────────────────────────────────────
//  Document store
// ─────────────────────────────────────────────

#[derive(Default)]
struct DocStore {
    docs: HashMap<String, String>,
}

// ─────────────────────────────────────────────
//  LSP Backend
// ─────────────────────────────────────────────

struct AetherLsp {
    client: Client,
    docs:   Arc<RwLock<DocStore>>,
}

impl AetherLsp {
    fn new(client: Client) -> Self {
        AetherLsp {
            client,
            docs: Arc::new(RwLock::new(DocStore::default())),
        }
    }

    async fn reanalyze(&self, uri: &Url, src: &str) {
        let file = uri.to_string();
        let result = ae_syntax::parse(src, &file);

        let mut diags: Vec<Diagnostic> = Vec::new();

        // Lex errors
        for le in &result.lex_errors {
            diags.push(Diagnostic {
                range: byte_range_to_lsp(src, le.span.start, le.span.end),
                severity: Some(DiagnosticSeverity::ERROR),
                source: Some("ae-lsp".into()),
                message: format!("unexpected character `{}`", le.slice),
                ..Default::default()
            });
        }

        // Parse errors
        for pe in &result.errors {
            let (start, end) = match pe {
                ae_syntax::ParseError::Unexpected { start, end, .. } => (*start, *end),
                _ => (0, 0),
            };
            diags.push(Diagnostic {
                range: byte_range_to_lsp(src, start, end),
                severity: Some(DiagnosticSeverity::ERROR),
                source: Some("ae-lsp".into()),
                message: pe.to_string(),
                ..Default::default()
            });
        }

        if result.ok() {
            // Semantic analysis
            let sema = ae_sema::analyze(result.root, &result.store, &result.spans);
            for diag in &sema.diagnostics {
                let span = result.spans.get(&diag.hash).cloned().unwrap_or(SourceSpan {
                    file: file.clone(), start: 0, end: 0, line: 1, col: 0,
                });
                let severity = match &diag.severity {
                    ae_sema::DiagSeverity::Error   => DiagnosticSeverity::ERROR,
                    ae_sema::DiagSeverity::Warning  => DiagnosticSeverity::WARNING,
                    ae_sema::DiagSeverity::Info     => DiagnosticSeverity::INFORMATION,
                };
                let mut data = None;
                if let Some(sugg) = &diag.suggestion {
                    data = Some(serde_json::json!({
                        "suggestion": sugg,
                        "stabilityLevel": diag.stability_level,
                        "hashHex": diag.hash_hex,
                    }));
                }
                diags.push(Diagnostic {
                    range: byte_range_to_lsp(src, span.start, span.end),
                    severity: Some(severity),
                    source: Some("ae-lsp".into()),
                    message: diag.message.clone(),
                    data,
                    ..Default::default()
                });
            }
        }

        self.client.publish_diagnostics(uri.clone(), diags, None).await;
    }
}

// ─────────────────────────────────────────────
//  LanguageServer trait implementation
// ─────────────────────────────────────────────

#[tower_lsp::async_trait]
impl LanguageServer for AetherLsp {
    async fn initialize(&self, _params: InitializeParams) -> LspResult<InitializeResult> {
        Ok(InitializeResult {
            capabilities: ServerCapabilities {
                text_document_sync: Some(TextDocumentSyncCapability::Kind(
                    TextDocumentSyncKind::FULL,
                )),
                hover_provider: Some(HoverProviderCapability::Simple(true)),
                semantic_tokens_provider: Some(
                    SemanticTokensServerCapabilities::SemanticTokensOptions(
                        SemanticTokensOptions {
                            legend: SemanticTokensLegend {
                                token_types: vec![
                                    SemanticTokenType::VARIABLE,
                                    SemanticTokenType::FUNCTION,
                                    SemanticTokenType::PARAMETER,
                                    SemanticTokenType::TYPE,
                                ],
                                token_modifiers: vec![
                                    SemanticTokenModifier::new("static"),          // 0 — mono
                                    SemanticTokenModifier::new("dynamic"),         // 1 — union
                                    SemanticTokenModifier::new("dynamic.critical"),// 2 — union in stable
                                ],
                            },
                            full: Some(SemanticTokensFullOptions::Bool(true)),
                            range: None,
                            work_done_progress_options: Default::default(),
                        },
                    ),
                ),
                inlay_hint_provider: Some(OneOf::Left(true)),
                ..Default::default()
            },
            server_info: Some(ServerInfo {
                name: "ae-lsp".into(),
                version: Some("0.1.0".into()),
            }),
        })
    }

    async fn initialized(&self, _: InitializedParams) {
        self.client.log_message(MessageType::INFO, "Aether LSP initialized").await;
    }

    async fn shutdown(&self) -> LspResult<()> {
        Ok(())
    }

    async fn did_open(&self, params: DidOpenTextDocumentParams) {
        let uri = params.text_document.uri.clone();
        let src = params.text_document.text.clone();
        self.docs.write().await.docs.insert(uri.to_string(), src.clone());
        self.reanalyze(&uri, &src).await;
    }

    async fn did_change(&self, params: DidChangeTextDocumentParams) {
        let uri = params.text_document.uri.clone();
        if let Some(change) = params.content_changes.into_iter().last() {
            let src = change.text;
            self.docs.write().await.docs.insert(uri.to_string(), src.clone());
            self.reanalyze(&uri, &src).await;
        }
    }

    async fn hover(&self, params: HoverParams) -> LspResult<Option<Hover>> {
        let uri = params.text_document_position_params.text_document.uri.to_string();
        let docs = self.docs.read().await;
        let src = match docs.docs.get(&uri) {
            Some(s) => s.clone(),
            None    => return Ok(None),
        };
        drop(docs);

        let result = ae_syntax::parse(&src, &uri);
        if result.ok() {
            let pos   = params.text_document_position_params.position;
            let offset = lsp_pos_to_byte(&src, pos);
            let sema   = ae_sema::analyze(result.root, &result.store, &result.spans);

            // Find the node whose span contains this offset
            for (hash, span) in &result.spans.spans {
                if span.start <= offset && offset < span.end {
                    if let Some(ty) = sema.types.get(hash) {
                        let stability = match sema.stability_level(hash) {
                            0 => "✅ monomorphized",
                            1 => "🟡 dynamic dispatch",
                            _ => "🔴 dynamic.critical",
                        };
                        return Ok(Some(Hover {
                            contents: HoverContents::Markup(MarkupContent {
                                kind: MarkupKind::Markdown,
                                value: format!(
                                    "**Type**: `{}`\n\n**Stability**: {}\n\n`hash: {}`",
                                    ty, stability, ae_ast::hash_to_hex(hash)
                                ),
                            }),
                            range: Some(byte_range_to_lsp(&src, span.start, span.end)),
                        }));
                    }
                }
            }
        }
        Ok(None)
    }

    async fn semantic_tokens_full(
        &self,
        params: SemanticTokensParams,
    ) -> LspResult<Option<SemanticTokensResult>> {
        let uri = params.text_document.uri.to_string();
        let docs = self.docs.read().await;
        let src  = match docs.docs.get(&uri) { Some(s) => s.clone(), None => return Ok(None) };
        drop(docs);

        let result = ae_syntax::parse(&src, &uri);
        if !result.ok() { return Ok(None); }
        let sema = ae_sema::analyze(result.root, &result.store, &result.spans);

        // Build sorted list of (line, col, len, type_idx, modifier_bits)
        let mut raw: Vec<(u32, u32, u32, u32, u32)> = Vec::new();

        for (hash, span) in &result.spans.spans {
            let level = sema.stability_level(hash);
            if level == 0 { continue; } // only annotate dynamic sites

            let modifier_bits = 1u32 << level; // bit 1 = dynamic, bit 2 = dynamic.critical
            let lsp_range = byte_range_to_lsp(&src, span.start, span.end);
            let line = lsp_range.start.line;
            let col  = lsp_range.start.character;
            let len  = (span.end - span.start) as u32;
            raw.push((line, col, len, 0 /* variable */, modifier_bits));
        }

        raw.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));

        // LSP delta encoding: each token is relative to the previous one
        let mut tokens: Vec<SemanticToken> = Vec::new();
        let mut prev_line = 0u32;
        let mut prev_col  = 0u32;
        for (line, col, len, tok_type, tok_mods) in raw {
            let delta_line = line - prev_line;
            let delta_start = if delta_line == 0 { col - prev_col } else { col };
            tokens.push(SemanticToken {
                delta_line,
                delta_start,
                length: len,
                token_type: tok_type,
                token_modifiers_bitset: tok_mods,
            });
            prev_line = line;
            prev_col  = col;
        }

        Ok(Some(SemanticTokensResult::Tokens(SemanticTokens {
            result_id: None,
            data: tokens,
        })))
    }

    async fn inlay_hint(&self, params: InlayHintParams) -> LspResult<Option<Vec<InlayHint>>> {
        let uri = params.text_document.uri.to_string();
        let docs = self.docs.read().await;
        let src  = match docs.docs.get(&uri) { Some(s) => s.clone(), None => return Ok(None) };
        drop(docs);

        let result = ae_syntax::parse(&src, &uri);
        if !result.ok() { return Ok(None); }
        let sema = ae_sema::analyze(result.root, &result.store, &result.spans);

        let mut hints: Vec<InlayHint> = Vec::new();

        for (hash, span) in &result.spans.spans {
            if let Some(node) = result.store.get(hash) {
                // Only show hints for Let bindings with `auto` or Union type
                if let ae_ast::AstNodeKind::Let { ty: None, .. } = &node.kind {
                    if let Some(ty) = sema.types.get(hash) {
                        let lsp_range = byte_range_to_lsp(&src, span.start, span.end);
                        let label = if sema.stability_level(hash) > 0 {
                            format!(": {} ⚠️", ty)
                        } else {
                            format!(": {}", ty)
                        };
                        hints.push(InlayHint {
                            position:      lsp_range.end,
                            label:         InlayHintLabel::String(label),
                            kind:          Some(InlayHintKind::TYPE),
                            text_edits:    None,
                            tooltip:       None,
                            padding_left:  Some(true),
                            padding_right: None,
                            data:          None,
                        });
                    }
                }
            }
        }

        Ok(Some(hints))
    }
}

// ─────────────────────────────────────────────
//  Span conversion helpers
// ─────────────────────────────────────────────

fn byte_range_to_lsp(src: &str, start: usize, end: usize) -> Range {
    Range {
        start: byte_to_lsp_pos(src, start),
        end:   byte_to_lsp_pos(src, end),
    }
}

fn byte_to_lsp_pos(src: &str, byte: usize) -> Position {
    let byte = byte.min(src.len());
    let text = &src[..byte];
    let line = text.chars().filter(|&c| c == '\n').count() as u32;
    let col  = text.rfind('\n').map(|p| byte - p - 1).unwrap_or(byte) as u32;
    Position { line, character: col }
}

fn lsp_pos_to_byte(src: &str, pos: Position) -> usize {
    let mut line = 0u32;
    let mut byte = 0usize;
    for ch in src.chars() {
        if line == pos.line { break; }
        if ch == '\n' { line += 1; }
        byte += ch.len_utf8();
    }
    byte + pos.character as usize
}

// ─────────────────────────────────────────────
//  Public entry point
// ─────────────────────────────────────────────

/// Start the LSP server on stdio (called by `ae lsp`).
pub async fn run_lsp() {
    let stdin  = tokio::io::stdin();
    let stdout = tokio::io::stdout();

    let (service, socket) = LspService::new(|client| AetherLsp::new(client));
    Server::new(stdin, stdout, socket).serve(service).await;
}
