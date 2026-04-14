# 026 — Skill Scale Optimization

## Goal

提升 GavinAgent 在技能數量持續增長時的可管理性與執行效率，借鑒 Hermes-agent
的規模化設計，補足 GavinAgent 目前缺少的四個機制：分類目錄、介面過濾、
按需載入參考檔、以及技能啟用控制。

## Background

GavinAgent 技能數量已從初期的 7 個成長至 20+ 個（加計 Winbond skills repo
引入後），預期持續增長。目前的設計存在以下規模化瓶頸：

### 瓶頸 1 — 平面目錄，難以瀏覽

`agent/workspace/skills/` 所有技能並列，沒有分類。系統提示中的技能目錄
（Spec 022 catalog routing）會隨技能數成長線性膨脹，超過 50 個技能後
context 成本顯著上升。

### 瓶頸 2 — 沒有介面過濾機制

GavinAgent 有多個使用介面（Chat UI、MCP Copilot、Claude Code CLI），
不同介面適合不同技能集。例如 `skill-creator`、`mcp-builder` 只對
Claude Code 有意義，對 Telegram 等 gateway 完全不適用。
目前所有介面看到相同的技能清單。

### 瓶頸 3 — 參考檔不進 agent context

Spec 025 已計畫將 `scripts/`、`references/`、`assets/` 一起 sync 到
`~/.claude/skills/`，但 GavinAgent 自身的 agent loop（`loader.py`、
`nodes.py`）讀取技能時仍只載入 `SKILL.md` 主體，`references/` 裡的
補充文件從未進入 context——這與 Spec 023 識別的「Composio rules/ 未
跟隨」是同一類問題。

### 瓶頸 4 — 技能啟用狀態無法精細控制

目前只能透過刪除檔案或修改 embedding 來停用技能，沒有輕量的開關機制。
部署到新環境時也缺乏「預設停用高風險技能」的能力。

---

## Proposed Solution

### 元件 1 — 分類目錄結構

採用 Hermes-agent 的兩層目錄設計：

```
agent/workspace/skills/
├── cim/
│   ├── DESCRIPTION.md          ← 分類說明（選填）
│   ├── cim-eda/SKILL.md
│   ├── cim-fdc/SKILL.md
│   ├── cim-fabrpt/SKILL.md
│   └── cim-router/SKILL.md
├── fab-ops/
│   ├── DESCRIPTION.md
│   ├── fab-ops-analyst/SKILL.md
│   ├── dde-history/SKILL.md
│   └── issue-case-retriever/SKILL.md
├── general/
│   ├── charts/SKILL.md
│   ├── data-analysis/SKILL.md
│   └── web-research/SKILL.md
└── tooling/
    ├── mcp-builder/SKILL.md
    ├── skill-creator/SKILL.md
    └── spec-query/SKILL.md
```

`DESCRIPTION.md` 格式（YAML frontmatter + 說明段落）：

```markdown
---
name: cim
description: CIM 系統資料查詢技能集，涵蓋 EDA、FDC、FABRPT 等子系統。
---

包含對 CIM 各子系統的查詢路由與資料分析技能。
使用 cim-router 作為起點，由它判斷問題屬於哪個子系統再轉交。
```

**Discovery 相容性**：`Spec 023` 的 `all_skill_dirs()` 已使用 `rglob("SKILL.md")`
掃描，兩層結構無需修改掃描邏輯。`_get_category_from_path()` 參考
Hermes-agent 的實作，從路徑解析分類名稱：

```python
def _get_category_from_path(skill_path: Path, base_dir: Path) -> str | None:
    """
    skills/cim/cim-eda/SKILL.md → "cim"
    skills/charts/SKILL.md      → None（頂層技能，無分類）
    """
    try:
        rel = skill_path.relative_to(base_dir)
        if len(rel.parts) >= 3:   # category/skill-name/SKILL.md
            return rel.parts[0]
    except ValueError:
        pass
    return None
```

**系統提示 Tier 0 catalog**（擴充 Spec 022 catalog routing）：
在注入完整技能清單前，先注入分類摘要，讓 LLM 有全域視角：

```
Available skill categories:
- cim (4 skills): CIM 系統資料查詢，涵蓋 EDA、FDC、FABRPT 等子系統
- fab-ops (3 skills): 生產現況分析，Hold Lot、WIP、設備異常即時查詢
- general (3 skills): 通用工具，圖表、資料分析、網路搜尋
- tooling (3 skills): 開發輔助，MCP 建構、技能撰寫、規格查詢

Use skills_list(category=<name>) to see skills within a category.
```

