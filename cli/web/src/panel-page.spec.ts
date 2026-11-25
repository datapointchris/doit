import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import type { HabitRow, Lane, LaneRow, Panel, ShownPick, View } from './panel'

const HOLD_MS = 4_000

function laneRow(label: string): LaneRow {
  return { label, text: `what ${label} does`, note: 'never run', urgency: 'overdue', key: `review:${label}` }
}

function habit(id: number, name: string): HabitRow {
  return { id, name, done: false, key: `habit:${id}`, items: [] }
}

function lane(name: string, rows: LaneRow[]): Lane {
  return { name, title: name.toUpperCase(), status: 'ok', reason: null, rows, total: rows.length }
}

function invented(): View {
  return {
    generated_at: new Date().toISOString(),
    suggestion: null,
    suggestion_reason: 'no draw from the last 15 minutes stands',
    totals: {
      minutes: { need: 30, done: 0, behind: 120 },
      checkoffs: { need: 1, done: 0, behind: 4 },
      checklists: [{ name: 'habits', done: 0, total: 3, unavailable: '' }],
      unavailable: '',
    },
    pursuits: { status: 'ok', reason: null, rows: [] },
    habits: { status: 'ok', reason: null, rows: [habit(1, 'Brush teeth'), habit(3, 'Drink water'), habit(4, 'Stretch')] },
    lanes: [lane('review', ['prune-images', 'check-disks', 'rotate-keys', 'renew-certs', 'read-logs'].map(laneRow))],
    problems: [],
  }
}

/**
 * The watch's routes as the page reaches them: a click waits HOLD_MS, a second
 * POST of its key or a DELETE of its id takes it back, and a pick that went
 * through leaves the view at the watch's next read. The panel lists picks as
 * the watch's shownPicks does: never a canceled one, and a done or uncertain
 * one only until a read begins after it ended. GET answers any pick by id.
 */
class FakeWatch {
  view = invented()
  readAt = new Date().toISOString()
  staleSince: string | null = null
  picks: ShownPick[] = []
  committed: string[] = []
  deleted: string[] = []
  asked: string[] = []
  reads = 0
  private serial = 0

  panel(): Panel {
    this.commitDue()
    return {
      schema_version: 3,
      read_at: this.readAt,
      stale_since: this.staleSince,
      stale_reason: this.staleSince ? { error: 'unreachable', detail: 'no answer from the doit service' } : null,
      view: structuredClone(this.view),
      nudge: null,
      picked: [],
      picks: structuredClone(this.listed()),
    }
  }

  private listed(): ShownPick[] {
    const heldUntilRead = (pick: ShownPick) => pick.state === 'done' || pick.state === 'uncertain'
    return this.picks.filter(
      (pick) => pick.state !== 'canceled' && !(heldUntilRead(pick) && Date.parse(pick.ended_at!) < Date.parse(this.readAt)),
    )
  }

  private end(pick: ShownPick, state: ShownPick['state']) {
    pick.state = state
    pick.ended_at = new Date().toISOString()
  }

  /** A pick taken back by another client of the socket, such as a second window or a terminal's DELETE. */
  takeBackElsewhere(key: string) {
    const waiting = this.picks.find((pick) => pick.key === key && pick.state === 'pending')
    if (waiting) this.end(waiting, 'canceled')
  }

  private commitDue() {
    let read = false
    for (const pick of this.picks) {
      if (pick.state !== 'pending' || Date.parse(pick.commit_at) > Date.now()) continue
      this.end(pick, 'done')
      this.committed.push(pick.key)
      read = true
    }
    if (read) this.readAgain()
  }

  /** The watch's next read of the view, which counts each done pick in its rows. */
  readAgain() {
    const done = this.picks.filter((pick) => pick.state === 'done').map((pick) => pick.key)
    for (const row of this.view.habits.rows) if (row.key && done.includes(row.key)) Object.assign(row, { done: true, key: null })
    for (const shown of this.view.lanes) shown.rows = shown.rows.filter((row) => !done.includes(row.key ?? ''))
    this.readAt = new Date(Date.now() + 1).toISOString()
  }

  fetch = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    if (method === 'GET' && path === '/api/panel') {
      this.reads++
      return json(200, this.panel())
    }
    if (method === 'POST' && path === '/api/pending-picks') {
      const { key, minutes } = JSON.parse(String(init?.body)) as { key: string; minutes: number | null }
      const waiting = this.picks.find((pick) => pick.key === key && pick.state === 'pending')
      if (waiting) {
        this.end(waiting, 'canceled')
        return json(200, waiting)
      }
      const pick: ShownPick = {
        id: `pick-${++this.serial}`,
        key,
        text: key,
        action: 'complete',
        state: 'pending',
        minutes,
        commit_at: new Date(Date.now() + HOLD_MS).toISOString(),
        message: null,
        ended_at: null,
      }
      this.picks.push(pick)
      return json(201, pick)
    }
    const byID = path.match(/^\/api\/pending-picks\/(.+)$/)
    const pick = byID ? this.picks.find((shown) => shown.id === byID[1]) : undefined
    if (byID && !pick) return json(404, { exit_code: 2, message: 'no pick' })
    if (method === 'GET' && pick) {
      this.asked.push(pick.id)
      return json(200, pick)
    }
    if (method === 'DELETE' && pick) {
      this.deleted.push(pick.id)
      this.end(pick, 'canceled')
      return json(200, pick)
    }
    return json(404, { exit_code: 2, message: `no route ${method} ${path}` })
  }
}

