import { inject, reactive, type InjectionKey } from 'vue'
import { dayOf, pickOf, underWay, type Panel, type ShownPick } from './panel'
import * as watch from './watch'

/** How often a page that stays visible checks whether the desk's day changed. */
export const ROLLOVER_POLL_MS = 60_000
/** How often a pick that runs from elsewhere, such as a terminal, is read again. */
const COMMITTING_POLL_MS = 1_000
/** How often an ended pick is read again, until the watch's next read of the view takes it in. */
const SETTLING_POLL_MS = 3_000
/** How long after a pick ends the page waits for that read before it stops asking. */
const SETTLING_FOR_MS = 30_000
/** How often a panel whose watch has not read the view yet is read again. */
const VIEWLESS_POLL_MS = 5_000

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
    /**
     * Whether the latest read found no watch, or one that does not admit this
     * page. A restarted watch listens at a new address, so this window cannot
     * reach it again.
     */
    cutOff: false,
    /** The desk's day that the page shows. */
    day: dayOf(now()),
    notice: null as string | null,
    /** Keys whose pick went through while this page was open today. */
    done: [] as string[],
  })
  // Each section's rows by identity, ordered as the page first drew them.
  const placed = new Map<string, Map<string, unknown>>()
  // The picks the watch listed at the latest read.
  let listed: ShownPick[] = []
  // This page's clicks still waiting for the watch's answer, by key, each drawn committing.
  const clicks = new Map<string, ShownPick>()
  let clicked = 0
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

  /** The watch's picks, then each of this page's clicks that the watch does not list running. */
  function withClicks(picks: ShownPick[]): ShownPick[] {
    const running = (key: string) => picks.some((pick) => pick.key === key && underWay(pick))
    return [...picks, ...[...clicks.values()].filter((click) => !running(click.key))]
  }

  function redraw() {
    if (state.panel) state.panel.picks = withClicks(listed)
  }

  function apply(panel: Panel) {
    for (const pick of panel.picks) if (pick.state === 'done' || pick.state === 'skipped') markDone(pick.key)
    for (const held of panel.picked) markDone(held.key)
    listed = panel.picks
    panel.picks = withClicks(listed)
    state.panel = panel
    state.unanswered = null
    state.cutOff = false
  }

  function schedule() {
    clearTimeout(timer)
    const panel = state.panel
    if (stopped || !panel) return
    if (!panel.view) {
      timer = setTimeout(() => void load(), VIEWLESS_POLL_MS)
      return
    }
    const moment = now().getTime()
    const readAt = panel.read_at ? Date.parse(panel.read_at) : 0
    const waits: number[] = []
    for (const pick of panel.picks) {
      // A click's own answer reads the panel again, so only a pick from elsewhere is polled while it runs.
      if (pick.state === 'committing') {
        if (!clicks.has(pick.key)) waits.push(COMMITTING_POLL_MS)
      } else if (pick.ended_at && Date.parse(pick.ended_at) >= readAt && moment - Date.parse(pick.ended_at) < SETTLING_FOR_MS)
        waits.push(SETTLING_POLL_MS)
    }
    if (waits.length) timer = setTimeout(() => void load(), Math.min(...waits))
  }

  /** Reads the panel the watch keeps. A second call while one runs shares it. */
  function load(): Promise<void> {
    reading ??= (async () => {
      const answered = await watch.readPanel()
      if (answered.ok) apply(answered.body)
      else {
        state.unanswered = answered.message
        state.cutOff = answered.status === 0 || answered.status === 403
      }
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

  /**
   * One click on a row of key, which the watch commits in the request. A click
   * on a row whose pick still runs does nothing, so a double click is one pick.
   * No answer leaves whether it landed unknown, so the page reads the panel and
   * never sends the click again.
   */
  async function click(key: string, minutes: number | null = null) {
    if (clicks.has(key) || pickOf(picks(), key)?.state === 'committing') return
    clicks.set(key, {
      id: `click-${++clicked}`,
      key,
      text: key,
      action: 'complete',
      state: 'committing',
      minutes,
      message: null,
      ended_at: null,
    })
    redraw()
    const answered = await watch.pick(key, minutes)
    clicks.delete(key)
    redraw()
    if (answered.ok) markDone(key)
    await load()
    const ended = pickOf(picks(), key)
    if (ended && !underWay(ended)) tell(endedText(ended))
    else tell(answered.ok ? `Done: ${answered.body.message ?? key}.` : answered.message)
  }

  /** An answer to the nudge, which acts at once. */
  async function answer(key: string, label: string) {
    const answered = await watch.pick(key, null)
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
