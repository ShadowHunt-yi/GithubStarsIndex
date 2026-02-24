#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Stars 知识库同步脚本
功能：
  1. 从 GitHub API 抓取用户 Star 的项目列表
  2. 对新增项目的 README 调用 AI 生成中英文摘要
  3. 将结果写入本仓库的 stars.md
  4. 可选：将 stars.md 推送到 Obsidian Vault 仓库
"""

import os
import sys
import json
import time
import base64
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import yaml
from openai import OpenAI

# ── 日志配置 ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.parent  # 仓库根目录
CONFIG_PATH = SCRIPT_DIR / "config.yml"
PROCESSED_PATH = SCRIPT_DIR / ".processed"  # 已处理记录文件
STARS_MD_PATH_DEFAULT = SCRIPT_DIR / "stars.md"


# ════════════════════════════════════════════════════════════
# 配置加载
# ════════════════════════════════════════════════════════════


def load_config() -> dict:
    """加载 config.yml，并用环境变量覆盖敏感字段"""
    if not CONFIG_PATH.exists():
        log.error(f"配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 环境变量优先覆盖配置文件中的值
    # GitHub 用户名
    if os.environ.get("GH_USERNAME"):
        cfg["github"]["username"] = os.environ["GH_USERNAME"]

    # AI 配置
    if os.environ.get("AI_BASE_URL"):
        cfg["ai"]["base_url"] = os.environ["AI_BASE_URL"]
    if os.environ.get("AI_API_KEY"):
        cfg["ai"]["api_key"] = os.environ["AI_API_KEY"]
    if os.environ.get("AI_MODEL"):
        cfg["ai"]["model"] = os.environ["AI_MODEL"]

    # GitHub Token（用于提升 API 频率限制）
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        cfg["github"]["token"] = os.environ.get("GH_TOKEN") or os.environ.get(
            "GITHUB_TOKEN"
        )
    else:
        cfg["github"]["token"] = None

    # Vault 同步配置
    vault = cfg.get("vault_sync", {})
    if os.environ.get("VAULT_SYNC_ENABLED", "").lower() == "true":
        vault["enabled"] = True
    if os.environ.get("VAULT_REPO"):
        vault["repo"] = os.environ["VAULT_REPO"]
    if os.environ.get("VAULT_FILE_PATH"):
        vault["file_path"] = os.environ["VAULT_FILE_PATH"]
    if os.environ.get("VAULT_PAT"):
        vault["pat"] = os.environ["VAULT_PAT"]
    cfg["vault_sync"] = vault

    return cfg


# ════════════════════════════════════════════════════════════
# GitHub API 客户端
# ════════════════════════════════════════════════════════════


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, username: str, token: Optional[str] = None):
        self.username = username
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, url: str, params: dict = None) -> requests.Response:
        """带重试的 GET 请求"""
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                # 处理 GitHub API 限速
                if resp.status_code == 403 and "rate limit" in resp.text.lower():
                    reset_time = int(
                        resp.headers.get("X-RateLimit-Reset", time.time() + 60)
                    )
                    wait = max(reset_time - int(time.time()), 5)
                    log.warning(f"API 限速，等待 {wait} 秒...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                log.warning(f"请求失败（第 {attempt + 1} 次），重试中... {e}")
                time.sleep(2**attempt)

    def get_starred_repos(self) -> list[dict]:
        """获取用户全部 Star 的仓库列表（自动翻页）"""
        repos = []
        page = 1
        log.info(f"正在抓取 @{self.username} 的 Stars...")

        while True:
            url = f"{self.BASE_URL}/users/{self.username}/starred"
            # 按 created 倒序，最新 Star 在前
            resp = self._get(
                url,
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "created",
                    "direction": "desc",
                },
            )
            data = resp.json()

            if not data:
                break

            for item in data:
                repos.append(
                    {
                        "full_name": item["full_name"],
                        "name": item["name"],
                        "owner": item["owner"]["login"],
                        "description": item.get("description") or "",
                        "stars": item["stargazers_count"],
                        "language": item.get("language") or "N/A",
                        "url": item["html_url"],
                        "homepage": item.get("homepage") or "",
                        "topics": item.get("topics", []),
                        # starred_at 需要带特殊 Accept Header 才有，此处用 pushed_at 代替
                        "updated_at": item.get("pushed_at", "")[:10],
                    }
                )

            log.info(f"  第 {page} 页：获取 {len(data)} 个，共 {len(repos)} 个")

            # Link header 判断是否有下一页
            if "next" not in resp.headers.get("Link", ""):
                break
            page += 1

        log.info(f"共获取 {len(repos)} 个 Star")
        return repos

    def get_readme(self, full_name: str, max_length: int) -> str:
        """获取仓库 README 内容（截取指定长度）"""
        url = f"{self.BASE_URL}/repos/{full_name}/readme"
        try:
            resp = self._get(url)
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
            return content[:max_length]
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return ""
            log.warning(f"获取 README 失败 [{full_name}]: {e}")
            return ""
        except Exception as e:
            log.warning(f"解析 README 失败 [{full_name}]: {e}")
            return ""

    def push_file_to_repo(
        self, repo: str, file_path: str, content: str, commit_message: str, pat: str
    ) -> bool:
        """
        通过 GitHub API 将文件写入目标仓库
        repo: owner/repo-name 格式
        """
        url = f"{self.BASE_URL}/repos/{repo}/contents/{file_path}"
        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
        }

        # 先获取现有文件的 SHA（更新文件时需要）
        sha = None
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                sha = resp.json().get("sha")
        except Exception:
            pass

        payload = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha

        try:
            resp = requests.put(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            log.info(f"✅ 已推送至 Vault 仓库: {repo}/{file_path}")
            return True
        except Exception as e:
            log.error(f"❌ 推送 Vault 仓库失败: {e}")
            return False


# ════════════════════════════════════════════════════════════
# AI 摘要生成
# ════════════════════════════════════════════════════════════


class AISummarizer:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        self.model = model
        self.max_retries = max_retries
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )

    def summarize(self, repo_name: str, description: str, readme: str) -> dict:
        """
        为单个仓库生成中英文摘要
        返回: {"zh": "中文摘要", "en": "English summary"}
        """
        context = f"Repo: {repo_name}\nDescription: {description}\n\nREADME:\n{readme}"

        prompt = """你是一个技术文档分析专家。请根据以下 GitHub 仓库信息生成：
