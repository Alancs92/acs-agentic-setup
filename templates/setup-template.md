<!--
Copy to setups/<slug>/README.md and fill in.
Then add the setup to setups/INDEX.md, add a line to CHANGELOG.md, and add
any scripts/config the setup needs inside setups/<slug>/.
-->

# <Setup name>

- **Created:** YYYY-MM-DD
- **Status:** active | archived
- **Agents involved:** <e.g. Claude Code, Ollama>
- **Environment:** local | cloud | hybrid
- **Topology:** single agent | multi-agent

## What this is

Plain description of the setup and what it's for.

## Why

What problem this solves, or what it's an experiment toward.

## Components

- <agent/tool> — <role it plays>

## Reproducing it

Step-by-step, or point at `scripts/` in this same directory.

```bash
# example
./scripts/bootstrap.sh
```

## Config

Point at config files in this directory. Don't inline secrets — reference
where they come from (env var, secret manager) instead of the value.

## Notes / known issues

Anything that broke, surprised you, or is still rough.
