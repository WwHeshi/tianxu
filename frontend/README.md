# 天序前端

## 本地运行

```bash
cp .env.example .env.local
npm install
npm run dev
```

默认页面为 <code>http://localhost:3000</code>。<code>NEXT_PUBLIC_API_BASE_URL</code>
指向 FastAPI 服务，默认值为 <code>http://localhost:8000</code>。

主要页面：

- <code>/</code>：登录后的功能首页
- <code>/chart</code>：八字排盘、运势展示与 AI 分析报告
- <code>/chat/[[...conversationId]]</code>：普通或命盘绑定的多轮命理对话
- <code>/setup</code>、<code>/login</code>、<code>/change-password</code>：账户初始化、登录和修改密码
- <code>/admin/knowledge</code>、<code>/admin/graph</code>、<code>/admin/evaluations</code>、
  <code>/admin/users</code>：管理员功能

浏览器请求通过 HttpOnly Session Cookie 认证。

## 验证

```bash
npm run lint
npm run build
```
