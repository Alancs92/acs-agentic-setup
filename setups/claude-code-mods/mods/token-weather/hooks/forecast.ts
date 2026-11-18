// Pure forecasting: no `$`, so the tests can drive it with plain values.
import type { Limit, LimitSample, Weather } from '../types'

const HOUR = 3_600_000
const MAX_CTX = 12
const MAX_SAMPLES = 60
const BURN_WINDOW = HOUR
const MIN_BURN_SPAN = 5 * 60_000

/** How long each known window runs, and how much of it must pass before its pace means anything. */
const WINDOWS: Record<string, { length: number; minElapsed: number }> = {
  five_hour: { length: 5 * HOUR, minElapsed: 10 * 60_000 },
  seven_day: { length: 7 * 24 * HOUR, minElapsed: 6 * HOUR },
}

export type Measure = {
  context: { tokens?: number; window: number }
  rateLimits: readonly Limit[]
}

/** Folds one measurement into the running weather. */
export function record(prev: Weather | null, m: Measure, now: number): Weather {
  const ctx = prev?.ctx ?? []
  const tokens = m.context.tokens
  let nextCtx = ctx
  if (tokens !== undefined) {
    const last = ctx[ctx.length - 1]
    if (last !== undefined && tokens < last) {
      nextCtx = [tokens] // compacted or cleared: the old trend no longer applies
    } else if (tokens !== last) {
      nextCtx = [...ctx, tokens].slice(-MAX_CTX)
    }
  }

  const limits = m.rateLimits.length > 0 ? m.rateLimits.map(l => ({ ...l })) : (prev?.limits ?? [])

  let samples = (prev?.samples ?? []).filter(s => now - s.t <= BURN_WINDOW)
  for (const l of m.rateLimits) {
    const mine = samples.filter(s => s.kind === l.kind)
    const last = mine[mine.length - 1]
    if (last && l.percentUsed + 0.5 < last.p) {
      samples = samples.filter(s => s.kind !== l.kind) // the window reset
    }
    samples.push({ kind: l.kind, t: now, p: l.percentUsed })
  }

  return { window: m.context.window, ctx: nextCtx, limits, samples: samples.slice(-MAX_SAMPLES) }
}

export type ContextOutlook = {
  tokens: number
  pct: number
  perTurn?: number
  turnsLeft?: number
  spark: string
}

const BARS = '▁▂▃▄▅▆▇█'

/** Bars scaled to the samples' own range, so small moves still show. */
export function sparkline(values: readonly number[]): string {
  if (values.length === 0) return ''
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo
  return values
    .map(v => BARS[span === 0 ? 0 : Math.min(7, Math.floor(((v - lo) / span) * 8))])
    .join('')
}

export function contextOutlook(w: Weather): ContextOutlook | undefined {
  const tokens = w.ctx[w.ctx.length - 1]
  if (tokens === undefined || w.window <= 0) return undefined

  const deltas: number[] = []
  for (let i = 1; i < w.ctx.length; i++) {
    const d = (w.ctx[i] ?? 0) - (w.ctx[i - 1] ?? 0)
    if (d > 0) deltas.push(d)
  }
  const recent = deltas.slice(-4)
  const perTurn = recent.length > 0 ? recent.reduce((a, b) => a + b, 0) / recent.length : undefined
  const turnsLeft = perTurn ? Math.max(0, Math.floor((w.window - tokens) / perTurn)) : undefined

  return {
    tokens,
    pct: Math.round((tokens / w.window) * 100),
    perTurn,
    turnsLeft,
    spark: sparkline(w.ctx.slice(-8)),
  }
}

export type LimitOutlook = {
  kind: string
  label: string
  pct: number
  resetInMs?: number
  /** Percent points per hour, from recent readings or the window's average pace. */
  ratePerHour?: number
  basis: 'burn' | 'pace' | 'none'
  /** Where the window lands at its reset at this rate (may pass 100). */
  atReset?: number
  /** Set when the limit is hit before the reset at this rate. */
  outInMs?: number
}

export function label(kind: string): string {
  if (kind === 'five_hour') return '5h'
  if (kind === 'seven_day') return 'week'
  if (kind === 'spend_limit') return 'spend'
  return kind.replace(/^seven_day_/, 'week ').replace(/_/g, ' ')
}

function burnRate(samples: readonly LimitSample[], kind: string): number | undefined {
  const mine = samples.filter(s => s.kind === kind)
  const first = mine[0]
  const last = mine[mine.length - 1]
  if (!first || !last || last.t - first.t < MIN_BURN_SPAN) return undefined
  return Math.max(0, ((last.p - first.p) / (last.t - first.t)) * HOUR)
}

export function limitOutlook(l: Limit, samples: readonly LimitSample[], now: number): LimitOutlook {
  const out: LimitOutlook = { kind: l.kind, label: label(l.kind), pct: l.percentUsed, basis: 'none' }
  const resetAt = l.resetsAt ? Date.parse(l.resetsAt) : NaN
  if (Number.isFinite(resetAt)) out.resetInMs = Math.max(0, resetAt - now)

  if (l.percentUsed >= 100) {
    out.outInMs = 0
    return out
  }
  if (out.resetInMs === undefined) return out

  const spec = WINDOWS[l.kind]
  // The 5-hour window moves fast enough that the last hour says more than its average.
  const burn = l.kind === 'five_hour' ? burnRate(samples, l.kind) : undefined
  if (burn !== undefined) {
    out.ratePerHour = burn
    out.basis = 'burn'
  } else if (spec) {
    const elapsed = spec.length - out.resetInMs
    if (elapsed >= spec.minElapsed) {
      out.ratePerHour = (l.percentUsed / elapsed) * HOUR
      out.basis = 'pace'
    }
  }

  if (out.ratePerHour !== undefined) {
    out.atReset = l.percentUsed + (out.ratePerHour * out.resetInMs) / HOUR
    if (out.atReset > 100 && out.ratePerHour > 0) {
      out.outInMs = ((100 - l.percentUsed) / out.ratePerHour) * HOUR
    }
  }
  return out
}

export type Sky = { glyph: string; level: 'fair' | 'watch' | 'warn' | 'storm' }

/** The worst of the context fill and the limits, as weather. */
export function sky(ctx: ContextOutlook | undefined, limits: readonly LimitOutlook[]): Sky {
  let pressure = ctx?.pct ?? 0
  for (const l of limits) {
    pressure = Math.max(pressure, l.pct, Math.min(l.atReset ?? 0, 99))
    if (l.outInMs !== undefined) pressure = 100
  }
  if (pressure >= 90) return { glyph: '⛈', level: 'storm' }
  if (pressure >= 75) return { glyph: '☂', level: 'warn' }
  if (pressure >= 50) return { glyph: '☁', level: 'watch' }
  return { glyph: '☀', level: 'fair' }
}

export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}k`
  return String(Math.round(n))
}

export function duration(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000))
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h${String(m % 60).padStart(2, '0')}m`
  return `${Math.floor(h / 24)}d${h % 24}h`
}

export function pctText(n: number): string {
  return `${Math.round(n)}%`
}
