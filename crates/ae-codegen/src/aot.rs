use cranelift_codegen::Context;
use cranelift_codegen::ir::{AbiParam, types, InstBuilder, Value as ClValue};
use cranelift_frontend::{FunctionBuilder, FunctionBuilderContext, Variable};
use cranelift_module::{DataDescription, Linkage, Module, DataId, FuncId};
use cranelift_object::{ObjectBuilder, ObjectModule};
use cranelift_native;
use ae_ast::{AstStore, ContentHash, AstNodeKind, BinOpKind};
use ae_sema::{SemaResult, AetherType};
use std::collections::HashMap;
use std::fs::File;
use std::io::Write;
use std::path::Path;

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
        builder.declare_var(var, types::I64);
        var
    }
}

pub struct AotEngine {
    module: ObjectModule,
    ctx: Context,
    builder_context: FunctionBuilderContext,
    func_ids: HashMap<String, FuncId>,
}

impl AotEngine {
    pub fn new() -> Result<Self, String> {
        let isa = cranelift_native::builder()
            .map_err(|e| e.to_string())?
            .finish(cranelift_codegen::settings::Flags::new(cranelift_codegen::settings::builder()))
            .map_err(|e| e.to_string())?;

        let builder = ObjectBuilder::new(
            isa,
            "aether_app",
            cranelift_module::default_libcall_names(),
        ).map_err(|e| e.to_string())?;

        let module = ObjectModule::new(builder);

        Ok(Self {
            module,
            ctx: Context::new(),
            builder_context: FunctionBuilderContext::new(),
            func_ids: HashMap::new(),
        })
    }

    pub fn emit_object_file(
        mut self,
        root_hash: ContentHash,
        store: &AstStore,
        sema: &SemaResult,
        output_path: &Path,
    ) -> Result<(), String> {
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
                    .map_err(|e| e.to_string())?;

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

                self.module.define_function(func_id, &mut self.ctx).map_err(|e| e.to_string())?;
                self.module.clear_context(&mut self.ctx);
            }
        }

        // Entry main function
        if let Some(&id) = self.func_ids.get("main") {
            let _ = id;
        } else {
            let mut sig = self.module.make_signature();
            sig.returns.push(AbiParam::new(types::I64));

            let func_id = self.module.declare_function("main", Linkage::Export, &sig).map_err(|e| e.to_string())?;
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

            self.module.define_function(func_id, &mut self.ctx).map_err(|e| e.to_string())?;
            self.module.clear_context(&mut self.ctx);
        };

        let product = self.module.finish();
        let obj_bytes = product.emit().map_err(|e| e.to_string())?;

        let mut file = File::create(output_path).map_err(|e| e.to_string())?;
        file.write_all(&obj_bytes).map_err(|e| e.to_string())?;

        Ok(())
    }

    fn translate_node(
        module: &mut ObjectModule,
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
                        // Fallback: treat as i64 and warn — never panic
                        eprintln!("[ae-codegen/aot] WARNING: print({:?}) unsupported, treating as i64", arg_type);
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
                    .unwrap_or_else(|| panic!("Undefined function in AOT: {}", func));

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
                let left = Self::translate_node(module, func_ids, global_str_id, *lhs, store, sema, builder, lctx);
                let right = Self::translate_node(module, func_ids, global_str_id, *rhs, store, sema, builder, lctx);

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
                        eprintln!("[ae-codegen/aot] WARNING: binary op {:?} not implemented, emitting 0", op);
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

                builder.ins().brif(cond_val, then_blk, &[], else_blk, &[]);

                builder.switch_to_block(then_blk);
                let then_val = Self::translate_node(module, func_ids, global_str_id, *then_block, store, sema, builder, lctx);
                builder.ins().jump(merge_blk, &[then_val]);

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

                builder.ins().jump(header_block, &[]);

                builder.switch_to_block(header_block);
                let cond_val = Self::translate_node(module, func_ids, global_str_id, *cond, store, sema, builder, lctx);
                builder.ins().brif(cond_val, body_block, &[], exit_block, &[]);

                builder.switch_to_block(body_block);
                Self::translate_node(module, func_ids, global_str_id, *body, store, sema, builder, lctx);
                builder.ins().jump(header_block, &[]);

                builder.seal_block(header_block);
                builder.seal_block(body_block);
                builder.seal_block(exit_block);

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
                builder.ins().iconst(types::I64, 0)
            }
            AstNodeKind::ArrayLit(_) | AstNodeKind::Index { .. } => {
                eprintln!("[ae-codegen/aot] WARNING: array operations not yet implemented in AOT, emitting 0");
                builder.ins().iconst(types::I64, 0)
            }
            _ => {
                eprintln!("[ae-codegen/aot] WARNING: unhandled AST node kind, emitting 0");
                builder.ins().iconst(types::I64, 0)
            }
        }
    }
}

fn compile_string_literal(module: &mut ObjectModule, text: &str, id_counter: u32) -> Result<DataId, String> {
    let name = format!("str_lit_{}", id_counter);
    
    let data_id = module
        .declare_data(&name, Linkage::Local, false, false)
        .map_err(|e| e.to_string())?;

    let mut data_ctx = DataDescription::new();
    let mut bytes = text.as_bytes().to_vec();
    bytes.push(0);
    data_ctx.define(bytes.into_boxed_slice());

    module
        .define_data(data_id, &data_ctx)
        .map_err(|e| e.to_string())?;

    Ok(data_id)
}
