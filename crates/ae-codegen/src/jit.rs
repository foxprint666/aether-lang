use cranelift_codegen::Context;
use cranelift_codegen::ir::{AbiParam, types, InstBuilder, Value as ClValue};
use cranelift_frontend::{FunctionBuilder, FunctionBuilderContext, Variable};
use cranelift_jit::{JITBuilder, JITModule};
use cranelift_module::{DataDescription, Linkage, Module, DataId, FuncId};
use ae_ast::{AstStore, ContentHash, AstNodeKind, BinOpKind};
use ae_sema::{SemaResult, AetherType};
use std::collections::HashMap;
use std::ffi::CStr;
use std::os::raw::c_char;

pub extern "C" fn aether_print_i64(value: i64) {
    println!("{}", value);
}

pub extern "C" fn aether_print_str(ptr: *const c_char) {
    unsafe {
        if !ptr.is_null() {
            if let Ok(c_str) = CStr::from_ptr(ptr).to_str() {
                println!("{}", c_str);
            }
        }
    }
}


struct LoweringContext {
    variables: HashMap<String, Variable>,
    next_var_id: u32,
}

impl LoweringContext {
    fn new() -> Self {
        Self {
            variables: HashMap::new(),
            next_var_id: 0,
        }
    }

    fn declare_variable(&mut self, name: &str, builder: &mut FunctionBuilder) -> Variable {
        let var = Variable::from_u32(self.next_var_id);
        self.next_var_id += 1;
        self.variables.insert(name.to_string(), var);
        
        // Assume all local variables are i64 for Phase 4 native lowering
        builder.declare_var(var, types::I64);
        var
    }
}


pub struct JitEngine {
    module: JITModule,
    ctx: Context,
    builder_context: FunctionBuilderContext,
    func_ids: HashMap<String, FuncId>,
}

impl JitEngine {
    pub fn new() -> Self {
        let mut builder = JITBuilder::new(cranelift_module::default_libcall_names()).unwrap();
        builder.symbol("aether_print_i64", aether_print_i64 as *const u8);
        builder.symbol("aether_print_str", aether_print_str as *const u8);
        let module = JITModule::new(builder);
        Self {
            module,
            ctx: Context::new(),
            builder_context: FunctionBuilderContext::new(),
            func_ids: HashMap::new(),
        }
    }

    /// Compile a program with multiple functions down to raw executable machine code
    pub fn compile_function(
        &mut self,
        root_hash: ContentHash,
        store: &AstStore,
        sema: &SemaResult,
    ) -> *const u8 {
        // Pass 1: Declare all functions in the AST store
        for node in store.iter_nodes() {
            if let AstNodeKind::FnDef { name, params, .. } = &node.kind {
                let mut sig = self.module.make_signature();
                for _ in params {
                    sig.params.push(AbiParam::new(types::I64));
                }
                sig.returns.push(AbiParam::new(types::I64));

                let func_id = self.module
                    .declare_function(name, Linkage::Export, &sig)
                    .unwrap();

                self.func_ids.insert(name.clone(), func_id);
            }
        }

        // Pass 2: Define all declared functions
        let mut global_str_id = 0u32;
        for node in store.iter_nodes() {
            if let AstNodeKind::FnDef { name, params, body, .. } = &node.kind {
                let func_id = *self.func_ids.get(name).unwrap();

                let mut sig = self.module.make_signature();
                for _ in params {
                    sig.params.push(AbiParam::new(types::I64));
                }
                sig.returns.push(AbiParam::new(types::I64));
                self.ctx.func.signature = sig;

                let mut builder = FunctionBuilder::new(&mut self.ctx.func, &mut self.builder_context);
                let entry_block = builder.create_block();
                builder.append_block_params_for_function_params(entry_block);
                builder.switch_to_block(entry_block);

                let mut lctx = LoweringContext::new();
                for (i, (param_name, _)) in params.iter().enumerate() {
                    let val = builder.block_params(entry_block)[i];
                    let var = lctx.declare_variable(param_name, &mut builder);
                    builder.def_var(var, val);
                }

                builder.seal_block(entry_block);

                let ret_val = Self::translate_node(
                    &mut self.module,
                    &self.func_ids,
                    &mut global_str_id,
                    *body,
                    store,
                    sema,
                    &mut builder,
                    &mut lctx,
                );

                builder.ins().return_(&[ret_val]);
                builder.finalize();

                self.module.define_function(func_id, &mut self.ctx).unwrap();
                self.module.clear_context(&mut self.ctx);
            }
        }

        // Entry function (main or top-level program script)
        let main_func_id = if let Some(&id) = self.func_ids.get("main") {
            id
        } else {
            let mut sig = self.module.make_signature();
            sig.returns.push(AbiParam::new(types::I64));

            let func_id = self.module.declare_function("jit_func", Linkage::Export, &sig).unwrap();
            self.ctx.func.signature = sig;

            let mut builder = FunctionBuilder::new(&mut self.ctx.func, &mut self.builder_context);
            let entry_block = builder.create_block();
            builder.append_block_params_for_function_params(entry_block);
            builder.switch_to_block(entry_block);
            builder.seal_block(entry_block);

            let mut lctx = LoweringContext::new();
            let result = Self::translate_node(
                &mut self.module,
                &self.func_ids,
                &mut global_str_id,
                root_hash,
                store,
                sema,
                &mut builder,
                &mut lctx,
            );

            builder.ins().return_(&[result]);
            builder.finalize();

            self.module.define_function(func_id, &mut self.ctx).unwrap();
            self.module.clear_context(&mut self.ctx);
            func_id
        };

        self.module.finalize_definitions().unwrap();
        self.module.get_finalized_function(main_func_id)
    }

