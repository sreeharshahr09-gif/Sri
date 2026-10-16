import os
import time

import app_stamp


def make_app(tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "app.py").write_text("print('app')\n")
    (tmp_path / "ui" / "page.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("y = 1\n")
    return tmp_path


def test_stamp_follows_code_and_process(tmp_path):
    root = make_app(tmp_path)
    assert not app_stamp.is_current(root=root)  # no app has started here yet
    app_stamp.write_stamp(4242, root=root)
    assert app_stamp.is_current(root=root) and app_stamp.is_current("4242", root=root)
    assert not app_stamp.is_current("999", root=root)  # another process answers on the port

    (root / "tests" / "test_x.py").write_text("y = 2  # tests do not count\n")
    assert app_stamp.is_current(4242, root=root)

    page = root / "ui" / "page.py"
    page.write_text("x = 22\n")  # an update of the app's code
    later = time.time() + 5
    os.utime(page, (later, later))
    assert not app_stamp.is_current(4242, root=root)
    app_stamp.write_stamp(5151, root=root)
    assert app_stamp.is_current(5151, root=root)


def test_another_folder_has_another_stamp(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, b = make_app(tmp_path / "a"), make_app(tmp_path / "b")  # same files, different copies
    assert app_stamp.code_stamp(a) != app_stamp.code_stamp(b)
