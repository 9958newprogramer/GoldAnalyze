"""Load versioned Skill descriptors from disk."""

from __future__ import annotations

from pathlib import Path

from app.models import SkillDescriptor


class SkillRegistry:
    def __init__(self, skills_root: Path):
        self.skills_root = skills_root
        self._skills: dict[str, SkillDescriptor] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, SkillDescriptor] = {}
        for manifest_path in sorted(self.skills_root.glob("*/skill.json")):
            descriptor = SkillDescriptor.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if descriptor.name in loaded:
                raise ValueError(f"重复 Skill：{descriptor.name}")
            loaded[descriptor.name] = descriptor
        if not loaded:
            raise RuntimeError(f"未在 {self.skills_root} 找到 Skill Manifest")
        self._skills = loaded

    def get(self, name: str) -> SkillDescriptor:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Skill 未注册：{name}") from exc

    def list(self) -> list[SkillDescriptor]:
        return list(self._skills.values())
