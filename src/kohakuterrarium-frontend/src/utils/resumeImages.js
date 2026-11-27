let resolveChoice = null

export function installResumeImagesResolver(listener) {
  resolveChoice = listener
  return () => {
    if (resolveChoice === listener) resolveChoice = null
  }
}

export async function resumeWithImageChoices(resume) {
  let imageRepairConfirmation
  const memberImageRepairConfirmations = {}
  while (true) {
    try {
      return await resume({ imageRepairConfirmation, memberImageRepairConfirmations })
    } catch (error) {
      const report = error.response?.data?.detail
      if (report?.code !== "missing_resume_images" || !resolveChoice) throw error
      const action = await resolveChoice(report)
      if (action === "cancel") return null
      const confirmation = action === "repair" ? report.confirmation : undefined
      if (report.member_id) {
        if (confirmation) memberImageRepairConfirmations[report.member_id] = confirmation
        else delete memberImageRepairConfirmations[report.member_id]
      } else {
        imageRepairConfirmation = confirmation
      }
    }
  }
}
