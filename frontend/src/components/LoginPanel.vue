<script setup>
import { ref } from 'vue';
import AppIcon from './AppIcon.vue';
defineProps({ busy: Boolean, ready: Boolean, error: String });
const emit = defineEmits(['login']);
const username = ref('admin'),
  password = ref('');
function submit() {
  emit('login', { username: username.value, password: password.value });
}
</script>

<template>
  <main class="login-page">
    <div class="login-intro">
      <span class="brand-logo"
        ><AppIcon name="ChartNoAxesCombined" :size="30" /></span
      ><span class="eyebrow">COMMERCE INSIGHTS</span>
      <h1>读懂行为，<br />发现用户的下一步。</h1>
      <p>电商平台用户行为分析与精准推荐系统</p>
      <div class="login-features">
        <span>行为分析</span><span>用户画像</span><span>智能推荐</span>
      </div>
    </div>
    <section class="login-card panel">
      <span class="eyebrow">欢迎回来</span>
      <h2>登录分析工作台</h2>
      <p class="muted">使用管理员为你创建的系统账号。</p>
      <form @submit.prevent="submit">
        <label class="form-field"
          >账号<input
            v-model="username"
            autocomplete="username"
            maxlength="30"
            required
            :disabled="busy || !ready" /></label
        ><label class="form-field"
          >密码<input
            v-model="password"
            type="password"
            autocomplete="current-password"
            maxlength="128"
            required
            :disabled="busy || !ready"
        /></label>
        <p v-if="error" class="error-message" role="alert">{{ error }}</p>
        <button type="submit" class="button primary" :disabled="busy || !ready">
          {{ !ready ? '正在连接…' : busy ? '正在登录…' : '登录工作台'
          }}<AppIcon name="ArrowRight" :size="16" />
        </button>
      </form>
      <p class="section-note">
        首次管理员登录信息保存在本机
        backend/instance/first-admin.txt。请由组长创建团队账号。
      </p>
    </section>
  </main>
</template>
