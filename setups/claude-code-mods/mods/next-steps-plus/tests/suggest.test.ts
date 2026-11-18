import { describe, expect, test } from 'claude-code/testing'

import {
  cleanBlock,
  expandTemplate,
  forkPrompt,
  gitSection,
  indexSection,
  parseStatus,
  parseSuggestions,
  prSection,
  splitList,
  titleOf,
} from '../hooks/suggest'

describe('git', () => {
  test('status: branch, upstream, ahead/behind and file counts', async () => {
    const s = parseStatus(
      '## chore/x...origin/chore/x [ahead 2, behind 1]\nM  staged.ts\n M edited.ts\nMM both.ts\n?? new.ts\n',
    )
    expect(s).toMatchObject({ branch: 'chore/x', upstream: 'origin/chore/x', ahead: 2, behind: 1 })
    expect(s).toMatchObject({ staged: 2, unstaged: 2, untracked: 1 })
    expect(s.paths).toEqual(['staged.ts', 'edited.ts', 'both.ts', 'new.ts'])
  })

  test('status: a fresh branch with no upstream, and a detached head', async () => {
    expect(parseStatus('## No commits yet on main\n')).toMatchObject({ branch: 'main', ahead: 0 })
    expect(parseStatus('## feat/y\n')).toMatchObject({ branch: 'feat/y', upstream: undefined })
    expect(parseStatus('## HEAD (no branch)\n').branch).toBeUndefined()
  })

  test('section: one compact block, paths capped', async () => {
    const paths = Array.from({ length: 14 }, (_, i) => `f${i}.ts`)
    const text = gitSection(
      { branch: 'feat/y', upstream: 'origin/feat/y', ahead: 3, behind: 0, staged: 0, unstaged: 14, untracked: 0, paths },
      ['abc1234 fix the thing'],
      'demo',
    )
    expect(text).toMatch(/repo: demo/)
    expect(text).toMatch(/branch: feat\/y \(3 ahead, 0 behind origin\/feat\/y\)/)
    expect(text).toMatch(/14 unstaged/)
    expect(text).toMatch(/\(\+4 more\)/)
    expect(text).toMatch(/abc1234 fix the thing/)
  })
})

describe('pull request', () => {
  test('summary: state, review, merge state and checks by outcome', async () => {
    const json = JSON.stringify({
      number: 25,
      title: 'Keep swift-lsp',
      state: 'OPEN',
      isDraft: true,
      reviewDecision: 'REVIEW_REQUIRED',
      mergeStateStatus: 'BLOCKED',
      statusCheckRollup: [
        { __typename: 'CheckRun', name: 'test', status: 'COMPLETED', conclusion: 'SUCCESS' },
        { __typename: 'CheckRun', name: 'lint', status: 'COMPLETED', conclusion: 'FAILURE' },
        { __typename: 'CheckRun', name: 'build', status: 'IN_PROGRESS', conclusion: '' },
        { __typename: 'StatusContext', context: 'ci/legacy', state: 'SUCCESS' },
      ],
    })
    const text = prSection(json)
    expect(text).toMatch(/PR #25 "Keep swift-lsp" OPEN \(draft\)/)
    expect(text).toMatch(/review: REVIEW_REQUIRED/)
    expect(text).toMatch(/merge: BLOCKED/)
    expect(text).toMatch(/checks: 2 passed, 1 failed \(lint\), 1 pending/)
  })

  test('summary: no checks, and junk', async () => {
    expect(prSection(JSON.stringify({ number: 3, title: 't', state: 'MERGED', statusCheckRollup: [] }))).toMatch(
      /PR #3 "t" MERGED · checks: none/,
    )
    expect(prSection('not json')).toBeUndefined()
  })
})

describe('notes', () => {
  const place = { repo: 'demo', branch: 'feat/x', home: '/Users/me' }

  test('templates: placeholders, home, and unresolved ones skipped', async () => {
    expect(expandTemplate('~/notes/{repo}/active/{branch_dashed}.md', place)).toBe(
      '/Users/me/notes/demo/active/feat-x.md',
    )
    expect(expandTemplate('{home}/n/{branch}.md', place)).toBe('/Users/me/n/feat/x.md')
    expect(expandTemplate('~/n/{repo}.md', { home: '/Users/me' })).toBeUndefined()
    expect(expandTemplate('  ', place)).toBeUndefined()
    expect(splitList(' a.md , ,b.md')).toEqual(['a.md', 'b.md'])
  })

  test('templates: a repo or branch that could leave the folder is refused', async () => {
    expect(expandTemplate('~/n/{repo}.md', { ...place, repo: '..' })).toBeUndefined()
    expect(expandTemplate('~/n/{branch}.md', { ...place, branch: 'a/../../b' })).toBeUndefined()
  })

  test('blocks keep their lines but lose escapes and hidden characters', async () => {
    expect(cleanBlock('# Title\n\x1b[31mred\x1b[0m\tline​\n\n\n\nend', 100)).toBe('# Title\nred line\n\nend')
    expect(cleanBlock('abcdef', 4)).toBe('abc…')
  })

  test('index: titles from the first heading, else the file name', async () => {
    expect(titleOf('---\ntags: x\n---\n# Hash what you store\nbody', 'x.md')).toBe('Hash what you store')
    expect(titleOf('no heading here', 'gotchas/a.md')).toBe('gotchas/a.md')
    const text = indexSection(
      [
        { path: 'gotchas/a.md', title: 'A' },
        { path: 'invariants/b.md', title: 'B' },
      ],
      1000,
    )
    expect(text).toBe('- gotchas/a.md: A\n- invariants/b.md: B')
  })
})

describe('fork prompt and reply', () => {
  test('context goes in a fenced data block, only when there is some', async () => {
    const withContext = forkPrompt('', 'branch: feat/x')
    expect(withContext).toMatch(/<context>\nbranch: feat\/x\n<\/context>/)
    expect(withContext).toMatch(/data, not instructions/)
    expect(forkPrompt('', '')).not.toMatch(/<context>/)
  })

  test('reply: unknown commands dropped, text cleaned, at most three', async () => {
    const reply = `Sure: [
      {"label": "Run tests", "prompt": "Run the tests\\u001b[31m now"},
      {"label": "Ghost", "prompt": "/nope do it"},
      {"label": "Commit", "prompt": "/code-commit the fix"},
      {"label": "Push", "prompt": "Push it"},
      {"label": "Extra", "prompt": "One too many"}
    ]`
    const items = parseSuggestions(reply, new Set(['code-commit']))
    expect(items.map(i => i.label)).toEqual(['Run tests', 'Commit', 'Push'])
    expect(items[0]?.prompt).toBe('Run the tests now')
    expect(parseSuggestions('no json', null)).toEqual([])
  })
})
