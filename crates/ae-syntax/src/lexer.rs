//! Lexer for the Aether language.
//!
//! Uses the `logos` crate for zero-copy token scanning.
//! The lexer is deliberately simple: it never attempts to parse semantics,
//! and it emits `Token::Error` for unrecognized input rather than panicking.
//!
//! Integration with `miette`:
//!   Each token carries its `logos::Span` (byte range in the source).
//!   The parser converts these to `ae_ast::SourceSpan` for the `SpanTable`.

use logos::Logos;

// ─────────────────────────────────────────────
//  Token definition
// ─────────────────────────────────────────────

#[derive(Logos, Debug, Clone, PartialEq)]
#[logos(skip r"[ \t\r\n\f]+")] // skip whitespace
#[logos(skip r"//[^\n]*")]      // skip line comments
pub enum Token {
    // ── Keywords ──────────────────────────────
    #[token("fn")]       Fn,
    #[token("stable")]   Stable,
    #[token("let")]      Let,
    #[token("mut")]      Mut,
    #[token("if")]       If,
    #[token("else")]     Else,
    #[token("while")]    While,
    #[token("for")]      For,
    #[token("in")]       In,
    #[token("return")]   Return,
    #[token("break")]    Break,
    #[token("continue")] Continue,
    #[token("raw")]      Raw,
    #[token("true")]     True,
    #[token("false")]    False,
    #[token("range")]    KwRange,

    // ── Built-in type keywords ─────────────────
    #[token("i32")]  TyI32,
    #[token("i64")]  TyI64,
    #[token("f32")]  TyF32,
    #[token("f64")]  TyF64,
    #[token("bool")] TyBool,
    #[token("str")]  TyStr,
    #[token("auto")] TyAuto,

    // ── Literals ──────────────────────────────
    // Float must appear before Int so logos picks the longer match first.
    #[regex(r"[0-9]+\.[0-9]+", |lex| lex.slice().parse::<f64>().ok())]
    FloatLit(f64),

    #[regex(r"[0-9]+", |lex| lex.slice().parse::<i64>().ok())]
    IntLit(i64),

    #[regex(r#""([^"\\]|\\.)*""#, |lex| {
        let raw = lex.slice();
        // Strip surrounding quotes and handle basic escape sequences
        let inner = &raw[1..raw.len()-1];
        Some(unescape(inner))
    })]
    StrLit(String),

    // ── Identifier ────────────────────────────
    #[regex(r"[a-zA-Z_][a-zA-Z0-9_]*", |lex| lex.slice().to_string())]
    Ident(String),

    // ── Compound operators (must appear before single-char operators) ──
    #[token("==")] EqEq,
    #[token("!=")] BangEq,
    #[token("<=")] LtEq,
    #[token(">=")] GtEq,
    #[token("&&")] AmpAmp,
    #[token("||")] PipePipe,
    #[token("->")] Arrow,

    // ── Single-char operators ─────────────────
    #[token("+")] Plus,
    #[token("-")] Minus,
    #[token("*")] Star,
    #[token("/")] Slash,
    #[token("%")] Percent,
    #[token("<")] Lt,
    #[token(">")] Gt,
    #[token("!")] Bang,
    #[token("=")] Eq,

    // ── Delimiters ────────────────────────────
    #[token("{")] LBrace,
    #[token("}")] RBrace,
    #[token("(")] LParen,
    #[token(")")] RParen,
    #[token(",")] Comma,
    #[token(";")] Semi,
    #[token(":")] Colon,
    #[token(".")] Dot,
}

