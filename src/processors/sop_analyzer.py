"""
SOP 分析器 —— 纯逻辑，零副作用

职责：
1. 从文本中识别 SOP 步骤（序号模式匹配）
2. 识别决策点与分支逻辑
3. 生成结构化文档和 Mermaid 流程图
4. 不负责：文件 I/O、PDF 解析、checkpoint 管理
"""
from __future__ import annotations

import re


class SOPAnalyzer:
    """SOP 步骤识别与分析器"""

    @staticmethod
    def identify_steps(text: str) -> list[dict]:
        """识别 SOP 步骤"""
        steps = []

        # 模式1: "第X步：..."
        pattern = r'(?:第\s*([一二三四五六七八九十百千\d]+)\s*步[：:]\s*)(.*?)(?=第\s*(?:[一二三四五六七八九十百千\d]+)\s*步[：:]|\Z)'
        for match in re.finditer(pattern, text, re.DOTALL):
            steps.append({
                "number": match.group(1), "content": match.group(2).strip(),
                "start_pos": match.start(), "type": "numbered_step",
            })

        if not steps:
            pattern = r'(?:步骤\s*(\d+)[：:.、]\s*)(.*?)(?=步骤\s*\d+[：:.、]|\Z)'
            for match in re.finditer(pattern, text, re.DOTALL):
                steps.append({
                    "number": match.group(1), "content": match.group(2).strip(),
                    "start_pos": match.start(), "type": "numbered_step",
                })

        if not steps:
            pattern = r'(?:^|\n)\s*(\d+)[\.、．)）]\s+(.*?)(?=\n\s*\d+[\.、．)）]|\Z)'
            for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
                steps.append({
                    "number": match.group(1), "content": match.group(2).strip(),
                    "start_pos": match.start(), "type": "numbered_list",
                })

        if not steps:
            pattern = r'(?:^|\n)\s*([一二三四五六七八九十]+)[、．]\s*(.*?)(?=\n\s*[一二三四五六七八九十]+[、．]|\Z)'
            for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
                steps.append({
                    "number": match.group(1), "content": match.group(2).strip(),
                    "start_pos": match.start(), "type": "cn_numbered_list",
                })

        return steps

    @staticmethod
    def identify_decisions(text: str, steps: list[dict]) -> list[dict]:
        """识别决策点与分支"""
        decisions = []
        cond_patterns = [
            (r'(如果|若|当|假如|倘若|一旦)\s*([^，,；;。！!？?\n]{5,100})[，,；;。！!？?]', "condition"),
            (r'(否则|反之|不然|要不然)[，,。]?\s*([^，,；;。！!？?\n]{5,100})', "else_branch"),
            (r'(重复|再次|重新|循环)\s*([^，,；;。！!？?\n]{5,100})', "loop"),
        ]
        for pattern, d_type in cond_patterns:
            for match in re.finditer(pattern, text):
                decisions.append({
                    "type": d_type,
                    "keyword": match.group(1),
                    "content": match.group(2).strip() if match.lastindex and match.lastindex >= 2 else "",
                    "position": match.start(),
                    "context": text[max(0, match.start()-50):match.end()+50],
                })
        return decisions

    @staticmethod
    def generate_document(
        title: str, steps: list[dict], decisions: list[dict], original_text: str
    ) -> str:
        """生成结构化 SOP 文档（含 Mermaid 流程图）"""
        from datetime import datetime

        lines = [
            f"# {title} - 标准操作流程 (SOP)", "",
            f"> 自动提取时间: {datetime.now().isoformat()}",
            f"> 步骤数: {len(steps)}", f"> 决策点: {len(decisions)}",
            "", "---", "", "## 流程图", "", "```mermaid", "flowchart TD",
        ]

        if steps:
            lines.append("    Start([开始]) --> Step1")
            for i, step in enumerate(steps):
                step_id = f"Step{i+1}"
                content_preview = step["content"][:30]
                lines.append(f"    {step_id}[{step['number']}. {content_preview}...]")

                related = [d for d in decisions if abs(d["position"] - step["start_pos"]) < 500]
                for j, dec in enumerate(related):
                    dec_id = f"Dec{i+1}_{j+1}"
                    lines.append(f"    {step_id} --> {dec_id}{{{{ {dec['keyword']} }}}}")
                    lines.append(f"    {dec_id} --> |是| Next{i+1}")
                    lines.append(f"    {dec_id} --> |否| Alt{i+1}")

                if i < len(steps) - 1:
                    lines.append(f"    Step{i+1} --> Step{i+2}")
            lines.append(f"    Step{len(steps)} --> End([结束])")
        else:
            lines.append("    Start([开始]) --> End([结束])")

        lines.extend(["```", "", "## 详细步骤", ""])

        if steps:
            for step in steps:
                lines.extend([f"### 步骤 {step['number']}", "", step["content"], ""])
        else:
            lines.append("*(未识别到明确步骤，以下是原文关键段落)*")
            lines.append("")
            for i, para in enumerate([p.strip() for p in original_text.split("\n\n") if len(p.strip()) > 50][:10]):
                lines.extend([f"#### 段落 {i+1}", "", para, ""])

        if decisions:
            lines.extend(["## 决策表", "", "| 类型 | 条件 | 操作 |", "|------|------|------|"])
            d_type_map = {"condition": "条件判断", "else_branch": "否则分支", "loop": "循环操作"}
            for dec in decisions:
                lines.append(
                    f"| {d_type_map.get(dec['type'], dec['type'])} "
                    f"| {dec['keyword']} "
                    f"| {dec['content'][:50]} |"
                )
            lines.append("")

        lines.extend(["## 附录：原文摘要", "", original_text[:2000]])
        return "\n".join(lines)

    @staticmethod
    def merge_documents(
        sop_docs: list[dict], output_name: str
    ) -> tuple[str, str]:
        """合并多个 SOP 文档

        Returns:
            (merged_content, output_path_stem)
        """
        merged_lines = [
            f"# 批量 SOP 合集", "",
            f"> 合并文件数: {len(sop_docs)}",
            f"> 合并时间: {__import__('datetime').datetime.now().isoformat()}",
            "", "---", "", "## 目录", "",
        ]

        for i, doc in enumerate(sop_docs):
            title = doc.get("title", f"文档{i+1}")
            merged_lines.append(f"{i+1}. [{title}](#{title.lower().replace(' ', '-')})")

        merged_lines.extend(["", "---", ""])

        for doc in sop_docs:
            title = doc.get("title", "")
            content = doc.get("content", "")
            merged_lines.append(f"# {title}")
            merged_lines.append("")
            adjusted = re.sub(r'^(#+)', r'#\1', content, flags=re.MULTILINE)
            merged_lines.append(adjusted)
            merged_lines.extend(["", "---", ""])

        return "\n".join(merged_lines), f"{output_name}_合集"
