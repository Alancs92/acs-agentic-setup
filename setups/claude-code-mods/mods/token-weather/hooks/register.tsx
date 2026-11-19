import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import {
  type ContextOutlook,
  type LimitOutlook,
  contextOutlook,
  duration,
  limitOutlook,
  pctText,
  record,
  sky,
  tokens,
} from './forecast'
import { BEAT_MS, fileName, liveCount, liveDir, stale } from './live'

// Change the tag when Weather's fields change: it declines what an earlier
// version stored, which outlives a reload.
const weather = atom({ plugin: 'token-weather', key: 'weather' } as const, null, {
  shape: 'weather-1',
})
// Bumped once a minute so the reset countdowns stay current between turns.
const tick = atom({ plugin: 'token-weather', key: 'tick' } as const, 0)
// Sessions of this account beating lately, this one included; 0 when unknown.
const live = atom({ plugin: 'token-weather', key: 'live' } as const, 0)

type Part = { text: string; color?: string; dim?: boolean }
type Detail = 0 | 1 | 2

function pctColor(pct: number, isOut = false): string | undefined {
  if (isOut || pct >= 90) return 'error'
  if (pct >= 75) return 'warning'
  return undefined
}

function contextParts(c: ContextOutlook, glyph: Part, d: Detail): Part[] {
  const parts: Part[] = [glyph, { text: d === 0 ? ' ' : ' ctx ', dim: true }]
  parts.push({ text: pctText(c.pct), color: pctColor(c.pct) })
  if (d === 0) return parts
  if (c.spark.length > 1) parts.push({ text: ` ${c.spark}`, color: 'claude' })
  if (c.perTurn !== undefined) {
    parts.push({ text: ` +${tokens(c.perTurn)}${d === 2 ? '/turn' : '/t'}`, dim: true })
    if (d === 2 && c.turnsLeft !== undefined) {
      parts.push({ text: ` ~${c.turnsLeft} turns left`, color: pctColor(c.pct) ?? undefined, dim: c.pct < 75 })
    }
  }
  return parts
}

function limitParts(l: LimitOutlook, d: Detail): Part[] {
  const isOut = l.outInMs !== undefined
  const parts: Part[] = [
    { text: `${d === 0 && l.label === 'week' ? 'wk' : l.label} `, dim: true },
    { text: pctText(l.pct), color: pctColor(l.pct, isOut) },
  ]
  if (d === 0) {
    if (isOut) parts.push({ text: '!', color: 'error' })
    return parts
  }
  if (d === 2 && l.basis === 'burn' && l.ratePerHour !== undefined) {
    parts.push({ text: ` ↑${l.ratePerHour.toFixed(l.ratePerHour < 10 ? 1 : 0)}%/h`, dim: true })
  }
  if (l.outInMs !== undefined) {
    const text = l.outInMs === 0 ? ' limit hit' : ` out in ~${duration(l.outInMs)}`
    parts.push({ text, color: 'error' })
  } else if (l.atReset !== undefined) {
    const at = pctText(l.atReset)
    parts.push({ text: d === 2 ? ` → ~${at} at reset` : ` →${at}`, color: pctColor(l.atReset), dim: l.atReset < 75 })
  }
  if (l.resetInMs !== undefined) {
    parts.push({ text: d === 2 ? ` · resets ${duration(l.resetInMs)}` : ` ⟳${duration(l.resetInMs)}`, dim: true })
  }
  return parts
}

const widthOf = (parts: readonly Part[]) => parts.reduce((n, p) => n + [...p.text].length, 0)

/** The most detailed line that fits the band's width; the session count only where the full line leaves room. */
function line(ctx: ContextOutlook | undefined, limits: readonly LimitOutlook[], columns: number, sessions: number): Part[] {
  const s = sky(ctx, limits)
  const glyphColor = { fair: 'success', watch: undefined, warn: 'warning', storm: 'error' }[s.level]
  const glyph: Part = { text: s.glyph, color: glyphColor }

  let parts: Part[] = []
  for (const d of [2, 1, 0] as const) {
    const segments: Part[][] = []
    segments.push(ctx ? contextParts(ctx, glyph, d) : [glyph, { text: ' ctx —', dim: true }])
    for (const l of limits) segments.push(limitParts(l, d))
    parts = segments.flatMap((seg, i) => (i === 0 ? seg : [{ text: d === 0 ? ' · ' : '  │  ', dim: true }, ...seg]))
    if (widthOf(parts) <= columns) {
      if (d === 2 && sessions > 0) {
        const count: Part[] = [
          { text: '  │  ', dim: true },
          { text: `${sessions} live session${sessions === 1 ? '' : 's'}`, dim: true },
        ]
        if (widthOf(parts) + widthOf(count) <= columns) parts = [...parts, ...count]
      }
      break
    }
  }
  return parts
}

let folder: string | null | undefined // undefined: not looked up yet; null: none

async function liveFolder($: EngineInterface): Promise<string | undefined> {
  if (folder === undefined) folder = liveDir(await $.env.get('CLAUDE_CONFIG_DIR'), await $.env.get('HOME')) ?? null
  return folder ?? undefined
}

function remove($: EngineInterface, paths: string[]): Promise<unknown> {
  return $.process.run(['rm', '-f', ...paths], { timeoutMs: 4_000 }).catch(() => undefined)
}

/**
 * Rewrites this session's file, prunes ones long dead, and recounts. The id is read
 * each time: after a /clear the session goes on under a new one. Never throws: the
 * count is a nicety, and without it the band draws as before.
 */
async function beat($: EngineInterface): Promise<void> {
  try {
    const dir = await liveFolder($)
    const name = fileName(await $.session.id())
    if (!dir || !name) return
    const now = await $.clock.now()
    await $.fs.write(`${dir}/${name}`, JSON.stringify({ t: now }))
    const entries = await $.fs.list(dir)
    const old = stale(entries, now)
    if (old.length > 0) await remove($, old.map(n => `${dir}/${n}`))
    const n = liveCount(entries, now)
    await update($, live, () => n)
  } catch {
    await update($, live, () => 0).catch(() => undefined)
  }
}

export const register: Register = on => {
  let ticker: { cancel: () => void } | undefined

  on('session.start', async ($, e, next) => {
    const usage = await $.session.usage()
    const now = await $.clock.now()
    await update($, weather, w => record(w, usage, now))

    ticker?.cancel()
    ticker = $.clock.every(BEAT_MS, () => {
      void update($, tick, n => n + 1)
      void beat($)
    })
    void beat($)

    return next(e)
  })

  // The process goes on after a /clear, under a new id: the ticker keeps running and
  // its next beat writes the new file. Only this id's file goes.
  on('session.end', async ($, e, next) => {
    const dir = await liveFolder($).catch(() => undefined)
    const name = fileName(e.sessionId)
    if (dir && name) await remove($, [`${dir}/${name}`])
    return next(e)
  })

  on('session.measure', async ($, e, next) => {
    const now = await $.clock.now()
    await update($, weather, w => record(w, e, now))

    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)

    const w = await read($, weather)
    await read($, tick)
    const sessions = await read($, live)
    if (!w) return next(e)

    const now = await $.clock.now()
    const ctx = contextOutlook(w)
    const limits = w.limits.map(l => limitOutlook(l, w.samples, now))
    if (!ctx && limits.length === 0) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    const parts = line(ctx, limits, e.props.bodyColumns, sessions)

    return (
      <Box>
        <Text wrap="truncate-end">
          {parts.map(p => (
            <Text color={p.color} dimColor={p.dim && !p.color}>
              {p.text}
            </Text>
          ))}
        </Text>
      </Box>
    )
  })
}