    /// Recursively lowers AST nodes into Cranelift SSA instructions
    fn translate_node(
        module: &mut JITModule,
        func_ids: &HashMap<String, FuncId>,
        global_str_id: &mut u32,
        hash: ContentHash,
        store: &AstStore,
        sema: &SemaResult,
        builder: &mut FunctionBuilder,
        lctx: &mut LoweringContext
    ) -> ClValue {
        let node = store.get(&hash).expect("AST node not found");
        
        match &node.kind {
            AstNodeKind::IntLit(val) => {
                // Compile literal to an integer constant instruction
                builder.ins().iconst(types::I64, *val)
            }
            AstNodeKind::StrLit(text) => {
                let id = *global_str_id;
                *global_str_id += 1;
                let data_id = compile_string_literal(module, text, id).unwrap();
                let local_data_id = module.declare_data_in_func(data_id, &mut builder.func);
                let ptr_type = module.target_config().pointer_type();
                builder.ins().symbol_value(ptr_type, local_data_id)
            }
            AstNodeKind::Ident(name) => {
                let var = lctx.variables.get(name).expect("Undefined variable");
                builder.use_var(*var)
            }
            AstNodeKind::Let { name, value, .. } => {
                let val = Self::translate_node(module, func_ids, global_str_id, *value, store, sema, builder, lctx);
                let var = lctx.declare_variable(name, builder);
                builder.def_var(var, val);
                val
            }
            AstNodeKind::Assign { name, value } => {
                let val = Self::translate_node(module, func_ids, global_str_id, *value, store, sema, builder, lctx);
                let var = lctx.variables.get(name).expect("Undefined variable");
                builder.def_var(*var, val);
                val
            }
            AstNodeKind::FnDef { .. } => {
                // Function definitions are handled during Pass 2
                builder.ins().iconst(types::I64, 0)
            }
            AstNodeKind::Call { func, args } if func == "print" => {
                let arg_hash = args[0];
                let arg_val = Self::translate_node(module, func_ids, global_str_id, arg_hash, store, sema, builder, lctx);
                let arg_type = sema.types.get(&arg_hash).unwrap_or(&AetherType::I64);

                let ptr_type = module.target_config().pointer_type();

                let (helper_name, sig) = match arg_type {
                    AetherType::I64 => {
                        let mut sig = module.make_signature();
                        sig.params.push(AbiParam::new(types::I64));
                        ("aether_print_i64", sig)
                    }
                    AetherType::Str => {
                        let mut sig = module.make_signature();
                        sig.params.push(AbiParam::new(ptr_type));
                        ("aether_print_str", sig)
                    }
                    _ => {
                        // Fallback: treat as i64 and warn — never panic across FFI
                        eprintln!("[ae-codegen/jit] WARNING: print({:?}) unsupported, treating as i64", arg_type);
                        let mut sig = module.make_signature();
                        sig.params.push(AbiParam::new(types::I64));
                        ("aether_print_i64", sig)
                    }
                };

                let _sig_ref = builder.import_signature(sig.clone());
                let func_id = module
                    .declare_function(helper_name, Linkage::Import, &sig)
                    .unwrap();
                let callee = module.declare_func_in_func(func_id, &mut builder.func);
                
                builder.ins().call(callee, &[arg_val]);
                arg_val
            }
            AstNodeKind::Call { func, args } => {
                let func_id = func_ids.get(func)
                    .unwrap_or_else(|| panic!("Undefined function in JIT: {}", func));

                let callee = module.declare_func_in_func(*func_id, &mut builder.func);

                let mut arg_values = Vec::new();
                for &arg_hash in args {
                    arg_values.push(Self::translate_node(module, func_ids, global_str_id, arg_hash, store, sema, builder, lctx));
                }

                let call_inst = builder.ins().call(callee, &arg_values);
                let results = builder.inst_results(call_inst);
                if !results.is_empty() {
                    results[0]
                } else {
                    builder.ins().iconst(types::I64, 0)
                }
            }
            AstNodeKind::BinOp { op, lhs, rhs } => {
                // Recursively lower the left and right sides
                let left = Self::translate_node(module, func_ids, global_str_id, *lhs, store, sema, builder, lctx);
                let right = Self::translate_node(module, func_ids, global_str_id, *rhs, store, sema, builder, lctx);

                // Map AST binary ops to Cranelift instructions
                match op {
                    BinOpKind::Add => builder.ins().iadd(left, right),
                    BinOpKind::Sub => builder.ins().isub(left, right),
                    BinOpKind::Mul => builder.ins().imul(left, right),
                    BinOpKind::Div => builder.ins().sdiv(left, right),
                    BinOpKind::Eq  => builder.ins().icmp(cranelift_codegen::ir::condcodes::IntCC::Equal, left, right),
                    BinOpKind::Ne  => builder.ins().icmp(cranelift_codegen::ir::condcodes::IntCC::NotEqual, left, right),
                    BinOpKind::Lt  => builder.ins().icmp(cranelift_codegen::ir::condcodes::IntCC::SignedLessThan, left, right),
                    BinOpKind::Le  => builder.ins().icmp(cranelift_codegen::ir::condcodes::IntCC::SignedLessThanOrEqual, left, right),
                    BinOpKind::Gt  => builder.ins().icmp(cranelift_codegen::ir::condcodes::IntCC::SignedGreaterThan, left, right),
                    BinOpKind::Ge  => builder.ins().icmp(cranelift_codegen::ir::condcodes::IntCC::SignedGreaterThanOrEqual, left, right),
                    _ => {
                        eprintln!("[ae-codegen/jit] WARNING: binary op {:?} not implemented, emitting 0", op);
                        builder.ins().iconst(types::I64, 0)
                    }
                }
            }
            AstNodeKind::If { cond, then_block, else_block } => {
                let cond_val = Self::translate_node(module, func_ids, global_str_id, *cond, store, sema, builder, lctx);

                let then_blk = builder.create_block();
                let else_blk = builder.create_block();
                let merge_blk = builder.create_block();

                builder.append_block_param(merge_blk, types::I64);

                // branch to then_blk if cond_val is true (non-zero), else else_blk
                builder.ins().brif(cond_val, then_blk, &[], else_blk, &[]);

                // Then block
                builder.switch_to_block(then_blk);
                let then_val = Self::translate_node(module, func_ids, global_str_id, *then_block, store, sema, builder, lctx);
                builder.ins().jump(merge_blk, &[then_val]);

                // Else block
                builder.switch_to_block(else_blk);
                let else_val = if let Some(eb) = else_block {
                    Self::translate_node(module, func_ids, global_str_id, *eb, store, sema, builder, lctx)
                } else {
                    builder.ins().iconst(types::I64, 0)
                };
                builder.ins().jump(merge_blk, &[else_val]);

                builder.seal_block(then_blk);
                builder.seal_block(else_blk);
                builder.seal_block(merge_blk);

                builder.switch_to_block(merge_blk);
                builder.block_params(merge_blk)[0]
            }
            AstNodeKind::While { cond, body } => {
                let header_block = builder.create_block();
                let body_block = builder.create_block();
                let exit_block = builder.create_block();

                // 1. Jump from current block into header block
                builder.ins().jump(header_block, &[]);

                // 2. Header Block (evaluate condition)
                builder.switch_to_block(header_block);
                let cond_val = Self::translate_node(module, func_ids, global_str_id, *cond, store, sema, builder, lctx);
                builder.ins().brif(cond_val, body_block, &[], exit_block, &[]);

                // 3. Body Block (execute loop body)
                builder.switch_to_block(body_block);
                Self::translate_node(module, func_ids, global_str_id, *body, store, sema, builder, lctx);
                builder.ins().jump(header_block, &[]);

                // 4. Seal blocks
                builder.seal_block(header_block);
                builder.seal_block(body_block);
                builder.seal_block(exit_block);

                // 5. Focus exit block
                builder.switch_to_block(exit_block);
                builder.ins().iconst(types::I64, 0)
            }
            AstNodeKind::Program(items) | AstNodeKind::Block(items) => {
                let mut last_val = builder.ins().iconst(types::I64, 0);
                for &item in items {
                    last_val = Self::translate_node(module, func_ids, global_str_id, item, store, sema, builder, lctx);
                }
                last_val
            }
            AstNodeKind::BoolLit(b) => {
                builder.ins().iconst(types::I64, if *b { 1 } else { 0 })
            }
            AstNodeKind::Return(val) => {
                let ret_val = if let Some(hash) = *val {
                    Self::translate_node(module, func_ids, global_str_id, hash, store, sema, builder, lctx)
                } else {
                    builder.ins().iconst(types::I64, 0)
                };
                builder.ins().return_(&[ret_val]);
                // Cranelift requires a value here even though Return is a terminator;
                // this code is unreachable but keeps the block well-formed.
                builder.ins().iconst(types::I64, 0)
            }
            AstNodeKind::ArrayLit(_) | AstNodeKind::Index { .. } => {
                // Arrays/indexing are not yet lowered to Cranelift IR.
                // Emit 0 and warn — do NOT panic (would crash across FFI boundary).
                eprintln!("[ae-codegen/jit] WARNING: array operations not yet implemented in JIT, emitting 0");
                builder.ins().iconst(types::I64, 0)
            }
            _ => {
                eprintln!("[ae-codegen/jit] WARNING: unhandled AST node kind, emitting 0");
                builder.ins().iconst(types::I64, 0)
            }
        }
    }
}

