//! Recursive-descent parser for the Aether language.
//!
//! Converts a flat `Vec<SpannedToken>` into an `AstStore` + `SpanTable`.
//!
//! Span/Hash discipline (per Aether spec §1):
//!   - Spans are stored in `SpanTable` AFTER node creation; they never
//!     enter the BLAKE3 hash computation.
//!   - `make_node()` from ae-ast handles the two-step insert atomically.
//!
//! Cranelift-friendly SSA note: The parser emits `AstNodeKind::Let` and
//! `AstNodeKind::Assign` as distinct nodes. The codegen layer can use
//! `builder.declare_var()` / `builder.def_var()` / `builder.use_var()`
//! without any manual phi-node analysis.

use ae_ast::{
    make_node, AetherTypeSer, AstNodeKind, AstStore, BinOpKind, ContentHash,
    SourceSpan, SpanTable, UnaryOpKind,
};

use crate::lexer::{LexError, SpannedToken, Token};

// ─────────────────────────────────────────────
//  Parse error
// ─────────────────────────────────────────────

#[derive(Debug, Clone, thiserror::Error)]
pub enum ParseError {
    #[error("unexpected token `{found}` at {start}..{end}; expected {expected}")]
    Unexpected {
        found:    String,
        expected: String,
        start:    usize,
        end:      usize,
    },
    #[error("unexpected end of file; expected {expected}")]
    UnexpectedEof { expected: String },
    #[error("{0}")]
    Lex(#[from] LexError),
}

impl ParseError {
    pub fn to_source_span(&self) -> miette::SourceSpan {
        match self {
            Self::Unexpected { start, end, .. } => (*start, end - start).into(),
            _ => (0usize, 0usize).into(),
        }
    }
}

// ─────────────────────────────────────────────
//  Parser state
// ─────────────────────────────────────────────

pub struct Parser<'src> {
    tokens:   Vec<SpannedToken>,
    pos:      usize,
    src_file: String,
    src:      &'src str,
    pub store:   AstStore,
    pub spans:   SpanTable,
    pub errors:  Vec<ParseError>,
}

impl<'src> Parser<'src> {
    pub fn new(tokens: Vec<SpannedToken>, src: &'src str, file: impl Into<String>) -> Self {
        Parser {
            tokens,
            pos: 0,
            src_file: file.into(),
            src,
            store: AstStore::new(),
            spans: SpanTable::new(),
            errors: Vec::new(),
        }
    }

