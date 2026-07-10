"""
Obsidian 集成模块

功能：
1. 将生成的笔记自动保存到 Obsidian 仓库
2. 支持自定义子目录
3. 自动生成 frontmatter（tags、日期、来源等）
4. 支持 Wikilinks [[链接]] 格式
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..schemas.output import NoteOutput


class ObsidianIntegration:
    """Obsidian 仓库集成"""

    def __init__(self, vault_path: Path, default_subdir: str = "notes"):
        """
        Args:
            vault_path: Obsidian 仓库根目录
            default_subdir: 默认子目录
        """
        self.vault_path = vault_path.resolve()
        self.default_subdir = default_subdir

        if not self.vault_path.exists():
            raise ValueError(f"Obsidian 仓库不存在: {self.vault_path}")

    def get_target_dir(self, subdir: Optional[str] = None) -> Path:
        """获取目标目录"""
        target = self.vault_path / (subdir or self.default_subdir)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def save_note(
        self,
        note: NoteOutput,
        subdir: Optional[str] = None,
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
    ) -> Path:
        """保存笔记到 Obsidian 仓库

        Args:
            note: 笔记对象
            subdir: 子目录（可选）
            tags: 标签列表
            aliases: 别名列表

        Returns:
            保存的文件路径
        """
        target_dir = self.get_target_dir(subdir)

        # 清理文件名
        safe_name = self._sanitize_filename(note.title)
        file_path = target_dir / f"{safe_name}.md"

        # 生成 Obsidian 格式内容
        content = self._to_obsidian_format(note, tags, aliases)

        # 写入文件
        file_path.write_text(content, encoding="utf-8")

        return file_path

    def save_batch(
        self,
        notes: list[NoteOutput],
        subdir: Optional[str] = None,
        common_tags: list[str] | None = None,
    ) -> list[Path]:
        """批量保存笔记"""
        paths = []
        for note in notes:
            path = self.save_note(note, subdir, common_tags)
            paths.append(path)
        return paths

    def create_index(
        self,
        notes: list[NoteOutput],
        index_name: str = "笔记索引",
        subdir: Optional[str] = None,
    ) -> Path:
        """创建笔记索引页（MOC - Map of Content）"""
        target_dir = self.get_target_dir(subdir)
        index_path = target_dir / f"{index_name}.md"

        lines = [
            "---",
            f"title: {index_name}",
            f"date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "type: moc",
            "---",
            "",
            f"# {index_name}",
            "",
            f"> 共 {len(notes)} 篇笔记",
            "",
            "## 笔记列表",
            "",
        ]

        for note in notes:
            safe_name = self._sanitize_filename(note.title)
            source = note.source_info.get("source", "未知来源")
            source_type = note.source_info.get("type", "unknown")

            lines.append(f"- [[{safe_name}]] - {source_type} | {source}")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"*自动生成于 {datetime.now(timezone.utc).isoformat()}*")

        index_path.write_text("\n".join(lines), encoding="utf-8")
        return index_path

    def list_notes(self, subdir: Optional[str] = None) -> list[Path]:
        """列出仓库中的笔记"""
        target_dir = self.get_target_dir(subdir)
        return sorted(target_dir.glob("*.md"))

    def find_note(self, title: str, subdir: Optional[str] = None) -> Optional[Path]:
        """查找笔记"""
        safe_name = self._sanitize_filename(title)
        target_dir = self.get_target_dir(subdir)
        path = target_dir / f"{safe_name}.md"
        return path if path.exists() else None

    def _to_obsidian_format(
        self,
        note: NoteOutput,
        tags: list[str] | None = None,
        aliases: list[str] | None = None,
    ) -> str:
        """转换为 Obsidian 格式"""
        all_tags = note.source_info.get("tags", [])
        if tags:
            all_tags.extend(tags)
        all_tags = list(set(all_tags))  # 去重

        frontmatter = {
            "title": note.title,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "source": note.source_info.get("source", ""),
            "source_type": note.source_info.get("type", ""),
            "tags": all_tags,
        }

        if aliases:
            frontmatter["aliases"] = aliases

        # 手动构建 YAML frontmatter（避免依赖 PyYAML）
        fm_lines = ["---"]
        for key, value in frontmatter.items():
            if isinstance(value, list):
                fm_lines.append(f"{key}:")
                for item in value:
                    fm_lines.append(f"  - {item}")
            elif isinstance(value, str) and ":" in value:
                fm_lines.append(f'{key}: "{value}"')
            else:
                fm_lines.append(f"{key}: {value}")
        fm_lines.append("---")

        # 处理内容中的 Wikilinks
        content = self._convert_to_wikilinks(note.content)

        return "\n".join(fm_lines) + "\n\n" + content

    def _convert_to_wikilinks(self, content: str) -> str:
        """将 Markdown 链接转换为 Wikilinks 格式

        [text](path) → [[path|text]]
        """
        # 转换标准 Markdown 链接
        def replace_link(match):
            text = match.group(1)
            url = match.group(2)
            # 只转换内部链接（非 http）
            if url.startswith("http"):
                return match.group(0)
            # 去除文件扩展名
            url = re.sub(r'\.md$', '', url)
            if text != url:
                return f"[[{url}|{text}]]"
            return f"[[{url}]]"

        content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace_link, content)
        return content

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理文件名（移除非法字符）"""
        # Windows/macOS/Linux 兼容
        invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'
        name = re.sub(invalid_chars, '_', name)
        # 限制长度
        if len(name) > 200:
            name = name[:200]
        # 去除首尾空格和点
        name = name.strip('. ')
        return name if name else "untitled"

    def validate_vault(self) -> tuple[bool, str]:
        """校验 Obsidian 仓库有效性

        Returns:
            (是否有效, 描述信息)
        """
        if not self.vault_path.exists():
            return False, f"仓库路径不存在: {self.vault_path}"

        # 检查 .obsidian 目录（Obsidian 配置目录）
        obsidian_config = self.vault_path / ".obsidian"
        if not obsidian_config.exists():
            return True, "仓库路径有效（未检测到 .obsidian 配置目录，将创建）"

        return True, "Obsidian 仓库有效"
