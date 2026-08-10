//! ae-codegen — Code Generation for Aether
//!
//! Phase 0 implements a **tree-walking interpreter** that directly executes
//! the typed AST. This gives us a fully working `ae run` immediately, while
//! the Cranelift JIT backend is scaffolded for Phase 1.
//!
//! # Array semantics
//! Arrays use `Rc<RefCell<Vec<Value>>>` for shared-reference semantics.
//! This means `push(arr, val)` mutates in-place — all bindings holding a
//! reference to the same array see the change. This matches standard
//! imperative semantics and will transition to heap-pointer passing when
//! the Cranelift JIT is enabled in Phase 1.
//!
//! # Cranelift SSA Note
//! When the JIT is active in Phase 1, all mutable variables will use
//! `cranelift_frontend::Variable` + `builder.declare_var()` / `def_var()` /
//! `use_var()`. Cranelift constructs SSA form (with phi nodes) automatically
//! from these calls — no manual dominance-frontier analysis required.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use ae_ast::{AstNodeKind, AstStore, BinOpKind, ContentHash, SpanTable, UnaryOpKind};
use ae_sema::SemaResult;

pub mod jit;
pub mod aot;
pub mod ffi;

// ─────────────────────────────────────────────
//  Runtime value
// ─────────────────────────────────────────────

