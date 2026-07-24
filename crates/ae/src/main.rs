use std::{env, fs, process};

use ae_codegen::Interpreter;
use ae_sema::{DiagSeverity, analyze};
use ae_syntax::parse;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: ae <file.ae>");
        process::exit(1);
    }
    
    let path = &args[1];
    let src = fs::read_to_string(path).unwrap_or_else(|e| {
        eprintln!("error: cannot read `{}`: {}", path, e);
        process::exit(1);
    });

    // 1. Parse
    let result = parse(&src, path);
    for e in &result.lex_errors {
        eprintln!("lex error [{}]: {}", path, e);
    }
    for e in &result.errors {
        eprintln!("parse error [{}]: {}", path, e);
    }
    if !result.ok() { process::exit(1); }

    // 2. Semantic analysis
    let sema = analyze(result.root, &result.store, &result.spans);
    let mut had_error = false;
    for d in &sema.diagnostics {
        if matches!(d.severity, DiagSeverity::Error) {
            eprintln!("error: {}", d.message);
            had_error = true;
        }
    }
    if had_error { process::exit(1); }

    // 3. Execute
    let mut interp = Interpreter::new(&result.store, &result.spans, &sema);
    if let Err(e) = interp.run(result.root) {
        eprintln!("runtime error: {}", e);
        process::exit(1);
    }
}
