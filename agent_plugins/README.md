# Aether Agent Plugin Package

This directory packages Aether for agent hosts that understand Codex-style local
plugins.

```text
agent_plugins/
  marketplace.json
  plugins/
    aether-agent/
      .codex-plugin/plugin.json
      skills/aether/SKILL.md
```

The plugin does not bundle a model. It teaches an agent to use the installed
Aether runtime:

```bash
pip install aether-lang-runtime
aether validate patch.json
aether apply patch.json
aether rollback <snapshot-id>
```

Use the generic skill pack in `agent_skills/aether/` for non-Codex agent
wrappers.
