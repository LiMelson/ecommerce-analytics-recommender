<script setup>
import { computed } from 'vue';
defineEmits(['change']);
const props = defineProps({
  page: Number,
  total: Number,
  pageSize: { type: Number, default: 20 },
});
const pages = computed(() =>
  Math.max(1, Math.ceil(props.total / props.pageSize)),
);
</script>
<template>
  <div class="pagination">
    <span>共 {{ total }} 条 · 每页 {{ pageSize }} 条</span>
    <div>
      <button
        class="button small-button"
        :disabled="page <= 1"
        @click="$emit('change', page - 1)"
      >
        上一页</button
      ><span>{{ page }} / {{ pages }}</span
      ><button
        class="button small-button"
        :disabled="page >= pages || page >= 10000"
        @click="$emit('change', page + 1)"
      >
        下一页
      </button>
    </div>
  </div>
</template>
