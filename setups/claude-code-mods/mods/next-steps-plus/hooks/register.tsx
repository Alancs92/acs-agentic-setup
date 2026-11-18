// next-steps-plus: when a turn ends, fork the session (it shares the prompt cache,
// so it has the whole conversation, CLAUDE.md and memory for the price of one short
// reply) and ask for up to three likely next prompts. Unlike next-steps, the fork is
// also handed fresh context: the repo's git state, the branch's PR and checks, and
// the user's own notes files and notes folders (by title). The band shows each
// suggestion's label; hovering or focusing one reveals its full prompt. 1/2/3 put
// it in the prompt box as a draft ($.prompt.fill); nothing is submitted.

import type { EngineInterface, Register, RenderElement } from 'claude-code'

import {
  type Suggestion,
  cleanBlock,
  expandTemplate,
  forkPrompt,
  gitSection,
  indexSection,
  parseStatus,
  parseSuggestions,
  prSection,
  skillList,
  splitList,
  titleOf,
} from './suggest'

type View = { kind: 'hidden' } | { kind: 'loading'; turnId: string } | { kind: 'offer'; items: Suggestion[] }

type Options = {
  minAnswerChars: number
  suggestSkills: boolean
  git: boolean
  pr: boolean
  notes: string[]
  index: string[]
}

const GIT_TIMEOUT = 4_000
const GH_TIMEOUT = 8_000
const PR_TTL = 2 * 60_000
const INDEX_TTL = 10 * 60_000
const NOTE_BYTES = 8_000
const NOTE_CHARS = 3_000
const NOTES_MAX = 3
const INDEX_CHARS = 2_500
const INDEX_FILES = 60
const TITLE_BYTES = 600
const PR_FIELDS = 'number,title,state,isDraft,reviewDecision,mergeStateStatus,statusCheckRollup'

// Session-local; a hot reload resets them, which is fine.
let view: View = { kind: 'hidden' }
let focused: number | null = null
let home: string | undefined
const prCache = new Map<string, { at: number; text: string | undefined }>()
const indexCache = new Map<string, { at: number; text: string | undefined }>()

function show($: EngineInterface, next: View): void {
  view = next
  focused = null
  $.ui.invalidate('ui.render')
}

function readOptions(options: Record<string, unknown> | undefined): Options {
  const o = options ?? {}
  return {
    minAnswerChars: typeof o.minAnswerChars === 'number' ? o.minAnswerChars : 80,
    suggestSkills: o.suggestSkills !== false,
    git: o.gitContext !== false,
    pr: o.prContext !== false,
    notes: typeof o.notesFiles === 'string' ? splitList(o.notesFiles).slice(0, NOTES_MAX) : [],
    index: typeof o.notesIndexDirs === 'string' ? splitList(o.notesIndexDirs).slice(0, NOTES_MAX) : [],
  }
}

/** stdout of a command that exited 0, else undefined; never rejects. */
async function run($: EngineInterface, argv: string[], timeoutMs: number): Promise<string | undefined> {
  try {
    const r = await $.process.run(argv, { timeoutMs })
    return r.exitCode === 0 ? r.stdout : undefined
  } catch {
    return undefined
  }
}

async function homeDir($: EngineInterface): Promise<string | undefined> {
  if (home === undefined) home = (await run($, ['printenv', 'HOME'], GIT_TIMEOUT))?.trim() || undefined
  return home
}

function short(path: string, homePath: string | undefined): string {
  return homePath && path.startsWith(`${homePath}/`) ? `~${path.slice(homePath.length)}` : path
}

type Git = { repo?: string; branch?: string; section?: string }

async function gitContext($: EngineInterface): Promise<Git> {
  const status = await run($, ['git', 'status', '--porcelain=v1', '--branch'], GIT_TIMEOUT)
  if (status === undefined) return {}
  const [remote, log] = await Promise.all([
    run($, ['git', 'remote', 'get-url', 'origin'], GIT_TIMEOUT),
    run($, ['git', 'log', '-3', '--format=%h %s'], GIT_TIMEOUT),
  ])
  const s = parseStatus(status)
  const repo = remote
    ?.trim()
    .replace(/\/+$/, '')
    .split(/[/:]/)
    .pop()
    ?.replace(/\.git$/, '')
  const commits = log ? log.split('\n').filter(line => line.trim() !== '') : []
  return { repo, branch: s.branch, section: gitSection(s, commits, repo) }
}

