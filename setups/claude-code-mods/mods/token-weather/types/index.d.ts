/** One rate-limit window as the engine reported it. */
export type Limit = { kind: string; percentUsed: number; resetsAt?: string }

/** One reading of a limit window, kept to measure its burn rate. */
export type LimitSample = { kind: string; t: number; p: number }

/** What the band draws from, refreshed at every session.measure. */
export type Weather = {
  /** The model's context window, in tokens. */
  window: number
  /** Context tokens after each turn, oldest first; reset on a compaction. */
  ctx: number[]
  /** The latest reading of each rate-limit window. */
  limits: Limit[]
  /** Recent readings, for a per-hour burn rate (pruned to the last hour). */
  samples: LimitSample[]
}

declare module 'claude-code' {
  interface PluginState {
    'token-weather': { weather: Shaped<Weather | null>; tick: number }
  }
}
