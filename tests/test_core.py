"""Comprehensive pytest test suite for oh-my-somnia core modules.

Tests cover:
- selector.better: fitness comparison rules
- history.record/read: recording and filtering
- genome: Gene round-trip, slugify, auto-promotion on wins
- config: type validation and legacy directory fallback
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from oh_my_somnia.config import Config, ConfigError, _apply, project_dir
from oh_my_somnia.evaluator import Fitness
from oh_my_somnia.genome import PROMOTE_AFTER_WINS, Gene, Genome, slugify
from oh_my_somnia.history import read as history_read
from oh_my_somnia.history import record as history_record
from oh_my_somnia.selector import better


# =============================================================================
# selector.better() tests
# =============================================================================


class TestSelectorBetter:
    """Test the selector.better() function for fitness comparison."""

    def test_passed_beats_failed_same_score(self):
        """A passed fitness beats a failed one regardless of score."""
        passed = Fitness(passed=True, score=0.5)
        failed = Fitness(passed=False, score=0.9)
        assert better(passed, failed)
        assert not better(failed, passed)

    def test_passed_beats_failed_low_score(self):
        """Passed fitness wins even with a lower score."""
        passed = Fitness(passed=True, score=0.1)
        failed = Fitness(passed=False, score=0.99)
        assert better(passed, failed)

    def test_higher_score_wins_both_passed(self):
        """Higher score wins when both are passed."""
        high = Fitness(passed=True, score=0.9)
        low = Fitness(passed=True, score=0.5)
        assert better(high, low)
        assert not better(low, high)

    def test_higher_score_wins_both_failed(self):
        """Higher score wins when both are failed."""
        high = Fitness(passed=False, score=0.9)
        low = Fitness(passed=False, score=0.5)
        assert better(high, low)
        assert not better(low, high)

    def test_equal_fitness_not_better(self):
        """Equal fitness is not better (reflexive)."""
        fitness = Fitness(passed=True, score=0.7)
        assert not better(fitness, fitness)

    def test_equal_passed_status_equal_score_not_better(self):
        """Two passed with same score: neither is better."""
        a = Fitness(passed=True, score=0.7)
        b = Fitness(passed=True, score=0.7)
        assert not better(a, b)
        assert not better(b, a)

    def test_epsilon_tolerance_same_score(self):
        """Scores within EPSILON are treated as equal."""
        high = Fitness(passed=True, score=1.0)
        low = Fitness(passed=True, score=1.0 - 1e-10)  # within EPSILON
        assert not better(high, low)


# =============================================================================
# history.record() and history.read() tests
# =============================================================================


class TestHistoryRecord:
    """Test history.record() function."""

    def test_record_writes_json_line(self, tmp_home, monkeypatch):
        """record() appends a JSON line to the history file."""
        # Monkeypatch config functions to use tmp_home
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        entry = {"project": "myproj", "status": "pass"}
        history_record(entry)

        history_file = tmp_home / "history.jsonl"
        assert history_file.exists()
        content = history_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["project"] == "myproj"
        assert parsed["status"] == "pass"
        assert "timestamp" in parsed

    def test_record_multiple_entries(self, tmp_home, monkeypatch):
        """record() appends multiple entries, one per line."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        history_record({"project": "proj1"})
        history_record({"project": "proj2"})
        history_record({"project": "proj3"})

        history_file = tmp_home / "history.jsonl"
        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[0])["project"] == "proj1"
        assert json.loads(lines[1])["project"] == "proj2"
        assert json.loads(lines[2])["project"] == "proj3"

    def test_record_creates_parent_dir(self, tmp_home, monkeypatch):
        """record() creates parent directories as needed."""
        from oh_my_somnia import history as hist_mod
        # History path is nested
        history_path = tmp_home / "nested" / "deep" / "history.jsonl"
        # Monkeypatch on the history module where it's imported
        monkeypatch.setattr(hist_mod, "history_path", lambda: history_path)

        hist_mod.record({"data": "test"})

        assert history_path.exists()
        assert history_path.parent.exists()


