import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import type { Clock, HabitRow, Lane, LaneRow, Panel, PursuitRow, ShownPick, View } from './panel'

function laneRow(label: string): LaneRow {
  return { label, text: `what ${label} does`, note: 'never run', urgency: 'overdue', handle: `claude /${label}`, key: `review:${label}` }
}

function habit(id: number, name: string): HabitRow {
  return { id, name, done: false, key: `habit:${id}`, items: [] }
}

const chore: PursuitRow = {
  name: 'chore',
  description: '',
  unit: 'checkoffs',
  owed: true,
  note: 'owed 2',
  need: 1,
  done: 0,
  writes_through: false,
  usual_minutes: null,
  key: 'pursuit:chore',
  items: [],
  items_error: null,
}

function lane(name: string, rows: LaneRow[]): Lane {
  return { name, title: name.toUpperCase(), status: 'ok', reason: null, rows, total: rows.length }
}

const counting: Clock = {
  counted_minutes: 12,
  interval_minutes: 33,
  early: { answer: 'too_late', label: 'too late', key: 'answer:too_late:' },
  answered: null,
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
    pursuits: { status: 'ok', reason: null, rows: [structuredClone(chore)] },
    habits: { status: 'ok', reason: null, rows: [habit(1, 'Brush teeth'), habit(3, 'Drink water'), habit(4, 'Stretch')] },
    lanes: [lane('review', ['prune-images', 'check-disks', 'rotate-keys', 'renew-certs', 'read-logs'].map(laneRow))],
    problems: [],
  }
}

/**
 * The watch's routes as the page reaches them: a click commits in its own
 * request, and a pick that went through leaves the view at the watch's next
 * read, which ends after the page's first read of the panel. The panel lists
 * picks as the watch's shownPicks does: a done or uncertain one only until a
 * read begins after it ended.
 */
class FakeWatch {
  view = invented()
  readAt = new Date().toISOString()
  staleSince: string | null = null
  picks: ShownPick[] = []
  posted: string[] = []
  notes: (string | undefined)[] = []
  committed: string[] = []
  reads = 0
  drawnReads = 0
  private serial = 0
  private unread = false
  private held: Promise<void> | null = null
  release = () => {}

  /** Holds each pick's answer until release, as a pick whose command runs a while does. */
  holdPicks() {
    this.held = new Promise((resolve) => (this.release = resolve))
  }

  panel(): Panel {
    const panel: Panel = {
      schema_version: 3,
      read_at: this.readAt,
      stale_since: this.staleSince,
      stale_reason: this.staleSince ? { error: 'unreachable', detail: 'no answer from the doit service' } : null,
      view: structuredClone(this.view),
      nudge: null,
      clock: structuredClone(counting),
      picked: [],
      picks: structuredClone(this.listed()),
    }
    if (this.unread) this.readAgain()
    return panel
  }

  private listed(): ShownPick[] {
    const heldUntilRead = (pick: ShownPick) => pick.state === 'done' || pick.state === 'uncertain'
    return this.picks.filter((pick) => !(heldUntilRead(pick) && Date.parse(pick.ended_at!) < Date.parse(this.readAt)))
  }

  /** The watch's next read of the view, which counts each done pick in its rows. */
  readAgain() {
    this.unread = false
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
    if (method === 'POST' && path === '/api/panel/reads') {
      this.drawnReads++
      return json(200, this.panel())
    }
    if (method === 'POST' && path === '/api/picks') {
      const { key, minutes, note } = JSON.parse(String(init?.body)) as { key: string; minutes: number | null; note?: string }
      this.posted.push(key)
      this.notes.push(note)
      const pick: ShownPick = {
        id: `pick-${++this.serial}`,
        key,
        text: key,
        action: 'complete',
        state: 'committing',
        minutes,
        message: null,
        ended_at: null,
      }
      this.picks.push(pick)
      if (this.held) await this.held
      pick.state = 'done'
      pick.ended_at = new Date().toISOString()
      this.committed.push(key)
      this.unread = true
      return json(200, { schema_version: 3, step: 'done', message: key })
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
  it('a click shows its row committing until the watch answers, and a row whose pick runs ignores a second click', async () => {
    await open()
    watch.holdPicks()

    await row('habit:1').trigger('click')
    await flushPromises()
    expect(row('habit:1').classes()).toContain('committing')
    await row('habit:1').trigger('click')
    await flushPromises()
    expect(watch.posted).toEqual(['habit:1'])

    watch.release()
    await flushPromises()
    expect(watch.committed).toEqual(['habit:1'])
    expect(page.findAll('.tick.done').map((done) => done.text())).toContain('Brush teeth')
  })

  it('an open reads the view with a draw, offers too late before the nudge, and shows each command', async () => {
    await open()
    expect(watch.drawnReads).toBe(1)
    expect(page.get('.feedback .label').text()).toBe('Nudge in 21m')

    await row('answer:too_late:').trigger('click')
    await flushPromises()
    expect(watch.posted).toEqual(['answer:too_late:'])
    expect(page.get('[data-lane="review"] .command').text()).toBe('claude /prune-images')
  })

  it('a pursuit logs with the note typed beside it, which clears once the log goes through', async () => {
    await open()
    const field = page.get('input[aria-label="what the chore log was"]')
    await field.setValue('  vacuumed the house ')
    await row('pursuit:chore').trigger('click')
    await flushPromises()
    expect(watch.posted).toEqual(['pursuit:chore'])
    expect(watch.notes).toEqual(['vacuumed the house'])
    expect((field.element as HTMLInputElement).value).toBe('')
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
    expect(watch.committed).toEqual(['review:check-disks'])

    await vi.advanceTimersByTimeAsync(5_000)
    expect(watch.view.lanes[0]!.rows.map((shown) => shown.label)).not.toContain('check-disks')
    expect(laneLabels()).toEqual(before)
    expect(row('review:check-disks').classes()).toContain('done')
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
      clock: counting,
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
