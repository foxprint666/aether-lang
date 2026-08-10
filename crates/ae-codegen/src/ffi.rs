/// ae-codegen FFI — Tier-1 Cranelift JIT Sandbox C-ABI Guard Ring
///
/// Exposes two symbols over the stable C ABI:
///   ae_sandbox_execute(src_ptr, src_len) → *mut c_char   (JSON ExecutionResult — caller must free)
///   ae_sandbox_free(ptr)                 → void           (reclaim string from above)
///
/// Safety contract
/// ───────────────
/// • All panics are caught by `std::panic::catch_unwind` — no Rust panic can
///   ever unwind past an FFI boundary (which would be UB).
/// • The returned pointer is from `CString::into_raw()`.  Ownership transfers
///   to the caller; they MUST call `ae_sandbox_free` exactly once.
/// • Passing a NULL `src_ptr` returns a JSON error payload (does not panic).
/// • `#[no_mangle]` + `extern "C"` keeps the ABI stable across compilers.
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::panic;
use std::time::Instant;

use ae_sema::analyze;

use crate::Interpreter;

// ─────────────────────────────────────────────────────────────────────────────
// Public FFI symbols
// ─────────────────────────────────────────────────────────────────────────────

/// Execute an Aether source snippet.  Returns a heap-allocated JSON string.
///
/// Caller **must** pass the returned pointer to `ae_sandbox_free`.
#[no_mangle]
pub extern "C" fn ae_sandbox_execute(src_ptr: *const c_char, _src_len: usize) -> *mut c_char {
    let result = panic::catch_unwind(|| {
        if src_ptr.is_null() {
            return make_error_json("null source pointer", 0.0);
        }

        let source = unsafe {
            match CStr::from_ptr(src_ptr).to_str() {
                Ok(s) => s.to_owned(),
                Err(_) => return make_error_json("source is not valid UTF-8", 0.0),
            }
        };

        let t0 = Instant::now();
        execute_aether_source(&source, t0)
    });

    let json = match result {
        Ok(s) => s,
        Err(_) => make_error_json("internal panic caught by FFI guard ring", 0.0),
    };

    // Leak into raw pointer; caller must call ae_sandbox_free.
    match CString::new(json) {
        Ok(c) => c.into_raw(),
        Err(_) => CString::new(r#"{"success":false,"error":"cstring error"}"#)
            .unwrap()
            .into_raw(),
    }
}

/// Free a string previously returned by `ae_sandbox_execute`.
/// Passing NULL is a safe no-op.
#[no_mangle]
pub extern "C" fn ae_sandbox_free(ptr: *mut c_char) {
    if ptr.is_null() {
        return;
    }
    unsafe {
        drop(CString::from_raw(ptr));
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Private helpers
// ─────────────────────────────────────────────────────────────────────────────

fn execute_aether_source(source: &str, t0: Instant) -> String {
    // Parse — ae_syntax::parse(src, file_name) returns ParseResult
    let pr = ae_syntax::parse(source, "<ffi>");
    if !pr.ok() {
        let msgs: Vec<String> = pr
            .errors
            .iter()
            .map(|e| e.to_string())
            .chain(pr.lex_errors.iter().map(|e| e.to_string()))
            .collect();
        let elapsed = t0.elapsed().as_secs_f64() * 1000.0;
        return make_error_json(&msgs.join("; "), elapsed);
    }

    // Semantic analysis — analyze(root, store, spans)
    let sema = analyze(pr.root, &pr.store, &pr.spans);
    if !sema.diagnostics.is_empty() {
        let msgs: Vec<String> = sema.diagnostics.iter().map(|d| d.message.clone()).collect();
        let elapsed = t0.elapsed().as_secs_f64() * 1000.0;
        return make_error_json(&msgs.join("; "), elapsed);
    }

    // Tree-walking interpreter (T1 fast path)
    let mut interp = Interpreter::new(&pr.store, &pr.spans, &sema);
    let exec_result = interp.run(pr.root);
    let elapsed = t0.elapsed().as_secs_f64() * 1000.0;

    match exec_result {
        Ok(()) => make_success_json("", elapsed),
        Err(e) => make_error_json(&e.to_string(), elapsed),
    }
}

fn make_success_json(stdout: &str, elapsed_ms: f64) -> String {
    let stdout_json = serde_json::to_string(stdout).unwrap_or_else(|_| "\"\"".to_string());
    format!(
        r#"{{"success":true,"stdout":{stdout_json},"stderr":"","elapsed_ms":{elapsed_ms:.3},"tier":"t1_cranelift"}}"#
    )
}

fn make_error_json(error: &str, elapsed_ms: f64) -> String {
    let err_json = serde_json::to_string(error).unwrap_or_else(|_| "\"\"".to_string());
    format!(
        r#"{{"success":false,"stdout":"","stderr":{err_json},"error":{err_json},"elapsed_ms":{elapsed_ms:.3},"tier":"t1_cranelift"}}"#
    )
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;

    fn call_ffi(src: &str) -> String {
        let c_src = CString::new(src).unwrap();
        let ptr = ae_sandbox_execute(c_src.as_ptr(), src.len());
        assert!(!ptr.is_null());
        let result = unsafe { CStr::from_ptr(ptr).to_str().unwrap().to_owned() };
        ae_sandbox_free(ptr);
        result
    }

    #[test]
    fn ffi_null_pointer_returns_error() {
        let ptr = ae_sandbox_execute(std::ptr::null(), 0);
        let s = unsafe { CStr::from_ptr(ptr).to_str().unwrap().to_owned() };
        ae_sandbox_free(ptr);
        assert!(s.contains("\"success\":false"));
        assert!(s.contains("null source pointer"));
    }

    #[test]
    fn ffi_free_null_is_noop() {
        ae_sandbox_free(std::ptr::null_mut()); // must not panic/crash
    }

    #[test]
    fn ffi_valid_source_returns_success() {
        let result = call_ffi("let x = 1 + 2;");
        assert!(result.contains("\"success\":true"), "Got: {result}");
        assert!(result.contains("t1_cranelift"));
    }

    #[test]
    fn ffi_parse_error_returns_failure() {
        let result = call_ffi("fn (((broken");
        assert!(result.contains("\"success\":false"), "Got: {result}");
    }
}