class TestHistoryRead:
    """Test history.read() function."""

    def test_read_empty_when_no_file(self, tmp_home, monkeypatch):
        """read() returns empty list when history file doesn't exist."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "nonexistent.jsonl")

        result = history_read()
        assert result == []

    def test_read_all_entries(self, tmp_home, monkeypatch):
        """read() returns all entries in order."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        for i in range(3):
            history_record({"index": i})

        result = history_read()
        assert len(result) == 3
        assert result[0]["index"] == 0
        assert result[1]["index"] == 1
        assert result[2]["index"] == 2

    def test_read_with_limit(self, tmp_home, monkeypatch):
        """read(limit=N) returns the last N entries."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        for i in range(5):
            history_record({"index": i})

        result = history_read(limit=2)
        assert len(result) == 2
        assert result[0]["index"] == 3
        assert result[1]["index"] == 4

    def test_read_limit_zero_returns_empty(self, tmp_home, monkeypatch):
        """read(limit=0) returns an empty list."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        for i in range(5):
            history_record({"index": i})

        result = history_read(limit=0)
        assert result == []

    def test_read_limit_larger_than_entries(self, tmp_home, monkeypatch):
        """read(limit=N) where N > entry count returns all entries."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        for i in range(3):
            history_record({"index": i})

        result = history_read(limit=10)
        assert len(result) == 3

    def test_read_project_filter(self, tmp_home, monkeypatch):
        """read(project='X') filters entries by project field."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        history_record({"project": "alpha", "data": 1})
        history_record({"project": "beta", "data": 2})
        history_record({"project": "alpha", "data": 3})
        history_record({"project": "gamma", "data": 4})

        result = history_read(project="alpha")
        assert len(result) == 2
        assert result[0]["data"] == 1
        assert result[1]["data"] == 3

    def test_read_project_filter_no_match(self, tmp_home, monkeypatch):
        """read(project='X') returns empty when no matching project."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        history_record({"project": "alpha"})
        history_record({"project": "beta"})

        result = history_read(project="gamma")
        assert result == []

    def test_read_project_and_limit_combined(self, tmp_home, monkeypatch):
        """read(project='X', limit=N) filters and limits together."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "history_path",
                           lambda: tmp_home / "history.jsonl")

        history_record({"project": "alpha", "i": 1})
        history_record({"project": "beta", "i": 2})
        history_record({"project": "alpha", "i": 3})
        history_record({"project": "alpha", "i": 4})
        history_record({"project": "beta", "i": 5})

        result = history_read(project="alpha", limit=2)
        assert len(result) == 2
        assert result[0]["i"] == 3
        assert result[1]["i"] == 4

    def test_read_skips_invalid_json(self, tmp_home, monkeypatch):
        """read() skips malformed JSON lines."""
        from oh_my_somnia import config as cfg_mod
        history_path = tmp_home / "history.jsonl"
        monkeypatch.setattr(cfg_mod, "history_path", lambda: history_path)

        # Write mixed valid and invalid JSON
        with open(history_path, "w") as f:
            f.write(json.dumps({"valid": 1}) + "\n")
            f.write("not valid json\n")
            f.write(json.dumps({"valid": 2}) + "\n")

        result = history_read()
        assert len(result) == 2
        assert result[0]["valid"] == 1
        assert result[1]["valid"] == 2

    def test_read_skips_blank_lines(self, tmp_home, monkeypatch):
        """read() skips blank lines."""
        from oh_my_somnia import config as cfg_mod
        history_path = tmp_home / "history.jsonl"
        monkeypatch.setattr(cfg_mod, "history_path", lambda: history_path)

        with open(history_path, "w") as f:
            f.write(json.dumps({"id": 1}) + "\n")
            f.write("\n")
            f.write("\n")
            f.write(json.dumps({"id": 2}) + "\n")

        result = history_read()
        assert len(result) == 2


# =============================================================================
# genome tests
# =============================================================================


class TestGenomeSlugify:
    """Test the slugify() function."""

    def test_slugify_lowercase(self):
        """slugify converts to lowercase."""
        assert slugify("HELLO World") == "hello-world"

    def test_slugify_replaces_spaces(self):
        """slugify replaces spaces with hyphens."""
        assert slugify("verify before done") == "verify-before-done"

    def test_slugify_removes_special_chars(self):
        """slugify converts special characters to hyphens."""
        assert slugify("hello@world!") == "hello-world"

    def test_slugify_kebab_case(self):
        """slugify produces kebab-case output."""
        assert slugify("Verify Before Done") == "verify-before-done"

    def test_slugify_max_length(self):
        """slugify truncates to 60 chars."""
        long_text = "a" * 70
        result = slugify(long_text)
        assert len(result) == 60

    def test_slugify_strips_leading_trailing_hyphens(self):
        """slugify strips leading/trailing hyphens."""
        assert slugify("--hello--world--") == "hello-world"

    def test_slugify_fallback_to_gene(self):
        """slugify returns 'gene' when input is empty."""
        assert slugify("") == "gene"
        assert slugify("!!!") == "gene"