1. 一段专业的**中文摘要**（100字以内），准确描述该项目的核心功能、适用场景和技术亮点。
2. 一组**关键词标签**（5-8个），涵盖核心技术、用途等。

请严格按照以下 JSON 格式输出，不要有任何多余内容：
{
  "zh": "中文摘要内容",
  "tags": ["tag1", "tag2", ...]
}"""

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": context},
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                )
                result = json.loads(response.choices[0].message.content)
                return {
                    "zh": result.get("zh", "").strip(),
                    "tags": result.get("tags", []),
                }
            except json.JSONDecodeError:
                # 尝试从纯文本中提取
                raw = response.choices[0].message.content
                log.warning(f"JSON 解析失败，原始输出: {raw[:200]}")
                return {
                    "zh": "摘要生成失败",
                    "tags": [],
                }
            except Exception as e:
                if attempt == self.max_retries - 1:
                    log.error(f"AI 摘要生成失败 [{repo_name}]: {e}")
                    return {
                        "zh": "摘要生成失败",
                        "tags": [],
                    }
                log.warning(f"AI 请求失败（第 {attempt + 1} 次），重试中...")
                time.sleep(2**attempt)


# ════════════════════════════════════════════════════════════
# Markdown 生成
# ════════════════════════════════════════════════════════════


class MarkdownWriter:
    @staticmethod
    def render_repo_block(repo: dict, summary: dict) -> str:
        """渲染单个仓库的 Markdown 块 (Obsidian 优化版)"""
        # 提取 GitHub Topics
        topics_str = ""
        if repo.get("topics"):
            topics_str = " ".join(f"`#{t}`" for t in repo["topics"][:8])

        # 提取 AI Tags
        ai_tags_str = ""
        if summary.get("tags"):
            ai_tags_str = " ".join(f"`#{t}`" for t in summary["tags"])

        # 构建元数据行
        links = [f"[🔗 GitHub]({repo['url']})"]
        if repo.get("homepage"):
            links.append(f"[🌐 官网]({repo['homepage']})")
        meta_links = " | ".join(links)

        lines = [
            f"## {repo['full_name']}",
            f"> {meta_links}",
            f"> ⭐ **{repo['stars']:,}** · 🗣️ **{repo['language']}** · 🕐 **{repo['updated_at']}**",
            "",
            f"> {repo.get('description', '暂无描述')}",
            "",
            f"> [!abstract] AI 总结",
            f"> {summary['zh']}",
        ]

        # 增加话题和标签（合并展示更简洁）
        if ai_tags_str or topics_str:
            tags_line = "> "
            if ai_tags_str:
                tags_line += f"**AI 标签**: {ai_tags_str} "
            if topics_str:
                tags_line += f"**GitHub 话题**: {topics_str}"
            lines.append(tags_line)

        lines += [
            "",
            "---",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def build_header(total: int) -> str:
        """生成文档头部"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"""# ⭐ GitHub Stars 知识库

