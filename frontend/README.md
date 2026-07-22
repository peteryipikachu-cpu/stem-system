# STEM 题目审核系统前端

本仓库保存 STEM 题目审核系统的 Next.js 前端；应用代码位于 `app/`。

## 本地开发

```bash
cd app
cp .env.example .env.local
npm ci
npm run dev
```

浏览器请求使用相对路径 `/api/*`。开发环境默认转发到 `http://localhost:8000`；如后端地址不同，可在启动前设置 `BACKEND_API_URL`。

```bash
BACKEND_API_URL=http://localhost:8000 npm run dev
```

## 检查

```bash
cd app
npm run lint
npm run build
```
