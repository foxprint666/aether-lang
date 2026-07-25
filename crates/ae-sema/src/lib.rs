//! ae-sema — Semantic Analyzer for Aether
//!
//! Responsibilities:
//!   1. Flow-sensitive type inference over the AstStore
//!   2. `stable` function enforcement (hard error on Union-typed variables)
//!   3. Undefined variable / function detection
//!   4. Emitting structured `SemaDiagnostic` messages with LSP-ready spans

use std::collections::HashMap;

use ae_ast::{AetherTypeSer, AstNodeKind, AstStore, BinOpKind, ContentHash, SpanTable, UnaryOpKind};

// ─────────────────────────────────────────────
//  Runtime type (richer than AetherTypeSer)
// ─────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub enum AetherType {
    I32,
    I64,
    F32,
    F64,
    Bool,
    Str,
    Auto,
    Unit,
    Union(Vec<AetherType>),
    /// Typed array: `[T]`
    Array(Box<AetherType>),
    Fn {
        params: Vec<AetherType>,
        ret: Box<AetherType>,
    },
}

impl std::fmt::Display for AetherType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::I32 => write!(f, "i32"),
            Self::I64 => write!(f, "i64"),
            Self::F32 => write!(f, "f32"),
            Self::F64 => write!(f, "f64"),
            Self::Bool => write!(f, "bool"),
            Self::Str => write!(f, "str"),
            Self::Auto => write!(f, "auto"),
            Self::Unit => write!(f, "()"),
            Self::Union(ts) => {
                let s: Vec<_> = ts.iter().map(|t| t.to_string()).collect();
                write!(f, "Union<{}>", s.join(", "))
            }
            Self::Array(inner) => write!(f, "[{}]", inner),
            Self::Fn { params, ret } => {
                let ps: Vec<_> = params.iter().map(|t| t.to_string()).collect();
                write!(f, "fn({}) -> {}", ps.join(", "), ret)
            }
        }
    }
}

impl From<&AetherTypeSer> for AetherType {
    fn from(s: &AetherTypeSer) -> Self {
        match s {
            AetherTypeSer::I32   => AetherType::I32,
            AetherTypeSer::I64   => AetherType::I64,
            AetherTypeSer::F32   => AetherType::F32,
            AetherTypeSer::F64   => AetherType::F64,
            AetherTypeSer::Bool  => AetherType::Bool,
            AetherTypeSer::Str   => AetherType::Str,
            AetherTypeSer::Auto  => AetherType::Auto,
            AetherTypeSer::Unit  => AetherType::Unit,
            AetherTypeSer::Union(ts) => AetherType::Union(ts.iter().map(Into::into).collect()),
            AetherTypeSer::Array(inner) => AetherType::Array(Box::new(AetherType::from(inner.as_ref()))),
        }
    }
}

fn merge_types(a: AetherType, b: AetherType) -> AetherType {
    if a == b {
        return a;
    }
    // Numeric promotions
    match (&a, &b) {
        (AetherType::I32, AetherType::I64) | (AetherType::I64, AetherType::I32) => AetherType::I64,
        (AetherType::F32, AetherType::F64) | (AetherType::F64, AetherType::F32) => AetherType::F64,
        (AetherType::I32, AetherType::F64) | (AetherType::F64, AetherType::I32) => AetherType::F64,
        (AetherType::I64, AetherType::F64) | (AetherType::F64, AetherType::I64) => AetherType::F64,
        // Integer literals can widen
        (AetherType::I32, AetherType::F32) | (AetherType::F32, AetherType::I32) => AetherType::F32,
        _ => {
            // Form a Union — this triggers stability violation if in a `stable` function
            let mut ts = Vec::new();
            match a { AetherType::Union(mut v) => ts.append(&mut v), t => ts.push(t) }
            match b { AetherType::Union(mut v) => ts.append(&mut v), t => ts.push(t) }
            ts.dedup_by(|a, b| a == b);
            AetherType::Union(ts)
        }
    }
}

fn is_union(t: &AetherType) -> bool {
    matches!(t, AetherType::Union(_))
}

