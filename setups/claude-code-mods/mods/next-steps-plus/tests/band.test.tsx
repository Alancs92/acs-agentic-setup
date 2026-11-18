import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

const T0 = Date.parse('2026-11-18T10:00:00Z')
const HOME = '/Users/me'

const BAND = {
  plugin: 'next-steps-plus',
  component: 'AbovePrompt',
  surface: 'terminal',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 20,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 19 },
    view: {},
  },
} as const

const PR_JSON = JSON.stringify({
  number: 7,
  title: 'Add a',
  state: 'OPEN',
  isDraft: false,
  reviewDecision: 'APPROVED',
  mergeStateStatus: 'CLEAN',
  statusCheckRollup: [{ __typename: 'CheckRun', name: 'lint', status: 'COMPLETED', conclusion: 'FAILURE' }],
})

const REPLY = JSON.stringify([
  { label: 'Fix lint', prompt: 'Fix the failing lint check on PR #7, then push.' },
  { label: 'Commit a.ts', prompt: '/code-commit the change to a.ts' },
])

const ANSWER = 'Done. I changed a.ts and the tests pass; lint on the PR is still red, which is worth a look.'

type Entry = { name: string; kind: 'file' | 'dir' }
type World = {
  inRepo?: boolean
  gh?: { exitCode: number; stdout?: string; stderr?: string }
  files?: Record<string, string>
  dirs?: Record<string, Entry[]>
  reply?: string
}

/** The engine beneath the mod: git, gh, files, the fork and the prompt box. */
function engine(on: On, w: World = {}) {
  const seen = { fork: '', argv: [] as string[][], filled: '' }
  mock.clock(on, { now: T0 })
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.reason === 'refusal' ? '' : e.answer }))
  on('session.cwd', () => ({ value: '/work/demo' }))
  on('command.list', () => ({
    value: [{ name: 'code-commit', description: 'Commit with checks', source: 'user' as const }],
  }))
  on('process.run', (_, e) => {
    seen.argv.push([...e.argv])
    const [cmd, sub] = e.argv
    const result = (exitCode: number, stdout: string, stderr = '') => ({
      value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false },
    })
    const ok = (stdout: string) => result(0, stdout)
    if (cmd === 'printenv') return ok(`${HOME}\n`)
    if (cmd === 'git') {
      if (w.inRepo === false) return result(128, '', 'fatal: not a git repository')
      if (sub === 'status') return ok('## feat/x...origin/feat/x [ahead 2]\n M a.ts\n')
      if (sub === 'remote') return ok('git@github.com:me/demo.git\n')
      if (sub === 'log') return ok('abc1234 add a\n')
    }
    if (cmd === 'gh') {
      const g = w.gh ?? { exitCode: 0, stdout: PR_JSON }
      return result(g.exitCode, g.stdout ?? '', g.stderr ?? '')
    }
    return result(127, '', 'not found')
  })
  on('fs.exists', (_, e) => ({ value: e.path in (w.files ?? {}) }))
  on('fs.read', (_, e) => {
    const text = w.files?.[e.path]
    return text === undefined ? { deny: 'ENOENT' } : { value: text }
  })
  on('fs.list', (_, e) => {
    const entries = w.dirs?.[e.path]
    return entries === undefined
      ? { deny: 'ENOENT' }
      : { value: entries.map(x => ({ ...x, size: 1, mtimeMs: 0, isLink: false })) }
  })
  on('model.fork', (_, e) => {
    seen.fork = e.prompt
    return {
      value: {
        isAnswered: true,
        text: w.reply ?? REPLY,
        usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
      },
    }
  })
  on('prompt.suggest', () => ({ isShown: true }))
  on('ui.focus', () => ({}))
  on('prompt.fill', (_, e) => {
    seen.filled = e.text
    return { isFilled: true }
  })
  return seen
}

async function endTurn($: Engine, answer = ANSWER) {
  await $.turn.complete({
    turnId: 't1',
    reason: 'answer',
    answer,
    durationMs: 1000,
    isAborted: false,
    category: null,
    explanation: null,
  } as never)
}

/** The preview Boxes, in order: the Boxes in the drawn tree that set `display`. */
async function previews(ui: { drawn: () => Promise<unknown> }) {
  const found: { props: Record<string, unknown>; hover?: Record<string, unknown> }[] = []
  const walk = (node: unknown) => {
    if (Array.isArray(node)) return node.forEach(walk)
    if (typeof node !== 'object' || node === null) return
    const n = node as { type?: unknown; props?: Record<string, unknown>; hover?: Record<string, unknown>; children?: unknown }
    if (n.type === 'Box' && n.props && 'display' in n.props) found.push({ props: n.props, hover: n.hover })
    walk(n.props?.children)
    walk(n.children)
  }
  walk(await ui.drawn())
  return found
}

