# GitHub Stars 知识库

> 自动抓取 GitHub Stars，生成 AI 摘要，便于检索。

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [Obsidian 同步（可选）](#obsidian-同步可选)
- [本地运行](#本地运行)

---

## 功能特性

- 🤖 自动抓取 GitHub 账号 Star 的全部仓库
- 📝 为每个仓库读取 README，调用 AI 生成**摘要**
- ⏭️ 增量更新，已处理的仓库**自动跳过**，不重复消耗 API
- ⏰ GitHub Actions **定时自动运行**，cron 表达式自由配置
- 🔄 可选：自动将 `stars.md` **推送到 Obsidian Vault 仓库**
- 🌐 支持任意 **OpenAI 格式兼容接口**（OpenAI / Azure / 本地 Ollama 等）

---

## 快速开始

### 第一步：Fork 本仓库

点击右上角 **Fork**，将本仓库复制到你自己的账号下。

### 第二步：配置 Secrets 和 Variables

进入 Fork 后仓库的 **Settings → Secrets and variables → Actions**，分两类配置：

**🔐 Secrets**（机密，加密保存）

| Secret 名称  | 说明                                                      | 必填 |
| ------------ | --------------------------------------------------------- | ---- |
| `AI_API_KEY` | AI 接口的 API Key                                         | ✅    |
| `VAULT_PAT`  | Vault 仓库的 Personal Access Token（仅 Vault 同步时需要） | ❌    |

> `GITHUB_TOKEN` 由 Actions 自动提供，无需手动添加。

**📋 Variables**（非机密，明文保存）

| Variable 名称        | 说明                                                           | 必填 |
| -------------------- | -------------------------------------------------------------- | ---- |
| `GH_USERNAME`        | 要抓取 Stars 的 GitHub 用户名                                  | ✅    |
| `AI_BASE_URL`        | AI 接口地址（OpenAI 兼容格式，如 `https://api.openai.com/v1`） | ✅    |
| `AI_MODEL`           | 模型名称（如 `gpt-4o-mini`），不填则用 `config.yml` 默认值     | ❌    |
| `VAULT_SYNC_ENABLED` | 是否启用同步到 Vault 仓库，填 `true` 开启                      | ❌    |
| `VAULT_REPO`         | Vault 仓库（`owner/repo-name` 格式）                           | ❌    |
| `VAULT_FILE_PATH`    | `stars.md` 在 Vault 仓库中的路径，默认 `GitHub Stars/stars.md` | ❌    |

### 第三步：按需修改 config.yml

`config.yml` 只包含非敏感的**行为配置**，通常不需要修改，默认即可直接运行。如需调整 AI 超时等参数，在此修改即可。

### 第四步：自定义定时频率

编辑 `.github/workflows/sync.yml`，修改 `cron` 表达式：

```yaml
schedule:
  - cron: "0 2 * * 1"  # 改为你想要的频率
```

> 💡 可使用 [crontab.guru](https://crontab.guru) 在线生成 cron 表达式

### 第五步：手动触发首次运行

进入 **Actions → 🌟 GitHub Stars 知识库同步 → Run workflow**，手动触发首次全量同步。

---

## 配置说明

所有非敏感配置均在 `config.yml` 中管理：

```yaml
ai:
  model: "gpt-4o-mini"         # AI 模型（可被 AI_MODEL Variable 覆盖）
  max_readme_length: 4000       # README 截取长度（避免超 Token）
  timeout: 60                   # 请求超时（秒）
  max_retries: 3                # 失败重试次数

output:
  file_path: "stars.md"         # 输出文件路径

vault_sync:
  # Vault 同步的开关和仓库名通过 Actions Variables 控制，此处仅配置默认路径和 commit 信息
  default_file_path: "GitHub Stars/stars.md"
  commit_message: "🤖 自动更新 GitHub Stars 摘要"
```

---

## Obsidian 同步（可选）

如果你想将 `stars.md` 自动同步到 Obsidian Vault：

1. 在 Vault 仓库所属账号创建一个 **Fine-grained Personal Access Token（PAT）**，赋予目标 Vault 仓库的 **Contents: Read and write** 权限

2. 将 PAT 添加为本仓库的 Secret：`VAULT_PAT`

3. 在仓库 **Settings → Secrets and variables → Actions → Variables** 中配置：

   | Variable 名称        | 示例值                              |
   | -------------------- | ----------------------------------- |
   | `VAULT_SYNC_ENABLED` | `true`                              |
   | `VAULT_REPO`         | `your-username/your-obsidian-vault` |
   | `VAULT_FILE_PATH`    | `GitHub Stars/stars.md`             |

4. 确保 Obsidian Git 插件开启了**定时 Pull**，每次 Action 运行后 Obsidian 会自动获取最新的 `stars.md`

---

## 本地运行

```bash
# 克隆仓库
git clone https://github.com/your-username/github-stars-summary.git
cd github-stars-summary

# 安装依赖
pip install -r requirements.txt

# ── 必填环境变量 ──
export GH_USERNAME="your-github-username"       # 要抓取 Stars 的 GitHub 用户名
export AI_BASE_URL="https://api.openai.com/v1"  # AI 接口地址
export AI_API_KEY="sk-..."                       # AI API Key

# ── 选填环境变量 ──
export AI_MODEL="gpt-4o-mini"     # 不填则用 config.yml 中的默认值
export GH_TOKEN="ghp_..."         # GitHub Token，不填也能运行，但 API 限速更严（60次/小时）

# 运行
python scripts/sync_stars.py
```

---

## 文件说明

| 文件                         | 说明                           |
| ---------------------------- | ------------------------------ |
| `config.yml`                 | 主配置文件（非敏感配置）       |
| `stars.md`                   | 自动生成的 Stars 知识库文档    |
| `.processed`                 | 已处理 repo 列表（防重复处理） |
| `scripts/sync_stars.py`      | 核心同步脚本                   |
| `.github/workflows/sync.yml` | GitHub Actions 定时工作流      |