/// Shared-reference array wrapper.
pub type AeArray = Rc<RefCell<Vec<Value>>>;

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Int(i64),
    Float(f64),
    Bool(bool),
    Str(String),
    /// Shared-reference array: Rc<RefCell<Vec<Value>>>
    Array(AeArray),
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
            Value::Array(a) => {
                let elems: Vec<String> = a.borrow().iter().map(|v| format!("{}", v)).collect();
                write!(f, "[{}]", elems.join(", "))
            }
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
    #[error("assertion failed: {0}")]
    AssertFailed(String),
    #[error("index out of bounds: index {0} on array of length {1}")]
    IndexOutOfBounds(i64, usize),
    #[error("missing AST node for hash")]
    MissingNode,
    #[error("raw blocks cannot be executed in Phase 0 (unsafe escape hatch)")]
    RawBlock,
    #[error("explicit panic: {0}")]
    Panic(String),
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
    /// Function definitions: name → (param names, body hash)
    fns:      HashMap<String, (Vec<String>, ContentHash)>,
    /// Global env (shared across all top-level statements)
    globals:  HashMap<String, Value>,
    /// Flag to track if we're executing top-level statements
    in_global_scope: bool,
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
            in_global_scope: true,
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

    // ── Variable assignment with proper scoping ──
    //
    // Lookup order: current frame first, then globals.
    // If not found anywhere → error (no silent global creation).

    fn assign(&mut self, name: &str, val: Value, frame: &mut Frame) -> Result<(), ExecError> {
        if frame.contains_key(name) {
            frame.insert(name.to_string(), val);
        } else if self.globals.contains_key(name) {
            self.globals.insert(name.to_string(), val);
        } else {
            return Err(ExecError::UndefinedVar(format!(
                "`{}` — did you forget `let {} = ...`?", name, name
            )));
        }
        Ok(())
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
                if self.in_global_scope {
                    self.globals.insert(name, v);
                } else {
                    frame.insert(name, v);
                }
                Ok(Signal::Value(Value::Unit))
            }

            // ── Assignment ───────────────────
            AstNodeKind::Assign { name, value } => {
                let v = self.exec(value, frame)?.into_value();
                self.assign(&name, v, frame)?;
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

            // ── Array literal ─────────────────
            AstNodeKind::ArrayLit(elems) => {
                let mut vals = Vec::new();
                for h in elems {
                    vals.push(self.exec(h, frame)?.into_value());
                }
                Ok(Signal::Value(Value::Array(Rc::new(RefCell::new(vals)))))
            }

            // ── Index ────────────────────────
            AstNodeKind::Index { array, index } => {
                let arr_val = self.exec(array, frame)?.into_value();
                let idx_val = self.exec(index, frame)?.into_value();
                match (arr_val, idx_val) {
                    (Value::Array(arr), Value::Int(i)) => {
                        let borrowed = arr.borrow();
                        let len = borrowed.len();
                        let idx = if i < 0 { len as i64 + i } else { i } as usize;
                        borrowed.get(idx)
                            .cloned()
                            .ok_or(ExecError::IndexOutOfBounds(i, len))
                            .map(Signal::Value)
                    }
                    (Value::Str(s), Value::Int(i)) => {
                        let chars: Vec<char> = s.chars().collect();
                        let len = chars.len();
                        let idx = if i < 0 { len as i64 + i } else { i } as usize;
                        chars.get(idx)
                            .map(|c| Signal::Value(Value::Str(c.to_string())))
                            .ok_or(ExecError::IndexOutOfBounds(i, len))
                    }
                    _ => Err(ExecError::TypeError("index requires array[i64]".into())),
                }
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
                let iter_kind = self.node(iter)?.clone();
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
                    if self.in_global_scope {
                        self.globals.insert(var.clone(), Value::Int(i));
                    } else {
                        frame.insert(var.clone(), Value::Int(i));
                    }
                    match self.exec(body, frame)? {
                        Signal::Break    => break,
                        Signal::Continue => continue,
                        Signal::Return(v) => return Ok(Signal::Return(v)),
                        _ => {}
                    }
                }
                if self.in_global_scope {
                    self.globals.remove(&var);
                } else {
                    frame.remove(&var);
                }
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
            // ── I/O ──────────────────────────────────────────────────────
            "print" => {
                for a in &args { println!("{}", a); }
                return Ok(Value::Unit);
            }
            "eprint" => {
                for a in &args { eprintln!("{}", a); }
                return Ok(Value::Unit);
            }

            // ── Assertions / control ─────────────────────────────────────
            "assert" => {
                match args.first() {
                    Some(Value::Bool(true)) => return Ok(Value::Unit),
                    Some(Value::Bool(false)) => {
                        let msg = args.get(1).map(|v| v.to_string()).unwrap_or_else(|| "assertion failed".into());
                        return Err(ExecError::AssertFailed(msg));
                    }
                    _ => return Err(ExecError::TypeError("assert expects bool".into())),
                }
            }
            "panic" => {
                let msg = args.first().map(|v| v.to_string()).unwrap_or_else(|| "explicit panic".into());
                return Err(ExecError::Panic(msg));
            }

            // ── Type conversion ──────────────────────────────────────────
            "to_str" => {
                let v = args.into_iter().next().ok_or_else(|| ExecError::TypeError("to_str needs 1 arg".into()))?;
                return Ok(Value::Str(v.to_string()));
            }
            "to_int" => {
                return match args.first() {
                    Some(Value::Float(f)) => Ok(Value::Int(*f as i64)),
                    Some(Value::Str(s))   => s.trim().parse::<i64>().map(Value::Int)
                        .map_err(|_| ExecError::TypeError(format!("cannot parse `{}` as i64", s))),
                    Some(Value::Int(n))   => Ok(Value::Int(*n)),
                    _ => Err(ExecError::TypeError("to_int requires float, str, or int".into())),
                };
            }
            "to_float" => {
                return match args.first() {
                    Some(Value::Int(n))  => Ok(Value::Float(*n as f64)),
                    Some(Value::Str(s))  => s.trim().parse::<f64>().map(Value::Float)
                        .map_err(|_| ExecError::TypeError(format!("cannot parse `{}` as f64", s))),
                    Some(Value::Float(f)) => Ok(Value::Float(*f)),
                    _ => Err(ExecError::TypeError("to_float requires int, str, or float".into())),
                };
            }

            // ── String operations ────────────────────────────────────────
            "format" => {
                return builtin_format(&args);
            }
            "str_len" => {
                return match args.first() {
                    Some(Value::Str(s)) => Ok(Value::Int(s.chars().count() as i64)),
                    _ => Err(ExecError::TypeError("str_len requires str".into())),
                };
            }
            "str_contains" => {
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Str(s)), Some(Value::Str(pat))) => Ok(Value::Bool(s.contains(pat.as_str()))),
                    _ => Err(ExecError::TypeError("str_contains(str, str) expected".into())),
                };
            }
            "str_starts_with" => {
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Str(s)), Some(Value::Str(pat))) => Ok(Value::Bool(s.starts_with(pat.as_str()))),
                    _ => Err(ExecError::TypeError("str_starts_with(str, str) expected".into())),
                };
            }

            // ── Math ─────────────────────────────────────────────────────
            "sqrt" => {
                return match args.first() {
                    Some(Value::Float(f)) => Ok(Value::Float(f.sqrt())),
                    Some(Value::Int(n))   => Ok(Value::Float((*n as f64).sqrt())),
                    _ => Err(ExecError::TypeError("sqrt requires numeric".into())),
                };
            }
            "abs" => {
                return match args.first() {
                    Some(Value::Int(n))   => Ok(Value::Int(n.abs())),
                    Some(Value::Float(f)) => Ok(Value::Float(f.abs())),
                    _ => Err(ExecError::TypeError("abs requires numeric".into())),
                };
            }
            "min" => {
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Int(a)), Some(Value::Int(b)))     => Ok(Value::Int(*a.min(b))),
                    (Some(Value::Float(a)), Some(Value::Float(b))) => Ok(Value::Float(a.min(*b))),
                    _ => Err(ExecError::TypeError("min(numeric, numeric) expected".into())),
                };
            }
            "max" => {
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Int(a)), Some(Value::Int(b)))     => Ok(Value::Int(*a.max(b))),
                    (Some(Value::Float(a)), Some(Value::Float(b))) => Ok(Value::Float(a.max(*b))),
                    _ => Err(ExecError::TypeError("max(numeric, numeric) expected".into())),
                };
            }
            "pow" => {
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Int(base)), Some(Value::Int(exp))) => {
                        Ok(Value::Int(base.pow(*exp as u32)))
                    }
                    (Some(Value::Float(base)), Some(Value::Float(exp))) => {
                        Ok(Value::Float(base.powf(*exp)))
                    }
                    _ => Err(ExecError::TypeError("pow(numeric, numeric) expected".into())),
                };
            }

            // ── Array operations ─────────────────────────────────────────
            "len" => {
                return match args.first() {
                    Some(Value::Array(a)) => Ok(Value::Int(a.borrow().len() as i64)),
                    Some(Value::Str(s))   => Ok(Value::Int(s.chars().count() as i64)),
                    _ => Err(ExecError::TypeError("len requires array or str".into())),
                };
            }
            "push" => {
                return match args.get(0) {
                    Some(Value::Array(a)) => {
                        let arr = Rc::clone(a); // clone Rc before consuming args
                        let val = args.into_iter().nth(1).ok_or_else(|| ExecError::TypeError("push(arr, val) needs 2 args".into()))?;
                        arr.borrow_mut().push(val);
                        Ok(Value::Unit)
                    }
                    _ => Err(ExecError::TypeError("push(array, value) expected".into())),
                };
            }
            "pop" => {
                return match args.first() {
                    Some(Value::Array(a)) => {
                        a.borrow_mut().pop().map(Ok).unwrap_or(Ok(Value::Unit))
                    }
                    _ => Err(ExecError::TypeError("pop(array) expected".into())),
                };
            }
            "get" => {
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Array(a)), Some(Value::Int(i))) => {
                        let borrowed = a.borrow();
                        let len = borrowed.len();
                        let idx = *i as usize;
                        borrowed.get(idx).cloned()
                            .ok_or(ExecError::IndexOutOfBounds(*i, len))
                    }
                    _ => Err(ExecError::TypeError("get(array, i64) expected".into())),
                };
            }
            "set" => {
                return match (args.get(0), args.get(1), args.get(2)) {
                    (Some(Value::Array(a)), Some(Value::Int(i)), Some(val)) => {
                        let idx = *i as usize;
                        let val = val.clone();
                        let mut borrow = a.borrow_mut();
                        let len = borrow.len();
                        if idx >= len {
                            return Err(ExecError::IndexOutOfBounds(*i, len));
                        }
                        borrow[idx] = val;
                        Ok(Value::Unit)
                    }
                    _ => Err(ExecError::TypeError("set(array, i64, value) expected".into())),
                };
            }
            "new_array" => {
                // new_array(size, fill_value) — creates a pre-filled array
                return match (args.get(0), args.get(1)) {
                    (Some(Value::Int(n)), Some(fill)) => {
                        let arr = vec![fill.clone(); *n as usize];
                        Ok(Value::Array(Rc::new(RefCell::new(arr))))
                    }
                    _ => Err(ExecError::TypeError("new_array(i64, value) expected".into())),
                };
            }
            "array_copy" => {
                // Deep copy an array (breaks shared reference)
                return match args.first() {
                    Some(Value::Array(a)) => {
                        let copy = a.borrow().clone();
                        Ok(Value::Array(Rc::new(RefCell::new(copy))))
                    }
                    _ => Err(ExecError::TypeError("array_copy(array) expected".into())),
                };
            }

            _ => {}
        }

        // User-defined function
        let (pnames, body) = self.fns.get(name)
            .cloned()
            .ok_or_else(|| ExecError::UndefinedFn(name.to_string()))?;

        let mut new_frame: Frame = pnames.into_iter()
            .zip(args.into_iter())
            .collect();

        let prev_global_scope = self.in_global_scope;
        self.in_global_scope = false;

        let res = match self.exec(body, &mut new_frame)? {
            Signal::Return(v) | Signal::Value(v) => v,
            Signal::Break | Signal::Continue => Value::Unit,
        };

        self.in_global_scope = prev_global_scope;
        Ok(res)
    }
}

