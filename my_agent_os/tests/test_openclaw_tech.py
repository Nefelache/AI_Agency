"""
新模块集成测试套件 — 验证所有 OpenClaw 技术方案的 Python 实现。

覆盖:
  - LocalConfig (local-first 配置)
  - LaneQueue (车道命令队列)
  - MarkdownMemoryStore (L3 MEMORY.md)
  - embeddings (L4 语义向量)
  - ContextWindowGuard + JsonlSessionCache (压缩)
  - ExternalSkill + skill_loader (SKILL.md 插件)
  - 多模型 LLM provider 路由
  - MemoryStore L4 扩展方法
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# 1. LocalConfig
# ══════════════════════════════════════════════════════════════════════════════

class TestLocalConfig:
    def test_init_creates_dirs_and_default_config(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        cfg = LocalConfig(base_dir=tmp_path)
        assert (tmp_path / "config.json").exists()
        assert (tmp_path / "skills").is_dir()
        assert (tmp_path / "sessions").is_dir()

    def test_get_set_roundtrip(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        cfg = LocalConfig(base_dir=tmp_path)
        cfg.set("llm_provider", "openai")
        assert cfg.get("llm_provider") == "openai"

    def test_persist_across_instances(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        cfg1 = LocalConfig(base_dir=tmp_path)
        cfg1.set("llm_model", "gpt-4o")
        cfg2 = LocalConfig(base_dir=tmp_path)
        assert cfg2.llm_model == "gpt-4o"

    def test_default_provider_is_deepseek(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        cfg = LocalConfig(base_dir=tmp_path)
        assert cfg.llm_provider == "deepseek"

    def test_memory_md_path(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        cfg = LocalConfig(base_dir=tmp_path)
        assert cfg.memory_md_path == tmp_path / "MEMORY.md"

    def test_update_multiple_keys(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        cfg = LocalConfig(base_dir=tmp_path)
        cfg.update({"llm_provider": "anthropic", "context_window_tokens": 16384})
        assert cfg.context_window_tokens == 16384
        assert cfg.llm_provider == "anthropic"

    def test_corrupt_config_falls_back_to_defaults(self, tmp_path):
        from my_agent_os.config.local_config import LocalConfig
        (tmp_path / "skills").mkdir()
        (tmp_path / "sessions").mkdir()
        (tmp_path / "config.json").write_text("NOT_JSON", encoding="utf-8")
        cfg = LocalConfig(base_dir=tmp_path)
        assert cfg.llm_provider == "deepseek"


# ══════════════════════════════════════════════════════════════════════════════
# 2. LaneQueue
# ══════════════════════════════════════════════════════════════════════════════

class TestLaneQueue:
    @pytest.mark.asyncio
    async def test_submit_returns_result(self):
        from my_agent_os.agent_core.lane_queue import LaneQueue
        q = LaneQueue()
        async def add(x, y): return x + y
        result = await q.submit("test", add, 3, 5)
        assert result == 8
        await q.close()

    @pytest.mark.asyncio
    async def test_same_lane_serialized(self):
        """同一车道的任务严格串行，执行顺序可预期。"""
        from my_agent_os.agent_core.lane_queue import LaneQueue
        q = LaneQueue()
        order: list[int] = []
        async def task(n):
            order.append(n)
            return n
        await asyncio.gather(
            q.submit("lane", task, 1),
            q.submit("lane", task, 2),
            q.submit("lane", task, 3),
        )
        assert order == [1, 2, 3]
        await q.close()

    @pytest.mark.asyncio
    async def test_different_lanes_run_parallel(self):
        from my_agent_os.agent_core.lane_queue import LaneQueue
        import time
        q = LaneQueue()
        async def slow_task(n):
            await asyncio.sleep(0.05)
            return n
        start = time.monotonic()
        results = await asyncio.gather(
            q.submit("lane_a", slow_task, 1),
            q.submit("lane_b", slow_task, 2),
        )
        elapsed = time.monotonic() - start
        assert set(results) == {1, 2}
        assert elapsed < 0.15  # 并行应 < 100ms
        await q.close()

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        from my_agent_os.agent_core.lane_queue import LaneQueue
        q = LaneQueue()
        async def bad(): raise ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            await q.submit("err-lane", bad)
        await q.close()

    @pytest.mark.asyncio
    async def test_closed_queue_raises(self):
        from my_agent_os.agent_core.lane_queue import LaneQueue
        q = LaneQueue()
        await q.close()
        async def noop(): return 1
        with pytest.raises(RuntimeError):
            await q.submit("x", noop)


# ══════════════════════════════════════════════════════════════════════════════
# 3. MarkdownMemoryStore (L3)
# ══════════════════════════════════════════════════════════════════════════════

class TestMarkdownMemoryStore:
    def test_init_creates_default_sections(self, tmp_path):
        from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore, DEFAULT_SECTIONS
        store = MarkdownMemoryStore(tmp_path / "MEMORY.md")
        text = store.read_all()
        for s in DEFAULT_SECTIONS:
            assert f"## {s}" in text

    def test_upsert_fact_adds_bullet(self, tmp_path):
        from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore
        store = MarkdownMemoryStore(tmp_path / "MEMORY.md")
        store.upsert_fact("用户偏好深色模式", section="Core Preferences")
        sections = store.read_sections()
        assert "用户偏好深色模式" in sections["Core Preferences"]

    def test_upsert_deduplication(self, tmp_path):
        from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore
        store = MarkdownMemoryStore(tmp_path / "MEMORY.md")
        store.upsert_fact("重要决策 A", section="Key Decisions")
        store.upsert_fact("重要决策 A", section="Key Decisions")
        sections = store.read_sections()
        assert sections["Key Decisions"].count("重要决策 A") == 1

    def test_set_section_replaces_content(self, tmp_path):
        from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore
        store = MarkdownMemoryStore(tmp_path / "MEMORY.md")
        store.set_section("Active Goals", "- Q2 完成核心功能")
        assert "Q2 完成核心功能" in store.read_sections()["Active Goals"]

    def test_snapshot_for_prompt_respects_max_chars(self, tmp_path):
        from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore
        store = MarkdownMemoryStore(tmp_path / "MEMORY.md")
        store.set_section("Core Preferences", "A" * 2000)
        snap = store.snapshot_for_prompt(max_chars=500)
        assert len(snap) <= 600  # 允许少量溢出来自标题

    def test_snapshot_empty_when_no_facts(self, tmp_path):
        from my_agent_os.memory_layer.markdown_store import MarkdownMemoryStore
        store = MarkdownMemoryStore(tmp_path / "MEMORY.md")
        snap = store.snapshot_for_prompt()
        assert snap == ""


# ══════════════════════════════════════════════════════════════════════════════
# 4. Embeddings (L4)
# ══════════════════════════════════════════════════════════════════════════════

class TestEmbeddings:
    def test_encode_returns_json_vector(self):
        from my_agent_os.memory_layer import embeddings as emb
        blob = emb.encode("hello world")
        vec = json.loads(blob)
        assert isinstance(vec, list)
        assert len(vec) == emb.VECTOR_DIM

    def test_l2_normalized(self):
        import math
        from my_agent_os.memory_layer import embeddings as emb
        vec = json.loads(emb.encode("normalization test"))
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6 or norm == 0.0

    def test_similar_texts_high_score(self):
        from my_agent_os.memory_layer import embeddings as emb
        blob = emb.encode("汽车配件 大众 副厂")
        score = emb.similarity_score("大众 配件", blob)
        assert score > 0.1

    def test_different_texts_lower_score(self):
        from my_agent_os.memory_layer import embeddings as emb
        blob = emb.encode("机器学习 深度神经网络")
        score_related = emb.similarity_score("神经网络", blob)
        score_unrelated = emb.similarity_score("汽车配件价格", blob)
        assert score_related >= score_unrelated

    def test_empty_text_returns_zero_vector(self):
        from my_agent_os.memory_layer import embeddings as emb
        vec = json.loads(emb.encode(""))
        assert all(v == 0.0 for v in vec)

    def test_cosine_similarity_range(self):
        from my_agent_os.memory_layer import embeddings as emb
        score = emb.similarity_score("test", emb.encode("test"))
        assert 0.0 <= score <= 1.0 + 1e-9


# ══════════════════════════════════════════════════════════════════════════════
# 5. ContextWindowGuard + JsonlSessionCache
# ══════════════════════════════════════════════════════════════════════════════

class TestContextWindowGuard:
    def test_should_not_compact_short_history(self):
        from my_agent_os.memory_layer.compaction import ContextWindowGuard
        guard = ContextWindowGuard(max_tokens=8192)
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        assert not guard.should_compact(history)

    def test_should_compact_long_history(self):
        from my_agent_os.memory_layer.compaction import ContextWindowGuard, estimate_tokens
        guard = ContextWindowGuard(max_tokens=100, threshold=0.8, reserve=0)
        # 触发阈值 = 80 tokens；3.5 chars/token → 需要 > 80 * 3.5 = 280 chars
        history = [{"role": "user", "content": "A" * 350}]
        assert guard.should_compact(history)

    def test_compact_drops_code_fence_turns(self):
        from my_agent_os.memory_layer.compaction import ContextWindowGuard
        guard = ContextWindowGuard()
        code_turn = {"role": "assistant", "content": "```python\n" + "x = 1\n" * 50 + "```"}
        regular_turn = {"role": "user", "content": "explain this"}
        compacted = guard.compact([regular_turn, code_turn])
        assert all(t["content"] != code_turn["content"] for t in compacted)

    def test_compact_truncates_long_assistant(self):
        from my_agent_os.memory_layer.compaction import ContextWindowGuard
        guard = ContextWindowGuard()
        long_turn = {"role": "assistant", "content": "B" * 2000}
        short_turn = {"role": "user", "content": "ok"}
        compacted = guard._truncate_long_assistant_turns([short_turn, long_turn])
        assistant_turns = [t for t in compacted if t["role"] == "assistant"]
        assert len(assistant_turns[0]["content"]) < 1000

    def test_collapse_oldest(self):
        from my_agent_os.memory_layer.compaction import ContextWindowGuard
        guard = ContextWindowGuard()
        history = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        collapsed = guard._collapse_oldest(history)
        assert len(collapsed) < 10
        assert "省略" in collapsed[0]["content"] or "omitted" in collapsed[0]["content"]


class TestJsonlSessionCache:
    def test_append_and_read(self, tmp_path):
        from my_agent_os.memory_layer.compaction import JsonlSessionCache
        cache = JsonlSessionCache(tmp_path, "user1")
        cache.append_turn("user", "你好")
        cache.append_turn("assistant", "你好！有什么可以帮你？")
        turns = cache.read_turns()
        assert len(turns) == 2
        assert turns[0]["role"] == "user"

    def test_compact_if_needed_triggers(self, tmp_path):
        from my_agent_os.memory_layer.compaction import JsonlSessionCache, ContextWindowGuard
        guard = ContextWindowGuard(max_tokens=50, threshold=0.5, reserve=0)
        cache = JsonlSessionCache(tmp_path, "user2")
        for i in range(10):
            cache.append_turn("user", "A" * 30)
        remaining = cache.compact_if_needed(guard)
        assert remaining < 10

    def test_replace_all(self, tmp_path):
        from my_agent_os.memory_layer.compaction import JsonlSessionCache
        cache = JsonlSessionCache(tmp_path, "user3")
        cache.append_turn("user", "old")
        cache.replace_all([{"role": "user", "content": "new"}])
        turns = cache.read_turns()
        assert len(turns) == 1
        assert turns[0]["content"] == "new"


# ══════════════════════════════════════════════════════════════════════════════
# 6. SKILL.md ExternalSkill 加载器
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillLoader:
    def _write_skill_md(self, skills_dir: Path, name: str, content: str) -> None:
        d = skills_dir / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content, encoding="utf-8")

    def test_parse_minimal_skill_md(self, tmp_path):
        from my_agent_os.skills_layer.skill_loader import _parse_skill_md
        md = (
            "# 测试技能\n"
            "description: 这是一个测试\n\n"
            "## Instructions\n用于单元测试。\n"
        )
        path = tmp_path / "test_skill" / "SKILL.md"
        path.parent.mkdir()
        path.write_text(md, encoding="utf-8")
        parsed = _parse_skill_md(path)
        assert parsed["name"] == "测试技能"
        assert "测试" in parsed["description"]
        assert "单元测试" in parsed["instructions"]

    def test_discover_returns_skill_instances(self, tmp_path):
        from my_agent_os.skills_layer import skill_loader
        skill_loader._loaded_packs.clear()
        self._write_skill_md(
            tmp_path,
            "my_skill",
            "# My Skill\ndescription: Does stuff.\n\n## Instructions\nDo the thing.\n",
        )
        skills = skill_loader.discover_external_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "my_skill"

    @pytest.mark.asyncio
    async def test_skill_with_execute_code(self, tmp_path):
        from my_agent_os.skills_layer import skill_loader
        skill_loader._loaded_packs.clear()
        md = (
            "# Echo Skill\n"
            "description: Echoes input.\n\n"
            "## Instructions\nEcho.\n\n"
            "## Execute\n"
            "```python\n"
            "async def execute(params):\n"
            "    return {'success': True, 'output': params.get('msg', 'hi')}\n"
            "```\n"
        )
        self._write_skill_md(tmp_path, "echo_skill", md)
        skills = skill_loader.discover_external_skills(tmp_path)
        result = await skills[0].execute({"msg": "pong"})
        assert result["success"] is True
        assert result["output"] == "pong"

    @pytest.mark.asyncio
    async def test_skill_without_execute_returns_instructions(self, tmp_path):
        from my_agent_os.skills_layer import skill_loader
        skill_loader._loaded_packs.clear()
        md = "# Info Skill\ndescription: Info only.\n\n## Instructions\n这是说明性技能。\n"
        self._write_skill_md(tmp_path, "info_skill", md)
        skills = skill_loader.discover_external_skills(tmp_path)
        result = await skills[0].execute({})
        assert result["success"] is True

    def test_no_skills_dir_returns_empty(self, tmp_path):
        from my_agent_os.skills_layer import skill_loader
        skill_loader._loaded_packs.clear()
        missing = tmp_path / "nonexistent"
        skills = skill_loader.discover_external_skills(missing)
        assert skills == []


# ══════════════════════════════════════════════════════════════════════════════
# 7. 多模型 LLM provider 路由
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiModelLLM:
    def test_resolve_provider_default_deepseek(self, tmp_path, monkeypatch):
        from my_agent_os.config.local_config import LocalConfig
        import my_agent_os.config.local_config as lc
        cfg = LocalConfig(base_dir=tmp_path)
        monkeypatch.setattr(lc, "_local_config", cfg)
        from my_agent_os.agent_core import llm_client
        assert llm_client._resolve_provider() == "deepseek"

    def test_resolve_provider_from_local_config(self, tmp_path, monkeypatch):
        from my_agent_os.config.local_config import LocalConfig
        import my_agent_os.config.local_config as lc
        cfg = LocalConfig(base_dir=tmp_path)
        cfg.set("llm_provider", "anthropic")
        monkeypatch.setattr(lc, "_local_config", cfg)
        from my_agent_os.agent_core import llm_client
        assert llm_client._resolve_provider() == "anthropic"

    def test_provider_default_models_present(self):
        from my_agent_os.agent_core.llm_client import _PROVIDER_DEFAULT_MODELS
        for p in ("deepseek", "openai", "anthropic", "gemini", "ollama"):
            assert p in _PROVIDER_DEFAULT_MODELS

    @pytest.mark.asyncio
    async def test_call_llm_uses_graceful_fallback_on_bad_key(self, tmp_path, monkeypatch):
        """无效 key 时应返回兜底 JSON 字符串，不抛异常。"""
        import my_agent_os.config.settings as s
        monkeypatch.setattr(s.settings, "DEEPSEEK_API_KEY", "invalid_key_for_test")
        from my_agent_os.agent_core.llm_client import _graceful_fallback, LLM_RETRY_ATTEMPTS
        # 验证兜底消息格式有效
        fb = _graceful_fallback()
        data = json.loads(fb)
        assert "answer" in data

    def test_get_active_provider(self, tmp_path, monkeypatch):
        from my_agent_os.config.local_config import LocalConfig
        import my_agent_os.config.local_config as lc
        cfg = LocalConfig(base_dir=tmp_path)
        cfg.set("llm_provider", "ollama")
        monkeypatch.setattr(lc, "_local_config", cfg)
        from my_agent_os.agent_core.llm_client import get_active_provider
        assert get_active_provider() == "ollama"


# ══════════════════════════════════════════════════════════════════════════════
# 8. MemoryStore L4 扩展（store_embedding + semantic_search）
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryStoreL4:
    @pytest.mark.asyncio
    async def test_store_and_semantic_search(self, tmp_path):
        from my_agent_os.memory_layer.store import MemoryStore
        from my_agent_os.memory_layer.models import MemoryRecord, MemoryType

        store = MemoryStore(str(tmp_path / "test.db"))
        await store.initialize()

        rec = MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="大众汽车配件采购偏好",
            user_id="u1",
        )
        await store.add_memory(rec)
        await store.store_embedding(rec.id, rec.content)

        hits = await store.semantic_search("大众配件", top_k=5, user_id="u1")
        assert len(hits) >= 1
        assert hits[0][0] == rec.id
        assert hits[0][1] > 0.0

        await store.close()

    @pytest.mark.asyncio
    async def test_semantic_search_returns_empty_when_no_embeddings(self, tmp_path):
        from my_agent_os.memory_layer.store import MemoryStore
        store = MemoryStore(str(tmp_path / "empty.db"))
        await store.initialize()
        hits = await store.semantic_search("test", top_k=5)
        assert hits == []
        await store.close()
