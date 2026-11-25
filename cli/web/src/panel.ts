// The watch serves the panel document at /api/panel, in the shape that
// schemas/desk-nudge-panel.schema.json and schemas/desk-panel-view.schema.json
// describe. The page names only what it draws. Each vocabulary below is held
// to its schema enum by schema-vocabularies.spec.ts.

export const PICK_STATES = ['pending', 'committing', 'done', 'skipped', 'refused', 'uncertain', 'canceled'] as const
export type PickState = (typeof PICK_STATES)[number]
export const PICK_ACTIONS = ['complete', 'tick', 'untick'] as const
export const UNITS = ['minutes', 'checkoffs'] as const
export const URGENCIES = ['none', 'due', 'overdue'] as const
export const PART_STATUSES = ['ok', 'unavailable'] as const
export const CHECKLIST_NAMES = ['habits', 'review', 'practice'] as const

export interface ShownPick {
  id: string
  key: string
  text: string
  action: (typeof PICK_ACTIONS)[number]
  state: PickState
  minutes: number | null
  commit_at: string
  message: string | null
  ended_at: string | null
}

export interface Completes {
  kind: string
  ref: string
  item_id: string | null
}

export interface Choice {
  id: string | null
  title: string
  by: string
  key: string | null
  completes: Completes | null
}

export type Unit = (typeof UNITS)[number]
export type PartStatus = (typeof PART_STATUSES)[number]

export interface Suggestion {
  pursuit: string
  item_id: string | null
  label: string
  description: string
  unit: Unit
  writes_through: boolean
  usual_minutes: number | null
  need: number | null
  done: number | null
  behind: number | null
  key: string | null
  choices: Choice[]
  choices_error: string | null
}

export interface PursuitRow {
  name: string
  description: string
  unit: Unit
  owed: boolean
  note: string | null
  need: number | null
  done: number | null
  writes_through: boolean
  usual_minutes: number | null
  key: string | null
  items: Choice[]
  items_error: string | null
}

export interface ChecklistItem {
  id: number
  name: string
  target: number
  ticks: number
  done: boolean
  key: string | null
  untick_key: string | null
}

export interface HabitRow {
  id: number | null
  name: string
  done: boolean
  key: string | null
  items: ChecklistItem[]
}

export interface LaneRow {
  label: string
  text: string
  note: string
  urgency: (typeof URGENCIES)[number]
  key: string | null
}

export interface Lane {
  name: string
  title: string
  status: PartStatus
  reason: string | null
  rows: LaneRow[]
  total: number
}

export interface UnitTotals {
  need: number
  done: number
  behind: number
}

export interface Checklist {
  name: (typeof CHECKLIST_NAMES)[number]
  done: number | null
  total: number | null
  unavailable: string
}

export interface Part<Row> {
  status: PartStatus
  reason: string | null
  rows: Row[]
}

export interface View {
  generated_at: string
  suggestion: Suggestion | null
  suggestion_reason: string | null
  totals: {
    minutes: UnitTotals | null
    checkoffs: UnitTotals | null
    checklists: Checklist[]
    unavailable: string
  }
  pursuits: Part<PursuitRow>
  habits: Part<HabitRow>
  lanes: Lane[]
  problems: string[]
}

export interface NudgeAnswer {
  answer: string
  label: string
  key: string
}

export interface Panel {
  schema_version: number
  read_at: string | null
  stale_since: string | null
  stale_reason: { error: string; detail: string } | null
  view: View | null
  nudge: { nudge_id: string; answers: NudgeAnswer[] } | null
  picked: { key: string; at: string }[]
  picks: ShownPick[]
}

export type Look = 'idle' | 'pending' | 'committing' | 'done' | 'uncertain'

/**
 * How a row looks under its pick, and what the pick said where the row shows
 * it. A refused pick frees its row, so it looks idle and says why. A skipped
 * one found the habit done already.
 */
export function lookOf(pick: ShownPick | null, done: boolean): { look: Look; said: string | null } {
  switch (pick?.state) {
    case 'pending':
    case 'committing':
    case 'done':
    case 'uncertain':
      return { look: pick.state, said: pick.state === 'uncertain' ? pick.message : null }
    case 'skipped':
      return { look: 'done', said: pick.message }
    case 'refused':
      return { look: done ? 'done' : 'idle', said: pick.message }
  }
  return { look: done ? 'done' : 'idle', said: null }
}

/** A pick still running: waiting for its commit_at, or committing. */
export function underWay(pick: ShownPick): boolean {
  return pick.state === 'pending' || pick.state === 'committing'
}

/** The pick a row of key shows: the one under way, else the latest that ended. */
export function pickOf(picks: ShownPick[], key: string | null): ShownPick | null {
  if (key === null) return null
  const ofKey = picks.filter((pick) => pick.key === key)
  return ofKey.find(underWay) ?? ofKey.at(-1) ?? null
}

/**
 * A checklist item's sets as this page counts them: the ticks the view read,
 * and each tick or untick that went through since. A read that begins after a
 * pick ended counts it in the view and drops it from the picks, so none is counted twice.
 */
export function setsCounted(item: ChecklistItem, picks: ShownPick[]): number {
  let sets = item.ticks
  for (const pick of picks) {
    if (pick.state !== 'done') continue
    if (pick.key === item.key) sets += 1
    if (pick.key === item.untick_key) sets -= 1
  }
  return Math.max(sets, 0)
}

/** Minutes as `45m` or `1h 30m`. */
export function minutesText(minutes: number): string {
  const whole = Math.round(minutes)
  if (whole < 60) return `${whole}m`
  const hours = Math.floor(whole / 60)
  const left = whole % 60
  return left ? `${hours}h ${left}m` : `${hours}h`
}

/** The desk's day on its own clock, as YYYY-MM-DD. */
export function dayOf(moment: Date): string {
  const month = String(moment.getMonth() + 1).padStart(2, '0')
  const day = String(moment.getDate()).padStart(2, '0')
  return `${moment.getFullYear()}-${month}-${day}`
}

/** How long ago an instant was, as `just now`, `4m ago` or `2h ago`. */
export function ageText(instant: string, now: Date): string {
  const seconds = Math.max(0, (now.getTime() - Date.parse(instant)) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

/** A clock time on the desk, as 14:05. */
export function clockText(instant: string): string {
  return new Date(instant).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}
