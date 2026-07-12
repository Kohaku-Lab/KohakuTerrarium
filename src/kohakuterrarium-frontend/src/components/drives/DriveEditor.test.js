/**
 * DriveEditor mounted interaction tests (coverage-and-verification §Product
 * surfaces). Pins two round-1 regressions:
 *
 *  - R1-35: an edit submits the revision the form was OPENED against, so a
 *    stale draft can conflict rather than silently pass CAS.
 *  - R1-38: a creature-scoped create must carry the picked creature id as
 *    ``scope_id`` (never the graph id), and the picker gates submission.
 */

import { mount } from "@vue/test-utils"
import { afterEach, describe, expect, it } from "vitest"
import ElementPlus, { ElSelect } from "element-plus"

import DriveEditor from "./DriveEditor.vue"

// el-dialog teleports its body to document.body, out of the wrapper's reach.
// Stub only the dialog shell (render both slots inline); every other el-*
// stays real so selects/inputs/buttons behave like production.
const ElDialogStub = {
  name: "ElDialog",
  template: `<div class="el-dialog-stub"><slot /><slot name="footer" /></div>`,
}

const mountOpts = {
  global: { plugins: [ElementPlus], stubs: { ElDialog: ElDialogStub } },
}

function mountEditor(props) {
  return mount(DriveEditor, { props: { modelValue: true, ...props }, ...mountOpts })
}

function buttonByText(w, text) {
  return w.findAll("button").find((b) => b.text().trim() === text)
}

afterEach(() => {
  document.body.innerHTML = ""
})

describe("DriveEditor — R1-35 revision threading", () => {
  it("edit mode emits save with the revision the form was opened against", async () => {
    const record = {
      drive_id: "d1",
      kind: "generic",
      title: "Original",
      priority: 2,
      scope_type: "graph",
      scope_id: "g1",
      revision: 4,
      presentation: {},
      spec: {},
      metadata: {},
    }
    const w = mountEditor({ mode: "edit", record })
    await w.vm.$nextTick()
    const save = buttonByText(w, "Save")
    expect(save).toBeTruthy()
    await save.trigger("click")
    const saved = w.emitted("save")
    expect(saved).toBeTruthy()
    // The captured base revision rides along unchanged — the panel forwards it
    // to the store which forwards it to the API.
    expect(saved[0][0].expectedRevision).toBe(4)
    expect(saved[0][0].patch.title).toBe("Original")
  })
})

describe("DriveEditor — R1-38 creature scope", () => {
  it("a creature-scoped create carries the picked creature id as scope_id", async () => {
    const w = mountEditor({
      mode: "create",
      kinds: ["generic"],
      creatures: [
        { creature_id: "c1", name: "Alice" },
        { creature_id: "c2", name: "Bob" },
      ],
    })
    await w.vm.$nextTick()

    // Title is required.
    await w.find('input[placeholder="What is this Drive for?"]').setValue("Scoped work")

    // Switch scope to creature via the scope select (kind[0], scope[1]).
    const selects = w.findAllComponents(ElSelect)
    selects[1].vm.$emit("update:modelValue", "creature")
    await w.vm.$nextTick()

    // The creature picker appears; choose c2.
    const picker = w
      .findAllComponents(ElSelect)
      .find((s) => s.attributes("data-testid") === "creature-picker")
    expect(picker).toBeTruthy()
    picker.vm.$emit("update:modelValue", "c2")
    await w.vm.$nextTick()

    await buttonByText(w, "Create").trigger("click")
    const created = w.emitted("create")
    expect(created).toBeTruthy()
    expect(created[0][0].scope_type).toBe("creature")
    expect(created[0][0].scope_id).toBe("c2")
  })

  it("disables Create for a creature scope until a creature is chosen", async () => {
    const w = mountEditor({
      mode: "create",
      kinds: ["generic"],
      creatures: [{ creature_id: "c1", name: "Alice" }],
    })
    await w.vm.$nextTick()
    await w.find('input[placeholder="What is this Drive for?"]').setValue("Needs a creature")
    w.findAllComponents(ElSelect)[1].vm.$emit("update:modelValue", "creature")
    await w.vm.$nextTick()

    const create = buttonByText(w, "Create")
    expect(create.attributes("disabled")).toBeDefined()
    // Clicking is a no-op — no create is emitted without a creature.
    await create.trigger("click")
    expect(w.emitted("create")).toBeFalsy()
  })

  it("a graph-scoped create omits scope_id so the panel defaults it to the graph", async () => {
    const w = mountEditor({ mode: "create", kinds: ["generic"], creatures: [] })
    await w.vm.$nextTick()
    await w.find('input[placeholder="What is this Drive for?"]').setValue("Graph work")
    await buttonByText(w, "Create").trigger("click")
    const created = w.emitted("create")
    expect(created).toBeTruthy()
    expect(created[0][0].scope_type).toBe("graph")
    expect(created[0][0].scope_id).toBeUndefined()
  })
})
