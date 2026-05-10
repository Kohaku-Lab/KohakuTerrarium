/**
 * Smoke: the studio home module loads without throwing.
 *
 * The frontend's existing test harness doesn't mount full
 * components (see existing tests — they're store/util scoped),
 * so this just imports the SFC module and checks it parsed.
 * The bigger workspace-picker behavior test lands once the 4c
 * page is real (and once component-mount infra is set up).
 */

import { describe, expect, it } from "vitest"

describe("studio home placeholder", () => {
  it("module loads", async () => {
    const mod = await import("./StudioHomePage.vue")
    expect(mod.default).toBeTruthy()
  })
})
