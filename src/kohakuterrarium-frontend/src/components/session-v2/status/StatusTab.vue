<template>
  <div class="kt-v2-canvas h-full overflow-y-auto" data-test="v2-status-tab">
    <div class="max-w-6xl mx-auto px-6 py-5 flex flex-col gap-4">
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <SessionCard />
        <UsageCard />
        <HealthCard />
      </div>
      <AgentsTable />
      <div class="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
        <ChannelsTable />
        <DrivesTable />
      </div>
      <JobsTable />
    </div>
  </div>
</template>

<script setup>
import AgentsTable from "@/components/session-v2/status/AgentsTable.vue"
import ChannelsTable from "@/components/session-v2/status/ChannelsTable.vue"
import DrivesTable from "@/components/session-v2/status/DrivesTable.vue"
import HealthCard from "@/components/session-v2/status/HealthCard.vue"
import JobsTable from "@/components/session-v2/status/JobsTable.vue"
import SessionCard from "@/components/session-v2/status/SessionCard.vue"
import UsageCard from "@/components/session-v2/status/UsageCard.vue"

/** The v2 Status tab: one whole page of everything about the session — cards, then agents, channels, drives and jobs. */
</script>
