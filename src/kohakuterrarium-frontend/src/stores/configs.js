import { configAPI } from "@/utils/api"

export const useConfigsStore = defineStore("configs", {
  state: () => ({
    /** @type {import('@/utils/api').ConfigItem[]} */
    creatures: [],
    /** @type {import('@/utils/api').ConfigItem[]} */
    terrariums: [],
    loading: false,
    fetched: false,
  }),

  actions: {
    async fetchAll() {
      if (this.fetched) return
      this.loading = true
      try {
        const [creatures, terrariums] = await Promise.all([
          configAPI.listCreatures(),
          configAPI.listTerrariums(),
        ])
        this.creatures = creatures
        this.terrariums = terrariums
        this.fetched = true
      } catch (err) {
        console.error("Failed to fetch configs:", err)
      } finally {
        this.loading = false
      }
    },
  },
})