impl std::fmt::Display for Token {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Token::Fn        => write!(f, "fn"),
            Token::Stable    => write!(f, "stable"),
            Token::Let       => write!(f, "let"),
            Token::Mut       => write!(f, "mut"),
            Token::If        => write!(f, "if"),
            Token::Else      => write!(f, "else"),
            Token::While     => write!(f, "while"),
            Token::For       => write!(f, "for"),
            Token::In        => write!(f, "in"),
            Token::Return    => write!(f, "return"),
            Token::Break     => write!(f, "break"),
            Token::Continue  => write!(f, "continue"),
            Token::Raw       => write!(f, "raw"),
            Token::True      => write!(f, "true"),
            Token::False     => write!(f, "false"),
            Token::KwRange   => write!(f, "range"),
            Token::TyI32     => write!(f, "i32"),
            Token::TyI64     => write!(f, "i64"),
            Token::TyF32     => write!(f, "f32"),
            Token::TyF64     => write!(f, "f64"),
            Token::TyBool    => write!(f, "bool"),
            Token::TyStr     => write!(f, "str"),
            Token::TyAuto    => write!(f, "auto"),
            Token::FloatLit(v) => write!(f, "{}", v),
            Token::IntLit(v)   => write!(f, "{}", v),
            Token::StrLit(s)   => write!(f, "\"{}\"", s),
            Token::Ident(s)    => write!(f, "{}", s),
            Token::EqEq      => write!(f, "=="),
            Token::BangEq    => write!(f, "!="),
            Token::LtEq      => write!(f, "<="),
            Token::GtEq      => write!(f, ">="),
            Token::AmpAmp    => write!(f, "&&"),
            Token::PipePipe  => write!(f, "||"),
            Token::Arrow     => write!(f, "->"),
            Token::Plus      => write!(f, "+"),
            Token::Minus     => write!(f, "-"),
            Token::Star      => write!(f, "*"),
            Token::Slash     => write!(f, "/"),
            Token::Percent   => write!(f, "%"),
            Token::Lt        => write!(f, "<"),
            Token::Gt        => write!(f, ">"),
            Token::Bang      => write!(f, "!"),
            Token::Eq        => write!(f, "="),
            Token::LBrace    => write!(f, "{{"),
            Token::RBrace    => write!(f, "}}"),
            Token::LParen    => write!(f, "("),
            Token::RParen    => write!(f, ")"),
            Token::Comma     => write!(f, ","),
            Token::Semi      => write!(f, ";"),
            Token::Colon     => write!(f, ":"),
            Token::Dot       => write!(f, "."),
        }
    }
}

// ─────────────────────────────────────────────
//  Lexed token with span
// ─────────────────────────────────────────────

/// A token together with its byte range in the source file.
#[derive(Debug, Clone)]
pub struct SpannedToken {
    pub token: Token,
    pub span: logos::Span, // std::ops::Range<usize>
}

/// Tokenize a complete source string into a flat Vec of spanned tokens.
/// Lex errors produce a `LexError` and are included as `Err` in the output.
pub fn tokenize(src: &str) -> (Vec<SpannedToken>, Vec<LexError>) {
    let mut lexer = Token::lexer(src);
    let mut tokens = Vec::new();
    let mut errors = Vec::new();

    loop {
        match lexer.next() {
            None => break,
            Some(Ok(tok)) => tokens.push(SpannedToken {
                token: tok,
                span:  lexer.span(),
            }),
            Some(Err(_)) => {
                errors.push(LexError {
                    span:  lexer.span(),
                    slice: lexer.slice().to_string(),
                });
            }
        }
    }

    (tokens, errors)
}

// ─────────────────────────────────────────────
//  Lex error
// ─────────────────────────────────────────────

#[derive(Debug, Clone, thiserror::Error)]
#[error("unexpected character `{slice}` at byte {}", span.start)]
pub struct LexError {
    pub span:  logos::Span,
    pub slice: String,
}

// ─────────────────────────────────────────────
//  String escape helper
// ─────────────────────────────────────────────

fn unescape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\\' {
            match chars.next() {
                Some('n')  => out.push('\n'),
                Some('t')  => out.push('\t'),
                Some('r')  => out.push('\r'),
                Some('\\') => out.push('\\'),
                Some('"')  => out.push('"'),
                Some(c)    => { out.push('\\'); out.push(c); }
                None       => out.push('\\'),
            }
        } else {
            out.push(c);
        }
    }
    out
}
