import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { CHECKLIST_NAMES, PART_STATUSES, PICK_ACTIONS, PICK_STATES, UNITS, URGENCIES } from './panel'

// A value added to a schema enum fails here until the page's copy has it, as the Go watch fails its schema test.
function schema(name: string): Record<string, unknown> {
  const schemas = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'schemas')
  return JSON.parse(readFileSync(join(schemas, name), 'utf8')) as Record<string, unknown>
}

function enumAt(document: Record<string, unknown>, path: string): unknown {
  let at: unknown = document
  for (const step of path.split('.')) at = (at as Record<string, unknown>)[step]
  return (at as { enum: unknown }).enum
}

describe('the page names each value its schemas allow', () => {
  const panel = schema('desk-nudge-panel.schema.json')
  const view = schema('desk-panel-view.schema.json')

  it.each([
    ['pick states', PICK_STATES, panel, 'definitions.shown_pick.properties.state'],
    ['pick actions', PICK_ACTIONS, panel, 'definitions.shown_pick.properties.action'],
    ['units', UNITS, view, 'properties.pursuits.properties.rows.items.properties.unit'],
    ['urgencies', URGENCIES, view, 'definitions.lane_row.properties.urgency'],
    ['part statuses', PART_STATUSES, view, 'definitions.part_status'],
    ['checklist names', CHECKLIST_NAMES, view, 'properties.totals.properties.checklists.items.properties.name'],
  ])('%s', (_, values, document, path) => {
    expect([...values]).toEqual(enumAt(document, path))
  })
})