// ─────────────────────────────────────────────
//  Diagnostics
// ─────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum DiagSeverity {
    Error,
    Warning,
    Info,
}

#[derive(Debug, Clone)]
pub struct SemaDiagnostic {
    pub severity: DiagSeverity,
    pub message:  String,
    pub hash:     ContentHash,
    /// human-readable hex snippet of hash
    pub hash_hex: String,
    /// Suggestion for AI agents to parse
    pub suggestion: Option<String>,
    /// Stability heatmap level: 0 = mono, 1 = dynamic, 2 = dynamic.critical
    pub stability_level: u8,
}

impl SemaDiagnostic {
    fn error(hash: ContentHash, msg: impl Into<String>, hint: impl Into<String>, level: u8) -> Self {
        let hash_hex = ae_ast::hash_to_hex(&hash);
        SemaDiagnostic {
            severity: DiagSeverity::Error,
            message: msg.into(),
            hash,
            hash_hex,
            suggestion: Some(hint.into()),
            stability_level: level,
        }
    }
    fn warn(hash: ContentHash, msg: impl Into<String>) -> Self {
        let hash_hex = ae_ast::hash_to_hex(&hash);
        SemaDiagnostic {
            severity: DiagSeverity::Warning,
            message: msg.into(),
            hash,
            hash_hex,
            suggestion: None,
            stability_level: 1,
        }
    }
}

// ─────────────────────────────────────────────
//  Semantic analysis result
// ─────────────────────────────────────────────

pub struct SemaResult {
    /// Type of every expression node, keyed by ContentHash
    pub types: HashMap<ContentHash, AetherType>,
    pub diagnostics: Vec<SemaDiagnostic>,
    /// Function signatures: name → (param_types, return_type)
    pub fn_sigs: HashMap<String, (Vec<AetherType>, AetherType)>,
}

impl SemaResult {
    pub fn has_errors(&self) -> bool {
        self.diagnostics.iter().any(|d| matches!(d.severity, DiagSeverity::Error))
    }

    /// Returns the stability level of a node: 0=mono, 1=dynamic, 2=critical
    pub fn stability_level(&self, hash: &ContentHash) -> u8 {
        match self.types.get(hash) {
            Some(AetherType::Union(_)) => 1,
            Some(AetherType::Auto)     => 1,
            _                          => 0,
        }
    }
}

// ─────────────────────────────────────────────
//  Type Environment (scope stack)
// ─────────────────────────────────────────────

#[derive(Default)]
struct TypeEnv {
    scopes: Vec<HashMap<String, AetherType>>,
}

impl TypeEnv {
    fn push(&mut self) {
        self.scopes.push(HashMap::new());
    }
    fn pop(&mut self) {
        self.scopes.pop();
    }
    fn define(&mut self, name: &str, ty: AetherType) {
        if let Some(scope) = self.scopes.last_mut() {
            scope.insert(name.to_string(), ty);
        }
    }
    fn lookup(&self, name: &str) -> Option<&AetherType> {
        for scope in self.scopes.iter().rev() {
            if let Some(t) = scope.get(name) {
                return Some(t);
            }
        }
        None
    }
}

// ─────────────────────────────────────────────
//  Analyzer
// ─────────────────────────────────────────────

pub struct Analyzer<'a> {
    store:        &'a AstStore,
    spans:        &'a SpanTable,
    types:        HashMap<ContentHash, AetherType>,
    diagnostics:  Vec<SemaDiagnostic>,
    env:          TypeEnv,
    fn_sigs:      HashMap<String, (Vec<AetherType>, AetherType)>,
    in_stable_fn: bool,
}

