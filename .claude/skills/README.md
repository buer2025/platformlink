# 项目内置 Skill

从开源仓库挑选、审查后复制进来的 Claude Code skill，只保留文档（`.md`）和 security-audit 的本地校验脚本；
没有安装任何钩子、二进制或会联网的脚本。三个来源都是 MIT 协议，协议原文见 `LICENSES/`。

| Skill | 用途 | 来源 |
|---|---|---|
| security-audit | 安全审查方法（鉴权、注入、LLM/提示注入、供应链） | cloudflare/security-audit-skill @ c1c8a8c |
| systematic-debugging | 先找根因再修 | obra/superpowers @ 22f0b46 |
| test-driven-development | 先写失败的测试 | obra/superpowers @ 22f0b46 |
| verification-before-completion | 声称完成前必须跑验证 | obra/superpowers @ 22f0b46 |
| writing-plans | 多步任务先写计划 | obra/superpowers @ 22f0b46 |
| receiving-code-review | 对 review 意见先验证再改 | obra/superpowers @ 22f0b46 |
| fastapi-patterns | FastAPI / Pydantic v2 / 依赖注入 | affaan-m/everything-claude-code @ b38be6a |
| database-migrations | 安全可回滚的迁移（Alembic/Postgres） | 同上 |
| postgres-patterns | 索引、查询优化 | 同上 |
| error-handling | 错误类型、重试、熔断 | 同上 |
| eval-harness | 评测驱动开发（对应 `backend/evals`） | 同上 |
| prompt-caching-strategy | 提示词缓存命中率与成本 | 同上 |
| react-patterns | React 19 / TanStack Query | 同上 |
| frontend-a11y | 表单、键盘、读屏可访问性 | 同上 |
| make-interfaces-feel-better | 间距、阴影、动效、点击区域等细节 | 同上 |
| design-system | 设计 token 审计与一致性 | 同上 |

## 没装的

- **impeccable**：核心流程依赖首次运行时下载的二进制，还会往 `.claude/settings` 写编辑钩子。
- **hallmark**：面向营销落地页，依赖它自己的脚本，还有安装钩子；不适合后台管理界面。
- **superpowers** 的其余 skill（brainstorming、subagent-driven-development、using-superpowers 等）：
  偏重流程编排，和本项目工作方式重复。部分已装 skill 会引用 `superpowers:executing-plans` 等，
  这些引用可以忽略。
- 其余 ECC skill：与本项目的技术栈无关。
- t3code、claude-mem：不是 skill（一个是应用，一个是记忆服务）。

## 更新

重新从上述仓库对应路径复制 `.md` 文件，并更新本表里的提交号。
