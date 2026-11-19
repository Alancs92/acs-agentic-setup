// Live sessions, counted without a server or a lock: each session keeps one small
// file of its own in its account's config dir and rewrites it every minute, and
// the count is the files written lately. One writer per file, so nothing races.
// Pure: no `$`, so the tests drive it with plain values.

/** How often a session rewrites its file. */
export const BEAT_MS = 60_000
/** A file this recent is a live session: two and a half beats, since a beat can run late. */
export const FRESH_MS = 150_000
/** A file this old was left by a session that ended without cleaning up, and is removed. */
export const PRUNE_MS = 60 * 60_000

/** Only names this mod writes: no dots but the extension, so nothing else is counted or removed. */
const NAME = /^[A-Za-z0-9_-]{1,128}\.json$/

export type Entry = { name: string; kind: string; mtimeMs: number }

/** The folder for one account: its config dir when set, else ~/.claude. */
export function liveDir(configDir: string | undefined, home: string | undefined): string | undefined {
  const base = configDir?.replace(/\/+$/, '') || (home ? `${home.replace(/\/+$/, '')}/.claude` : undefined)
  return base ? `${base}/token-weather/live` : undefined
}

/** This session's file name, from its id. */
export function fileName(sessionId: string): string | undefined {
  const base = sessionId.replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 128)
  return base === '' ? undefined : `${base}.json`
}

function isOurs(e: Entry): boolean {
  return e.kind === 'file' && NAME.test(e.name)
}

export function liveCount(entries: readonly Entry[], now: number): number {
  return entries.filter(e => isOurs(e) && now - e.mtimeMs <= FRESH_MS).length
}

export function stale(entries: readonly Entry[], now: number): string[] {
  return entries.filter(e => isOurs(e) && now - e.mtimeMs > PRUNE_MS).map(e => e.name)
}