---

### 元件 2 — `platforms:` 介面過濾

在 SKILL.md frontmatter 新增 `platforms` 欄位，限定技能在哪些 GavinAgent
介面出現。未填寫表示所有介面皆可用。

```yaml
---
name: mcp-builder
platforms: [claude_code]          # 只對 Claude Code 可見
---

---
name: fab-ops-analyst
platforms: [chat, copilot, telegram]   # 排除 Claude Code
---

---
name: charts
# platforms 省略 → 全介面皆可
---
```

有效平台識別符：

| 識別符 | 說明 |
|---|---|
| `chat` | GavinAgent Chat UI |
| `copilot` | MCP Copilot（VS Code / IDE） |
| `claude_code` | Claude Code CLI |
| `telegram` | Telegram gateway |
| `api` | 直接 API 呼叫 |

**過濾時機**：在 `embed_all_skills()`、`skill_catalog_for_prompt()`、
及 `sync_claude_code` 各自的掃描迴圈中，讀取 `platforms` 欄位後過濾。
每個入口傳入自身的平台識別符：

```python
# embeddings.py
embed_all_skills(platform="chat")

# sync_claude_code.py (_sync_skills)
# Claude Code 只取 platforms 包含 "claude_code" 或未設定的技能
```

**實作位置**：`agent/skills/discovery.py`（Spec 023 新增的模組）中加入：

```python
def skill_matches_platform(frontmatter: dict, platform: str | None) -> bool:
    platforms = frontmatter.get("platforms")
    if not platforms or platform is None:
        return True
    return platform in platforms
```

---

### 元件 3 — 參考檔按需載入（Tier 3）

當技能的 `references/` 目錄中有補充文件，`loader.py` 應在 agent context
中告知 LLM 這些檔案的存在，並在需要時透過工具載入，而非全部預載。

**SKILL.md 加入導引段落**（由 `loader.py` 自動附加，不需手動撰寫）：

```
[Supporting files available on demand:]
- references/catalogs.md
- references/column-mapping.md

To load any of these, call: skill_view("<skill-name>", "<file_path>")
```

`loader.py` 修改：

```python
def _append_supporting_files_hint(body: str, skill_dir: Path, skill_name: str) -> str:
    """掃描 references/、templates/、assets/，附加按需載入提示。"""
    BUNDLED_DIRS = ("references", "templates", "assets")
    files = []
    for subdir in BUNDLED_DIRS:
        sub = skill_dir / subdir
        if sub.is_dir():
            for f in sorted(sub.rglob("*")):
                if f.is_file():
                    files.append(str(f.relative_to(skill_dir)))
    if not files:
        return body
    hint_lines = ["", "[Supporting files available on demand:]"]
    hint_lines += [f"- {f}" for f in files]
    hint_lines.append(f'\nTo load: skill_view("{skill_name}", "<file_path>")')
    return body + "\n".join(hint_lines)
```

`skill_view` 工具已在 Spec 023 規劃，此處只需確保 `loader.py` 在載入
技能時呼叫 `_append_supporting_files_hint`。

---

### 元件 4 — 技能啟用控制

新增 Django 設定與管理介面，支援技能的全域停用與按平台停用。

#### 4a. 設定檔（`config/settings/base.py`）

```python
# 全域停用的技能名稱清單
AGENT_DISABLED_SKILLS: list[str] = config(
    "AGENT_DISABLED_SKILLS", default="", cast=Csv()
)

# 按平台停用，格式："platform:skill-a,skill-b;platform2:skill-c"
AGENT_PLATFORM_DISABLED_SKILLS: str = config(
    "AGENT_PLATFORM_DISABLED_SKILLS", default=""
)
```

#### 4b. `discovery.py` 過濾函式

```python
def get_disabled_skills(platform: str | None = None) -> set[str]:
    from django.conf import settings
    global_disabled = set(settings.AGENT_DISABLED_SKILLS)
    if platform is None:
        return global_disabled
    # 解析 "chat:skill-a,skill-b;copilot:skill-c"
    platform_map = _parse_platform_disabled(settings.AGENT_PLATFORM_DISABLED_SKILLS)
    return platform_map.get(platform, global_disabled)
```

#### 4c. Django Admin 擴充

