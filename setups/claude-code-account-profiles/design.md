# Design: per-account Claude Code profiles

- **Date:** 2026-07-25
- **Status:** approved design → pending implementation plan
- **Origin:** token-usage audit ([`../../research/token-usage-audit.md`](../../research/token-usage-audit.md))
  concluded the lean plugin profile was worth applying across accounts. This
  design makes that reproducible and account-aware.

## Goal

A **shared "lean core" definition** that each Claude Code account applies, with
**slight per-account tweaks**, so any account can be brought to a consistent,
lean baseline reproducibly — without hand-editing each account's `settings.json`.

Non-goals (explicit YAGNI cuts):
- No lean⇄full runtime toggle (a single curated set per account is enough).
- No coordination of hooks / env / permissions / model — those legitimately
  differ per account and stay entirely per-account.
- v1 does not modify `bootstrap-sync` (see §7).

## Accounts in scope

`~/.claude-accounts/<account>/` dirs: **personal** (Alancs92), **harrison**
(work), **personal2**. Each holds its own `settings.json`, `hooks`, `plugins`,
`skills`, etc. `claude-acs` switches the active account.

## Scope of a profile

**Plugins + skills only.**
- Plugins: `enabledPlugins` (+ the `extraKnownMarketplaces` they need).
- Skills: a **prune-list** (remove/exclude), not a full declarative set —
  matches the measured finding that skills curation is low-yield.

## Architecture

Three parts, each with one job:

| Part | Role | Lives in |
|---|---|---|
| `profiles.json` | **Data** — source of truth. `core` + per-account `add`/`remove`/`skills_prune`. No logic. | repo |
| `apply_profile.py` | **Logic** — pure resolver + side-effecting applier (stdlib Python 3, no deps). | repo |
| `claude-acs apply-profile <account>` | **Entry** — thin wrapper that locates the repo and invokes the script. | `claude-acs` fn source |

Repo-as-truth; `claude-acs` is only the ergonomic entry point.

## Data model (`profiles.json`)

```jsonc
{
  "core": {
    "plugins": [ "superpowers@claude-plugins-official", "…16 more…" ],
    // ^ EXACTLY the 18 plugin ids currently in
    //   .claude-accounts/personal/settings.json (applied 2026-07-25). Copied
    //   verbatim so the golden hash below holds.
    "marketplaces": {
      "harrison-engineering": { "source": { "source": "github", "repo": "harrison-ai/hai-agentic-dev" } },
      "obsidian-skills":      { "source": { "source": "github", "repo": "kepano/obsidian-skills" } },
      "qmd":                  { "source": { "source": "github", "repo": "tobi/qmd" } }
    },
    "skills_prune": [ "code-review", "skill-creator" ]   // user-level dups of core plugins
  },
  "accounts": {
    "personal":  { "plugins_add": [], "plugins_remove": [],
                   "skills_prune": [ "cloudflare", "cloudflare-email-service", "cloudflare-one",
                                     "cloudflare-one-migrations", "wrangler", "workers-best-practices",
                                     "durable-objects", "sandbox-sdk", "agents-sdk", "web-perf",
                                     "turnstile-spin" ] },
    "harrison":  { "plugins_add": [], "plugins_remove": [], "skills_prune": [] },   // empty = pure core
    "personal2": { "plugins_add": [], "plugins_remove": [], "skills_prune": [] }    // empty = pure core
  }
}
```

**Resolution:** `effective_plugins = dedupe(core.plugins + account.plugins_add) − account.plugins_remove`.
An **empty overlay means the account gets the pure core** — a valid default, not
a placeholder. Whether `harrison` / `personal2` actually *want* deviations is a
discrete implementation task: diff each account's current/intended
`enabledPlugins` against `core` and record only the genuine differences as
`add`/`remove`. Until then they resolve to pure core (safe to apply).

**Config hash (unambiguous):** the resolver emits `enabledPlugins` as an
**object** `{ "<plugin-id>": true, … }` (Claude Code's on-disk shape). The hash
is `SHA-256(json.dumps(enabledPlugins_object, sort_keys=True))` — identical to
how the audit's baseline hashes were computed, so the golden value below is
directly comparable.

## Data flow — `apply-profile <account>`

1. Locate repo + parse/validate manifest (core.plugins non-empty; account exists).
2. **Resolve** effective plugins; warn if `add` duplicates core or `remove`
   targets an absent plugin.
3. Read the account's `settings.json`; **back up** to a timestamped file
   (`settings.json.bak-<ISO8601>`), never overwriting a prior backup.
4. **Merge**: replace `enabledPlugins`; union `marketplaces` into
   `extraKnownMarketplaces`; **leave hooks / env / permissions / model / all
   other keys untouched**.
5. Validate the result parses as JSON; on failure, abort and restore the backup.
6. **Skills reconcile**: `prune = core.skills_prune ∪ account.skills_prune`.
   For each name present in the account's `skills/`:
   - real dir → **move to `.claude-accounts/<account>/.profile-pruned-skills/<name>/`**
     (reversible; never `rm`).
   - symlink (bootstrap) → **v1: skip + report** (see §7); v2: record to an
     exclude file for `bootstrap-sync`.
7. Print a **diff** (plugins ±, skills pruned/skipped) and the new config hash.

**Flags:** `--dry-run` stops before step 3 (simulate only); interactive confirm
before writing unless `--yes`; `--account <name>` required (no implicit "all").

## Error handling & safety

- **Backup-before-write**, timestamped, non-clobbering.
- **JSON validation** before and after; abort + restore on any parse failure.
- **Idempotent** — a second run yields the same hash and prints "already in sync."
- **No destructive skill deletes** — pruned skills are *moved* to a per-account
  trash dir, fully restorable.
- Refuses unknown account or missing account dir. Only ever writes
  `enabledPlugins`, `extraKnownMarketplaces`, and named skills.

## Testing

- **Unit** — the pure resolver (`manifest + account → effective plugins`) with
  fixtures: dedupe, add, remove, remove-absent (warn), add-duplicate (warn).
- **Golden / regression** — `resolve("personal")` MUST hash to
  **`59a9cd9351a982b637b0578a06b29afdb394587df27586cc1fba982738bfbbc5`** — the
  lean profile already applied and measured (−8.7k / −20% startup tax). The
  manifest is correct only if it reproduces reality.
- **Integration** — apply `--dry-run` against a *temp copy* of an account dir;
  assert the settings diff + skill actions.
- **Idempotency** — apply twice; the second run is a no-op.

## Known dependency (v1 scope)

The bootstrap-symlink exclude (step 6, symlink branch) needs `bootstrap-sync` to
honor an exclude file — a change to *that* skill. **v1 scopes skill-prune to
real-dir skills only** (covers the Cloudflare family, the actual target) and
reports+skips symlinked prunes. Bootstrap-exclude is a documented **v2**
integration. Rationale: keeps v1 self-contained, and skills curation is
low-yield per the audit — not worth touching `bootstrap-sync` now.

## Repo placement

```
setups/claude-code-account-profiles/
  README.md            # setup doc (what/why/reproduce) — added at implementation
  design.md            # this spec
  profiles.json        # the manifest
  scripts/
    apply_profile.py   # resolver + applier
    INDEX.md
```
Plus: row in `setups/INDEX.md`, a `CHANGELOG.md` entry, and the `apply-profile|ap`
case added to the `claude-acs` function source (locating that source is the
first implementation task — it is a shell function, not yet found on disk).
