# Repository Manifests

Repository manifests describe where real-repository benchmark tasks get their code.

Implemented source types:

- `local`: copy selected files from the current worktree into an isolated temp project.
- `git`: clone an external repository into the temp project. This requires `--allow-network-repos`.

External git manifests should pin immutable commits:

```json
{
  "repository_id": "example-project",
  "source": {
    "type": "git",
    "url": "https://github.com/example/project.git",
    "commit": "0123456789abcdef0123456789abcdef01234567"
  },
  "default_timeout_ms": 30000
}
```

Do not use moving branches such as `main` or `master` as benchmark pins.
