"""The credential store.

It replaced .htpasswd, whose `-c` flag truncates the file it is given and sat
one typo away from deleting every login on the box. These tests pin the
properties that made the replacement worth doing.
"""
import pytest

from pcs import viewers


@pytest.fixture
def store(tmp_path):
    return tmp_path / "viewers"


def test_a_password_round_trips(store):
    viewers.add(store, "tester", "hunter2")
    assert viewers.authenticate(store, "tester", "hunter2")


def test_a_wrong_password_fails(store):
    viewers.add(store, "tester", "hunter2")
    assert not viewers.authenticate(store, "tester", "hunter3")


def test_an_unknown_user_fails(store):
    viewers.add(store, "tester", "hunter2")
    assert not viewers.authenticate(store, "ghost", "hunter2")


def test_the_password_is_not_stored(store):
    viewers.add(store, "tester", "hunter2")
    assert "hunter2" not in store.read_text()


def test_two_identical_passwords_hash_differently(store):
    """Per-record salt. Without it, equal hashes reveal equal passwords and one
    cracked entry breaks every account that shares it."""
    viewers.add(store, "a", "same")
    viewers.add(store, "b", "same")
    a, b = viewers.load(store)
    assert a.phc != b.phc


def test_adding_an_existing_name_rotates_rather_than_duplicates(store):
    viewers.add(store, "tester", "old")
    assert viewers.add(store, "tester", "new") is True
    assert len(viewers.load(store)) == 1
    assert viewers.authenticate(store, "tester", "new")
    assert not viewers.authenticate(store, "tester", "old")


def test_adding_a_second_login_keeps_the_first(store):
    """The .htpasswd -c bug in one line: adding a viewer must never be a way to
    delete the existing ones."""
    viewers.add(store, "admin", "a")
    viewers.add(store, "tester", "b")
    assert {v.name for v in viewers.load(store)} == {"admin", "tester"}
    assert viewers.authenticate(store, "admin", "a")


def test_removing_the_last_login_is_refused(store):
    viewers.add(store, "admin", "a")
    with pytest.raises(viewers.ViewerError, match="only login"):
        viewers.remove(store, "admin")


def test_removing_one_of_two_works(store):
    viewers.add(store, "admin", "a")
    viewers.add(store, "tester", "b")
    viewers.remove(store, "tester")
    assert not viewers.authenticate(store, "tester", "b")
    assert viewers.authenticate(store, "admin", "a")


def test_removing_an_unknown_name_says_so(store):
    viewers.add(store, "admin", "a")
    with pytest.raises(viewers.ViewerError, match="no login"):
        viewers.remove(store, "ghost")


@pytest.mark.parametrize("bad", ["has space", "has:colon", "", "x" * 65, "a\nb"])
def test_a_username_that_would_corrupt_the_file_is_refused(store, bad):
    with pytest.raises(viewers.ViewerError):
        viewers.add(store, bad, "pw")


def test_the_file_is_not_world_readable(store):
    viewers.add(store, "tester", "hunter2")
    assert oct(store.stat().st_mode & 0o777) == "0o640"


def test_a_corrupt_line_fails_the_login_instead_of_the_service(store):
    viewers.add(store, "tester", "hunter2")
    store.write_text("tester:this-is-not-a-hash\n")
    assert viewers.authenticate(store, "tester", "hunter2") is False


def test_generated_passwords_are_unique_and_unambiguous(store):
    pws = {viewers.generate_password() for _ in range(50)}
    assert len(pws) == 50
    assert not set("".join(pws)) & set("lIO01")
