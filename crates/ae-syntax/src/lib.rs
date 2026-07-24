//! ae-syntax — Lexer + Parser public API

pub mod lexer;
pub mod parser;

pub use lexer::{tokenize, LexError, SpannedToken, Token};
pub use parser::{ParseError, Parser};

use ae_ast::{AstStore, ContentHash, SpanTable};

/// High-level parse result.
pub struct ParseResult {
    pub root:   ContentHash,
    pub store:  AstStore,
    pub spans:  SpanTable,
    pub errors: Vec<ParseError>,
    pub lex_errors: Vec<LexError>,
}

impl ParseResult {
    pub fn ok(&self) -> bool {
        self.errors.is_empty() && self.lex_errors.is_empty()
    }
}

/// Parse a complete Aether source file.
///
/// Returns a `ParseResult` containing:
/// - `root`  — the `ContentHash` of the top-level `Program` node
/// - `store` — the content-addressable AST store
/// - `spans` — out-of-band source locations (never factored into hashes)
/// - `errors` / `lex_errors` — any parse or lex errors
pub fn parse(src: &str, file: impl Into<String>) -> ParseResult {
    let (tokens, lex_errors) = tokenize(src);
    let file = file.into();
    let mut p = Parser::new(tokens, src, file);
    let root = p.parse_program();

    ParseResult {
        root,
        store:  p.store,
        spans:  p.spans,
        errors: p.errors,
        lex_errors,
    }
}
