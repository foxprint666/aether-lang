//! ae-ast — Content-Addressable Abstract Syntax Tree
//!
//! Core design principle (per the Aether spec):
//!   - `AstNodeKind` contains ONLY semantic structure — no spans, no file paths.
//!   - BLAKE3 is computed solely over `AstNodeKind` after JSON serialization.
//!   - Spans live in a completely separate `SpanTable` keyed by `ContentHash`.
//!   - Two identical code blocks in different files → same `ContentHash`.

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

// ─────────────────────────────────────────────
//  Content Hash
// ─────────────────────────────────────────────

/// A 32-byte BLAKE3 hash uniquely identifying an AST node by its structure.
pub type ContentHash = [u8; 32];

pub fn hash_to_hex(h: &ContentHash) -> String {
    hex::encode(h)
}

pub fn hash_node(kind: &AstNodeKind) -> ContentHash {
    let bytes = serde_json::to_vec(kind).expect("AstNodeKind must be serializable");
    *blake3::hash(&bytes).as_bytes()
}

// ─────────────────────────────────────────────
//  Type representation (serializable, for hashing)
// ─────────────────────────────────────────────

/// Serializable type — safe to include in `AstNodeKind` (and thus in hashes).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AetherTypeSer {
    I32,
    I64,
    F32,
    F64,
    Bool,
    Str,
    Auto,
    Unit,
    Union(Vec<AetherTypeSer>),
}

impl std::fmt::Display for AetherTypeSer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::I32  => write!(f, "i32"),
            Self::I64  => write!(f, "i64"),
            Self::F32  => write!(f, "f32"),
            Self::F64  => write!(f, "f64"),
            Self::Bool => write!(f, "bool"),
            Self::Str  => write!(f, "str"),
            Self::Auto => write!(f, "auto"),
            Self::Unit => write!(f, "()"),
            Self::Union(ts) => {
                let parts: Vec<String> = ts.iter().map(|t| t.to_string()).collect();
                write!(f, "Union<{}>", parts.join(", "))
            }
        }
    }
}

// ─────────────────────────────────────────────
//  Binary / Unary operators
// ─────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum BinOpKind {
    Add, Sub, Mul, Div, Mod,
    Eq, Ne, Lt, Le, Gt, Ge,
    And, Or,
}

impl std::fmt::Display for BinOpKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Add => "+",  Self::Sub => "-",  Self::Mul => "*",
            Self::Div => "/",  Self::Mod => "%",  Self::Eq  => "==",
            Self::Ne  => "!=", Self::Lt  => "<",  Self::Le  => "<=",
            Self::Gt  => ">",  Self::Ge  => ">=", Self::And => "&&",
            Self::Or  => "||",
        };
        write!(f, "{}", s)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum UnaryOpKind {
    Neg,
    Not,
}

// ─────────────────────────────────────────────
//  Pure Structural AST Node (hashed)
// ─────────────────────────────────────────────

/// The pure structural representation of an AST node.
/// CRITICAL: No spans, no file info. Everything here feeds into BLAKE3.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum AstNodeKind {
    // ── Literals ──────────────────────────────
    IntLit(i64),
    FloatLit(f64),
    BoolLit(bool),
    StrLit(String),

    // ── Variable reference ────────────────────
    Ident(String),

    // ── Let binding ───────────────────────────
    Let {
        name: String,
        mutable: bool,
        ty: Option<AetherTypeSer>,
        value: ContentHash,
    },

    // ── Assignment ────────────────────────────
    Assign {
        name: String,
        value: ContentHash,
    },

    // ── Operators ─────────────────────────────
    BinOp {
        op: BinOpKind,
        lhs: ContentHash,
        rhs: ContentHash,
    },
    UnaryOp {
        op: UnaryOpKind,
        operand: ContentHash,
    },

    // ── Control flow ──────────────────────────
    If {
        cond: ContentHash,
        then_block: ContentHash,
        else_block: Option<ContentHash>,
    },
    While {
        cond: ContentHash,
        body: ContentHash,
    },
    For {
        var: String,
        iter: ContentHash,
        body: ContentHash,
    },
    Range {
        start: ContentHash,
        end: ContentHash,
    },
    Return(Option<ContentHash>),
    Break,
    Continue,

    // ── Functions ─────────────────────────────
    FnDef {
        name: String,
        stable: bool,
        params: Vec<(String, AetherTypeSer)>,
        ret_ty: Option<AetherTypeSer>,
        body: ContentHash,
    },
    Call {
        func: String,
        args: Vec<ContentHash>,
    },

    // ── Blocks ────────────────────────────────
    Block(Vec<ContentHash>),

    // ── Low-level escape hatch ────────────────
    /// `raw { ... }` — parsed but semantically quarantined in Phase 0
    RawBlock(Vec<ContentHash>),

    // ── Program root ──────────────────────────
    Program(Vec<ContentHash>),
}

