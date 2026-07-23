# 天序前端

## 本地运行

```bash
cp .env.example .env.local
npm install
npm run dev
```

默认页面为 `http://localhost:3000`。`NEXT_PUBLIC_API_BASE_URL` 指向 FastAPI 服务，默认值为 `http://localhost:8000`。

## 验证

```bash
npm run lint
npm run build
```