> 🤖 由 [GitHubStarIndex](https://github.com) 自动生成 · 最后更新：{now} · 共 **{total}** 个项目

---

"""

    @staticmethod
    def build_toc(repos: list[dict]) -> str:
        """生成目录（每10个一行）"""
        lines = ["## 📑 目录\n"]
        for i, repo in enumerate(repos):
            anchor = (
                repo["full_name"]
                .lower()
                .replace("/", "")
                .replace("-", "-")
                .replace("_", "_")
                .replace(".", "")
            )
            lines.append(f"- [{repo['full_name']}](#{anchor})")
        lines.append("\n---\n")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# 已处理记录管理
# ════════════════════════════════════════════════════════════


def load_processed() -> set:
    """加载已处理的 repo 列表"""
    if not PROCESSED_PATH.exists():
        return set()
    with open(PROCESSED_PATH, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_processed(processed: set):
    """保存已处理的 repo 列表"""
    with open(PROCESSED_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(processed)) + "\n")


def load_existing_entries(stars_md_path: Path) -> dict:
    """
    从现有 stars.md 中解析出已存在的 repo 条目内容
    返回: {full_name: markdown_block}
    """
    entries = {}
    if not stars_md_path.exists():
        return entries

    content = stars_md_path.read_text(encoding="utf-8")
    # 按 ## 分割各 repo 块
    parts = content.split("\n## ")
    for part in parts[1:]:  # 跳过文档头
        lines = part.strip().split("\n")
        if lines:
            # 提取 full_name（格式：[owner/repo](url)）
            first_line = lines[0]
            if "[" in first_line and "]" in first_line:
                full_name = first_line.split("[")[1].split("]")[0]
                entries[full_name] = "## " + part
    return entries


# ════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════


def main():
    log.info("=" * 60)
    log.info("GitHub Stars 知识库同步开始")
    log.info("=" * 60)

    # 1. 加载配置
    cfg = load_config()
    github_cfg = cfg["github"]
    ai_cfg = cfg["ai"]
    output_cfg = cfg["output"]
    vault_cfg = cfg.get("vault_sync", {})

    # 校验必要配置
    if not github_cfg.get("username"):
        log.error(
            "GitHub 用户名未配置（config.yml github.username 或 GH_USERNAME 环境变量）"
        )
        sys.exit(1)
    if not ai_cfg.get("base_url"):
        log.error("AI 接口地址未配置（AI_BASE_URL 环境变量）")
        sys.exit(1)
    if not ai_cfg.get("api_key"):
        log.error("AI API Key 未配置（AI_API_KEY 环境变量）")
        sys.exit(1)

    # 2. 初始化客户端
    gh = GitHubClient(github_cfg["username"], github_cfg.get("token"))
    ai = AISummarizer(
        base_url=ai_cfg["base_url"],
        api_key=ai_cfg["api_key"],
        model=ai_cfg["model"],
        timeout=ai_cfg.get("timeout", 60),
        max_retries=ai_cfg.get("max_retries", 3),
    )
    md_writer = MarkdownWriter()

    stars_md_path = SCRIPT_DIR / output_cfg.get("file_path", "stars.md")

    # 3. 加载已处理记录 & 现有 MD 条目
    processed = load_processed()
    existing_entries = load_existing_entries(stars_md_path)
    log.info(
        f"已处理记录: {len(processed)} 个，MD 中已有条目: {len(existing_entries)} 个"
    )

    # 4. 获取全量 Stars
    all_repos = gh.get_starred_repos()

    # 5. 过滤出新增的 repos
    new_repos = [r for r in all_repos if r["full_name"] not in processed]
    log.info(f"新增 Stars: {len(new_repos)} 个")

    # 6. 对新增 repos 生成摘要
    new_entries = {}
    for i, repo in enumerate(new_repos, 1):
        full_name = repo["full_name"]
        log.info("[{}/{}] 处理: {}".format(i, len(new_repos), full_name))

        readme = gh.get_readme(full_name, ai_cfg.get("max_readme_length", 4000))
        if not readme and not repo["description"]:
            log.warning("  → 无 README 和描述，使用默认摘要")
            summary = {
                "zh": "该项目暂无描述信息。",
                "en": "No description available for this project.",
                "tags": [],
            }
        else:
            summary = ai.summarize(full_name, repo["description"], readme)
            log.info("  → AI 摘要完成")

        block = md_writer.render_repo_block(repo, summary)
        new_entries[full_name] = block
        processed.add(full_name)

        # 避免 AI API 限速
        if i < len(new_repos):
            time.sleep(1)

    # 7. 合并所有条目（新条目在前，保持最新 Star 优先）
    all_entries = {}
    # 先放新条目（最新 Star 在前）
    for repo in new_repos:
        fn = repo["full_name"]
        if fn in new_entries:
            all_entries[fn] = new_entries[fn]
    # 再放已有条目
    for fn, block in existing_entries.items():
        if fn not in all_entries:
            all_entries[fn] = block

    # 8. 生成完整 stars.md
    log.info(f"生成 stars.md，共 {len(all_entries)} 个条目...")
    header = md_writer.build_header(len(all_entries))

    # 构建目录用的 repo 信息列表
    toc_repos = []
    for repo in all_repos:
        if repo["full_name"] in all_entries:
            toc_repos.append(repo)
    # 补充未在 all_repos 中的旧条目（理论上不会有，保险起见）
    existing_in_all = {r["full_name"] for r in toc_repos}
    for fn in all_entries:
        if fn not in existing_in_all:
            toc_repos.append({"full_name": fn})

    toc = md_writer.build_toc(toc_repos)
    body = "\n".join(all_entries.values())
    final_content = header + toc + body

    stars_md_path.write_text(final_content, encoding="utf-8")
    log.info(f"✅ stars.md 已写入: {stars_md_path}")

    # 9. 保存已处理记录
    save_processed(processed)
    log.info(f"✅ .processed 已更新，共 {len(processed)} 条")

    # 10. 可选：推送到 Vault 仓库
    if vault_cfg.get("enabled"):
        vault_repo = vault_cfg.get("repo", "")
        vault_file = vault_cfg.get("file_path", "GitHub Stars/stars.md")
        vault_pat = vault_cfg.get("pat", "")
        vault_msg = vault_cfg.get("commit_message", "🤖 自动更新 GitHub Stars 摘要")

        if not vault_repo or not vault_pat:
            log.error("Vault 同步已启用，但 VAULT_REPO 或 VAULT_PAT 未配置，跳过")
        else:
            log.info(f"正在推送到 Vault 仓库: {vault_repo}/{vault_file}")
            gh.push_file_to_repo(
                vault_repo, vault_file, final_content, vault_msg, vault_pat
            )
    else:
        log.info("Vault 同步未启用（vault_sync.enabled: false），跳过")

    log.info("=" * 60)
    log.info(f"同步完成！新增 {len(new_entries)} 个，总计 {len(all_entries)} 个")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