class TestGeneRoundTrip:
    """Test Gene.render_file() and _parse_gene() round-trip."""

    def test_gene_render_file_format(self):
        """Gene.render_file() produces expected format."""
        gene = Gene(
            id="test-gene",
            title="Test Gene",
            content="This is the content",
            status="active",
            origin="seed",
            born="2026-07-07",
            uses=5,
            wins=3,
        )
        rendered = gene.render_file()

        assert "---" in rendered
        assert "id: test-gene" in rendered
        assert "title: Test Gene" in rendered
        assert "status: active" in rendered
        assert "origin: seed" in rendered
        assert "born: 2026-07-07" in rendered
        assert "uses: 5" in rendered
        assert "wins: 3" in rendered
        assert "This is the content" in rendered

    def test_gene_render_and_parse_round_trip(self, tmp_path):
        """Gene.render_file() output can be parsed back identically."""
        from oh_my_somnia.genome import _parse_gene

        original = Gene(
            id="my-gene",
            title="My Gene Title",
            content="Gene content here",
            status="candidate",
            origin="mutation",
            born="2026-06-15",
            uses=10,
            wins=2,
        )

        # Write to file and parse back
        gene_file = tmp_path / "my-gene.md"
        gene_file.write_text(original.render_file(), encoding="utf-8")

        parsed = _parse_gene(gene_file)
        assert parsed is not None
        assert parsed.id == original.id
        assert parsed.title == original.title
        assert parsed.content == original.content
        assert parsed.status == original.status
        assert parsed.origin == original.origin
        assert parsed.born == original.born
        assert parsed.uses == original.uses
        assert parsed.wins == original.wins

    def test_gene_parse_preserves_all_fields(self, tmp_path):
        """Parsing preserves all fields exactly."""
        from oh_my_somnia.genome import _parse_gene

        gene = Gene(
            id="test",
            title="Title",
            content="Multi\nline\ncontent",
            status="active",
            origin="seed",
            born="2026-07-07",
            uses=7,
            wins=2,
        )

        gene_file = tmp_path / "test.md"
        gene_file.write_text(gene.render_file(), encoding="utf-8")
        parsed = _parse_gene(gene_file)

        assert parsed.uses == 7
        assert parsed.wins == 2