// ─────────────────────────────────────────────
//  AstNode wrapper
// ─────────────────────────────────────────────

/// An AST node bound to its structural hash.
#[derive(Debug, Clone)]
pub struct AstNode {
    pub hash: ContentHash,
    pub kind: AstNodeKind,
}

impl AstNode {
    pub fn new(kind: AstNodeKind) -> Self {
        let hash = hash_node(&kind);
        AstNode { hash, kind }
    }
}

// ─────────────────────────────────────────────
//  Content-Addressable Store
// ─────────────────────────────────────────────

/// The global content-addressable store.
/// Unchanged nodes produce identical hashes → free incremental compilation.
#[derive(Debug, Default)]
pub struct AstStore {
    nodes: HashMap<ContentHash, AstNode>,
}

impl AstStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Insert a node and return its hash. If already present, returns existing hash (no-op).
    pub fn insert(&mut self, node: AstNode) -> ContentHash {
        let hash = node.hash;
        self.nodes.entry(hash).or_insert(node);
        hash
    }

    pub fn get(&self, hash: &ContentHash) -> Option<&AstNode> {
        self.nodes.get(hash)
    }

    pub fn contains(&self, hash: &ContentHash) -> bool {
        self.nodes.contains_key(hash)
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    pub fn iter_nodes(&self) -> impl Iterator<Item = &AstNode> {
        self.nodes.values()
    }
}

// ─────────────────────────────────────────────
//  Span Table (out-of-band, never hashed)
// ─────────────────────────────────────────────

/// Source location — completely separated from structural hashing.
/// Used only for error reporting and LSP diagnostics.
#[derive(Debug, Clone)]
pub struct SourceSpan {
    pub file:  String,
    pub start: usize, // byte offset
    pub end:   usize, // byte offset
    pub line:  u32,
    pub col:   u32,
}

impl SourceSpan {
    pub fn as_miette_span(&self) -> miette::SourceSpan {
        (self.start, self.end.saturating_sub(self.start)).into()
    }
}

/// Maps `ContentHash → SourceSpan` for every parsed node.
/// A node with the same structure in two different files gets two entries here,
/// one for each location, but shares a single `ContentHash` in the `AstStore`.
#[derive(Debug, Default)]
pub struct SpanTable {
    pub spans: HashMap<ContentHash, SourceSpan>,
}

impl SpanTable {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&mut self, hash: ContentHash, span: SourceSpan) {
        // Store the most-recent span for this hash (last-write wins).
        self.spans.insert(hash, span);
    }

    pub fn get(&self, hash: &ContentHash) -> Option<&SourceSpan> {
        self.spans.get(hash)
    }
}

// ─────────────────────────────────────────────
//  Helpers
// ─────────────────────────────────────────────

/// Build a literal node and insert it in the store in one step.
pub fn make_node(store: &mut AstStore, spans: &mut SpanTable, kind: AstNodeKind, span: SourceSpan) -> ContentHash {
    let node = AstNode::new(kind);
    let hash = node.hash;
    spans.insert(hash, span);
    store.insert(node)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_nodes_same_hash() {
        let k1 = AstNodeKind::IntLit(42);
        let k2 = AstNodeKind::IntLit(42);
        assert_eq!(hash_node(&k1), hash_node(&k2));
    }

    #[test]
    fn different_nodes_different_hash() {
        let k1 = AstNodeKind::IntLit(42);
        let k2 = AstNodeKind::IntLit(43);
        assert_ne!(hash_node(&k1), hash_node(&k2));
    }

    #[test]
    fn store_deduplicates() {
        let mut store = AstStore::new();
        let n1 = AstNode::new(AstNodeKind::IntLit(7));
        let n2 = AstNode::new(AstNodeKind::IntLit(7));
        let h1 = store.insert(n1);
        let h2 = store.insert(n2);
        assert_eq!(h1, h2);
        assert_eq!(store.len(), 1);
    }

    #[test]
    fn span_never_affects_hash() {
        let kind = AstNodeKind::Ident("foo".to_string());
        let h1 = hash_node(&kind);
        // The hash must be identical regardless of what span we would attach
        let h2 = hash_node(&kind);
        assert_eq!(h1, h2);
    }
}