impl<'a> Analyzer<'a> {
    pub fn new(store: &'a AstStore, spans: &'a SpanTable) -> Self {
        let mut a = Analyzer {
            store,
            spans,
            types: HashMap::new(),
            diagnostics: Vec::new(),
            env: TypeEnv::default(),
            fn_sigs: HashMap::new(),
            in_stable_fn: false,
        };
        a.env.push(); // global scope
        // ── Built-in functions ───────────────────────────────────────
        let auto = AetherType::Auto;
        let unit = AetherType::Unit;
        let bool_t = AetherType::Bool;
        let int_t  = AetherType::I64;
        let float_t = AetherType::F64;
        let str_t  = AetherType::Str;
        let arr_t  = AetherType::Array(Box::new(AetherType::Auto));

        // I/O
        a.fn_sigs.insert("print".into(),   (vec![auto.clone()], unit.clone()));
        a.fn_sigs.insert("eprint".into(),  (vec![auto.clone()], unit.clone()));
        // Assertions
        a.fn_sigs.insert("assert".into(),  (vec![bool_t.clone(), auto.clone()], unit.clone()));
        a.fn_sigs.insert("panic".into(),   (vec![str_t.clone()], unit.clone()));
        // Type conversion
        a.fn_sigs.insert("to_str".into(),  (vec![auto.clone()], str_t.clone()));
        a.fn_sigs.insert("to_int".into(),  (vec![auto.clone()], int_t.clone()));
        a.fn_sigs.insert("to_float".into(),(vec![auto.clone()], float_t.clone()));
        // String ops
        a.fn_sigs.insert("format".into(),  (vec![str_t.clone()], str_t.clone()));
        a.fn_sigs.insert("str_len".into(), (vec![str_t.clone()], int_t.clone()));
        a.fn_sigs.insert("str_contains".into(),   (vec![str_t.clone(), str_t.clone()], bool_t.clone()));
        a.fn_sigs.insert("str_starts_with".into(),(vec![str_t.clone(), str_t.clone()], bool_t.clone()));
        // Math
        a.fn_sigs.insert("sqrt".into(), (vec![float_t.clone()], float_t.clone()));
        a.fn_sigs.insert("abs".into(),  (vec![auto.clone()],    auto.clone()));
        a.fn_sigs.insert("min".into(),  (vec![auto.clone(), auto.clone()], auto.clone()));
        a.fn_sigs.insert("max".into(),  (vec![auto.clone(), auto.clone()], auto.clone()));
        a.fn_sigs.insert("pow".into(),  (vec![auto.clone(), auto.clone()], auto.clone()));
        // Array ops
        a.fn_sigs.insert("len".into(),        (vec![auto.clone()],                          int_t.clone()));
        a.fn_sigs.insert("push".into(),       (vec![arr_t.clone(), auto.clone()],            unit.clone()));
        a.fn_sigs.insert("pop".into(),        (vec![arr_t.clone()],                          auto.clone()));
        a.fn_sigs.insert("get".into(),        (vec![arr_t.clone(), int_t.clone()],           auto.clone()));
        a.fn_sigs.insert("set".into(),        (vec![arr_t.clone(), int_t.clone(), auto.clone()], unit.clone()));
        a.fn_sigs.insert("new_array".into(),  (vec![int_t.clone(), auto.clone()],            arr_t.clone()));
        a.fn_sigs.insert("array_copy".into(), (vec![arr_t.clone()],                          arr_t.clone()));
        a
    }

    fn record(&mut self, hash: ContentHash, ty: AetherType) -> AetherType {
        self.types.insert(hash, ty.clone());
        ty
    }

    // ── Analyze a node by hash ────────────────

    pub fn analyze(&mut self, hash: ContentHash) -> AetherType {
        // Clone kind to avoid borrow conflicts
        let kind = match self.store.get(&hash) {
            Some(n) => n.kind.clone(),
            None    => return AetherType::Unit,
        };

        let ty = self.analyze_kind(hash, kind);
        self.types.insert(hash, ty.clone());
        ty
    }

