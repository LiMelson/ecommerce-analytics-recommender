<script setup>
import { computed } from 'vue';
import { House, PanelsTopLeft, ChartNoAxesCombined, ChartColumn, ShoppingBag, Database,
  ScrollText, MessageSquare, Settings, UserRound, ChevronDown, PanelLeft, RefreshCw,
  X, ArrowUpRight, Download, Upload, Search, Users, Sparkles, Clock, ShieldCheck,
  Plus, Pencil, Trash2, Info, Check, CircleCheck, ChevronRight, ChevronLeft,
  Eye, ShoppingCart, Layers, SlidersHorizontal, CalendarDays, Bell, Maximize2,
  ArrowRight, Filter, FolderOpen, CircleHelp, Zap, FileSpreadsheet, LayoutGrid } from '@lucide/vue';
const props = defineProps({ name: { type: String, default: 'Layers' }, size: { type: Number, default: 18 } });
const icons = { House, PanelsTopLeft, ChartNoAxesCombined, ChartColumn, ShoppingBag, Database,
  ScrollText, MessageSquare, Settings, UserRound, ChevronDown, PanelLeft, RefreshCw,
  X, ArrowUpRight, Download, Upload, Search, Users, Sparkles, Clock, ShieldCheck,
  Plus, Pencil, Trash2, Info, Check, CircleCheck, ChevronRight, ChevronLeft,
  Eye, ShoppingCart, Layers, SlidersHorizontal, CalendarDays, Bell, Maximize2,
  ArrowRight, Filter, FolderOpen, CircleHelp, Zap, FileSpreadsheet, LayoutGrid };
const component = computed(() => icons[props.name] || Layers);
</script>
<template><component :is="component" :size="size" :stroke-width="1.75" aria-hidden="true" /></template>
