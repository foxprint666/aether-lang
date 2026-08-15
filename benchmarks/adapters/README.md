# Language Adapters

Planned adapter contract:

- `parse`
- `validate`
- `transform`
- `serialize`
- `diagnostics`
- `test_command`

The existing Python LibCST engine and Node Recast engine are the first reference implementations. Future TypeScript, Rust, Go, Java, and C/C++ adapters should plug into this contract without changing benchmark core.