在現有 Skills admin 頁面加入：
- **Enabled** 欄位（checkbox，預設 True）
- **Disabled platforms** 多選欄位
- 儲存後自動觸發 `embed_all_skills()` re-index

#### 4d. `sync_claude_code` 整合

停用的技能在 sync 時跳過，不寫入 `~/.claude/skills/`：

```python
disabled = get_disabled_skills(platform="claude_code")
if safe_name in disabled:
    self.stdout.write(f"  Skills: {name}  [skipped — disabled]")
    continue
```

---

## 與既有 Spec 的關係

| Spec | 關係 |
|---|---|
| 022 — Skill Routing | 元件 1 的 Tier 0 catalog 擴充 Spec 022 的 catalog routing |
| 023 — Multi-Source Discovery | 元件 2 的 `skill_matches_platform()` 加入 Spec 023 的 `discovery.py` |
| 025 — Anthropic Compliance | 元件 3 的 Tier 3 依賴 Spec 025 的 bundled dirs sync |

**實作順序**：Spec 023 → Spec 025 → Spec 026（本規格）

---

## Out of Scope

- Skills Hub 遠端 marketplace（私有 index 伺服器）
- 技能版本控制與回滾
- 分類層級的 embedding（目前只對個別技能做 embedding）
- 自動將現有平面技能目錄遷移至分類結構（手動搬移，不提供遷移腳本）

## Acceptance Criteria

**元件 1 — 分類目錄**
- [x] `_get_category_from_path()` 正確解析兩層路徑，回傳分類名稱或 `None`
- [x] `skill_catalog_for_prompt()` 先輸出 Tier 0 分類摘要，再輸出技能清單
- [x] `DESCRIPTION.md` 中的 description 欄位被採用為分類說明
- [x] 頂層技能（無分類）歸入 `null` 分類，不影響掃描

**元件 2 — 介面過濾**
- [x] `platforms: [claude_code]` 的技能不出現在 Chat UI 技能目錄
- [x] 未設定 `platforms` 的技能在所有介面皆可見
- [x] `sync_claude_code` 只 sync `platforms` 包含 `claude_code` 或未設定的技能
- [x] `embed_all_skills(platform="chat")` 不 embed `platforms` 排除 `chat` 的技能

**元件 3 — 按需載入**
- [x] 有 `references/` 的技能，agent context 中包含補充檔案清單提示
- [x] 無 `references/` 的技能，載入行為與現在相同（無迴歸）
- [x] `skill_view` 工具可成功載入 `references/` 下的個別檔案（由 Spec 023 `skill_view` 工具實作）

**元件 4 — 啟用控制**
- [x] `AGENT_DISABLED_SKILLS=skill-creator` 環境變數使該技能不被 embed 或 sync
- [ ] Django admin 的 Enabled checkbox 可停用技能並觸發 re-index（已有 DB `Skill.enabled` 欄位；admin 整合待後續）
- [x] 按平台停用不影響其他平台的技能可見性

## Open Questions

1. **分類遷移時機**：現有 20+ 個技能何時搬入分類目錄？建議在 Spec 025
   `import_skills` 實作完成後一併整理，避免 import 後又需重新搬移。

2. **Tier 0 catalog 的 token 預算**：分類摘要注入系統提示會增加固定 token
   成本。若分類數超過 15 個，摘要本身也需要截斷策略。

3. **`platforms` 欄位命名**：與 Hermes-agent 同名（`platforms`），但語意
   不同（Hermes 是 OS 平台，GavinAgent 是介面平台）。是否改用 `interfaces`
   避免混淆？

## Test Cases

測試檔位置：`tests/agent/test_skill_scale.py`
測試風格與 `test_sync_claude_code.py` / `test_skills.py` 一致：
pytest + `tmp_path`，純函式邏輯不起 Django server，需 DB 的用 `@pytest.mark.django_db`。

---

### 元件 1 — 分類目錄

