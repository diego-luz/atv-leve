"""Loading and validating device profiles (YAML)."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .adb import PACKAGE_RE

GENERIC_NAME = "_generic.yaml"
PLACEHOLDER = "TODO"
GLOB_CHARS = set("*?[")
ALLOWED_KEYS = {
    "brand", "model", "match", "android_release", "protected", "safe_to_disable",
    "confirm_first", "notes", "inventory", "device_info", "descriptions",
}


class ProfileError(Exception):
    pass


def project_root() -> Path | None:
    """Repository root when running from a source checkout (editable install)."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def profiles_dir() -> Path:
    env = os.environ.get("ATVLEVE_PROFILES")
    if env:
        return Path(env)
    root = project_root()
    if root and (root / "profiles").is_dir():
        return root / "profiles"
    return Path.cwd() / "profiles"


@dataclass
class Profile:
    path: Path
    brand: str = ""
    model: str = ""
    match: dict[str, list[str]] = field(default_factory=dict)
    android_release: str = ""
    protected: list[str] = field(default_factory=list)
    safe_to_disable: list[str] = field(default_factory=list)
    confirm_first: list[str] = field(default_factory=list)
    notes: str = ""
    descriptions: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    def matches(self, getprop) -> bool:
        """True when every non-empty `match` entry equals the device property."""
        criteria = {k: v for k, v in self.match.items() if v}
        if not criteria:
            return False
        return all(getprop(prop) in values for prop, values in criteria.items())


@dataclass
class RuleSet:
    protected: list[str]
    safe: set[str]
    confirm: set[str]
    profile_name: str = ""
    descriptions: dict[str, str] = field(default_factory=dict)

    def protected_match(self, name: str) -> str | None:
        for pattern in self.protected:
            if fnmatch.fnmatchcase(name, pattern):
                return pattern
        return None


def _clean_list(raw, key: str, path: Path, allow_glob: bool, warnings: list[str]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProfileError(f"{path.name}: '{key}' precisa ser uma lista")
    out = []
    for item in raw:
        if item is None:
            continue
        item = str(item).strip()
        if not item or item == PLACEHOLDER:
            continue
        has_glob = bool(GLOB_CHARS & set(item))
        if has_glob and not allow_glob:
            raise ProfileError(
                f"{path.name}: '{key}' aceita só nomes exatos, sem curinga: {item!r}"
            )
        check = item.replace("*", "x").replace("?", "x") if has_glob else item
        if not has_glob and not PACKAGE_RE.match(check):
            raise ProfileError(f"{path.name}: nome de pacote inválido em '{key}': {item!r}")
        if item in out:
            warnings.append(f"{path.name}: '{item}' repetido em '{key}'")
            continue
        out.append(item)
    return out


def load_profile(path: Path) -> Profile:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"não foi possível ler {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{path.name}: o arquivo precisa ser um mapa YAML")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise ProfileError(f"{path.name}: chaves desconhecidas: {', '.join(sorted(unknown))}")

    warnings: list[str] = []
    match_raw = data.get("match") or {}
    if not isinstance(match_raw, dict):
        raise ProfileError(f"{path.name}: 'match' precisa ser um mapa propriedade -> lista")
    desc_raw = data.get("descriptions") or {}
    if not isinstance(desc_raw, dict):
        raise ProfileError(f"{path.name}: 'descriptions' precisa ser um mapa pacote -> texto")
    match = {}
    for prop, values in match_raw.items():
        if values is None:
            values = []
        if not isinstance(values, list):
            values = [values]
        match[str(prop)] = [str(v) for v in values if v is not None and str(v) != PLACEHOLDER]

    profile = Profile(
        path=path,
        brand=str(data.get("brand") or ""),
        model=str(data.get("model") or ""),
        match=match,
        android_release=str(data.get("android_release") or ""),
        protected=_clean_list(data.get("protected"), "protected", path, True, warnings),
        safe_to_disable=_clean_list(data.get("safe_to_disable"), "safe_to_disable", path, False,
                                    warnings),
        confirm_first=_clean_list(data.get("confirm_first"), "confirm_first", path, False,
                                  warnings),
        notes=str(data.get("notes") or ""),
        descriptions={str(k): str(v) for k, v in desc_raw.items()},
        warnings=warnings,
    )
    both = set(profile.safe_to_disable) & set(profile.confirm_first)
    if both:
        raise ProfileError(
            f"{path.name}: pacotes em safe_to_disable e confirm_first ao mesmo tempo: "
            + ", ".join(sorted(both))
        )
    return profile


def load_generic(directory: Path | None = None) -> Profile:
    path = (directory or profiles_dir()) / GENERIC_NAME
    if not path.exists():
        raise ProfileError(
            f"Arquivo de regras genéricas não encontrado: {path}. Sem ele o ATV Leve não "
            "sabe o que proteger e se recusa a continuar."
        )
    generic = load_profile(path)
    if not generic.protected:
        raise ProfileError(f"{path.name}: a lista 'protected' não pode ficar vazia")
    return generic


def list_profiles(directory: Path | None = None) -> list[Profile]:
    directory = directory or profiles_dir()
    out = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == GENERIC_NAME:
            continue
        out.append(load_profile(path))
    return out


def find_profile(name_or_path: str, directory: Path | None = None) -> Profile:
    path = Path(name_or_path)
    if path.suffix in (".yaml", ".yml") and path.exists():
        return load_profile(path)
    directory = directory or profiles_dir()
    candidate = directory / f"{name_or_path}.yaml"
    if candidate.exists():
        return load_profile(candidate)
    available = ", ".join(p.name for p in list_profiles(directory)) or "nenhum"
    raise ProfileError(f"Perfil '{name_or_path}' não encontrado. Disponíveis: {available}")


def select_profile(getprop, directory: Path | None = None) -> Profile | None:
    matches = [p for p in list_profiles(directory) if p.matches(getprop)]
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        raise ProfileError(f"Mais de um perfil reconhece esta TV ({names}). Use --profile.")
    return matches[0] if matches else None


def build_rules(generic: Profile, profile: Profile | None) -> RuleSet:
    protected = list(generic.protected)
    safe: set[str] = set(generic.safe_to_disable)
    confirm: set[str] = set(generic.confirm_first)
    name = ""
    descriptions: dict[str, str] = dict(generic.descriptions)
    if profile:
        descriptions.update(profile.descriptions)
        protected += [p for p in profile.protected if p not in protected]
        safe |= set(profile.safe_to_disable)
        confirm |= set(profile.confirm_first)
        name = profile.name
    confirm -= safe
    return RuleSet(protected=protected, safe=safe, confirm=confirm, profile_name=name,
                   descriptions=descriptions)


def protected_conflicts(rules: RuleSet) -> list[tuple[str, str]]:
    """Entries listed as safe/confirm that a protection rule overrides (reported to the user)."""
    out = []
    for name in sorted(rules.safe | rules.confirm):
        pattern = rules.protected_match(name)
        if pattern:
            out.append((name, pattern))
    return out
