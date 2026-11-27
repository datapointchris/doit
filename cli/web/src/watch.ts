import type { Panel } from './panel'

// The page reaches the watch's routes under /api/, carrying the cookie that
// its opening code bought. A POST of a pick is never sent twice, because one
// that arrives after the first ended is a second pick.

/** What a route answered: its status, and its body, or why there is none. */
export type Answered<Body> = { ok: true; status: number; body: Body } | { ok: false; status: number; message: string }

async function ask<Body>(method: string, path: string, body?: unknown): Promise<Answered<Body>> {
  let response: Response
  let text: string
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    text = await response.text()
  } catch {
    // A reply cut off after its headers says no more than none did.
    return { ok: false, status: 0, message: 'the desk nudge watch did not answer' }
  }
  let parsed: unknown = null
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }
  if (response.ok) return { ok: true, status: response.status, body: parsed as Body }
  const message =
    (parsed as { message?: string } | null)?.message ?? (text.trim() || `the desk nudge watch answered HTTP ${response.status}`)
  return { ok: false, status: response.status, message }
}

export function readPanel(): Promise<Answered<Panel>> {
  return ask<Panel>('GET', '/panel')
}

/** Has the watch read the view now, with a draw, as an open of the panel asks what is next, and answers the panel. */
export function readViewNow(): Promise<Answered<Panel>> {
  return ask<Panel>('POST', '/panel/reads')
}

/**
 * A pick, which the watch commits at once, answering the done step it leads to or why it did not go through.
 * A note says what a pursuit's log was, and the watch refuses one on any other pick.
 */
export function pick(key: string, minutes: number | null, note = ''): Promise<Answered<{ message: string | null }>> {
  return ask<{ message: string | null }>('POST', '/picks', note.trim() ? { key, minutes, note: note.trim() } : { key, minutes })
}

/** Asks webviewrs to close the window. A page in a plain browser has no ipc, and stays. */
export function closeWindow(): void {
  window.ipc?.postMessage('close')
}