```python
# ── _get_category_from_path ────────────────────────────────────────────────

class TestGetCategoryFromPath:
    def test_two_level_returns_category(self, tmp_path):
        """category/skill-name/SKILL.md → 'category'"""
        base = tmp_path / "skills"
        skill_md = base / "cim" / "cim-eda" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        assert _get_category_from_path(skill_md, base) == "cim"

    def test_one_level_returns_none(self, tmp_path):
        """skill-name/SKILL.md（頂層技能）→ None"""
        base = tmp_path / "skills"
        skill_md = base / "charts" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        assert _get_category_from_path(skill_md, base) is None

    def test_different_base_dir_returns_none(self, tmp_path):
        """base_dir 不匹配時不崩潰，回傳 None"""
        other_base = tmp_path / "other"
        skill_md = tmp_path / "skills" / "cim" / "cim-eda" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        assert _get_category_from_path(skill_md, other_base) is None

    def test_category_name_is_directory_name(self, tmp_path):
        """分類名稱取自目錄名，不是 DESCRIPTION.md 的 name 欄位"""
        base = tmp_path / "skills"
        skill_md = base / "fab-ops" / "fab-ops-analyst" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        assert _get_category_from_path(skill_md, base) == "fab-ops"


# ── skill_catalog_for_prompt ────────────────────────────────────────────────

class TestSkillCatalogForPrompt:
    def test_tier0_header_appears_before_skills(self, tmp_path):
        """Tier 0 分類摘要出現在個別技能清單之前"""
        # 建立：cim/cim-eda、cim/cim-fdc、general/charts
        _make_categorised_skills(tmp_path)
        catalog = skill_catalog_for_prompt(base_dir=tmp_path / "skills")
        tier0_pos = catalog.index("Available skill categories")
        charts_pos = catalog.index("charts")
        assert tier0_pos < charts_pos

    def test_category_description_from_description_md(self, tmp_path):
        """DESCRIPTION.md 的 description 欄位作為分類說明"""
        skills = tmp_path / "skills"
        cim = skills / "cim"
        cim.mkdir(parents=True)
        desc = "---\nname: cim\ndescription: CIM 核心查詢技能集\n---\n"
        (cim / "DESCRIPTION.md").write_text(desc, encoding="utf-8")
        _make_skill(skills, "cim/cim-eda")
        catalog = skill_catalog_for_prompt(base_dir=skills)
        assert "CIM 核心查詢技能集" in catalog

    def test_category_without_description_md_shows_name_only(self, tmp_path):
        """無 DESCRIPTION.md 時，分類仍顯示，說明為空"""
        skills = tmp_path / "skills"
        _make_skill(skills, "fab-ops/fab-ops-analyst")
        catalog = skill_catalog_for_prompt(base_dir=skills)
        assert "fab-ops" in catalog

    def test_uncategorised_skills_excluded_from_tier0(self, tmp_path):
        """頂層技能（無分類）不出現在 Tier 0 分類摘要中，但仍出現在技能清單"""
        skills = tmp_path / "skills"
        _make_skill(skills, "charts")          # 頂層，無分類
        _make_skill(skills, "cim/cim-eda")     # 有分類
        catalog = skill_catalog_for_prompt(base_dir=skills)
        # Tier 0 只列出 cim，不列 charts
        tier0_section = catalog.split("##")[0] if "##" in catalog else catalog
        assert "charts" not in tier0_section
        assert "cim" in tier0_section

    def test_skill_count_correct(self, tmp_path):
        """分類摘要中的技能數量正確"""
        skills = tmp_path / "skills"
        for i in range(3):
            _make_skill(skills, f"cim/skill-{i}")
        catalog = skill_catalog_for_prompt(base_dir=skills)
        assert "3 skill" in catalog    # "3 skills" 或 "3 skill(s)"

    def test_empty_skills_dir_returns_empty_string(self, tmp_path):
        """無技能時回傳空字串，不崩潰"""
        skills = tmp_path / "skills"
        skills.mkdir()
        assert skill_catalog_for_prompt(base_dir=skills) == ""
```

---

### 元件 2 — 介面過濾