/// Compiles a string literal into JIT memory and returns its local identifier.
fn compile_string_literal(module: &mut JITModule, text: &str, id_counter: u32) -> Result<DataId, String> {
    let name = format!("str_lit_{}", id_counter);
    
    let data_id = module
        .declare_data(&name, Linkage::Local, false, false)
        .map_err(|e| e.to_string())?;

    let mut data_ctx = DataDescription::new();
    let mut bytes = text.as_bytes().to_vec();
    bytes.push(0); // Null terminator
    data_ctx.define(bytes.into_boxed_slice());

    module
        .define_data(data_id, &data_ctx)
        .map_err(|e| e.to_string())?;

    Ok(data_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ae_ast::AstNode;
    use std::collections::HashMap;

    #[test]
    fn test_jit_basic_math() {
        let mut store = AstStore::new();

        // Build AST for: 10 + 20
        let lhs = store.insert(AstNode::new(AstNodeKind::IntLit(10)));
        let rhs = store.insert(AstNode::new(AstNodeKind::IntLit(20)));
        
        let add = store.insert(AstNode::new(AstNodeKind::BinOp {
            op: BinOpKind::Add,
            lhs,
            rhs,
        }));

        let mut engine = JitEngine::new();
        let empty_sema = SemaResult {
            types: HashMap::new(),
            diagnostics: Vec::new(),
            fn_sigs: HashMap::new(),
        };

        let func_ptr = engine.compile_function(add, &store, &empty_sema);
        
        // Transmute the raw pointer to a callable Rust function
        let callable: fn() -> i64 = unsafe { std::mem::transmute(func_ptr) };
        let result = callable();

        assert_eq!(result, 30);
    }

    #[test]
    fn test_jit_let_and_if() {
        let mut store = AstStore::new();

        // let x = 10;
        // let y = 20;
        // if x < y { 100 } else { 200 }
        
        let val_10 = store.insert(AstNode::new(AstNodeKind::IntLit(10)));
        let val_20 = store.insert(AstNode::new(AstNodeKind::IntLit(20)));
        let val_100 = store.insert(AstNode::new(AstNodeKind::IntLit(100)));
        let val_200 = store.insert(AstNode::new(AstNodeKind::IntLit(200)));

        let let_x = store.insert(AstNode::new(AstNodeKind::Let {
            name: "x".to_string(),
            mutable: true,
            ty: None,
            value: val_10,
        }));

        let let_y = store.insert(AstNode::new(AstNodeKind::Let {
            name: "y".to_string(),
            mutable: false,
            ty: None,
            value: val_20,
        }));

        let ref_x = store.insert(AstNode::new(AstNodeKind::Ident("x".to_string())));
        let ref_y = store.insert(AstNode::new(AstNodeKind::Ident("y".to_string())));

        let cond = store.insert(AstNode::new(AstNodeKind::BinOp {
            op: BinOpKind::Lt,
            lhs: ref_x,
            rhs: ref_y,
        }));

        let if_expr = store.insert(AstNode::new(AstNodeKind::If {
            cond,
            then_block: val_100,
            else_block: Some(val_200),
        }));

        let program = store.insert(AstNode::new(AstNodeKind::Program(vec![let_x, let_y, if_expr])));

        let mut engine = JitEngine::new();
        let empty_sema = SemaResult {
            types: HashMap::new(),
            diagnostics: Vec::new(),
            fn_sigs: HashMap::new(),
        };

        let func_ptr = engine.compile_function(program, &store, &empty_sema);
        let callable: fn() -> i64 = unsafe { std::mem::transmute(func_ptr) };
        let result = callable();

        assert_eq!(result, 100);
    }

    #[test]
    fn test_jit_bool_lit_no_panic() {
        // Regression: BoolLit used to hit the unimplemented!() arm.
        // After the fix it must emit iconst(1) / iconst(0) without panicking.
        let mut store = AstStore::new();

        let true_node  = store.insert(AstNode::new(AstNodeKind::BoolLit(true)));
        let false_node = store.insert(AstNode::new(AstNodeKind::BoolLit(false)));

        let program = store.insert(AstNode::new(
            AstNodeKind::Program(vec![true_node, false_node]),
        ));

        let mut engine = JitEngine::new();
        let empty_sema = SemaResult {
            types: HashMap::new(),
            diagnostics: Vec::new(),
            fn_sigs: HashMap::new(),
        };

        // Must not panic — result is the last expression value (false → 0)
        let func_ptr = engine.compile_function(program, &store, &empty_sema);
        let callable: fn() -> i64 = unsafe { std::mem::transmute(func_ptr) };
        let result = callable();
        // BoolLit(false) is last → 0; BoolLit(true) → 1
        assert!(result == 0 || result == 1, "unexpected BoolLit result: {}", result);
    }

    #[test]
    fn test_jit_array_lit_no_panic() {
        // Regression: ArrayLit hit the unimplemented!() arm before A1 fix.
        // After the fix it must emit iconst(0) with a warning (no panic).
        let mut store = AstStore::new();

        let elem = store.insert(AstNode::new(AstNodeKind::IntLit(99)));
        let arr  = store.insert(AstNode::new(AstNodeKind::ArrayLit(vec![elem])));
        let prog = store.insert(AstNode::new(AstNodeKind::Program(vec![arr])));

        let mut engine = JitEngine::new();
        let empty_sema = SemaResult {
            types: HashMap::new(),
            diagnostics: Vec::new(),
            fn_sigs: HashMap::new(),
        };

        // Must not panic — emits iconst(0) as documented fallback
        let func_ptr = engine.compile_function(prog, &store, &empty_sema);
        let callable: fn() -> i64 = unsafe { std::mem::transmute(func_ptr) };
        let _result = callable(); // 0 — just verify no panic
    }
}
