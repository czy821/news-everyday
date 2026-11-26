# 午间万象 · GitHub Pages 每日 12:00 自动更新版

本项目将《午间万象 · 每日时政茶馆》部署为 **GitHub Pages 固定公开网址**，并通过 **GitHub Actions** 每天中午 12:00 自动抓取最新国际与国内新闻、按既定比例生成当日内容并推送上线，全程零服务器成本。

## 目录结构

- `index.html`：单页响应式前端，自动加载 `./data/edition.json` 渲染当期内容（内置首期兜底数据）。
- `assets/hero.jpg`：包罗万象拼贴杂志风格头图。
- `data/edition.json`：每日 12:00 自动更新的当期 20 篇时政与趣闻数据。
- `scripts/update_news.py`：纯 Python 3 标准库抓取与改写脚本（零第三方依赖）。
- `.github/workflows/daily-update.yml`：GitHub Actions 每日定时任务配置。

## 5 分钟上线步骤

1. **新建 GitHub 仓库**：在 GitHub 创建一个公开仓库（例如 `noon-briefing`），将本压缩包内的所有文件（注意包含隐藏目录 `.github/workflows/daily-update.yml`）上传并推送到 `main` 分支。
2. **开启 GitHub Pages**：进入仓库 `Settings` → `Pages` → `Build and deployment`，选择 `Deploy from a branch`，分支选 `main`、目录选 `/ (root)`，保存后等待约 1 分钟即可获得固定网址 `https://<你的用户名>.github.io/<仓库名>/`。
3. **配置 AI 改写密钥（推荐）**：进入仓库 `Settings` → `Secrets and variables` → `Actions` → `New repository secret`，添加：
   - Name：`GEMINI_API_KEY`
   - Secret：你的 Gemini API Key（可在 Google AI Studio 免费申请）。
   > 未配置密钥时脚本也会自动抓取真实 RSS 新闻更新；配置后会自动改写为家常通俗长文并生成「对普通人 / 对世界」双维深度拆解。
4. **调整定时时区（可选）**：`.github/workflows/daily-update.yml` 默认每天 **北京时间 12:00（UTC 04:00）** 运行；如需改为美西时间 PST 12:00，将 cron 改为 `0 20 * * *` 即可。
5. **立即手动测试**：打开仓库顶部的 `Actions` 标签页，点击左侧 `Daily Noon Briefing Update`，点击右侧 `Run workflow` 即可立刻验证自动抓取与更新效果。