class TestGenomeAutoPromotion:
    """Test genome.record_trial() auto-promotion on wins."""

    def test_record_trial_increments_uses(self, tmp_home, monkeypatch):
        """record_trial() increments uses for all candidates."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome_dir.mkdir()

        # Create a candidate gene
        gene = Gene(
            id="test-candidate",
            title="Test",
            content="content",
            status="candidate",
            uses=0,
            wins=0,
        )

        genome = Genome([genome_dir])
        genome.write(gene)

        # Record a passed trial
        genome.record_trial(passed=True)

        assert genome.genes["test-candidate"].uses == 1
        assert genome.genes["test-candidate"].wins == 1

    def test_record_trial_increments_uses_on_failure(self, tmp_home, monkeypatch):
        """record_trial(passed=False) increments uses but not wins."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome_dir.mkdir()

        gene = Gene(
            id="test-candidate",
            title="Test",
            content="content",
            status="candidate",
            uses=0,
            wins=0,
        )

        genome = Genome([genome_dir])
        genome.write(gene)
        genome.record_trial(passed=False)

        assert genome.genes["test-candidate"].uses == 1
        assert genome.genes["test-candidate"].wins == 0

    def test_record_trial_auto_promotes_after_wins(self, tmp_home, monkeypatch):
        """record_trial() promotes candidate to active after PROMOTE_AFTER_WINS wins."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome_dir.mkdir()

        gene = Gene(
            id="test-candidate",
            title="Test",
            content="content",
            status="candidate",
            uses=0,
            wins=0,
        )

        genome = Genome([genome_dir])
        genome.write(gene)

        # Record wins up to PROMOTE_AFTER_WINS
        for _ in range(PROMOTE_AFTER_WINS):
            genome.record_trial(passed=True)

        assert genome.genes["test-candidate"].status == "active"
        assert genome.genes["test-candidate"].wins == PROMOTE_AFTER_WINS

    def test_record_trial_does_not_update_active_genes(self, tmp_home, monkeypatch):
        """record_trial() only updates candidate genes, not active ones."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome_dir.mkdir()

        gene = Gene(
            id="active-gene",
            title="Test",
            content="content",
            status="active",
            uses=5,
            wins=5,
        )

        genome = Genome([genome_dir])
        genome.write(gene)
        original_uses = genome.genes["active-gene"].uses
        genome.record_trial(passed=True)

        # Active genes should not be modified by record_trial
        assert genome.genes["active-gene"].status == "active"
        assert genome.genes["active-gene"].uses == original_uses

    def test_make_gene_preserves_uses_and_wins_on_collision(self, tmp_home, monkeypatch):
        """make_gene() preserves uses/wins when id collides with existing gene."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome_dir.mkdir()

        # Create and write an existing gene
        existing = Gene(
            id="my-gene",
            title="Old Title",
            content="old content",
            status="candidate",
            uses=10,
            wins=3,
        )

        genome = Genome([genome_dir])
        genome.write(existing)

        # make_gene with the same id
        new = genome.make_gene(
            gene_id="my-gene",
            title="New Title",
            content="new content",
            origin="mutation",
            status="candidate",
        )

        assert new.uses == 10
        assert new.wins == 3
        assert new.status == "candidate"  # Preserves existing status


class TestGenomeWrite:
    """Test genome.write() method."""

    def test_genome_write_creates_file(self, tmp_home, monkeypatch):
        """genome.write() creates a gene file."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome = Genome([genome_dir])

        gene = Gene(
            id="test-gene",
            title="Test Gene",
            content="Test content",
        )

        genome.write(gene)

        gene_file = genome_dir / "test-gene.md"
        assert gene_file.exists()
        content = gene_file.read_text()
        assert "id: test-gene" in content
        assert "Test content" in content

    def test_genome_write_sets_born_date(self, tmp_home, monkeypatch):
        """genome.write() sets born date if not set."""
        from oh_my_somnia import config as cfg_mod
        monkeypatch.setattr(cfg_mod, "genome_dir", lambda: tmp_home / "genome")
        monkeypatch.setattr(cfg_mod, "somnia_home", lambda: tmp_home)

        genome_dir = tmp_home / "genome"
        genome = Genome([genome_dir])

        gene = Gene(
            id="test",
            title="Test",
            content="content",
            born="",
        )

        assert gene.born == ""
        genome.write(gene)
        assert gene.born != ""


# =============================================================================
# config tests
# =============================================================================


class TestConfigApply:
    """Test config._apply() function."""

    def test_apply_string_value(self):
        """_apply() accepts string values for string fields."""
        cfg = Config()
        cfg.fitness_command = ""
        _apply(cfg, {"fitness_command": "pytest"})
        assert cfg.fitness_command == "pytest"

    def test_apply_int_value(self):
        """_apply() accepts int values for int fields."""
        cfg = Config()
        cfg.generations = 3
        _apply(cfg, {"generations": 5})
        assert cfg.generations == 5

    def test_apply_bool_value(self):
        """_apply() accepts bool values for bool fields."""
        cfg = Config()
        cfg.judge = True
        _apply(cfg, {"judge": False})
        assert cfg.judge is False

    def test_apply_list_value(self):
        """_apply() accepts list values for list fields."""
        cfg = Config()
        cfg.extra_ignores = []
        _apply(cfg, {"extra_ignores": ["build", "dist"]})
        assert cfg.extra_ignores == ["build", "dist"]

    def test_apply_raises_on_wrong_type_string_for_int(self):
        """_apply() raises ConfigError when string given for int field."""
        cfg = Config()
        cfg.generations = 3
        with pytest.raises(ConfigError) as exc_info:
            _apply(cfg, {"generations": "not-an-int"})
        assert "expects an integer" in str(exc_info.value)

    def test_apply_raises_on_wrong_type_string_for_list(self):
        """_apply() raises ConfigError when string given for list field."""
        cfg = Config()
        cfg.extra_ignores = []
        with pytest.raises(ConfigError) as exc_info:
            _apply(cfg, {"extra_ignores": "not-a-list"})
        assert "expects a list" in str(exc_info.value)

    def test_apply_raises_on_wrong_type_int_for_string(self):
        """_apply() raises ConfigError when int given for string field."""
        cfg = Config()
        cfg.fitness_command = ""
        with pytest.raises(ConfigError) as exc_info:
            _apply(cfg, {"fitness_command": 123})
        assert "expects a string" in str(exc_info.value)

    def test_apply_raises_on_wrong_type_string_for_bool(self):
        """_apply() raises ConfigError when string given for bool field."""
        cfg = Config()
        cfg.judge = True
        with pytest.raises(ConfigError) as exc_info:
            _apply(cfg, {"judge": "yes"})
        assert "expects true/false" in str(exc_info.value)

    def test_apply_with_hyphenated_key(self):
        """_apply() converts hyphenated keys to underscores."""
        cfg = Config()
        cfg.fitness_command = ""
        _apply(cfg, {"fitness-command": "pytest"})
        assert cfg.fitness_command == "pytest"

    def test_apply_ignores_none_values(self):
        """_apply() ignores None values."""
        cfg = Config()
        original = cfg.generations
        _apply(cfg, {"generations": None})
        assert cfg.generations == original

    def test_apply_ignores_unknown_keys(self):
        """_apply() ignores unknown config keys."""
        cfg = Config()
        # Should not raise
        _apply(cfg, {"unknown_key": "value"})