```python
# ── skill_matches_platform ──────────────────────────────────────────────────

class TestSkillMatchesPlatform:
    def test_no_platforms_field_matches_all(self):
        """未設定 platforms → 任何 platform 都匹配"""
        assert skill_matches_platform({}, "chat") is True
        assert skill_matches_platform({}, "claude_code") is True
        assert skill_matches_platform({}, None) is True

    def test_platform_in_list_matches(self):
        """platform 在清單中 → True"""
        fm = {"platforms": ["chat", "copilot"]}
        assert skill_matches_platform(fm, "chat") is True
        assert skill_matches_platform(fm, "copilot") is True

    def test_platform_not_in_list_excludes(self):
        """platform 不在清單中 → False"""
        fm = {"platforms": ["claude_code"]}
        assert skill_matches_platform(fm, "chat") is False
        assert skill_matches_platform(fm, "telegram") is False

    def test_platform_none_always_matches(self):
        """platform=None（不指定）→ 不過濾，總是 True"""
        fm = {"platforms": ["claude_code"]}
        assert skill_matches_platform(fm, None) is True

    def test_empty_platforms_list_matches_all(self):
        """platforms: [] → 不限制（視為未設定）"""
        assert skill_matches_platform({"platforms": []}, "chat") is True


# ── sync_claude_code 平台過濾整合 ────────────────────────────────────────────

class TestSyncClaudeCodePlatformFilter:
    def test_claude_code_only_skill_is_synced(self, tmp_path):
        """platforms: [claude_code] 的技能會被 sync"""
        src = tmp_path / "workspace" / "skills"
        dst = tmp_path / "claude_skills"
        _make_skill(src, "mcp-builder",
                    frontmatter={"name": "mcp-builder",
                                 "description": "MCP builder",
                                 "platforms": ["claude_code"]})
        cmd = _make_sync_cmd()
        cmd._sync_skills(dst, dry_run=False,
                         skills_dir=src, platform="claude_code")
        assert (dst / "mcp-builder" / "SKILL.md").exists()

    def test_chat_only_skill_excluded_from_claude_code_sync(self, tmp_path):
        """platforms: [chat] 的技能不會被 sync 到 ~/.claude/skills/"""
        src = tmp_path / "workspace" / "skills"
        dst = tmp_path / "claude_skills"
        _make_skill(src, "fab-ops-analyst",
                    frontmatter={"name": "fab-ops-analyst",
                                 "description": "Fab ops",
                                 "platforms": ["chat", "copilot"]})
        cmd = _make_sync_cmd()
        cmd._sync_skills(dst, dry_run=False,
                         skills_dir=src, platform="claude_code")
        assert not (dst / "fab-ops-analyst").exists()

    def test_no_platforms_field_always_synced(self, tmp_path):
        """未設定 platforms 的技能在 claude_code sync 中也會出現"""
        src = tmp_path / "workspace" / "skills"
        dst = tmp_path / "claude_skills"
        _make_skill(src, "charts",
                    frontmatter={"name": "charts", "description": "Charts"})
        cmd = _make_sync_cmd()
        cmd._sync_skills(dst, dry_run=False,
                         skills_dir=src, platform="claude_code")
        assert (dst / "charts" / "SKILL.md").exists()
```

---

### 元件 3 — 參考檔按需載入

```python
# ── _append_supporting_files_hint ──────────────────────────────────────────

class TestAppendSupportingFilesHint:
    def test_no_bundled_dirs_body_unchanged(self, tmp_path):
        """無 references/templates/assets → 回傳原始 body，不附加任何內容"""
        skill_dir = tmp_path / "charts"
        skill_dir.mkdir()
        body = "## Instructions\n\nDo something."
        result = _append_supporting_files_hint(body, skill_dir, "charts")
        assert result == body

    def test_references_dir_appends_hint(self, tmp_path):
        """有 references/ → hint 段落附加在 body 末尾"""
        skill_dir = tmp_path / "cim-router"
        ref = skill_dir / "references"
        ref.mkdir(parents=True)
        (ref / "catalogs.md").write_text("# Catalogs", encoding="utf-8")
        body = "## Instructions"
        result = _append_supporting_files_hint(body, skill_dir, "cim-router")
        assert "[Supporting files available on demand:]" in result
        assert "references/catalogs.md" in result
        assert 'skill_view("cim-router"' in result

    def test_multiple_bundled_dirs_all_listed(self, tmp_path):
        """references/ + assets/ 同時存在 → 兩者都列出"""
        skill_dir = tmp_path / "mcp-builder"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "references" / "api.md").write_text("API", encoding="utf-8")
        (skill_dir / "assets").mkdir()
        (skill_dir / "assets" / "template.json").write_text("{}", encoding="utf-8")
        result = _append_supporting_files_hint("body", skill_dir, "mcp-builder")
        assert "references/api.md" in result
        assert "assets/template.json" in result

    def test_nested_files_in_references_listed_with_relative_path(self, tmp_path):
        """references/subdir/file.md → 以相對路徑列出"""
        skill_dir = tmp_path / "skill-a"
        nested = skill_dir / "references" / "sub"
        nested.mkdir(parents=True)
        (nested / "deep.md").write_text("Deep", encoding="utf-8")
        result = _append_supporting_files_hint("body", skill_dir, "skill-a")
        assert "references/sub/deep.md" in result

    def test_empty_references_dir_no_hint(self, tmp_path):
        """references/ 存在但為空 → 不附加 hint"""
        skill_dir = tmp_path / "skill-b"
        (skill_dir / "references").mkdir(parents=True)
        body = "## Instructions"
        result = _append_supporting_files_hint(body, skill_dir, "skill-b")
        assert result == body

    def test_hint_appended_after_body_content(self, tmp_path):
        """hint 永遠在 body 之後，不在 body 之前"""
        skill_dir = tmp_path / "skill-c"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "references" / "ref.md").write_text("ref", encoding="utf-8")
        body = "ORIGINAL BODY"
        result = _append_supporting_files_hint(body, skill_dir, "skill-c")
        assert result.index("ORIGINAL BODY") < result.index("[Supporting files")
```