/** The fork runs detached: wait until the band shows its offer. */
async function offer(ui: { find: (q: { text: RegExp }) => Promise<unknown> }) {
  for (let i = 0; i < 200; i++) {
    if (await ui.find({ text: /^next:$/ })) return
    await Promise.resolve()
  }
  throw new Error('no offer drawn')
}


const options = {
  notesFiles: '~/notes/{repo}/active/{branch_dashed}.md',
  notesIndexDirs: '~/notes/{repo}/handbook',
}

describe('context', () => {
  test('the fork hears git, the PR and its checks, the notes file and the handbook titles', { options }, async ($, on) => {
    const seen = engine(on, {
      files: {
        [`${HOME}/notes/demo/active/feat-x.md`]: '# Active work\n\n- [ ] wire the parser',
        [`${HOME}/notes/demo/handbook/README.md`]: '# Handbook',
        [`${HOME}/notes/demo/handbook/gotchas/glob.md`]: '# Globs fail open',
      },
      dirs: {
        [`${HOME}/notes/demo/handbook`]: [
          { name: 'README.md', kind: 'file' },
          { name: 'gotchas', kind: 'dir' },
        ],
        [`${HOME}/notes/demo/handbook/gotchas`]: [{ name: 'glob.md', kind: 'file' }],
      },
    })
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)

    expect(seen.fork).toMatch(/repo: demo · branch: feat\/x \(2 ahead, 0 behind origin\/feat\/x\)/)
    expect(seen.fork).toMatch(/PR #7 "Add a" OPEN · review: APPROVED · merge: CLEAN · checks: 0 passed, 1 failed \(lint\)/)
    expect(seen.fork).toMatch(/notes \(~\/notes\/demo\/active\/feat-x\.md\):\n# Active work\n\n- \[ \] wire the parser/)
    expect(seen.fork).toMatch(/- gotchas\/glob\.md: Globs fail open/)
    expect(seen.fork).toMatch(/\/code-commit: Commit with checks/)
    await ui.unmount()
  })

  test('outside a repo there is no git or PR context, and suggestions still come', async ($, on) => {
    const seen = engine(on, { inRepo: false })
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)
    expect(seen.fork).not.toMatch(/<context>/)
    expect(seen.argv.some(a => a[0] === 'gh')).toBe(false)
    await ui.unmount()
  })

  test('no PR for the branch is said plainly; a gh failure is left out', async ($, on) => {
    const none = engine(on, { gh: { exitCode: 1, stderr: 'no pull requests found for branch "feat/x"' } })
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)
    expect(none.fork).toMatch(/pull request: none for this branch yet/)
    await ui.unmount()
  })

  test('a short answer gets no suggestions and no fork', async ($, on) => {
    const seen = engine(on)
    const ui = await $.ui.mount(BAND)
    await endTurn($, 'ok')
    expect(seen.fork).toBe('')
    expect(await ui.find({ text: /next/ })).toBeUndefined()
    await ui.unmount()
  })
})

describe('band', () => {
  test('labels show; each prompt is hidden until hovered', async ($, on) => {
    engine(on)
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)

    expect(await ui.findAll({ type: 'Button' })).toHaveLength(3) // two picks and dismiss
    const [first, second] = await previews(ui)
    for (const preview of [first, second]) {
      expect(preview?.props).toMatchObject({ display: 'none' })
      expect(preview?.hover).toEqual({ display: 'flex' })
    }
    expect((await ui.find({ type: 'Text', text: /^Fix the failing lint check/ }))?.text).toBe(
      'Fix the failing lint check on PR #7, then push.',
    )
    await ui.unmount()
  })

  test('keyboard focus on a suggestion shows its prompt', async ($, on) => {
    engine(on)
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)

    // The engine raises ui.focus when the person Tabs or arrows onto a Button in the band.
    await $.ui.focus({ component: 'AbovePrompt', requestId: 'band', element: 'pick:1', origin: 'person' } as never)
    const [first, second] = await previews(ui)
    expect(second?.props).toMatchObject({ display: 'flex' })
    expect(second?.hover).toBeUndefined()
    expect(first?.props).toMatchObject({ display: 'none' })
    await ui.unmount()
  })

  test('a press fills the prompt box and hides the band; 0 dismisses', async ($, on) => {
    const seen = engine(on)
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)

    await ui.press({ key: 'pick:0' })
    expect(seen.filled).toBe('Fix the failing lint check on PR #7, then push.')
    expect(await ui.find({ text: /^next:$/ })).toBeUndefined()
    await ui.unmount()
  })

  test('model text is cleaned before it is drawn', async ($, on) => {
    engine(on, { reply: JSON.stringify([{ label: 'Red\u001b[31m alert', prompt: 'Do​ it' }]) })
    const ui = await $.ui.mount(BAND)
    await endTurn($)
    await offer(ui)
    expect((await ui.find({ key: 'pick:0' }))?.text).toMatch(/Red alert/)
    expect(await ui.find({ text: /Do it/ })).toBeDefined()
    await ui.unmount()
  })
})
