<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart, BarChart, PieChart, FunnelChart, HeatmapChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, DataZoomComponent } from 'echarts/components';
use([CanvasRenderer, LineChart, BarChart, PieChart, FunnelChart, HeatmapChart, ScatterChart,
  GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, DataZoomComponent]);
const props = defineProps({ option: { type: Object, required: true }, height: { type: [String, Number], default: 320 }, label: { type: String, default: '数据图表' } });
const element = ref(null);
let chart, observer;
onMounted(() => {
  chart = init(element.value);
  chart.setOption(props.option);
  observer = new ResizeObserver(() => chart?.resize());
  observer.observe(element.value);
});
watch(() => props.option, option => chart?.setOption(option, { notMerge: true }), { deep: true });
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose(); });
</script>
<template><div ref="element" class="app-chart" :style="{ height: typeof height === 'number' ? `${height}px` : height }" role="img" :aria-label="label"></div></template>