let watch: FakeWatch
let page: VueWrapper

async function open() {
  page = mount(App, { attachTo: document.body })
  await flushPromises()
}

const row = (key: string) => page.get(`[data-key="${key}"]`)
const laneLabels = () => page.findAll('[data-lane="review"] .tick .label > div:first-child').map((label) => label.text())

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-24T17:00:00'))
  watch = new FakeWatch()
  vi.stubGlobal('fetch', watch.fetch)
})

afterEach(() => {
  page?.unmount()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the panel page against a fake watch', () => {
  it('a click drains then commits, and a second click cancels', async () => {
    await open()

    await row('habit:1').trigger('click')
    await flushPromises()
    expect(row('habit:1').classes()).toContain('pending')
    expect(row('habit:1').find('.drain').exists()).toBe(true)

    await vi.advanceTimersByTimeAsync(HOLD_MS + 500)
    expect(watch.committed).toEqual(['habit:1'])
    expect(page.findAll('.tick.done').map((done) => done.text())).toContain('Brush teeth')

    await row('habit:3').trigger('click')
    await flushPromises()
    expect(row('habit:3').classes()).toContain('pending')
    await row('habit:3').trigger('click')
    await flushPromises()
    expect(watch.deleted).toHaveLength(1)
    expect(row('habit:3').classes()).not.toContain('pending')

    await vi.advanceTimersByTimeAsync(HOLD_MS * 2)
    expect(watch.committed).toEqual(['habit:1'])
  })

  it('a fold shows its count, and a ticked row stays put', async () => {
    await open()
    const fold = page.get('[data-lane="review"] .more.fold')
    expect(fold.text()).toContain('2 more')
    expect(fold.attributes('aria-expanded')).toBe('false')
    await fold.trigger('click')
    expect(fold.attributes('aria-expanded')).toBe('true')
    const before = laneLabels()

    await row('review:check-disks').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(HOLD_MS + 500)
    expect(watch.committed).toEqual(['review:check-disks'])
    expect(watch.view.lanes[0]!.rows.map((shown) => shown.label)).not.toContain('check-disks')

    await vi.advanceTimersByTimeAsync(5_000)
    expect(laneLabels()).toEqual(before)
    expect(row('review:check-disks').classes()).toContain('done')
  })

  it('a pick that another client takes back is never told done', async () => {
    await open()
    await row('habit:1').trigger('click')
    await flushPromises()
    expect(row('habit:1').classes()).toContain('pending')

    watch.takeBackElsewhere('habit:1')
    await vi.advanceTimersByTimeAsync(HOLD_MS + 500)
    expect(watch.committed).toEqual([])
    expect(watch.asked).toHaveLength(1)
    expect(row('habit:1').classes()).not.toContain('pending')
    expect(page.get('[role="status"]').text()).not.toContain('done')
  })

  it('a desk whose watch has not read the view says why, and reads again', async () => {
    const detail = 'not configured: config.toml sets no api; run `doit auth login <url>`'
    vi.spyOn(watch, 'panel').mockImplementation(() => ({
      schema_version: 3,
      read_at: null,
      stale_since: new Date().toISOString(),
      stale_reason: { error: 'login', detail },
      view: null,
      nudge: null,
      picked: [],
      picks: [],
    }))
    await open()
    expect(page.get('.empty-board').text()).toContain(detail)
    const reads = watch.reads
    await vi.advanceTimersByTimeAsync(5_000)
    expect(watch.reads).toBeGreaterThan(reads)
  })

  it('a failed lane draws its reason', async () => {
    watch.view.lanes.push({ ...lane('practice', []), status: 'unavailable', reason: 'practice timed out after 10s' })
    await open()
    expect(page.get('[data-lane="practice"]').text()).toContain('practice timed out after 10s')
  })

  it('a return reloads, and a stale part is marked', async () => {
    await open()
    const reads = watch.reads
    expect(page.get('.topline').classes()).not.toContain('stale')

    watch.staleSince = new Date().toISOString()
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(watch.reads).toBe(reads + 1)
    expect(page.get('.topline .read').classes()).toContain('stale')
    expect(page.get('.topline .read').attributes('title')).toBe('no answer from the doit service')
  })
})
