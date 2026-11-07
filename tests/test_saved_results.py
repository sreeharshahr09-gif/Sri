"""Clearing the patent runs' saved results: only the app's own parts of the cache folder, from the sidebar."""

import os

import pytest
from streamlit.testing.v1 import AppTest

from analyst_agent.patents import cache, semantic


def _fill(folder):
    (folder / "replies" / "ab").mkdir(parents=True)
    (folder / "replies" / "ab" / "ab12.json").write_text('{"content": "x"}', encoding="utf-8")
    (folder / "replies" / "cd").mkdir()
    (folder / "replies" / "cd" / "cd34.json").write_text('{"content": "yy"}', encoding="utf-8")
    (folder / "extract").mkdir()
    (folder / "extract" / "sig.jsonl").write_text("{}\n" * 10, encoding="utf-8")
    (folder / "pdf").mkdir()
    (folder / "pdf" / "EP1234567A1.pdf").write_bytes(b"%PDF-1.4 " + b"0" * 500)
    (folder / "emb_0123.npy").write_bytes(b"1" * 300)
    (folder / "notes.txt").write_text("the user's own file", encoding="utf-8")  # not the app's: never touched


def test_summary_counts_each_part(tmp_path):
    _fill(tmp_path)
    parts = {p.key: p for p in cache.summary(tmp_path)}
    assert [p.key for p in cache.summary(tmp_path)] == ["replies", "extract", "maps", "pdf"]
    assert (parts["replies"].files, parts["extract"].files, parts["maps"].files, parts["pdf"].files) == (2, 1, 1, 1)
    assert parts["maps"].size == 300 and parts["extract"].size == 30


def test_clear_deletes_only_the_chosen_parts_and_nothing_else(tmp_path):
    _fill(tmp_path)
    deleted, freed, problems = cache.clear(["replies", "extract", "maps"], tmp_path)
    assert deleted == 4 and freed > 300 and problems == []
    assert not (tmp_path / "replies").exists() and not (tmp_path / "extract").exists()
    assert not list(tmp_path.glob("emb_*.npy"))
    assert (tmp_path / "pdf" / "EP1234567A1.pdf").exists() and (tmp_path / "notes.txt").exists()
    assert cache.clear(["pdf", "not-a-part"], tmp_path)[0] == 1 and (tmp_path / "notes.txt").exists()


def test_a_missing_folder_is_empty_not_an_error(tmp_path):
    assert all(p.files == 0 for p in cache.summary(tmp_path / "nothing-here"))
    assert cache.clear(["replies", "extract", "maps", "pdf"], tmp_path / "nothing-here") == (0, 0, [])


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_links_out_of_the_folder_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.json").write_text("{}", encoding="utf-8")
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / "replies").symlink_to(outside, target_is_directory=True)
    (folder / "extract").mkdir()
    (folder / "extract" / "link").symlink_to(outside, target_is_directory=True)
    cache.clear(["replies", "extract"], folder)
    assert (outside / "keep.json").exists()


def test_human_size():
    assert cache.human_size(512) == "512 bytes"
    assert cache.human_size(2048) == "2.0 KB"
    assert cache.human_size(5 * 1024**3) == "5.0 GB"


def _sidebar():
    from ui.common import saved_results_settings

    saved_results_settings()


def test_the_sidebar_clears_the_ticked_items_after_a_confirmation(tmp_path, monkeypatch):
    _fill(tmp_path)
    monkeypatch.setattr(semantic, "CACHE_DIR", tmp_path)
    at = AppTest.from_function(_sidebar, default_timeout=30)
    at.run()
    labels = [c.label for c in at.sidebar.checkbox]
    assert labels[0].startswith("Saved answers (") and "2 files" in labels[0] and "1 file)" in labels[1]
    assert [c.value for c in at.sidebar.checkbox] == [True, True, True, False]  # PDFs are kept unless ticked
    assert str(tmp_path) in at.sidebar.code[0].value
    at.sidebar.button(key="cache_clear").click().run()
    assert "cannot be undone" in at.sidebar.warning[0].value
    assert (tmp_path / "replies").exists()  # nothing is deleted before the confirmation
    at.sidebar.button(key="cache_yes").click().run()
    assert not at.exception
    assert "Deleted 4 files" in at.sidebar.success[0].value
    assert not (tmp_path / "replies").exists() and (tmp_path / "pdf" / "EP1234567A1.pdf").exists()
    assert (tmp_path / "notes.txt").exists()
    assert "Saved answers (empty)" in [c.label for c in at.sidebar.checkbox]


def test_cancel_deletes_nothing(tmp_path, monkeypatch):
    _fill(tmp_path)
    monkeypatch.setattr(semantic, "CACHE_DIR", tmp_path)
    at = AppTest.from_function(_sidebar, default_timeout=30)
    at.run()
    at.sidebar.button(key="cache_clear").click().run()
    at.sidebar.button(key="cache_no").click().run()
    assert (tmp_path / "replies").exists() and not at.sidebar.warning and not at.exception


def test_nothing_is_deleted_while_a_run_is_in_progress(tmp_path, monkeypatch):
    from ui import jobs

    _fill(tmp_path)
    monkeypatch.setattr(semantic, "CACHE_DIR", tmp_path)
    at = AppTest.from_function(_sidebar, default_timeout=30)
    at.run()
    at.sidebar.button(key="cache_clear").click().run()
    monkeypatch.setattr(jobs, "is_running", lambda key: True)  # a run started after the page was drawn
    at.sidebar.button(key="cache_yes").click().run()
    assert (tmp_path / "replies").exists() and not at.exception
    assert "in progress" in " ".join(c.value for c in at.sidebar.caption)