// ─────────────────────────────────────────────
//  format() builtin
// ─────────────────────────────────────────────

fn builtin_format(args: &[Value]) -> Result<Value, ExecError> {
    if args.is_empty() {
        return Err(ExecError::TypeError("format() requires at least a template string".into()));
    }
    let template = match &args[0] {
        Value::Str(s) => s.clone(),
        _ => return Err(ExecError::TypeError("First argument to format() must be a string".into())),
    };

    let mut result = String::new();
    let mut arg_idx = 1;
    let mut chars = template.chars().peekable();

    while let Some(c) = chars.next() {
        if c == '{' && chars.peek() == Some(&'}') {
            chars.next(); // consume '}'
            if arg_idx < args.len() {
                result.push_str(&args[arg_idx].to_string());
                arg_idx += 1;
            } else {
                return Err(ExecError::TypeError("Not enough arguments for format template".into()));
            }
        } else {
            result.push(c);
        }
    }
    Ok(Value::Str(result))
}

// ─────────────────────────────────────────────
//  Binary operator evaluation
// ─────────────────────────────────────────────

fn eval_binop(op: BinOpKind, l: Value, r: Value) -> Result<Value, ExecError> {
    use Value::*;
    match (l, r) {
        (Int(a), Int(b)) => match op {
            BinOpKind::Add => Ok(Int(a.wrapping_add(b))),
            BinOpKind::Sub => Ok(Int(a.wrapping_sub(b))),
            BinOpKind::Mul => Ok(Int(a.wrapping_mul(b))),
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
        (Str(a), Float(b)) => match op {
            BinOpKind::Add => Ok(Str(a + &b.to_string())),
            _ => Err(ExecError::TypeError("str + float only supports concatenation".into())),
        },
        (l, r) => Err(ExecError::TypeError(format!("incompatible operands: {:?} and {:?}", l, r))),
    }
}
