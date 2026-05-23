import { configAPI } from "@/utils/api"

// Creature / terrarium config lists.  Re-fetched on every modal open
// (NewCreatureModal / NewTerrariumModal / AdvancedStartModal call
// fetchAll() on mount).  The earlier ``if (this.fetched) return``
// guard was a forever-cache: a user who opened the modal once before
// running ``kt install <pkg>`` would never see the newly installed
// creature configs without a full page reload.  The endpoint is
// already cheap — backend ``scan_creatures_in_dirs`` caches the disk
// walk for 10s and ``invalidate_scan_caches`` is fired by the install
// ops — so dropping the frontend guard is the right shape.
export const useConfigsStore = defineStore("configs", {
  state: () => ({
    /** @type {import('@/utils/api').ConfigItem[]} */
    creatures: [],
    /** @type {import('@/utils/api').ConfigItem[]} */
    terrariums: [],
    loading: false,
  }),

  actions: {
    async fetchAll() {
      if (this.loading) return
      this.loading = true
      try {
        const [creatures, terrariums] = await Promise.all([
          configAPI.listCreatures(),
          configAPI.listTerrariums(),
        ])
        this.creatures = creatures
        this.terrariums = terrariums
      } catch (err) {
        console.error("Failed to fetch configs:", err)
      } finally {
        this.loading = false
      }
    },
  },
})
