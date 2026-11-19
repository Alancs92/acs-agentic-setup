import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { fileName, liveCount, liveDir, stale } from '../hooks/live'

const T0 = Date.parse('2026-11-19T10:00:00Z')
const SEC = 1000
const MIN = 60 * SEC
const HOUR = 60 * MIN
const DIR = '/cfg/acct/token-weather/live'
const iso = (ms: number) => new Date(ms).toISOString()

const LIMITS = [
  { kind: 'five_hour', percentUsed: 36, resetsAt: iso(T0 + 2 * HOUR) },
  { kind: 'seven_day', percentUsed: 40, resetsAt: iso(T0 + 3 * 24 * HOUR) },
]

const BAND = (bodyColumns: number) =>
  ({
    plugin: 'token-weather',
    component: 'AbovePrompt',
    surface: 'terminal',
    props: {
      hasSurvey: false,
      isCollapsed: false,
      isWorking: false,
      maxRows: 10,
      bodyColumns,
      scroll: { offset: 0, bodyRows: 9 },
      view: {},
    },
  }) as const

const file = (name: string, mtimeMs: number, kind = 'file') => ({ name, kind, mtimeMs })

describe('live sessions: helpers', () => {
  test('a session id becomes a safe file name', async () => {
    expect(fileName('8f2c-41aa_b')).toBe('8f2c-41aa_b.json')
    expect(fileName('../x y')).toBe('___x_y.json')
    expect(fileName('')).toBeUndefined()
  })

  test("the folder sits in the account's config dir, else in ~/.claude", async () => {
    expect(liveDir('/cfg/acct', '/home/me')).toBe(DIR)
    expect(liveDir('/cfg/acct/', '/home/me')).toBe(DIR)
    expect(liveDir(undefined, '/home/me')).toBe('/home/me/.claude/token-weather/live')
    expect(liveDir('', '/home/me')).toBe('/home/me/.claude/token-weather/live')
    expect(liveDir(undefined, undefined)).toBeUndefined()
  })

  test('counts the session files written in the last 150 s, nothing else', async () => {
    const entries = [
      file('a.json', T0),
      file('b.json', T0 - 2 * MIN),
      file('c.json', T0 - 3 * MIN), // missed its beats: gone
      file('notes.txt', T0),
      file('d.json', T0, 'dir'),
      file('.e.json', T0),
    ]
    expect(liveCount(entries, T0)).toBe(2)
  })

  test('prunes only session files over an hour old', async () => {
    const entries = [file('old.json', T0 - 61 * MIN), file('recent.json', T0 - 59 * MIN), file('keep.txt', T0 - 9 * HOUR)]
    expect(stale(entries, T0)).toEqual(['old.json'])
  })
})

type World = {
  id: string
  env?: Record<string, string>
  files: Map<string, number>
  writeFails?: boolean
  removed: string[][]
}

function world(files: Record<string, number> = {}): World {
  return { id: 'sess-1', files: new Map(Object.entries(files)), removed: [] }
}

/** The engine beneath the mod: env, the session's id, a folder of files and rm. */
function engine(on: On, w: World) {
  const clock = mock.clock(on, { now: T0 })
  mock.env(on, w.env ?? { CLAUDE_CONFIG_DIR: '/cfg/acct', HOME: '/home/me' })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  on('session.id', () => ({ value: w.id }))
  on('session.usage', () => ({
    value: { startedAt: T0, context: { window: 200_000, tokens: 62_000 }, rateLimits: LIMITS },
  }))
  on('fs.write', (_, e) => {
    if (w.writeFails) return { deny: 'EACCES' }
    w.files.set(e.path, clock.now())
    return { value: undefined }
  })
  on('fs.list', (_, e) => ({
    value: [...w.files]
      .filter(([path]) => path.startsWith(`${e.path}/`))
      .map(([path, t]) => ({ name: path.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: t, isLink: false })),
  }))
  on('process.run', (_, e) => {
    if (e.argv[0] === 'rm') {
      w.removed.push([...e.argv])
      for (const path of e.argv.slice(2)) w.files.delete(path)
    }
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return clock
}

async function start($: Engine, clock: { settle: () => Promise<void> }) {
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await clock.settle()
}

async function bandText($: Engine, columns: number): Promise<string> {
  const ui = await $.ui.mount(BAND(columns))
  const row = await ui.find({ type: 'Text', text: /31%/ })
  await ui.unmount()
  return row?.text ?? ''
}

describe('live sessions: band', () => {
  test("each session keeps a file in its account's folder; a full-width band counts the live ones", async ($, on) => {
    const w = world({ [`${DIR}/other.json`]: T0 - 30 * SEC, [`${DIR}/gone.json`]: T0 - 10 * MIN })
    const clock = engine(on, w)
    await start($, clock)

    expect(w.files.get(`${DIR}/sess-1.json`)).toBe(T0)
    expect(await bandText($, 200)).toMatch(/ {2}│ {2}2 live sessions$/)
    expect(await bandText($, 36)).not.toMatch(/live/)
  })

  test('the count only takes room the full line leaves over', async ($, on) => {
    const w = world({ [`${DIR}/other.json`]: T0 })
    const clock = engine(on, w)
    await start($, clock)

    const full = (await bandText($, 200)).replace(/ {2}│ {2}2 live sessions$/, '')
    expect(full).toMatch(/resets 3d0h$/)
    // Exactly as wide as the full line without the count: the detail stays, the count goes.
    expect(await bandText($, [...full].length)).toBe(full)
  })

  test('every minute the count follows sessions starting and ending', async ($, on) => {
    const w = world()
    const clock = engine(on, w)
    await start($, clock)
    expect(await bandText($, 200)).toMatch(/1 live session$/)

    w.files.set(`${DIR}/other.json`, clock.now())
    await clock.advance(MIN)
    expect(await bandText($, 200)).toMatch(/2 live sessions$/)

    await clock.advance(3 * MIN) // the other one stopped beating
    expect(await bandText($, 200)).toMatch(/1 live session$/)
  })

  test('an ending session removes its file; after /clear it beats under the new id', async ($, on) => {
    const w = world()
    const clock = engine(on, w)
    await start($, clock)

    await $.session.end({ reason: 'clear', sessionId: 'sess-1', resume: { id: 'sess-1' } } as never)
    expect(w.removed).toContainEqual(['rm', '-f', `${DIR}/sess-1.json`])
    expect(w.files.has(`${DIR}/sess-1.json`)).toBe(false)

    w.id = 'sess-2'
    await clock.advance(MIN)
    expect(w.files.has(`${DIR}/sess-2.json`)).toBe(true)
    expect(await bandText($, 200)).toMatch(/1 live session$/)
  })

  test('files a crashed session left over an hour ago are pruned', async ($, on) => {
    const w = world({ [`${DIR}/crashed.json`]: T0 - 2 * HOUR })
    const clock = engine(on, w)
    await start($, clock)
    expect(w.removed).toContainEqual(['rm', '-f', `${DIR}/crashed.json`])
  })

  test('without a folder it can write, the band draws as before, with no count', async ($, on) => {
    const w = world()
    w.writeFails = true
    const clock = engine(on, w)
    await start($, clock)
    const text = await bandText($, 200)
    expect(text).toMatch(/ctx 31%/)
    expect(text).not.toMatch(/live/)
  })
})
