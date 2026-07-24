//! ae-codegen — Code Generation for Aether
//!
//! Phase 0 implements a **tree-walking interpreter** that directly executes
//! the typed AST. This gives us a fully working `ae run` immediately, while
//! the Cranelift JIT backend is scaffolded for Phase 1.
//!
//! # Cranelift SSA Note
//! When the JIT is active in Phase 1, all mutable variables will use
//! `cranelift_frontend::Variable` + `builder.declare_var()` / `def_var()` /
//! `use_var()`. Cranelift constructs SSA form (with phi nodes) automatically
//! from these calls — no manual dominance-frontier analysis required.

use std::collections::HashMap;

use ae_ast::{AstNodeKind, AstStore, BinOpKind, ContentHash, SpanTable, UnaryOpKind};
use ae_sema::SemaResult;

// ─────────────────────────────────────────────
//  Runtime value
// ─────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Int(i64),
    Float(f64),
    Bool(bool),
    Str(String),
    Unit,
}

impl std::fmt::Display for Value {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Value::Int(n)   => write!(f, "{}", n),
            Value::Float(v) => {
                if v.fract() == 0.0 { write!(f, "{:.1}", v) }
                else                { write!(f, "{}", v) }
            }
            Value::Bool(b)  => write!(f, "{}", b),
            Value::Str(s)   => write!(f, "{}", s),
            Value::Unit     => write!(f, "()"),
        }
    }
}

// ─────────────────────────────────────────────
//  Execution error
// ─────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum ExecError {
    #[error("undefined variable `{0}`")]
    UndefinedVar(String),
    #[error("undefined function `{0}`")]
    UndefinedFn(String),
    #[error("type error: {0}")]
    TypeError(String),
    #[error("division by zero")]
    DivByZero,
    #[error("assertion failed")]
    AssertFailed,
    #[error("missing AST node for hash")]
    MissingNode,
    #[error("raw blocks cannot be executed in Phase 0 (unsafe escape hatch)")]
    RawBlock,
}

// ─────────────────────────────────────────────
//  Control flow signal
// ─────────────────────────────────────────────

/// Used internally to propagate `return`, `break`, and `continue`.
#[derive(Debug)]
enum Signal {
    Value(Value),
    Return(Value),
    Break,
    Continue,
}

impl Signal {
    fn into_value(self) -> Value {
        match self {
            Signal::Value(v) | Signal::Return(v) => v,
            Signal::Break | Signal::Continue => Value::Unit,
        }
    }
}

// ─────────────────────────────────────────────
//  Interpreter
// ─────────────────────────────────────────────

pub struct Interpreter<'a> {
    store:    &'a AstStore,
    _spans:   &'a SpanTable,
    _sema:    &'a SemaResult,
    /// Function definitions: name → body hash + param names
    fns:      HashMap<String, (Vec<String>, ContentHash)>,
    /// Global env (call frames pushed on the stack below)
    globals:  HashMap<String, Value>,
}

type Frame = HashMap<String, Value>;

impl<'a> Interpreter<'a> {
    pub fn new(store: &'a AstStore, spans: &'a SpanTable, sema: &'a SemaResult) -> Self {
        // Pre-register function bodies from the store
        let mut fns = HashMap::new();
        for node in store.iter_nodes() {
            if let AstNodeKind::FnDef { name, params, body, .. } = &node.kind {
                let pnames: Vec<String> = params.iter().map(|(n, _)| n.clone()).collect();
                fns.insert(name.clone(), (pnames, *body));
            }
        }
        Interpreter {
            store,
            _spans: spans,
            _sema: sema,
            fns,
            globals: HashMap::new(),
        }
    }

    // ── Entry point ─────────────────────────