class TestConfigProjectDir:
    """Test config.project_dir() function."""

    def test_project_dir_returns_somnia_when_exists(self, tmp_path):
        """project_dir() returns .somnia when it exists."""
        somnia_dir = tmp_path / ".somnia"
        somnia_dir.mkdir()

        result = project_dir(tmp_path)
        assert result == somnia_dir

    def test_project_dir_returns_somnia_when_neither_exists(self, tmp_path):
        """project_dir() returns .somnia when neither .somnia nor .darwin exists."""
        result = project_dir(tmp_path)
        assert result.name == ".somnia"
        assert result.parent == tmp_path

    def test_project_dir_prefers_somnia_over_darwin(self, tmp_path):
        """project_dir() prefers .somnia over .darwin when both exist."""
        somnia_dir = tmp_path / ".somnia"
        darwin_dir = tmp_path / ".darwin"
        somnia_dir.mkdir()
        darwin_dir.mkdir()

        result = project_dir(tmp_path)
        assert result == somnia_dir

    def test_project_dir_falls_back_to_darwin(self, tmp_path):
        """project_dir() falls back to .darwin when .somnia doesn't exist."""
        darwin_dir = tmp_path / ".darwin"
        darwin_dir.mkdir()

        result = project_dir(tmp_path)
        assert result == darwin_dir

    def test_project_dir_legacy_fallback_only(self, tmp_path):
        """project_dir() only uses .darwin as fallback, not preference."""
        darwin_dir = tmp_path / ".darwin"
        darwin_dir.mkdir()

        # If .somnia exists, it should be preferred even if .darwin is there first
        result = project_dir(tmp_path)
        assert result == darwin_dir

        # Now create .somnia; it should become the choice
        somnia_dir = tmp_path / ".somnia"
        somnia_dir.mkdir()

        result = project_dir(tmp_path)
        assert result == somnia_dir


class TestConfigType:
    """Test config type validation edge cases."""

    def test_apply_float_value(self):
        """_apply() accepts float/int for float fields."""
        cfg = Config()
        cfg.max_budget_usd = 0.0
        _apply(cfg, {"max_budget_usd": 2.5})
        assert cfg.max_budget_usd == 2.5

    def test_apply_int_to_float_field(self):
        """_apply() accepts int for float fields (converts)."""
        cfg = Config()
        cfg.max_budget_usd = 0.0
        _apply(cfg, {"max_budget_usd": 5})
        assert cfg.max_budget_usd == 5.0

    def test_apply_raises_on_list_with_non_strings(self):
        """_apply() raises ConfigError for list with non-string items."""
        cfg = Config()
        cfg.extra_ignores = []
        with pytest.raises(ConfigError) as exc_info:
            _apply(cfg, {"extra_ignores": ["valid", 123, "item"]})
        assert "list of strings" in str(exc_info.value)

    def test_apply_raises_bool_for_int_field(self):
        """_apply() rejects bool for int fields (bool is subclass of int)."""
        cfg = Config()
        cfg.generations = 3
        with pytest.raises(ConfigError) as exc_info:
            _apply(cfg, {"generations": True})
        assert "expects an integer" in str(exc_info.value)