async function prContext($: EngineInterface, cwd: string, branch: string | undefined, now: number) {
  if (!branch) return undefined
  const key = `${cwd}\0${branch}`
  const hit = prCache.get(key)
  if (hit && now - hit.at < PR_TTL) return hit.text
  let text: string | undefined
  try {
    const r = await $.process.run(['gh', 'pr', 'view', '--json', PR_FIELDS], { timeoutMs: GH_TIMEOUT })
    if (r.exitCode === 0) text = prSection(r.stdout)
    else if (/no (open )?pull requests? found/i.test(r.stderr)) text = 'pull request: none for this branch yet'
  } catch {
    text = undefined
  }
  prCache.set(key, { at: now, text })
  return text
}

async function notesContext($: EngineInterface, templates: string[], place: Parameters<typeof expandTemplate>[1]) {
  const out: string[] = []
  for (const template of templates) {
    const path = expandTemplate(template, place)
    if (!path) continue
    try {
      if (!(await $.fs.exists(path))) continue
      const body = cleanBlock(await $.fs.read(path, { as: 'text', length: NOTE_BYTES }), NOTE_CHARS)
      if (body !== '') out.push(`notes (${short(path, place.home)}):\n${body}`)
    } catch {
      // unreadable: skipped
    }
  }
  return out
}

async function markdownIn($: EngineInterface, dir: string): Promise<string[]> {
  const found: string[] = []
  const top = await $.fs.list(dir)
  for (const e of top) {
    if (found.length >= INDEX_FILES) break
    if (e.kind === 'file' && e.name.endsWith('.md')) found.push(e.name)
    else if (e.kind === 'dir' && !e.name.startsWith('.')) {
      const inner = await $.fs.list(`${dir}/${e.name}`).catch(() => [])
      for (const f of inner) {
        if (found.length >= INDEX_FILES) break
        if (f.kind === 'file' && f.name.endsWith('.md')) found.push(`${e.name}/${f.name}`)
      }
    }
  }
  return found.sort()
}

async function indexContext($: EngineInterface, templates: string[], place: Parameters<typeof expandTemplate>[1], now: number) {
  const out: string[] = []
  for (const template of templates) {
    const dir = expandTemplate(template, place)?.replace(/\/+$/, '')
    if (!dir) continue
    const hit = indexCache.get(dir)
    let text = hit && now - hit.at < INDEX_TTL ? hit.text : undefined
    if (!hit || now - hit.at >= INDEX_TTL) {
      try {
        const files = await markdownIn($, dir)
        const entries = await Promise.all(
          files.map(async path => ({
            path,
            title: titleOf(await $.fs.read(`${dir}/${path}`, { as: 'text', length: TITLE_BYTES }).catch(() => ''), path),
          })),
        )
        text = entries.length > 0 ? `notes index (${short(dir, place.home)}):\n${indexSection(entries, INDEX_CHARS)}` : undefined
      } catch {
        text = undefined
      }
      indexCache.set(dir, { at: now, text })
    }
    if (text) out.push(text)
  }
  return out
}

/** The context block for the fork: empty when nothing applies. */
async function gather($: EngineInterface, o: Options): Promise<string> {
  const now = await $.clock.now()
  const needsPlace = o.pr || o.notes.length > 0 || o.index.length > 0
  const [git, cwd, homePath] = await Promise.all([
    o.git || needsPlace ? gitContext($) : Promise.resolve<Git>({}),
    $.session.cwd(),
    o.notes.length > 0 || o.index.length > 0 ? homeDir($) : Promise.resolve(undefined),
  ])
  const place = { repo: git.repo, branch: git.branch, home: homePath ?? '' }
  const [pr, notes, index] = await Promise.all([
    o.pr ? prContext($, cwd, git.branch, now) : Promise.resolve(undefined),
    homePath ? notesContext($, o.notes, place) : Promise.resolve([]),
    homePath ? indexContext($, o.index, place, now) : Promise.resolve([]),
  ])
  const sections: string[] = []
  if (o.git && git.section) sections.push(git.section)
  if (pr) sections.push(pr)
  sections.push(...notes, ...index)
  return sections.join('\n\n')
}