    pub fn run(&mut self, root: ContentHash) -> Result<(), ExecError> {
        // Execute top-level (register fns, run global stmts, then call main)
        let kind = self.node(root)?.clone();
        if let AstNodeKind::Program(items) = kind {
            // Pass 1: collect all fn defs
            for h in &items {
                if let Ok(AstNodeKind::FnDef { name, params, body, .. }) = self.node(*h).map(|n| n.clone()) {
                    let pnames: Vec<_> = params.iter().map(|(n, _)| n.clone()).collect();
                    self.fns.insert(name, (pnames, body));
                }
            }
            // Pass 2: execute non-fn top-level stmts
            for h in &items {
                if let Ok(k) = self.node(*h).map(|n| n.clone()) {
                    if !matches!(k, AstNodeKind::FnDef { .. }) {
                        self.exec(*h, &mut HashMap::new())?;
                    }
                }
            }
            // Pass 3: call main() if defined
            if self.fns.contains_key("main") {
                self.call_fn("main", vec![], &mut HashMap::new())?;
            }
        }
        Ok(())
    }

    fn node(&self, hash: ContentHash) -> Result<&AstNodeKind, ExecError> {
        self.store.get(&hash).map(|n| &n.kind).ok_or(ExecError::MissingNode)
    }

    // ── Execute a node, return a Signal ─────

