<template>
  <div class="provider-row">
    <div class="provider-row-main">
      <div class="provider-row-heading">
        <span class="provider-row-name" :title="identifier || name">{{ name }}</span>
        <el-tag size="small" effect="plain" :type="tone">{{ status }}</el-tag>
      </div>
      <p v-if="subtitle" class="provider-row-subtitle" :title="subtitle">{{ subtitle }}</p>
      <slot />
    </div>
    <div class="provider-row-actions"><slot name="actions" /></div>
  </div>
</template>
<script setup>
defineProps({ name: { type: String, required: true }, identifier: { type: String, default: "" }, status: { type: String, required: true }, subtitle: { type: String, default: "" }, tone: { type: String, default: "info" } })
</script>
<style scoped>
.provider-row {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
  padding: 1rem 0;
}
.provider-row + .provider-row {
  border-top: 1px solid var(--el-border-color-lighter);
}
.provider-row-main {
  flex: 1;
  min-width: 0;
}
.provider-row-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.65rem;
}
.provider-row-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}
.provider-row-subtitle {
  margin: 0.4rem 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
.provider-row-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
  max-width: 55%;
}
.provider-row-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
@media (max-width: 640px) {
  .provider-row {
    flex-direction: column;
    gap: 0.65rem;
  }
  .provider-row-actions {
    max-width: 100%;
    justify-content: flex-start;
  }
}
</style>
