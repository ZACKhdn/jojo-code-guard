# 啾啾代码守护仓库规则

本仓库是新建插件源码库，不按业务老项目处理。所有新建文本使用 UTF-8 无 BOM；默认使用 LF，`.bat`、`.cmd` 使用 CRLF，并用 `-text diff` 保留脚本字节。

- `skills/jojo-code-guard/` 是唯一的 Skill 源码；`~/.codex/skills/jojo-code-guard` 是本机运行副本。
- 修改 Skill 源码后，更新本机运行副本并确认 Codex 只发现一份 Skill。
- `.vscode/settings.json` 只约束本仓库开发环境，不复制到用户业务仓库。
- Codex manifest 不声明 Claude 的 hooks；Claude hook 仅由 Claude 插件结构加载。
- 同步脚本必须从 `skills/jojo-code-guard/` 复制共享 Skill，不能维护第二份脚本副本。
- 不执行全仓库自动格式化，不提交缓存、生成日志或本机插件缓存。
- 发布前验证 Codex marketplace 安装、Claude SessionStart hook、JSON、Python 语法、BOM 和换行。
- Windows 首次提交时使用 `git add --chmod=+x hooks/run-hook.cmd hooks/session-start`，确保 Unix 客户端可执行 Claude hook。
- 未经用户明确要求，不提交、不打标签、不 push。