    fn exec(&mut self, hash: ContentHash, frame: &mut Frame) -> Result<Signal, ExecError> {
        let kind = self.node(hash)?.clone();
        match kind {
            // ── Literals ─────────────────────
            AstNodeKind::IntLit(n)   => Ok(Signal::Value(Value::Int(n))),
            AstNodeKind::FloatLit(f) => Ok(Signal::Value(Value::Float(f))),
            AstNodeKind::BoolLit(b)  => Ok(Signal::Value(Value::Bool(b))),
            AstNodeKind::StrLit(s)   => Ok(Signal::Value(Value::Str(s))),

            // ── Identifier ───────────────────
            AstNodeKind::Ident(name) => {
                let val = frame.get(&name)
                    .or_else(|| self.globals.get(&name))
                    .cloned()
                    .ok_or_else(|| ExecError::UndefinedVar(name))?;
                Ok(Signal::Value(val))
            }

            // ── Let binding ──────────────────
            AstNodeKind::Let { name, value, .. } => {
                let v = self.exec(value, frame)?.into_value();
                frame.insert(name, v);
                Ok(Signal::Value(Value::Unit))
            }

            // ── Assignment ───────────────────
            AstNodeKind::Assign { name, value } => {
                let v = self.exec(value, frame)?.into_value();
                if frame.contains_key(&name) {
                    frame.insert(name, v);
                } else {
                    self.globals.insert(name, v);
                }
                Ok(Signal::Value(Value::Unit))
            }

            // ── Binary operation ─────────────
            AstNodeKind::BinOp { op, lhs, rhs } => {
                let lv = self.exec(lhs, frame)?.into_value();
                // Short-circuit evaluation for && and ||
                if op == BinOpKind::And {
                    if lv == Value::Bool(false) { return Ok(Signal::Value(Value::Bool(false))); }
                }
                if op == BinOpKind::Or {
                    if lv == Value::Bool(true) { return Ok(Signal::Value(Value::Bool(true))); }
                }
                let rv = self.exec(rhs, frame)?.into_value();
                Ok(Signal::Value(eval_binop(op, lv, rv)?))
            }

            // ── Unary operation ──────────────
            AstNodeKind::UnaryOp { op, operand } => {
                let v = self.exec(operand, frame)?.into_value();
                let result = match (op, v) {
                    (UnaryOpKind::Neg, Value::Int(n))   => Value::Int(-n),
                    (UnaryOpKind::Neg, Value::Float(f)) => Value::Float(-f),
                    (UnaryOpKind::Not, Value::Bool(b))  => Value::Bool(!b),
                    _ => return Err(ExecError::TypeError("invalid unary operand".into())),
                };
                Ok(Signal::Value(result))
            }

            // ── Function call ────────────────
            AstNodeKind::Call { func, args } => {
                let mut arg_vals = Vec::new();
                for a in args { arg_vals.push(self.exec(a, frame)?.into_value()); }
                let result = self.call_fn(&func, arg_vals, frame)?;
                Ok(Signal::Value(result))
            }

            // ── Block ────────────────────────
            AstNodeKind::Block(stmts) => {
                let mut last = Signal::Value(Value::Unit);
                for h in stmts {
                    last = self.exec(h, frame)?;
                    if matches!(last, Signal::Return(_) | Signal::Break | Signal::Continue) {
                        return Ok(last);
                    }
                }
                Ok(last)
            }

            // ── If / else ────────────────────
            AstNodeKind::If { cond, then_block, else_block } => {
                let cv = self.exec(cond, frame)?.into_value();
                match cv {
                    Value::Bool(true)  => self.exec(then_block, frame),
                    Value::Bool(false) => {
                        if let Some(eb) = else_block {
                            self.exec(eb, frame)
                        } else {
                            Ok(Signal::Value(Value::Unit))
                        }
                    }
                    _ => Err(ExecError::TypeError("condition must be bool".into())),
                }
            }

            // ── While ────────────────────────
            AstNodeKind::While { cond, body } => {
                loop {
                    let cv = self.exec(cond, frame)?.into_value();
                    match cv {
                        Value::Bool(false) => break,
                        Value::Bool(true)  => {
                            match self.exec(body, frame)? {
                                Signal::Break    => break,
                                Signal::Continue => continue,
                                Signal::Return(v) => return Ok(Signal::Return(v)),
                                _ => {}
                            }
                        }
                        _ => return Err(ExecError::TypeError("while condition must be bool".into())),
                    }
                }
                Ok(Signal::Value(Value::Unit))
            }

            // ── For ──────────────────────────
            AstNodeKind::For { var, iter, body } => {
                let iter_hash = iter;
                let iter_kind = self.node(iter_hash)?.clone();
                let (start_h, end_h) = match iter_kind {
                    AstNodeKind::Range { start, end } => (start, end),
                    _ => return Err(ExecError::TypeError("for loop requires a range(start, end)".into())),
                };
                let start = match self.exec(start_h, frame)?.into_value() {
                    Value::Int(n) => n,
                    _ => return Err(ExecError::TypeError("range start must be i64".into())),
                };
                let end = match self.exec(end_h, frame)?.into_value() {
                    Value::Int(n) => n,
                    _ => return Err(ExecError::TypeError("range end must be i64".into())),
                };
                for i in start..end {
                    frame.insert(var.clone(), Value::Int(i));
                    match self.exec(body, frame)? {
                        Signal::Break    => break,
                        Signal::Continue => continue,
                        Signal::Return(v) => return Ok(Signal::Return(v)),
                        _ => {}
                    }
                }
                frame.remove(&var);
                Ok(Signal::Value(Value::Unit))
            }

            // ── Range ────────────────────────
            AstNodeKind::Range { .. } => {
                // Range is only valid as a for-loop iterator (handled above)
                Ok(Signal::Value(Value::Unit))
            }

            // ── Return ───────────────────────
            AstNodeKind::Return(val) => {
                let v = if let Some(h) = val {
                    self.exec(h, frame)?.into_value()
                } else {
                    Value::Unit
                };
                Ok(Signal::Return(v))
            }

            AstNodeKind::Break    => Ok(Signal::Break),
            AstNodeKind::Continue => Ok(Signal::Continue),

            // ── FnDef (skip at exec time, already registered) ─
            AstNodeKind::FnDef { .. } => Ok(Signal::Value(Value::Unit)),

            // ── Program ──────────────────────
            AstNodeKind::Program(items) => {
                for h in items { self.exec(h, frame)?; }
                Ok(Signal::Value(Value::Unit))
            }

            // ── Raw block — refused ──────────
            AstNodeKind::RawBlock(_) => Err(ExecError::RawBlock),
        }
    }

    // ── Built-in + user function dispatch ───

