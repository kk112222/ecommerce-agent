import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    // 内网穿透演示时，浏览器带的 Host 是隧道域名。vite 从 5.x 起会校验 Host 头，
    // 不在白名单里直接回 "Blocked request. This host is not allowed."（页面全白）。
    // 只放行隧道服务商的域名，不用 true（那等于谁的 Host 都收）。
    allowedHosts: ['.trycloudflare.com', '.lhr.life', '.localhost.run'],
    // 代理：把 /api 请求转发到 FastAPI 后端
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