    // ── Cursor helpers ───────────────────────

    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.pos).map(|st| &st.token)
    }

    fn peek_spanned(&self) -> Option<&SpannedToken> {
        self.tokens.get(self.pos)
    }

    fn advance(&mut self) -> Option<&SpannedToken> {
        let st = self.tokens.get(self.pos);
        if st.is_some() {
            self.pos += 1;
        }
        st
    }

    fn current_span(&self) -> logos::Span {
        self.tokens.get(self.pos)
            .map(|st| st.span.clone())
            .or_else(|| self.tokens.last().map(|st| st.span.clone()))
            .unwrap_or(0..0)
    }

    fn span_to_source(&self, s: &logos::Span) -> SourceSpan {
        // Compute line/col from byte offset
        let text = &self.src[..s.start.min(self.src.len())];
        let line = text.chars().filter(|&c| c == '\n').count() as u32 + 1;
        let col  = text.rfind('\n').map(|p| s.start - p - 1).unwrap_or(s.start) as u32;
        SourceSpan {
            file:  self.src_file.clone(),
            start: s.start,
            end:   s.end,
            line,
            col,
        }
    }

    fn expect(&mut self, tok: &Token, _ctx: &str) -> Result<logos::Span, ParseError> {
        match self.peek_spanned() {
            Some(st) if std::mem::discriminant(&st.token) == std::mem::discriminant(tok) => {
                let span = st.span.clone();
                self.advance();
                Ok(span)
            }
            Some(st) => Err(ParseError::Unexpected {
                found:    format!("{}", st.token),
                expected: format!("{}", tok),
                start:    st.span.start,
                end:      st.span.end,
            }),
            None => Err(ParseError::UnexpectedEof { expected: format!("{}", tok) }),
        }
    }

    fn eat(&mut self, tok: &Token) -> bool {
        if self.peek().map(|t| std::mem::discriminant(t) == std::mem::discriminant(tok)).unwrap_or(false) {
            self.advance();
            true
        } else {
            false
        }
    }

    fn mk(&mut self, kind: AstNodeKind, span: logos::Span) -> ContentHash {
        let src_span = self.span_to_source(&span);
        make_node(&mut self.store, &mut self.spans, kind, src_span)
    }

    // ── Error recovery ───────────────────────

    fn sync_to_next_statement(&mut self) {
        let start_pos = self.pos;
        loop {
            match self.peek() {
                None | Some(Token::RBrace) => break,
                Some(Token::Semi) => { self.advance(); break; }
                Some(Token::Fn) | Some(Token::Stable) | Some(Token::Let)
                | Some(Token::If) | Some(Token::While) | Some(Token::For)
                | Some(Token::Return) => break,
                _ => { self.advance(); }
            }
        }
        if self.pos == start_pos && self.peek().is_some() {
            // Force advance to prevent infinite loops if we're stuck
            self.advance();
        }
    }

    // ─────────────────────────────────────────
    //  Public entry point
    // ─────────────────────────────────────────

    pub fn parse_program(&mut self) -> ContentHash {
        let start = self.current_span();
        let mut items: Vec<ContentHash> = Vec::new();

        while self.peek().is_some() {
            match self.parse_top_level_item() {
                Ok(h)  => items.push(h),
                Err(e) => {
                    self.errors.push(e);
                    self.sync_to_next_statement();
                }
            }
        }

        let end_pos = self.tokens.last().map(|st| st.span.end).unwrap_or(0);
        let span = start.start..end_pos;
        self.mk(AstNodeKind::Program(items), span)
    }

    // ─────────────────────────────────────────
    //  Top-level items
    // ─────────────────────────────────────────

    fn parse_top_level_item(&mut self) -> Result<ContentHash, ParseError> {
        match self.peek() {
            Some(Token::Fn) | Some(Token::Stable) => self.parse_fn_def(),
            Some(Token::Let) => {
                let h = self.parse_let()?;
                self.eat(&Token::Semi);
                Ok(h)
            }
            _ => {
                let h = self.parse_stmt()?;
                Ok(h)
            }
        }
    }

    // ─────────────────────────────────────────
    //  Function definition
    // ─────────────────────────────────────────

    fn parse_fn_def(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;

        // Optional `stable` modifier
        let stable = if self.peek() == Some(&Token::Stable) {
            self.advance();
            true
        } else {
            false
        };

        // `fn`
        self.expect(&Token::Fn, "function definition")?;

        // name
        let name = match self.advance() {
            Some(SpannedToken { token: Token::Ident(n), .. }) => n.clone(),
            Some(st) => return Err(ParseError::Unexpected {
                found:    format!("{}", st.token),
                expected: "function name".into(),
                start:    st.span.start,
                end:      st.span.end,
            }),
            None => return Err(ParseError::UnexpectedEof { expected: "function name".into() }),
        };

        // params: `( name: Type, ... )`
        self.expect(&Token::LParen, "parameter list")?;
        let mut params: Vec<(String, AetherTypeSer)> = Vec::new();
        while self.peek() != Some(&Token::RParen) {
            let pname = match self.advance() {
                Some(SpannedToken { token: Token::Ident(n), .. }) => n.clone(),
                Some(st) => return Err(ParseError::Unexpected {
                    found: format!("{}", st.token),
                    expected: "parameter name".into(),
                    start: st.span.start, end: st.span.end,
                }),
                None => return Err(ParseError::UnexpectedEof { expected: "parameter name".into() }),
            };
            self.expect(&Token::Colon, "`:` after parameter name")?;
            let ty = self.parse_type()?;
            params.push((pname, ty));
            if !self.eat(&Token::Comma) { break; }
        }
        self.expect(&Token::RParen, "`)` after parameters")?;

        // optional `-> RetType`
        let ret_ty = if self.eat(&Token::Arrow) {
            Some(self.parse_type()?)
        } else {
            None
        };

        // body block
        let body = self.parse_block()?;
        let end  = self.current_span().end;

        Ok(self.mk(AstNodeKind::FnDef { name, stable, params, ret_ty, body }, start..end))
    }

    // ─────────────────────────────────────────
    //  Type parser
    // ─────────────────────────────────────────

    fn parse_type(&mut self) -> Result<AetherTypeSer, ParseError> {
        match self.advance() {
            Some(SpannedToken { token: Token::TyI32,  .. }) => Ok(AetherTypeSer::I32),
            Some(SpannedToken { token: Token::TyI64,  .. }) => Ok(AetherTypeSer::I64),
            Some(SpannedToken { token: Token::TyF32,  .. }) => Ok(AetherTypeSer::F32),
            Some(SpannedToken { token: Token::TyF64,  .. }) => Ok(AetherTypeSer::F64),
            Some(SpannedToken { token: Token::TyBool, .. }) => Ok(AetherTypeSer::Bool),
            Some(SpannedToken { token: Token::TyStr,  .. }) => Ok(AetherTypeSer::Str),
            Some(SpannedToken { token: Token::TyAuto, .. }) => Ok(AetherTypeSer::Auto),
            Some(st) => Err(ParseError::Unexpected {
                found:    format!("{}", st.token),
                expected: "type name (i32, i64, f64, bool, str, auto, ...)".into(),
                start:    st.span.start,
                end:      st.span.end,
            }),
            None => Err(ParseError::UnexpectedEof { expected: "type name".into() }),
        }
    }

    // ─────────────────────────────────────────
    //  Block: `{ stmt* }`
    // ─────────────────────────────────────────

    fn parse_block(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::LBrace, "block start `{`")?;

        let mut stmts: Vec<ContentHash> = Vec::new();
        while self.peek() != Some(&Token::RBrace) && self.peek().is_some() {
            match self.parse_stmt() {
                Ok(h)  => stmts.push(h),
                Err(e) => {
                    self.errors.push(e);
                    self.sync_to_next_statement();
                }
            }
        }

        let end_sp = self.current_span();
        self.expect(&Token::RBrace, "block end `}`")?;
        Ok(self.mk(AstNodeKind::Block(stmts), start..end_sp.end))
    }

    // ─────────────────────────────────────────
    //  Statement
    // ─────────────────────────────────────────

    fn parse_stmt(&mut self) -> Result<ContentHash, ParseError> {
        let h = match self.peek() {
            Some(Token::Let)      => self.parse_let()?,
            Some(Token::Return)   => self.parse_return()?,
            Some(Token::While)    => self.parse_while()?,
            Some(Token::For)      => self.parse_for()?,
            Some(Token::If)       => self.parse_if()?,
            Some(Token::Raw)      => self.parse_raw_block()?,
            Some(Token::Break)    => { let s = self.current_span(); self.advance(); self.mk(AstNodeKind::Break, s) }
            Some(Token::Continue) => { let s = self.current_span(); self.advance(); self.mk(AstNodeKind::Continue, s) }
            Some(Token::LBrace)   => self.parse_block()?,
            _ => {
                // Expression statement or assignment
                let expr = self.parse_expr()?;
                // Check for `=` assignment
                if self.eat(&Token::Eq) {
                    // expr must be an Ident for assignment
                    if let Some(node) = self.store.get(&expr) {
                        if let AstNodeKind::Ident(name) = &node.kind.clone() {
                            let name = name.clone();
                            let val_start = self.current_span().start;
                            let value = self.parse_expr()?;
                            let val_end = self.current_span().end;
                            let h = self.mk(AstNodeKind::Assign { name, value }, val_start..val_end);
                            self.eat(&Token::Semi);
                            return Ok(h);
                        }
                    }
                }
                self.eat(&Token::Semi);
                expr
            }
        };
        self.eat(&Token::Semi);
        Ok(h)
    }

    // ─────────────────────────────────────────
    //  `let [mut] name [: Type] = expr;`
    // ─────────────────────────────────────────

    fn parse_let(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::Let, "let binding")?;

        let mutable = self.eat(&Token::Mut);

        let name = match self.advance() {
            Some(SpannedToken { token: Token::Ident(n), .. }) => n.clone(),
            Some(st) => return Err(ParseError::Unexpected {
                found: format!("{}", st.token), expected: "variable name".into(),
                start: st.span.start, end: st.span.end,
            }),
            None => return Err(ParseError::UnexpectedEof { expected: "variable name".into() }),
        };

        let ty = if self.eat(&Token::Colon) {
            Some(self.parse_type()?)
        } else {
            None
        };

        self.expect(&Token::Eq, "`=` in let binding")?;
        let value = self.parse_expr()?;
        let end   = self.current_span().end;

        Ok(self.mk(AstNodeKind::Let { name, mutable, ty, value }, start..end))
    }

    // ─────────────────────────────────────────
    //  `return [expr];`
    // ─────────────────────────────────────────

    fn parse_return(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::Return, "return")?;
        let value = if self.peek() == Some(&Token::Semi) || self.peek() == Some(&Token::RBrace) {
            None
        } else {
            Some(self.parse_expr()?)
        };
        let end = self.current_span().end;
        Ok(self.mk(AstNodeKind::Return(value), start..end))
    }

    // ─────────────────────────────────────────
    //  `while cond { body }`
    // ─────────────────────────────────────────

    fn parse_while(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::While, "while")?;
        let cond = self.parse_expr()?;
        let body = self.parse_block()?;
        let end  = self.current_span().end;
        Ok(self.mk(AstNodeKind::While { cond, body }, start..end))
    }

    // ─────────────────────────────────────────
    //  `for var in range(start, end) { body }`
    // ─────────────────────────────────────────

    fn parse_for(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::For, "for")?;

        let var = match self.advance() {
            Some(SpannedToken { token: Token::Ident(n), .. }) => n.clone(),
            Some(st) => return Err(ParseError::Unexpected {
                found: format!("{}", st.token), expected: "loop variable".into(),
                start: st.span.start, end: st.span.end,
            }),
            None => return Err(ParseError::UnexpectedEof { expected: "loop variable".into() }),
        };

        self.expect(&Token::In, "`in`")?;
        let iter = self.parse_expr()?;
        let body = self.parse_block()?;
        let end  = self.current_span().end;
        Ok(self.mk(AstNodeKind::For { var, iter, body }, start..end))
    }

    // ─────────────────────────────────────────
    //  `if cond { then } [else { else }]`
    // ─────────────────────────────────────────

    fn parse_if(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::If, "if")?;
        let cond       = self.parse_expr()?;
        let then_block = self.parse_block()?;

        let else_block = if self.eat(&Token::Else) {
            if self.peek() == Some(&Token::If) {
                Some(self.parse_if()?)
            } else {
                Some(self.parse_block()?)
            }
        } else {
            None
        };

        let end = self.current_span().end;
        Ok(self.mk(AstNodeKind::If { cond, then_block, else_block }, start..end))
    }

    // ─────────────────────────────────────────
    //  `raw { ... }` — quarantined block
    // ─────────────────────────────────────────

    fn parse_raw_block(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        self.expect(&Token::Raw, "raw block")?;
        self.expect(&Token::LBrace, "`{` in raw block")?;
        let mut stmts = Vec::new();
        while self.peek() != Some(&Token::RBrace) && self.peek().is_some() {
            match self.parse_stmt() {
                Ok(h)  => stmts.push(h),
                Err(e) => { self.errors.push(e); self.sync_to_next_statement(); }
            }
        }
        let end = self.current_span().end;
        self.expect(&Token::RBrace, "`}` closing raw block")?;
        Ok(self.mk(AstNodeKind::RawBlock(stmts), start..end))
    }

    // ─────────────────────────────────────────
    //  Expression parsing (Pratt-style)
    // ─────────────────────────────────────────

    pub fn parse_expr(&mut self) -> Result<ContentHash, ParseError> {
        self.parse_or()
    }

    fn parse_or(&mut self) -> Result<ContentHash, ParseError> {
        let mut lhs = self.parse_and()?;
        while self.peek() == Some(&Token::PipePipe) {
            let start = self.current_span().start;
            self.advance();
            let rhs = self.parse_and()?;
            let end  = self.current_span().end;
            lhs = self.mk(AstNodeKind::BinOp { op: BinOpKind::Or, lhs, rhs }, start..end);
        }
        Ok(lhs)
    }

    fn parse_and(&mut self) -> Result<ContentHash, ParseError> {
        let mut lhs = self.parse_cmp()?;
        while self.peek() == Some(&Token::AmpAmp) {
            let start = self.current_span().start;
            self.advance();
            let rhs = self.parse_cmp()?;
            let end  = self.current_span().end;
            lhs = self.mk(AstNodeKind::BinOp { op: BinOpKind::And, lhs, rhs }, start..end);
        }
        Ok(lhs)
    }

    fn parse_cmp(&mut self) -> Result<ContentHash, ParseError> {
        let mut lhs = self.parse_add()?;
        loop {
            let op = match self.peek() {
                Some(Token::EqEq)  => BinOpKind::Eq,
                Some(Token::BangEq)=> BinOpKind::Ne,
                Some(Token::Lt)    => BinOpKind::Lt,
                Some(Token::LtEq)  => BinOpKind::Le,
                Some(Token::Gt)    => BinOpKind::Gt,
                Some(Token::GtEq)  => BinOpKind::Ge,
                _ => break,
            };
            let start = self.current_span().start;
            self.advance();
            let rhs = self.parse_add()?;
            let end  = self.current_span().end;
            lhs = self.mk(AstNodeKind::BinOp { op, lhs, rhs }, start..end);
        }
        Ok(lhs)
    }

    fn parse_add(&mut self) -> Result<ContentHash, ParseError> {
        let mut lhs = self.parse_mul()?;
        loop {
            let op = match self.peek() {
                Some(Token::Plus)  => BinOpKind::Add,
                Some(Token::Minus) => BinOpKind::Sub,
                _ => break,
            };
            let start = self.current_span().start;
            self.advance();
            let rhs = self.parse_mul()?;
            let end  = self.current_span().end;
            lhs = self.mk(AstNodeKind::BinOp { op, lhs, rhs }, start..end);
        }
        Ok(lhs)
    }

    fn parse_mul(&mut self) -> Result<ContentHash, ParseError> {
        let mut lhs = self.parse_unary()?;
        loop {
            let op = match self.peek() {
                Some(Token::Star)    => BinOpKind::Mul,
                Some(Token::Slash)   => BinOpKind::Div,
                Some(Token::Percent) => BinOpKind::Mod,
                _ => break,
            };
            let start = self.current_span().start;
            self.advance();
            let rhs = self.parse_unary()?;
            let end  = self.current_span().end;
            lhs = self.mk(AstNodeKind::BinOp { op, lhs, rhs }, start..end);
        }
        Ok(lhs)
    }

    fn parse_unary(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        match self.peek() {
            Some(Token::Minus) => {
                self.advance();
                let operand = self.parse_unary()?;
                let end = self.current_span().end;
                Ok(self.mk(AstNodeKind::UnaryOp { op: UnaryOpKind::Neg, operand }, start..end))
            }
            Some(Token::Bang) => {
                self.advance();
                let operand = self.parse_unary()?;
                let end = self.current_span().end;
                Ok(self.mk(AstNodeKind::UnaryOp { op: UnaryOpKind::Not, operand }, start..end))
            }
            _ => self.parse_call(),
        }
    }

    fn parse_call(&mut self) -> Result<ContentHash, ParseError> {
        let mut expr = self.parse_primary()?;

        loop {
            if self.peek() == Some(&Token::LParen) {
                // Function call: extract name from ident node
                if let Some(node) = self.store.get(&expr) {
                    if let AstNodeKind::Ident(name) = node.kind.clone() {
                        let start = self.current_span().start;
                        self.advance(); // consume `(`
                        let mut args = Vec::new();
                        while self.peek() != Some(&Token::RParen) {
                            args.push(self.parse_expr()?);
                            if !self.eat(&Token::Comma) { break; }
                        }
                        self.expect(&Token::RParen, "`)` after arguments")?;
                        let end = self.current_span().end;
                        expr = self.mk(AstNodeKind::Call { func: name, args }, start..end);
                        continue;
                    }
                }
            } else if self.peek() == Some(&Token::LBracket) {
                // Index operation: expr[index]
                let start = self.current_span().start;
                self.advance(); // consume `[`
                let index = self.parse_expr()?;
                self.expect(&Token::RBracket, "`]` after index")?;
                let end = self.current_span().end;
                expr = self.mk(AstNodeKind::Index { array: expr, index }, start..end);
                continue;
            }
            break;
        }

        Ok(expr)
    }

    fn parse_primary(&mut self) -> Result<ContentHash, ParseError> {
        let start = self.current_span().start;
        match self.peek().cloned() {
            Some(Token::IntLit(v)) => {
                let span = self.advance().unwrap().span.clone();
                Ok(self.mk(AstNodeKind::IntLit(v), span))
            }
            Some(Token::FloatLit(v)) => {
                let span = self.advance().unwrap().span.clone();
                Ok(self.mk(AstNodeKind::FloatLit(v), span))
            }
            Some(Token::True) => {
                let span = self.advance().unwrap().span.clone();
                Ok(self.mk(AstNodeKind::BoolLit(true), span))
            }
            Some(Token::False) => {
                let span = self.advance().unwrap().span.clone();
                Ok(self.mk(AstNodeKind::BoolLit(false), span))
            }
            Some(Token::StrLit(s)) => {
                let span = self.advance().unwrap().span.clone();
                Ok(self.mk(AstNodeKind::StrLit(s), span))
            }
            Some(Token::KwRange) => {
                self.advance();
                self.expect(&Token::LParen, "`(` after range")?;
                let range_start = self.parse_expr()?;
                self.expect(&Token::Comma, "`,` in range")?;
                let range_end = self.parse_expr()?;
                self.expect(&Token::RParen, "`)` after range")?;
                let end = self.current_span().end;
                Ok(self.mk(AstNodeKind::Range { start: range_start, end: range_end }, start..end))
            }
            Some(Token::LParen) => {
                self.advance();
                let inner = self.parse_expr()?;
                self.expect(&Token::RParen, "`)` closing grouped expression")?;
                Ok(inner)
            }
            Some(Token::LBracket) => {
                // Array literal: [expr, expr, ...]
                let start = self.current_span().start;
                self.advance(); // consume `[`
                let mut elems: Vec<ContentHash> = Vec::new();
                while self.peek() != Some(&Token::RBracket) && self.peek().is_some() {
                    elems.push(self.parse_expr()?);
                    if !self.eat(&Token::Comma) { break; }
                }
                self.expect(&Token::RBracket, "`]` closing array literal")?;
                let end = self.current_span().end;
                Ok(self.mk(AstNodeKind::ArrayLit(elems), start..end))
            }
            Some(Token::If) => self.parse_if(),
            Some(Token::LBrace) => self.parse_block(),
            Some(Token::Ident(name)) => {
                let span = self.advance().unwrap().span.clone();
                Ok(self.mk(AstNodeKind::Ident(name), span))
            }
            Some(tok) => {
                let st = self.peek_spanned().unwrap();
                Err(ParseError::Unexpected {
                    found:    format!("{}", tok),
                    expected: "expression".into(),
                    start:    st.span.start,
                    end:      st.span.end,
                })
            }
            None => Err(ParseError::UnexpectedEof { expected: "expression".into() }),
        }
    }
}
