"""ULID-based ID helpers with entity prefixes."""

from gateway.shared import ids

CROCKFORD_ALPHABET = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_prefixes() -> None:
    assert ids.message_id().startswith("msg_")
    assert ids.token_id().startswith("tok_")
    assert ids.delivery_id().startswith("whd_")


def test_id_suffix_is_a_ulid() -> None:
    suffix = ids.message_id().removeprefix("msg_")

    assert len(suffix) == 26
    assert set(suffix) <= CROCKFORD_ALPHABET


def test_ids_are_unique() -> None:
    generated = [ids.message_id() for _ in range(200)]

    assert len(set(generated)) == len(generated)
