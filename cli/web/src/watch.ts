import type { Panel, ShownPick } from './panel'

// The page reaches the watch's routes under /api/, carrying the cookie that
// its opening code bought. A POST of a click is never sent twice, because the
// watch reads a second one as a second click, which takes the first back.

/** What a route answered: its status, and its body, or why there is none. */
export type Answered<Body> = { ok: true; status: number; body: Body } | { ok: false; status: number; message: string }

async function ask<Body>(method: string, path: string, body?: unknown): Promise<Answered<Body>> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    return { ok: false, status: 0, message: 'the desk nudge watch did not answer' }
  }
  const text = await response.text()
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

/** A click, which the watch holds for a few seconds. On a key whose pick waits, it takes that pick back. */
export function click(key: string, minutes: number | null): Promise<Answered<ShownPick>> {
  return ask<ShownPick>('POST', '/pending-picks', { key, minutes })
}

/** How a pick stands, or how it ended, for a while after the panel stops listing it. */
export function readPick(id: string): Promise<Answered<ShownPick>> {
  return ask<ShownPick>('GET', `/pending-picks/${encodeURIComponent(id)}`)
}

/** Takes a waiting pick back. A resend answers as the first did, so one that got no answer is sent once more. */
export async function takeBack(id: string): Promise<Answered<ShownPick>> {
  const first = await ask<ShownPick>('DELETE', `/pending-picks/${encodeURIComponent(id)}`)
  if (first.ok || first.status !== 0) return first
  return ask<ShownPick>('DELETE', `/pending-picks/${encodeURIComponent(id)}`)
}

/** A pick that acts at once, such as an answer to the nudge. */
export function pickNow(key: string): Promise<Answered<unknown>> {
  return ask<unknown>('POST', '/picks', { key, minutes: null })
}

/** Asks webviewrs to close the window. A page in a plain browser has no ipc, and stays. */
export function closeWindow(): void {
  window.ipc?.postMessage('close')
}
