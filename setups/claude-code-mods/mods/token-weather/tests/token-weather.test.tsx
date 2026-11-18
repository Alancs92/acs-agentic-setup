import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { contextOutlook, limitOutlook, record } from '../hooks/forecast'

const T0 = Date.parse('2026-11-18T10:00:00Z')
const MIN = 60_000
const HOUR = 60 * MIN
const iso = (ms: number) => new Date(ms).toISOString()

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

describe('forecast', () => {
  test('context: per-turn growth, turns left, and a reset on compaction', async () => {
    let w = record(null, { context: { window: 200_000, tokens: 40_000 }, rateLimits: [] }, T0)
    w = record(w, { context: { window: 200_000, tokens: 50_000 }, rateLimits: [] }, T0)
    w = record(w, { context: { window: 200_000, tokens: 62_000 }, rateLimits: [] }, T0)

    const c = contextOutlook(w)
    expect(c?.pct).toBe(31)
    expect(c?.perTurn).toBe(11_000)
    expect(c?.turnsLeft).toBe(12)

    w = record(w, { context: { window: 200_000, tokens: 30_000 }, rateLimits: [] }, T0)
    expect(w.ctx).toEqual([30_000])
    expect(contextOutlook(w)?.perTurn).toBeUndefined()
  })

  test('5h: the last hour of readings projects the reset', async () => {
    const reset = iso(T0 + 2 * HOUR)
    let w = record(null, { context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed: 30, resetsAt: reset }] }, T0)
    w = record(w, { context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed: 36, resetsAt: reset }] }, T0 + 30 * MIN)

    const l = limitOutlook(w.limits[0]!, w.samples, T0 + 30 * MIN)
    expect(l.basis).toBe('burn')
    expect(l.ratePerHour).toBe(12)
    expect(l.atReset).toBe(54)
    expect(l.outInMs).toBeUndefined()
  })

  test('5h: a fast burn says when the limit is hit before the reset', async () => {
    const reset = iso(T0 + 2 * HOUR)
    let w = record(null, { context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed: 40, resetsAt: reset }] }, T0)
    w = record(w, { context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed: 60, resetsAt: reset }] }, T0 + 30 * MIN)

    const l = limitOutlook(w.limits[0]!, w.samples, T0 + 30 * MIN)
    expect(l.ratePerHour).toBe(40)
    expect(l.outInMs).toBe(HOUR)
  })

  test('week: the window average paces the reset', async () => {
    const l = limitOutlook({ kind: 'seven_day', percentUsed: 40, resetsAt: iso(T0 + 3 * 24 * HOUR) }, [], T0)
    expect(l.basis).toBe('pace')
    expect(Math.round(l.atReset ?? 0)).toBe(70)
  })

  test('a window that reset drops its old readings', async () => {
    let w = record(null, { context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed: 80, resetsAt: iso(T0 + HOUR) }] }, T0)
    const after = T0 + HOUR + 5 * MIN
    w = record(w, { context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed: 2, resetsAt: iso(T0 + 6 * HOUR) }] }, after)
    expect(w.limits[0]?.percentUsed).toBe(2)
    expect(w.samples).toEqual([{ kind: 'five_hour', t: after, p: 2 }])
  })

  test('a dip before the reset is held, so it neither shows nor skews the burn', async () => {
    const reset = iso(T0 + 2 * HOUR)
    const five = (percentUsed: number) => ({ context: { window: 1 }, rateLimits: [{ kind: 'five_hour', percentUsed, resetsAt: reset }] })
    let w = record(null, five(36), T0)
    w = record(w, five(0), T0 + 5 * MIN)
    expect(w.limits[0]?.percentUsed).toBe(36)

    w = record(w, five(38), T0 + 10 * MIN)
    const l = limitOutlook(w.limits[0]!, w.samples, T0 + 10 * MIN)
    expect(l.ratePerHour).toBe(12)
    expect(l.outInMs).toBeUndefined()
  })
})

/** The engine beneath the mod: the start, the usage op and the measure event. */
function engine(on: On, usage: { tokens?: number; limits: { kind: string; percentUsed: number; resetsAt?: string }[] }) {
  mock.clock(on, { now: T0 })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.usage', () => ({
    value: { startedAt: T0, context: { window: 200_000, tokens: usage.tokens }, rateLimits: usage.limits },
  }))
  on('session.measure', (_, e) => ({ changed: e.changed }))
}

describe('band', () => {
  const limits = [
    { kind: 'five_hour', percentUsed: 36, resetsAt: iso(T0 + 2 * HOUR) },
    { kind: 'seven_day', percentUsed: 40, resetsAt: iso(T0 + 3 * 24 * HOUR) },
  ]

  test('a wide band shows context, its trend and both limits', async ($, on) => {
    engine(on, { tokens: 40_000, limits })
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    await $.session.measure({ context: { window: 200_000, tokens: 62_000 }, rateLimits: limits, changed: ['context'] })

    const ui = await $.ui.mount(BAND(160))
    const row = await ui.find({ type: 'Text', text: /ctx/ })
    expect(row?.text).toMatch(/ctx 31%/)
    expect(row?.text).toMatch(/\+22\.0k\/turn ~6 turns left/)
    expect(row?.text).toMatch(/5h 36%/)
    expect(row?.text).toMatch(/week 40% → ~70% at reset · resets 3d0h/)
    await ui.unmount()
  })

  test('a narrow band keeps only the figures', async ($, on) => {
    engine(on, { tokens: 62_000, limits })
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })

    const ui = await $.ui.mount(BAND(36))
    const row = await ui.find({ type: 'Text', text: /31%/ })
    // ☁: the week is paced to reach ~70% by its reset.
    expect(row?.text).toBe('☁ 31% · 5h 36% · wk 40%')
    await ui.unmount()
  })

  test('nothing is drawn before the first reading', async ($, on) => {
    engine(on, { limits: [] })
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })

    const ui = await $.ui.mount(BAND(160))
    expect(await ui.find({ type: 'Text', text: /ctx|5h/ })).toBeUndefined()
    await ui.unmount()
  })
})