---

### 元件 4 — 技能啟用控制

```python
# ── get_disabled_skills ────────────────────────────────────────────────────

class TestGetDisabledSkills:
    def test_global_disabled_from_settings(self, settings):
        """AGENT_DISABLED_SKILLS 設定回傳全域停用集合"""
        settings.AGENT_DISABLED_SKILLS = ["skill-creator", "mcp-builder"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        result = get_disabled_skills(platform=None)
        assert result == {"skill-creator", "mcp-builder"}

    def test_platform_specific_overrides_global(self, settings):
        """平台專屬設定存在時，覆蓋全域設定"""
        settings.AGENT_DISABLED_SKILLS = ["skill-a"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = "chat:skill-b,skill-c"
        result = get_disabled_skills(platform="chat")
        assert result == {"skill-b", "skill-c"}
        assert "skill-a" not in result

    def test_unknown_platform_falls_back_to_global(self, settings):
        """未設定平台專屬時，回退到全域清單"""
        settings.AGENT_DISABLED_SKILLS = ["skill-a"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = "chat:skill-b"
        result = get_disabled_skills(platform="telegram")
        assert result == {"skill-a"}

    def test_empty_settings_returns_empty_set(self, settings):
        """未設定任何停用技能 → 空集合"""
        settings.AGENT_DISABLED_SKILLS = []
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        assert get_disabled_skills() == set()


# ── _parse_platform_disabled ───────────────────────────────────────────────

class TestParsePlatformDisabled:
    def test_single_platform_single_skill(self):
        assert _parse_platform_disabled("chat:skill-a") == {"chat": {"skill-a"}}

    def test_single_platform_multiple_skills(self):
        result = _parse_platform_disabled("chat:skill-a,skill-b")
        assert result == {"chat": {"skill-a", "skill-b"}}

    def test_multiple_platforms(self):
        result = _parse_platform_disabled("chat:skill-a;copilot:skill-b,skill-c")
        assert result["chat"] == {"skill-a"}
        assert result["copilot"] == {"skill-b", "skill-c"}

    def test_empty_string_returns_empty_dict(self):
        assert _parse_platform_disabled("") == {}

    def test_malformed_entry_skipped_without_crash(self):
        """格式有誤的 entry 跳過，不崩潰"""
        result = _parse_platform_disabled("chat:skill-a;INVALID;copilot:skill-b")
        assert "chat" in result
        assert "copilot" in result


# ── collect_all_skills 與停用控制整合 ─────────────────────────────────────

class TestCollectAllSkillsDisabled:
    @pytest.mark.django_db
    def test_globally_disabled_skill_excluded(self, tmp_path, settings):
        """全域停用的技能不出現在 collect_all_skills() 結果中"""
        settings.AGENT_DISABLED_SKILLS = ["charts"]
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        skills = tmp_path / "skills"
        _make_skill(skills, "charts")
        _make_skill(skills, "weather")
        results = collect_all_skills()
        names = [s["name"] for s in results]
        assert "charts" not in names
        assert "weather" in names

    @pytest.mark.django_db
    def test_platform_disabled_skill_excluded_for_that_platform(self, tmp_path, settings):
        """chat 平台停用的技能在 platform='chat' 時不出現"""
        settings.AGENT_DISABLED_SKILLS = []
        settings.AGENT_PLATFORM_DISABLED_SKILLS = "chat:skill-creator"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        skills = tmp_path / "skills"
        _make_skill(skills, "skill-creator")
        _make_skill(skills, "weather")
        results = collect_all_skills(platform="chat")
        names = [s["name"] for s in results]
        assert "skill-creator" not in names
        assert "weather" in names

    @pytest.mark.django_db
    def test_platform_disabled_skill_visible_on_other_platform(self, tmp_path, settings):
        """chat 停用的技能在 platform='copilot' 仍可見"""
        settings.AGENT_DISABLED_SKILLS = []
        settings.AGENT_PLATFORM_DISABLED_SKILLS = "chat:skill-creator"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path)
        skills = tmp_path / "skills"
        _make_skill(skills, "skill-creator")
        results = collect_all_skills(platform="copilot")
        names = [s["name"] for s in results]
        assert "skill-creator" in names


# ── sync_claude_code 停用整合 ─────────────────────────────────────────────

class TestSyncClaudeCodeDisabled:
    def test_disabled_skill_not_written_to_claude_skills(self, tmp_path, settings):
        """全域停用的技能不寫入 ~/.claude/skills/"""
        settings.AGENT_DISABLED_SKILLS = ["charts"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        src = tmp_path / "workspace" / "skills"
        dst = tmp_path / "claude_skills"
        _make_skill(src, "charts")
        _make_skill(src, "weather")
        cmd = _make_sync_cmd()
        cmd._sync_skills(dst, dry_run=False, skills_dir=src)
        assert not (dst / "charts").exists()
        assert (dst / "weather" / "SKILL.md").exists()

    def test_disabled_skill_appears_in_dry_run_output_as_skipped(self, tmp_path, settings):
        """dry-run 時停用技能標示 [skipped — disabled]"""
        settings.AGENT_DISABLED_SKILLS = ["charts"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        src = tmp_path / "workspace" / "skills"
        dst = tmp_path / "claude_skills"
        _make_skill(src, "charts")
        cmd = _make_sync_cmd()
        cmd._sync_skills(dst, dry_run=True, skills_dir=src)
        output = cmd.stdout.getvalue()
        assert "skipped" in output.lower() or "disabled" in output.lower()
```