    fn call_fn(&mut self, name: &str, args: Vec<Value>, parent_frame: &mut Frame) -> Result<Value, ExecError> {
        match name {
            "print" => {
                for a in &args { println!("{}", a); }
                return Ok(Value::Unit);
            }
            "assert" => {
                match args.first() {
                    Some(Value::Bool(true)) => return Ok(Value::Unit),
                    Some(Value::Bool(false)) => return Err(ExecError::AssertFailed),
                    _ => return Err(ExecError::TypeError("assert expects bool".into())),
                }
            }
            _ => {}
        }

        let (pnames, body) = self.fns.get(name)
            .cloned()
            .ok_or_else(|| ExecError::UndefinedFn(name.to_string()))?;

        let mut new_frame: Frame = pnames.into_iter()
            .zip(args.into_iter())
            .collect();

        match self.exec(body, &mut new_frame)? {
            Signal::Return(v) | Signal::Value(v) => Ok(v),
            Signal::Break | Signal::Continue => Ok(Value::Unit),
        }
    }
}

// ─────────────────────────────────────────────
//  Binary operator evaluation
// ─────────────────────────────────────────────

fn eval_binop(op: BinOpKind, l: Value, r: Value) -> Result<Value, ExecError> {
    use Value::*;
    match (l, r) {
        (Int(a), Int(b)) => match op {
            BinOpKind::Add => Ok(Int(a + b)),
            BinOpKind::Sub => Ok(Int(a - b)),
            BinOpKind::Mul => Ok(Int(a * b)),
            BinOpKind::Div => { if b == 0 { Err(ExecError::DivByZero) } else { Ok(Int(a / b)) } }
            BinOpKind::Mod => { if b == 0 { Err(ExecError::DivByZero) } else { Ok(Int(a % b)) } }
            BinOpKind::Eq  => Ok(Bool(a == b)),
            BinOpKind::Ne  => Ok(Bool(a != b)),
            BinOpKind::Lt  => Ok(Bool(a < b)),
            BinOpKind::Le  => Ok(Bool(a <= b)),
            BinOpKind::Gt  => Ok(Bool(a > b)),
            BinOpKind::Ge  => Ok(Bool(a >= b)),
            BinOpKind::And | BinOpKind::Or => Err(ExecError::TypeError("&& / || require bool".into())),
        },
        (Float(a), Float(b)) => match op {
            BinOpKind::Add => Ok(Float(a + b)),
            BinOpKind::Sub => Ok(Float(a - b)),
            BinOpKind::Mul => Ok(Float(a * b)),
            BinOpKind::Div => Ok(Float(a / b)),
            BinOpKind::Mod => Ok(Float(a % b)),
            BinOpKind::Eq  => Ok(Bool((a - b).abs() < f64::EPSILON)),
            BinOpKind::Ne  => Ok(Bool((a - b).abs() >= f64::EPSILON)),
            BinOpKind::Lt  => Ok(Bool(a < b)),
            BinOpKind::Le  => Ok(Bool(a <= b)),
            BinOpKind::Gt  => Ok(Bool(a > b)),
            BinOpKind::Ge  => Ok(Bool(a >= b)),
            _ => Err(ExecError::TypeError("unsupported float op".into())),
        },
        (Int(a), Float(b)) => eval_binop(op, Float(a as f64), Float(b)),
        (Float(a), Int(b)) => eval_binop(op, Float(a), Float(b as f64)),
        (Bool(a), Bool(b)) => match op {
            BinOpKind::And => Ok(Bool(a && b)),
            BinOpKind::Or  => Ok(Bool(a || b)),
            BinOpKind::Eq  => Ok(Bool(a == b)),
            BinOpKind::Ne  => Ok(Bool(a != b)),
            _ => Err(ExecError::TypeError("bool only supports &&, ||, ==, !=".into())),
        },
        (Str(a), Str(b)) => match op {
            BinOpKind::Add => Ok(Str(a + &b)),
            BinOpKind::Eq  => Ok(Bool(a == b)),
            BinOpKind::Ne  => Ok(Bool(a != b)),
            _ => Err(ExecError::TypeError("str only supports +, ==, !=".into())),
        },
        (Str(a), Int(b)) => match op {
            BinOpKind::Add => Ok(Str(a + &b.to_string())),
            _ => Err(ExecError::TypeError("str + int only supports concatenation".into())),
        },
        (l, r) => Err(ExecError::TypeError(format!("incompatible operands: {:?} and {:?}", l, r))),
    }
}

// iter_nodes() is defined on AstStore in ae-ast; no orphan impl needed here.
