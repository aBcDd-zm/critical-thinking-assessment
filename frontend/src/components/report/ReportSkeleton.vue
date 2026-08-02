<template>
  <div class="report-skeleton">
    <div class="skeleton-hero">
      <div class="skeleton-line skeleton-line-short"></div>
      <div class="skeleton-line skeleton-line-long"></div>
      <div class="skeleton-line skeleton-line-medium"></div>
    </div>

    <div class="report-panel skeleton-panel">
      <div class="skeleton-line skeleton-line-short"></div>
      <div
        v-for="i in 6"
        :key="i"
        class="skeleton-row"
      >
        <div class="skeleton-line skeleton-name"></div>
        <div class="skeleton-bar"></div>
      </div>
    </div>

    <div
      v-for="i in 3"
      :key="`card-${i}`"
      class="report-panel skeleton-panel"
    >
      <div class="skeleton-line skeleton-line-short"></div>
      <div class="skeleton-line skeleton-line-medium"></div>
      <div class="skeleton-line skeleton-line-long"></div>
    </div>
  </div>
</template>
