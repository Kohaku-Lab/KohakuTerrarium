import { describe, expect, it, vi } from "vitest"
import { installResumeImagesResolver, resumeWithImageChoices } from "./resumeImages"

function missingImages(detail = {}) {
  return { response: { data: { detail: { code: "missing_resume_images", ...detail } } } }
}

describe("resumeWithImageChoices", () => {
  it("only submits confirmations after explicit choices, scoped to the member", async () => {
    const choose = vi.fn().mockResolvedValueOnce("repair").mockResolvedValueOnce("retry")
    const uninstall = installResumeImagesResolver(choose)
    const resume = vi
      .fn()
      .mockRejectedValueOnce(missingImages({ member_id: "first", confirmation: "confirm-first" }))
      .mockRejectedValueOnce(missingImages({ member_id: "second", confirmation: "unused" }))
      .mockResolvedValue({ instance_id: "restored" })
    try {
      await expect(resumeWithImageChoices(resume)).resolves.toEqual({ instance_id: "restored" })
      expect(resume).toHaveBeenLastCalledWith({
        imageRepairConfirmation: undefined,
        memberImageRepairConfirmations: { first: "confirm-first" },
      })
      expect(choose).toHaveBeenCalledTimes(2)
    } finally {
      uninstall()
    }
  })

  it("cancels without retrying or sending a confirmation", async () => {
    const uninstall = installResumeImagesResolver(() => "cancel")
    const resume = vi.fn().mockRejectedValue(missingImages({ confirmation: "never" }))
    try {
      await expect(resumeWithImageChoices(resume)).resolves.toBeNull()
      expect(resume).toHaveBeenCalledTimes(1)
    } finally {
      uninstall()
    }
  })
})
