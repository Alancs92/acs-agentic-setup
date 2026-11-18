# next-steps-plus

A Claude Code mod. After each answered turn it suggests up to three next prompts
in the band above the input. It is next-steps with three additions:

- **Fresh context for the suggester.** Besides the conversation (which the fork
  already shares, with CLAUDE.md and memory), it is handed:
  - the repo's git state: branch, ahead/behind, changed files, last commits;
  - the branch's pull request: state, review, merge state, checks by outcome
    (via `gh`, read-only, cached two minutes per branch);
  - your notes files (their opening part) and notes folders (Markdown titles
    only), from the paths you configure.
- **The full prompt on demand.** Hover a suggestion, or move the focus onto it
  (ctrl+x tab, then arrows), to see the whole prompt under its label.
- **Longer prompts:** up to 1000 characters.

1, 2 or 3 put a suggestion in the prompt box as an editable draft; 0 dismisses.
Nothing is ever sent for you. Unlike next-steps, it never puts a suggestion in
the empty prompt box as dim ghost text: what the suggester reads (PR titles,
check names, files) can steer what it writes, so a suggestion reaches the box
only when you press its number.

## Options

| Option | Default | What it does |
|---|---|---|
| `minAnswerChars` | 80 | Skip suggestions after shorter answers. |
| `suggestSkills` | true | Let suggestions be one of your skills or slash commands. |
| `gitContext` | true | Include the git state. |
| `prContext` | true | Include the branch's PR and checks (`gh`). |
| `notesFiles` | empty | Comma-separated files to include, e.g. `~/notes/{repo}/{branch_dashed}.md`. |
| `notesIndexDirs` | empty | Comma-separated folders whose `.md` files are listed by title. |

Placeholders: `{repo}` (from the `origin` remote), `{branch}`, `{branch_dashed}`
(slashes as dashes), `{home}`; a leading `~` is your home. A repo or branch name
that could step outside the folder is refused, and missing files are skipped.

Set them with `claude plugin configure next-steps-plus@<marketplace> --values-stdin`.

## What it reads and sends

Local `git` (repo hooks off), `gh pr view` for the current branch, and the files
you configure. Everything is capped (about 8 KB in all) and goes to the model in
a block marked as data, not instructions. Model output is stripped of escape
sequences and hidden characters before it is drawn or filled in.

## Origin

Adapted from `next-steps` 1.0.0 in the `claude-plugins-community` marketplace
(MIT licence; its manifest names its author). The text cleaning, skill listing,
reply parsing and band layout follow it; the context gathering, path templates
and prompt preview are new.
