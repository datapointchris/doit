import { inject, reactive, type InjectionKey } from 'vue'
import { dayOf, pickOf, underWay, type Panel, type ShownPick } from './panel'
import * as watch from './watch'

/** How often a page that stays visible checks whether the desk's day changed. */
export const ROLLOVER_POLL_MS = 60_000
/** How often a committing pick is read again. A pending one is read just after its commit_at. */
const COMMITTING_POLL_MS = 1_000
/** How often an ended pick is read again, until the watch's next read of the view takes it in. */
const SETTLING_POLL_MS = 3_000
/** How long after a pick ends the page waits for that read before it stops asking. */
const SETTLING_FOR_MS = 30_000

/** A row as a section draws it, and whether the view has dropped it since this page drew it. */
export interface Placed<Row> {
  row: Row
  gone: boolean
}

export type PanelStore = ReturnType<typeof createPanelStore>

export const panelStoreKey: InjectionKey<PanelStore> = Symbol('panel store')

export function usePanelStore(): PanelStore {
  const store = inject(panelStoreKey)
  if (!store) throw new Error('the panel store is not provided')
  return store
}

export function createPanelStore(now: () => Date = () => new Date()) {
  const state = reactive({
    panel: null as Panel | null,
    /** Why the latest read of the panel got no answer, while the page shows the one before it. */
    unanswered: null as string | null,
    /** The desk's day that the page shows. */
    day: dayOf(now()),
    notice: null as string | null,
    /** Keys whose pick went through while this page was open today. */
    done: [] as string[],
  })
  // Each section's rows by identity, ordered as the page first drew them.
  const placed = new Map<string, Map<string, unknown>>()
  // The picks seen under way here by id, so that each one's end is told once.
  const seenUnderWay = new Map<string, ShownPick>()
  let reading: Promise<void> | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let noticeTimer: ReturnType<typeof setTimeout> | undefined
  let stopped = false

  function tell(message: string) {
    state.notice = message
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => (state.notice = null), 3_000)
  }

  function picks(): ShownPick[] {
    return state.panel?.picks ?? []
  }

  function markDone(key: string) {
    if (!state.done.includes(key)) state.done.push(key)
  }

  function apply(panel: Panel) {
    const listed = new Set(panel.picks.map((pick) => pick.id))
    for (const pick of panel.picks) {
      if (underWay(pick)) {
        seenUnderWay.set(pick.id, pick)
        continue
      }
      if (pick.state === 'done' || pick.state === 'skipped') markDone(pick.key)
      if (seenUnderWay.delete(pick.id)) tell(endedText(pick))
    }
    // The watch lists a pick that went through only until its next read of the
    // view, which may begin before this page's next read. A refused or skipped
    // pick stays listed, so a pick seen under way that left the list went through.
    for (const [id, pick] of seenUnderWay) {
      if (listed.has(id)) continue
      seenUnderWay.delete(id)
      markDone(pick.key)
      tell(endedText({ ...pick, state: 'done' }))
    }
    for (const held of panel.picked) markDone(held.key)
    state.panel = panel
    state.unanswered = null
  }

  function schedule() {
    clearTimeout(timer)
    const panel = state.panel
    if (stopped || !panel) return
    const moment = now().getTime()
    const readAt = panel.read_at ? Date.parse(panel.read_at) : 0
    const waits: number[] = []
    for (const pick of panel.picks) {
      if (pick.state === 'pending') waits.push(Math.max(Date.parse(pick.commit_at) - moment + 250, 250))
      else if (pick.state === 'committing') waits.push(COMMITTING_POLL_MS)
      else if (pick.ended_at && Date.parse(pick.ended_at) >= readAt && moment - Date.parse(pick.ended_at) < SETTLING_FOR_MS)
        waits.push(SETTLING_POLL_MS)
    }
    if (waits.length) timer = setTimeout(() => void load(), Math.min(...waits))
  }

  /** Reads the panel the watch keeps. A second call while one runs shares it. */
  function load(): Promise<void> {
    reading ??= (async () => {
      const answered = await watch.readPanel()
      if (answered.ok) apply(answered.body)
      else state.unanswered = answered.message
      schedule()
    })().finally(() => {
      reading = null
    })
    return reading
  }

  /** Reads again where the desk's day moved past the one shown, starting the day's rows afresh. */
  async function syncDay() {
    const today = dayOf(now())
    if (today === state.day) return
    state.day = today
    state.done = []
    placed.clear()
    await load()
  }

  function upsert(pick: ShownPick) {
    const panel = state.panel
    if (!panel) return
    panel.picks = [...panel.picks.filter((shown) => shown.id !== pick.id), pick]
    if (underWay(pick)) seenUnderWay.set(pick.id, pick)
  }

  function drop(id: string) {
    const panel = state.panel
    if (panel) panel.picks = panel.picks.filter((shown) => shown.id !== id)
    seenUnderWay.delete(id)
  }

  async function takeBack(pick: ShownPick) {
    const answered = await watch.takeBack(pick.id)
    if (answered.ok) {
      drop(pick.id)
      tell(`Took back ${pick.text}. Nothing was written.`)
    } else {
      tell(answered.message)
      await load()
    }
    schedule()
  }

  /**
   * One click on a row of key. A click on a row whose pick waits takes it back,
   * and any other starts a pick that the watch commits a few seconds later.
   */
  async function click(key: string, minutes: number | null = null) {
    const shown = pickOf(picks(), key)
    if (shown?.state === 'pending') return takeBack(shown)
    if (shown?.state === 'committing') {
      tell(`${shown.text} is committing, so it can no longer be taken back.`)
      return
    }
    const answered = await watch.click(key, minutes)
    if (!answered.ok) {
      tell(answered.message)
      // No answer leaves whether it landed unknown, and a second POST would be a second click.
      if (answered.status === 0) await load()
      return
    }
    if (answered.body.state === 'canceled') {
      drop(answered.body.id)
      tell(`Took back ${answered.body.text}. Nothing was written.`)
    } else {
      upsert(answered.body)
    }
    schedule()
  }

  /** An answer to the nudge, which acts at once. */
  async function answer(key: string, label: string) {
    const answered = await watch.pickNow(key)
    tell(answered.ok ? `Answered the nudge: ${label}.` : answered.message)
    await load()
  }

  /**
   * A section's rows, ordered as the page first drew them. When the view drops a
   * row whose pick went through on this page, the row stays in its place, drawn
   * done. Rows new to the view come after.
   */
  function steady<Row>(
    section: string,
    rows: Row[],
    identity: (row: Row) => string,
    keyOf: (row: Row) => (string | null)[],
  ): Placed<Row>[] {
    const before = placed.get(section) ?? new Map<string, unknown>()
    const fresh = new Map(rows.map((row) => [identity(row), row]))
    const kept = new Map<string, unknown>()
    const drawn: Placed<Row>[] = []
    for (const [id, row] of before) {
      const now = fresh.get(id)
      if (now !== undefined) {
        kept.set(id, now)
        drawn.push({ row: now, gone: false })
      } else if (keyOf(row as Row).some((key) => key !== null && state.done.includes(key))) {
        kept.set(id, row)
        drawn.push({ row: row as Row, gone: true })
      }
    }
    for (const [id, row] of fresh) {
      if (kept.has(id)) continue
      kept.set(id, row)
      drawn.push({ row, gone: false })
    }
    placed.set(section, kept)
    return drawn
  }

  function stop() {
    stopped = true
    clearTimeout(timer)
    clearTimeout(noticeTimer)
  }

  return { state, load, syncDay, click, answer, steady, stop, now }
}

function endedText(pick: ShownPick): string {
  switch (pick.state) {
    case 'done':
      return `${pick.text}: done.`
    case 'skipped':
      return `${pick.text}: ${pick.message ?? 'already done'}.`
    default:
      return pick.message ? `${pick.text}: ${pick.message}` : `${pick.text}: ${pick.state}.`
  }
}