export const register: Register = (on, options) => {
  const o = readOptions(options as Record<string, unknown> | undefined)

  // A new turn (typed or otherwise) hides whatever was offered.
  on('turn.start', async ($, e, next) => {
    if (view.kind !== 'hidden') show($, { kind: 'hidden' })
    return next(e)
  })

  // Turn over: gather context and ask the fork, detached, so the turn never waits.
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.reason !== 'answer' || e.answer.trim().length < o.minAnswerChars) return result
    const turnId = e.turnId
    show($, { kind: 'loading', turnId })
    void (async () => {
      let items: Suggestion[] = []
      try {
        const [commands, context] = await Promise.all([
          $.command.list().catch(() => null),
          gather($, o).catch(error => {
            $.ui.log(`context skipped: ${String(error)}`)
            return ''
          }),
        ])
        const known = commands === null ? null : new Set(commands.map(command => command.name))
        const skills = o.suggestSkills && commands !== null ? skillList(commands) : ''
        const reply = await $.model.fork({ prompt: forkPrompt(skills, context) })
        items = reply.isAnswered ? parseSuggestions(reply.text, known) : []
      } catch (error) {
        $.ui.log(`fork failed: ${String(error)}`)
      }
      // A newer turn started (or another completed) while we waited: drop ours.
      if (view.kind !== 'loading' || view.turnId !== turnId) return
      show($, items.length === 0 ? { kind: 'hidden' } : { kind: 'offer', items })
      if (items[0] !== undefined) void $.prompt.suggest({ text: items[0].prompt }).catch(() => undefined)
    })()
    return result
  })

  // Keyboard focus on a suggestion shows its prompt, as a pointer hovering it does.
  // A failure here is skipped and the ring moves anyway (fails open), as it should.
  on('ui.focus', { component: 'AbovePrompt' }, async ($, e, next) => {
    const match = /^pick:(\d)$/.exec(e.element ?? '')
    const now = match ? Number(match[1]) : null
    if (now !== focused) {
      focused = now
      $.ui.invalidate('ui.render')
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next): Promise<RenderElement> => {
    const below = await next(e)
    if (e.props.hasSurvey || e.props.isWorking || view.kind === 'hidden') return below
    const { Box, Text, Button } = $.ui.resolve(e)

    if (view.kind === 'loading') {
      return (
        <Box flexDirection="column">
          {below}
          <Box marginTop={1}>
            <Text dimColor>next steps…</Text>
          </Box>
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        {below}
        <Box marginTop={1} />
        <Text dimColor>next:</Text>
        {view.items.map((item, index) => {
          const isFocused = focused === index
          return (
            <Box key={`s${index}`} flexDirection="column" marginLeft={2}>
              <Button
                key={`pick:${index}`}
                hotkey={String(index + 1)}
                plain
                label={item.label}
                onPress={() => {
                  show($, { kind: 'hidden' })
                  void $.prompt.fill({ text: item.prompt }).then(
                    r => r.isFilled || $.ui.toast('could not fill the prompt box'),
                    error => $.ui.toast(`could not fill: ${String(error)}`),
                  )
                }}
              />
              {/* No key: the row's keyed Box is the hover scope, so the pointer on the
                  label reveals this. A keyed hidden Box could never be hovered. */}
              <Box
                marginLeft={3}
                display={isFocused ? 'flex' : 'none'}
                hover={isFocused ? undefined : { display: 'flex' }}
              >
                <Text dimColor wrap="wrap">
                  {item.prompt}
                </Text>
              </Box>
            </Box>
          )
        })}
        <Box marginLeft={2}>
          <Button key="dismiss" hotkey="0" plain label="dismiss" onPress={() => show($, { kind: 'hidden' })} />
        </Box>
      </Box>
    )
  })
}