    fn analyze_kind(&mut self, hash: ContentHash, kind: AstNodeKind) -> AetherType {
        match kind {
            // ── Literals ─────────────────────
            AstNodeKind::IntLit(_)   => AetherType::I64,
            AstNodeKind::FloatLit(_) => AetherType::F64,
            AstNodeKind::BoolLit(_)  => AetherType::Bool,
            AstNodeKind::StrLit(_)   => AetherType::Str,

            // ── Identifier ───────────────────
            AstNodeKind::Ident(name) => {
                match self.env.lookup(&name).cloned() {
                    Some(t) => t,
                    None => {
                        self.diagnostics.push(SemaDiagnostic::error(
                            hash,
                            format!("undefined variable `{}`", name),
                            format!("declare it with `let {} = ...`", name),
                            2,
                        ));
                        AetherType::Auto
                    }
                }
            }

            // ── Let binding ──────────────────
            AstNodeKind::Let { name, ty, value, .. } => {
                let inferred = self.analyze(value);
                let resolved = if let Some(annotation) = ty {
                    AetherType::from(&annotation)
                } else {
                    inferred.clone()
                };

                // Stability check inside `stable` function
                if self.in_stable_fn && is_union(&resolved) {
                    self.diagnostics.push(SemaDiagnostic::error(
                        hash,
                        format!(
                            "type stability violation: `{}` inferred as `{}` in `stable` function",
                            name, resolved
                        ),
                        "ensure both branches produce the same concrete type",
                        2,
                    ));
                }

                self.env.define(&name, resolved.clone());
                resolved
            }

            // ── Assignment ───────────────────
            AstNodeKind::Assign { name, value } => {
                let val_ty = self.analyze(value);
                let cur_ty = self.env.lookup(&name).cloned();
                let new_ty = match cur_ty {
                    Some(existing) => merge_types(existing, val_ty),
                    None => val_ty,
                };
                if self.in_stable_fn && is_union(&new_ty) {
                    self.diagnostics.push(SemaDiagnostic::warn(
                        hash,
                        format!("assignment to `{}` widens type to `{}` (dynamic dispatch)", name, new_ty),
                    ));
                }
                self.env.define(&name, new_ty.clone());
                AetherType::Unit
            }

            // ── Binary operation ─────────────
            AstNodeKind::BinOp { op, lhs, rhs } => {
                let lt = self.analyze(lhs);
                let rt = self.analyze(rhs);
                match &op {
                    BinOpKind::Eq | BinOpKind::Ne | BinOpKind::Lt |
                    BinOpKind::Le | BinOpKind::Gt | BinOpKind::Ge |
                    BinOpKind::And | BinOpKind::Or => AetherType::Bool,
                    _ => merge_types(lt, rt),
                }
            }

            // ── Unary operation ──────────────
            AstNodeKind::UnaryOp { op, operand } => {
                let t = self.analyze(operand);
                match op {
                    UnaryOpKind::Not => AetherType::Bool,
                    UnaryOpKind::Neg => t,
                }
            }

            // ── Function call ────────────────
            AstNodeKind::Call { func, args } => {
                for a in args { self.analyze(a); }
                match self.fn_sigs.get(&func).cloned() {
                    Some((_, ret)) => ret,
                    None => {
                        self.diagnostics.push(SemaDiagnostic::error(
                            hash,
                            format!("call to undefined function `{}`", func),
                            format!("define it with `fn {}(...) {{ ... }}`", func),
                            2,
                        ));
                        AetherType::Auto
                    }
                }
            }

            // ── If / else ────────────────────
            AstNodeKind::If { cond, then_block, else_block } => {
                self.analyze(cond);
                let then_ty = self.analyze(then_block);
                let else_ty = else_block.map(|h| self.analyze(h)).unwrap_or(AetherType::Unit);
                merge_types(then_ty, else_ty)
            }

            // ── While ────────────────────────
            AstNodeKind::While { cond, body } => {
                self.analyze(cond);
                self.env.push();
                self.analyze(body);
                self.env.pop();
                AetherType::Unit
            }

            // ── For ──────────────────────────
            AstNodeKind::For { var, iter, body } => {
                self.analyze(iter);
                self.env.push();
                self.env.define(&var, AetherType::I64); // range always yields i64
                self.analyze(body);
                self.env.pop();
                AetherType::Unit
            }

            // ── Range ────────────────────────
            AstNodeKind::Range { start, end } => {
                self.analyze(start);
                self.analyze(end);
                AetherType::Auto // Range<i64> — simplified for Phase 0
            }

            // ── Return ───────────────────────
            AstNodeKind::Return(val) => {
                val.map(|h| self.analyze(h)).unwrap_or(AetherType::Unit)
            }

            // ── Block ────────────────────────
            AstNodeKind::Block(stmts) => {
                self.env.push();
                let mut last = AetherType::Unit;
                for s in stmts {
                    last = self.analyze(s);
                }
                self.env.pop();
                last
            }

            // ── Function definition ──────────
            AstNodeKind::FnDef { name, stable, params, ret_ty, body } => {
                let param_types: Vec<AetherType> = params.iter().map(|(_, t)| AetherType::from(t)).collect();
                let declared_ret = ret_ty.as_ref().map(AetherType::from).unwrap_or(AetherType::Auto);

                // Register signature BEFORE analyzing body (enables recursion)
                self.fn_sigs.insert(name.clone(), (param_types.clone(), declared_ret.clone()));

                let prev_stable = self.in_stable_fn;
                if stable { self.in_stable_fn = true; }

                self.env.push();
                for (pname, pty) in &params {
                    self.env.define(pname, AetherType::from(pty));
                }
                let body_ty = self.analyze(body);
                self.env.pop();

                // Reconcile return type
                let actual_ret = if declared_ret == AetherType::Auto { body_ty } else { declared_ret };

                // Update signature with inferred return type
                self.fn_sigs.insert(name.clone(), (param_types, actual_ret.clone()));

                if stable && is_union(&actual_ret) {
                    self.diagnostics.push(SemaDiagnostic::error(
                        hash,
                        format!(
                            "type stability violation: `stable fn {}` returns `{}`; must return a concrete type",
                            name, actual_ret
                        ),
                        "ensure all code paths return the same concrete type",
                        2,
                    ));
                }

                self.in_stable_fn = prev_stable;
                AetherType::Unit
            }

            // ── Program ──────────────────────
            AstNodeKind::Program(items) => {
                // First pass: register all top-level fn signatures
                for h in &items {
                    if let Some(node) = self.store.get(h) {
                        if let AstNodeKind::FnDef { name, params, ret_ty, .. } = &node.kind.clone() {
                            let pts: Vec<_> = params.iter().map(|(_, t)| AetherType::from(t)).collect();
                            let ret = ret_ty.as_ref().map(AetherType::from).unwrap_or(AetherType::Auto);
                            self.fn_sigs.insert(name.clone(), (pts, ret));
                        }
                    }
                }
                // Second pass: full analysis
                for h in items { self.analyze(h); }
                AetherType::Unit
            }

            // ── Raw block — quarantine ───────
            AstNodeKind::RawBlock(_) => {
                // Raw blocks are not analyzed in Phase 0 — they bypass type checking
                AetherType::Unit
            }

            AstNodeKind::Break | AstNodeKind::Continue => AetherType::Unit,

            // ── Array literal ─────────────────
            AstNodeKind::ArrayLit(elems) => {
                // Infer element type from first element; subsequent elements widen via merge
                let mut elem_ty = AetherType::Auto;
                for h in elems {
                    let t = self.analyze(h);
                    elem_ty = merge_types(elem_ty, t);
                }
                AetherType::Array(Box::new(elem_ty))
            }

            // ── Index ────────────────────────
            AstNodeKind::Index { array, index } => {
                let arr_ty = self.analyze(array);
                self.analyze(index);
                match arr_ty {
                    AetherType::Array(inner) => *inner,
                    AetherType::Str => AetherType::Str, // char indexing returns str
                    _ => AetherType::Auto,
                }
            }
        }
    }

    pub fn into_result(self) -> SemaResult {
        SemaResult {
            types: self.types,
            diagnostics: self.diagnostics,
            fn_sigs: self.fn_sigs,
        }
    }
}

/// Top-level semantic analysis entry point.
pub fn analyze(
    root: ContentHash,
    store: &AstStore,
    spans: &SpanTable,
) -> SemaResult {
    let mut analyzer = Analyzer::new(store, spans);
    analyzer.analyze(root);
    analyzer.into_result()
}