---

## Implementation Notes

**`agent/skills/discovery.py`**:
- `_get_category_from_path(skill_path, base_dir)` — extracts category from two-level path; returns `None` for flat layout
- `iter_skill_dirs(base)` — updated to scan one level deeper for category dirs (returns only dirs with SKILL.md)
- `skill_matches_platform(frontmatter, platform)` — filters by `platforms:` field; `None` platform = no filter; empty list = unset
- `_parse_platform_disabled(raw)` — parses `"chat:skill-a,skill-b;copilot:skill-c"` → `dict[str, set[str]]`
- `get_disabled_skills(platform)` — returns disabled set; platform-specific list overrides global
- `collect_all_skills()` — accepts `platform` kwarg; applies both platform and disabled filtering; adds `"category"` to returned dicts

**`agent/skills/loader.py`**:
- `_append_supporting_files_hint(body, skill_dir, skill_name)` — scans `references/`, `templates/`, `assets/` and appends Tier 3 hint block; called from `_parse_skill_md()`

**`agent/skills/embeddings.py`**:
- `embed_all_skills()` — accepts `platform` kwarg; uses `iter_skill_dirs()` (two-level aware) and applies platform + disabled filtering
- `skill_catalog_for_prompt(base_dir, platform)` — new function combining Tier 0 category summary + Tier 1 per-skill bullets; reads `DESCRIPTION.md` for category descriptions
- `build_skill_catalog()` — delegates to `skill_catalog_for_prompt()`; accepts `platform` kwarg
- `_read_category_description(category_dir)` — reads `description` field from `DESCRIPTION.md`

**`agent/management/commands/sync_claude_code.py`**:
- `_sync_skills()` — accepts `skills_dir` override (for testing) and `platform` kwarg (default `"claude_code"`); applies platform filtering and disabled skill skipping

**`config/settings/base.py`**:
- `AGENT_DISABLED_SKILLS` — `Csv()` env var, list of globally disabled skill names
- `AGENT_PLATFORM_DISABLED_SKILLS` — string env var, `"platform:skill-a,skill-b;..."` format

**Tests**: `tests/agent/test_skill_scale.py` — 39 test cases covering all 4 components; 70 total (31 Spec 025 + 39 Spec 026) all passing.
